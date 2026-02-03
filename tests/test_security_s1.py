import unittest
import os
import json
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


class TestGatewayAuth(unittest.TestCase):
    """Tests that POST endpoints require valid Bearer token."""

    def setUp(self):
        os.environ["LANCELOT_API_TOKEN"] = "test-secret-token-12345"
        # Force reimport so gateway picks up the env var
        import importlib
        import gateway
        importlib.reload(gateway)
        self.client = TestClient(gateway.app)
        self.valid_headers = {"Authorization": "Bearer test-secret-token-12345"}
        self.invalid_headers = {"Authorization": "Bearer wrong-token"}
        # Ensure onboarding is in READY state
        data_dir = "/home/lancelot/data"
        user_file = os.path.join(data_dir, "USER.md")
        lock_file = os.path.join(data_dir, "LOCKDOWN")
        if os.path.exists(lock_file):
            os.remove(lock_file)
        if os.path.exists(data_dir):
            with open(user_file, "w") as f:
                f.write("# User Profile\n- Name: Arthur\n- Role: Commander\n- Bonded: True\n- OnboardingComplete: True")

    def tearDown(self):
        data_dir = "/home/lancelot/data"
        user_file = os.path.join(data_dir, "USER.md")
        if os.path.exists(data_dir):
            with open(user_file, "w") as f:
                f.write("# User Profile\n- Name: Arthur\n- Role: Commander\n- Bonded: True\n- OnboardingComplete: True")

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
        data = response.json()
        self.assertIn("response", data)

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
        self.assertNotEqual(response.status_code, 401)

    def test_forge_discover_no_token_returns_401(self):
        response = self.client.post("/forge/discover", json={"url": "test docs"})
        self.assertEqual(response.status_code, 401)

    def test_forge_dispatch_no_token_returns_401(self):
        response = self.client.post(
            "/forge/dispatch",
            json={"content": "test", "prompt": "post [x:local:post]"},
        )
        self.assertEqual(response.status_code, 401)


class TestHealthNoAuth(unittest.TestCase):
    """Tests that /health is accessible without authentication."""

    def setUp(self):
        os.environ["LANCELOT_API_TOKEN"] = "test-secret-token-12345"
        import importlib
        import gateway
        importlib.reload(gateway)
        self.client = TestClient(gateway.app)

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

    def setUp(self):
        os.environ["LANCELOT_API_TOKEN"] = "test-secret-token-12345"
        import importlib
        import gateway
        importlib.reload(gateway)
        self.client = TestClient(gateway.app)

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


class TestDevModeNoToken(unittest.TestCase):
    """Tests that when LANCELOT_API_TOKEN is not set, all endpoints are accessible."""

    def setUp(self):
        if "LANCELOT_API_TOKEN" in os.environ:
            del os.environ["LANCELOT_API_TOKEN"]
        import importlib
        import gateway
        importlib.reload(gateway)
        self.client = TestClient(gateway.app)
        # Ensure onboarding is in READY state
        data_dir = "/home/lancelot/data"
        user_file = os.path.join(data_dir, "USER.md")
        lock_file = os.path.join(data_dir, "LOCKDOWN")
        if os.path.exists(lock_file):
            os.remove(lock_file)
        if os.path.exists(data_dir):
            with open(user_file, "w") as f:
                f.write("# User Profile\n- Name: Arthur\n- Role: Commander\n- Bonded: True\n- OnboardingComplete: True")

    def tearDown(self):
        data_dir = "/home/lancelot/data"
        user_file = os.path.join(data_dir, "USER.md")
        if os.path.exists(data_dir):
            with open(user_file, "w") as f:
                f.write("# User Profile\n- Name: Arthur\n- Role: Commander\n- Bonded: True\n- OnboardingComplete: True")

    def test_chat_accessible_without_token_in_dev_mode(self):
        response = self.client.post("/chat", json={"text": "hello", "user": "Arthur"})
        self.assertNotEqual(response.status_code, 401)

    def test_crusader_status_accessible_in_dev_mode(self):
        response = self.client.get("/crusader_status")
        self.assertNotEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
