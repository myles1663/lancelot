import logging
import sys
from types import SimpleNamespace

import feature_flags
import gateway_health


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
