import os
import asyncio

from src.core.security_bridge import CommsBridge, MFAListener, WebhookAuthenticator


def test_webhook_authenticator_fails_closed_without_secret(monkeypatch):
    monkeypatch.delenv("LANCELOT_API_TOKEN", raising=False)
    auth = WebhookAuthenticator()

    assert auth.verify_remote_header("Bearer anything") is False


def test_webhook_authenticator_requires_exact_bearer_match(monkeypatch):
    monkeypatch.setenv("LANCELOT_WEBHOOK_BEARER", "shared-secret")
    monkeypatch.delenv("LANCELOT_API_TOKEN", raising=False)
    auth = WebhookAuthenticator()

    assert auth.verify_remote_header("Bearer shared-secret") is True
    assert auth.verify_remote_header("Bearer wrong-secret") is False
    assert auth.verify_remote_header("Basic shared-secret") is False


def test_webhook_authenticator_falls_back_to_api_token(monkeypatch):
    monkeypatch.delenv("LANCELOT_WEBHOOK_BEARER", raising=False)
    monkeypatch.setenv("LANCELOT_API_TOKEN", "api-token-secret")
    auth = WebhookAuthenticator()

    assert auth.verify_remote_header("Bearer api-token-secret") is True
    assert auth.verify_remote_header("Bearer wrong-secret") is False


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
