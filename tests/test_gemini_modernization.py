"""Tests for Gemini Modernization (Steps 1-6).

Covers: SDK migration, system instructions, context caching,
thinking/reasoning, Live API, and UCP commerce integration.
All tests mock the Gemini client — no real API calls.
"""
import unittest
import os
import json
import asyncio
import re
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock


def _make_mock_provider(provider_name="gemini"):
    """Create a mock ProviderClient with standard defaults."""
    mock_provider = MagicMock()
    mock_provider.provider_name = provider_name
    mock_provider._client = MagicMock()
    mock_provider._client.caches.create.side_effect = Exception("cache not available")
    mock_result = MagicMock()
    mock_result.text = "Test response"
    mock_result.tool_calls = []
    mock_result.has_tool_calls = False
    mock_result.usage = {"input_tokens": 10, "output_tokens": 20}
    mock_provider.generate.return_value = mock_result
    mock_provider.build_user_message.return_value = "mock-message"
    return mock_provider


def _fake_frontier_redact(text):
    if not isinstance(text, str):
        return text

    text = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "[SSN]", text)
    text = re.sub(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "[EMAIL]",
        text,
    )
    text = re.sub(
        r"\b(?:\+?1[-.\s]?)?(?:\(\d{3}\)|\d{3})[-.\s]\d{3}[-.\s]\d{4}\b",
        "[PHONE]",
        text,
    )
    text = re.sub(
        r"\b(?:dob|date of birth|birth date)\s*[:\-]?\s*"
        r"(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|[A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})",
        "[DATE_OF_BIRTH]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\b(?:\d[ -]*?){13,19}\b", "[CREDIT_CARD]", text)
    return text


def _build_orchestrator(mock_provider=None):
    """Build an orchestrator with provider and cache init mocked out."""
    import importlib
    import orchestrator as orch_mod
    importlib.reload(orch_mod)

    if mock_provider is None:
        mock_provider = _make_mock_provider()

    # Patch _init_provider and _init_context_cache to avoid real SDK calls
    with patch.object(orch_mod.LancelotOrchestrator, '_init_provider') as mock_init_prov, \
         patch.object(orch_mod.LancelotOrchestrator, '_init_context_cache') as mock_init_cache:
        orch = orch_mod.LancelotOrchestrator()
        orch.provider = mock_provider
        scrub_router = MagicMock()
        scrub_router.route.side_effect = (
            lambda *_args, **_kwargs: SimpleNamespace(
                executed=True,
                output=_fake_frontier_redact(
                    _args[1] if len(_args) > 1 else _kwargs.get("text", "")
                ),
            )
        )
        orch.model_router = scrub_router
    return orch


