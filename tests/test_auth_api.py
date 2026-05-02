"""Tests for War Room Authentication API (auth_api.py)."""

import importlib
import json
import os
import sys
import hashlib
import hmac
import time
import types
import uuid

import bcrypt
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import src.core.auth_api as auth_module
from src.core.auth_api import (
    auth_config,
    login,
    validate_token,
    logout,
    reset_password,
    verify_warroom_session,
    resolve_operator_identity,
    resolve_request_capabilities,
    request_has_capability,
    get_api_key_identity,
    init_auth_api,
    _verify_password,
    _cleanup_expired,
)
from src.core.operator_identity import OperatorIdentity, resolve_operator_id


# ── Helpers ──────────────────────────────────────────────────────


def _make_mock_request(token=None, json_data=None, client_host="127.0.0.1", cookie_token=None, scheme="http"):
    """Create a mock FastAPI Request."""
    request = MagicMock()
    headers = {}
    if token:
        headers["authorization"] = f"Bearer {token}"
    request.headers = headers
    request.cookies = {}
    if cookie_token:
        request.cookies[auth_module.get_warroom_session_cookie_name()] = cookie_token
    if json_data is not None:
        request.json = AsyncMock(return_value=json_data)
    if client_host:
        client = MagicMock()
        client.host = client_host
        request.client = client
    else:
        request.client = None
    request.url = MagicMock()
    request.url.scheme = scheme
    return request


def _insert_session(token, username="testuser", expires_in=1800, capabilities=None):
    """Manually insert a session into the session store."""
    identity = OperatorIdentity(
        operator_id=resolve_operator_id(username),
        display_name=username,
        session_id=str(uuid.uuid4()),
        session_started_at="2026-01-01T00:00:00Z",
        auth_method="local",
        ip_address="127.0.0.1",
    )
    auth_module._sessions[token] = {
        "expires_at": time.time() + expires_in,
        "username": username,
        "operator_identity": identity,
        "capabilities": sorted(capabilities or {"warroom.login"}),
    }
    return identity


def _response_json(response):
    if isinstance(response, dict):
        return response
    return json.loads(response.body)


def _response_cookie_token(response):
    cookie_header = response.headers.get("set-cookie", "")
    prefix = auth_module.get_warroom_session_cookie_name() + "="
    if prefix not in cookie_header:
        return ""
    return cookie_header.split(prefix, 1)[1].split(";", 1)[0]


# ── _verify_password ─────────────────────────────────────────────


class TestVerifyPassword:

    def test_bcrypt_hash_match(self):
        stored_hash = bcrypt.hashpw(b"my-secret-password", bcrypt.gensalt()).decode("utf-8")
        assert _verify_password("my-secret-password", stored_hash) is True

    def test_bcrypt_hash_mismatch(self):
        stored_hash = bcrypt.hashpw(b"correct-password", bcrypt.gensalt()).decode("utf-8")
        assert _verify_password("wrong-password", stored_hash) is False

    def test_sha256_hash_match(self):
        password = "my-secret-password"
        stored_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
        assert _verify_password(password, stored_hash) is True

    def test_sha256_hash_mismatch(self):
        stored_hash = hashlib.sha256(b"correct-password").hexdigest()
        assert _verify_password("wrong-password", stored_hash) is False

    def test_plaintext_match(self):
        assert _verify_password("mypass", "mypass") is True

    def test_plaintext_mismatch(self):
        assert _verify_password("mypass", "otherpass") is False

    def test_sha256_is_64_chars(self):
        # Verify the detection logic: 64-char string triggers hash path
        stored = "a" * 64
        # Won't match, but should not raise
        assert _verify_password("test", stored) is False

    def test_plaintext_path_for_short_string(self):
        # Strings shorter than 64 chars use plaintext comparison
        assert _verify_password("short", "short") is True

    def test_constant_time_comparison(self):
        # Ensure hmac.compare_digest is used (implementation detail,
        # but critical for security). We verify by checking the function
        # works correctly rather than timing.
        password = "test123"
        hashed = hashlib.sha256(password.encode("utf-8")).hexdigest()
        assert _verify_password(password, hashed) is True


# ── login endpoint ───────────────────────────────────────────────


class TestLogin:

    def setup_method(self):
        auth_module._sessions.clear()
        auth_module._login_failures.clear()
        auth_module._audit_logger = None


