"""
Tests for NVIDIA Nemotron provider integration.

Tests cover:
- NvidiaProviderClient construction and methods (mocked OpenAI SDK)
- FlagshipClient NVIDIA dispatch (mocked HTTP)
- Factory registration
- Config loading from models.yaml
- Usage tracker cost rates
- Integration tests with real API (env-gated)
"""

import json
import os
import sys
import pathlib
import pytest
import yaml
from unittest.mock import MagicMock, patch

from src.core.flagship_client import FlagshipClient, FlagshipError
from src.core.provider_profile import ProfileRegistry, ProviderProfile, LaneConfig
from src.core.usage_tracker import _FALLBACK_COST_PER_1K


# ---------------------------------------------------------------------------
# Check if openai SDK is available
# ---------------------------------------------------------------------------
try:
    import openai as _openai_module
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

needs_openai = pytest.mark.skipif(not HAS_OPENAI, reason="openai SDK not installed")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_openai_response(content="Hello!", tool_calls=None, prompt_tokens=10, completion_tokens=20):
    """Create a mock OpenAI-compatible response object."""
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls

    choice = MagicMock()
    choice.message = message

    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


def _mock_tool_call(name="search", args='{"query": "test"}', call_id="call_123"):
    """Create a mock OpenAI-compatible tool call."""
    tc = MagicMock()
    tc.function.name = name
    tc.function.arguments = args
    tc.id = call_id
    return tc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def nvidia_profile():
    return ProviderProfile(
        name="nvidia",
        display_name="NVIDIA Nemotron",
        fast=LaneConfig(model="nvidia/nemotron-3-nano-30b-a3b", max_tokens=8192, temperature=0.3),
        deep=LaneConfig(model="nvidia/nemotron-3-super-120b-a12b", max_tokens=16384, temperature=0.7),
        cache=LaneConfig(model="nvidia/nemotron-nano-9b-v2", max_tokens=2048, temperature=0.1),
    )


@pytest.fixture
def nvidia_client():
    """Create NvidiaProviderClient with mocked OpenAI SDK."""
    if not HAS_OPENAI:
        pytest.skip("openai SDK not installed")

    with patch("providers.nvidia_client.openai") as mock_openai:
        mock_sdk_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_sdk_client
        from providers.nvidia_client import NvidiaProviderClient
        client = NvidiaProviderClient(api_key="nvapi-test")
        yield client, mock_sdk_client


# ===================================================================
# Config — models.yaml integration (no SDK needed)
# ===================================================================

class TestNvidiaConfig:

    def test_nvidia_in_real_models_yaml(self):
        """Verify NVIDIA provider is in the actual models.yaml."""
        models_path = pathlib.Path(__file__).resolve().parent.parent / "config" / "models.yaml"
        with open(models_path) as f:
            data = yaml.safe_load(f)
        assert "nvidia" in data["providers"]
        nvidia = data["providers"]["nvidia"]
        assert nvidia["display_name"] == "NVIDIA Nemotron"
        assert "nemotron" in nvidia["fast"]["model"]
        assert "nemotron" in nvidia["deep"]["model"]
        assert "nemotron" in nvidia["cache"]["model"]

    def test_profile_registry_loads_nvidia(self):
        """Verify ProfileRegistry can load NVIDIA from real config."""
        repo_root = pathlib.Path(__file__).resolve().parent.parent
        models_path = str(repo_root / "config" / "models.yaml")
        router_path = str(repo_root / "config" / "router.yaml")
        reg = ProfileRegistry(models_path=models_path, router_path=router_path)
        assert reg.has_provider("nvidia")
        profile = reg.get_profile("nvidia")
        assert profile.display_name == "NVIDIA Nemotron"
        assert profile.fast.model == "nvidia/nemotron-3-nano-30b-a3b"
        assert profile.deep.model == "nvidia/nemotron-3-super-120b-a12b"
        assert profile.cache is not None
        assert profile.cache.model == "nvidia/nemotron-nano-9b-v2"

    def test_nvidia_fast_lane_config(self):
        repo_root = pathlib.Path(__file__).resolve().parent.parent
        reg = ProfileRegistry(
            models_path=str(repo_root / "config" / "models.yaml"),
            router_path=str(repo_root / "config" / "router.yaml"),
        )
        profile = reg.get_profile("nvidia")
        assert profile.fast.max_tokens == 8192
        assert profile.fast.temperature == 0.3

    def test_nvidia_deep_lane_config(self):
        repo_root = pathlib.Path(__file__).resolve().parent.parent
        reg = ProfileRegistry(
            models_path=str(repo_root / "config" / "models.yaml"),
            router_path=str(repo_root / "config" / "router.yaml"),
        )
        profile = reg.get_profile("nvidia")
        assert profile.deep.max_tokens == 16384
        assert profile.deep.temperature == 0.7


