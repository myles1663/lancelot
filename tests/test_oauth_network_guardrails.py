import asyncio
from unittest.mock import patch

from src.core.outbound_http import OutboundNetworkError
from src.core.google_oauth_manager import (
    GoogleOAuthManager,
    VAULT_ACCESS_TOKEN as GOOGLE_ACCESS_TOKEN,
    VAULT_CLIENT_ID,
    VAULT_CLIENT_SECRET,
    VAULT_REFRESH_TOKEN as GOOGLE_REFRESH_TOKEN,
)
from src.core.oauth_token_manager import (
    OAuthTokenManager,
    VAULT_REFRESH_TOKEN as ANTHROPIC_REFRESH_TOKEN,
)
from src.core.openai_codex_oauth_manager import (
    OpenAICodexOAuthManager,
    VAULT_REFRESH_TOKEN as CODEX_REFRESH_TOKEN,
)
from src.core.security_bridge import CommsBridge


class DummyVault:
    def __init__(self):
        self._entries = {}

    def store(self, key, value, type="config"):
        self._entries[key] = value

    def retrieve(self, key, accessor_id=""):
        return self._entries.get(key, "")

    def exists(self, key):
        return key in self._entries

    def delete(self, key):
        self._entries.pop(key, None)


def _blocked(*args, **kwargs):
    raise OutboundNetworkError("blocked by network allowlist")


def test_anthropic_oauth_exchange_fails_closed_when_allowlist_blocks(monkeypatch, tmp_path):
    monkeypatch.setenv("LANCELOT_ANTHROPIC_OAUTH_STATE_FILE", str(tmp_path / "anthropic-oauth.json"))
    monkeypatch.setattr("src.core.oauth_token_manager.assert_url_allowed", _blocked)
    manager = OAuthTokenManager(vault=DummyVault(), port=8000)
    _, state = manager.generate_auth_url()

    with patch("src.core.oauth_token_manager.requests.post") as mock_post:
        assert manager.exchange_code("auth-code", state) is False

    mock_post.assert_not_called()


def test_anthropic_oauth_refresh_fails_closed_when_allowlist_blocks(monkeypatch, tmp_path):
    monkeypatch.setenv("LANCELOT_ANTHROPIC_OAUTH_STATE_FILE", str(tmp_path / "anthropic-oauth.json"))
    monkeypatch.setattr("src.core.oauth_token_manager.assert_url_allowed", _blocked)
    vault = DummyVault()
    vault.store(ANTHROPIC_REFRESH_TOKEN, "refresh-token", type="oauth_token")
    manager = OAuthTokenManager(vault=vault, port=8000)

    with patch("src.core.oauth_token_manager.requests.post") as mock_post:
        assert manager._refresh_token() is False

    mock_post.assert_not_called()


def test_google_oauth_exchange_fails_closed_when_allowlist_blocks(monkeypatch, tmp_path):
    monkeypatch.setenv("LANCELOT_GOOGLE_OAUTH_STATE_FILE", str(tmp_path / "google-oauth.json"))
    monkeypatch.setattr("src.core.google_oauth_manager.assert_url_allowed", _blocked)
    vault = DummyVault()
    manager = GoogleOAuthManager(vault=vault, port=8000)
    auth_url = manager.generate_auth_url("client-id", "client-secret")
    assert auth_url
    state = next(iter(manager._pending_flows))

    with patch("src.core.google_oauth_manager.requests.post") as mock_post:
        assert manager.exchange_code("auth-code", state) is False

    mock_post.assert_not_called()