class TestAuthorizationCapabilities:

    def setup_method(self):
        auth_module._sessions.clear()

    def test_oidc_groups_gain_admin_capabilities_only_when_admin_group_matches(self):
        with patch.dict(os.environ, {"OIDC_ADMIN_GROUPS": "admins,secops"}):
            admin_caps = auth_module._capabilities_for_oidc_groups(["engineering", "admins"])
            user_caps = auth_module._capabilities_for_oidc_groups(["engineering"])

        assert "platform.admin" in admin_caps
        assert "warroom.login" in admin_caps
        assert "platform.admin" not in user_caps
        assert "warroom.login" in user_caps

    def test_enforce_allowed_groups_requires_explicit_policy(self, monkeypatch):
        monkeypatch.delenv("OIDC_ALLOWED_GROUPS", raising=False)
        monkeypatch.delenv("OIDC_ALLOW_ANY_AUTHENTICATED", raising=False)

        with pytest.raises(PermissionError, match="OIDC_ALLOWED_GROUPS"):
            auth_module._enforce_allowed_groups(["engineering"])

    def test_enforce_allowed_groups_allows_explicit_open_mode(self, monkeypatch):
        monkeypatch.delenv("OIDC_ALLOWED_GROUPS", raising=False)
        monkeypatch.setenv("OIDC_ALLOW_ANY_AUTHENTICATED", "true")

        auth_module._enforce_allowed_groups(["engineering"])

    def test_resolve_request_capabilities_reads_session_capabilities(self):
        token = "session-token"
        _insert_session(
            token,
            capabilities={"warroom.login", "soul.admin"},
        )
        request = _make_mock_request(cookie_token=token)

        caps = resolve_request_capabilities(request)

        assert caps == {"warroom.login", "soul.admin"}
        assert request_has_capability(request, "soul.admin") is True
        assert request_has_capability(request, "platform.admin") is False

    def test_api_key_identity_path_has_admin_capabilities(self):
        request = _make_mock_request(token="api-token")

        caps = resolve_request_capabilities(request)

        assert "platform.admin" in caps
        assert "soul.admin" in caps

    @patch("src.core.auth_api._get_warroom_password", return_value="correct-pass")
    @patch("src.core.auth_api._get_warroom_username", return_value="admin")
    @pytest.mark.asyncio
    async def test_login_valid_credentials(self, mock_user, mock_pass):
        request = _make_mock_request(
            json_data={"username": "admin", "password": "correct-pass"}
        )
        response = await login(request)
        payload = _response_json(response)
        assert payload["username"] == "admin"
        assert "operator_id" in payload
        assert "session_id" in payload
        assert payload["expires_in"] > 0
        assert _response_cookie_token(response)

    @patch("src.core.auth_api._get_warroom_password", return_value="correct-pass")
    @patch("src.core.auth_api._get_warroom_username", return_value="admin")
    @pytest.mark.asyncio
    async def test_login_invalid_password(self, mock_user, mock_pass):
        request = _make_mock_request(
            json_data={"username": "admin", "password": "wrong-pass"}
        )
        response = await login(request)
        assert response.status_code == 401

    @patch("src.core.auth_api._get_warroom_password", return_value="correct-pass")
    @patch("src.core.auth_api._get_warroom_username", return_value="admin")
    @pytest.mark.asyncio
    async def test_login_invalid_username(self, mock_user, mock_pass):
        request = _make_mock_request(
            json_data={"username": "hacker", "password": "correct-pass"}
        )
        response = await login(request)
        assert response.status_code == 401

    @patch("src.core.auth_api._get_warroom_password", return_value="")
    @patch("src.core.auth_api._get_warroom_username", return_value="")
    @pytest.mark.asyncio
    async def test_login_credentials_not_configured(self, mock_user, mock_pass):
        request = _make_mock_request(
            json_data={"username": "admin", "password": "pass"}
        )
        response = await login(request)
        assert response.status_code == 503

    @patch("src.core.auth_api._get_warroom_password")
    @patch("src.core.auth_api._get_warroom_username", return_value="admin")
    @patch("src.core.auth_api._persist_warroom_password_secret")
    @pytest.mark.asyncio
    async def test_login_with_sha256_hashed_password(self, mock_persist, mock_user, mock_pass):
        plain = "my-secure-password"
        hashed = hashlib.sha256(plain.encode("utf-8")).hexdigest()
        mock_pass.return_value = hashed

        request = _make_mock_request(
            json_data={"username": "admin", "password": plain}
        )
        response = await login(request)
        payload = _response_json(response)
        assert payload["username"] == "admin"
        mock_persist.assert_called_once()
        upgraded_hash = mock_persist.call_args.args[0]
        assert upgraded_hash.startswith("$2")
        assert bcrypt.checkpw(plain.encode("utf-8"), upgraded_hash.encode("utf-8"))

    @patch("src.core.auth_api._get_warroom_password", return_value="pass")
    @patch("src.core.auth_api._get_warroom_username", return_value="admin")
    @pytest.mark.asyncio
    async def test_login_creates_session_in_store(self, mock_user, mock_pass):
        request = _make_mock_request(
            json_data={"username": "admin", "password": "pass"}
        )
        response = await login(request)
        token = _response_cookie_token(response)
        assert token in auth_module._sessions
        session = auth_module._sessions[token]
        assert session["username"] == "admin"
        assert "operator_identity" in session
        assert isinstance(session["operator_identity"], OperatorIdentity)

    @patch("src.core.auth_api._get_warroom_password", return_value="pass")
    @patch("src.core.auth_api._get_warroom_username", return_value="admin")
    @pytest.mark.asyncio
    async def test_login_operator_id_is_deterministic(self, mock_user, mock_pass):
        request = _make_mock_request(
            json_data={"username": "admin", "password": "pass"}
        )
        response = await login(request)
        payload = _response_json(response)
        expected_id = resolve_operator_id("admin")
        assert payload["operator_id"] == expected_id

    @patch("src.core.auth_api._get_warroom_password", return_value="pass")
    @patch("src.core.auth_api._get_warroom_username", return_value="admin")
    @pytest.mark.asyncio
    async def test_login_logs_failed_attempt(self, mock_user, mock_pass):
        audit = MagicMock()
        init_auth_api(audit_logger=audit)

        request = _make_mock_request(
            json_data={"username": "admin", "password": "wrong"}
        )
        await login(request)
        audit.log_event.assert_called_once()
        assert audit.log_event.call_args[0][0] == "AUTH_LOGIN_FAILED"

        auth_module._audit_logger = None

    @patch("src.core.auth_api._get_warroom_password", return_value="pass")
    @patch("src.core.auth_api._get_warroom_username", return_value="admin")
    @pytest.mark.asyncio
    async def test_login_logs_success(self, mock_user, mock_pass):
        audit = MagicMock()
        init_auth_api(audit_logger=audit)

        request = _make_mock_request(
            json_data={"username": "admin", "password": "pass"}
        )
        await login(request)
        audit.log_event.assert_called_once()
        assert audit.log_event.call_args[0][0] == "AUTH_LOGIN_SUCCESS"

        auth_module._audit_logger = None

    @patch("src.core.auth_api._get_warroom_password", return_value="correct-pass")
    @patch("src.core.auth_api._get_warroom_username", return_value="admin")
    @pytest.mark.asyncio
    async def test_login_rate_limited_after_repeated_failures(self, mock_user, mock_pass, monkeypatch):
        monkeypatch.setattr(auth_module, "_LOGIN_RATE_LIMIT_MAX_FAILURES", 2)
        monkeypatch.setattr(auth_module, "_LOGIN_RATE_LIMIT_WINDOW_S", 300)

        request = _make_mock_request(
            json_data={"username": "admin", "password": "wrong-pass"},
            client_host="10.0.0.5",
        )

        first = await login(request)
        second = await login(request)
        third = await login(request)

        assert first.status_code == 401
        assert second.status_code == 401
        assert third.status_code == 429

    @patch("src.core.auth_api._get_warroom_password", return_value="correct-pass")
    @patch("src.core.auth_api._get_warroom_username", return_value="admin")
    @pytest.mark.asyncio
    async def test_login_disabled_in_oidc_mode(self, mock_user, mock_pass, monkeypatch):
        monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "oidc")
        request = _make_mock_request(
            json_data={"username": "admin", "password": "correct-pass"},
        )
        response = await login(request)
        assert response.status_code == 400


