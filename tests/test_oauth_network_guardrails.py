import asyncio
import time
from unittest.mock import patch

from src.core.outbound_http import OutboundNetworkError
from src.core.google_oauth_manager import (
    GoogleOAuthManager,
    VAULT_ACCESS_TOKEN as GOOGLE_ACCESS_TOKEN,
    VAULT_CALENDAR_TOKEN,
    VAULT_CLIENT_ID,
    VAULT_CLIENT_SECRET,
    VAULT_GMAIL_TOKEN,
    VAULT_REFRESH_TOKEN as GOOGLE_REFRESH_TOKEN,
    VAULT_TOKEN_EXPIRY,
    get_google_oauth_manager,
    set_google_oauth_manager,
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


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


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


def test_google_oauth_exchange_success_stores_and_fans_out_tokens(monkeypatch, tmp_path):
    monkeypatch.setenv("LANCELOT_GOOGLE_OAUTH_STATE_FILE", str(tmp_path / "google-oauth.json"))
    vault = DummyVault()
    manager = GoogleOAuthManager(vault=vault, port=8000)
    manager.generate_auth_url("client-id", "client-secret")
    state = next(iter(manager._pending_flows))

    with patch("src.core.google_oauth_manager.assert_url_allowed", return_value="https://oauth2.googleapis.com/token"), \
            patch("src.core.google_oauth_manager.requests.post", return_value=_Response({
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "expires_in": 600,
            })) as mock_post, \
            patch.object(manager, "start_background_refresh") as start_refresh:
        assert manager.exchange_code("auth-code", state) is True

    assert mock_post.call_args.kwargs["data"]["code_verifier"]
    assert vault.retrieve(GOOGLE_ACCESS_TOKEN) == "access-token"
    assert vault.retrieve(GOOGLE_REFRESH_TOKEN) == "refresh-token"
    assert vault.retrieve(VAULT_GMAIL_TOKEN) == "access-token"
    assert vault.retrieve(VAULT_CALENDAR_TOKEN) == "access-token"
    assert not manager._pending_flows
    start_refresh.assert_called_once()


def test_google_oauth_exchange_rejects_unknown_expired_and_unconfigured_flows(monkeypatch, tmp_path):
    monkeypatch.setenv("LANCELOT_GOOGLE_OAUTH_STATE_FILE", str(tmp_path / "google-oauth.json"))
    manager = GoogleOAuthManager(vault=DummyVault(), port=8000)

    assert manager.exchange_code("auth-code", "missing-state") is False

    manager._pending_flows["expired-state"] = {
        "code_verifier": "verifier",
        "created_at": time.time() - 901,
    }
    assert manager.exchange_code("auth-code", "expired-state") is False

    manager._pending_flows["no-creds"] = {
        "code_verifier": "verifier",
        "created_at": time.time(),
    }
    assert manager.exchange_code("auth-code", "no-creds") is False


def test_google_oauth_exchange_rejects_response_without_access_token(monkeypatch, tmp_path):
    monkeypatch.setenv("LANCELOT_GOOGLE_OAUTH_STATE_FILE", str(tmp_path / "google-oauth.json"))
    vault = DummyVault()
    manager = GoogleOAuthManager(vault=vault, port=8000)
    manager.generate_auth_url("client-id", "client-secret")
    state = next(iter(manager._pending_flows))

    with patch("src.core.google_oauth_manager.assert_url_allowed", return_value="https://oauth2.googleapis.com/token"), \
            patch("src.core.google_oauth_manager.requests.post", return_value=_Response({"expires_in": 600})):
        assert manager.exchange_code("auth-code", state) is False


def test_google_oauth_status_and_valid_token_states(monkeypatch, tmp_path):
    monkeypatch.setenv("LANCELOT_GOOGLE_OAUTH_STATE_FILE", str(tmp_path / "google-oauth.json"))
    vault = DummyVault()
    manager = GoogleOAuthManager(vault=vault, port=8000)

    assert manager.get_status()["status"] == "not_configured"
    assert manager.get_valid_token() is None

    vault.store(VAULT_CLIENT_ID, "client-id")
    assert manager.get_status()["status"] == "awaiting_authorization"

    vault.store(GOOGLE_ACCESS_TOKEN, "access-token")
    vault.store(GOOGLE_REFRESH_TOKEN, "refresh-token")
    vault.store(VAULT_TOKEN_EXPIRY, str(time.time() + 1000))
    assert manager.get_status()["status"] == "healthy"
    assert manager.get_valid_token() == "access-token"

    vault.store(VAULT_TOKEN_EXPIRY, str(time.time() + 1))
    with patch.object(manager, "_refresh_token", return_value=True) as refresh:
        assert manager.get_status()["status"] == "expiring_soon"
        assert manager.get_valid_token() == "access-token"
    refresh.assert_called_once()

    vault.store(VAULT_TOKEN_EXPIRY, str(time.time() - 1))
    with patch.object(manager, "_refresh_token", return_value=False):
        assert manager.get_status()["status"] == "expired"
        assert manager.get_valid_token() is None


def test_google_oauth_refresh_token_success_and_failure_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("LANCELOT_GOOGLE_OAUTH_STATE_FILE", str(tmp_path / "google-oauth.json"))
    vault = DummyVault()
    manager = GoogleOAuthManager(vault=vault, port=8000)

    assert manager._refresh_token() is False

    vault.store(GOOGLE_REFRESH_TOKEN, "refresh-token")
    vault.store(VAULT_CLIENT_ID, "client-id")
    vault.store(VAULT_CLIENT_SECRET, "client-secret")
    with patch("src.core.google_oauth_manager.assert_url_allowed", return_value="https://oauth2.googleapis.com/token"), \
            patch("src.core.google_oauth_manager.requests.post", return_value=_Response({
                "access_token": "new-access",
                "expires_in": 900,
            })):
        assert manager._refresh_token() is True

    assert vault.retrieve(GOOGLE_ACCESS_TOKEN) == "new-access"
    assert vault.retrieve(GOOGLE_REFRESH_TOKEN) == "refresh-token"

    with patch("src.core.google_oauth_manager.assert_url_allowed", return_value="https://oauth2.googleapis.com/token"), \
            patch("src.core.google_oauth_manager.requests.post", return_value=_Response({})):
        assert manager._refresh_token() is False


def test_google_oauth_recover_from_vault_fans_out_and_handles_failures(monkeypatch, tmp_path):
    monkeypatch.setenv("LANCELOT_GOOGLE_OAUTH_STATE_FILE", str(tmp_path / "google-oauth.json"))
    vault = DummyVault()
    manager = GoogleOAuthManager(vault=vault, port=8000)

    assert manager.recover_from_vault() is False

    vault.store(GOOGLE_ACCESS_TOKEN, "access-token")
    assert manager.recover_from_vault() is False

    vault.store(GOOGLE_REFRESH_TOKEN, "refresh-token")
    vault.store(VAULT_TOKEN_EXPIRY, str(time.time() - 1))
    with patch.object(manager, "_refresh_token", return_value=False):
        assert manager.recover_from_vault() is False

    vault.store(VAULT_TOKEN_EXPIRY, str(time.time() + 600))
    with patch.object(manager, "start_background_refresh") as start_refresh:
        assert manager.recover_from_vault() is True

    assert vault.retrieve(VAULT_GMAIL_TOKEN) == "access-token"
    assert vault.retrieve(VAULT_CALENDAR_TOKEN) == "access-token"
    start_refresh.assert_called_once()


def test_google_oauth_revoke_calls_remote_when_token_available(monkeypatch, tmp_path):
    monkeypatch.setenv("LANCELOT_GOOGLE_OAUTH_STATE_FILE", str(tmp_path / "google-oauth.json"))
    vault = DummyVault()
    for key in (
        GOOGLE_ACCESS_TOKEN,
        GOOGLE_REFRESH_TOKEN,
        VAULT_TOKEN_EXPIRY,
        VAULT_CLIENT_ID,
        VAULT_CLIENT_SECRET,
        VAULT_GMAIL_TOKEN,
        VAULT_CALENDAR_TOKEN,
    ):
        vault.store(key, f"{key}-value")
    manager = GoogleOAuthManager(vault=vault, port=8000)

    with patch("src.core.google_oauth_manager.assert_url_allowed", return_value="https://oauth2.googleapis.com/revoke"), \
            patch("src.core.google_oauth_manager.requests.post") as mock_post:
        manager.revoke()

    assert mock_post.call_args.kwargs["data"] == {"token": f"{GOOGLE_ACCESS_TOKEN}-value"}
    assert all(not vault.exists(key) for key in (
        GOOGLE_ACCESS_TOKEN,
        GOOGLE_REFRESH_TOKEN,
        VAULT_TOKEN_EXPIRY,
        VAULT_CLIENT_ID,
        VAULT_CLIENT_SECRET,
        VAULT_GMAIL_TOKEN,
        VAULT_CALENDAR_TOKEN,
    ))


def test_google_oauth_pending_flow_load_handles_empty_invalid_and_stale_files(monkeypatch, tmp_path):
    state_file = tmp_path / "google-oauth.json"
    monkeypatch.setenv("LANCELOT_GOOGLE_OAUTH_STATE_FILE", str(state_file))

    state_file.write_text("", encoding="utf-8")
    assert GoogleOAuthManager(vault=DummyVault(), port=8000)._pending_flows == {}

    state_file.write_text("{not json", encoding="utf-8")
    assert GoogleOAuthManager(vault=DummyVault(), port=8000)._pending_flows == {}

    state_file.write_text(
        '{"fresh":{"code_verifier":"v","created_at":9999999999},"bad":"shape","stale":{"created_at":0}}',
        encoding="utf-8",
    )
    assert set(GoogleOAuthManager(vault=DummyVault(), port=8000)._pending_flows) == {"fresh"}


def test_google_oauth_singleton_set_get():
    manager = object()

    set_google_oauth_manager(manager)
    assert get_google_oauth_manager() is manager

    set_google_oauth_manager(None)
    assert get_google_oauth_manager() is None


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
