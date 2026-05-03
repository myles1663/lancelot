import sys
import urllib.request
from io import BytesIO
from types import SimpleNamespace
from urllib.error import HTTPError

from src.core.skills.builtins import telegram_send


def test_secret_or_env_prefers_secret_cache(monkeypatch):
    monkeypatch.setenv("LANCELOT_TELEGRAM_TOKEN", "env-token")
    monkeypatch.setitem(
        sys.modules,
        "secret_cache",
        SimpleNamespace(get=lambda key, default="": "vault-token"),
    )

    assert telegram_send._secret_or_env("LANCELOT_TELEGRAM_TOKEN") == "vault-token"


def test_secret_or_env_falls_back_to_environment(monkeypatch):
    monkeypatch.setenv("LANCELOT_TELEGRAM_CHAT_ID", "env-chat")
    monkeypatch.setitem(
        sys.modules,
        "secret_cache",
        SimpleNamespace(get=lambda key, default="": ""),
    )

    assert telegram_send._secret_or_env("LANCELOT_TELEGRAM_CHAT_ID") == "env-chat"


def test_secret_or_env_falls_back_to_vault(monkeypatch):
    monkeypatch.delenv("LANCELOT_TELEGRAM_TOKEN", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "secret_cache",
        SimpleNamespace(get=lambda key, default="": ""),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.core.secret_cache",
        SimpleNamespace(get=lambda key, default="": ""),
    )

    class _Vault:
        def __init__(self, **kwargs):
            pass

        def exists(self, key):
            return key == "system.telegram_token"

        def retrieve(self, key):
            return "vault-token"

    monkeypatch.setitem(
        sys.modules,
        "connectors.vault",
        SimpleNamespace(CredentialVault=_Vault),
    )

    assert telegram_send._secret_or_env("LANCELOT_TELEGRAM_TOKEN") == "vault-token"


def test_send_text_retries_plain_text_when_html_parse_fails(monkeypatch):
    monkeypatch.setattr(telegram_send, "_secret_or_env", lambda key: "configured")
    monkeypatch.setitem(sys.modules, "gateway", SimpleNamespace(telegram_bot=None))

    calls = []

    def fake_urlopen(req, timeout=15):
        calls.append(req.data.decode("utf-8"))
        if len(calls) == 1:
            raise HTTPError(
                req.full_url,
                400,
                "Bad Request",
                hdrs=None,
                fp=BytesIO(b"{\"ok\":false,\"description\":\"Bad Request: can't parse entities\"}"),
            )
        return SimpleNamespace(read=lambda: b'{"ok":true}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    result = telegram_send.send_text("headline with <bad html>")

    assert result["status"] == "sent"
    assert len(calls) == 2
    assert '"parse_mode": "HTML"' in calls[0]
    assert "parse_mode" not in calls[1]


def test_send_text_splits_long_messages(monkeypatch):
    monkeypatch.setattr(telegram_send, "_secret_or_env", lambda key: "configured")
    monkeypatch.setitem(sys.modules, "gateway", SimpleNamespace(telegram_bot=None))

    calls = []

    def fake_urlopen(req, timeout=15):
        calls.append(req.data.decode("utf-8"))
        return SimpleNamespace(read=lambda: b'{"ok":true}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    result = telegram_send.send_text("alpha\n\n" * 700)

    assert result["status"] == "sent"
    assert result["chunk_count"] > 1
    assert len(calls) == result["chunk_count"]
