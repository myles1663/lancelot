"""
Tests for src.core.provider_profile — runtime configs & provider profiles.
Prompt 14: Runtime Configs & Provider Profiles.
"""

import os
import pathlib
import pytest
import yaml

from src.core.provider_profile import (
    ConfigError,
    LaneConfig,
    ProviderProfile,
    LocalConfig,
    RoutingLane,
    EscalationTrigger,
    ReceiptsConfig,
    ProfileRegistry,
    load_models_config,
    load_router_config,
)


# ---------------------------------------------------------------------------
# Paths to real config files
# ---------------------------------------------------------------------------
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_MODELS_PATH = _REPO_ROOT / "config" / "models.yaml"
_ROUTER_PATH = _REPO_ROOT / "config" / "router.yaml"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def models_yaml(tmp_path):
    """Write a minimal valid models.yaml and return its path."""
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
    """Write a minimal valid router.yaml and return its path."""
    data = {
        "version": "1.0",
        "routing_order": [
            {"lane": "local_redaction", "priority": 1, "description": "PII redaction"},
            {"lane": "local_utility", "priority": 2, "description": "Utility tasks"},
            {"lane": "flagship_fast", "priority": 3, "description": "Fast lane"},
            {"lane": "flagship_deep", "priority": 4, "description": "Deep lane"},
        ],
        "escalation": {
            "triggers": [
                {"type": "risk", "description": "High-risk actions"},
                {"type": "complexity", "description": "Planning tasks"},
            ],
        },
        "receipts": {
            "enabled": True,
            "include_rationale": True,
            "include_timing": True,
        },
        "local_utility_tasks": [
            "classify_intent", "extract_json", "summarize", "redact", "rag_rewrite",
        ],
    }
    p = tmp_path / "router.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return str(p)


@pytest.fixture
def registry(models_yaml, router_yaml):
    """ProfileRegistry loaded from temp config files."""
    return ProfileRegistry(models_path=models_yaml, router_path=router_yaml)


# ===================================================================
# Real config files validation
# ===================================================================

class TestRealConfigFiles:

    def test_models_yaml_exists(self):
        assert _MODELS_PATH.exists()

    def test_router_yaml_exists(self):
        assert _ROUTER_PATH.exists()

    def test_models_yaml_loads(self):
        data = load_models_config(str(_MODELS_PATH))
        assert "version" in data
        assert "local" in data
        assert "providers" in data

    def test_router_yaml_loads(self):
        data = load_router_config(str(_ROUTER_PATH))
        assert "version" in data
        assert "routing_order" in data

    def test_real_registry_loads(self):
        reg = ProfileRegistry(
            models_path=str(_MODELS_PATH),
            router_path=str(_ROUTER_PATH),
        )
        assert len(reg.provider_names) >= 3

    def test_real_config_has_three_providers(self):
        data = load_models_config(str(_MODELS_PATH))
        providers = data["providers"]
        assert "gemini" in providers
        assert "openai" in providers
        assert "anthropic" in providers

    def test_real_router_has_four_lanes(self):
        data = load_router_config(str(_ROUTER_PATH))
        assert len(data["routing_order"]) == 4

    def test_real_config_versions(self):
        reg = ProfileRegistry(
            models_path=str(_MODELS_PATH),
            router_path=str(_ROUTER_PATH),
        )
        assert reg.models_version == "1.0"
        assert reg.router_version == "1.0"


# ===================================================================
# load_models_config
# ===================================================================