def _run_async(coro):
    """Run async tests safely even if another suite closed the default event loop."""
    created = False
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("Event loop is closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        created = True

    try:
        return loop.run_until_complete(coro)
    finally:
        if created:
            loop.close()
            asyncio.set_event_loop(None)


# ---------------------------------------------------------------------------
# Test 1: SDK Migration
# ---------------------------------------------------------------------------
class TestSDKMigration(unittest.TestCase):
    """Verifies provider-based SDK integration in orchestrator."""

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key-123"})
    def test_orchestrator_creates_provider(self):
        """Provider should be instantiated when API key is present."""
        import importlib
        import orchestrator as orch_mod
        importlib.reload(orch_mod)

        with patch("providers.factory.create_provider") as mock_create:
            mock_provider = _make_mock_provider()
            mock_create.return_value = mock_provider
            with patch.object(orch_mod.LancelotOrchestrator, '_init_context_cache'):
                orch = orch_mod.LancelotOrchestrator()
            mock_create.assert_called()
            self.assertIsNotNone(orch.provider)

    @patch.dict(os.environ, {}, clear=False)
    def test_orchestrator_no_api_key(self):
        """Provider should be None when no API key is set."""
        env = os.environ.copy()
        for key in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                     "XAI_API_KEY", "NVIDIA_API_KEY"):
            env.pop(key, None)
        with patch.dict(os.environ, env, clear=True):
            import importlib
            import orchestrator as orch_mod
            importlib.reload(orch_mod)
            orch = orch_mod.LancelotOrchestrator()
            self.assertIsNone(orch.provider)

    @patch.dict(
        os.environ,
        {
            "LANCELOT_PROVIDER": "gemini",
            "LANCELOT_PROVIDER_MODE": "sdk",
        },
        clear=True,
    )
    def test_orchestrator_skips_adc_without_explicit_oauth_mode(self):
        """Ambient ADC must not trigger Gemini OAuth bootstrap unless explicitly selected."""
        import importlib
        import google.auth
        import orchestrator as orch_mod

        importlib.reload(orch_mod)
        orch = orch_mod.LancelotOrchestrator.__new__(orch_mod.LancelotOrchestrator)
        orch.provider = None
        orch.model_name = "gemini-3-flash-preview"

        def _unexpected_default(*args, **kwargs):
            raise AssertionError("google.auth.default should not be called")

        with patch.object(google.auth, "default", side_effect=_unexpected_default):
            orch._init_provider()

        self.assertIsNone(orch.provider)

    @patch.dict(
        os.environ,
        {
            "LANCELOT_PROVIDER": "gemini",
            "LANCELOT_PROVIDER_MODE": "sdk",
            "LANCELOT_AUTH_MODE": "OAUTH",
        },
        clear=True,
    )
    def test_orchestrator_explicit_oauth_uses_adc_without_refreshing(self):
        """Explicit Gemini OAuth mode should use ADC but avoid refresh-side network during init."""
        import importlib
        import google.auth
        import orchestrator as orch_mod

        importlib.reload(orch_mod)
        fake_creds = MagicMock(expired=True, refresh_token="refresh-token")
        mock_provider = _make_mock_provider()
        orch = orch_mod.LancelotOrchestrator.__new__(orch_mod.LancelotOrchestrator)
        orch.provider = None
        orch.model_name = "gemini-3-flash-preview"

        with patch.object(google.auth, "default", return_value=(fake_creds, "proj")), \
             patch("providers.factory.create_provider", return_value=mock_provider) as mock_create:
            orch._init_provider()

        mock_create.assert_called_once_with("gemini", "", credentials=fake_creds)
        fake_creds.refresh.assert_not_called()
        self.assertIs(orch.provider, mock_provider)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key", "GEMINI_MODEL": "gemini-3-pro"})
    def test_model_name_configurable(self):
        """Model name should be read from GEMINI_MODEL env var."""
        import importlib
        import orchestrator as orch_mod
        importlib.reload(orch_mod)

        # Patch _init_provider so ProfileRegistry doesn't override model_name,
        # and _init_context_cache so no real cache call is made.
        with patch.object(orch_mod.LancelotOrchestrator, '_init_provider'), \
             patch.object(orch_mod.LancelotOrchestrator, '_init_context_cache'):
            orch = orch_mod.LancelotOrchestrator()
        self.assertEqual(orch.model_name, "gemini-3-pro")

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"})
    def test_chat_uses_provider_generate(self):
        """chat() should call provider.generate() with proper args."""
        mock_provider = _make_mock_provider()
        orch = _build_orchestrator(mock_provider)
        result = orch.chat("hello")
        mock_provider.generate.assert_called()
        call_kwargs = mock_provider.generate.call_args
        # Provider.generate receives model as a keyword arg
        model_arg = call_kwargs.kwargs.get("model") or (call_kwargs.args[0] if call_kwargs.args else None)
        self.assertIsNotNone(model_arg)

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
    def test_instruction_has_persona(self):
        orch = _build_orchestrator()
        instruction = orch._build_system_instruction()
        self.assertIn("Lancelot", instruction)
        self.assertIn("loyal AI Knight", instruction)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"})
    def test_instruction_has_guardrails_with_unmistakably(self):
        orch = _build_orchestrator()
        instruction = orch._build_system_instruction()
        self.assertIn("unmistakably", instruction)
        self.assertIn("refuse to execute destructive", instruction)
        self.assertIn("refuse to reveal stored secrets", instruction)
        self.assertIn("refuse to bypass security", instruction)
        self.assertIn("refuse to modify your own rules", instruction)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"})
    def test_instruction_has_rules_and_context(self):
        orch = _build_orchestrator()
        instruction = orch._build_system_instruction()
        self.assertIn("Rules:", instruction)
        self.assertIn("User Context:", instruction)
        self.assertIn("Memory:", instruction)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"})
    def test_crusader_mode_modifies_instruction(self):
        orch = _build_orchestrator()
        normal = orch._build_system_instruction(crusader_mode=False)
        crusader = orch._build_system_instruction(crusader_mode=True)
        # Crusader mode should modify the instruction
        self.assertNotEqual(normal, crusader)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"})
    def test_system_instruction_passed_to_provider(self):
        """Verify system_instruction is passed to provider.generate(), not in contents."""
        mock_provider = _make_mock_provider()
        orch = _build_orchestrator(mock_provider)
        orch.chat("test message")

        call_kwargs = mock_provider.generate.call_args
        # system_instruction should be passed as a keyword argument to generate()
        sys_instr = call_kwargs.kwargs.get("system_instruction", "")
        self.assertIsNotNone(sys_instr)
        self.assertTrue(len(sys_instr) > 0, "system_instruction should be non-empty")


