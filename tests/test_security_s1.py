"""Tests for S1: Gateway Authentication.

Verifies that POST endpoints require a valid Bearer token, /health is
open, and dev mode (no token configured) allows all access.

These tests must import gateway.py, which initializes production
singletons at module level. We patch the data directory to use a temp
path so tests run outside Docker.
"""

import importlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from src.core.operator_identity import OperatorIdentity

# Stub heavy external dependencies that gateway.py imports
sys.modules.setdefault("google.generativeai", type(sys)("google.generativeai"))


def _prepare_data_dir(data_dir: str):
    """Create minimal memory files so orchestrator init succeeds."""
    os.makedirs(data_dir, exist_ok=True)
    for fn in ("USER.md", "RULES.md", "MEMORY_SUMMARY.md"):
        path = os.path.join(data_dir, fn)
        if not os.path.exists(path):
            with open(path, "w") as f:
                if fn == "USER.md":
                    f.write("# User Profile\n- Name: Arthur\n- Role: Commander\n- Bonded: True\n- OnboardingComplete: True")
                else:
                    f.write("")


def _load_gateway(data_dir: str):
    """Import (or reload) gateway with a patched data directory.

    Patches the hardcoded '/home/lancelot/data' to a temp directory so
    the orchestrator, onboarding, and receipt service init correctly
    outside Docker.
    """
    _prepare_data_dir(data_dir)
    source_soul_dir = Path(__file__).resolve().parents[1] / "soul"
    test_soul_dir = Path(data_dir) / "soul"
    if test_soul_dir.exists():
        shutil.rmtree(test_soul_dir)
    shutil.copytree(source_soul_dir, test_soul_dir)
    os.environ["SOUL_DIR"] = str(test_soul_dir)

    # Patch at the orchestrator and onboarding level
    import orchestrator as _orch_mod
    import onboarding as _onb_mod

    orig_orch_init = _orch_mod.LancelotOrchestrator.__init__
    orig_onb_init = _onb_mod.OnboardingOrchestrator.__init__

    def patched_orch_init(self, data_dir_arg="/home/lancelot/data", **kw):
        kw.pop("data_dir", None)
        orig_orch_init(self, data_dir=data_dir, **kw)

    def patched_onb_init(self, data_dir_arg="/home/lancelot/data", **kw):
        kw.pop("data_dir", None)
        orig_onb_init(self, data_dir=data_dir, **kw)

    _orch_mod.LancelotOrchestrator.__init__ = patched_orch_init
    _onb_mod.OnboardingOrchestrator.__init__ = patched_onb_init
    for module_name in ("src.core.soul.api", "soul.api"):
        mod = sys.modules.get(module_name)
        if mod and hasattr(mod, "_set_soul_dir"):
            mod._set_soul_dir(str(test_soul_dir))

    try:
        import gateway
        importlib.reload(gateway)
    finally:
        _orch_mod.LancelotOrchestrator.__init__ = orig_orch_init
        _onb_mod.OnboardingOrchestrator.__init__ = orig_onb_init

    return gateway


def _insert_session(token: str, capabilities: set[str]):
    from src.core import auth_api

    identity = OperatorIdentity(
        operator_id="op-123",
        display_name="Arthur",
        session_id="session-1",
        session_started_at="2026-04-10T00:00:00Z",
        auth_method="local",
        ip_address="127.0.0.1",
    )
    auth_api._sessions[token] = {
        "expires_at": 9999999999,
        "username": "Arthur",
        "operator_identity": identity,
        "capabilities": sorted(capabilities),
        "groups": [],
    }


