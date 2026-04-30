"""
Tests for src.core.model_router — ModelRouter v1 (local utility routing).
Prompt 15: ModelRouter v1 (Local Utility).
"""

import json
import pytest
import yaml
from unittest.mock import MagicMock, patch

from src.core.model_router import ModelRouter, RouterDecision, RouterResult
from src.core.local_model_roles import (
    LocalModelRoleConfig,
    ROLE_SCRUB_SEGMENT_VERIFIER,
    ROLE_UTILITY,
)
from src.core.model_usage_policy import (
    FRONTIER_SCRUB_REQUIRED,
    LOCAL_EXECUTION_DISABLED,
    LOCAL_EXECUTION_LOW_RISK_ONLY,
    init_model_usage_policy,
    update_model_usage_policy,
)
from src.core.provider_profile import ProfileRegistry
from src.core.local_model_client import LocalModelClient, LocalModelError


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
                "fast": {"model": "gemini-flash", "max_tokens": 4096, "temperature": 0.3},
                "deep": {"model": "gemini-pro", "max_tokens": 8192, "temperature": 0.7},
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
        "escalation": {
            "triggers": [
                {"type": "risk", "description": "High-risk"},
            ],
        },
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
    """Mock LocalModelClient."""
    client = MagicMock(spec=LocalModelClient)
    client.classify_intent.return_value = "question"
    client.extract_json.return_value = {"name": "John", "age": 30}
    client.summarize.return_value = "Summary text."
    client.redact.return_value = "[NAME] lives at [ADDRESS]."
    client.rag_rewrite.return_value = "improved search query"
    client.complete.return_value = "raw output"
    return client


@pytest.fixture
def router(registry, mock_local):
    return ModelRouter(registry=registry, local_client=mock_local)


@pytest.fixture(autouse=True)
def _init_model_policy(tmp_data_dir):
    init_model_usage_policy(str(tmp_data_dir))
    update_model_usage_policy(
        local_execution_mode=LOCAL_EXECUTION_LOW_RISK_ONLY,
        frontier_scrub_mode=FRONTIER_SCRUB_REQUIRED,
    )
    yield


# ===================================================================
# Lane determination
# ===================================================================

class TestLaneDetermination:

    def test_redact_routes_to_local_redaction(self, router):
        result = router.route("redact", "John Smith at 123 Main St")
        assert result.decision.lane == "local_redaction"

    def test_classify_intent_routes_to_local_utility(self, router):
        result = router.route("classify_intent", "What time is it?")
        assert result.decision.lane == "local_utility"

    def test_extract_json_routes_to_local_utility(self, router):
        result = router.route("extract_json", "John is 30", schema="{}")
        assert result.decision.lane == "local_utility"

    def test_summarize_routes_to_local_utility(self, router):
        result = router.route("summarize", "Long text here")
        assert result.decision.lane == "local_utility"

    def test_rag_rewrite_routes_to_local_utility(self, router):
        result = router.route("rag_rewrite", "what is ML?")
        assert result.decision.lane == "local_utility"

    def test_unknown_task_routes_to_flagship(self, router):
        result = router.route("conversation", "Hello there")
        assert result.decision.lane == "flagship_fast"

    def test_planning_routes_to_deep(self, router):
        result = router.route("plan", "Design a system")
        assert result.decision.lane == "flagship_deep"

    def test_local_execution_disabled_routes_utility_to_flagship(self, router):
        update_model_usage_policy(local_execution_mode=LOCAL_EXECUTION_DISABLED)
        result = router.route("summarize", "Long text here")
        assert result.decision.lane == "flagship_fast"

    def test_redaction_stays_local_when_local_execution_disabled(self, router):
        update_model_usage_policy(local_execution_mode=LOCAL_EXECUTION_DISABLED)
        result = router.route("redact", "John Smith at 123 Main St")
        assert result.decision.lane == "local_redaction"


# ===================================================================
# Local execution — classify_intent
# ===================================================================

class TestClassifyIntent:

    def test_executes_and_returns_output(self, router, mock_local):
        result = router.route("classify_intent", "What time is it?")
        assert result.executed is True
        assert result.output == "question"
        mock_local.classify_intent.assert_called_once_with("What time is it?")

    def test_decision_records_success(self, router):
        result = router.route("classify_intent", "Hello")
        assert result.decision.success is True
        assert result.decision.model == "local-llm"
        assert result.decision.error is None


# ===================================================================
# Local execution — extract_json
# ===================================================================

