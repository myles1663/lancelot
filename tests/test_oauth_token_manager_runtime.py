import os
import time
from urllib.parse import parse_qs, urlparse
from unittest.mock import MagicMock

import pytest

from src.core import oauth_token_manager as oauth
from src.core.oauth_token_manager import OAuthTokenManager


class _Vault:
    def __init__(self):
        self.entries = {}
        self.deleted = []

    def store(self, key, value, type="config"):
        self.entries[key] = (value, type)

    def retrieve(self, key, accessor_id=""):
        return self.entries.get(key, ("", ""))[0]

    def exists(self, key):
        return key in self.entries

    def delete(self, key):
        self.deleted.append(key)
        self.entries.pop(key, None)


class _Response:
    def __init__(self, payload, raise_error=False):
        self.payload = payload
        self.raise_error = raise_error

    def raise_for_status(self):
        if self.raise_error:
            raise RuntimeError("http failed")

    def json(self):
        return self.payload


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("LANCELOT_ANTHROPIC_OAUTH_STATE_FILE", str(tmp_path / "anthropic-oauth.json"))
    monkeypatch.setattr(oauth, "assert_url_allowed", lambda url, **_kwargs: url)
    oauth._oauth_token_cache.clear()
    return OAuthTokenManager(vault=_Vault(), port=8123)


def test_pkce_auth_url_persists_pending_flow_and_cleans_expired(manager, monkeypatch):
    monkeypatch.setattr(oauth.secrets, "token_urlsafe", MagicMock(side_effect=["verifier", "state"]))
    manager._pending_flows["expired"] = {"code_verifier": "old", "created_at": time.time() - oauth.PENDING_FLOW_TTL - 1}

    auth_url, state = manager.generate_auth_url()
    params = parse_qs(urlparse(auth_url).query)

    assert state == "state"
    assert state in manager._pending_flows
    assert "expired" not in manager._pending_flows
    assert params["client_id"] == [oauth.CLIENT_ID]
    assert params["redirect_uri"] == ["http://localhost:8123/callback"]
    assert params["code_challenge_method"] == ["S256"]
    assert manager._state_file.exists()


def test_exchange_code_validates_state_expiry_http_and_payload(manager, monkeypatch):
    assert manager.exchange_code("code", "missing") is False

    manager._pending_flows["expired"] = {
        "code_verifier": "verifier",
        "created_at": time.time() - oauth.PENDING_FLOW_TTL - 1,
    }
    assert manager.exchange_code("code", "expired") is False

    manager._pending_flows["http"] = {"code_verifier": "verifier", "created_at": time.time()}
    monkeypatch.setattr(oauth.requests, "post", lambda *_args, **_kwargs: _Response({}, raise_error=True))
    assert manager.exchange_code("code", "http") is False

    manager._pending_flows["missing_tokens"] = {"code_verifier": "verifier", "created_at": time.time()}
    monkeypatch.setattr(oauth.requests, "post", lambda *_args, **_kwargs: _Response({"access_token": "access"}))
    assert manager.exchange_code("code", "missing_tokens") is False


def test_exchange_code_success_stores_tokens_and_cache(manager, monkeypatch):
    calls = []
    manager._pending_flows["state"] = {"code_verifier": "verifier", "created_at": time.time()}

    def fake_post(url, data, headers, timeout):
        calls.append((url, data, headers, timeout))
        return _Response({"access_token": "access", "refresh_token": "refresh", "expires_in": 120})

    monkeypatch.setattr(oauth.requests, "post", fake_post)

    assert manager.exchange_code("auth-code", "state") is True

    assert calls[0][0] == oauth.ANTHROPIC_TOKEN_URL
    assert calls[0][1]["grant_type"] == "authorization_code"
    assert calls[0][1]["code_verifier"] == "verifier"
    assert manager._vault.retrieve(oauth.VAULT_ACCESS_TOKEN) == "access"
    assert manager._vault.retrieve(oauth.VAULT_REFRESH_TOKEN) == "refresh"
    assert oauth.get_oauth_token() == "access"


def test_get_valid_token_refreshes_expired_and_best_effort_near_expiry(manager, monkeypatch):
    assert manager.get_valid_token() is None

    now = time.time()
    manager._vault.store(oauth.VAULT_ACCESS_TOKEN, "current", type="oauth_token")
    manager._vault.store(oauth.VAULT_REFRESH_TOKEN, "refresh", type="oauth_token")
    manager._vault.store(oauth.VAULT_TOKEN_EXPIRY, str(now - 1), type="metadata")
    monkeypatch.setattr(manager, "_refresh_token", MagicMock(return_value=False))
    assert manager.get_valid_token() is None

    monkeypatch.setattr(manager, "_refresh_token", MagicMock(side_effect=lambda: manager._vault.store(oauth.VAULT_ACCESS_TOKEN, "new", type="oauth_token") or True))
    assert manager.get_valid_token() == "new"

    manager._vault.store(oauth.VAULT_ACCESS_TOKEN, "near-expiry", type="oauth_token")
    manager._vault.store(oauth.VAULT_TOKEN_EXPIRY, str(time.time() + 1), type="metadata")
    manager._refresh_token = MagicMock(return_value=False)
    assert manager.get_valid_token() == "near-expiry"
    manager._refresh_token.assert_called_once()


