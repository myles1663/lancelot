"""
Tests for Provider Lanes & Escalation.
Prompt 16: fast/deep flagship lanes with risk-based escalation.

Tests cover:
- FlagshipClient construction and configuration
- FlagshipClient provider dispatch (mocked HTTP)
- ModelRouter escalation logic (task type, risk keywords, failure)
- ModelRouter flagship execution (mocked FlagshipClient)
- Failure escalation (fast → deep retry)
- Forced lane override
- Integration tests with real APIs (env-gated)
"""

import json
import os
import pytest
import yaml
from unittest.mock import MagicMock, patch, PropertyMock
from urllib.error import HTTPError, URLError

from src.core.flagship_client import FlagshipClient, FlagshipError
from src.core.model_router import (
    ModelRouter, RouterDecision, RouterResult,
    _DEEP_TASK_TYPES, _RISK_KEYWORDS,
)
from src.core.provider_profile import ProfileRegistry, ProviderProfile, LaneConfig
from src.core.local_model_client import LocalModelClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def gemini_profile():
    return ProviderProfile(
        name="gemini",
        display_name="Google Gemini",
        fast=LaneConfig(model="gemini-2.0-flash", max_tokens=4096, temperature=0.3),
        deep=LaneConfig(model="gemini-2.0-pro", max_tokens=8192, temperature=0.7),
        cache=LaneConfig(model="gemini-2.0-flash", max_tokens=2048, temperature=0.1),
    )


@pytest.fixture
def openai_profile():
    return ProviderProfile(
        name="openai",
        display_name="OpenAI",
        fast=LaneConfig(model="gpt-4o-mini", max_tokens=4096, temperature=0.3),
        deep=LaneConfig(model="gpt-4o", max_tokens=8192, temperature=0.7),
    )


