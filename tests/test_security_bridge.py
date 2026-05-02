import os
import asyncio
import json
import sys
import time
import types
from unittest.mock import patch

from src.core.security_bridge import CommsBridge, MFAListener, WebhookAuthenticator


def test_webhook_authenticator_fails_closed_without_secret(monkeypatch):
    monkeypatch.delenv("LANCELOT_API_TOKEN", raising=False)
    monkeypatch.delenv("LANCELOT_WEBHOOK_BEARER", raising=False)
    monkeypatch.delenv("LANCELOT_GOOGLE_CHAT_AUDIENCE", raising=False)
    auth = WebhookAuthenticator()

    assert auth.verify_remote_header("Bearer anything") is False


def test_webhook_authenticator_requires_exact_bearer_match_in_bonded_mode(monkeypatch):
    monkeypatch.setenv("LANCELOT_WEBHOOK_AUTH_MODE", "bonded_bearer")
    monkeypatch.setenv("LANCELOT_WEBHOOK_BEARER", "shared-secret")
    monkeypatch.delenv("LANCELOT_API_TOKEN", raising=False)
    auth = WebhookAuthenticator()

    assert auth.verify_remote_header("Bearer shared-secret") is True
    assert auth.verify_remote_header("Bearer wrong-secret") is False
    assert auth.verify_remote_header("Basic shared-secret") is False


def test_webhook_authenticator_does_not_fall_back_to_api_token_in_bonded_mode(monkeypatch):
    monkeypatch.setenv("LANCELOT_WEBHOOK_AUTH_MODE", "bonded_bearer")
    monkeypatch.delenv("LANCELOT_WEBHOOK_BEARER", raising=False)
    monkeypatch.setenv("LANCELOT_API_TOKEN", "api-token-secret")
    auth = WebhookAuthenticator()

    assert auth.verify_remote_header("Bearer api-token-secret") is False
    assert auth.verify_remote_header("Bearer wrong-secret") is False


def test_webhook_authenticator_verifies_google_signed_token(monkeypatch):
    monkeypatch.setenv("LANCELOT_WEBHOOK_AUTH_MODE", "google_signed")
    monkeypatch.setenv("LANCELOT_GOOGLE_CHAT_AUDIENCE", "chat-app-audience")
    auth = WebhookAuthenticator()

    with patch("google.oauth2.id_token.verify_oauth2_token") as mock_verify:
        mock_verify.return_value = {
            "email": "chat@system.gserviceaccount.com",
            "email_verified": True,
        }
        assert auth.verify_remote_header("Bearer signed-jwt") is True


def test_webhook_authenticator_rejects_wrong_google_identity(monkeypatch):
    monkeypatch.setenv("LANCELOT_WEBHOOK_AUTH_MODE", "google_signed")
    monkeypatch.setenv("LANCELOT_GOOGLE_CHAT_AUDIENCE", "chat-app-audience")
    auth = WebhookAuthenticator()

    with patch("google.oauth2.id_token.verify_oauth2_token") as mock_verify:
        mock_verify.return_value = {
            "email": "other@example.com",
            "email_verified": True,
        }
        assert auth.verify_remote_header("Bearer signed-jwt") is False


def test_comms_bridge_defaults_to_none_when_unconfigured(monkeypatch):
    monkeypatch.delenv("LANCELOT_COMMS_TYPE", raising=False)

    bridge = CommsBridge()

    assert bridge.comms_type == "none"


def test_mfa_challenge_survives_listener_restart(tmp_path):
    async def _run():
        first = MFAListener(data_dir=str(tmp_path))
        await first.request_mfa(
            "task-1",
            "Sensitive action",
            operator_id="op-1",
            session_id="session-1",
            actor="Arthur",
        )

        second = MFAListener(data_dir=str(tmp_path))
        ok, reason = second.submit_code(
            "task-1",
            "123456",
            operator_id="op-1",
            session_id="session-1",
            actor="Arthur",
        )
        assert ok is True
        assert reason == "accepted"

        third = MFAListener(data_dir=str(tmp_path))
        code = await third.wait_for_code("task-1", timeout=0.1)
        assert code == "123456"

    asyncio.run(_run())


def test_mfa_listener_prunes_corrupt_duplicate_timeout_and_authorization_paths(tmp_path, monkeypatch):
    (tmp_path / "mfa_challenges.json").write_text("{not json", encoding="utf-8")
    listener = MFAListener(data_dir=str(tmp_path))
    assert listener._pending_challenges == {}

    async def _run():
        await listener.request_mfa("task-1", "Sensitive action", operator_id="op-1", session_id="sess-1")
        await listener.request_mfa("task-1", "Duplicate action", operator_id="op-1", session_id="sess-1")
        assert list(listener._pending_challenges) == ["task-1"]

        assert listener.submit_code("missing", "123456") == (False, "unknown_task")
        assert listener.submit_code("task-1", "123456", operator_id="op-2", session_id="sess-1") == (
            False,
            "forbidden",
        )
        assert listener.submit_code("task-1", "123456", operator_id="op-1", session_id="sess-2") == (
            False,
            "forbidden",
        )
        ok, reason = listener.submit_code("task-1", "654321", operator_id="admin", session_id="admin", is_admin=True)
        assert (ok, reason) == (True, "accepted")
        assert await listener.wait_for_code("task-1", timeout=0.1) == "654321"

        with pytest.raises(ValueError, match="No MFA challenge"):
            await listener.wait_for_code("unknown", timeout=0.01)

        await listener.request_mfa("task-timeout", "Timeout action")
        with pytest.raises(TimeoutError, match="not received"):
            await listener.wait_for_code("task-timeout", timeout=0.01)

    import pytest

    asyncio.run(_run())

    listener._pending_challenges["expired"] = {"created_at_ts": time.time() - 9999}
    listener._events["expired"] = object()
    listener._prune_expired()
    assert "expired" not in listener._pending_challenges

    monkeypatch.setattr(type(listener._challenge_file), "write_text", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))
    listener._pending_challenges["unsaved"] = {"created_at_ts": time.time()}
    listener._save_challenges()


