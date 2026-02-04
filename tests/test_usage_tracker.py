"""
Tests for src.core.usage_tracker and /usage/* War Room endpoints (Prompt 17).
"""

import pytest
import yaml
from dataclasses import dataclass
from typing import Optional
from unittest.mock import MagicMock

from src.core.usage_tracker import (
    UsageTracker,
    LaneUsage,
    _COST_PER_1K,
    _AVG_TOKENS,
)
from src.core.model_router import ModelRouter, RouterDecision, RouterResult
from src.core.provider_profile import ProfileRegistry
from src.core.local_model_client import LocalModelClient, LocalModelError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class FakeDecision:
    """Lightweight stand-in for RouterDecision in unit tests."""
    lane: str
    model: str
    success: bool
    elapsed_ms: float = 5.0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tracker():
    return UsageTracker()


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
# LaneUsage data class
# ===================================================================

class TestLaneUsage:

    def test_defaults(self):
        u = LaneUsage()
        assert u.requests == 0
        assert u.successes == 0
        assert u.failures == 0
        assert u.total_tokens_est == 0
        assert u.total_cost_est == 0.0

    def test_to_dict(self):
        u = LaneUsage(requests=3, successes=2, failures=1,
                       total_tokens_est=300, total_cost_est=0.003,
                       total_elapsed_ms=15.0)
        d = u.to_dict()
        assert d["requests"] == 3
        assert d["successes"] == 2
        assert d["failures"] == 1
        assert d["total_tokens_est"] == 300
        assert d["total_cost_est"] == 0.003
        assert d["avg_elapsed_ms"] == 5.0

    def test_avg_elapsed_zero_requests(self):
        u = LaneUsage()
        assert u.to_dict()["avg_elapsed_ms"] == 0.0


# ===================================================================
# UsageTracker — recording
# ===================================================================

class TestRecording:

    def test_single_record(self, tracker):
        d = FakeDecision(lane="local_utility", model="local-llm", success=True)
        tracker.record(d)
        breakdown = tracker.lane_breakdown()
        assert "local_utility" in breakdown
        assert breakdown["local_utility"]["requests"] == 1
        assert breakdown["local_utility"]["successes"] == 1

    def test_multiple_records(self, tracker):
        for _ in range(5):
            tracker.record(FakeDecision("local_utility", "local-llm", True))
        assert tracker.lane_breakdown()["local_utility"]["requests"] == 5

    def test_failure_counted(self, tracker):
        tracker.record(FakeDecision("flagship_fast", "gpt-4o-mini", False))
        lane = tracker.lane_breakdown()["flagship_fast"]
        assert lane["failures"] == 1
        assert lane["successes"] == 0

    def test_mixed_lanes(self, tracker):
        tracker.record(FakeDecision("local_utility", "local-llm", True))
        tracker.record(FakeDecision("flagship_fast", "gpt-4o-mini", True))
        tracker.record(FakeDecision("flagship_deep", "gpt-4o", True))
        breakdown = tracker.lane_breakdown()
        assert len(breakdown) == 3

    def test_elapsed_accumulated(self, tracker):
        tracker.record(FakeDecision("local_utility", "local-llm", True, 10.0))
        tracker.record(FakeDecision("local_utility", "local-llm", True, 20.0))
        lane = tracker.lane_breakdown()["local_utility"]
        assert lane["total_elapsed_ms"] == 30.0
        assert lane["avg_elapsed_ms"] == 15.0


# ===================================================================
# UsageTracker — cost estimation
# ===================================================================

class TestCostEstimation:

    def test_local_cost_is_zero(self, tracker):
        tracker.record(FakeDecision("local_utility", "local-llm", True))
        lane = tracker.lane_breakdown()["local_utility"]
        assert lane["total_cost_est"] == 0.0

    def test_flagship_cost_positive(self, tracker):
        tracker.record(FakeDecision("flagship_fast", "gpt-4o-mini", True))
        lane = tracker.lane_breakdown()["flagship_fast"]
        assert lane["total_cost_est"] > 0.0

    def test_deep_costs_more_than_fast(self, tracker):
        tracker.record(FakeDecision("flagship_fast", "gpt-4o-mini", True))
        tracker.record(FakeDecision("flagship_deep", "gpt-4o", True))
        breakdown = tracker.lane_breakdown()
        assert breakdown["flagship_deep"]["total_cost_est"] > breakdown["flagship_fast"]["total_cost_est"]

    def test_token_estimates_use_lane_defaults(self, tracker):
        tracker.record(FakeDecision("local_redaction", "local-llm", True))
        tracker.record(FakeDecision("flagship_deep", "gpt-4o", True))
        breakdown = tracker.lane_breakdown()
        assert breakdown["local_redaction"]["total_tokens_est"] == _AVG_TOKENS["local_redaction"]
        assert breakdown["flagship_deep"]["total_tokens_est"] == _AVG_TOKENS["flagship_deep"]

    def test_unknown_model_uses_default_rate(self, tracker):
        tracker.record(FakeDecision("flagship_fast", "unknown-model-xyz", True))
        # Should not crash; uses fallback rate
        lane = tracker.lane_breakdown()["flagship_fast"]
        assert lane["total_cost_est"] > 0.0