@pytest.fixture
def anthropic_profile():
    return ProviderProfile(
        name="anthropic",
        display_name="Anthropic",
        fast=LaneConfig(model="claude-3-5-haiku-latest", max_tokens=4096, temperature=0.3),
        deep=LaneConfig(model="claude-sonnet-4-20250514", max_tokens=8192, temperature=0.7),
    )


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
        "escalation": {
            "triggers": [
                {"type": "risk", "description": "High-risk"},
                {"type": "complexity", "description": "Complex"},
                {"type": "failure", "description": "Fast lane failure"},
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
    client = MagicMock(spec=LocalModelClient)
    client.classify_intent.return_value = "question"
    client.redact.return_value = "[NAME]"
    return client


@pytest.fixture
def mock_flagship():
    client = MagicMock(spec=FlagshipClient)
    client.complete.return_value = "flagship response"
    client.is_configured.return_value = True
    client._profile = MagicMock()
    client._get_lane_config.side_effect = lambda lane: (
        LaneConfig(model="gemini-2.0-flash", max_tokens=4096, temperature=0.3)
        if lane == "fast"
        else LaneConfig(model="gemini-2.0-pro", max_tokens=8192, temperature=0.7)
    )
    return client


@pytest.fixture
def router(registry, mock_local, mock_flagship):
    return ModelRouter(
        registry=registry,
        local_client=mock_local,
        flagship_client=mock_flagship,
    )


# ===================================================================
# FlagshipClient — construction
# ===================================================================

class TestFlagshipClientInit:

    def test_gemini_client(self, gemini_profile):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            c = FlagshipClient("gemini", gemini_profile)
            assert c.is_configured() is True

    def test_openai_client(self, openai_profile):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            c = FlagshipClient("openai", openai_profile)
            assert c.is_configured() is True

    def test_anthropic_client(self, anthropic_profile):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            c = FlagshipClient("anthropic", anthropic_profile)
            assert c.is_configured() is True

    def test_not_configured_without_key(self, gemini_profile):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GEMINI_API_KEY", None)
            c = FlagshipClient("gemini", gemini_profile)
            assert c.is_configured() is False

    def test_complete_raises_without_key(self, gemini_profile):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GEMINI_API_KEY", None)
            c = FlagshipClient("gemini", gemini_profile)
            with pytest.raises(FlagshipError, match="API key not configured"):
                c.complete("hello")


# ===================================================================
# FlagshipClient — lane config
# ===================================================================

class TestFlagshipLaneConfig:

    def test_fast_lane(self, gemini_profile):
        c = FlagshipClient("gemini", gemini_profile)
        lc = c._get_lane_config("fast")
        assert lc.model == "gemini-2.0-flash"

    def test_deep_lane(self, gemini_profile):
        c = FlagshipClient("gemini", gemini_profile)
        lc = c._get_lane_config("deep")
        assert lc.model == "gemini-2.0-pro"

    def test_cache_lane(self, gemini_profile):
        c = FlagshipClient("gemini", gemini_profile)
        lc = c._get_lane_config("cache")
        assert lc.model == "gemini-2.0-flash"
        assert lc.max_tokens == 2048

    def test_no_cache_raises(self, openai_profile):
        c = FlagshipClient("openai", openai_profile)
        with pytest.raises(FlagshipError, match="no cache lane"):
            c._get_lane_config("cache")

    def test_unknown_lane_raises(self, gemini_profile):
        c = FlagshipClient("gemini", gemini_profile)
        with pytest.raises(FlagshipError, match="Unknown lane"):
            c._get_lane_config("turbo")


# ===================================================================
# FlagshipClient — Gemini API (mocked HTTP)
# ===================================================================

class TestFlagshipGemini:

    @patch("urllib.request.urlopen")
    def test_gemini_complete(self, mock_open, gemini_profile):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "candidates": [{"content": {"parts": [{"text": "Paris"}]}}],
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = mock_resp

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            c = FlagshipClient("gemini", gemini_profile)
            result = c.complete("Capital of France?", lane="fast")
            assert result == "Paris"

    @patch("urllib.request.urlopen")
    def test_gemini_uses_correct_model(self, mock_open, gemini_profile):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "candidates": [{"content": {"parts": [{"text": "out"}]}}],
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = mock_resp

        with patch.dict(os.environ, {"GEMINI_API_KEY": "k"}):
            c = FlagshipClient("gemini", gemini_profile)
            c.complete("test", lane="deep")
            url = mock_open.call_args[0][0].full_url
            assert "gemini-2.0-pro" in url


# ===================================================================
# FlagshipClient — OpenAI API (mocked HTTP)
# ===================================================================

class TestFlagshipOpenAI:

    @patch("urllib.request.urlopen")
    def test_openai_complete(self, mock_open, openai_profile):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": "Hello!"}}],
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = mock_resp

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            c = FlagshipClient("openai", openai_profile)
            result = c.complete("Hi", lane="fast")
            assert result == "Hello!"

    @patch("urllib.request.urlopen")
    def test_openai_sends_auth_header(self, mock_open, openai_profile):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": "out"}}],
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = mock_resp

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            c = FlagshipClient("openai", openai_profile)
            c.complete("test", lane="fast")
            req = mock_open.call_args[0][0]
            assert "Bearer sk-test" in req.get_header("Authorization")


# ===================================================================
# FlagshipClient — Anthropic API (mocked HTTP)
# ===================================================================

class TestFlagshipAnthropic:

    @patch("urllib.request.urlopen")
    def test_anthropic_complete(self, mock_open, anthropic_profile):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "content": [{"text": "Bonjour!"}],
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = mock_resp

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            c = FlagshipClient("anthropic", anthropic_profile)
            result = c.complete("Hello", lane="fast")
            assert result == "Bonjour!"

    @patch("urllib.request.urlopen")
    def test_anthropic_sends_api_key_header(self, mock_open, anthropic_profile):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "content": [{"text": "out"}],
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = mock_resp

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "ant-key"}):
            c = FlagshipClient("anthropic", anthropic_profile)
            c.complete("test", lane="deep")
            req = mock_open.call_args[0][0]
            assert req.get_header("X-api-key") == "ant-key"
            assert req.get_header("Anthropic-version") == "2023-06-01"


# ===================================================================
# FlagshipClient — error handling
# ===================================================================

class TestFlagshipErrors:

    @patch("urllib.request.urlopen")
    def test_http_error_raises(self, mock_open, gemini_profile):
        mock_open.side_effect = HTTPError(
            "http://api.google.com", 429, "Rate limited",
            {}, MagicMock(read=lambda: b"rate limit exceeded"),
        )
        with patch.dict(os.environ, {"GEMINI_API_KEY": "k"}):
            c = FlagshipClient("gemini", gemini_profile)
            with pytest.raises(FlagshipError, match="429"):
                c.complete("test")

    @patch("urllib.request.urlopen")
    def test_connection_error_raises(self, mock_open, gemini_profile):
        mock_open.side_effect = URLError("connection refused")
        with patch.dict(os.environ, {"GEMINI_API_KEY": "k"}):
            c = FlagshipClient("gemini", gemini_profile)
            with pytest.raises(FlagshipError, match="Connection failed"):
                c.complete("test")

    def test_unsupported_provider(self, gemini_profile):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "k"}):
            c = FlagshipClient("mistral", gemini_profile)
            c._api_key = "k"
            with pytest.raises(FlagshipError, match="Unsupported provider"):
                c.complete("test")


# ===================================================================
# Escalation — task type based
# ===================================================================