def test_webhook_authenticator_secret_cache_google_failure_and_unsupported_modes(monkeypatch):
    monkeypatch.setenv("LANCELOT_WEBHOOK_AUTH_MODE", "bonded_bearer")
    monkeypatch.delenv("LANCELOT_WEBHOOK_BEARER", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "secret_cache",
        types.SimpleNamespace(get=lambda key, default="": "cached-secret"),
    )
    auth = WebhookAuthenticator()
    assert auth.verify_remote_header("Bearer cached-secret") is True

    monkeypatch.setitem(
        sys.modules,
        "secret_cache",
        types.SimpleNamespace(get=lambda key, default="": (_ for _ in ()).throw(RuntimeError("cache down"))),
    )
    monkeypatch.setenv("LANCELOT_WEBHOOK_BEARER", "env-secret")
    assert auth.verify_remote_header("Bearer env-secret") is True

    monkeypatch.setenv("LANCELOT_WEBHOOK_AUTH_MODE", "google_signed")
    monkeypatch.setenv("LANCELOT_GOOGLE_CHAT_AUDIENCE", "audience")
    with patch("google.oauth2.id_token.verify_oauth2_token", side_effect=RuntimeError("bad jwt")):
        assert auth.verify_remote_header("Bearer signed") is False
    with patch("google.oauth2.id_token.verify_oauth2_token", return_value={
        "email": "chat@system.gserviceaccount.com",
        "email_verified": False,
    }):
        assert auth.verify_remote_header("Bearer signed") is False

    monkeypatch.setenv("LANCELOT_WEBHOOK_AUTH_MODE", "unsupported")
    assert auth.verify_remote_header("Bearer signed") is False


def test_comms_bridge_sends_alerts_and_handles_missing_or_failed_channels(monkeypatch):
    sent = []

    class Response:
        def __init__(self, status=200):
            self.status = status

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def post(self, url, json):
            sent.append((url, json))
            return Response(status=200)

    monkeypatch.setitem(sys.modules, "aiohttp", types.SimpleNamespace(ClientSession=lambda: Session()))
    monkeypatch.setattr(
        "src.core.security_bridge.assert_url_allowed",
        lambda url, component=None, network_interceptor=None: url,
    )

    monkeypatch.setenv("LANCELOT_COMMS_TYPE", "google_chat")
    monkeypatch.setenv("LANCELOT_COMMS_WEBHOOK", "https://chat.example/webhook")
    asyncio.run(CommsBridge().send_alert("hello"))
    assert sent[-1] == ("https://chat.example/webhook", {"text": "hello"})

    monkeypatch.setenv("LANCELOT_COMMS_TYPE", "telegram")
    monkeypatch.setenv("LANCELOT_TELEGRAM_TOKEN", "token-1")
    monkeypatch.setenv("LANCELOT_TELEGRAM_CHAT_ID", "chat-1")
    monkeypatch.setitem(
        sys.modules,
        "secret_cache",
        types.SimpleNamespace(get=lambda key, default="": (_ for _ in ()).throw(RuntimeError("cache down"))),
    )
    asyncio.run(CommsBridge().send_alert("hello telegram"))
    assert sent[-1] == (
        "https://api.telegram.org/bottoken-1/sendMessage",
        {"chat_id": "chat-1", "text": "hello telegram", "parse_mode": "Markdown"},
    )

    monkeypatch.setenv("LANCELOT_COMMS_TYPE", "telegram")
    monkeypatch.delenv("LANCELOT_TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("LANCELOT_TELEGRAM_CHAT_ID", raising=False)
    asyncio.run(CommsBridge().send_alert("missing telegram"))

    monkeypatch.setenv("LANCELOT_COMMS_TYPE", "unknown")
    asyncio.run(CommsBridge().send_alert("unknown"))

    monkeypatch.setenv("LANCELOT_COMMS_TYPE", "google_chat")
    monkeypatch.setenv("LANCELOT_COMMS_WEBHOOK", "https://chat.example/webhook")
    monkeypatch.setattr(
        "src.core.security_bridge.assert_url_allowed",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("blocked")),
    )
    asyncio.run(CommsBridge().send_alert("blocked"))


def test_comms_bridge_handles_missing_google_webhook_and_non_200_response(monkeypatch):
    sent = []

    class Response:
        status = 503

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def post(self, url, json):
            sent.append((url, json))
            return Response()

    monkeypatch.setitem(sys.modules, "aiohttp", types.SimpleNamespace(ClientSession=lambda: Session()))
    monkeypatch.setattr(
        "src.core.security_bridge.assert_url_allowed",
        lambda url, component=None, network_interceptor=None: url,
    )
    monkeypatch.setenv("LANCELOT_COMMS_TYPE", "google_chat")
    monkeypatch.delenv("LANCELOT_COMMS_WEBHOOK", raising=False)
    asyncio.run(CommsBridge().send_alert("missing"))
    assert sent == []

    monkeypatch.setenv("LANCELOT_COMMS_WEBHOOK", "https://chat.example/webhook")
    asyncio.run(CommsBridge().send_alert("service down"))
    assert sent == [("https://chat.example/webhook", {"text": "service down"})]
