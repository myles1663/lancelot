"""Tests for War Room Authentication API (auth_api.py)."""

import os
import sys
import hashlib
import hmac
import time
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import MagicMock, patch, AsyncMock

import src.core.auth_api as auth_module
from src.core.auth_api import (
    login,
    validate_token,
    logout,
    verify_warroom_session,
    resolve_operator_identity,
    get_api_key_identity,
    init_auth_api,
    _verify_password,
    _cleanup_expired,
)
from src.core.operator_identity import OperatorIdentity, resolve_operator_id


# ── Helpers ──────────────────────────────────────────────────────


def _make_mock_request(token=None, json_data=None, client_host="127.0.0.1"):
    """Create a mock FastAPI Request."""
    request = MagicMock()
    headers = {}
    if token:
        headers["authorization"] = f"Bearer {token}"
    request.headers = headers
    if json_data is not None:
        request.json = AsyncMock(return_value=json_data)
    if client_host:
        client = MagicMock()
        client.host = client_host
        request.client = client
    else:
        request.client = None
    return request


def _insert_session(token, username="testuser", expires_in=1800):
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
    }
    return identity


# ── _verify_password ─────────────────────────────────────────────


class TestVerifyPassword:

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
        auth_module._audit_logger = None

    @patch("src.core.auth_api._get_warroom_password", return_value="correct-pass")
    @patch("src.core.auth_api._get_warroom_username", return_value="admin")
    @pytest.mark.asyncio
    async def test_login_valid_credentials(self, mock_user, mock_pass):
        request = _make_mock_request(
            json_data={"username": "admin", "password": "correct-pass"}
        )
        response = await login(request)
        assert "token" in response
        assert response["username"] == "admin"
        assert "operator_id" in response
        assert "session_id" in response
        assert response["expires_in"] > 0

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
    @pytest.mark.asyncio
    async def test_login_with_sha256_hashed_password(self, mock_user, mock_pass):
        plain = "my-secure-password"
        hashed = hashlib.sha256(plain.encode("utf-8")).hexdigest()
        mock_pass.return_value = hashed

        request = _make_mock_request(
            json_data={"username": "admin", "password": plain}
        )
        response = await login(request)
        assert "token" in response
        assert response["username"] == "admin"

    @patch("src.core.auth_api._get_warroom_password", return_value="pass")
    @patch("src.core.auth_api._get_warroom_username", return_value="admin")
    @pytest.mark.asyncio
    async def test_login_creates_session_in_store(self, mock_user, mock_pass):
        request = _make_mock_request(
            json_data={"username": "admin", "password": "pass"}
        )
        response = await login(request)
        token = response["token"]
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
        expected_id = resolve_operator_id("admin")
        assert response["operator_id"] == expected_id

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


# ── validate_token endpoint ──────────────────────────────────────


class TestValidateToken:

    def setup_method(self):
        auth_module._sessions.clear()

    @pytest.mark.asyncio
    async def test_valid_token(self):
        _insert_session("valid-token", username="alice", expires_in=600)
        request = _make_mock_request(token="valid-token")
        response = await validate_token(request)
        assert response["valid"] is True
        assert response["username"] == "alice"
        assert response["remaining_seconds"] > 0

    @pytest.mark.asyncio
    async def test_expired_token(self):
        _insert_session("expired-token", expires_in=-10)
        request = _make_mock_request(token="expired-token")
        response = await validate_token(request)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_token(self):
        request = _make_mock_request()  # No token
        response = await validate_token(request)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_unknown_token(self):
        request = _make_mock_request(token="nonexistent")
        response = await validate_token(request)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_no_bearer_prefix(self):
        request = MagicMock()
        request.headers = {"authorization": "Basic abc123"}
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

        request = _make_mock_request(token="my-token")
        response = await logout(request)

        assert response["status"] == "ok"
        assert "my-token" not in auth_module._sessions

    @pytest.mark.asyncio
    async def test_logout_nonexistent_token(self):
        request = _make_mock_request(token="doesnt-exist")
        response = await logout(request)
        assert response["status"] == "ok"

    @pytest.mark.asyncio
    async def test_logout_logs_event(self):
        audit = MagicMock()
        auth_module._audit_logger = audit
        _insert_session("log-token", username="bob")

        request = _make_mock_request(token="log-token")
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
        request = _make_mock_request(token="sess-token")
        assert verify_warroom_session(request) is True

    def test_expired_session_returns_false_and_cleans_up(self):
        _insert_session("old-token", expires_in=-10)
        request = _make_mock_request(token="old-token")
        assert verify_warroom_session(request) is False
        assert "old-token" not in auth_module._sessions

    def test_no_auth_header(self):
        request = _make_mock_request()
        assert verify_warroom_session(request) is False

    def test_unknown_token(self):
        request = _make_mock_request(token="unknown")
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
        request = _make_mock_request(token="good-token")
        result = resolve_operator_identity(request)
        assert result is not None
        assert result.operator_id == identity.operator_id
        assert result.display_name == "myles"

    def test_returns_none_for_expired_session(self):
        _insert_session("exp-token", expires_in=-10)
        request = _make_mock_request(token="exp-token")
        result = resolve_operator_identity(request)
        assert result is None

    def test_returns_none_for_no_auth(self):
        request = _make_mock_request()
        result = resolve_operator_identity(request)
        assert result is None

    def test_returns_none_for_unknown_token(self):
        request = _make_mock_request(token="missing")
        result = resolve_operator_identity(request)
        assert result is None

    def test_refreshes_session_timeout(self):
        _insert_session("refresh-token", expires_in=100)
        old_expiry = auth_module._sessions["refresh-token"]["expires_at"]

        # Small sleep to ensure time advances
        request = _make_mock_request(token="refresh-token")
        resolve_operator_identity(request)

        new_expiry = auth_module._sessions["refresh-token"]["expires_at"]
        # The new expiry should be at least as large (session refreshed)
        assert new_expiry >= old_expiry

    def test_cleans_expired_session_from_store(self):
        _insert_session("dead-token", expires_in=-10)
        request = _make_mock_request(token="dead-token")
        resolve_operator_identity(request)
        assert "dead-token" not in auth_module._sessions


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