class TestAuthConfig:

    @pytest.mark.asyncio
    async def test_local_auth_config(self, monkeypatch):
        monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "local")
        monkeypatch.setenv("WARROOM_USERNAME", "admin")
        monkeypatch.setenv("WARROOM_PASSWORD_RESET_CODE", "reset-code")

        response = await auth_config()

        assert response["provider"] == "local"
        assert response["local"]["enabled"] is True
        assert response["local"]["password_reset_enabled"] is True
        assert response["local"]["username_hint"] == "admin"

    @pytest.mark.asyncio
    async def test_oidc_auth_config(self, monkeypatch):
        monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "oidc")
        monkeypatch.setenv("OIDC_ISSUER_URL", "https://issuer.example")
        monkeypatch.setenv("OIDC_CLIENT_ID", "client-id")
        monkeypatch.setenv("OIDC_CLIENT_SECRET", "client-secret")
        monkeypatch.setenv("OIDC_ALLOWED_GROUPS", "lancelot-admins")

        response = await auth_config()

        assert response["provider"] == "oidc"
        assert response["oidc"]["enabled"] is True
        assert response["oidc"]["configured"] is True
        assert response["oidc"]["allowed_groups_configured"] is True
        assert response["oidc"]["allow_any_authenticated"] is False

    @pytest.mark.asyncio
    async def test_oidc_auth_config_open_mode_is_explicit(self, monkeypatch):
        monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "oidc")
        monkeypatch.setenv("OIDC_ISSUER_URL", "https://issuer.example")
        monkeypatch.setenv("OIDC_CLIENT_ID", "client-id")
        monkeypatch.setenv("OIDC_CLIENT_SECRET", "client-secret")
        monkeypatch.delenv("OIDC_ALLOWED_GROUPS", raising=False)
        monkeypatch.setenv("OIDC_ALLOW_ANY_AUTHENTICATED", "true")

        response = await auth_config()

        assert response["oidc"]["configured"] is True
        assert response["oidc"]["allowed_groups_configured"] is False
        assert response["oidc"]["allow_any_authenticated"] is True


class TestResetPassword:

    def setup_method(self):
        auth_module._audit_logger = None

    @patch("src.core.auth_api._get_warroom_password_reset_code")
    @patch("src.core.auth_api._get_warroom_username", return_value="admin")
    @patch("src.core.auth_api._persist_warroom_password_secret")
    @pytest.mark.asyncio
    async def test_reset_password_success(self, mock_persist, mock_user, mock_reset, monkeypatch):
        monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "local")
        reset_code = "recovery-code"
        mock_reset.return_value = bcrypt.hashpw(
            reset_code.encode("utf-8"),
            bcrypt.gensalt(),
        ).decode("utf-8")

        request = _make_mock_request(
            json_data={
                "username": "admin",
                "reset_code": reset_code,
                "new_password": "new-password-123",
            },
        )
        response = await reset_password(request)
        assert response["status"] == "ok"
        mock_persist.assert_called_once()

    @patch("src.core.auth_api._get_warroom_password_reset_code", return_value="")
    @patch("src.core.auth_api._get_warroom_username", return_value="admin")
    @pytest.mark.asyncio
    async def test_reset_password_requires_configuration(self, mock_user, mock_reset, monkeypatch):
        monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "local")
        request = _make_mock_request(
            json_data={
                "username": "admin",
                "reset_code": "bad",
                "new_password": "new-password-123",
            },
        )
        response = await reset_password(request)
        assert response.status_code == 503

    @patch("src.core.auth_api._get_warroom_password_reset_code")
    @patch("src.core.auth_api._get_warroom_username", return_value="admin")
    @pytest.mark.asyncio
    async def test_reset_password_rejects_bad_reset_code(self, mock_user, mock_reset, monkeypatch):
        monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "local")
        mock_reset.return_value = bcrypt.hashpw(
            b"good-reset-code",
            bcrypt.gensalt(),
        ).decode("utf-8")
        audit = MagicMock()
        auth_module._audit_logger = audit

        request = _make_mock_request(
            json_data={
                "username": "admin",
                "reset_code": "bad-code",
                "new_password": "new-password-123",
            },
        )

        response = await reset_password(request)

        assert response.status_code == 403
        audit.log_event.assert_called_once()
        assert audit.log_event.call_args.args[0] == "AUTH_PASSWORD_RESET_FAILED"
        auth_module._audit_logger = None

    @pytest.mark.asyncio
    async def test_reset_password_disabled_in_oidc_mode(self, monkeypatch):
        monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "oidc")
        request = _make_mock_request(
            json_data={
                "username": "admin",
                "reset_code": "reset",
                "new_password": "new-password-123",
            },
        )

        response = await reset_password(request)

        assert response.status_code == 400

    @patch("src.core.auth_api._get_warroom_password_reset_code", return_value="reset-code")
    @patch("src.core.auth_api._get_warroom_username", return_value="admin")
    @pytest.mark.asyncio
    async def test_reset_password_rejects_short_new_password(self, mock_user, mock_reset, monkeypatch):
        monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "local")
        request = _make_mock_request(
            json_data={
                "username": "admin",
                "reset_code": "reset-code",
                "new_password": "short",
            },
        )

        response = await reset_password(request)

        assert response.status_code == 400
        assert "at least 8" in response.body.decode("utf-8")


# ── validate_token endpoint ──────────────────────────────────────


class TestValidateToken:

    def setup_method(self):
        auth_module._sessions.clear()

    @pytest.mark.asyncio
    async def test_valid_token(self):
        _insert_session("valid-token", username="alice", expires_in=600)
        request = _make_mock_request(cookie_token="valid-token")
        response = await validate_token(request)
        payload = _response_json(response)
        assert payload["valid"] is True
        assert payload["username"] == "alice"
        assert payload["remaining_seconds"] > 0
        assert auth_module.get_warroom_session_cookie_name() in response.headers.get("set-cookie", "")

    @pytest.mark.asyncio
    async def test_expired_token(self):
        _insert_session("expired-token", expires_in=-10)
        request = _make_mock_request(cookie_token="expired-token")
        response = await validate_token(request)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_token(self):
        request = _make_mock_request()  # No token
        response = await validate_token(request)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_unknown_token(self):
        request = _make_mock_request(cookie_token="nonexistent")
        response = await validate_token(request)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_no_bearer_prefix(self):
        request = MagicMock()
        request.headers = {"authorization": "Basic abc123"}
        request.cookies = {}
        response = await validate_token(request)
        assert response.status_code == 401


# ── logout endpoint ──────────────────────────────────────────────


class TestLogout:

    def setup_method(self):
        auth_module._sessions.clear()
        auth_module._audit_logger = None

    @pytest.mark.asyncio
    async def test_logout_clears_session(self):
        _insert_session("my-token")
        assert "my-token" in auth_module._sessions

        request = _make_mock_request(cookie_token="my-token")
        response = await logout(request)

        payload = _response_json(response)
        assert payload["status"] == "ok"
        assert "my-token" not in auth_module._sessions
        assert "Max-Age=0" in response.headers.get("set-cookie", "")

    @pytest.mark.asyncio
    async def test_logout_nonexistent_token(self):
        request = _make_mock_request(cookie_token="doesnt-exist")
        response = await logout(request)
        assert _response_json(response)["status"] == "ok"

    @pytest.mark.asyncio
    async def test_logout_logs_event(self):
        audit = MagicMock()
        auth_module._audit_logger = audit
        _insert_session("log-token", username="bob")

        request = _make_mock_request(cookie_token="log-token")
        await logout(request)

        audit.log_event.assert_called_once()
        assert audit.log_event.call_args[0][0] == "AUTH_LOGOUT"

        auth_module._audit_logger = None


# ── verify_warroom_session ───────────────────────────────────────