# ===================================================================
# UsageTracker — savings
# ===================================================================

class TestSavings:

    def test_no_savings_without_local(self, tracker):
        tracker.record(FakeDecision("flagship_fast", "gpt-4o-mini", True))
        savings = tracker.estimated_savings()
        assert savings["local_requests"] == 0
        assert savings["estimated_savings"] == 0.0

    def test_savings_with_local_requests(self, tracker):
        for _ in range(10):
            tracker.record(FakeDecision("local_utility", "local-llm", True))
        savings = tracker.estimated_savings()
        assert savings["local_requests"] == 10
        assert savings["estimated_savings"] > 0.0
        assert savings["hypothetical_flagship_cost"] > 0.0

    def test_savings_includes_redaction(self, tracker):
        tracker.record(FakeDecision("local_redaction", "local-llm", True))
        tracker.record(FakeDecision("local_utility", "local-llm", True))
        savings = tracker.estimated_savings()
        assert savings["local_requests"] == 2

    def test_savings_description_readable(self, tracker):
        tracker.record(FakeDecision("local_utility", "local-llm", True))
        savings = tracker.estimated_savings()
        assert "1 requests handled locally" in savings["savings_description"]
        assert "$0" in savings["savings_description"]


# ===================================================================
# UsageTracker — summary
# ===================================================================

class TestSummary:

    def test_empty_summary(self, tracker):
        s = tracker.summary()
        assert s["total_requests"] == 0
        assert s["total_tokens_est"] == 0
        assert s["total_cost_est"] == 0.0
        assert s["success_rate"] == 0.0
        assert s["by_lane"] == {}

    def test_summary_after_requests(self, tracker):
        tracker.record(FakeDecision("local_utility", "local-llm", True, 10.0))
        tracker.record(FakeDecision("flagship_fast", "gpt-4o-mini", True, 50.0))
        s = tracker.summary()
        assert s["total_requests"] == 2
        assert s["total_tokens_est"] > 0
        assert s["success_rate"] == 1.0
        assert "local_utility" in s["by_lane"]
        assert "flagship_fast" in s["by_lane"]
        assert "savings" in s

    def test_summary_has_period_start(self, tracker):
        s = tracker.summary()
        assert "period_start" in s
        assert "T" in s["period_start"]  # ISO format

    def test_summary_success_rate_partial(self, tracker):
        tracker.record(FakeDecision("flagship_fast", "gpt-4o-mini", True))
        tracker.record(FakeDecision("flagship_fast", "gpt-4o-mini", False))
        s = tracker.summary()
        assert s["success_rate"] == 0.5

    def test_summary_avg_elapsed(self, tracker):
        tracker.record(FakeDecision("local_utility", "local-llm", True, 10.0))
        tracker.record(FakeDecision("local_utility", "local-llm", True, 30.0))
        s = tracker.summary()
        assert s["avg_elapsed_ms"] == 20.0


# ===================================================================
# UsageTracker — reset
# ===================================================================

class TestReset:

    def test_reset_clears_counters(self, tracker):
        tracker.record(FakeDecision("local_utility", "local-llm", True))
        tracker.record(FakeDecision("flagship_fast", "gpt-4o-mini", True))
        tracker.reset()
        s = tracker.summary()
        assert s["total_requests"] == 0
        assert s["by_lane"] == {}

    def test_reset_updates_period_start(self, tracker):
        old_start = tracker.summary()["period_start"]
        tracker.reset()
        new_start = tracker.summary()["period_start"]
        assert new_start >= old_start

    def test_recording_after_reset(self, tracker):
        tracker.record(FakeDecision("local_utility", "local-llm", True))
        tracker.reset()
        tracker.record(FakeDecision("flagship_fast", "gpt-4o-mini", True))
        s = tracker.summary()
        assert s["total_requests"] == 1
        assert "flagship_fast" in s["by_lane"]
        assert "local_utility" not in s["by_lane"]


# ===================================================================
# Integration: ModelRouter auto-records to UsageTracker
# ===================================================================

class TestRouterIntegration:

    def test_router_has_usage_tracker(self, router):
        assert hasattr(router, 'usage')
        assert isinstance(router.usage, UsageTracker)

    def test_local_routing_auto_records(self, router):
        router.route("classify_intent", "Hello")
        s = router.usage.summary()
        assert s["total_requests"] == 1
        assert "local_utility" in s["by_lane"]

    def test_redaction_auto_records(self, router):
        router.route("redact", "John Smith")
        s = router.usage.summary()
        assert "local_redaction" in s["by_lane"]

    def test_flagship_auto_records(self, router):
        router.route("conversation", "Hi there")
        s = router.usage.summary()
        # Flagship without client still records the attempt
        assert s["total_requests"] == 1

    def test_multiple_routes_accumulate(self, router):
        router.route("classify_intent", "a")
        router.route("summarize", "b")
        router.route("redact", "c")
        router.route("conversation", "d")
        s = router.usage.summary()
        assert s["total_requests"] == 4

    def test_local_savings_tracked(self, router):
        router.route("classify_intent", "a")
        router.route("summarize", "b")
        router.route("redact", "c")
        savings = router.usage.estimated_savings()
        assert savings["local_requests"] == 3
        assert savings["estimated_savings"] > 0.0

    def test_usage_reset_independent_of_decisions(self, router):
        router.route("classify_intent", "a")
        router.usage.reset()
        # Decisions are still in the router
        assert len(router.recent_decisions) == 1
        # But usage is cleared
        assert router.usage.summary()["total_requests"] == 0


