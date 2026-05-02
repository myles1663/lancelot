import logging
import sys
from types import SimpleNamespace

import feature_flags
from src.core import gateway_health


class _Orchestrator:
    provider = object()
    local_model = None

    def is_memory_enabled(self):
        return True


def _health(state, *, degraded_reasons=None, error_message=None, metadata=None):
    return SimpleNamespace(
        state=SimpleNamespace(value=state),
        degraded_reasons=degraded_reasons or [],
        error_message=error_message,
        metadata=metadata or {},
    )


def test_health_snapshot_surfaces_enabled_uab_provider_offline(monkeypatch):
    monkeypatch.setattr(feature_flags, "FEATURE_TOOLS_HOST_EXECUTION", False, raising=False)
    monkeypatch.setattr(feature_flags, "FEATURE_TOOLS_HOST_BRIDGE", False, raising=False)
    monkeypatch.setattr(feature_flags, "FEATURE_TOOLS_UAB", True, raising=False)

    fabric = SimpleNamespace(
        get_health=lambda: {
            "uab_bridge": _health(
                "offline",
                degraded_reasons=["UAB daemon unreachable"],
                error_message="Cannot reach UAB daemon",
                metadata={"daemon_url": "http://host.docker.internal:7900"},
            )
        }
    )
    monkeypatch.setitem(
        sys.modules,
        "src.tools.fabric",
        SimpleNamespace(get_tool_fabric=lambda: fabric),
    )

    snapshot = gateway_health.build_health_snapshot(
        main_orchestrator=_Orchestrator(),
        crusader_mode=SimpleNamespace(is_active=False),
        app_version="test",
        startup_time=1,
        error_count=0,
        total_requests=1,
        logger=logging.getLogger("test"),
    )

    assert snapshot["components"]["uab_bridge"] == "offline"
    assert snapshot["tool_fabric"]["providers"]["uab_bridge"]["state"] == "offline"
    assert snapshot["tool_fabric"]["providers"]["uab_bridge"]["error_message"] == "Cannot reach UAB daemon"


def test_health_snapshot_omits_disabled_optional_tool_providers(monkeypatch):
    monkeypatch.setattr(feature_flags, "FEATURE_TOOLS_HOST_EXECUTION", False, raising=False)
    monkeypatch.setattr(feature_flags, "FEATURE_TOOLS_HOST_BRIDGE", False, raising=False)
    monkeypatch.setattr(feature_flags, "FEATURE_TOOLS_UAB", False, raising=False)

    snapshot = gateway_health.build_health_snapshot(
        main_orchestrator=_Orchestrator(),
        crusader_mode=SimpleNamespace(is_active=False),
        app_version="test",
        startup_time=1,
        error_count=0,
        total_requests=1,
        logger=logging.getLogger("test"),
    )

    assert "uab_bridge" not in snapshot["components"]
    assert "tool_fabric" not in snapshot


def test_local_model_role_lane_summaries():
    assert gateway_health.summarize_local_model_role_lane({}) == {
        "ready": False,
        "loaded": False,
        "status": "unavailable",
    }

    degraded = gateway_health.summarize_local_model_role_lane({
        "planner": {
            "enabled": True,
            "ready": False,
            "loaded": True,
            "last_error": "planner cold",
            "last_checked_at": "2026-01-02T00:00:00Z",
            "last_smoke_elapsed_ms": 12,
        },
        "writer": {
            "enabled": False,
            "ready": True,
            "loaded": True,
        },
    })
    ready = gateway_health.summarize_local_model_role_lane({
        "planner": {
            "ready": True,
            "loaded": True,
            "last_verified_at": "2026-01-01T00:00:00Z",
            "last_checked_at": "2026-01-01T00:00:01Z",
            "last_smoke_elapsed_ms": 7,
        }
    })

    assert degraded["status"] == "degraded"
    assert degraded["last_error"] == "planner cold"
    assert degraded["last_smoke_elapsed_ms"] == 12
    assert ready["ready"] is True
    assert ready["status"] == "ok"