# ---------------------------------------------------------------------------
# Test 3: Context Caching
# ---------------------------------------------------------------------------
class TestContextCaching(unittest.TestCase):
    """Verifies context caching creation, usage, and invalidation."""

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"})
    def test_cache_creation(self):
        """Cache should be created during _init_context_cache when provider is Gemini."""
        mock_cache = MagicMock()
        mock_cache.name = "caches/test-cache-id"
        mock_provider = _make_mock_provider()
        # Override: cache creation succeeds
        mock_provider._client.caches.create.side_effect = None
        mock_provider._client.caches.create.return_value = mock_cache

        import importlib
        import orchestrator as orch_mod
        importlib.reload(orch_mod)

        # Only patch _init_provider; let _init_context_cache run with the mock provider
        with patch.object(orch_mod.LancelotOrchestrator, '_init_provider'):
            orch = orch_mod.LancelotOrchestrator()
            orch.provider = mock_provider
            # Manually call _init_context_cache since we patched _init_provider
            orch._init_context_cache()

        self.assertIsNotNone(orch._cache)
        self.assertEqual(orch._cache.name, "caches/test-cache-id")

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"})
    def test_cache_fallback_on_error(self):
        """Cache should be None if creation fails (e.g., content too small)."""
        mock_provider = _make_mock_provider()
        # Default: cache creation raises

        import importlib
        import orchestrator as orch_mod
        importlib.reload(orch_mod)

        with patch.object(orch_mod.LancelotOrchestrator, '_init_provider'):
            orch = orch_mod.LancelotOrchestrator()
            orch.provider = mock_provider
            orch._init_context_cache()

        self.assertIsNone(orch._cache)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"})
    def test_chat_works_with_cache(self):
        """chat() should work when cache exists (provider.generate still called)."""
        mock_cache = MagicMock()
        mock_cache.name = "caches/test-cache-id"
        mock_provider = _make_mock_provider()
        mock_provider._client.caches.create.side_effect = None
        mock_provider._client.caches.create.return_value = mock_cache

        orch = _build_orchestrator(mock_provider)
        orch._cache = mock_cache  # Simulate cache being set
        orch.chat("test message")

        mock_provider.generate.assert_called()

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"})
    def test_cache_invalidated_on_rule_update(self):
        """_update_rules() should recreate cache after modifying RULES.md."""
        mock_cache = MagicMock()
        mock_cache.name = "caches/original"
        mock_provider = _make_mock_provider()
        mock_provider._client.caches.create.side_effect = None
        mock_provider._client.caches.create.return_value = mock_cache

        import importlib
        import orchestrator as orch_mod
        importlib.reload(orch_mod)

        with patch.object(orch_mod.LancelotOrchestrator, '_init_provider'):
            orch = orch_mod.LancelotOrchestrator()
            orch.provider = mock_provider
            orch._init_context_cache()

        # Initial cache creation = 1 call
        initial_calls = mock_provider._client.caches.create.call_count

        # Update rules should trigger cache recreation
        orch._update_rules("A valid short rule")

        # Should have been called again for cache invalidation
        self.assertGreater(mock_provider._client.caches.create.call_count, initial_calls)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key", "GEMINI_CACHE_TTL": "7200"})
    def test_cache_ttl_configurable(self):
        """Cache TTL should be read from GEMINI_CACHE_TTL env var."""
        orch = _build_orchestrator()
        self.assertEqual(orch._cache_ttl, 7200)


