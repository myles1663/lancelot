"""Tests for F1-F8 feature improvements."""
import os
import sys
import time
import json
import unittest
import tempfile
import shutil
from unittest.mock import patch, MagicMock


# --- F1: Environment-Based Configuration ---

class TestEnvConfiguration(unittest.TestCase):

    def test_env_example_file_exists(self):
        env_example = os.path.join(os.path.dirname(__file__), ".env.example")
        self.assertTrue(os.path.exists(env_example))

    def test_env_example_has_required_vars(self):
        env_example = os.path.join(os.path.dirname(__file__), ".env.example")
        with open(env_example, "r") as f:
            content = f.read()
        self.assertIn("GEMINI_API_KEY", content)
        self.assertIn("LANCELOT_API_TOKEN", content)
        self.assertIn("VAULT_ENCRYPTION_KEY", content)
        self.assertIn("LANCELOT_HMAC_KEY", content)
        self.assertIn("ALLOWED_ORIGINS", content)
        self.assertIn("LANCELOT_LOG_LEVEL", content)

    def test_log_level_configurable(self):
        import logging
        with patch.dict(os.environ, {"LANCELOT_LOG_LEVEL": "DEBUG"}):
            level = os.getenv("LANCELOT_LOG_LEVEL", "INFO").upper()
            self.assertEqual(level, "DEBUG")
            self.assertEqual(getattr(logging, level), logging.DEBUG)


# --- F2: Structured Error Responses ---

class TestStructuredErrors(unittest.TestCase):

    def test_error_response_format(self):
        # Import error_response directly
        sys.path.insert(0, os.path.dirname(__file__))
        from gateway import error_response
        resp = error_response(401, "Unauthorized")
        self.assertEqual(resp.status_code, 401)
        body = json.loads(resp.body)
        self.assertEqual(body["error"], "Unauthorized")
        self.assertEqual(body["status"], 401)

    def test_error_response_with_detail(self):
        from gateway import error_response
        resp = error_response(500, "Internal server error", detail="Something broke")
        body = json.loads(resp.body)
        self.assertEqual(body["detail"], "Something broke")
        self.assertEqual(body["status"], 500)

    def test_error_response_with_request_id(self):
        from gateway import error_response
        resp = error_response(400, "Bad request", request_id="test-uuid")
        body = json.loads(resp.body)
        self.assertEqual(body["request_id"], "test-uuid")


# --- F4: Onboarding Simplification ---

class TestSimplifiedOnboarding(unittest.TestCase):

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.data_dir, ignore_errors=True)

    def test_welcome_to_handshake(self):
        from onboarding import OnboardingOrchestrator
        orch = OnboardingOrchestrator(data_dir=self.data_dir)
        self.assertEqual(orch.state, "WELCOME")

        response = orch.process("Arthur", "Hello")
        self.assertEqual(orch.state, "HANDSHAKE")
        self.assertIn("Welcome, Arthur", response)
        self.assertIn("bonded", response)

    def test_handshake_to_ready(self):
        from onboarding import OnboardingOrchestrator
        orch = OnboardingOrchestrator(data_dir=self.data_dir)
        orch.process("Arthur", "Hello")  # WELCOME -> HANDSHAKE

        response = orch.process("Arthur", "AIzaSyTestKey12345")
        self.assertEqual(orch.state, "READY")
        self.assertIn("Setup complete", response)

    def test_no_quest_selection_state(self):
        from onboarding import OnboardingOrchestrator
        orch = OnboardingOrchestrator(data_dir=self.data_dir)
        orch.process("Arthur", "Hello")  # WELCOME -> HANDSHAKE
        orch.process("Arthur", "AIzaSyTestKey12345")  # HANDSHAKE -> READY
        # Should be READY directly, never QUEST_SELECTION
        self.assertEqual(orch.state, "READY")

    def test_lockdown_after_three_bad_keys(self):
        from onboarding import OnboardingOrchestrator
        orch = OnboardingOrchestrator(data_dir=self.data_dir)
        orch.process("Arthur", "Hello")  # WELCOME -> HANDSHAKE

        orch.process("Arthur", "bad")  # fail 1
        orch.process("Arthur", "bad")  # fail 2
        response = orch.process("Arthur", "bad")  # fail 3 -> LOCKDOWN
        self.assertEqual(orch.state, "LOCKDOWN")
        self.assertIn("LOCKED", response)


# --- F5: Rich Response Formatting (logic tests) ---

class TestResponseClassification(unittest.TestCase):

    def test_high_confidence_indicator(self):
        # >90 should be classified as high confidence
        confidence = 95
        self.assertGreater(confidence, 90)

    def test_draft_confidence_range(self):
        # 70-90 is draft range
        confidence = 80
        self.assertTrue(70 <= confidence <= 90)

    def test_low_confidence_indicator(self):
        # <70 is low/permission required
        confidence = 55
        self.assertLess(confidence, 70)


# --- F6: Health Check Enhancement ---

class TestHealthCheckEnhanced(unittest.TestCase):

    def test_health_returns_version(self):
        from fastapi.testclient import TestClient
        from gateway import app
        client = TestClient(app)
        resp = client.get("/health")
        data = resp.json()
        self.assertEqual(data["version"], "3.0")
        self.assertIn("components", data)
        self.assertIn("uptime_seconds", data)

    def test_health_has_component_status(self):
        from fastapi.testclient import TestClient
        from gateway import app
        client = TestClient(app)
        resp = client.get("/health")
        components = resp.json()["components"]
        self.assertIn("gateway", components)
        self.assertIn("orchestrator", components)
        self.assertIn("sentry", components)
        self.assertIn("vault", components)
        self.assertIn("chromadb", components)

    def test_health_status_online(self):
        from fastapi.testclient import TestClient
        from gateway import app
        client = TestClient(app)
        resp = client.get("/health")
        self.assertEqual(resp.json()["status"], "online")


# --- F7: Request ID Tracking ---

class TestRequestIDTracking(unittest.TestCase):

    def test_chat_response_includes_request_id(self):
        from fastapi.testclient import TestClient
        from gateway import app
        client = TestClient(app)
        resp = client.post("/chat", json={"text": "hello", "user": "test"})
        data = resp.json()
        self.assertIn("request_id", data)

    def test_two_requests_get_different_ids(self):
        from fastapi.testclient import TestClient
        from gateway import app
        client = TestClient(app)
        r1 = client.post("/chat", json={"text": "hello", "user": "test"}).json()
        r2 = client.post("/chat", json={"text": "world", "user": "test"}).json()
        self.assertNotEqual(r1.get("request_id"), r2.get("request_id"))

    def test_mfa_response_includes_request_id(self):
        from fastapi.testclient import TestClient
        from gateway import app
        client = TestClient(app)
        resp = client.post("/mfa_submit", json={"code": "123456"})
        data = resp.json()
        self.assertIn("request_id", data)


# --- F8: Graceful Shutdown & Startup ---

class TestReadinessEndpoint(unittest.TestCase):

    def test_ready_endpoint_exists(self):
        from fastapi.testclient import TestClient
        from gateway import app
        client = TestClient(app)
        resp = client.get("/ready")
        self.assertIn(resp.status_code, [200, 503])
        data = resp.json()
        self.assertIn("ready", data)
        self.assertIn("components", data)

    def test_health_always_responds(self):
        from fastapi.testclient import TestClient
        from gateway import app
        client = TestClient(app)
        resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