class TestExtractJson:

    def test_executes_and_returns_data(self, router, mock_local):
        result = router.route("extract_json", "John is 30", schema='{"name": "string"}')
        assert result.executed is True
        assert result.data == {"name": "John", "age": 30}
        mock_local.extract_json.assert_called_once_with("John is 30", '{"name": "string"}')

    def test_default_schema(self, router, mock_local):
        result = router.route("extract_json", "text")
        mock_local.extract_json.assert_called_once_with("text", "{}")


# ===================================================================
# Local execution — summarize
# ===================================================================

class TestSummarize:

    def test_executes_and_returns_output(self, router, mock_local):
        result = router.route("summarize", "A very long document...")
        assert result.executed is True
        assert result.output == "Summary text."
        mock_local.summarize.assert_called_once_with("A very long document...")


# ===================================================================
# Local execution — redact
# ===================================================================

class TestRedact:

    def test_executes_via_local_redaction_lane(self, router, mock_local):
        result = router.route("redact", "John Smith, 555-1234")
        assert result.executed is True
        assert result.decision.lane == "local_redaction"
        assert result.output == "[NAME] lives at [ADDRESS]."
        mock_local.redact.assert_called_once()

    def test_redaction_rationale_mentions_privacy(self, router):
        result = router.route("redact", "text")
        assert "privacy" in result.decision.rationale.lower()


# ===================================================================
# Local execution — rag_rewrite
# ===================================================================

class TestRagRewrite:

    def test_executes_and_returns_output(self, router, mock_local):
        result = router.route("rag_rewrite", "what's ML?")
        assert result.executed is True
        assert result.output == "improved search query"
        mock_local.rag_rewrite.assert_called_once_with("what's ML?")


# ===================================================================
# Flagship routing (not executed in v1)
# ===================================================================

class TestFlagshipRouting:

    def test_not_executed(self, router):
        result = router.route("conversation", "Hello")
        assert result.executed is False
        assert result.output is None

    def test_decision_recorded(self, router):
        result = router.route("conversation", "Hello")
        assert result.decision.task_type == "conversation"
        assert result.decision.lane == "flagship_fast"
        # No flagship client configured → recorded as failure
        assert result.decision.success is False

    def test_model_is_pending(self, router):
        result = router.route("plan", "Design a system")
        assert result.decision.model == "pending"

    def test_rationale_mentions_flagship(self, router):
        result = router.route("chat", "Hi")
        assert "flagship" in result.decision.rationale.lower()


# ===================================================================
# Error handling
# ===================================================================

class TestErrorHandling:

    def test_local_error_sets_failure(self, router, mock_local):
        mock_local.classify_intent.side_effect = LocalModelError("connection refused")
        result = router.route("classify_intent", "text")
        assert result.executed is False
        assert result.decision.success is False
        assert "connection refused" in result.decision.error

    def test_no_local_client(self, registry):
        router = ModelRouter(registry=registry, local_client=None)
        result = router.route("classify_intent", "text")
        assert result.executed is False
        assert result.decision.success is False
        assert "not configured" in result.decision.error

    def test_error_still_records_decision(self, router, mock_local):
        mock_local.summarize.side_effect = LocalModelError("timeout")
        result = router.route("summarize", "text")
        assert result.decision.id is not None
        assert result.decision.timestamp is not None
        assert len(router.recent_decisions) == 1

    def test_model_name_fallback_logs_warning(self, registry, mock_local, caplog):
        flagship = MagicMock()
        flagship.get_lane_config.side_effect = RuntimeError("profile unavailable")
        flagship.complete.return_value = "ok"
        router = ModelRouter(
            registry=registry,
            local_client=mock_local,
            flagship_client=flagship,
        )
        with caplog.at_level("WARNING"):
            result = router.route("conversation", "hello")
        assert result.decision.model == "flagship-fast"
        assert "Falling back to synthetic flagship model name" in caplog.text

    def test_sdk_model_name_fallback_logs_warning(self, registry, mock_local, caplog):
        provider_client = MagicMock()
        provider_client.build_user_message.return_value = {"role": "user", "content": "hello"}
        provider_client.generate.return_value = MagicMock(text="ok")
        sdk_registry = MagicMock()
        sdk_registry.is_local_task.return_value = False
        sdk_registry.provider_names = ["gemini"]
        sdk_registry.get_profile.side_effect = RuntimeError("registry unavailable")

        router = ModelRouter(
            registry=sdk_registry,
            local_client=mock_local,
            provider_client=provider_client,
        )
        with caplog.at_level("WARNING"):
            result = router.route("conversation", "hello")

        assert result.decision.model == "sdk-fast"
        assert "Falling back to synthetic SDK model name" in caplog.text


