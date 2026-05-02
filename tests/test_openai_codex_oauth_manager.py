import sys
import base64
import json
import time
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.openai_codex_oauth_manager import (  # noqa: E402
    DEFAULT_CALLBACK_PORT,
    OpenAICodexOAuthManager,
    VAULT_ACCESS_TOKEN,
    VAULT_ACCOUNT_ID,
    VAULT_REFRESH_TOKEN,
    VAULT_TOKEN_EXPIRY,
    get_codex_oauth_token,
    get_openai_codex_manager,
    set_openai_codex_manager,
)


class _DummyVault:
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


def _jwt_with_claims(claims):
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode("utf-8")).decode("ascii").rstrip("=")
    return f"header.{payload}.signature"


def test_generate_auth_url_uses_supported_codex_scopes():
    manager = OpenAICodexOAuthManager(vault=_DummyVault(), port=DEFAULT_CALLBACK_PORT)

    auth_url, state = manager.generate_auth_url()

    parsed = urlparse(auth_url)
    params = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "auth.openai.com"
    assert parsed.path == "/oauth/authorize"
    assert state
    assert params["scope"] == ["openid profile email offline_access"]
    assert params["redirect_uri"] == [f"http://localhost:{DEFAULT_CALLBACK_PORT}/auth/callback"]


def test_exchange_code_success_stores_tokens_and_account_id(monkeypatch, tmp_path):
    monkeypatch.setenv("LANCELOT_CODEX_OAUTH_STATE_FILE", str(tmp_path / "codex-oauth.json"))
    vault = _DummyVault()
    manager = OpenAICodexOAuthManager(vault=vault, port=DEFAULT_CALLBACK_PORT)
    _, state = manager.generate_auth_url()
    access_token = _jwt_with_claims({"sub": "account-123"})

    with patch("src.core.openai_codex_oauth_manager.assert_url_allowed", return_value="https://auth.openai.com/oauth/token"), \
            patch("src.core.openai_codex_oauth_manager.requests.post", return_value=_Response({
                "access_token": access_token,
                "refresh_token": "refresh-token",
                "expires_in": 600,
            })) as mock_post:
        assert manager.exchange_code("auth-code", state) is True

    assert mock_post.call_args.kwargs["data"]["code_verifier"]
    assert vault.retrieve(VAULT_ACCESS_TOKEN) == access_token
    assert vault.retrieve(VAULT_REFRESH_TOKEN) == "refresh-token"
    assert vault.retrieve(VAULT_ACCOUNT_ID) == "account-123"
    assert get_codex_oauth_token() == access_token
    assert not manager._pending_flows


def test_exchange_code_rejects_unknown_expired_and_missing_access_token(monkeypatch, tmp_path):
    monkeypatch.setenv("LANCELOT_CODEX_OAUTH_STATE_FILE", str(tmp_path / "codex-oauth.json"))
    manager = OpenAICodexOAuthManager(vault=_DummyVault(), port=DEFAULT_CALLBACK_PORT)

    assert manager.exchange_code("auth-code", "missing-state") is False

    manager._pending_flows["expired-state"] = {
        "code_verifier": "verifier",
        "created_at": time.time() - 601,
    }
    assert manager.exchange_code("auth-code", "expired-state") is False

    _, state = manager.generate_auth_url()
    with patch("src.core.openai_codex_oauth_manager.assert_url_allowed", return_value="https://auth.openai.com/oauth/token"), \
            patch("src.core.openai_codex_oauth_manager.requests.post", return_value=_Response({})):
        assert manager.exchange_code("auth-code", state) is False


def test_token_status_and_valid_token_states(monkeypatch, tmp_path):
    monkeypatch.setenv("LANCELOT_CODEX_OAUTH_STATE_FILE", str(tmp_path / "codex-oauth.json"))
    vault = _DummyVault()
    manager = OpenAICodexOAuthManager(vault=vault, port=DEFAULT_CALLBACK_PORT)

    assert manager.get_token_status()["status"] == "not_configured"
    assert manager.get_valid_token() is None

    vault.store(VAULT_ACCESS_TOKEN, "access-token")
    vault.store(VAULT_ACCOUNT_ID, "account-123")
    vault.store(VAULT_TOKEN_EXPIRY, str(time.time() + 1000))
    status = manager.get_token_status()
    assert status["status"] == "active"
    assert status["account_id"] == "account-123"
    assert manager.get_valid_token() == "access-token"

    vault.store(VAULT_TOKEN_EXPIRY, str(time.time() + 1))
    with patch.object(manager, "_refresh_token", return_value=True) as refresh:
        assert manager.get_token_status()["status"] == "expiring"
        assert manager.get_valid_token() == "access-token"
    refresh.assert_called_once()

    vault.store(VAULT_TOKEN_EXPIRY, str(time.time() - 1))
    with patch.object(manager, "_refresh_token", return_value=False):
        assert manager.get_token_status()["status"] == "expired"
        assert manager.get_valid_token() is None


