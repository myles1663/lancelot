import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import api_auth, auth_api
from src.core.operator_identity import OperatorIdentity
from src.core.providers import api as providers_api

import model_discovery
import providers.factory as provider_factory


def _insert_session(token: str, capabilities: set[str]) -> None:
    auth_api._sessions[token] = {
        "expires_at": 9999999999,
        "username": "Arthur",
        "operator_identity": OperatorIdentity(
            operator_id="op-arthur",
            display_name="Arthur",
            session_id="session-1",
            session_started_at="2026-04-19T00:00:00Z",
            auth_method="local",
            ip_address="127.0.0.1",
        ),
        "capabilities": sorted(capabilities),
        "groups": [],
    }


def _client() -> TestClient:
    auth_api._sessions.clear()
    _insert_session("provider-admin", {"warroom.login", "provider.admin"})
    api_auth.init_api_auth(lambda request: True)
    app = FastAPI()
    app.include_router(providers_api.router)
    client = TestClient(app)
    client.cookies.set(auth_api.get_warroom_session_cookie_name(), "provider-admin")
    return client


class _FakeDiscovery:
    def __init__(
        self,
        *,
        provider_name: str = "gemini",
        discovered_models=None,
        lane_assignments=None,
        stack=None,
    ):
        self.provider_name = provider_name
        self.discovered_models = discovered_models or []
        self.lane_assignments = lane_assignments or {"fast": "model-fast", "deep": "model-deep"}
        self._stack = stack or {
            "provider": provider_name,
            "lanes": dict(self.lane_assignments),
            "discovered_models": [m.id for m in self.discovered_models],
            "models_count": len(self.discovered_models),
            "last_refresh": "2026-04-19T00:00:00Z",
        }
        self.refresh_calls = 0
        self.replaced = []
        self.overrides = []
        self.reset_calls = 0

    def get_stack(self):
        return dict(self._stack)

    def refresh(self):
        self.refresh_calls += 1

    def get_model_profile(self, model_id):
        return {"id": model_id, "lane_hint": "fast"}

    def replace_provider(self, new_provider, lane_overrides=None):
        self.replaced.append((new_provider, lane_overrides))
        self.provider_name = getattr(new_provider, "provider_name", self.provider_name)

    def set_lane_override(self, lane, model_id):
        self.overrides.append((lane, model_id))
        self.lane_assignments[lane] = model_id

    def reset_overrides(self):
        self.reset_calls += 1


class _FakeOrchestrator:
    def __init__(self):
        self.switch_calls = []
        self.lane_calls = []

    def switch_provider(self, provider_name):
        self.switch_calls.append(provider_name)
        return f"switched:{provider_name}"

    def set_lane_model(self, lane, model_id):
        self.lane_calls.append((lane, model_id))


class _FakeModel:
    def __init__(
        self,
        model_id: str,
        *,
        display_name: str = "Display",
        context_window: int = 8192,
        supports_tools: bool = True,
        capability_tier: str = "tier-1",
        input_cost_per_1k: float = 0.1,
        output_cost_per_1k: float = 0.2,
    ):
        self.id = model_id
        self.display_name = display_name
        self.context_window = context_window
        self.supports_tools = supports_tools
        self.capability_tier = capability_tier
        self.input_cost_per_1k = input_cost_per_1k
        self.output_cost_per_1k = output_cost_per_1k


