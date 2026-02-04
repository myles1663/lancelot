"""
Regression tests for Prompt 18 — Hardening & Reliability Pass.

Covers:
  1. Error leakage prevention (no internal details in API responses)
  2. Health check error handling
  3. Download timeout configuration
  4. Local model server error sanitisation
  5. Control-plane error safety
  6. Usage tracker resilience
  7. Router error containment
"""

import json
import pytest
import yaml
import urllib.error
from dataclasses import dataclass
from unittest.mock import MagicMock, patch, PropertyMock

from src.core.model_router import ModelRouter, RouterDecision, RouterResult
from src.core.provider_profile import ProfileRegistry
from src.core.local_model_client import LocalModelClient, LocalModelError
from src.core.flagship_client import FlagshipClient, FlagshipError
from src.core.usage_tracker import UsageTracker, LaneUsage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def models_yaml(tmp_path):
    data = {
        "version": "1.0",
        "local": {"enabled": True, "url": "http://local-llm:8080"},
        "providers": {
            "gemini": {
                "display_name": "Google Gemini",
                "fast": {"model": "gemini-2.0-flash", "max_tokens": 4096, "temperature": 0.3},
                "deep": {"model": "gemini-2.0-pro", "max_tokens": 8192, "temperature": 0.7},
            },
        },
    }
    p = tmp_path / "models.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return str(p)


@pytest.fixture
def router_yaml(tmp_path):
    data = {
        "version": "1.0",
        "routing_order": [
            {"lane": "local_redaction", "priority": 1, "description": "PII"},
            {"lane": "local_utility", "priority": 2, "description": "Utility"},
            {"lane": "flagship_fast", "priority": 3, "description": "Fast"},
            {"lane": "flagship_deep", "priority": 4, "description": "Deep"},
        ],
        "escalation": {"triggers": [{"type": "risk", "description": "Risk"}]},
        "receipts": {"enabled": True, "include_rationale": True, "include_timing": True},
        "local_utility_tasks": [
            "classify_intent", "extract_json", "summarize", "redact", "rag_rewrite",
        ],
    }
    p = tmp_path / "router.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return str(p)


@pytest.fixture
def registry(models_yaml, router_yaml):
    return ProfileRegistry(models_path=models_yaml, router_path=router_yaml)


@pytest.fixture
def mock_local():
    client = MagicMock(spec=LocalModelClient)
    client.classify_intent.return_value = "question"
    client.summarize.return_value = "Summary."
    client.redact.return_value = "[REDACTED]"
    client.rag_rewrite.return_value = "rewritten"
    client.complete.return_value = "output"
    return client


@pytest.fixture
def router(registry, mock_local):
    return ModelRouter(registry=registry, local_client=mock_local)


@pytest.fixture
def tmp_data_dir(tmp_path):
    return tmp_path / "data"


# ===================================================================
# 1. Gateway error leakage prevention
# ===================================================================

class TestGatewayErrorLeakage:
    """Verify that gateway error_response calls no longer include detail=str(e)."""

    def test_error_response_excludes_detail_by_default(self):
        """The error_response function should not include internal details."""
        # Import the function directly — it lives in gateway.py
        # but gateway.py has heavy imports that may not resolve in test env.
        # Instead, we verify the pattern via source inspection.
        from pathlib import Path
        gateway_path = Path(__file__).parent.parent / "src" / "core" / "gateway.py"
        if not gateway_path.exists():
            pytest.skip("gateway.py not accessible")
        source = gateway_path.read_text(encoding="utf-8")
        # There should be no remaining instances of detail=str(e)
        assert "detail=str(e)" not in source, (
            "gateway.py still leaks exception details via detail=str(e)"
        )

    def test_no_raw_exception_in_websocket(self):
        """WebSocket error handler should not send raw exception text."""
        from pathlib import Path
        gateway_path = Path(__file__).parent.parent / "src" / "core" / "gateway.py"
        if not gateway_path.exists():
            pytest.skip("gateway.py not accessible")
        source = gateway_path.read_text(encoding="utf-8")
        assert 'send_text(f"Error: {e}")' not in source, (
            "gateway.py still leaks raw exception to WebSocket clients"
        )

    def test_health_check_has_error_handling(self):
        """Health check endpoint should be wrapped in try/except."""
        from pathlib import Path
        gateway_path = Path(__file__).parent.parent / "src" / "core" / "gateway.py"
        if not gateway_path.exists():
            pytest.skip("gateway.py not accessible")
        source = gateway_path.read_text(encoding="utf-8")
        # Find the health_check function and verify it has exception handling
        idx = source.find("def health_check")
        assert idx != -1, "health_check function not found"
        # Get function body up to the next @app. or def  at module level
        next_func = source.find("\n@app.", idx + 1)
        if next_func == -1:
            next_func = len(source)
        func_body = source[idx:next_func]
        assert "try:" in func_body, "health_check missing try block"
        assert "except" in func_body, "health_check missing except block"
        assert "Health check failed" in func_body, (
            "health_check should return safe error message"
        )


