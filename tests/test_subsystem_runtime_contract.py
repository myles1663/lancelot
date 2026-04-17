from pathlib import Path
import importlib

import pytest

from src.core import flags_api


@pytest.mark.asyncio
async def test_boot_only_flags_are_marked_restart_required():
    response = await flags_api.get_flags()
    flags = response["flags"]

    assert flags["FEATURE_MCP"]["restart_required"] is True
    assert flags["FEATURE_OBSERVABILITY"]["restart_required"] is True
    assert flags["FEATURE_TIME_TRAVEL"]["restart_required"] is True
    assert flags["FEATURE_A2A"]["restart_required"] is True
    assert flags["FEATURE_INCIDENT_RESPONSE"]["restart_required"] is True
    assert flags["FEATURE_MEMORY_VNEXT"]["restart_required"] is False


def test_gateway_source_gates_boot_only_subsystems_and_initializes_mcp():
    source = Path("src/core/gateway.py").read_text(encoding="utf-8")

    assert '("/api/mcp", "FEATURE_MCP")' in source
    assert '("/api/metrics", "FEATURE_OBSERVABILITY")' in source
    assert '("/api/incidents", "FEATURE_INCIDENT_RESPONSE")' in source
    assert '("/api/playbooks", "FEATURE_INCIDENT_RESPONSE")' in source
    assert '("/api/actioncards", "FEATURE_ACTION_CARDS")' in source
    assert "init_mcp_api(" in source
    assert "MCP subsystem initialized." in source
    assert "from observability.metrics_api import router as metrics_api_router, init_metrics_api" in source
    assert "Metrics API initialized." in source


def test_feature_flags_module_is_canonical_across_import_paths():
    bare = importlib.import_module("feature_flags")
    namespaced = importlib.import_module("src.core.feature_flags")

    assert bare is namespaced

    original = bare.FEATURE_INCIDENT_RESPONSE
    try:
        bare.FEATURE_INCIDENT_RESPONSE = not original
        assert namespaced.FEATURE_INCIDENT_RESPONSE is (not original)
    finally:
        bare.FEATURE_INCIDENT_RESPONSE = original