class TestGatewayAuth(unittest.TestCase):
    """Tests that POST endpoints require valid Bearer token."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="lancelot_s1_")
        os.environ["LANCELOT_API_TOKEN"] = "test-secret-token-12345"
        os.environ["LANCELOT_WEBHOOK_AUTH_MODE"] = "bonded_bearer"
        os.environ["LANCELOT_WEBHOOK_BEARER"] = "test-webhook-secret-67890"
        gw = _load_gateway(cls._tmpdir)
        cls.app = gw.app

    def setUp(self):
        self.client = TestClient(self.app)
        self.valid_headers = {"Authorization": "Bearer test-secret-token-12345"}
        self.invalid_headers = {"Authorization": "Bearer wrong-token"}

    def test_chat_no_token_returns_401(self):
        response = self.client.post("/chat", json={"text": "hello", "user": "Arthur"})
        self.assertEqual(response.status_code, 401)
        self.assertIn("Unauthorized", response.json()["error"])

    def test_chat_invalid_token_returns_401(self):
        response = self.client.post(
            "/chat",
            json={"text": "hello", "user": "Arthur"},
            headers=self.invalid_headers,
        )
        self.assertEqual(response.status_code, 401)

    def test_chat_valid_token_succeeds(self):
        response = self.client.post(
            "/chat",
            json={"text": "hello", "user": "Arthur"},
            headers=self.valid_headers,
        )
        self.assertNotEqual(response.status_code, 401)

    def test_chat_rejects_undeclared_fields(self):
        response = self.client.post(
            "/chat",
            json={"text": "hello", "user": "Arthur", "operator_id": "spoofed"},
            headers=self.valid_headers,
        )
        self.assertEqual(response.status_code, 422)

    def test_chat_rejects_invalid_json(self):
        response = self.client.post(
            "/chat",
            content='{"text": "hello"',
            headers={**self.valid_headers, "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 422)

    def test_mfa_submit_no_token_returns_401(self):
        response = self.client.post("/mfa_submit", json={"code": "123456"})
        self.assertEqual(response.status_code, 401)

    def test_mfa_submit_valid_token_succeeds(self):
        response = self.client.post(
            "/mfa_submit",
            json={"code": "123456"},
            headers=self.valid_headers,
        )
        self.assertNotEqual(response.status_code, 401)

    def test_mfa_submit_rejects_undeclared_fields(self):
        response = self.client.post(
            "/mfa_submit",
            json={"code": "123456", "approved_by": "spoofed"},
            headers=self.valid_headers,
        )
        self.assertEqual(response.status_code, 422)

    def test_mcp_callback_no_token_returns_401(self):
        response = self.client.post(
            "/mcp_callback",
            json={"request_id": "abc", "action": "APPROVE"},
        )
        self.assertEqual(response.status_code, 401)

    def test_mcp_callback_valid_token_succeeds(self):
        response = self.client.post(
            "/mcp_callback",
            json={"request_id": "abc", "action": "APPROVE"},
            headers=self.valid_headers,
        )
        self.assertIn(response.status_code, (200, 400))

    def test_mcp_callback_rejects_undeclared_fields(self):
        response = self.client.post(
            "/mcp_callback",
            json={"request_id": "abc", "action": "APPROVE", "operator_id": "spoofed"},
            headers=self.valid_headers,
        )
        self.assertEqual(response.status_code, 422)

    def test_mcp_callback_webhook_bearer_succeeds_without_session_capability(self):
        response = self.client.post(
            "/mcp_callback",
            json={"request_id": "abc", "action": "APPROVE"},
            headers={"Authorization": "Bearer test-webhook-secret-67890"},
        )
        self.assertIn(response.status_code, (200, 400))

    def test_forge_discover_no_token_returns_401(self):
        response = self.client.post("/forge/discover", json={"url": "test docs"})
        self.assertEqual(response.status_code, 401)

    def test_forge_discover_rejects_undeclared_fields(self):
        response = self.client.post(
            "/forge/discover",
            json={"url": "test docs", "approval": "spoofed"},
            headers=self.valid_headers,
        )
        self.assertEqual(response.status_code, 422)

    def test_forge_dispatch_no_token_returns_401(self):
        response = self.client.post(
            "/forge/dispatch",
            json={"content": "test", "prompt": "post [x:local:post]"},
        )
        self.assertEqual(response.status_code, 401)

    def test_forge_dispatch_rejects_undeclared_fields(self):
        response = self.client.post(
            "/forge/dispatch",
            json={"content": "test", "prompt": "post [x:local:post]", "task_id": "spoofed"},
            headers=self.valid_headers,
        )
        self.assertEqual(response.status_code, 422)


class TestHealthNoAuth(unittest.TestCase):
    """Tests that /health is accessible without authentication."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="lancelot_s1_health_")
        os.environ["LANCELOT_API_TOKEN"] = "test-secret-token-12345"
        gw = _load_gateway(cls._tmpdir)
        cls.app = gw.app

    def setUp(self):
        self.client = TestClient(self.app)

    def test_health_no_token_succeeds(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertIn("status", response.json())

    def test_health_with_token_also_succeeds(self):
        response = self.client.get(
            "/health",
            headers={"Authorization": "Bearer test-secret-token-12345"},
        )
        self.assertEqual(response.status_code, 200)


class TestCrusaderStatusAuth(unittest.TestCase):
    """Tests that /crusader_status requires authentication."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="lancelot_s1_cru_")
        os.environ["LANCELOT_API_TOKEN"] = "test-secret-token-12345"
        gw = _load_gateway(cls._tmpdir)
        cls.app = gw.app

    def setUp(self):
        self.client = TestClient(self.app)

    def test_crusader_status_no_token_returns_401(self):
        response = self.client.get("/crusader_status")
        self.assertEqual(response.status_code, 401)

    def test_crusader_status_valid_token_succeeds(self):
        response = self.client.get(
            "/crusader_status",
            headers={"Authorization": "Bearer test-secret-token-12345"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("crusader_mode", response.json())


class TestGatewayCapabilityHardening(unittest.TestCase):
    """Tests that legacy gateway control routes require coarse admin capabilities."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="lancelot_s1_caps_")
        os.environ["LANCELOT_API_TOKEN"] = "test-secret-token-12345"
        gw = _load_gateway(cls._tmpdir)
        cls.gateway = gw
        cls.app = gw.app

    def setUp(self):
        from src.core import auth_api

        auth_api._sessions.clear()
        self.client = TestClient(self.app)

    def test_crusader_activate_requires_platform_admin(self):
        limited = "limited-session"
        _insert_session(limited, {"warroom.login"})
        self.client.cookies.set("lancelot_session", limited)

        response = self.client.post("/api/crusader/activate")
        self.assertEqual(response.status_code, 403)
        self.assertIn("Missing capability: platform.admin", response.json()["error"])

        admin = "admin-session"
        _insert_session(admin, {"warroom.login", "platform.admin"})
        self.client.cookies.set("lancelot_session", admin)
        response = self.client.post("/api/crusader/activate")
        self.assertNotIn(response.status_code, (401, 403))

    def test_google_oauth_start_requires_connectors_admin(self):
        limited = "limited-connectors-session"
        _insert_session(limited, {"warroom.login"})
        self.client.cookies.set("lancelot_session", limited)

        response = self.client.post(
            "/api/google-oauth/start",
            json={"client_id": "cid", "client_secret": "csecret"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("Missing capability: connectors.admin", response.json()["error"])

    def test_google_oauth_start_rejects_undeclared_fields(self):
        admin = "admin-connectors-session"
        _insert_session(admin, {"warroom.login", "connectors.admin"})
        self.client.cookies.set("lancelot_session", admin)

        with patch("feature_flags.FEATURE_GOOGLE_OAUTH", True), \
             patch("google_oauth_manager.get_google_oauth_manager") as mock_get_manager:
            manager = MagicMock()
            manager.generate_auth_url.return_value = "https://accounts.google.com/o/oauth2/auth"
            mock_get_manager.return_value = manager

            response = self.client.post(
                "/api/google-oauth/start",
                json={
                    "client_id": "cid",
                    "client_secret": "csecret",
                    "operator_id": "spoofed",
                },
            )

        self.assertEqual(response.status_code, 422)


class TestDevModeNoToken(unittest.TestCase):
    """Tests that when LANCELOT_API_TOKEN is not set, all endpoints are accessible."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="lancelot_s1_dev_")
        if "LANCELOT_API_TOKEN" in os.environ:
            del os.environ["LANCELOT_API_TOKEN"]
        os.environ["LANCELOT_DEV_MODE"] = "true"
        gw = _load_gateway(cls._tmpdir)
        # Force dev mode: clear cached API_TOKEN and enable DEV_MODE
        # (secret_cache may have cached the token from a prior test class)
        gw.API_TOKEN = None
        gw.DEV_MODE = True
        cls.app = gw.app

    @classmethod
    def tearDownClass(cls):
        if "LANCELOT_DEV_MODE" in os.environ:
            del os.environ["LANCELOT_DEV_MODE"]

    def setUp(self):
        self.client = TestClient(self.app)

    def test_chat_accessible_without_token_in_dev_mode(self):
        response = self.client.post("/chat", json={"text": "hello", "user": "Arthur"})
        self.assertNotEqual(response.status_code, 401)

    def test_crusader_status_accessible_in_dev_mode(self):
        response = self.client.get("/crusader_status")
        self.assertNotEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