class TestVerifyWarroomSession:

    def setup_method(self):
        auth_module._sessions.clear()

    def test_valid_session(self):
        _insert_session("sess-token", expires_in=600)
        request = _make_mock_request(cookie_token="sess-token")
        assert verify_warroom_session(request) is True

    def test_expired_session_returns_false_and_cleans_up(self):
        _insert_session("old-token", expires_in=-10)
        request = _make_mock_request(cookie_token="old-token")
        assert verify_warroom_session(request) is False
        assert "old-token" not in auth_module._sessions

    def test_no_auth_header(self):
        request = _make_mock_request()
        assert verify_warroom_session(request) is False

    def test_unknown_token(self):
        request = _make_mock_request(cookie_token="unknown")
        assert verify_warroom_session(request) is False

    def test_non_bearer_auth(self):
        request = MagicMock()
        request.headers = {"authorization": "Token abc"}
        assert verify_warroom_session(request) is False


# ── resolve_operator_identity ────────────────────────────────────


class TestResolveOperatorIdentity:

    def setup_method(self):
        auth_module._sessions.clear()

    def test_returns_identity_for_valid_session(self):
        identity = _insert_session("good-token", username="myles")
        request = _make_mock_request(cookie_token="good-token")
        result = resolve_operator_identity(request)
        assert result is not None
        assert result.operator_id == identity.operator_id
        assert result.display_name == "myles"

    def test_returns_none_for_expired_session(self):
        _insert_session("exp-token", expires_in=-10)
        request = _make_mock_request(cookie_token="exp-token")
        result = resolve_operator_identity(request)
        assert result is None

    def test_returns_none_for_no_auth(self):
        request = _make_mock_request()
        result = resolve_operator_identity(request)
        assert result is None

    def test_returns_none_for_unknown_token(self):
        request = _make_mock_request(cookie_token="missing")
        result = resolve_operator_identity(request)
        assert result is None

    def test_refreshes_session_timeout(self):
        _insert_session("refresh-token", expires_in=100)
        old_expiry = auth_module._sessions["refresh-token"]["expires_at"]

        # Small sleep to ensure time advances
        request = _make_mock_request(cookie_token="refresh-token")
        resolve_operator_identity(request)

        new_expiry = auth_module._sessions["refresh-token"]["expires_at"]
        # The new expiry should be at least as large (session refreshed)
        assert new_expiry >= old_expiry

    def test_cleans_expired_session_from_store(self):
        _insert_session("dead-token", expires_in=-10)
        request = _make_mock_request(cookie_token="dead-token")
        resolve_operator_identity(request)
        assert "dead-token" not in auth_module._sessions

    def test_resolve_authenticated_identity_prefers_session(self):
        identity = _insert_session("session-token", username="browser-user")
        request = _make_mock_request(cookie_token="session-token")

        result = auth_module.resolve_authenticated_identity(request)

        assert result is identity

    def test_resolve_authenticated_identity_falls_back_to_api_key(self, monkeypatch):
        monkeypatch.setenv("LANCELOT_OPERATOR_NAME", "api-operator")
        request = _make_mock_request()

        result = auth_module.resolve_authenticated_identity(request)

        assert result.display_name == "api-operator"
        assert result.auth_method == "api_key"


# ── get_api_key_identity ─────────────────────────────────────────


class TestGetApiKeyIdentity:

    @patch.dict(os.environ, {"LANCELOT_OPERATOR_NAME": "api-user"}, clear=False)
    def test_uses_operator_name_env_var(self):
        request = _make_mock_request(client_host="10.0.0.5")
        result = get_api_key_identity(request)
        assert result.display_name == "api-user"
        assert result.auth_method == "api_key"
        assert result.ip_address == "10.0.0.5"
        assert result.operator_id == resolve_operator_id("api-user")

    @patch("src.core.auth_api._get_warroom_username", return_value="warroom-admin")
    @patch.dict(os.environ, {}, clear=False)
    def test_falls_back_to_warroom_username(self, mock_wr):
        # Remove LANCELOT_OPERATOR_NAME if present
        os.environ.pop("LANCELOT_OPERATOR_NAME", None)
        request = _make_mock_request()
        result = get_api_key_identity(request)
        assert result.display_name == "warroom-admin"

    @patch("src.core.auth_api._get_warroom_username", return_value="")
    @patch.dict(os.environ, {}, clear=False)
    def test_falls_back_to_operator_default(self, mock_wr):
        os.environ.pop("LANCELOT_OPERATOR_NAME", None)
        request = _make_mock_request()
        result = get_api_key_identity(request)
        assert result.display_name == "operator"

    def test_no_client_ip(self):
        request = _make_mock_request(client_host=None)
        request.client = None
        with patch.dict(os.environ, {"LANCELOT_OPERATOR_NAME": "user1"}):
            result = get_api_key_identity(request)
        assert result.ip_address == ""

    @patch.dict(os.environ, {"LANCELOT_OPERATOR_NAME": "myuser"}, clear=False)
    def test_session_id_is_empty(self):
        request = _make_mock_request()
        result = get_api_key_identity(request)
        assert result.session_id == ""
        assert result.session_started_at == ""


# ── _cleanup_expired ─────────────────────────────────────────────


class TestCleanupExpired:

    def setup_method(self):
        auth_module._sessions.clear()

    def test_removes_expired_sessions(self):
        auth_module._sessions["expired1"] = {
            "expires_at": time.time() - 100,
            "username": "a",
        }
        auth_module._sessions["expired2"] = {
            "expires_at": time.time() - 1,
            "username": "b",
        }
        auth_module._sessions["valid"] = {
            "expires_at": time.time() + 600,
            "username": "c",
        }

        _cleanup_expired()

        assert "expired1" not in auth_module._sessions
        assert "expired2" not in auth_module._sessions
        assert "valid" in auth_module._sessions

    def test_no_sessions_no_error(self):
        _cleanup_expired()  # Should not raise