class TestEscalationByTaskType:

    @pytest.mark.parametrize("task_type", list(_DEEP_TASK_TYPES))
    def test_deep_task_types_escalate(self, router, task_type):
        result = router.route(task_type, "some input")
        assert result.decision.lane == "flagship_deep"
        assert "deep" in result.decision.rationale.lower()

    def test_conversation_stays_fast(self, router):
        result = router.route("conversation", "Hello there")
        assert result.decision.lane == "flagship_fast"

    def test_chat_stays_fast(self, router):
        result = router.route("chat", "How are you?")
        assert result.decision.lane == "flagship_fast"


# ===================================================================
# Escalation — risk keyword based
# ===================================================================

class TestEscalationByRiskKeyword:

    @pytest.mark.parametrize("keyword", [
        "delete", "production", "deploy", "security", "critical",
        "destroy", "rollback", "password", "migrate",
    ])
    def test_risk_keywords_escalate(self, router, keyword):
        result = router.route("chat", f"Please {keyword} the database")
        assert result.decision.lane == "flagship_deep"
        assert keyword in result.decision.rationale

    def test_no_risk_stays_fast(self, router):
        result = router.route("chat", "What is the weather today?")
        assert result.decision.lane == "flagship_fast"

    def test_risk_keyword_case_insensitive(self, router):
        result = router.route("chat", "Please DELETE the records")
        assert result.decision.lane == "flagship_deep"


# ===================================================================
# Escalation — failure based (fast → deep retry)
# ===================================================================

class TestEscalationByFailure:

    def test_fast_failure_retries_on_deep(self, registry, mock_local):
        flagship = MagicMock(spec=FlagshipClient)
        flagship._profile = MagicMock()
        flagship._get_lane_config.side_effect = lambda lane: (
            LaneConfig(model="fast-model", max_tokens=4096, temperature=0.3)
            if lane == "fast"
            else LaneConfig(model="deep-model", max_tokens=8192, temperature=0.7)
        )
        # Fail on fast, succeed on deep
        flagship.complete.side_effect = [
            FlagshipError("fast lane rate limited"),
            "deep lane success",
        ]

        router = ModelRouter(
            registry=registry, local_client=mock_local,
            flagship_client=flagship,
        )
        result = router.route("chat", "Hello")
        assert result.executed is True
        assert result.output == "deep lane success"
        assert result.decision.lane == "flagship_deep"
        assert "Escalated" in result.decision.rationale

    def test_both_lanes_fail(self, registry, mock_local):
        flagship = MagicMock(spec=FlagshipClient)
        flagship._profile = MagicMock()
        flagship._get_lane_config.return_value = LaneConfig(
            model="m", max_tokens=1, temperature=0.1
        )
        flagship.complete.side_effect = FlagshipError("all lanes down")

        router = ModelRouter(
            registry=registry, local_client=mock_local,
            flagship_client=flagship,
        )
        result = router.route("chat", "Hello")
        assert result.executed is False
        assert result.decision.success is False
        # Escalation records only the final deep-lane outcome
        assert len(router.recent_decisions) == 1
        assert router.recent_decisions[0].lane == "flagship_deep"

    def test_deep_failure_does_not_retry(self, registry, mock_local):
        flagship = MagicMock(spec=FlagshipClient)
        flagship._profile = MagicMock()
        flagship._get_lane_config.return_value = LaneConfig(
            model="m", max_tokens=1, temperature=0.1
        )
        flagship.complete.side_effect = FlagshipError("deep failed")

        router = ModelRouter(
            registry=registry, local_client=mock_local,
            flagship_client=flagship,
        )
        # Force deep lane — should not retry
        result = router.route("plan", "Design architecture")
        assert result.executed is False
        assert result.decision.success is False
        assert len(router.recent_decisions) == 1  # No retry


# ===================================================================
# Flagship execution (mocked client)
# ===================================================================

class TestFlagshipExecution:

    def test_fast_lane_executes(self, router, mock_flagship):
        result = router.route("chat", "Hello")
        assert result.executed is True
        assert result.output == "flagship response"
        mock_flagship.complete.assert_called_once_with("Hello", lane="fast")

    def test_deep_lane_executes(self, router, mock_flagship):
        result = router.route("plan", "Design a system")
        assert result.executed is True
        mock_flagship.complete.assert_called_once_with(
            "Design a system", lane="deep"
        )

    def test_model_name_resolved(self, router):
        result = router.route("chat", "Hello")
        assert result.decision.model == "gemini-2.0-flash"

    def test_deep_model_name_resolved(self, router):
        result = router.route("plan", "Design")
        assert result.decision.model == "gemini-2.0-pro"

    def test_no_flagship_client_records_failure(self, registry, mock_local):
        router = ModelRouter(registry=registry, local_client=mock_local)
        result = router.route("chat", "Hello")
        assert result.executed is False
        assert "not configured" in result.decision.error


# ===================================================================
# Forced lane override
# ===================================================================