# ===================================================================
# 2. Local model server error sanitisation
# ===================================================================

class TestLocalModelServerErrors:
    """Verify local-llm server does not leak inference details."""

    def test_server_inference_error_sanitised(self):
        """Server should log details but not return them to client."""
        from pathlib import Path
        server_path = Path(__file__).parent.parent / "local_models" / "server.py"
        if not server_path.exists():
            pytest.skip("server.py not accessible")
        source = server_path.read_text(encoding="utf-8")
        # Should NOT have the raw exception in HTTPException detail
        assert 'f"Inference error: {exc}"' not in source, (
            "server.py still leaks raw inference error to client"
        )
        # Should have sanitised message
        assert '"Model inference failed"' in source, (
            "server.py should return sanitised error message"
        )


# ===================================================================
# 3. Download timeout
# ===================================================================

class TestDownloadTimeout:
    """Verify model download has a timeout."""

    def test_fetch_download_has_timeout(self):
        """urlopen in fetch_model._download must have a timeout parameter."""
        from pathlib import Path
        fetch_path = Path(__file__).parent.parent / "local_models" / "fetch_model.py"
        if not fetch_path.exists():
            pytest.skip("fetch_model.py not accessible")
        source = fetch_path.read_text(encoding="utf-8")
        # Find the _download function
        idx = source.find("def _download")
        assert idx != -1, "_download function not found"
        func_body = source[idx:idx + 500]
        assert "timeout=" in func_body, (
            "_download must include timeout parameter on urlopen"
        )


# ===================================================================
# 4. API discovery error sanitisation
# ===================================================================

class TestApiDiscoveryErrors:
    """Verify api_discovery does not leak URL error details."""

    def test_scrape_error_sanitised(self):
        """Error messages should not contain raw exception details."""
        from pathlib import Path
        disc_path = Path(__file__).parent.parent / "src" / "integrations" / "api_discovery.py"
        if not disc_path.exists():
            pytest.skip("api_discovery.py not accessible")
        source = disc_path.read_text(encoding="utf-8")
        assert 'f"Error fetching URL: {e}"' not in source, (
            "api_discovery.py still leaks raw error details"
        )


# ===================================================================
# 5. Control-plane error safety
# ===================================================================

class TestControlPlaneErrors:

    @pytest.fixture(autouse=True)
    def _setup_app(self, router, tmp_data_dir):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.core.control_plane import (
            router as cp_router,
            init_control_plane,
            set_model_router,
        )

        init_control_plane(str(tmp_data_dir))
        set_model_router(router)

        app = FastAPI()
        app.include_router(cp_router)
        self.client = TestClient(app)

        yield

        set_model_router(None)

    def test_all_endpoints_return_json(self):
        """All control-plane endpoints should return valid JSON."""
        endpoints = [
            "/system/status",
            "/onboarding/status",
            "/router/decisions",
            "/router/stats",
            "/usage/summary",
            "/usage/lanes",
            "/usage/savings",
        ]
        for ep in endpoints:
            resp = self.client.get(ep)
            assert resp.status_code == 200, f"{ep} returned {resp.status_code}"
            data = resp.json()
            assert isinstance(data, dict), f"{ep} did not return dict"

    def test_no_stack_traces_in_responses(self):
        """Responses should never contain stack trace markers."""
        endpoints = [
            "/system/status",
            "/onboarding/status",
            "/router/decisions",
            "/router/stats",
            "/usage/summary",
            "/usage/lanes",
            "/usage/savings",
        ]
        for ep in endpoints:
            resp = self.client.get(ep)
            body = resp.text
            assert "Traceback" not in body, f"{ep} leaked a traceback"
            assert "File \"" not in body, f"{ep} leaked a file path"

    def test_decisions_endpoint_handles_large_volume(self, router, mock_local):
        """Router should handle many decisions without error."""
        for i in range(250):
            router.route("classify_intent", f"text {i}")
        resp = self.client.get("/router/decisions")
        assert resp.status_code == 200
        data = resp.json()
        # Deque maxlen is 200, so total caps at 200
        assert data["total"] == 200
        # Endpoint caps at 50
        assert len(data["decisions"]) <= 50

    def test_usage_summary_after_many_routes(self, router, mock_local):
        """Usage summary should work with large volumes."""
        for _ in range(100):
            router.route("classify_intent", "a")
            router.route("summarize", "b")
        resp = self.client.get("/usage/summary")
        assert resp.status_code == 200
        usage = resp.json()["usage"]
        assert usage["total_requests"] == 200


