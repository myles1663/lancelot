import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orchestrator_provider import (
    get_deep_model,
    route_model,
    set_lane_model,
    switch_provider,
)


def _attach_provider_runtime_methods(runtime):
    runtime._deep_model_validation_cache = getattr(runtime, "_deep_model_validation_cache", {})

    def set_provider_runtime(provider, *, provider_name, provider_mode):
        runtime.provider = provider
        runtime._provider_name = provider_name
        runtime._provider_mode = provider_mode

    def set_provider_lane_configuration(
        *,
        fast_model=None,
        deep_model=None,
        cache_model=None,
        deep_thinking_config=None,
    ):
        if fast_model:
            runtime.model_name = fast_model
        if deep_model:
            runtime._deep_model_name = deep_model
        if cache_model:
            runtime._cache_model = cache_model
        if deep_thinking_config is not None:
            runtime._deep_thinking_config = deep_thinking_config

    def invalidate_deep_model_validation_cache():
        runtime._deep_model_validation_cache.clear()
        for attr in list(vars(runtime)):
            if attr.startswith("_deep_model_valid_"):
                delattr(runtime, attr)

    def set_model_lane(lane, model_id):
        if lane == "fast":
            runtime.model_name = model_id
        elif lane == "deep":
            runtime._deep_model_name = model_id
            invalidate_deep_model_validation_cache()
        elif lane == "cache":
            runtime._cache_model = model_id
            runtime._cache = None
        else:
            raise ValueError(f"Unknown lane: {lane}")

    runtime.set_provider_runtime = set_provider_runtime
    runtime.provider_stop_event = lambda: getattr(runtime, "_stop_event", None)
    runtime.set_provider_lane_configuration = set_provider_lane_configuration
    runtime.invalidate_deep_model_validation_cache = invalidate_deep_model_validation_cache
    runtime.clear_context_cache = lambda: setattr(runtime, "_cache", None)
    runtime.set_model_lane = set_model_lane
    runtime.deep_model_name = lambda: getattr(runtime, "_deep_model_name", "")
    runtime.cached_deep_model_validation = lambda model_id: runtime._deep_model_validation_cache.get(model_id)
    runtime.record_deep_model_validation = lambda model_id, valid: runtime._deep_model_validation_cache.__setitem__(model_id, bool(valid))
    return runtime


def test_switch_provider_applies_profile_and_invalidates_runtime_caches(monkeypatch):
    provider = SimpleNamespace(provider_name="openai")
    created = []

    def create_provider(provider_name, api_key, **kwargs):
        created.append((provider_name, api_key, kwargs))
        return provider

    class Registry:
        def has_provider(self, provider_name):
            return provider_name == "openai"

        def get_profile(self, provider_name):
            return SimpleNamespace(
                fast=SimpleNamespace(model="gpt-fast"),
                deep=SimpleNamespace(model="gpt-deep", thinking={"budget_tokens": 12000}),
                cache=SimpleNamespace(model="gpt-cache"),
            )

    runtime = _attach_provider_runtime_methods(SimpleNamespace(
        provider=None,
        model_name="old-fast",
        _cache=object(),
        _stop_event=object(),
        _deep_model_valid_old=True,
        get_anthropic_oauth_token=lambda: "",
        get_openai_codex_oauth_token=lambda: "",
        has_openai_codex_cli_auth=lambda: False,
    ))

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("LANCELOT_PROVIDER_MODE", "sdk")
    monkeypatch.setattr("providers.factory.create_provider", create_provider)
    monkeypatch.setitem(
        sys.modules,
        "provider_profile",
        types.SimpleNamespace(ProfileRegistry=Registry),
    )

    message = switch_provider(runtime, "openai")

    assert message == "Openai provider active (model: gpt-fast, mode: sdk)"
    assert runtime.provider is provider
    assert runtime._provider_name == "openai"
    assert runtime.model_name == "gpt-fast"
    assert runtime._deep_model_name == "gpt-deep"
    assert runtime._cache_model == "gpt-cache"
    assert runtime._cache is None
    assert not hasattr(runtime, "_deep_model_valid_old")
    assert created == [
        ("openai", "test-key", {"mode": "sdk", "auth_token": "", "stop_event": runtime._stop_event})
    ]


def test_switch_provider_requires_a_configured_credential(monkeypatch):
    runtime = _attach_provider_runtime_methods(SimpleNamespace(
        get_anthropic_oauth_token=lambda: "",
        get_openai_codex_oauth_token=lambda: "",
        has_openai_codex_cli_auth=lambda: False,
    ))

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="No API key or OAuth token"):
        switch_provider(runtime, "openai")


def test_set_lane_model_updates_lanes_and_clears_deep_validation_cache():
    runtime = _attach_provider_runtime_methods(SimpleNamespace(
        model_name="fast-old",
        _deep_model_name="deep-old",
        _cache_model="cache-old",
        _cache=object(),
        _deep_model_valid_deep_old=True,
    ))

    set_lane_model(runtime, "fast", "fast-new")
    set_lane_model(runtime, "deep", "deep-new")
    set_lane_model(runtime, "cache", "cache-new")

    assert runtime.model_name == "fast-new"
    assert runtime._deep_model_name == "deep-new"
    assert runtime._cache_model == "cache-new"
    assert runtime._cache is None
    assert not hasattr(runtime, "_deep_model_valid_deep_old")


def test_get_deep_model_validates_once_then_uses_cached_result():
    provider = MagicMock()
    provider.validate_model.return_value = True
    runtime = _attach_provider_runtime_methods(SimpleNamespace(
        model_name="fast-model",
        _deep_model_name="deep-model",
        provider=provider,
    ))

    assert get_deep_model(runtime) == "deep-model"
    assert get_deep_model(runtime) == "deep-model"
    provider.validate_model.assert_called_once_with("deep-model")


def test_route_model_uses_fast_for_trivial_and_deep_for_complex_requests():
    runtime = _attach_provider_runtime_methods(SimpleNamespace(
        model_name="fast-model",
        get_deep_model=lambda: "deep-model",
    ))

    assert route_model(runtime, "status") == "fast-model"
    assert route_model(runtime, "help me think through the best approach") == "deep-model"