def test_google_oauth_refresh_fails_closed_when_allowlist_blocks(monkeypatch, tmp_path):
    monkeypatch.setenv("LANCELOT_GOOGLE_OAUTH_STATE_FILE", str(tmp_path / "google-oauth.json"))
    monkeypatch.setattr("src.core.google_oauth_manager.assert_url_allowed", _blocked)
    vault = DummyVault()
    vault.store(VAULT_CLIENT_ID, "client-id", type="config")
    vault.store(VAULT_CLIENT_SECRET, "client-secret", type="config")
    vault.store(GOOGLE_REFRESH_TOKEN, "refresh-token", type="oauth_token")
    manager = GoogleOAuthManager(vault=vault, port=8000)

    with patch("src.core.google_oauth_manager.requests.post") as mock_post:
        assert manager._refresh_token() is False

    mock_post.assert_not_called()


def test_google_oauth_revoke_skips_remote_call_when_allowlist_blocks(monkeypatch, tmp_path):
    monkeypatch.setenv("LANCELOT_GOOGLE_OAUTH_STATE_FILE", str(tmp_path / "google-oauth.json"))
    monkeypatch.setattr("src.core.google_oauth_manager.assert_url_allowed", _blocked)
    vault = DummyVault()
    vault.store(GOOGLE_ACCESS_TOKEN, "access-token", type="oauth_token")
    manager = GoogleOAuthManager(vault=vault, port=8000)

    with patch("src.core.google_oauth_manager.requests.post") as mock_post:
        manager.revoke()

    mock_post.assert_not_called()
    assert vault.exists(GOOGLE_ACCESS_TOKEN) is False


def test_codex_oauth_exchange_fails_closed_when_allowlist_blocks(monkeypatch, tmp_path):
    monkeypatch.setenv("LANCELOT_CODEX_OAUTH_STATE_FILE", str(tmp_path / "codex-oauth.json"))
    monkeypatch.setattr("src.core.openai_codex_oauth_manager.assert_url_allowed", _blocked)
    manager = OpenAICodexOAuthManager(vault=DummyVault(), port=1455)
    _, state = manager.generate_auth_url()

    with patch("src.core.openai_codex_oauth_manager.requests.post") as mock_post:
        assert manager.exchange_code("auth-code", state) is False

    mock_post.assert_not_called()


def test_codex_oauth_refresh_fails_closed_when_allowlist_blocks(monkeypatch, tmp_path):
    monkeypatch.setenv("LANCELOT_CODEX_OAUTH_STATE_FILE", str(tmp_path / "codex-oauth.json"))
    monkeypatch.setattr("src.core.openai_codex_oauth_manager.assert_url_allowed", _blocked)
    vault = DummyVault()
    vault.store(CODEX_REFRESH_TOKEN, "refresh-token", type="oauth_token")
    manager = OpenAICodexOAuthManager(vault=vault, port=1455)

    with patch("src.core.openai_codex_oauth_manager.requests.post") as mock_post:
        assert manager._refresh_token() is False

    mock_post.assert_not_called()


def test_comms_bridge_google_chat_alert_fails_closed_when_allowlist_blocks(monkeypatch):
    monkeypatch.setenv("LANCELOT_COMMS_TYPE", "google_chat")
    monkeypatch.setenv("LANCELOT_COMMS_WEBHOOK", "https://chat.googleapis.com/webhook")
    monkeypatch.setattr("src.core.security_bridge.assert_url_allowed", _blocked)

    async def _run():
        bridge = CommsBridge()
        with patch("aiohttp.ClientSession") as mock_session:
            await bridge.send_alert("hello")
        mock_session.assert_not_called()

    asyncio.run(_run())


def test_comms_bridge_telegram_alert_fails_closed_when_allowlist_blocks(monkeypatch):
    monkeypatch.setenv("LANCELOT_COMMS_TYPE", "telegram")
    monkeypatch.setenv("LANCELOT_TELEGRAM_TOKEN", "123456:ABCDEF")
    monkeypatch.setenv("LANCELOT_TELEGRAM_CHAT_ID", "999888")
    monkeypatch.setattr("src.core.security_bridge.assert_url_allowed", _blocked)

    async def _run():
        bridge = CommsBridge()
        with patch("aiohttp.ClientSession") as mock_session:
            await bridge.send_alert("hello")
        mock_session.assert_not_called()

    asyncio.run(_run())
