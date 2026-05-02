import importlib

import pytest
from fastapi.testclient import TestClient

import gateway
from src.core import flags_api


@pytest.mark.asyncio
async def test_optional_subsystems_are_hot_toggleable_not_restart_required():
    response = await flags_api.get_flags()
    flags = response["flags"]

    assert flags["FEATURE_MCP"]["restart_required"] is False
    assert flags["FEATURE_OBSERVABILITY"]["restart_required"] is False
    assert flags["FEATURE_TIME_TRAVEL"]["restart_required"] is False
    assert flags["FEATURE_A2A"]["restart_required"] is False
    assert flags["FEATURE_INCIDENT_RESPONSE"]["restart_required"] is False
    assert flags["FEATURE_ACTION_CARDS"]["restart_required"] is False
    assert flags["FEATURE_TOOL_FLOW_STREAMING"]["restart_required"] is False
    assert flags["FEATURE_GOOGLE_OAUTH"]["restart_required"] is False
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


def test_hot_toggle_shutdown_hooks_clear_runtime_references(tmp_path):
    from src.a2a import api as a2a_api
    from src.a2a import server as a2a_server
    from src.core import actioncard_api
    from src.incidents import api as incidents_api
    from src.incidents import playbook_api
    from src.mcp import api as mcp_api
    from src.observability import metrics_api
    from src.timetravel import api as timetravel_api

    sentinel = object()

    mcp_api.init_mcp_api(registry=sentinel, evaluator=sentinel, proxy=sentinel)
    mcp_api.shutdown_mcp_api()
    assert mcp_api._registry is None
    assert mcp_api._evaluator is None
    assert mcp_api._proxy is None

    timetravel_api.init_timetravel_api(
        receipt_service=sentinel,
        soul=lambda: None,
        quest_executor=None,
    )
    timetravel_api.shutdown_timetravel_api()
    assert timetravel_api._receipt_service is None
    assert timetravel_api._resume_engine is None

    a2a_api.init_a2a_api(sentinel, sentinel, lambda: None, sentinel, sentinel)
    a2a_api.shutdown_a2a_api()
    assert a2a_api._registry is None
    assert a2a_api._outbound_pipeline is None

    a2a_server.init_a2a_server(
        lambda: None,
        sentinel,
        sentinel,
        sentinel,
        data_dir=str(tmp_path),
    )
    a2a_server.shutdown_a2a_server()
    assert a2a_server._registry is None
    assert a2a_server._inbound_pipeline is None

    actioncard_api.init_actioncard_api(sentinel, sentinel)
    actioncard_api.shutdown_actioncard_api()
    assert actioncard_api._card_store is None
    assert actioncard_api._card_resolver is None

    incidents_api.init_incidents_api(sentinel, str(tmp_path / "incidents"))
    incidents_api.shutdown_incidents_api()
    assert incidents_api._receipt_service is None
    assert incidents_api._data_dir is None

    playbook_api.init_playbook_api("playbooks")
    playbook_api.shutdown_playbook_api()
    assert playbook_api._playbooks_dir is None

    metrics_api.init_metrics_api(sentinel, data_dir=str(tmp_path / "metrics"))
    metrics_api.shutdown_metrics_api()
    assert metrics_api._receipt_service is None
