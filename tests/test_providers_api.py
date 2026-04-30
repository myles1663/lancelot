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


def test_override_and_reset_lanes_persist_and_sync_orchestrator():
    discovery = _FakeDiscovery(
        provider_name="openai",
        discovered_models=[_FakeModel("model-fast"), _FakeModel("model-deep"), _FakeModel("model-cache")],
        lane_assignments={"fast": "model-fast", "deep": "model-deep"},
    )
    orchestrator = _FakeOrchestrator()
    providers_api.init_provider_api(discovery, orchestrator)
    client = _client()

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

    reset = client.post("/api/v1/providers/lanes/reset")
    assert reset.status_code == 200
    assert reset.json()["status"] == "ok"
    assert discovery.reset_calls == 1
    assert orchestrator.lane_calls[-2:] == [("fast", "model-cache"), ("deep", "model-deep")]
    assert "lane_overrides" not in providers_api.load_persisted_config()


def test_rotate_provider_key_validates_hotswaps_and_persists(monkeypatch):
    discovery = _FakeDiscovery(provider_name="openai")
    orchestrator = _FakeOrchestrator()
    providers_api.init_provider_api(discovery, orchestrator)
    providers_api.report_auth_error("openai", "old auth failure")
    providers_api._save_config({"lane_overrides": {"fast": "gpt-fast"}})
    client = _client()

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