# ===================================================================
# 6. UsageTracker resilience
# ===================================================================

class TestUsageTrackerResilience:

    def test_record_with_missing_attributes(self):
        """Tracker should handle objects with missing attributes gracefully."""
        tracker = UsageTracker()

        class Bare:
            pass

        tracker.record(Bare())
        s = tracker.summary()
        assert s["total_requests"] == 1
        assert "unknown" in s["by_lane"]

    def test_record_with_none_values(self):
        """Tracker should handle None values without crashing."""
        tracker = UsageTracker()

        @dataclass
        class NoneDecision:
            lane: str = None
            model: str = None
            success: bool = False
            elapsed_ms: float = 0.0

        tracker.record(NoneDecision())
        s = tracker.summary()
        assert s["total_requests"] == 1

    def test_summary_serialisable(self):
        """Summary output must be JSON-serialisable."""
        tracker = UsageTracker()
        tracker.record(MagicMock(
            lane="local_utility", model="local-llm",
            success=True, elapsed_ms=5.0,
        ))
        summary = tracker.summary()
        # Should not raise
        serialised = json.dumps(summary)
        assert isinstance(serialised, str)

    def test_lane_breakdown_serialisable(self):
        """Lane breakdown must be JSON-serialisable."""
        tracker = UsageTracker()
        tracker.record(MagicMock(
            lane="flagship_fast", model="gpt-4o-mini",
            success=True, elapsed_ms=50.0,
        ))
        breakdown = tracker.lane_breakdown()
        serialised = json.dumps(breakdown)
        assert isinstance(serialised, str)


# ===================================================================
# 7. Router error containment
# ===================================================================

class TestRouterErrorContainment:

    def test_local_error_does_not_crash_router(self, router, mock_local):
        """A LocalModelError should be contained, not propagate."""
        mock_local.classify_intent.side_effect = LocalModelError("crash")
        result = router.route("classify_intent", "text")
        assert result.executed is False
        assert result.decision.success is False
        # Should NOT raise

    def test_flagship_error_does_not_crash_router(self, registry, mock_local):
        """A FlagshipError should be contained, not propagate."""
        flagship = MagicMock(spec=FlagshipClient)
        flagship._profile = MagicMock()
        from src.core.provider_profile import LaneConfig
        flagship._get_lane_config.return_value = LaneConfig(
            model="m", max_tokens=1, temperature=0.1
        )
        flagship.complete.side_effect = FlagshipError("network down")

        r = ModelRouter(registry=registry, local_client=mock_local,
                        flagship_client=flagship)
        # Deep task type to avoid fast→deep retry
        result = r.route("plan", "text")
        assert result.executed is False
        assert result.decision.success is False

    def test_error_decision_is_complete(self, router, mock_local):
        """Error decisions should have all required fields populated."""
        mock_local.summarize.side_effect = LocalModelError("timeout")
        result = router.route("summarize", "text")
        d = result.decision
        assert d.id is not None
        assert len(d.id) == 36
        assert d.timestamp is not None
        assert "T" in d.timestamp
        assert d.task_type == "summarize"
        assert d.lane in ("local_utility", "local_redaction")
        assert d.elapsed_ms >= 0
        assert d.error is not None

    def test_error_tracked_in_usage(self, router, mock_local):
        """Failed routes should still be tracked in usage."""
        mock_local.classify_intent.side_effect = LocalModelError("fail")
        router.route("classify_intent", "text")
        usage = router.usage.summary()
        assert usage["total_requests"] == 1
        lanes = usage["by_lane"]
        assert lanes["local_utility"]["failures"] == 1