def test_token_status_and_invalid_expiry(manager):
    assert manager.get_token_status() == {"configured": False, "valid": False, "status": "not_configured"}

    manager._vault.store(oauth.VAULT_ACCESS_TOKEN, "access", type="oauth_token")
    manager._vault.store(oauth.VAULT_TOKEN_EXPIRY, str(time.time() + oauth.REFRESH_WINDOW + 100), type="metadata")
    assert manager.get_token_status()["status"] == "active"

    manager._vault.store(oauth.VAULT_TOKEN_EXPIRY, str(time.time() + 1), type="metadata")
    assert manager.get_token_status()["status"] == "expiring"

    manager._vault.store(oauth.VAULT_TOKEN_EXPIRY, "not-a-number", type="metadata")
    status = manager.get_token_status()
    assert status["status"] == "expired"
    assert status["valid"] is False


def test_revoke_clears_vault_cache_and_legacy_environment(manager, monkeypatch):
    for key in (oauth.VAULT_ACCESS_TOKEN, oauth.VAULT_REFRESH_TOKEN, oauth.VAULT_TOKEN_EXPIRY):
        manager._vault.store(key, "value", type="oauth_token")
    oauth._oauth_token_cache["access_token"] = "access"
    monkeypatch.setenv(oauth.ENV_OAUTH_TOKEN, "legacy")

    manager.revoke()

    assert manager._vault.deleted == [
        oauth.VAULT_ACCESS_TOKEN,
        oauth.VAULT_REFRESH_TOKEN,
        oauth.VAULT_TOKEN_EXPIRY,
    ]
    assert oauth.get_oauth_token() is None
    assert oauth.ENV_OAUTH_TOKEN not in os.environ


def test_refresh_token_handles_missing_empty_http_missing_payload_and_success(manager, monkeypatch):
    assert manager._refresh_token() is False

    manager._vault.store(oauth.VAULT_REFRESH_TOKEN, "", type="oauth_token")
    assert manager._refresh_token() is False

    manager._vault.store(oauth.VAULT_REFRESH_TOKEN, "refresh", type="oauth_token")
    monkeypatch.setattr(oauth.requests, "post", lambda *_args, **_kwargs: _Response({}, raise_error=True))
    assert manager._refresh_token() is False

    monkeypatch.setattr(oauth.requests, "post", lambda *_args, **_kwargs: _Response({"access_token": "access"}))
    assert manager._refresh_token() is False

    calls = []

    def fake_post(url, data, headers, timeout):
        calls.append((url, data, headers, timeout))
        return _Response({"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 60})

    monkeypatch.setattr(oauth.requests, "post", fake_post)
    assert manager._refresh_token() is True

    assert calls[0][1]["grant_type"] == "refresh_token"
    assert manager._vault.retrieve(oauth.VAULT_ACCESS_TOKEN) == "new-access"
    assert manager._vault.retrieve(oauth.VAULT_REFRESH_TOKEN) == "new-refresh"


def test_pending_flow_load_and_save_error_paths(tmp_path, monkeypatch, caplog):
    state_file = tmp_path / "state.json"
    monkeypatch.setenv("LANCELOT_ANTHROPIC_OAUTH_STATE_FILE", str(state_file))

    state_file.write_text("", encoding="utf-8")
    empty = OAuthTokenManager(vault=_Vault())
    assert empty._pending_flows == {}

    state_file.write_text("{bad json", encoding="utf-8")
    bad = OAuthTokenManager(vault=_Vault())
    assert bad._pending_flows == {}

    state_file.write_text('{"fresh": {"created_at": 9999999999, "code_verifier": "v"}, "bad": "value"}', encoding="utf-8")
    loaded = OAuthTokenManager(vault=_Vault())
    assert loaded._pending_flows == {"fresh": {"created_at": 9999999999, "code_verifier": "v"}}

    loaded._state_file = tmp_path / "missing-parent" / "state.json"
    with caplog.at_level("WARNING"):
        loaded._save_pending_flows()
    assert "Failed to persist Anthropic OAuth pending state" in caplog.text


def test_background_refresh_hydrates_cache_and_checks_expiring_tokens(manager, monkeypatch):
    manager._vault.store(oauth.VAULT_ACCESS_TOKEN, "access", type="oauth_token")
    manager._vault.store(oauth.VAULT_REFRESH_TOKEN, "refresh", type="oauth_token")
    manager._vault.store(oauth.VAULT_TOKEN_EXPIRY, str(time.time() + 1), type="metadata")
    refresh = MagicMock(return_value=True)
    monkeypatch.setattr(manager, "_refresh_token", refresh)
    monkeypatch.setattr(oauth, "BACKGROUND_CHECK_INTERVAL", 0.01)

    manager.start_background_refresh()
    time.sleep(0.03)
    manager.start_background_refresh()
    manager.stop_background_refresh()

    assert oauth.get_oauth_token() == "access"
    assert refresh.call_count >= 1
    assert manager._refresh_thread is None


def test_background_refresh_logs_and_continues_on_vault_error(manager, monkeypatch, caplog):
    monkeypatch.setattr(oauth, "BACKGROUND_CHECK_INTERVAL", 0.01)
    manager._vault.exists = MagicMock(side_effect=[False, RuntimeError("vault failed")])

    with caplog.at_level("WARNING"):
        manager.start_background_refresh()
        time.sleep(0.02)
        manager.stop_background_refresh()

    assert "Background refresh error" in caplog.text


def test_module_singleton_roundtrip(manager):
    oauth.set_oauth_manager(manager)

    assert oauth.get_oauth_manager() is manager