def test_router_readiness_handles_none_exceptions_and_roles_shape():
    assert gateway_health.summarize_local_model_router_readiness(None)["status"] == "unavailable"

    class BrokenRoles:
        def status(self):
            raise RuntimeError("roles unavailable")

    assert gateway_health.summarize_local_model_router_readiness(BrokenRoles())["last_error"] == "roles unavailable"

    class Roles:
        def status(self):
            return {"roles": {"planner": {"ready": True, "loaded": True}}}

    assert gateway_health.summarize_local_model_router_readiness(Roles())["ready"] is True


def test_collect_local_model_status_uses_local_health_and_role_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "src.core.model_usage_policy.set_local_model_roles_status",
        lambda raw: calls.append(raw),
        raising=False,
    )

    class LocalModel:
        def health(self):
            return {
                "ready": False,
                "loaded": False,
                "status": "unavailable",
                "last_error": "model down",
            }

    class Roles:
        def status(self):
            return {"roles": {"planner": {"ready": True, "loaded": True, "last_verified_at": "v"}}}

    status = gateway_health._collect_local_model_status(
        SimpleNamespace(local_model=LocalModel(), local_model_roles=Roles()),
        logging.getLogger("test"),
    )

    assert status["ready"] is True
    assert status["loaded"] is True
    assert status["last_error"] is None
    assert calls


def test_collect_local_model_status_handles_health_and_role_errors(caplog):
    class BrokenLocalModel:
        def health(self):
            raise RuntimeError("health failed")

    class BrokenRoles:
        def status(self):
            raise RuntimeError("roles failed")

    logger = logging.getLogger("test.gateway_health")
    with caplog.at_level(logging.DEBUG):
        status = gateway_health._collect_local_model_status(
            SimpleNamespace(local_model=BrokenLocalModel(), local_model_roles=BrokenRoles()),
            logger,
        )

    assert status["status"] == "unavailable"
    assert status["last_error"] == "health failed"
    assert "Local model role status unavailable" in caplog.text


def test_tool_provider_status_uses_probe_fallback_and_missing_provider(monkeypatch):
    monkeypatch.setattr(feature_flags, "FEATURE_TOOLS_HOST_EXECUTION", True, raising=False)
    monkeypatch.setattr(feature_flags, "FEATURE_TOOLS_HOST_BRIDGE", True, raising=False)
    monkeypatch.setattr(feature_flags, "FEATURE_TOOLS_UAB", True, raising=False)

    class Fabric:
        def get_health(self):
            raise RuntimeError("cache unavailable")

        def probe_health(self):
            return {
                "host_execution": _health("healthy"),
                "host_bridge": _health("degraded", degraded_reasons=["token missing"]),
            }

    monkeypatch.setitem(sys.modules, "src.tools.fabric", SimpleNamespace(get_tool_fabric=lambda: Fabric()))

    components, details = gateway_health._collect_tool_provider_status(logging.getLogger("test"))

    assert components == {
        "host_execution": "ok",
        "host_bridge": "degraded",
        "uab_bridge": "unavailable",
    }
    assert details["uab_bridge"]["error_message"] == "provider is enabled but not registered"


def test_health_and_readiness_snapshots_cover_degraded_paths(monkeypatch):
    monkeypatch.setattr(feature_flags, "FEATURE_TOOLS_HOST_EXECUTION", False, raising=False)
    monkeypatch.setattr(feature_flags, "FEATURE_TOOLS_HOST_BRIDGE", False, raising=False)
    monkeypatch.setattr(feature_flags, "FEATURE_TOOLS_UAB", False, raising=False)
    orchestrator = SimpleNamespace(
        provider=None,
        local_model=None,
        local_model_roles=None,
        is_memory_enabled=lambda: False,
    )

    health = gateway_health.build_health_snapshot(
        main_orchestrator=orchestrator,
        crusader_mode=SimpleNamespace(is_active=True),
        app_version="test",
        startup_time=None,
        error_count=2,
        total_requests=4,
        logger=logging.getLogger("test"),
    )
    ready_code, ready_body = gateway_health.build_readiness_snapshot(
        main_orchestrator=orchestrator,
        startup_time=None,
    )

    assert health["components"]["orchestrator"] == "degraded"
    assert health["components"]["memory"] == "disabled"
    assert health["error_rate"] == 50.0
    assert health["uptime_seconds"] == 0
    assert ready_code == 503
    assert ready_body["ready"] is False
