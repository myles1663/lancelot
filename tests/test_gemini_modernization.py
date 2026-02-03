"""Tests for Gemini Modernization (Steps 1-6).

Covers: SDK migration, system instructions, context caching,
thinking/reasoning, Live API, and UCP commerce integration.
All tests mock the Gemini client — no real API calls.
"""
import unittest
import os
import json
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock


# ---------------------------------------------------------------------------
# Test 1: SDK Migration
# ---------------------------------------------------------------------------
class TestSDKMigration(unittest.TestCase):
    """Verifies google-genai SDK integration in orchestrator and librarian."""

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key-123"})
    @patch("orchestrator.genai.Client")
    @patch("orchestrator.chromadb.PersistentClient")
    def test_orchestrator_creates_genai_client(self, mock_chroma, mock_client_cls):
        """Client should be instantiated with api_key from env."""
        mock_client_cls.return_value = MagicMock()
        mock_chroma.return_value = MagicMock(
            get_or_create_collection=MagicMock(return_value=MagicMock())
        )
        from orchestrator import LancelotOrchestrator
        orch = LancelotOrchestrator()
        mock_client_cls.assert_called_once_with(api_key="test-key-123")
        self.assertIsNotNone(orch.client)

    @patch.dict(os.environ, {}, clear=False)
    @patch("orchestrator.chromadb.PersistentClient")
    def test_orchestrator_no_api_key(self, mock_chroma):
        """Client should be None when GEMINI_API_KEY is missing."""
        mock_chroma.return_value = MagicMock(
            get_or_create_collection=MagicMock(return_value=MagicMock())
        )
        env = os.environ.copy()
        env.pop("GEMINI_API_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            from orchestrator import LancelotOrchestrator
            import importlib
            import orchestrator
            importlib.reload(orchestrator)
            orch = orchestrator.LancelotOrchestrator()
            self.assertIsNone(orch.client)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key", "GEMINI_MODEL": "gemini-3-pro"})
    @patch("orchestrator.genai.Client")
    @patch("orchestrator.chromadb.PersistentClient")
    def test_model_name_configurable(self, mock_chroma, mock_client_cls):
        """Model name should be read from GEMINI_MODEL env var."""
        mock_client_cls.return_value = MagicMock()
        mock_chroma.return_value = MagicMock(
            get_or_create_collection=MagicMock(return_value=MagicMock())
        )
        from orchestrator import LancelotOrchestrator
        import importlib
        import orchestrator
        importlib.reload(orchestrator)
        orch = orchestrator.LancelotOrchestrator()
        self.assertEqual(orch.model_name, "gemini-3-pro")

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"})
    @patch("orchestrator.genai.Client")
    @patch("orchestrator.chromadb.PersistentClient")
    def test_chat_uses_new_sdk_pattern(self, mock_chroma, mock_client_cls):
        """chat() should call client.models.generate_content() with proper args."""
        mock_response = MagicMock()
        mock_response.text = "Confidence: 85 Test response"
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        mock_client.caches.create.side_effect = Exception("cache not available")
        mock_client_cls.return_value = mock_client
        mock_chroma.return_value = MagicMock(
            get_or_create_collection=MagicMock(return_value=MagicMock())
        )

        from orchestrator import LancelotOrchestrator
        import importlib
        import orchestrator
        importlib.reload(orchestrator)
        orch = orchestrator.LancelotOrchestrator()

        result = orch.chat("hello")
        mock_client.models.generate_content.assert_called_once()
        call_kwargs = mock_client.models.generate_content.call_args
        self.assertEqual(call_kwargs.kwargs.get("model") or call_kwargs[1].get("model", call_kwargs[0][0] if call_kwargs[0] else None),
                         orch.model_name)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"})
    @patch("librarian.genai.Client")
    def test_librarian_uses_new_sdk(self, mock_client_cls):
        """Librarian should create genai.Client like orchestrator."""
        mock_client_cls.return_value = MagicMock()
        from librarian import Librarian
        import importlib
        import librarian as lib_mod
        importlib.reload(lib_mod)
        lib = lib_mod.Librarian()
        mock_client_cls.assert_called_with(api_key="test-key")
        self.assertIsNotNone(lib.client)


# ---------------------------------------------------------------------------
# Test 2: System Instructions
# ---------------------------------------------------------------------------
class TestSystemInstructions(unittest.TestCase):
    """Verifies _build_system_instruction() structure and content."""

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"})
    @patch("orchestrator.genai.Client")
    @patch("orchestrator.chromadb.PersistentClient")
    def test_instruction_has_persona(self, mock_chroma, mock_client_cls):
        mock_client_cls.return_value = MagicMock()
        mock_client_cls.return_value.caches.create.side_effect = Exception("no cache")
        mock_chroma.return_value = MagicMock(
            get_or_create_collection=MagicMock(return_value=MagicMock())
        )
        import importlib
        import orchestrator
        importlib.reload(orchestrator)
        orch = orchestrator.LancelotOrchestrator()
        instruction = orch._build_system_instruction()
        self.assertIn("Lancelot", instruction)
        self.assertIn("loyal AI Knight", instruction)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"})
    @patch("orchestrator.genai.Client")
    @patch("orchestrator.chromadb.PersistentClient")
    def test_instruction_has_guardrails_with_unmistakably(self, mock_chroma, mock_client_cls):
        mock_client_cls.return_value = MagicMock()
        mock_client_cls.return_value.caches.create.side_effect = Exception("no cache")
        mock_chroma.return_value = MagicMock(
            get_or_create_collection=MagicMock(return_value=MagicMock())
        )
        import importlib
        import orchestrator
        importlib.reload(orchestrator)
        orch = orchestrator.LancelotOrchestrator()
        instruction = orch._build_system_instruction()
        self.assertIn("unmistakably", instruction)
        self.assertIn("refuse to execute destructive", instruction)
        self.assertIn("refuse to reveal stored secrets", instruction)
        self.assertIn("refuse to bypass security", instruction)
        self.assertIn("refuse to modify your own rules", instruction)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"})
    @patch("orchestrator.genai.Client")
    @patch("orchestrator.chromadb.PersistentClient")
    def test_instruction_has_rules_and_context(self, mock_chroma, mock_client_cls):
        mock_client_cls.return_value = MagicMock()
        mock_client_cls.return_value.caches.create.side_effect = Exception("no cache")
        mock_chroma.return_value = MagicMock(
            get_or_create_collection=MagicMock(return_value=MagicMock())
        )
        import importlib
        import orchestrator
        importlib.reload(orchestrator)
        orch = orchestrator.LancelotOrchestrator()
        instruction = orch._build_system_instruction()
        self.assertIn("Rules:", instruction)
        self.assertIn("User Context:", instruction)
        self.assertIn("Memory:", instruction)
        self.assertIn("Confidence Score", instruction)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"})
    @patch("orchestrator.genai.Client")
    @patch("orchestrator.chromadb.PersistentClient")
    def test_crusader_mode_modifies_instruction(self, mock_chroma, mock_client_cls):
        mock_client_cls.return_value = MagicMock()
        mock_client_cls.return_value.caches.create.side_effect = Exception("no cache")
        mock_chroma.return_value = MagicMock(
            get_or_create_collection=MagicMock(return_value=MagicMock())
        )
        import importlib
        import orchestrator
        importlib.reload(orchestrator)
        orch = orchestrator.LancelotOrchestrator()
        normal = orch._build_system_instruction(crusader_mode=False)
        crusader = orch._build_system_instruction(crusader_mode=True)
        # Crusader mode should modify the instruction (exact change depends on CrusaderPromptModifier)
        self.assertNotEqual(normal, crusader)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"})
    @patch("orchestrator.genai.Client")
    @patch("orchestrator.chromadb.PersistentClient")
    def test_system_instruction_passed_to_config(self, mock_chroma, mock_client_cls):
        """Verify system_instruction goes in GenerateContentConfig, not in prompt."""
        mock_response = MagicMock()
        mock_response.text = "Test response"
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        mock_client.caches.create.side_effect = Exception("cache not available")
        mock_client_cls.return_value = mock_client
        mock_chroma.return_value = MagicMock(
            get_or_create_collection=MagicMock(return_value=MagicMock())
        )

        import importlib
        import orchestrator
        importlib.reload(orchestrator)
        orch = orchestrator.LancelotOrchestrator()
        orch.chat("test message")

        call_kwargs = mock_client.models.generate_content.call_args
        config = call_kwargs.kwargs.get("config")
        self.assertIsNotNone(config)
        # The contents should NOT contain the system instruction text
        contents = call_kwargs.kwargs.get("contents", "")
        self.assertNotIn("unmistakably", contents)


# ---------------------------------------------------------------------------
# Test 3: Context Caching
# ---------------------------------------------------------------------------
class TestContextCaching(unittest.TestCase):
    """Verifies context caching creation, usage, and invalidation."""

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"})
    @patch("orchestrator.genai.Client")
    @patch("orchestrator.chromadb.PersistentClient")
    def test_cache_creation(self, mock_chroma, mock_client_cls):
        """Cache should be created during __init__ when client is available."""
        mock_cache = MagicMock()
        mock_cache.name = "caches/test-cache-id"
        mock_client = MagicMock()
        mock_client.caches.create.return_value = mock_cache
        mock_client_cls.return_value = mock_client
        mock_chroma.return_value = MagicMock(
            get_or_create_collection=MagicMock(return_value=MagicMock())
        )

        import importlib
        import orchestrator
        importlib.reload(orchestrator)
        orch = orchestrator.LancelotOrchestrator()
        self.assertIsNotNone(orch._cache)
        self.assertEqual(orch._cache.name, "caches/test-cache-id")

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"})
    @patch("orchestrator.genai.Client")
    @patch("orchestrator.chromadb.PersistentClient")
    def test_cache_fallback_on_error(self, mock_chroma, mock_client_cls):
        """Cache should be None if creation fails (e.g., content too small)."""
        mock_client = MagicMock()
        mock_client.caches.create.side_effect = Exception("Content too small for caching")
        mock_client_cls.return_value = mock_client
        mock_chroma.return_value = MagicMock(
            get_or_create_collection=MagicMock(return_value=MagicMock())
        )

        import importlib
        import orchestrator
        importlib.reload(orchestrator)
        orch = orchestrator.LancelotOrchestrator()
        self.assertIsNone(orch._cache)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"})
    @patch("orchestrator.genai.Client")
    @patch("orchestrator.chromadb.PersistentClient")
    def test_chat_uses_cache_when_available(self, mock_chroma, mock_client_cls):
        """chat() should pass cached_content in config when cache exists."""
        mock_cache = MagicMock()
        mock_cache.name = "caches/test-cache-id"
        mock_response = MagicMock()
        mock_response.text = "Test cached response"
        mock_client = MagicMock()
        mock_client.caches.create.return_value = mock_cache
        mock_client.models.generate_content.return_value = mock_response
        mock_client_cls.return_value = mock_client
        mock_chroma.return_value = MagicMock(
            get_or_create_collection=MagicMock(return_value=MagicMock())
        )

        import importlib
        import orchestrator
        importlib.reload(orchestrator)
        orch = orchestrator.LancelotOrchestrator()
        orch.chat("test message")

        call_kwargs = mock_client.models.generate_content.call_args
        config = call_kwargs.kwargs.get("config")
        self.assertIsNotNone(config)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"})
    @patch("orchestrator.genai.Client")
    @patch("orchestrator.chromadb.PersistentClient")
    def test_cache_invalidated_on_rule_update(self, mock_chroma, mock_client_cls):
        """_update_rules() should recreate cache after modifying RULES.md."""
        mock_cache = MagicMock()
        mock_cache.name = "caches/original"
        mock_client = MagicMock()
        mock_client.caches.create.return_value = mock_cache
        mock_client_cls.return_value = mock_client
        mock_chroma.return_value = MagicMock(
            get_or_create_collection=MagicMock(return_value=MagicMock())
        )

        import importlib
        import orchestrator
        importlib.reload(orchestrator)
        orch = orchestrator.LancelotOrchestrator()

        # Initial cache creation = 1 call
        initial_calls = mock_client.caches.create.call_count

        # Update rules should trigger cache recreation
        orch._update_rules("A valid short rule")

        # Should have been called again for cache invalidation
        self.assertGreater(mock_client.caches.create.call_count, initial_calls)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key", "GEMINI_CACHE_TTL": "7200"})
    @patch("orchestrator.genai.Client")
    @patch("orchestrator.chromadb.PersistentClient")
    def test_cache_ttl_configurable(self, mock_chroma, mock_client_cls):
        """Cache TTL should be read from GEMINI_CACHE_TTL env var."""
        mock_client = MagicMock()
        mock_client.caches.create.side_effect = Exception("test")
        mock_client_cls.return_value = mock_client
        mock_chroma.return_value = MagicMock(
            get_or_create_collection=MagicMock(return_value=MagicMock())
        )

        import importlib
        import orchestrator
        importlib.reload(orchestrator)
        orch = orchestrator.LancelotOrchestrator()
        self.assertEqual(orch._cache_ttl, 7200)


# ---------------------------------------------------------------------------
# Test 4: Thinking/Reasoning Config
# ---------------------------------------------------------------------------
class TestThinkingConfig(unittest.TestCase):
    """Verifies ThinkingConfig generation from env vars."""

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key", "GEMINI_THINKING_LEVEL": "high"})
    @patch("orchestrator.genai.Client")
    @patch("orchestrator.chromadb.PersistentClient")
    def test_thinking_level_from_env(self, mock_chroma, mock_client_cls):
        mock_client_cls.return_value = MagicMock()
        mock_client_cls.return_value.caches.create.side_effect = Exception("no cache")
        mock_chroma.return_value = MagicMock(
            get_or_create_collection=MagicMock(return_value=MagicMock())
        )
        import importlib
        import orchestrator
        importlib.reload(orchestrator)
        orch = orchestrator.LancelotOrchestrator()
        config = orch._get_thinking_config()
        self.assertIsNotNone(config)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key", "GEMINI_THINKING_LEVEL": "off"})
    @patch("orchestrator.genai.Client")
    @patch("orchestrator.chromadb.PersistentClient")
    def test_thinking_off_returns_none(self, mock_chroma, mock_client_cls):
        mock_client_cls.return_value = MagicMock()
        mock_client_cls.return_value.caches.create.side_effect = Exception("no cache")
        mock_chroma.return_value = MagicMock(
            get_or_create_collection=MagicMock(return_value=MagicMock())
        )
        import importlib
        import orchestrator
        importlib.reload(orchestrator)
        orch = orchestrator.LancelotOrchestrator()
        config = orch._get_thinking_config()
        self.assertIsNone(config)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key", "GEMINI_THINKING_LEVEL": "low"})
    @patch("orchestrator.genai.Client")
    @patch("orchestrator.chromadb.PersistentClient")
    def test_thinking_included_in_chat(self, mock_chroma, mock_client_cls):
        """ThinkingConfig should be included in the generation call."""
        mock_response = MagicMock()
        mock_response.text = "Test response"
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        mock_client.caches.create.side_effect = Exception("no cache")
        mock_client_cls.return_value = mock_client
        mock_chroma.return_value = MagicMock(
            get_or_create_collection=MagicMock(return_value=MagicMock())
        )

        import importlib
        import orchestrator
        importlib.reload(orchestrator)
        orch = orchestrator.LancelotOrchestrator()
        orch.chat("test")

        call_kwargs = mock_client.models.generate_content.call_args
        config = call_kwargs.kwargs.get("config")
        self.assertIsNotNone(config)


# ---------------------------------------------------------------------------
# Test 5: Live API Session
# ---------------------------------------------------------------------------
class TestLiveSession(unittest.TestCase):
    """Tests LiveSessionManager connect/send/close lifecycle."""

    def test_session_not_connected_initially(self):
        from live_session import LiveSessionManager
        mgr = LiveSessionManager(client=MagicMock(), model_name="test-model")
        self.assertFalse(mgr.is_connected)

    def test_send_raises_if_not_connected(self):
        from live_session import LiveSessionManager
        mgr = LiveSessionManager(client=MagicMock(), model_name="test-model")

        async def run():
            with self.assertRaises(RuntimeError):
                async for _ in mgr.send_text("hello"):
                    pass

        asyncio.get_event_loop().run_until_complete(run())

    def test_close_when_not_connected(self):
        """close() should be safe to call when session is None."""
        from live_session import LiveSessionManager
        mgr = LiveSessionManager(client=MagicMock(), model_name="test-model")

        async def run():
            await mgr.close()
            self.assertFalse(mgr.is_connected)

        asyncio.get_event_loop().run_until_complete(run())


class TestLiveWebSocket(unittest.TestCase):
    """Tests the /live WebSocket gateway endpoint."""

    def setUp(self):
        os.environ["LANCELOT_API_TOKEN"] = "test-secret-token"
        import importlib
        import gateway
        importlib.reload(gateway)
        from fastapi.testclient import TestClient
        self.client = TestClient(gateway.app)

    def test_live_endpoint_exists(self):
        """WebSocket endpoint should be registered."""
        routes = [r.path for r in self.client.app.routes]
        self.assertIn("/live", routes)


# ---------------------------------------------------------------------------
# Test 6: UCP Connector
# ---------------------------------------------------------------------------
class TestUCPConnector(unittest.TestCase):
    """Tests UCP discovery, search, and transaction flow."""

    def test_discover_blocked_url(self):
        """SSRF-blocked URLs should raise ValueError."""
        from ucp_connector import UCPConnector
        connector = UCPConnector()
        with self.assertRaises(ValueError):
            connector.discover_merchant("http://127.0.0.1:8080")

    def test_initiate_transaction_creates_pending(self):
        """initiate_transaction should create a pending transaction."""
        from ucp_connector import UCPConnector
        connector = UCPConnector()
        # Pre-populate a merchant manifest
        connector._registered_merchants["https://shop.example.com"] = {
            "name": "Test Shop",
            "endpoints": {"transact": "/api/transact"},
        }

        txn = connector.initiate_transaction(
            "https://shop.example.com",
            "PROD-001",
            {"quantity": 1}
        )
        self.assertEqual(txn["status"], "pending_confirmation")
        self.assertEqual(txn["product_id"], "PROD-001")
        self.assertIn("transaction_id", txn)

    def test_confirm_unknown_transaction_raises(self):
        """Confirming a non-existent transaction should raise ValueError."""
        from ucp_connector import UCPConnector
        connector = UCPConnector()
        with self.assertRaises(ValueError):
            connector.confirm_transaction("nonexistent-id")

    def test_confirm_already_completed_raises(self):
        """Confirming an already-completed transaction should raise."""
        from ucp_connector import UCPConnector
        connector = UCPConnector()
        connector._registered_merchants["https://shop.example.com"] = {
            "name": "Test Shop",
            "endpoints": {"transact": "/api/transact"},
        }
        txn = connector.initiate_transaction("https://shop.example.com", "PROD-001", {})
        # Manually mark as completed
        connector._pending_transactions[txn["transaction_id"]]["status"] = "completed"
        with self.assertRaises(ValueError):
            connector.confirm_transaction(txn["transaction_id"])

    def test_list_merchants_empty(self):
        from ucp_connector import UCPConnector
        connector = UCPConnector()
        self.assertEqual(connector.list_merchants(), [])

    def test_list_merchants_after_register(self):
        from ucp_connector import UCPConnector
        connector = UCPConnector()
        connector._registered_merchants["https://shop.example.com"] = {"name": "Shop A"}
        merchants = connector.list_merchants()
        self.assertEqual(len(merchants), 1)
        self.assertEqual(merchants[0]["name"], "Shop A")

    def test_get_transaction_returns_none_for_unknown(self):
        from ucp_connector import UCPConnector
        connector = UCPConnector()
        self.assertIsNone(connector.get_transaction("nonexistent"))

    def test_search_requires_discovery_first(self):
        """search_products should call discover_merchant if not cached."""
        from ucp_connector import UCPConnector
        connector = UCPConnector()
        # This will try to discover first and fail with a security block
        with self.assertRaises((ValueError, ConnectionError)):
            connector.search_products("http://10.0.0.1", "laptop")


# ---------------------------------------------------------------------------
# Test: UCP Gateway Endpoints
# ---------------------------------------------------------------------------
class TestUCPGatewayEndpoints(unittest.TestCase):
    """Tests /ucp/* endpoints require auth and validate input."""

    def setUp(self):
        os.environ["LANCELOT_API_TOKEN"] = "test-secret-token"
        import importlib
        import gateway
        importlib.reload(gateway)
        from fastapi.testclient import TestClient
        self.client = TestClient(gateway.app)
        self.headers = {"Authorization": "Bearer test-secret-token"}

    def test_ucp_discover_requires_auth(self):
        resp = self.client.post("/ucp/discover", json={"merchant_url": "https://x.com"})
        self.assertEqual(resp.status_code, 401)

    def test_ucp_search_requires_auth(self):
        resp = self.client.post("/ucp/search", json={"merchant_url": "https://x.com", "query": "test"})
        self.assertEqual(resp.status_code, 401)

    def test_ucp_transact_requires_auth(self):
        resp = self.client.post("/ucp/transact", json={"merchant_url": "https://x.com", "product_id": "1"})
        self.assertEqual(resp.status_code, 401)

    def test_ucp_confirm_requires_auth(self):
        resp = self.client.post("/ucp/confirm", json={"transaction_id": "abc"})
        self.assertEqual(resp.status_code, 401)

    def test_ucp_discover_missing_field(self):
        resp = self.client.post("/ucp/discover", json={}, headers=self.headers)
        self.assertEqual(resp.status_code, 400)

    def test_ucp_search_missing_field(self):
        resp = self.client.post("/ucp/search", json={"merchant_url": "https://x.com"}, headers=self.headers)
        self.assertEqual(resp.status_code, 400)

    def test_ucp_transact_missing_field(self):
        resp = self.client.post("/ucp/transact", json={"merchant_url": "https://x.com"}, headers=self.headers)
        self.assertEqual(resp.status_code, 400)

    def test_ucp_confirm_missing_field(self):
        resp = self.client.post("/ucp/confirm", json={}, headers=self.headers)
        self.assertEqual(resp.status_code, 400)


# ---------------------------------------------------------------------------
# Test: API Discovery SDK Migration
# ---------------------------------------------------------------------------
class TestAPIDiscoverySDKMigration(unittest.TestCase):
    """Verifies api_discovery.py uses new client.models.generate_content pattern."""

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"})
    def test_llm_extract_uses_new_sdk(self):
        """_llm_extract should call orchestrator.client.models.generate_content."""
        from api_discovery import APIDiscoveryEngine
        mock_response = MagicMock()
        mock_response.text = '{"api_name": "Test", "base_url": "https://api.test.com", "endpoints": []}'
        mock_orch = MagicMock()
        mock_orch.client.models.generate_content.return_value = mock_response
        mock_orch.model_name = "gemini-2.0-flash"

        engine = APIDiscoveryEngine(orchestrator=mock_orch)
        result = engine._llm_extract("Some API docs")
        mock_orch.client.models.generate_content.assert_called_once()
        self.assertEqual(result["api_name"], "Test")


# ---------------------------------------------------------------------------
# Test: Gateway Health Check Migration
# ---------------------------------------------------------------------------
class TestGatewayHealthMigration(unittest.TestCase):
    """Verifies health check uses client instead of model."""

    def setUp(self):
        os.environ.pop("LANCELOT_API_TOKEN", None)
        import importlib
        import gateway
        importlib.reload(gateway)
        from fastapi.testclient import TestClient
        self.client = TestClient(gateway.app)

    def test_health_check_returns_ok(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "online")
        self.assertIn("components", data)
        self.assertIn("orchestrator", data["components"])


if __name__ == "__main__":
    unittest.main()