# ===================================================================
# War Room /usage/* endpoints
# ===================================================================

class TestUsageEndpoints:

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

        # Generate some usage
        router.route("classify_intent", "What time?")
        router.route("redact", "John Smith")
        router.route("summarize", "Long text")
        router.route("conversation", "Hello")

        yield

        set_model_router(None)

    def test_summary_endpoint_200(self):
        resp = self.client.get("/usage/summary")
        assert resp.status_code == 200

    def test_summary_endpoint_has_data(self):
        resp = self.client.get("/usage/summary")
        data = resp.json()
        assert "usage" in data
        usage = data["usage"]
        assert usage["total_requests"] == 4
        assert "by_lane" in usage
        assert "savings" in usage

    def test_lanes_endpoint_200(self):
        resp = self.client.get("/usage/lanes")
        assert resp.status_code == 200

    def test_lanes_endpoint_has_breakdown(self):
        resp = self.client.get("/usage/lanes")
        data = resp.json()
        assert "lanes" in data
        lanes = data["lanes"]
        assert "local_utility" in lanes
        assert "local_redaction" in lanes

    def test_savings_endpoint_200(self):
        resp = self.client.get("/usage/savings")
        assert resp.status_code == 200

    def test_savings_endpoint_has_data(self):
        resp = self.client.get("/usage/savings")
        data = resp.json()
        assert "savings" in data
        savings = data["savings"]
        assert savings["local_requests"] == 3  # classify + redact + summarize
        assert savings["estimated_savings"] > 0.0

    def test_reset_endpoint_clears(self):
        resp = self.client.post("/usage/reset")
        assert resp.status_code == 200
        data = resp.json()
        assert data["usage"]["total_requests"] == 0

    def test_summary_without_router(self, tmp_data_dir):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.core.control_plane import (
            router as cp_router,
            set_model_router,
        )

        set_model_router(None)
        app = FastAPI()
        app.include_router(cp_router)
        client = TestClient(app)

        resp = client.get("/usage/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["usage"] == {}
        assert "not initialised" in data["message"]

    def test_lanes_without_router(self, tmp_data_dir):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.core.control_plane import (
            router as cp_router,
            set_model_router,
        )

        set_model_router(None)
        app = FastAPI()
        app.include_router(cp_router)
        client = TestClient(app)

        resp = client.get("/usage/lanes")
        assert resp.status_code == 200
        assert resp.json()["lanes"] == {}

    def test_savings_without_router(self, tmp_data_dir):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.core.control_plane import (
            router as cp_router,
            set_model_router,
        )

        set_model_router(None)
        app = FastAPI()
        app.include_router(cp_router)
        client = TestClient(app)

        resp = client.get("/usage/savings")
        assert resp.status_code == 200
        assert resp.json()["savings"] == {}

    def test_reset_without_router(self, tmp_data_dir):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.core.control_plane import (
            router as cp_router,
            set_model_router,
        )

        set_model_router(None)
        app = FastAPI()
        app.include_router(cp_router)
        client = TestClient(app)

        resp = client.post("/usage/reset")
        assert resp.status_code == 400


# ===================================================================
# Cost table sanity
# ===================================================================

class TestCostTable:

    def test_all_known_models_have_rates(self):
        expected_models = [
            "gemini-2.0-flash", "gemini-2.0-pro",
            "gpt-4o-mini", "gpt-4o",
            "claude-3-5-haiku-latest", "claude-sonnet-4-20250514",
            "local-llm",
        ]
        for model in expected_models:
            assert model in _COST_PER_1K, f"Missing cost rate for {model}"

    def test_local_rate_is_zero(self):
        assert _COST_PER_1K["local-llm"] == 0.0

    def test_deep_models_cost_more(self):
        assert _COST_PER_1K["gemini-2.0-pro"] > _COST_PER_1K["gemini-2.0-flash"]
        assert _COST_PER_1K["gpt-4o"] > _COST_PER_1K["gpt-4o-mini"]
        assert _COST_PER_1K["claude-sonnet-4-20250514"] > _COST_PER_1K["claude-3-5-haiku-latest"]

    def test_all_lanes_have_token_defaults(self):
        for lane in ("local_redaction", "local_utility", "flagship_fast", "flagship_deep"):
            assert lane in _AVG_TOKENS

    def test_deep_lane_estimates_more_tokens(self):
        assert _AVG_TOKENS["flagship_deep"] > _AVG_TOKENS["flagship_fast"]
        assert _AVG_TOKENS["flagship_fast"] > _AVG_TOKENS["local_utility"]