# ===================================================================
# 8. FlagshipClient error containment
# ===================================================================

class TestFlagshipClientErrors:

    def test_missing_api_key_raises_flagship_error(self):
        """Should raise FlagshipError, not a generic exception."""
        from src.core.provider_profile import ProviderProfile, LaneConfig
        profile = ProviderProfile(
            name="gemini",
            display_name="Gemini",
            fast=LaneConfig(model="flash", max_tokens=1, temperature=0.1),
            deep=LaneConfig(model="pro", max_tokens=1, temperature=0.1),
        )
        client = FlagshipClient("gemini", profile)
        with pytest.raises(FlagshipError, match="API key not configured"):
            client.complete("test")

    def test_unsupported_provider_raises_flagship_error(self):
        """Unsupported providers should raise FlagshipError."""
        from src.core.provider_profile import ProviderProfile, LaneConfig
        profile = ProviderProfile(
            name="unknown",
            display_name="Unknown",
            fast=LaneConfig(model="m", max_tokens=1, temperature=0.1),
            deep=LaneConfig(model="m", max_tokens=1, temperature=0.1),
        )
        client = FlagshipClient("unknown", profile)
        # Set a fake key to bypass the key check
        client._api_key = "fake"
        with pytest.raises(FlagshipError, match="Unsupported provider"):
            client.complete("test")

    def test_invalid_lane_raises_flagship_error(self):
        """Invalid lane names should raise FlagshipError."""
        from src.core.provider_profile import ProviderProfile, LaneConfig
        profile = ProviderProfile(
            name="gemini",
            display_name="Gemini",
            fast=LaneConfig(model="flash", max_tokens=1, temperature=0.1),
            deep=LaneConfig(model="pro", max_tokens=1, temperature=0.1),
        )
        client = FlagshipClient("gemini", profile)
        client._api_key = "fake"
        with pytest.raises(FlagshipError, match="Unknown lane"):
            client.complete("test", lane="nonexistent")


# ===================================================================
# 9. LocalModelClient error containment
# ===================================================================

class TestLocalModelClientErrors:

    def test_connection_refused_raises_local_error(self):
        """Connection failures should raise LocalModelError."""
        client = LocalModelClient(base_url="http://localhost:99999")
        with pytest.raises(LocalModelError):
            client.health()

    def test_is_healthy_returns_false_on_error(self):
        """is_healthy should return False, not raise."""
        client = LocalModelClient(base_url="http://localhost:99999")
        assert client.is_healthy() is False


# ===================================================================
# 10. RouterDecision serialisation safety
# ===================================================================

class TestDecisionSerialisation:

    def test_to_dict_is_json_safe(self, router, mock_local):
        """RouterDecision.to_dict() must produce JSON-serialisable output."""
        router.route("classify_intent", "test")
        d = router.recent_decisions[0]
        result = json.dumps(d.to_dict())
        assert isinstance(result, str)

    def test_to_dict_no_internal_objects(self, router, mock_local):
        """to_dict should contain only primitive types."""
        router.route("classify_intent", "test")
        d = router.recent_decisions[0].to_dict()
        for key, val in d.items():
            assert isinstance(val, (str, int, float, bool, type(None))), (
                f"Field '{key}' has non-primitive type: {type(val)}"
            )

    def test_error_decision_serialises(self, router, mock_local):
        """Error decisions should also serialise cleanly."""
        mock_local.classify_intent.side_effect = LocalModelError(
            "Connection refused: http://local:8080"
        )
        router.route("classify_intent", "text")
        d = router.recent_decisions[0]
        result = json.dumps(d.to_dict())
        assert isinstance(result, str)
        # Error field should be present
        parsed = json.loads(result)
        assert parsed["error"] is not None
        assert parsed["success"] is False
