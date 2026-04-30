import importlib

import pytest
from fastapi.testclient import TestClient

import gateway
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


def test_gateway_gates_boot_only_routes_when_flags_are_disabled(monkeypatch):
    gated_routes = [
        ("/api/mcp/servers", "FEATURE_MCP"),
        ("/api/metrics/summary", "FEATURE_OBSERVABILITY"),
        ("/api/incidents", "FEATURE_INCIDENT_RESPONSE"),
        ("/api/playbooks", "FEATURE_INCIDENT_RESPONSE"),
        ("/api/actioncards", "FEATURE_ACTION_CARDS"),
    ]

    for _, flag_name in gated_routes:
        monkeypatch.setattr(gateway._ff, flag_name, False, raising=False)

    client = TestClient(gateway.app)
    for path, flag_name in gated_routes:
        response = client.get(path)
        assert response.status_code == 503
        assert response.json()["error"] == "subsystem_disabled"
        assert response.json()["flag"] == flag_name


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