# ===================================================================
# Usage tracker cost rates (no SDK needed)
# ===================================================================

class TestNvidiaCostRates:

    def test_nemotron_nano_in_cost_rates(self):
        assert "nvidia/nemotron-3-nano-30b-a3b" in _FALLBACK_COST_PER_1K

    def test_nemotron_super_in_cost_rates(self):
        assert "nvidia/nemotron-3-super-120b-a12b" in _FALLBACK_COST_PER_1K

    def test_nemotron_nano_9b_in_cost_rates(self):
        assert "nvidia/nemotron-nano-9b-v2" in _FALLBACK_COST_PER_1K

    def test_nemotron_super_49b_in_cost_rates(self):
        assert "nvidia/llama-3.3-nemotron-super-49b-v1" in _FALLBACK_COST_PER_1K

    def test_nemotron_70b_in_cost_rates(self):
        assert "nvidia/llama-3.1-nemotron-70b-instruct" in _FALLBACK_COST_PER_1K

    def test_cost_rates_are_reasonable(self):
        """Nemotron models should be cheaper than flagship OpenAI/Anthropic."""
        nano_cost = _FALLBACK_COST_PER_1K["nvidia/nemotron-3-nano-30b-a3b"]
        gpt4o_cost = _FALLBACK_COST_PER_1K["gpt-4o"]
        assert nano_cost < gpt4o_cost


# ===================================================================
# Factory registration (no SDK needed for checking API_KEY_VARS)
# ===================================================================

class TestNvidiaFactory:

    def test_nvidia_in_api_key_vars(self):
        from src.core.providers.factory import API_KEY_VARS
        assert "nvidia" in API_KEY_VARS
        assert API_KEY_VARS["nvidia"] == "NVIDIA_API_KEY"

    @needs_openai
    def test_factory_creates_nvidia_client(self):
        with patch("providers.nvidia_client.openai") as mock_openai:
            mock_openai.OpenAI.return_value = MagicMock()
            from src.core.providers.factory import create_provider
            client = create_provider("nvidia", "nvapi-test")
            assert client.provider_name == "nvidia"

    def test_factory_unknown_still_raises(self):
        from src.core.providers.factory import create_provider
        with pytest.raises(ValueError, match="Unknown provider"):
            create_provider("deepseek", "key")


# ===================================================================
# FlagshipClient — NVIDIA dispatch (mocked HTTP, no SDK needed)
# ===================================================================