def test_refresh_token_success_and_failure_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("LANCELOT_CODEX_OAUTH_STATE_FILE", str(tmp_path / "codex-oauth.json"))
    vault = _DummyVault()
    manager = OpenAICodexOAuthManager(vault=vault, port=DEFAULT_CALLBACK_PORT)

    assert manager._refresh_token() is False

    vault.store(VAULT_REFRESH_TOKEN, "")
    assert manager._refresh_token() is False

    vault.store(VAULT_REFRESH_TOKEN, "refresh-token")
    with patch("src.core.openai_codex_oauth_manager.assert_url_allowed", return_value="https://auth.openai.com/oauth/token"), \
            patch("src.core.openai_codex_oauth_manager.requests.post", return_value=_Response({
                "access_token": "new-access",
                "expires_in": 900,
            })):
        assert manager._refresh_token() is True

    assert vault.retrieve(VAULT_ACCESS_TOKEN) == "new-access"
    assert vault.retrieve(VAULT_REFRESH_TOKEN) == "refresh-token"
    assert get_codex_oauth_token() == "new-access"

    with patch("src.core.openai_codex_oauth_manager.assert_url_allowed", return_value="https://auth.openai.com/oauth/token"), \
            patch("src.core.openai_codex_oauth_manager.requests.post", return_value=_Response({})):
        assert manager._refresh_token() is False


def test_revoke_clears_tokens_and_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("LANCELOT_CODEX_OAUTH_STATE_FILE", str(tmp_path / "codex-oauth.json"))
    vault = _DummyVault()
    for key in (VAULT_ACCESS_TOKEN, VAULT_REFRESH_TOKEN, VAULT_TOKEN_EXPIRY, VAULT_ACCOUNT_ID):
        vault.store(key, f"{key}-value")
    manager = OpenAICodexOAuthManager(vault=vault, port=DEFAULT_CALLBACK_PORT)
    manager._store_tokens("cached-token", "refresh-token", 600, "account-123")

    manager.revoke()

    assert all(not vault.exists(key) for key in (VAULT_ACCESS_TOKEN, VAULT_REFRESH_TOKEN, VAULT_TOKEN_EXPIRY, VAULT_ACCOUNT_ID))
    assert get_codex_oauth_token() is None


def test_pending_flow_load_handles_empty_invalid_and_stale_files(monkeypatch, tmp_path):
    state_file = tmp_path / "codex-oauth.json"
    monkeypatch.setenv("LANCELOT_CODEX_OAUTH_STATE_FILE", str(state_file))

    state_file.write_text("", encoding="utf-8")
    assert OpenAICodexOAuthManager(vault=_DummyVault(), port=DEFAULT_CALLBACK_PORT)._pending_flows == {}

    state_file.write_text("{not json", encoding="utf-8")
    assert OpenAICodexOAuthManager(vault=_DummyVault(), port=DEFAULT_CALLBACK_PORT)._pending_flows == {}

    state_file.write_text(
        '{"fresh":{"code_verifier":"v","created_at":9999999999},"bad":"shape","stale":{"created_at":0}}',
        encoding="utf-8",
    )
    assert set(OpenAICodexOAuthManager(vault=_DummyVault(), port=DEFAULT_CALLBACK_PORT)._pending_flows) == {"fresh"}


def test_singleton_set_get(monkeypatch, tmp_path):
    monkeypatch.setenv("LANCELOT_CODEX_OAUTH_STATE_FILE", str(tmp_path / "codex-oauth.json"))
    manager = OpenAICodexOAuthManager(vault=_DummyVault(), port=DEFAULT_CALLBACK_PORT)

    set_openai_codex_manager(manager)
    assert get_openai_codex_manager() is manager