class TestAuthHelpers:

    def setup_method(self):
        auth_module._sessions.clear()
        auth_module._login_failures.clear()
        auth_module._oidc_pending.clear()
        auth_module._oidc_exchange_codes.clear()

    @pytest.mark.asyncio
    async def test_parse_request_model_rejects_invalid_json(self):
        request = MagicMock()
        request.json = AsyncMock(side_effect=ValueError("invalid"))

        with pytest.raises(HTTPException) as exc:
            await auth_module._parse_request_model(request, auth_module.LoginRequest)

        assert exc.value.status_code == 422
        assert exc.value.detail == "Request body must be valid JSON"

    def test_auth_provider_infers_oidc_from_config(self, monkeypatch):
        monkeypatch.delenv("LANCELOT_AUTH_PROVIDER", raising=False)
        monkeypatch.setenv("OIDC_ISSUER_URL", "https://issuer.example/")
        monkeypatch.setenv("OIDC_CLIENT_ID", "client-id")

        assert auth_module._get_auth_provider() == "oidc"

    def test_oidc_claim_profile_handles_string_and_unknown_groups(self, monkeypatch):
        monkeypatch.setenv("OIDC_USERNAME_CLAIM", "user_name")
        monkeypatch.setenv("OIDC_DISPLAY_NAME_CLAIM", "display")
        monkeypatch.setenv("OIDC_GROUPS_CLAIM", "roles")

        username, display_name, groups = auth_module._extract_claims_profile(
            {"user_name": "arthur", "display": "Arthur", "roles": "operators"}
        )
        fallback = auth_module._extract_claims_profile({"roles": {"bad": "shape"}})

        assert (username, display_name, groups) == ("arthur", "Arthur", ["operators"])
        assert fallback == ("enterprise-user", "enterprise-user", [])

    def test_require_operator_capability_allows_and_denies(self):
        _insert_session("limited-session", capabilities={"warroom.login"})
        allowed = _make_mock_request(cookie_token="limited-session")
        denied = _make_mock_request(cookie_token="limited-session")

        auth_module.require_operator_capability("warroom.login")(allowed)
        with pytest.raises(HTTPException) as exc:
            auth_module.require_operator_capability("platform.admin")(denied)

        assert exc.value.status_code == 403

    def test_verify_warroom_session_token_handles_all_states(self):
        _insert_session("valid-token", expires_in=60)
        _insert_session("expired-token", expires_in=-1)

        assert auth_module.verify_warroom_session_token("valid-token") is True
        assert auth_module.verify_warroom_session_token("missing-token") is False
        assert auth_module.verify_warroom_session_token("expired-token") is False
        assert "expired-token" not in auth_module._sessions

    def test_cleanup_expired_oidc_state_removes_stale_records(self):
        now = time.time()
        auth_module._oidc_pending.update(
            {
                "fresh-state": {"created_at": now},
                "stale-state": {"created_at": now - auth_module._OIDC_STATE_TTL_S - 1},
            }
        )
        auth_module._oidc_exchange_codes.update(
            {
                "fresh-code": {"expires_at": now + 60},
                "stale-code": {"expires_at": now - 1},
            }
        )

        with patch("src.core.auth_api._save_auth_state") as save_state:
            auth_module._cleanup_expired_oidc_state(now)

        assert set(auth_module._oidc_pending) == {"fresh-state"}
        assert set(auth_module._oidc_exchange_codes) == {"fresh-code"}
        save_state.assert_called_once()

    def test_cookie_secure_env_and_request_fallbacks(self, monkeypatch):
        request = _make_mock_request(scheme="https")
        monkeypatch.delenv("WARROOM_SESSION_COOKIE_SECURE", raising=False)
        assert auth_module._session_cookie_secure(request) is True

        monkeypatch.setenv("WARROOM_SESSION_COOKIE_SECURE", "false")
        assert auth_module._session_cookie_secure(request) is False

        monkeypatch.setenv("WARROOM_SESSION_COOKIE_SECURE", "true")
        assert auth_module._session_cookie_secure(request) is True


class TestAuthStatePersistence:

    def test_session_state_survives_module_reload(self, monkeypatch, tmp_path):
        state_file = tmp_path / "auth_state.json"
        key_file = tmp_path / "auth_state.key"
        monkeypatch.setenv("LANCELOT_AUTH_STATE_FILE", str(state_file))
        monkeypatch.setenv("LANCELOT_AUTH_STATE_KEY_FILE", str(key_file))

        auth_module._sessions.clear()
        auth_module._oidc_pending.clear()
        auth_module._oidc_exchange_codes.clear()
        _insert_session("persisted-token", username="persist-user", expires_in=600)
        auth_module._save_auth_state()

        reloaded = importlib.reload(auth_module)

        assert "persisted-token" in reloaded._sessions
        assert reloaded._sessions["persisted-token"]["username"] == "persist-user"
        assert isinstance(
            reloaded._sessions["persisted-token"]["operator_identity"],
            OperatorIdentity,
        )

    def test_oidc_state_survives_module_reload(self, monkeypatch, tmp_path):
        state_file = tmp_path / "auth_state.json"
        key_file = tmp_path / "auth_state.key"
        monkeypatch.setenv("LANCELOT_AUTH_STATE_FILE", str(state_file))
        monkeypatch.setenv("LANCELOT_AUTH_STATE_KEY_FILE", str(key_file))

        auth_module._sessions.clear()
        auth_module._oidc_pending.clear()
        auth_module._oidc_exchange_codes.clear()
        auth_module._oidc_pending["state-1"] = {"created_at": time.time(), "nonce": "abc"}
        auth_module._oidc_exchange_codes["exchange-1"] = {"expires_at": time.time() + 60, "token": "tok"}
        auth_module._save_auth_state()

        reloaded = importlib.reload(auth_module)

        assert "state-1" in reloaded._oidc_pending
        assert "exchange-1" in reloaded._oidc_exchange_codes

    def test_auth_state_is_encrypted_at_rest(self, monkeypatch, tmp_path):
        state_file = tmp_path / "auth_state.json"
        key_file = tmp_path / "auth_state.key"
        monkeypatch.setenv("LANCELOT_AUTH_STATE_FILE", str(state_file))
        monkeypatch.setenv("LANCELOT_AUTH_STATE_KEY_FILE", str(key_file))

        auth_module._sessions.clear()
        auth_module._oidc_pending.clear()
        auth_module._oidc_exchange_codes.clear()
        _insert_session("persisted-token", username="persist-user", expires_in=600)
        auth_module._oidc_pending["state-1"] = {"created_at": time.time(), "nonce": "abc"}
        auth_module._save_auth_state()

        raw = state_file.read_text(encoding="utf-8")
        envelope = json.loads(raw)

        assert envelope["encrypted"] is True
        assert envelope["algorithm"] == "fernet"
        assert "persist-user" not in raw
        assert "persisted-token" not in raw
        assert "state-1" not in raw

    def test_legacy_plaintext_auth_state_still_loads(self, monkeypatch, tmp_path):
        state_file = tmp_path / "auth_state.json"
        key_file = tmp_path / "auth_state.key"
        monkeypatch.setenv("LANCELOT_AUTH_STATE_FILE", str(state_file))
        monkeypatch.setenv("LANCELOT_AUTH_STATE_KEY_FILE", str(key_file))

        identity = OperatorIdentity(
            operator_id=resolve_operator_id("legacy-user"),
            display_name="legacy-user",
            session_id="legacy-session",
            session_started_at="2026-01-01T00:00:00Z",
            auth_method="local",
            ip_address="127.0.0.1",
        )
        state_file.write_text(
            json.dumps(
                {
                    "sessions": {
                        "legacy-token": {
                            "expires_at": time.time() + 600,
                            "username": "legacy-user",
                            "operator_identity": identity.to_dict(),
                            "capabilities": ["warroom.login"],
                        }
                    },
                    "oidc_pending": {"legacy-state": {"created_at": time.time()}},
                    "oidc_exchange_codes": {},
                }
            ),
            encoding="utf-8",
        )

        reloaded = importlib.reload(auth_module)

        assert "legacy-token" in reloaded._sessions
        assert reloaded._sessions["legacy-token"]["username"] == "legacy-user"
        assert isinstance(reloaded._sessions["legacy-token"]["operator_identity"], OperatorIdentity)
        assert "legacy-state" in reloaded._oidc_pending