class TestFlagshipNvidia:

    @patch("urllib.request.urlopen")
    def test_nvidia_complete(self, mock_open, nvidia_profile):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": "Hello from Nemotron!"}}],
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = mock_resp

        with patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-test"}):
            c = FlagshipClient("nvidia", nvidia_profile)
            result = c.complete("Hi", lane="fast")
            assert result == "Hello from Nemotron!"

    @patch("urllib.request.urlopen")
    def test_nvidia_sends_bearer_auth(self, mock_open, nvidia_profile):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": "out"}}],
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = mock_resp

        with patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-mykey"}):
            c = FlagshipClient("nvidia", nvidia_profile)
            c.complete("test", lane="fast")
            req = mock_open.call_args[0][0]
            assert "Bearer nvapi-mykey" in req.get_header("Authorization")

    @patch("urllib.request.urlopen")
    def test_nvidia_uses_nim_url(self, mock_open, nvidia_profile):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": "out"}}],
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = mock_resp

        with patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-test"}):
            c = FlagshipClient("nvidia", nvidia_profile)
            c.complete("test", lane="deep")
            req = mock_open.call_args[0][0]
            assert "integrate.api.nvidia.com" in req.full_url

    @patch("urllib.request.urlopen")
    def test_nvidia_sends_correct_model(self, mock_open, nvidia_profile):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": "out"}}],
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = mock_resp

        with patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-test"}):
            c = FlagshipClient("nvidia", nvidia_profile)
            c.complete("test", lane="deep")
            req = mock_open.call_args[0][0]
            body = json.loads(req.data)
            assert body["model"] == "nvidia/nemotron-3-super-120b-a12b"

    @patch("urllib.request.urlopen")
    def test_nvidia_cache_lane(self, mock_open, nvidia_profile):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": "cached"}}],
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = mock_resp

        with patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-test"}):
            c = FlagshipClient("nvidia", nvidia_profile)
            result = c.complete("test", lane="cache")
            req = mock_open.call_args[0][0]
            body = json.loads(req.data)
            assert body["model"] == "nvidia/nemotron-nano-9b-v2"
            assert result == "cached"

    def test_nvidia_not_configured_raises(self, nvidia_profile):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("NVIDIA_API_KEY", None)
            c = FlagshipClient("nvidia", nvidia_profile)
            assert c.is_configured() is False
            with pytest.raises(FlagshipError, match="API key not configured"):
                c.complete("hello")


# ===================================================================
# NvidiaProviderClient — SDK-based tests (require openai)
# ===================================================================

@needs_openai
class TestNvidiaClientInit:

    def test_provider_name(self, nvidia_client):
        client, _ = nvidia_client
        assert client.provider_name == "nvidia"

    def test_inherits_provider_client(self, nvidia_client):
        from src.core.providers.base import ProviderClient
        client, _ = nvidia_client
        assert isinstance(client, ProviderClient)


@needs_openai
class TestNvidiaGenerate:

    def test_basic_generate(self, nvidia_client):
        client, mock_sdk = nvidia_client
        mock_sdk.chat.completions.create.return_value = _mock_openai_response("Paris")
        result = client.generate(
            model="nvidia/nemotron-3-nano-30b-a3b",
            messages=[{"role": "user", "content": "Capital of France?"}],
        )
        assert result.text == "Paris"
        assert result.usage["input_tokens"] == 10
        assert result.usage["output_tokens"] == 20

    def test_generate_with_system_instruction(self, nvidia_client):
        client, mock_sdk = nvidia_client
        mock_sdk.chat.completions.create.return_value = _mock_openai_response("42")
        client.generate(
            model="nvidia/nemotron-3-nano-30b-a3b",
            messages=[{"role": "user", "content": "Answer?"}],
            system_instruction="You are a math tutor.",
        )
        call_args = mock_sdk.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are a math tutor."
        assert messages[1]["role"] == "user"

    def test_generate_returns_generate_result(self, nvidia_client):
        client, mock_sdk = nvidia_client
        mock_sdk.chat.completions.create.return_value = _mock_openai_response("ok")
        from src.core.providers.base import GenerateResult
        result = client.generate("model", [{"role": "user", "content": "hi"}])
        assert isinstance(result, GenerateResult)
        assert not result.has_tool_calls