# ---------------------------------------------------------------------------
# Test 4: Thinking/Reasoning Config
# ---------------------------------------------------------------------------
class TestThinkingConfig(unittest.TestCase):
    """Verifies _get_thinking_config() generation from env vars."""

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key", "GEMINI_THINKING_LEVEL": "high"})
    def test_thinking_level_from_env(self):
        orch = _build_orchestrator()
        config = orch._get_thinking_config()
        self.assertIsNotNone(config)
        self.assertEqual(config["thinking_level"], "high")

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key", "GEMINI_THINKING_LEVEL": "off"})
    def test_thinking_off_returns_none(self):
        orch = _build_orchestrator()
        config = orch._get_thinking_config()
        self.assertIsNone(config)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key", "GEMINI_THINKING_LEVEL": "low"})
    def test_thinking_included_in_chat(self):
        """ThinkingConfig should be included in the provider.generate() config arg."""
        mock_provider = _make_mock_provider()
        orch = _build_orchestrator(mock_provider)
        orch.chat("test")

        call_kwargs = mock_provider.generate.call_args
        config = call_kwargs.kwargs.get("config")
        self.assertIsNotNone(config)
        # Config should contain thinking key with thinking_level dict
        self.assertIn("thinking", config)
        self.assertEqual(config["thinking"]["thinking_level"], "low")


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

        _run_async(run())

    def test_close_when_not_connected(self):
        """close() should be safe to call when session is None."""
        from live_session import LiveSessionManager
        mgr = LiveSessionManager(client=MagicMock(), model_name="test-model")

        async def run():
            await mgr.close()
            self.assertFalse(mgr.is_connected)

        _run_async(run())


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
        with patch.object(connector._net_interceptor, "check_url", return_value=False):
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

    def test_ucp_discover_rejects_undeclared_fields(self):
        resp = self.client.post(
            "/ucp/discover",
            json={"merchant_url": "https://x.com", "operator_id": "spoofed"},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 422)

    def test_ucp_search_missing_field(self):
        resp = self.client.post("/ucp/search", json={"merchant_url": "https://x.com"}, headers=self.headers)
        self.assertEqual(resp.status_code, 400)

    def test_ucp_search_rejects_undeclared_fields(self):
        resp = self.client.post(
            "/ucp/search",
            json={"merchant_url": "https://x.com", "query": "test", "operator_id": "spoofed"},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 422)

    def test_ucp_transact_missing_field(self):
        resp = self.client.post("/ucp/transact", json={"merchant_url": "https://x.com"}, headers=self.headers)
        self.assertEqual(resp.status_code, 400)

    def test_ucp_transact_rejects_undeclared_fields(self):
        resp = self.client.post(
            "/ucp/transact",
            json={
                "merchant_url": "https://x.com",
                "product_id": "1",
                "operator_id": "spoofed",
            },
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 422)

    def test_ucp_confirm_missing_field(self):
        resp = self.client.post("/ucp/confirm", json={}, headers=self.headers)
        self.assertEqual(resp.status_code, 400)

    def test_ucp_confirm_rejects_undeclared_fields(self):
        resp = self.client.post(
            "/ucp/confirm",
            json={"transaction_id": "abc", "operator_id": "spoofed"},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 422)


# ---------------------------------------------------------------------------
# Test: API Discovery SDK Migration
# ---------------------------------------------------------------------------
class TestAPIDiscoverySDKMigration(unittest.TestCase):
    """Verifies api_discovery.py uses provider.generate() pattern."""

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