# ── init_auth_api ────────────────────────────────────────────────


class TestInitAuthApi:

    def test_sets_audit_logger(self):
        audit = MagicMock()
        init_auth_api(audit_logger=audit)
        assert auth_module._audit_logger is audit
        auth_module._audit_logger = None

    def test_none_audit_logger(self):
        init_auth_api(audit_logger=None)
        assert auth_module._audit_logger is None


class TestStrictRequestBodies:

    def setup_method(self):
        auth_module._sessions.clear()
        auth_module._login_failures.clear()
        auth_module._oidc_exchange_codes.clear()

    def _client(self):
        app = FastAPI()
        app.include_router(auth_module.router)
        return TestClient(app)

    @patch("src.core.auth_api._get_warroom_password", return_value="correct-pass")
    @patch("src.core.auth_api._get_warroom_username", return_value="admin")
    def test_login_rejects_unexpected_fields(self, mock_user, mock_pass, monkeypatch):
        monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "local")
        client = self._client()

        response = client.post(
            "/auth/login",
            json={"username": "admin", "password": "correct-pass", "operator_id": "spoofed"},
        )

        assert response.status_code == 422
        assert "extra_forbidden" in response.text

    @patch("src.core.auth_api._get_warroom_password_reset_code")
    @patch("src.core.auth_api._get_warroom_username", return_value="admin")
    def test_reset_password_rejects_unexpected_fields(self, mock_user, mock_reset, monkeypatch):
        monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "local")
        mock_reset.return_value = bcrypt.hashpw(
            b"reset-secret",
            bcrypt.gensalt(),
        ).decode("utf-8")
        client = self._client()

        response = client.post(
            "/auth/reset-password",
            json={
                "username": "admin",
                "reset_code": "reset-secret",
                "new_password": "new-password-123",
                "session_id": "spoofed",
            },
        )

        assert response.status_code == 422
        assert "extra_forbidden" in response.text

    def test_oidc_exchange_rejects_unexpected_fields(self, monkeypatch):
        monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "oidc")
        auth_module._oidc_exchange_codes["exchange-1"] = {
            "token": "oidc-token",
            "expires_in": 120,
            "username": "Arthur",
            "operator_id": "op-arthur",
            "session_id": "session-1",
            "expires_at": time.time() + 60,
        }
        client = self._client()

        response = client.post(
            "/auth/oidc/exchange",
            json={"exchange_code": "exchange-1", "operator_id": "spoofed"},
        )

        assert response.status_code == 422
        assert "extra_forbidden" in response.text

    @patch("src.core.auth_api._persist_warroom_password_secret")
    @patch("src.core.auth_api._get_warroom_password", return_value="current-pass")
    def test_change_password_rejects_unexpected_fields(self, mock_pass, mock_persist, monkeypatch):
        monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "local")
        client = self._client()
        _insert_session("change-token", username="admin", capabilities={"warroom.login"})
        client.cookies.set(auth_module.get_warroom_session_cookie_name(), "change-token")

        response = client.post(
            "/auth/change-password",
            json={
                "current_password": "current-pass",
                "new_password": "next-password",
                "operator_id": "spoofed",
            },
        )

        assert response.status_code == 422
        assert "extra_forbidden" in response.text
        mock_persist.assert_not_called()


class TestOidcRoutes:

    def setup_method(self):
        auth_module._sessions.clear()
        auth_module._oidc_pending.clear()
        auth_module._oidc_exchange_codes.clear()

    def _client(self):
        app = FastAPI()
        app.include_router(auth_module.router)
        return TestClient(app, follow_redirects=False)

    def test_oidc_login_disabled_in_local_mode(self, monkeypatch):
        monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "local")
        client = self._client()

        response = client.get("/auth/oidc/login")

        assert response.status_code == 400

    def test_oidc_login_redirects_to_authorization_endpoint(self, monkeypatch):
        monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "oidc")
        client = self._client()

        with patch("src.core.auth_api._get_oidc_metadata", return_value={"authorization_endpoint": "https://idp.example/auth"}), \
                patch("src.core.auth_api._build_oidc_authorize_url", return_value=("https://idp.example/auth?state=s1", "s1")):
            response = client.get("/auth/oidc/login")

        assert response.status_code == 302
        assert response.headers["location"] == "https://idp.example/auth?state=s1"

    def test_oidc_login_reports_initialization_error(self, monkeypatch):
        monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "oidc")
        client = self._client()

        with patch("src.core.auth_api._get_oidc_metadata", side_effect=RuntimeError("missing config")):
            response = client.get("/auth/oidc/login")

        assert response.status_code == 503
        assert "missing config" in response.text

    def test_oidc_callback_disabled_and_missing_code_paths(self, monkeypatch):
        client = self._client()
        monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "local")
        assert client.get("/auth/oidc/callback").status_code == 400

        monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "oidc")
        response = client.get("/auth/oidc/callback?state=s1")

        assert response.status_code == 400
        assert "Missing OIDC code or state" in response.text

    def test_oidc_callback_redirects_provider_error(self, monkeypatch):
        monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "oidc")
        client = self._client()

        response = client.get("/auth/oidc/callback?error=access_denied&error_description=Denied")

        assert response.status_code == 302
        assert response.headers["location"] == "/war-room/login/callback#error=Denied"

    def test_oidc_callback_redirects_success_and_denial(self, monkeypatch):
        monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "oidc")
        client = self._client()

        async def successful_auth(request, code, state):
            return {"exchange_code": "exchange-success"}

        with patch("src.core.auth_api._complete_oidc_auth", side_effect=successful_auth):
            success = client.get("/auth/oidc/callback?code=c1&state=s1")

        with patch("src.core.auth_api._complete_oidc_auth", side_effect=PermissionError("denied")):
            denied = client.get("/auth/oidc/callback?code=c1&state=s1")

        assert success.status_code == 302
        assert success.headers["location"] == "/war-room/login/callback#exchange_code=exchange-success"
        assert denied.status_code == 302
        assert denied.headers["location"] == "/war-room/login/callback#error=access_denied"

    def test_oidc_callback_redirects_general_failure(self, monkeypatch):
        monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "oidc")
        client = self._client()

        with patch("src.core.auth_api._complete_oidc_auth", side_effect=RuntimeError("token endpoint down")):
            response = client.get("/auth/oidc/callback?code=c1&state=s1")

        assert response.status_code == 302
        assert response.headers["location"] == "/war-room/login/callback#error=oidc_callback_failed"

    def test_oidc_exchange_success_and_missing_code_paths(self, monkeypatch):
        monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "oidc")
        client = self._client()
        auth_module._oidc_exchange_codes["exchange-good"] = {
            "token": "oidc-token",
            "expires_in": 120,
            "username": "Arthur",
            "operator_id": "op-arthur",
            "session_id": "session-1",
            "expires_at": time.time() + 60,
        }

        success = client.post("/auth/oidc/exchange", json={"exchange_code": "exchange-good"})
        missing = client.post("/auth/oidc/exchange", json={"exchange_code": "missing"})

        assert success.status_code == 200
        assert success.json()["username"] == "Arthur"
        assert "exchange-good" not in auth_module._oidc_exchange_codes
        assert missing.status_code == 400

    def test_oidc_exchange_disabled_in_local_mode(self, monkeypatch):
        monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "local")
        client = self._client()

        response = client.post("/auth/oidc/exchange", json={"exchange_code": "exchange"})

        assert response.status_code == 400