@needs_openai
class TestNvidiaGenerateWithTools:

    def test_tool_calls_parsed(self, nvidia_client):
        client, mock_sdk = nvidia_client
        tc = _mock_tool_call(name="web_search", args='{"query": "weather"}', call_id="tc_1")
        mock_sdk.chat.completions.create.return_value = _mock_openai_response(
            content=None, tool_calls=[tc],
        )
        from src.core.providers.tool_schema import NormalizedToolDeclaration
        result = client.generate_with_tools(
            model="nvidia/nemotron-3-nano-30b-a3b",
            messages=[{"role": "user", "content": "What's the weather?"}],
            system_instruction="",
            tools=[NormalizedToolDeclaration(
                name="web_search",
                description="Search the web",
                parameters={"type": "object", "properties": {"query": {"type": "string"}}},
            )],
        )
        assert result.has_tool_calls
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "web_search"
        assert result.tool_calls[0].args == {"query": "weather"}
        assert result.tool_calls[0].id == "tc_1"

    def test_tool_config_any_mode(self, nvidia_client):
        client, mock_sdk = nvidia_client
        mock_sdk.chat.completions.create.return_value = _mock_openai_response("ok")
        client.generate_with_tools(
            model="nvidia/nemotron-3-nano-30b-a3b",
            messages=[{"role": "user", "content": "test"}],
            system_instruction="",
            tools=[],
            tool_config={"mode": "ANY"},
        )
        call_kwargs = mock_sdk.chat.completions.create.call_args.kwargs
        assert call_kwargs.get("tool_choice") == "required"

    def test_tool_config_none_mode(self, nvidia_client):
        client, mock_sdk = nvidia_client
        mock_sdk.chat.completions.create.return_value = _mock_openai_response("ok")
        client.generate_with_tools(
            model="nvidia/nemotron-3-nano-30b-a3b",
            messages=[{"role": "user", "content": "test"}],
            system_instruction="",
            tools=[],
            tool_config={"mode": "NONE"},
        )
        call_kwargs = mock_sdk.chat.completions.create.call_args.kwargs
        assert call_kwargs.get("tool_choice") == "none"

    def test_malformed_tool_args_fallback(self, nvidia_client):
        client, mock_sdk = nvidia_client
        tc = _mock_tool_call(name="fn", args="not-json", call_id="tc_2")
        mock_sdk.chat.completions.create.return_value = _mock_openai_response(
            content=None, tool_calls=[tc],
        )
        result = client.generate_with_tools(
            model="m", messages=[], system_instruction="", tools=[],
        )
        assert result.tool_calls[0].args == {"raw": "not-json"}


@needs_openai
class TestNvidiaMessageBuilders:

    def test_build_user_message_text(self, nvidia_client):
        client, _ = nvidia_client
        msg = client.build_user_message("Hello")
        assert msg == {"role": "user", "content": "Hello"}

    def test_build_user_message_with_images(self, nvidia_client):
        client, _ = nvidia_client
        msg = client.build_user_message("Describe", images=[(b"\x89PNG", "image/png")])
        assert msg["role"] == "user"
        assert isinstance(msg["content"], list)
        assert msg["content"][0]["type"] == "image_url"
        assert msg["content"][1] == {"type": "text", "text": "Describe"}

    def test_build_tool_response_message(self, nvidia_client):
        client, _ = nvidia_client
        msgs = client.build_tool_response_message([
            ("call_1", "search", '{"result": "found"}'),
            ("call_2", "calc", '{"answer": 42}'),
        ])
        assert len(msgs) == 2
        assert msgs[0]["role"] == "tool"
        assert msgs[0]["tool_call_id"] == "call_1"
        assert msgs[1]["tool_call_id"] == "call_2"