class TestForcedLane:

    def test_force_deep(self, router, mock_flagship):
        result = router.route("chat", "Simple greeting", lane="flagship_deep")
        assert result.decision.lane == "flagship_deep"
        mock_flagship.complete.assert_called_once_with(
            "Simple greeting", lane="deep"
        )

    def test_force_fast(self, router, mock_flagship):
        # Even with risk keyword, forced fast stays fast
        result = router.route("chat", "delete everything", lane="flagship_fast")
        assert result.decision.lane == "flagship_fast"
        mock_flagship.complete.assert_called_once_with(
            "delete everything", lane="fast"
        )

    def test_force_does_not_affect_local(self, router, mock_local):
        # Local tasks should still route locally even without force
        result = router.route("classify_intent", "test")
        assert result.decision.lane == "local_utility"


# ===================================================================
# Receipt generation for flagship
# ===================================================================

class TestFlagshipReceipts:

    def test_successful_flagship_receipt(self, router):
        result = router.route("chat", "Hello")
        d = result.decision
        assert d.success is True
        assert d.lane == "flagship_fast"
        assert d.model == "gemini-2.0-flash"
        assert d.elapsed_ms >= 0
        assert d.input_preview == "Hello"

    def test_failed_flagship_receipt(self, registry, mock_local):
        flagship = MagicMock(spec=FlagshipClient)
        flagship._profile = MagicMock()
        flagship._get_lane_config.return_value = LaneConfig(
            model="m", max_tokens=1, temperature=0.1
        )
        flagship.complete.side_effect = FlagshipError("all down")

        router = ModelRouter(
            registry=registry, local_client=mock_local,
            flagship_client=flagship,
        )
        result = router.route("chat", "Hello")
        # Fast fails → deep fails, last decision recorded
        assert result.decision.success is False
        assert result.decision.error is not None

    def test_stats_include_flagship_lanes(self, router):
        router.route("chat", "Hello")  # fast
        router.route("plan", "Design")  # deep
        router.route("classify_intent", "test")  # local

        stats = router.stats
        assert stats["by_lane"]["flagship_fast"] == 1
        assert stats["by_lane"]["flagship_deep"] == 1
        assert stats["by_lane"]["local_utility"] == 1


# ===================================================================
# Backward compatibility with Prompt 15 tests
# ===================================================================

class TestBackwardCompatibility:

    def test_local_tasks_still_route_locally(self, router, mock_local):
        for task in ["classify_intent", "summarize", "rag_rewrite"]:
            result = router.route(task, "test")
            assert result.decision.lane in ("local_utility", "local_redaction")
            assert result.executed is True

    def test_redact_still_local_redaction(self, router, mock_local):
        result = router.route("redact", "John Smith")
        assert result.decision.lane == "local_redaction"

    def test_stats_still_work(self, router):
        router.route("classify_intent", "a")
        router.route("chat", "b")
        stats = router.stats
        assert stats["total_decisions"] == 2
        assert stats["success_rate"] == 1.0


# ===================================================================
# Integration — real API tests (env-gated)
# ===================================================================

@pytest.mark.integration
class TestRealGeminiAPI:
    """Real Gemini API tests — only run with GEMINI_API_KEY."""

    @pytest.fixture(autouse=True)
    def _check_key(self):
        if not os.environ.get("GEMINI_API_KEY"):
            pytest.skip("GEMINI_API_KEY not set")

    @pytest.fixture
    def client(self, gemini_profile):
        return FlagshipClient("gemini", gemini_profile)

    def test_fast_lane_completion(self, client):
        result = client.complete("What is 2+2? Answer with just the number.", lane="fast")
        assert len(result) > 0

    def test_deep_lane_completion(self, client):
        result = client.complete("What is the capital of France? One word.", lane="deep")
        assert len(result) > 0


@pytest.mark.integration
class TestRealOpenAIAPI:
    """Real OpenAI API tests — only run with OPENAI_API_KEY."""

    @pytest.fixture(autouse=True)
    def _check_key(self):
        if not os.environ.get("OPENAI_API_KEY"):
            pytest.skip("OPENAI_API_KEY not set")

    @pytest.fixture
    def client(self, openai_profile):
        return FlagshipClient("openai", openai_profile)

    def test_fast_lane_completion(self, client):
        result = client.complete("What is 2+2? Answer with just the number.", lane="fast")
        assert len(result) > 0


@pytest.mark.integration
class TestRealAnthropicAPI:
    """Real Anthropic API tests — only run with ANTHROPIC_API_KEY."""

    @pytest.fixture(autouse=True)
    def _check_key(self):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            pytest.skip("ANTHROPIC_API_KEY not set")

    @pytest.fixture
    def client(self, anthropic_profile):
        return FlagshipClient("anthropic", anthropic_profile)

    def test_fast_lane_completion(self, client):
        result = client.complete("What is 2+2? Answer with just the number.", lane="fast")
        assert len(result) > 0