class TestChangePassword:

    def setup_method(self):
        auth_module._sessions.clear()
        auth_module._audit_logger = None

    @patch("src.core.auth_api._persist_warroom_password_secret")
    @patch("src.core.auth_api._get_warroom_password", return_value="current-pass")
    @pytest.mark.asyncio
    async def test_change_password_success_hashes_and_audits(self, mock_pass, mock_persist, monkeypatch):
        monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "local")
        audit = MagicMock()
        auth_module._audit_logger = audit
        _insert_session("change-token", username="admin", capabilities={"warroom.login"})
        request = _make_mock_request(
            cookie_token="change-token",
            json_data={"current_password": "current-pass", "new_password": "next-password"},
        )

        response = await auth_module.change_password(request)

        assert response["status"] == "ok"
        mock_persist.assert_called_once()
        assert mock_persist.call_args.args[0].startswith("$2")
        audit.log_event.assert_called_once()
        assert audit.log_event.call_args.args[0] == "AUTH_PASSWORD_CHANGED"

    @pytest.mark.asyncio
    async def test_change_password_disabled_in_oidc_mode(self, monkeypatch):
        monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "oidc")
        request = _make_mock_request(json_data={"current_password": "current", "new_password": "next-password"})

        response = await auth_module.change_password(request)

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_change_password_requires_active_session(self, monkeypatch):
        monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "local")
        _insert_session("expired-change-token", username="admin", expires_in=-1)

        no_token = await auth_module.change_password(
            _make_mock_request(json_data={"current_password": "current", "new_password": "next-password"})
        )
        expired = await auth_module.change_password(
            _make_mock_request(
                cookie_token="expired-change-token",
                json_data={"current_password": "current", "new_password": "next-password"},
            )
        )

        assert no_token.status_code == 401
        assert expired.status_code == 401

    @patch("src.core.auth_api._get_warroom_password", return_value="current-pass")
    @pytest.mark.asyncio
    async def test_change_password_rejects_short_and_wrong_current_password(self, mock_pass, monkeypatch):
        monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "local")
        audit = MagicMock()
        auth_module._audit_logger = audit
        _insert_session("change-token", username="admin", capabilities={"warroom.login"})

        short = await auth_module.change_password(
            _make_mock_request(
                cookie_token="change-token",
                json_data={"current_password": "current-pass", "new_password": "short"},
            )
        )
        wrong = await auth_module.change_password(
            _make_mock_request(
                cookie_token="change-token",
                json_data={"current_password": "wrong-pass", "new_password": "next-password"},
            )
        )

        assert short.status_code == 400
        assert wrong.status_code == 403
        assert audit.log_event.call_args.args[0] == "AUTH_PASSWORD_CHANGE_FAILED"