class TestLoadModelsConfig:

    def test_loads_valid_file(self, models_yaml):
        data = load_models_config(models_yaml)
        assert data["version"] == "1.0"
        assert data["local"]["enabled"] is True

    def test_raises_on_missing_file(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            load_models_config(str(tmp_path / "nonexistent.yaml"))

    def test_raises_on_invalid_yaml(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("{{invalid yaml", encoding="utf-8")
        with pytest.raises(ConfigError, match="Invalid YAML"):
            load_models_config(str(p))

    def test_raises_on_non_mapping(self, tmp_path):
        p = tmp_path / "list.yaml"
        p.write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="must be a YAML mapping"):
            load_models_config(str(p))

    def test_raises_missing_version(self, tmp_path):
        p = tmp_path / "m.yaml"
        p.write_text(yaml.dump({
            "local": {"enabled": True, "url": "http://x"},
            "providers": {"g": {
                "display_name": "G", "fast": {"model": "m", "max_tokens": 1, "temperature": 0.1},
                "deep": {"model": "m", "max_tokens": 1, "temperature": 0.1},
            }},
        }), encoding="utf-8")
        with pytest.raises(ConfigError, match="missing 'version'"):
            load_models_config(str(p))

    def test_raises_missing_local(self, tmp_path):
        p = tmp_path / "m.yaml"
        p.write_text(yaml.dump({
            "version": "1.0",
            "providers": {"g": {
                "display_name": "G", "fast": {"model": "m", "max_tokens": 1, "temperature": 0.1},
                "deep": {"model": "m", "max_tokens": 1, "temperature": 0.1},
            }},
        }), encoding="utf-8")
        with pytest.raises(ConfigError, match="missing 'local'"):
            load_models_config(str(p))

    def test_raises_missing_providers(self, tmp_path):
        p = tmp_path / "m.yaml"
        p.write_text(yaml.dump({
            "version": "1.0",
            "local": {"enabled": True, "url": "http://x"},
        }), encoding="utf-8")
        with pytest.raises(ConfigError, match="missing 'providers'"):
            load_models_config(str(p))

    def test_raises_empty_providers(self, tmp_path):
        p = tmp_path / "m.yaml"
        p.write_text(yaml.dump({
            "version": "1.0",
            "local": {"enabled": True, "url": "http://x"},
            "providers": {},
        }), encoding="utf-8")
        with pytest.raises(ConfigError, match="non-empty mapping"):
            load_models_config(str(p))

    def test_raises_missing_fast_lane(self, tmp_path):
        p = tmp_path / "m.yaml"
        p.write_text(yaml.dump({
            "version": "1.0",
            "local": {"enabled": True, "url": "http://x"},
            "providers": {"g": {
                "display_name": "G",
                "deep": {"model": "m", "max_tokens": 1, "temperature": 0.1},
            }},
        }), encoding="utf-8")
        with pytest.raises(ConfigError, match="missing 'fast'"):
            load_models_config(str(p))

    def test_raises_missing_deep_lane(self, tmp_path):
        p = tmp_path / "m.yaml"
        p.write_text(yaml.dump({
            "version": "1.0",
            "local": {"enabled": True, "url": "http://x"},
            "providers": {"g": {
                "display_name": "G",
                "fast": {"model": "m", "max_tokens": 1, "temperature": 0.1},
            }},
        }), encoding="utf-8")
        with pytest.raises(ConfigError, match="missing 'deep'"):
            load_models_config(str(p))

    def test_raises_missing_lane_model(self, tmp_path):
        p = tmp_path / "m.yaml"
        p.write_text(yaml.dump({
            "version": "1.0",
            "local": {"enabled": True, "url": "http://x"},
            "providers": {"g": {
                "display_name": "G",
                "fast": {"max_tokens": 1, "temperature": 0.1},
                "deep": {"model": "m", "max_tokens": 1, "temperature": 0.1},
            }},
        }), encoding="utf-8")
        with pytest.raises(ConfigError, match="missing 'model'"):
            load_models_config(str(p))

    def test_raises_empty_model_name(self, tmp_path):
        p = tmp_path / "m.yaml"
        p.write_text(yaml.dump({
            "version": "1.0",
            "local": {"enabled": True, "url": "http://x"},
            "providers": {"g": {
                "display_name": "G",
                "fast": {"model": "", "max_tokens": 1, "temperature": 0.1},
                "deep": {"model": "m", "max_tokens": 1, "temperature": 0.1},
            }},
        }), encoding="utf-8")
        with pytest.raises(ConfigError, match="non-empty string"):
            load_models_config(str(p))

    def test_raises_invalid_max_tokens(self, tmp_path):
        p = tmp_path / "m.yaml"
        p.write_text(yaml.dump({
            "version": "1.0",
            "local": {"enabled": True, "url": "http://x"},
            "providers": {"g": {
                "display_name": "G",
                "fast": {"model": "m", "max_tokens": 0, "temperature": 0.1},
                "deep": {"model": "m", "max_tokens": 1, "temperature": 0.1},
            }},
        }), encoding="utf-8")
        with pytest.raises(ConfigError, match="positive integer"):
            load_models_config(str(p))


# ===================================================================
# load_router_config
# ===================================================================

class TestLoadRouterConfig:

    def test_loads_valid_file(self, router_yaml):
        data = load_router_config(router_yaml)
        assert data["version"] == "1.0"
        assert len(data["routing_order"]) == 4

    def test_raises_on_missing_file(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            load_router_config(str(tmp_path / "nonexistent.yaml"))

    def test_raises_on_invalid_yaml(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("{{invalid", encoding="utf-8")
        with pytest.raises(ConfigError, match="Invalid YAML"):
            load_router_config(str(p))

    def test_raises_missing_version(self, tmp_path):
        p = tmp_path / "r.yaml"
        p.write_text(yaml.dump({
            "routing_order": [{"lane": "x", "priority": 1, "description": "d"}],
        }), encoding="utf-8")
        with pytest.raises(ConfigError, match="missing 'version'"):
            load_router_config(str(p))

    def test_raises_missing_routing_order(self, tmp_path):
        p = tmp_path / "r.yaml"
        p.write_text(yaml.dump({"version": "1.0"}), encoding="utf-8")
        with pytest.raises(ConfigError, match="missing 'routing_order'"):
            load_router_config(str(p))

    def test_raises_empty_routing_order(self, tmp_path):
        p = tmp_path / "r.yaml"
        p.write_text(yaml.dump({"version": "1.0", "routing_order": []}), encoding="utf-8")
        with pytest.raises(ConfigError, match="non-empty list"):
            load_router_config(str(p))

    def test_raises_missing_lane_field(self, tmp_path):
        p = tmp_path / "r.yaml"
        p.write_text(yaml.dump({
            "version": "1.0",
            "routing_order": [{"priority": 1, "description": "d"}],
        }), encoding="utf-8")
        with pytest.raises(ConfigError, match="missing 'lane'"):
            load_router_config(str(p))

    def test_raises_missing_escalation_triggers(self, tmp_path):
        p = tmp_path / "r.yaml"
        p.write_text(yaml.dump({
            "version": "1.0",
            "routing_order": [{"lane": "x", "priority": 1, "description": "d"}],
            "escalation": {},
        }), encoding="utf-8")
        with pytest.raises(ConfigError, match="missing 'triggers'"):
            load_router_config(str(p))

    def test_raises_missing_trigger_type(self, tmp_path):
        p = tmp_path / "r.yaml"
        p.write_text(yaml.dump({
            "version": "1.0",
            "routing_order": [{"lane": "x", "priority": 1, "description": "d"}],
            "escalation": {"triggers": [{"description": "no type"}]},
        }), encoding="utf-8")
        with pytest.raises(ConfigError, match="missing 'type'"):
            load_router_config(str(p))


# ===================================================================
# Data classes
# ===================================================================

class TestDataClasses:

    def test_lane_config_frozen(self):
        lc = LaneConfig(model="gpt-4", max_tokens=4096, temperature=0.7)
        with pytest.raises(AttributeError):
            lc.model = "other"

    def test_provider_profile_frozen(self):
        fast = LaneConfig(model="f", max_tokens=1, temperature=0.1)
        deep = LaneConfig(model="d", max_tokens=1, temperature=0.1)
        pp = ProviderProfile(name="test", display_name="Test", fast=fast, deep=deep)
        with pytest.raises(AttributeError):
            pp.name = "other"

    def test_provider_profile_optional_cache(self):
        fast = LaneConfig(model="f", max_tokens=1, temperature=0.1)
        deep = LaneConfig(model="d", max_tokens=1, temperature=0.1)
        pp = ProviderProfile(name="t", display_name="T", fast=fast, deep=deep)
        assert pp.cache is None

    def test_provider_profile_with_cache(self):
        fast = LaneConfig(model="f", max_tokens=1, temperature=0.1)
        deep = LaneConfig(model="d", max_tokens=1, temperature=0.1)
        cache = LaneConfig(model="c", max_tokens=1, temperature=0.1)
        pp = ProviderProfile(name="t", display_name="T", fast=fast, deep=deep, cache=cache)
        assert pp.cache.model == "c"

    def test_local_config_frozen(self):
        lc = LocalConfig(enabled=True, url="http://x")
        with pytest.raises(AttributeError):
            lc.enabled = False

    def test_routing_lane_default_enabled(self):
        rl = RoutingLane(lane="test", priority=1, description="d")
        assert rl.enabled is True

    def test_escalation_trigger(self):
        et = EscalationTrigger(type="risk", description="high risk")
        assert et.type == "risk"

    def test_receipts_config_defaults(self):
        rc = ReceiptsConfig()
        assert rc.enabled is True
        assert rc.include_rationale is True
        assert rc.include_timing is True


# ===================================================================
# ProfileRegistry — construction & versions
# ===================================================================

class TestRegistryConstruction:

    def test_loads_successfully(self, registry):
        assert registry is not None

    def test_models_version(self, registry):
        assert registry.models_version == "1.0"

    def test_router_version(self, registry):
        assert registry.router_version == "1.0"

    def test_raises_on_missing_models(self, tmp_path, router_yaml):
        with pytest.raises(ConfigError):
            ProfileRegistry(
                models_path=str(tmp_path / "missing.yaml"),
                router_path=router_yaml,
            )

    def test_raises_on_missing_router(self, tmp_path, models_yaml):
        with pytest.raises(ConfigError):
            ProfileRegistry(
                models_path=models_yaml,
                router_path=str(tmp_path / "missing.yaml"),
            )


# ===================================================================
# ProfileRegistry — local config
# ===================================================================

class TestRegistryLocal:

    def test_local_config_present(self, registry):
        assert registry.local is not None

    def test_local_enabled(self, registry):
        assert registry.local.enabled is True

    def test_local_url(self, registry):
        assert registry.local.url == "http://local-llm:8080"

    def test_local_utility_tasks(self, registry):
        tasks = registry.local_utility_tasks
        assert "classify_intent" in tasks
        assert "extract_json" in tasks
        assert "summarize" in tasks
        assert "redact" in tasks
        assert "rag_rewrite" in tasks

    def test_is_local_task_true(self, registry):
        assert registry.is_local_task("classify_intent") is True
        assert registry.is_local_task("redact") is True

    def test_is_local_task_false(self, registry):
        assert registry.is_local_task("conversation") is False
        assert registry.is_local_task("plan") is False


# ===================================================================
# ProfileRegistry — provider lookup
# ===================================================================

class TestRegistryProviders:

    def test_provider_names(self, registry):
        assert "gemini" in registry.provider_names

    def test_has_provider_true(self, registry):
        assert registry.has_provider("gemini") is True

    def test_has_provider_false(self, registry):
        assert registry.has_provider("nonexistent") is False

    def test_get_profile(self, registry):
        profile = registry.get_profile("gemini")
        assert isinstance(profile, ProviderProfile)
        assert profile.name == "gemini"
        assert profile.display_name == "Google Gemini"

    def test_get_profile_fast_lane(self, registry):
        profile = registry.get_profile("gemini")
        assert profile.fast.model == "gemini-flash"
        assert profile.fast.max_tokens == 4096
        assert profile.fast.temperature == 0.3

    def test_get_profile_deep_lane(self, registry):
        profile = registry.get_profile("gemini")
        assert profile.deep.model == "gemini-pro"
        assert profile.deep.max_tokens == 8192

    def test_get_profile_no_cache(self, registry):
        # Minimal fixture doesn't include cache lane
        profile = registry.get_profile("gemini")
        assert profile.cache is None

    def test_get_profile_unknown_raises(self, registry):
        with pytest.raises(ConfigError, match="Unknown provider"):
            registry.get_profile("nonexistent")

    def test_get_profile_error_lists_available(self, registry):
        with pytest.raises(ConfigError, match="gemini"):
            registry.get_profile("badname")


class TestRegistryWithCache:
    """Test provider profile with a cache lane."""

    @pytest.fixture
    def registry_with_cache(self, tmp_path, router_yaml):
        data = {
            "version": "1.0",
            "local": {"enabled": True, "url": "http://local-llm:8080"},
            "providers": {
                "openai": {
                    "display_name": "OpenAI",
                    "fast": {"model": "gpt-4o-mini", "max_tokens": 4096, "temperature": 0.3},
                    "deep": {"model": "gpt-4o", "max_tokens": 8192, "temperature": 0.7},
                    "cache": {"model": "gpt-4o-mini", "max_tokens": 2048, "temperature": 0.1},
                },
            },
        }
        p = tmp_path / "models.yaml"
        p.write_text(yaml.dump(data), encoding="utf-8")
        return ProfileRegistry(models_path=str(p), router_path=router_yaml)

    def test_cache_lane_loaded(self, registry_with_cache):
        profile = registry_with_cache.get_profile("openai")
        assert profile.cache is not None
        assert profile.cache.model == "gpt-4o-mini"
        assert profile.cache.max_tokens == 2048
        assert profile.cache.temperature == 0.1


# ===================================================================
# ProfileRegistry — routing order
# ===================================================================

class TestRegistryRouting:

    def test_routing_order_count(self, registry):
        assert len(registry.routing_order) == 4

    def test_routing_order_sorted_by_priority(self, registry):
        lanes = registry.routing_order
        priorities = [l.priority for l in lanes]
        assert priorities == sorted(priorities)

    def test_first_lane_is_local_redaction(self, registry):
        first = registry.routing_order[0]
        assert first.lane == "local_redaction"
        assert first.priority == 1

    def test_last_lane_is_flagship_deep(self, registry):
        last = registry.routing_order[-1]
        assert last.lane == "flagship_deep"
        assert last.priority == 4

    def test_all_lanes_enabled(self, registry):
        assert len(registry.enabled_lanes) == 4

    def test_enabled_lanes_filters_disabled(self, tmp_path, models_yaml):
        data = {
            "version": "1.0",
            "routing_order": [
                {"lane": "local_redaction", "priority": 1, "description": "d", "enabled": True},
                {"lane": "local_utility", "priority": 2, "description": "d", "enabled": False},
                {"lane": "flagship_fast", "priority": 3, "description": "d", "enabled": True},
            ],
        }
        p = tmp_path / "router.yaml"
        p.write_text(yaml.dump(data), encoding="utf-8")
        reg = ProfileRegistry(models_path=models_yaml, router_path=str(p))
        assert len(reg.routing_order) == 3
        assert len(reg.enabled_lanes) == 2
        disabled = [l for l in reg.routing_order if not l.enabled]
        assert len(disabled) == 1
        assert disabled[0].lane == "local_utility"


# ===================================================================
# ProfileRegistry — escalation
# ===================================================================

class TestRegistryEscalation:

    def test_escalation_triggers_loaded(self, registry):
        triggers = registry.escalation_triggers
        assert len(triggers) == 2

    def test_escalation_trigger_types(self, registry):
        types = [t.type for t in registry.escalation_triggers]
        assert "risk" in types
        assert "complexity" in types

    def test_no_escalation_section(self, tmp_path, models_yaml):
        data = {
            "version": "1.0",
            "routing_order": [
                {"lane": "x", "priority": 1, "description": "d"},
            ],
        }
        p = tmp_path / "router.yaml"
        p.write_text(yaml.dump(data), encoding="utf-8")
        reg = ProfileRegistry(models_path=models_yaml, router_path=str(p))
        assert len(reg.escalation_triggers) == 0


# ===================================================================
# ProfileRegistry — receipts
# ===================================================================

class TestRegistryReceipts:

    def test_receipts_config_loaded(self, registry):
        rc = registry.receipts_config
        assert rc.enabled is True
        assert rc.include_rationale is True
        assert rc.include_timing is True

    def test_receipts_defaults_when_missing(self, tmp_path, models_yaml):
        data = {
            "version": "1.0",
            "routing_order": [
                {"lane": "x", "priority": 1, "description": "d"},
            ],
        }
        p = tmp_path / "router.yaml"
        p.write_text(yaml.dump(data), encoding="utf-8")
        reg = ProfileRegistry(models_path=models_yaml, router_path=str(p))
        assert reg.receipts_config.enabled is True


# ===================================================================
# Multiple providers
# ===================================================================

class TestMultipleProviders:

    @pytest.fixture
    def multi_registry(self, tmp_path, router_yaml):
        data = {
            "version": "1.0",
            "local": {"enabled": True, "url": "http://local-llm:8080"},
            "providers": {
                "gemini": {
                    "display_name": "Google Gemini",
                    "fast": {"model": "gemini-flash", "max_tokens": 4096, "temperature": 0.3},
                    "deep": {"model": "gemini-pro", "max_tokens": 8192, "temperature": 0.7},
                },
                "openai": {
                    "display_name": "OpenAI",
                    "fast": {"model": "gpt-4o-mini", "max_tokens": 4096, "temperature": 0.3},
                    "deep": {"model": "gpt-4o", "max_tokens": 8192, "temperature": 0.7},
                },
                "anthropic": {
                    "display_name": "Anthropic",
                    "fast": {"model": "haiku", "max_tokens": 4096, "temperature": 0.3},
                    "deep": {"model": "sonnet", "max_tokens": 8192, "temperature": 0.7},
                },
            },
        }
        p = tmp_path / "models.yaml"
        p.write_text(yaml.dump(data), encoding="utf-8")
        return ProfileRegistry(models_path=str(p), router_path=router_yaml)

    def test_three_providers_registered(self, multi_registry):
        assert len(multi_registry.provider_names) == 3

    def test_each_provider_accessible(self, multi_registry):
        for name in ("gemini", "openai", "anthropic"):
            profile = multi_registry.get_profile(name)
            assert profile.name == name

    def test_providers_have_distinct_models(self, multi_registry):
        models = set()
        for name in multi_registry.provider_names:
            p = multi_registry.get_profile(name)
            models.add(p.fast.model)
            models.add(p.deep.model)
        assert len(models) == 6  # 3 fast + 3 deep, all distinct