# ===================================================================
# RouterDecision data
# ===================================================================

class TestRouterDecision:

    def test_has_uuid_id(self, router):
        result = router.route("classify_intent", "text")
        assert len(result.decision.id) == 36  # UUID format

    def test_has_iso_timestamp(self, router):
        result = router.route("classify_intent", "text")
        assert "T" in result.decision.timestamp  # ISO format

    def test_has_elapsed_ms(self, router):
        result = router.route("classify_intent", "text")
        assert result.decision.elapsed_ms >= 0

    def test_input_preview_truncated(self, router):
        long_text = "x" * 500
        result = router.route("classify_intent", long_text)
        assert len(result.decision.input_preview) <= 120

    def test_to_dict(self, router):
        result = router.route("classify_intent", "text")
        d = result.decision.to_dict()
        assert d["task_type"] == "classify_intent"
        assert d["lane"] == "local_utility"
        assert d["model"] == "local-llm"
        assert d["success"] is True
        assert "id" in d
        assert "timestamp" in d
        assert "rationale" in d
        assert "elapsed_ms" in d


# ===================================================================
# Recent decisions & stats
# ===================================================================

class TestDecisionsAndStats:

    def test_recent_decisions_empty_initially(self, registry, mock_local):
        r = ModelRouter(registry=registry, local_client=mock_local)
        assert len(r.recent_decisions) == 0

    def test_recent_decisions_accumulate(self, router):
        router.route("classify_intent", "a")
        router.route("summarize", "b")
        router.route("redact", "c")
        assert len(router.recent_decisions) == 3

    def test_recent_decisions_newest_first(self, router):
        router.route("classify_intent", "first")
        router.route("summarize", "second")
        decisions = router.recent_decisions
        assert decisions[0].task_type == "summarize"
        assert decisions[1].task_type == "classify_intent"

    def test_stats_empty(self, registry, mock_local):
        r = ModelRouter(registry=registry, local_client=mock_local)
        stats = r.stats
        assert stats["total_decisions"] == 0
        assert stats["success_rate"] == 0.0

    def test_stats_after_routing(self, router):
        router.route("classify_intent", "a")
        router.route("summarize", "b")
        router.route("redact", "c")
        router.route("conversation", "d")

        stats = router.stats
        assert stats["total_decisions"] == 4
        assert stats["by_lane"]["local_utility"] == 2
        assert stats["by_lane"]["local_redaction"] == 1
        assert stats["by_lane"]["flagship_fast"] == 1
        # 3 local succeed, 1 flagship fails (no client) → 0.75
        assert stats["success_rate"] == 0.75
        assert stats["avg_elapsed_ms"] >= 0

    def test_stats_with_failures(self, router, mock_local):
        mock_local.classify_intent.side_effect = LocalModelError("fail")
        router.route("classify_intent", "a")
        router.route("conversation", "b")

        stats = router.stats
        assert stats["total_decisions"] == 2
        # Both fail: local error + no flagship client
        assert stats["success_rate"] == 0.0


# ===================================================================
# All local tasks parametrized
# ===================================================================