class TestAuthStateAndOidcRuntime:

    def setup_method(self):
        auth_module._sessions.clear()
        auth_module._oidc_pending.clear()
        auth_module._oidc_exchange_codes.clear()

    def teardown_method(self):
        auth_module._sessions.clear()
        auth_module._oidc_pending.clear()
        auth_module._oidc_exchange_codes.clear()

    def test_encrypted_auth_state_round_trip_preserves_operator_identity(self, tmp_path, monkeypatch):
        state_file = tmp_path / "auth_state.json"
        key_file = tmp_path / "auth_state.key"
        monkeypatch.setenv("LANCELOT_AUTH_STATE_FILE", str(state_file))
        monkeypatch.setenv("LANCELOT_AUTH_STATE_KEY_FILE", str(key_file))
        monkeypatch.delenv("LANCELOT_AUTH_STATE_ENCRYPTION_KEY", raising=False)

        identity = _insert_session("persisted-token", username="admin", capabilities={"warroom.login", "soul.admin"})
        auth_module._oidc_pending["state-1"] = {"created_at": time.time(), "nonce": "n1"}
        auth_module._oidc_exchange_codes["exchange-1"] = {"expires_at": time.time() + 60, "token": "oidc-token"}

        auth_module._save_auth_state()
        raw = json.loads(state_file.read_text(encoding="utf-8"))
        assert raw["encrypted"] is True
        assert raw["ciphertext"]

        auth_module._sessions.clear()
        auth_module._oidc_pending.clear()
        auth_module._oidc_exchange_codes.clear()
        auth_module._load_auth_state()

        restored = auth_module._sessions["persisted-token"]
        assert restored["operator_identity"].operator_id == identity.operator_id
        assert restored["capabilities"] == ["soul.admin", "warroom.login"]
        assert auth_module._oidc_pending["state-1"]["nonce"] == "n1"
        assert auth_module._oidc_exchange_codes["exchange-1"]["token"] == "oidc-token"

    def test_auth_state_helpers_handle_configured_keys_and_bad_payloads(self, tmp_path, monkeypatch):
        configured_key = auth_module.Fernet.generate_key().decode("utf-8")
        monkeypatch.setenv("LANCELOT_AUTH_STATE_ENCRYPTION_KEY", configured_key)
        assert auth_module._load_or_create_auth_state_key() == configured_key.encode("utf-8")

        monkeypatch.delenv("LANCELOT_AUTH_STATE_ENCRYPTION_KEY", raising=False)
        key_file = tmp_path / "existing.key"
        key_file.write_bytes(auth_module.Fernet.generate_key())
        monkeypatch.setenv("LANCELOT_AUTH_STATE_KEY_FILE", str(key_file))
        assert auth_module._load_or_create_auth_state_key() == key_file.read_bytes()

        assert auth_module._deserialize_auth_state_payload("") == {}
        assert auth_module._deserialize_auth_state_payload(json.dumps(["not", "dict"])) == {}

        wrong_ciphertext = auth_module.Fernet(auth_module.Fernet.generate_key()).encrypt(b"{}").decode("utf-8")
        bad_envelope = json.dumps({"encrypted": True, "ciphertext": wrong_ciphertext})
        monkeypatch.setenv("LANCELOT_AUTH_STATE_ENCRYPTION_KEY", auth_module.Fernet.generate_key().decode("utf-8"))
        with pytest.raises(RuntimeError, match="could not be decrypted"):
            auth_module._deserialize_auth_state_payload(bad_envelope)

    def test_secret_cache_paths_and_persist_fallbacks(self, monkeypatch):
        fake_secret_cache = types.SimpleNamespace(
            get=lambda key, default="": {"WARROOM_USERNAME": "vault-user"}.get(key, default),
            is_bootstrapped=lambda: False,
            set_cached=MagicMock(),
        )
        monkeypatch.setitem(sys.modules, "secret_cache", fake_secret_cache)
        assert auth_module._get_warroom_username() == "vault-user"

        monkeypatch.setenv("WARROOM_PASSWORD", "env-pass")
        fake_secret_cache.get = MagicMock(side_effect=RuntimeError("cache offline"))
        assert auth_module._get_warroom_password() == "env-pass"

        fake_secret_cache.is_bootstrapped = lambda: False
        monkeypatch.setenv("WARROOM_PASSWORD_RESET_CODE", "")
        auth_module._persist_warroom_password_reset_code_secret("reset-hash")
        assert os.environ["WARROOM_PASSWORD_RESET_CODE"] == "reset-hash"

    def test_oidc_config_metadata_authorize_url_and_claim_profiles(self, monkeypatch):
        monkeypatch.setenv("OIDC_ISSUER_URL", "https://idp.example/")
        monkeypatch.setenv("OIDC_CLIENT_ID", "client-1")
        monkeypatch.setenv("OIDC_CLIENT_SECRET", "secret-1")
        monkeypatch.setenv("OIDC_SCOPES", "openid email")
        monkeypatch.setenv("OIDC_ALLOWED_GROUPS", "engineering")
        monkeypatch.setenv("OIDC_ADMIN_GROUPS", "admins")
        request = _make_mock_request()
        request.url_for = MagicMock(return_value="https://lancelot.example/auth/oidc/callback")

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "authorization_endpoint": "https://idp.example/auth",
                    "token_endpoint": "https://idp.example/token",
                }

        fake_httpx = types.SimpleNamespace(get=MagicMock(return_value=FakeResponse()))
        monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
        monkeypatch.setattr(auth_module, "assert_url_allowed", lambda url, component=None: url)
        monkeypatch.setattr(auth_module, "_save_auth_state", lambda: None)

        metadata = auth_module._get_oidc_metadata()
        authorize_url, state = auth_module._build_oidc_authorize_url(metadata, request)

        assert fake_httpx.get.call_args.args[0] == "https://idp.example/.well-known/openid-configuration"
        assert "client_id=client-1" in authorize_url
        assert "code_challenge_method=S256" in authorize_url
        assert state in auth_module._oidc_pending
        assert auth_module._extract_claims_profile({"sub": "sub-1", "groups": "engineering"}) == (
            "sub-1",
            "sub-1",
            ["engineering"],
        )
        assert auth_module._extract_claims_profile({"email": "a@example.com", "groups": 123}) == (
            "a@example.com",
            "a@example.com",
            [],
        )
        assert "platform.admin" in auth_module._capabilities_for_oidc_groups(["admins"])

    @pytest.mark.asyncio
    async def test_complete_oidc_auth_exchanges_code_fetches_userinfo_and_issues_exchange_code(self, monkeypatch):
        monkeypatch.setenv("OIDC_ISSUER_URL", "https://idp.example")
        monkeypatch.setenv("OIDC_CLIENT_ID", "client-1")
        monkeypatch.setenv("OIDC_CLIENT_SECRET", "secret-1")
        monkeypatch.setenv("OIDC_ALLOWED_GROUPS", "engineering")
        monkeypatch.setenv("OIDC_ADMIN_GROUPS", "engineering")
        monkeypatch.setattr(auth_module, "assert_url_allowed", lambda url, component=None: url)
        monkeypatch.setattr(auth_module, "_save_auth_state", lambda: None)
        request = _make_mock_request(client_host="10.0.0.10")

        auth_module._oidc_pending["state-good"] = {
            "redirect_uri": "https://lancelot.example/auth/oidc/callback",
            "code_verifier": "verifier-1",
            "created_at": time.time(),
        }

        class FakeResponse:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        post = MagicMock(return_value=FakeResponse({"access_token": "access-1"}))
        get = MagicMock(return_value=FakeResponse({
            "preferred_username": "arthur",
            "name": "Arthur",
            "sub": "subject-1",
            "groups": ["engineering"],
        }))
        monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(post=post, get=get))
        monkeypatch.setattr(auth_module, "_get_oidc_metadata", lambda: {
            "token_endpoint": "https://idp.example/token",
            "userinfo_endpoint": "https://idp.example/userinfo",
        })

        result = await auth_module._complete_oidc_auth(request, code="code-1", state="state-good")

        assert result["username"] == "Arthur"
        assert result["exchange_code"] in auth_module._oidc_exchange_codes
        session = auth_module._oidc_exchange_codes[result["exchange_code"]]
        assert session["username"] == "Arthur"
        stored_session = auth_module._sessions[session["token"]]
        assert "platform.admin" in stored_session["capabilities"]
        assert post.call_args.kwargs["data"]["code_verifier"] == "verifier-1"
        assert get.call_args.kwargs["headers"] == {"Authorization": "Bearer access-1"}

    @pytest.mark.asyncio
    async def test_complete_oidc_auth_rejects_missing_state_missing_claims_and_group_denial(self, monkeypatch):
        monkeypatch.setenv("OIDC_ALLOWED_GROUPS", "engineering")
        monkeypatch.setattr(auth_module, "_save_auth_state", lambda: None)
        with pytest.raises(ValueError, match="missing or expired"):
            await auth_module._complete_oidc_auth(_make_mock_request(), code="c1", state="missing")

        auth_module._oidc_pending["state-empty-claims"] = {
            "redirect_uri": "https://lancelot.example/cb",
            "code_verifier": "verifier",
            "created_at": time.time(),
        }
        monkeypatch.setattr(auth_module, "_get_oidc_metadata", lambda: {"token_endpoint": "https://idp.example/token"})
        monkeypatch.setattr(auth_module, "assert_url_allowed", lambda url, component=None: url)

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"access_token": "access-1"}

        monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(post=MagicMock(return_value=FakeResponse())))
        with pytest.raises(RuntimeError, match="userinfo endpoint did not return claims"):
            await auth_module._complete_oidc_auth(_make_mock_request(), code="c1", state="state-empty-claims")

        with pytest.raises(PermissionError, match="allowed enterprise group"):
            auth_module._enforce_allowed_groups(["sales"])