@pytest.fixture(autouse=True)
def _reset_provider_state(monkeypatch, tmp_path):
    auth_api._sessions.clear()
    providers_api._auth_errors.clear()
    providers_api.init_provider_api(None, None)
    monkeypatch.setattr(providers_api, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(providers_api, "_CONFIG_FILE", tmp_path / "provider_config.json")
    monkeypatch.setattr(providers_api, "_LEGACY_CONFIG_FILE", tmp_path / "legacy" / "provider_config.json")
    monkeypatch.setattr(providers_api, "_MODELS_YAML", str(tmp_path / "models.yaml"))
    yield
    auth_api._sessions.clear()
    providers_api._auth_errors.clear()
    providers_api.init_provider_api(None, None)


def test_stack_models_and_profiles_reflect_discovery_state(monkeypatch):
    discovery = _FakeDiscovery(
        provider_name="gemini",
        discovered_models=[_FakeModel("gemini-fast", display_name="Gemini Fast")],
    )
    providers_api.init_provider_api(discovery)
    client = _client()

    stack = client.get("/api/v1/providers/stack")
    assert stack.status_code == 200
    assert stack.json()["status"] == "no_key"

    monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret")
    stack = client.get("/api/v1/providers/stack")
    assert stack.status_code == 200
    assert stack.json()["status"] == "connected"

    providers_api.report_auth_error("gemini", "Bad key")
    stack = client.get("/api/v1/providers/stack")
    assert stack.status_code == 200
    assert stack.json()["status"] == "auth_error"
    assert stack.json()["status_detail"] == "Bad key"

    models = client.get("/api/v1/providers/models")
    assert models.status_code == 200
    assert models.json() == {
        "provider": "gemini",
        "models": [
            {
                "id": "gemini-fast",
                "display_name": "Gemini Fast",
                "context_window": 8192,
                "supports_tools": True,
                "capability_tier": "tier-1",
                "cost_input_per_1k": 0.1,
                "cost_output_per_1k": 0.2,
            }
        ],
    }

    profiles = client.get("/api/v1/providers/profiles")
    assert profiles.status_code == 200
    assert profiles.json()["profiles"] == {
        "model-fast": {"id": "model-fast", "lane_hint": "fast"},
        "model-deep": {"id": "model-deep", "lane_hint": "fast"},
    }


def test_available_providers_and_keys_include_oauth_and_masking(monkeypatch):
    monkeypatch.setattr(
        provider_factory,
        "API_KEY_VARS",
        {
            "openai": "OPENAI_API_KEY",
            "openai-codex": "",
            "anthropic": "ANTHROPIC_API_KEY",
        },
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-live-123456")
    monkeypatch.setattr(
        providers_api,
        "_codex_oauth_status",
        lambda: {"configured": True, "status": "active"},
    )
    monkeypatch.setattr(
        providers_api,
        "_anthropic_oauth_status",
        lambda: {"configured": True, "status": "refreshing"},
    )
    providers_api.init_provider_api(_FakeDiscovery(provider_name="openai"))
    client = _client()

    available = client.get("/api/v1/providers/available")
    assert available.status_code == 200
    providers = {item["name"]: item for item in available.json()["providers"]}
    assert providers["openai"]["active"] is True
    assert providers["openai"]["has_key"] is True
    assert providers["openai-codex"]["has_key"] is True
    assert providers["anthropic"]["has_key"] is True

    keys = client.get("/api/v1/providers/keys")
    assert keys.status_code == 200
    entries = {item["provider"]: item for item in keys.json()["keys"]}
    assert entries["openai"]["key_preview"] == "····3456"
    assert entries["openai"]["active"] is True
    assert entries["openai-codex"]["oauth_only"] is True
    assert entries["openai-codex"]["oauth_configured"] is True
    assert entries["anthropic"]["oauth_status"] == "refreshing"


def test_refresh_and_switch_provider_handle_errors_and_initialize_discovery(monkeypatch):
    client = _client()
    receipt_calls = []
    monkeypatch.setattr(
        "src.core.governance_receipts.emit_governance_receipt",
        lambda request, action_type, action_name, inputs=None, outputs=None, **kwargs: receipt_calls.append(
            (action_type.value, action_name, inputs, outputs)
        ),
    )

    refresh = client.post("/api/v1/providers/refresh")
    assert refresh.status_code == 200
    assert refresh.json() == {"status": "error", "message": "No provider configured"}

    monkeypatch.setattr(provider_factory, "API_KEY_VARS", {"openai": "OPENAI_API_KEY"})
    providers_api.init_provider_api(None, _FakeOrchestrator())

    missing = client.post("/api/v1/providers/switch", json={"provider": "openai"})
    assert missing.status_code == 200
    assert missing.json()["message"] == "No API key or OAuth token configured for openai"

    orchestrator = _FakeOrchestrator()
    providers_api.init_provider_api(None, orchestrator)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setattr(providers_api, "_provider_profile_lane_defaults", lambda provider: {"fast": "gpt-fast"})
    created = []

    class _ModelDiscovery:
        def __init__(self, provider, lane_overrides=None, fallback_lanes=None):
            created.append((provider, lane_overrides, fallback_lanes))
            self.provider_name = "openai"
            self.lane_assignments = lane_overrides or {}
            self.discovered_models = []

        def refresh(self):
            created.append("refresh")

        def get_stack(self):
            return {"provider": "openai", "lanes": {"fast": "gpt-fast"}}

    monkeypatch.setattr(model_discovery, "ModelDiscovery", _ModelDiscovery)
    monkeypatch.setattr(
        provider_factory,
        "create_provider",
        lambda provider_name, api_key, auth_token="": SimpleNamespace(
            provider_name=provider_name,
            api_key=api_key,
            auth_token=auth_token,
        ),
    )

    switched = client.post("/api/v1/providers/switch", json={"provider": "openai"})

    assert switched.status_code == 200
    body = switched.json()
    assert body["status"] == "ok"
    assert body["message"] == "switched:openai"
    assert body["stack"]["provider"] == "openai"
    assert orchestrator.switch_calls == ["openai"]
    assert created[0][1] == {}
    assert created[0][2] == {"fast": "gpt-fast"}
    assert created[1] == "refresh"
    assert providers_api.load_persisted_config()["active_provider"] == "openai"
    assert receipt_calls[-1] == (
        "provider_switched",
        "switch_provider",
        {"provider": "openai"},
        {"provider": "openai", "status": "ok"},
    )


def test_local_openai_config_persists_and_enables_provider_switch(monkeypatch):
    client = _client()
    receipt_calls = []
    monkeypatch.setattr(
        "src.core.governance_receipts.emit_governance_receipt",
        lambda request, action_type, action_name, inputs=None, outputs=None, **kwargs: receipt_calls.append(
            (action_type.value, action_name, inputs, outputs)
        ),
    )
    monkeypatch.setattr(
        provider_factory,
        "API_KEY_VARS",
        {"local-openai": "LOCAL_OPENAI_API_KEY"},
    )
    persisted_env = []
    monkeypatch.setattr(
        providers_api,
        "_update_env_file",
        lambda env_var, value: persisted_env.append((env_var, value)) or True,
    )

    saved = client.post(
        "/api/v1/providers/local-openai/config",
        json={
            "base_url": "http://localhost:11434/v1",
            "api_key": "",
            "fast_model": "llama3.1:8b",
            "deep_model": "qwen3:32b",
            "cache_model": "llama3.1:8b",
            "context_window": 65536,
            "supports_tools": True,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["status"] == "ok"
    assert saved.json()["config"]["base_url"] == "http://localhost:11434/v1"
    assert ("LOCAL_OPENAI_BASE_URL", "http://localhost:11434/v1") in persisted_env
    assert receipt_calls[-1][0:2] == (
        "provider_local_config_updated",
        "save_local_openai_config",
    )
    assert receipt_calls[-1][2]["api_key_configured"] is False

    providers_api.init_provider_api(None, _FakeOrchestrator())
    created = []

    class _ModelDiscovery:
        def __init__(self, provider, lane_overrides=None, fallback_lanes=None):
            created.append((provider, lane_overrides, fallback_lanes))
            self.provider_name = "local-openai"
            self.lane_assignments = fallback_lanes or {}
            self.discovered_models = []

        def refresh(self):
            created.append("refresh")

        def get_stack(self):
            return {"provider": "local-openai", "lanes": dict(self.lane_assignments)}

    monkeypatch.setattr(model_discovery, "ModelDiscovery", _ModelDiscovery)
    monkeypatch.setattr(
        provider_factory,
        "create_provider",
        lambda provider_name, api_key, auth_token="": SimpleNamespace(
            provider_name=provider_name,
            api_key=api_key,
            auth_token=auth_token,
        ),
    )

    switched = client.post("/api/v1/providers/switch", json={"provider": "local-openai"})

    assert switched.status_code == 200
    assert switched.json()["status"] == "ok"
    assert created[0][2] == {
        "fast": "llama3.1:8b",
        "deep": "qwen3:32b",
        "cache": "llama3.1:8b",
    }
    assert providers_api.load_persisted_config()["active_provider"] == "local-openai"


def test_local_openai_config_reports_provider_config_persistence_failures(monkeypatch):
    client = _client()
    monkeypatch.setattr(
        providers_api,
        "_save_config",
        lambda data: (_ for _ in ()).throw(
            providers_api.ProviderConfigPersistenceError("durable store unavailable")
        ),
    )

    response = client.post(
        "/api/v1/providers/local-openai/config",
        json={
            "base_url": "http://localhost:11434/v1",
            "api_key": "",
            "fast_model": "llama3.1:8b",
            "deep_model": "qwen3:32b",
            "cache_model": "llama3.1:8b",
            "context_window": 65536,
            "supports_tools": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "error"
    assert "durable store unavailable" in response.json()["message"]


def test_override_and_reset_lanes_persist_and_sync_orchestrator(monkeypatch):
    discovery = _FakeDiscovery(
        provider_name="openai",
        discovered_models=[_FakeModel("model-fast"), _FakeModel("model-deep"), _FakeModel("model-cache")],
        lane_assignments={"fast": "model-fast", "deep": "model-deep"},
    )
    orchestrator = _FakeOrchestrator()
    providers_api.init_provider_api(discovery, orchestrator)
    client = _client()
    receipt_calls = []
    monkeypatch.setattr(
        "src.core.governance_receipts.emit_governance_receipt",
        lambda request, action_type, action_name, inputs=None, outputs=None, **kwargs: receipt_calls.append(
            (action_type.value, action_name, inputs, outputs)
        ),
    )

    invalid_lane = client.post("/api/v1/providers/lanes/override", json={"lane": "wide", "model_id": "model-fast"})
    assert invalid_lane.status_code == 200
    assert "Unknown lane" in invalid_lane.json()["message"]

    missing_model = client.post("/api/v1/providers/lanes/override", json={"lane": "fast", "model_id": "missing"})
    assert missing_model.status_code == 200
    assert "not found in discovered models" in missing_model.json()["message"]

    overridden = client.post("/api/v1/providers/lanes/override", json={"lane": "fast", "model_id": "model-cache"})
    assert overridden.status_code == 200
    assert overridden.json()["status"] == "ok"
    assert discovery.overrides == [("fast", "model-cache")]
    assert orchestrator.lane_calls == [("fast", "model-cache")]
    assert providers_api.load_persisted_config()["lane_overrides"] == {"fast": "model-cache"}
    assert receipt_calls[-1][0:3] == (
        "provider_lane_overridden",
        "override_provider_lane",
        {"lane": "fast", "model_id": "model-cache"},
    )

    reset = client.post("/api/v1/providers/lanes/reset")
    assert reset.status_code == 200
    assert reset.json()["status"] == "ok"
    assert discovery.reset_calls == 1
    assert orchestrator.lane_calls[-2:] == [("fast", "model-cache"), ("deep", "model-deep")]
    assert "lane_overrides" not in providers_api.load_persisted_config()
    assert receipt_calls[-1][0:3] == (
        "provider_lanes_reset",
        "reset_provider_lanes",
        {"cleared_overrides": True},
    )


def test_rotate_provider_key_validates_hotswaps_and_persists(monkeypatch):
    discovery = _FakeDiscovery(provider_name="openai")
    orchestrator = _FakeOrchestrator()
    providers_api.init_provider_api(discovery, orchestrator)
    providers_api.report_auth_error("openai", "old auth failure")
    providers_api._save_config({"lane_overrides": {"fast": "gpt-fast"}})
    client = _client()
    receipt_calls = []
    monkeypatch.setattr(
        "src.core.governance_receipts.emit_governance_receipt",
        lambda request, action_type, action_name, inputs=None, outputs=None, **kwargs: receipt_calls.append(
            (action_type.value, action_name, inputs, outputs)
        ),
    )

    too_short = client.post("/api/v1/providers/keys/rotate", json={"provider": "openai", "api_key": "short"})
    assert too_short.status_code == 200
    assert too_short.json()["message"] == "API key is too short"

    monkeypatch.setattr(provider_factory, "API_KEY_VARS", {"openai": "OPENAI_API_KEY"})
    created = []

    class _TestProvider:
        def list_models(self):
            return ["a", "b"]

    monkeypatch.setattr(
        provider_factory,
        "create_provider",
        lambda provider_name, api_key, auth_token="": created.append((provider_name, api_key, auth_token))
        or (_TestProvider() if len(created) == 1 else SimpleNamespace(provider_name=provider_name)),
    )
    persisted = []
    monkeypatch.setattr(providers_api, "_update_env_file", lambda env_var, new_value: persisted.append((env_var, new_value)) or True)

    rotated = client.post(
        "/api/v1/providers/keys/rotate",
        json={"provider": "openai", "api_key": "sk-rotated-123456"},
    )

    assert rotated.status_code == 200
    body = rotated.json()
    assert body["status"] == "ok"
    assert body["models_discovered"] == 2
    assert body["hot_swapped"] is True
    assert body["persisted_to_env"] is True
    assert body["key_preview"] == "····3456"
    assert orchestrator.switch_calls == ["openai"]
    assert discovery.replaced == [(SimpleNamespace(provider_name="openai"), {"fast": "gpt-fast"})]
    assert persisted == [("OPENAI_API_KEY", "sk-rotated-123456")]
    assert providers_api._auth_errors == {}
    assert receipt_calls[-1] == (
        "provider_key_rotated",
        "rotate_provider_key",
        {"provider": "openai", "key_preview": body["key_preview"]},
        {"models_discovered": 2, "hot_swapped": True, "persisted_to_env": True},
    )


def test_oauth_routes_surface_manager_status_and_errors(monkeypatch):
    class _Manager:
        def __init__(self):
            self.revoked = False

        def generate_auth_url(self):
            return ("https://example.com/auth", "state-1")

        def get_token_status(self):
            return {"configured": True, "status": "active"}

        def revoke(self):
            self.revoked = True

    anthropic = _Manager()
    codex = _Manager()
    monkeypatch.setattr("oauth_token_manager.get_oauth_manager", lambda: anthropic)
    monkeypatch.setattr("openai_codex_oauth_manager.get_openai_codex_manager", lambda: codex)
    client = _client()

    initiate = client.post("/api/v1/providers/oauth/initiate")
    assert initiate.status_code == 200
    assert initiate.json() == {
        "status": "ok",
        "auth_url": "https://example.com/auth",
        "state": "state-1",
    }

    status = client.get("/api/v1/providers/oauth/status")
    assert status.status_code == 200
    assert status.json() == {"configured": True, "status": "active"}

    revoke = client.post("/api/v1/providers/oauth/revoke")
    assert revoke.status_code == 200
    assert revoke.json()["status"] == "ok"
    assert anthropic.revoked is True

    codex_initiate = client.post("/api/v1/providers/oauth/openai-codex/initiate")
    assert codex_initiate.status_code == 200
    assert codex_initiate.json()["provider"] == "openai-codex"

    codex_status = client.get("/api/v1/providers/oauth/openai-codex/status")
    assert codex_status.status_code == 200
    assert codex_status.json() == {"configured": True, "status": "active"}

    codex_revoke = client.post("/api/v1/providers/oauth/openai-codex/revoke")
    assert codex_revoke.status_code == 200
    assert codex.revoke is not None
    assert codex.revoked is True


def test_codex_oauth_routes_prefer_cli_auth_when_manager_has_no_tokens(monkeypatch):
    class _Manager:
        def generate_auth_url(self):
            return ("https://example.com/auth", "state-1")

        def get_token_status(self):
            return {"configured": False, "valid": False, "status": "not_configured", "provider": "openai-codex"}

        def revoke(self):
            return None

    monkeypatch.setattr("openai_codex_oauth_manager.get_openai_codex_manager", lambda: _Manager())
    monkeypatch.setattr(providers_api, "_codex_cli_auth_available", lambda: True)
    client = _client()

    codex_initiate = client.post("/api/v1/providers/oauth/openai-codex/initiate")
    assert codex_initiate.status_code == 200
    assert codex_initiate.json() == {
        "status": "ok",
        "message": "Codex CLI auth is already available via mounted ~/.codex/auth.json. No browser OAuth flow is required.",
        "provider": "openai-codex",
    }

    codex_status = client.get("/api/v1/providers/oauth/openai-codex/status")
    assert codex_status.status_code == 200
    assert codex_status.json() == {
        "configured": True,
        "valid": True,
        "status": "cli_auth",
        "provider": "openai-codex",
    }


def test_provider_api_helpers_cover_persistence_and_fallback_paths(monkeypatch, tmp_path):
    providers_api._save_config({"active_provider": "gemini"})
    assert providers_api.load_persisted_config() == {"active_provider": "gemini"}

    providers_api._CONFIG_FILE.write_text("{broken", encoding="utf-8")
    assert providers_api.load_persisted_config() == {}

    env_file = tmp_path / ".env"
    env_file.write_text("EXISTING=value\nROTATE_ME=old\n", encoding="utf-8")
    monkeypatch.setenv("LANCELOT_ENV_PATH", str(env_file))
    assert providers_api._update_env_file("ROTATE_ME", "new") is True
    assert providers_api._update_env_file("ADDED_VAR", "fresh") is True
    assert env_file.read_text(encoding="utf-8").splitlines() == [
        "EXISTING=value",
        "ROTATE_ME=new",
        "ADDED_VAR=fresh",
    ]

    assert providers_api._mask_key("abc") == "****"
    assert providers_api._mask_key("0123456789") == "····6789"

    monkeypatch.setattr(providers_api, "_MODELS_YAML", str(tmp_path / "missing.yaml"))
    display_names = providers_api._get_provider_display_names()
    assert display_names["openai"] == "OpenAI"
    assert display_names["anthropic"] == "Anthropic"


def test_load_persisted_config_migrates_legacy_file(tmp_path):
    legacy_path = tmp_path / "legacy" / "provider_config.json"
    config_path = tmp_path / "provider_config.json"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text('{"active_provider": "openai-codex"}', encoding="utf-8")

    providers_api._DATA_DIR = tmp_path
    providers_api._CONFIG_FILE = config_path
    providers_api._LEGACY_CONFIG_FILE = legacy_path

    loaded = providers_api.load_persisted_config()

    assert loaded == {"active_provider": "openai-codex"}
    assert config_path.exists()
    assert json.loads(config_path.read_text(encoding="utf-8")) == {"active_provider": "openai-codex"}


def test_ensure_persisted_active_provider_creates_or_updates_config():
    providers_api._save_config({"lane_overrides": {"fast": "gpt-5.4-mini"}})

    changed = providers_api.ensure_persisted_active_provider("openai-codex")

    assert changed is True
    assert providers_api.load_persisted_config() == {
        "lane_overrides": {"fast": "gpt-5.4-mini"},
        "active_provider": "openai-codex",
    }

    unchanged = providers_api.ensure_persisted_active_provider("openai-codex")

    assert unchanged is False


def test_provider_api_oauth_and_profile_fallback_helpers(monkeypatch, tmp_path):
    profile = SimpleNamespace(
        fast=SimpleNamespace(model="fast-profile"),
        deep=SimpleNamespace(model="deep-profile"),
        cache=SimpleNamespace(model="cache-profile"),
    )
    registry = SimpleNamespace(
        has_provider=lambda provider: provider == "openai",
        get_profile=lambda provider: profile,
    )
    monkeypatch.setattr("provider_profile.ProfileRegistry", lambda: registry)

    assert providers_api._provider_profile_lane_defaults("openai") == {
        "fast": "fast-profile",
        "deep": "deep-profile",
        "cache": "cache-profile",
    }
    assert providers_api._provider_profile_lane_overrides("missing") == {}

    monkeypatch.setattr(
        "provider_profile.ProfileRegistry",
        lambda: (_ for _ in ()).throw(RuntimeError("profiles unavailable")),
    )
    assert providers_api._provider_profile_lane_defaults("openai") == {}

    class LegacyDiscovery:
        def __init__(self, provider, lane_overrides=None, fallback_lanes=None):
            if fallback_lanes is not None:
                raise TypeError("old discovery constructor")
            self.provider = provider
            self.lane_overrides = lane_overrides

    monkeypatch.setattr(model_discovery, "ModelDiscovery", LegacyDiscovery)
    discovery = providers_api._create_model_discovery(
        "provider",
        lane_overrides={"fast": "m1"},
        fallback_lanes={"fast": "default"},
    )
    assert discovery.lane_overrides == {"fast": "m1"}

    class ReplaceLegacy:
        def __init__(self):
            self.calls = []

        def replace_provider(self, provider, lane_overrides=None, fallback_lanes=None):
            if fallback_lanes is not None:
                raise TypeError("old replace signature")
            self.calls.append((provider, lane_overrides))

    replace_legacy = ReplaceLegacy()
    providers_api._replace_discovery_provider(
        replace_legacy,
        "new-provider",
        lane_overrides={"deep": "m2"},
        fallback_lanes={"deep": "default"},
    )
    assert replace_legacy.calls == [("new-provider", {"deep": "m2"})]

    monkeypatch.setattr("oauth_token_manager.get_oauth_manager", lambda: None)
    assert providers_api._anthropic_oauth_status() is None
    assert providers_api._anthropic_oauth_token() == ""

    monkeypatch.setattr(
        "oauth_token_manager.get_oauth_manager",
        lambda: (_ for _ in ()).throw(RuntimeError("vault down")),
    )
    assert providers_api._anthropic_oauth_status() is None
    assert providers_api._anthropic_oauth_token() == ""

    monkeypatch.setattr("openai_codex_oauth_manager.get_openai_codex_manager", lambda: None)
    monkeypatch.setattr(providers_api, "_codex_cli_auth_available", lambda: False)
    assert providers_api._codex_oauth_status() is None
    assert providers_api._codex_oauth_token() == ""

    monkeypatch.setattr(
        "openai_codex_oauth_manager.get_openai_codex_manager",
        lambda: (_ for _ in ()).throw(RuntimeError("codex store down")),
    )
    assert providers_api._codex_oauth_status() is None
    assert providers_api._codex_oauth_token() == ""

    monkeypatch.setattr(
        "providers.codex_cli_client.has_codex_cli_auth",
        lambda: (_ for _ in ()).throw(RuntimeError("home missing")),
    )
    assert providers_api._codex_cli_auth_available() is False

    monkeypatch.setattr(providers_api, "_DATA_DIR", tmp_path / "missing-parent")
    monkeypatch.setattr(providers_api, "_CONFIG_FILE", tmp_path / "missing-parent" / "provider_config.json")
    monkeypatch.setattr(
        providers_api.tempfile,
        "mkstemp",
        lambda **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    with pytest.raises(providers_api.ProviderConfigPersistenceError):
        providers_api._save_config({"active_provider": "openai"})
    assert not providers_api._CONFIG_FILE.exists()


def test_load_persisted_config_hydrates_local_openai_runtime_env(monkeypatch, tmp_path):
    config_path = tmp_path / "provider_config.json"
    config_path.write_text(
        json.dumps({
            "local_openai": {
                "base_url": "http://local-model:8000/v1",
                "fast_model": "fast-local",
                "deep_model": "deep-local",
                "cache_model": "cache-local",
                "context_window": 12345,
                "supports_tools": False,
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(providers_api, "_CONFIG_FILE", config_path)
    monkeypatch.setattr(providers_api, "_LEGACY_CONFIG_FILE", tmp_path / "legacy.json")
    for env_var in (
        "LOCAL_OPENAI_BASE_URL",
        "LOCAL_OPENAI_FAST_MODEL",
        "LOCAL_OPENAI_DEEP_MODEL",
        "LOCAL_OPENAI_CACHE_MODEL",
        "LOCAL_OPENAI_CONTEXT_WINDOW",
        "LOCAL_OPENAI_SUPPORTS_TOOLS",
    ):
        monkeypatch.delenv(env_var, raising=False)

    loaded = providers_api.load_persisted_config()

    assert loaded["local_openai"]["base_url"] == "http://local-model:8000/v1"
    assert providers_api.os.environ["LOCAL_OPENAI_BASE_URL"] == "http://local-model:8000/v1"
    assert providers_api.os.environ["LOCAL_OPENAI_FAST_MODEL"] == "fast-local"
    assert providers_api.os.environ["LOCAL_OPENAI_SUPPORTS_TOOLS"] == "false"


def test_provider_api_switch_rotate_and_lane_error_branches(monkeypatch):
    client = _client()
    monkeypatch.setattr(provider_factory, "API_KEY_VARS", {"openai": "OPENAI_API_KEY", "anthropic": ""})

    unknown = client.post("/api/v1/providers/switch", json={"provider": "missing"})
    assert unknown.status_code == 200
    assert "Unknown provider" in unknown.json()["message"]

    monkeypatch.setattr(providers_api, "_anthropic_oauth_token", lambda: "oauth-token")
    monkeypatch.setattr(
        provider_factory,
        "create_provider",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("provider factory failed")),
    )
    switched = client.post("/api/v1/providers/switch", json={"provider": "anthropic"})
    assert switched.status_code == 200
    assert switched.json() == {"status": "error", "message": "provider factory failed"}

    discovery = _FakeDiscovery(
        provider_name="openai",
        discovered_models=[_FakeModel("model-fast")],
        lane_assignments={"fast": "model-fast"},
    )
    orchestrator = _FakeOrchestrator()
    orchestrator.set_lane_model = lambda *args: (_ for _ in ()).throw(RuntimeError("lane locked"))
    providers_api.init_provider_api(discovery, orchestrator)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-current-123456")

    missing_model = client.post("/api/v1/providers/lanes/override", json={"lane": "fast", "model_id": ""})
    assert missing_model.status_code == 200
    assert missing_model.json() == {"status": "error", "message": "model_id is required"}

    failed_override = client.post(
        "/api/v1/providers/lanes/override",
        json={"lane": "fast", "model_id": "model-fast"},
    )
    assert failed_override.status_code == 200
    assert failed_override.json()["status"] == "error"
    assert "lane locked" in failed_override.json()["message"]

    class ResetDiscovery(_FakeDiscovery):
        def reset_overrides(self):
            self.reset_calls += 1
            self.lane_assignments = {"fast": "model-fast", "deep": "model-deep"}

    reset_discovery = ResetDiscovery(provider_name="openai")
    reset_orchestrator = _FakeOrchestrator()

    def set_lane(lane, model):
        reset_orchestrator.lane_calls.append((lane, model))
        if lane == "deep":
            raise RuntimeError("deep lane unavailable")

    reset_orchestrator.set_lane_model = set_lane
    providers_api.init_provider_api(reset_discovery, reset_orchestrator)
    reset = client.post("/api/v1/providers/lanes/reset")
    assert reset.status_code == 200
    assert reset.json()["status"] == "ok"
    assert reset_orchestrator.lane_calls == [("fast", "model-fast"), ("deep", "model-deep")]

    providers_api.init_provider_api(_FakeDiscovery(provider_name="openai"), _FakeOrchestrator())
    created = []

    def create_provider(provider_name, api_key, auth_token=""):
        created.append((provider_name, api_key, auth_token))
        if len(created) == 1:
            return SimpleNamespace(list_models=lambda: ["m1"])
        raise RuntimeError("hotswap unavailable")

    monkeypatch.setattr(provider_factory, "create_provider", create_provider)
    monkeypatch.setattr(providers_api, "_update_env_file", lambda *args: False)
    rotated = client.post(
        "/api/v1/providers/keys/rotate",
        json={"provider": "openai", "api_key": "sk-new-123456789"},
    )
    assert rotated.status_code == 200
    body = rotated.json()
    assert body["status"] == "ok"
    assert body["hot_swapped"] is False
    assert body["persisted_to_env"] is False

    unknown_rotate = client.post(
        "/api/v1/providers/keys/rotate",
        json={"provider": "unknown", "api_key": "sk-new-123456789"},
    )
    assert unknown_rotate.status_code == 200
    assert "Unknown provider" in unknown_rotate.json()["message"]

    monkeypatch.setattr(
        provider_factory,
        "create_provider",
        lambda provider_name, api_key, auth_token="": SimpleNamespace(
            list_models=lambda: (_ for _ in ()).throw(RuntimeError("auth rejected"))
        ),
    )
    failed_validation = client.post(
        "/api/v1/providers/keys/rotate",
        json={"provider": "openai", "api_key": "sk-bad-123456789"},
    )
    assert failed_validation.status_code == 200
    assert "Key validation failed" in failed_validation.json()["message"]


def test_provider_api_refresh_and_oauth_error_routes(monkeypatch):
    client = _client()
    discovery = _FakeDiscovery(provider_name="openai", discovered_models=[_FakeModel("m1")])
    discovery.refresh = lambda: (_ for _ in ()).throw(RuntimeError("provider timeout"))
    providers_api.init_provider_api(discovery, None)

    refresh = client.post("/api/v1/providers/refresh")
    assert refresh.status_code == 200
    assert refresh.json() == {"status": "error", "message": "provider timeout"}

    monkeypatch.setattr("oauth_token_manager.get_oauth_manager", lambda: None)
    assert client.post("/api/v1/providers/oauth/initiate").json() == {
        "status": "error",
        "message": "OAuth manager not initialized",
    }
    assert client.get("/api/v1/providers/oauth/status").json() == {
        "configured": False,
        "status": "not_available",
    }

    monkeypatch.setattr(
        "oauth_token_manager.get_oauth_manager",
        lambda: (_ for _ in ()).throw(RuntimeError("oauth store down")),
    )
    assert client.post("/api/v1/providers/oauth/initiate").json()["message"] == "oauth store down"
    assert client.get("/api/v1/providers/oauth/status").json()["error"] == "oauth store down"
    assert client.post("/api/v1/providers/oauth/revoke").json() == {
        "status": "error",
        "message": "oauth store down",
    }

    monkeypatch.setattr(providers_api, "_codex_cli_auth_available", lambda: False)
    monkeypatch.setattr("openai_codex_oauth_manager.get_openai_codex_manager", lambda: None)
    assert client.post("/api/v1/providers/oauth/openai-codex/initiate").json() == {
        "status": "error",
        "message": "Codex OAuth manager not initialized",
    }
    assert client.get("/api/v1/providers/oauth/openai-codex/status").json() == {
        "configured": False,
        "status": "not_available",
        "provider": "openai-codex",
    }
    assert client.post("/api/v1/providers/oauth/openai-codex/revoke").json() == {
        "status": "ok",
        "message": "Codex OAuth tokens revoked",
    }

    monkeypatch.setattr(providers_api, "_codex_cli_auth_available", lambda: True)
    assert client.post("/api/v1/providers/oauth/openai-codex/revoke").json() == {
        "status": "ok",
        "message": "Codex CLI auth is sourced from mounted ~/.codex/auth.json. Sign out on the host to revoke it.",
    }

    monkeypatch.setattr(
        "openai_codex_oauth_manager.get_openai_codex_manager",
        lambda: (_ for _ in ()).throw(RuntimeError("codex oauth down")),
    )
    monkeypatch.setattr(providers_api, "_codex_cli_auth_available", lambda: False)
    assert client.post("/api/v1/providers/oauth/openai-codex/initiate").json()["message"] == "codex oauth down"
    assert client.post("/api/v1/providers/oauth/openai-codex/revoke").json() == {
        "status": "error",
        "message": "codex oauth down",
    }