class TestAllLocalTasks:

    @pytest.mark.parametrize("task_type,method", [
        ("classify_intent", "classify_intent"),
        ("extract_json", "extract_json"),
        ("summarize", "summarize"),
        ("redact", "redact"),
        ("rag_rewrite", "rag_rewrite"),
    ])
    def test_each_task_executes_locally(self, router, mock_local, task_type, method):
        kwargs = {"schema": "{}"} if task_type == "extract_json" else {}
        result = router.route(task_type, "input text", **kwargs)
        assert result.executed is True
        assert result.decision.model == "local-llm"
        getattr(mock_local, method).assert_called_once()

    @pytest.mark.parametrize("task_type", [
        "classify_intent", "extract_json", "summarize", "redact", "rag_rewrite",
    ])
    def test_each_task_generates_receipt(self, router, task_type):
        kwargs = {"schema": "{}"} if task_type == "extract_json" else {}
        router.route(task_type, "text", **kwargs)
        assert len(router.recent_decisions) == 1
        d = router.recent_decisions[0]
        assert d.task_type == task_type
        assert d.id is not None

    def test_role_router_sends_utility_tasks_to_utility_role(self, registry):
        utility_client = MagicMock()
        utility_client.summarize.return_value = "role summary"
        verifier_client = MagicMock()

        roles = MagicMock()
        roles.config_for.return_value = LocalModelRoleConfig(
            role=ROLE_UTILITY,
            base_url="http://local-bonsai-8b:8080",
            model="bonsai-8b",
            priority=1,
            timeout_s=30.0,
            max_input_chars=12000,
        )
        roles.client_for.return_value = utility_client

        router = ModelRouter(registry=registry, local_roles=roles)

        result = router.route("summarize", "Long text")

        assert result.executed is True
        assert result.output == "role summary"
        assert result.decision.model == "bonsai-8b"
        roles.config_for.assert_called_once_with(ROLE_UTILITY)
        roles.client_for.assert_called_once_with(ROLE_UTILITY)
        utility_client.summarize.assert_called_once_with("Long text")
        verifier_client.redact.assert_not_called()

    def test_role_router_sends_redaction_to_segment_verifier_role(self, registry):
        verifier_client = MagicMock()
        verifier_client.redact.return_value = "[NAME]"

        roles = MagicMock()
        roles.config_for.return_value = LocalModelRoleConfig(
            role=ROLE_SCRUB_SEGMENT_VERIFIER,
            base_url="http://local-bonsai-8b:8080",
            model="bonsai-8b",
            priority=9,
            timeout_s=10.0,
            max_input_chars=8000,
        )
        roles.client_for.return_value = verifier_client

        router = ModelRouter(registry=registry, local_roles=roles)

        result = router.route("redact", "Myles Hamilton")

        assert result.executed is True
        assert result.decision.lane == "local_redaction"
        assert result.decision.model == "bonsai-8b"
        assert result.output == "[NAME]"
        roles.config_for.assert_called_once_with(ROLE_SCRUB_SEGMENT_VERIFIER)
        roles.client_for.assert_called_once_with(ROLE_SCRUB_SEGMENT_VERIFIER)
        verifier_client.redact.assert_called_once_with("Myles Hamilton")


# ===================================================================
# War Room control plane endpoints
# ===================================================================

class TestControlPlaneEndpoints:

    @pytest.fixture(autouse=True)
    def _setup_app(self, router, tmp_data_dir):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.core import api_auth
        from src.core.control_plane import (
            router as cp_router,
            init_control_plane,
            set_model_router,
        )

        api_auth.init_api_auth(lambda request: True)
        init_control_plane(str(tmp_data_dir))
        set_model_router(router)

        app = FastAPI()
        app.include_router(cp_router)
        self.client = TestClient(app)

        # Generate some decisions
        router.route("classify_intent", "What time?")
        router.route("redact", "John Smith")
        router.route("conversation", "Hello")

        yield

        set_model_router(None)
        api_auth.init_api_auth(None)

    def test_decisions_endpoint_returns_200(self):
        resp = self.client.get("/router/decisions")
        assert resp.status_code == 200

    def test_decisions_endpoint_returns_list(self):
        resp = self.client.get("/router/decisions")
        data = resp.json()
        assert "decisions" in data
        assert len(data["decisions"]) == 3
        assert "total" in data

    def test_decisions_contain_required_fields(self):
        resp = self.client.get("/router/decisions")
        d = resp.json()["decisions"][0]
        for field in ("id", "timestamp", "task_type", "lane", "model",
                       "rationale", "elapsed_ms", "success"):
            assert field in d

    def test_stats_endpoint_returns_200(self):
        resp = self.client.get("/router/stats")
        assert resp.status_code == 200

    def test_stats_endpoint_returns_data(self):
        resp = self.client.get("/router/stats")
        stats = resp.json()["stats"]
        assert stats["total_decisions"] == 3
        assert "by_lane" in stats
        assert "success_rate" in stats

    def test_decisions_without_router(self, tmp_data_dir):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.core.control_plane import (
            router as cp_router,
            init_control_plane,
            set_model_router,
        )

        set_model_router(None)
        app = FastAPI()
        app.include_router(cp_router)
        client = TestClient(app)

        resp = client.get("/router/decisions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["decisions"] == []
        assert "not initialised" in data["message"]

    def test_stats_without_router(self, tmp_data_dir):
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

        resp = client.get("/router/stats")
        assert resp.status_code == 200
        assert resp.json()["stats"] == {}