@needs_openai
class TestNvidiaModelDiscovery:

    def test_list_models_from_api(self, nvidia_client):
        client, mock_sdk = nvidia_client
        model1 = MagicMock()
        model1.id = "nvidia/nemotron-3-nano-30b-a3b"
        model2 = MagicMock()
        model2.id = "nvidia/nemotron-3-super-120b-a12b"
        model3 = MagicMock()
        model3.id = "meta/llama-3.1-8b"  # non-nvidia, should be filtered
        mock_sdk.models.list.return_value = [model1, model2, model3]

        models = client.list_models()
        assert len(models) == 2
        assert all(m.id.startswith("nvidia/") for m in models)

    def test_list_models_tier_assignment(self, nvidia_client):
        client, mock_sdk = nvidia_client
        nano = MagicMock()
        nano.id = "nvidia/nemotron-3-nano-30b-a3b"
        super_m = MagicMock()
        super_m.id = "nvidia/nemotron-3-super-120b-a12b"
        mock_sdk.models.list.return_value = [nano, super_m]

        models = client.list_models()
        tiers = {m.id: m.capability_tier for m in models}
        assert tiers["nvidia/nemotron-3-nano-30b-a3b"] == "fast"
        assert tiers["nvidia/nemotron-3-super-120b-a12b"] == "deep"

    def test_list_models_fallback_on_error(self, nvidia_client):
        client, mock_sdk = nvidia_client
        mock_sdk.models.list.side_effect = Exception("connection refused")
        from providers.nvidia_client import _KNOWN_MODELS
        models = client.list_models()
        assert len(models) == len(_KNOWN_MODELS)

    def test_validate_model_success(self, nvidia_client):
        client, mock_sdk = nvidia_client
        mock_sdk.models.retrieve.return_value = MagicMock()
        assert client.validate_model("nvidia/nemotron-3-nano-30b-a3b") is True

    def test_validate_model_failure(self, nvidia_client):
        client, mock_sdk = nvidia_client
        mock_sdk.models.retrieve.side_effect = Exception("not found")
        assert client.validate_model("nvidia/nonexistent") is False


@needs_openai
class TestNvidiaErrors:

    def test_auth_error_raises_provider_auth_error(self, nvidia_client):
        client, mock_sdk = nvidia_client
        from src.core.providers.base import ProviderAuthError
        mock_sdk.chat.completions.create.side_effect = Exception("401 Unauthorized")
        with pytest.raises(ProviderAuthError) as exc_info:
            client.generate("m", [{"role": "user", "content": "hi"}])
        assert exc_info.value.provider == "nvidia"

    def test_retryable_error_retries(self, nvidia_client):
        client, mock_sdk = nvidia_client
        mock_sdk.chat.completions.create.side_effect = [
            Exception("429 rate_limit"),
            _mock_openai_response("ok"),
        ]
        result = client.generate("m", [{"role": "user", "content": "hi"}])
        assert result.text == "ok"
        assert mock_sdk.chat.completions.create.call_count == 2

    def test_non_retryable_error_raises_immediately(self, nvidia_client):
        client, mock_sdk = nvidia_client
        mock_sdk.chat.completions.create.side_effect = Exception("invalid model")
        with pytest.raises(Exception, match="invalid model"):
            client.generate("m", [{"role": "user", "content": "hi"}])
        assert mock_sdk.chat.completions.create.call_count == 1


# ===================================================================
# Integration — real NVIDIA API tests (env-gated)
# ===================================================================

@pytest.mark.integration
@needs_openai
class TestRealNvidiaAPI:
    """Real NVIDIA NIM API tests — only run with NVIDIA_API_KEY."""

    @pytest.fixture(autouse=True)
    def _check_key(self):
        if not os.environ.get("NVIDIA_API_KEY"):
            pytest.skip("NVIDIA_API_KEY not set")

    @pytest.fixture
    def client(self):
        from providers.nvidia_client import NvidiaProviderClient
        return NvidiaProviderClient(api_key=os.environ["NVIDIA_API_KEY"])

    @pytest.fixture
    def flagship(self, nvidia_profile):
        return FlagshipClient("nvidia", nvidia_profile)

    def test_generate_basic(self, client):
        result = client.generate(
            model="nvidia/nemotron-3-nano-30b-a3b",
            messages=[{"role": "user", "content": "What is 2+2? Answer with just the number."}],
        )
        assert result.text is not None
        assert len(result.text) > 0

    def test_list_models(self, client):
        models = client.list_models()
        assert len(models) > 0

    def test_flagship_fast_lane(self, flagship):
        result = flagship.complete("What is 2+2? Answer with just the number.", lane="fast")
        assert len(result) > 0
