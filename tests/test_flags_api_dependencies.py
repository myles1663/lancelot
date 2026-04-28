import json
import sys
import types

import pytest

from src.core import flags_api


class _SubsystemManagerStub:
    def get_by_flag(self, name):
        return None

    def is_running(self, name):
        return False

    def start(self, name):
        raise AssertionError("subsystem start should not run in dependency validation tests")

    def stop(self, name):
        raise AssertionError("subsystem stop should not run in dependency validation tests")


def _response_json(response):
    return json.loads(response.body.decode("utf-8"))


@pytest.fixture(autouse=True)
def _stub_runtime(monkeypatch):
    import feature_flags as ff
    import src.core.governance_receipts as governance_receipts

    monkeypatch.setattr(flags_api, "_audit_logger", None)
    monkeypatch.setattr(governance_receipts, "emit_governance_receipt", lambda *args, **kwargs: None)
    monkeypatch.setitem(
        sys.modules,
        "subsystem_manager",
        types.SimpleNamespace(subsystem_manager=_SubsystemManagerStub()),
    )

    original = {name: getattr(ff, name) for name in flags_api.FLAG_META}
    yield
    for name, value in original.items():
        monkeypatch.setattr(ff, name, value, raising=False)


@pytest.mark.asyncio
async def test_get_flags_exposes_dependency_edges_for_graph_rendering():
    payload = await flags_api.get_flags()
    flags = payload["flags"]

    assert flags["FEATURE_TOOLS_UAB"]["requires"] == [
        "FEATURE_TOOLS_FABRIC",
        "FEATURE_TOOLS_HOST_BRIDGE",
    ]
    assert flags["FEATURE_HIVE_UAB"]["requires"] == [
        "FEATURE_HIVE",
        "FEATURE_TOOLS_UAB",
    ]


@pytest.mark.asyncio
async def test_get_flags_has_operator_metadata_and_clean_public_text():
    payload = await flags_api.get_flags()
    flags = payload["flags"]

    for name in [
        "FEATURE_A2A",
        "FEATURE_ACTION_CARDS",
        "FEATURE_GOOGLE_OAUTH",
        "FEATURE_INCIDENT_RESPONSE",
        "FEATURE_OBSERVABILITY",
        "FEATURE_TIME_TRAVEL",
        "FEATURE_TOOL_FLOW_STREAMING",
    ]:
        assert flags[name]["category"] != "Other"
        assert flags[name]["description"]
        assert flags[name]["warning"]

    bad_fragments = (
        "\u00c3",
        "\u00c2",
        "\u00e2\u20ac",
        "\u00e2\u0080",
        "\u0080",
        "\u0099",
        "\u009c",
        "\u009d",
    )
    for name, flag in flags.items():
        for field in ("description", "warning", "confirm_enable", "category"):
            value = flag.get(field)
            if isinstance(value, str):
                assert not any(fragment in value for fragment in bad_fragments), (name, field, value)


def test_validate_flag_dependencies_blocks_enabling_uab_without_parents(monkeypatch):
    import feature_flags as ff

    monkeypatch.setattr(ff, "FEATURE_TOOLS_FABRIC", False, raising=False)
    monkeypatch.setattr(ff, "FEATURE_TOOLS_HOST_BRIDGE", False, raising=False)

    error = flags_api._validate_flag_dependencies("FEATURE_TOOLS_UAB", True)
    assert error == "Cannot enable FEATURE_TOOLS_UAB: requires FEATURE_TOOLS_FABRIC to be enabled first"

    monkeypatch.setattr(ff, "FEATURE_TOOLS_FABRIC", True, raising=False)
    error = flags_api._validate_flag_dependencies("FEATURE_TOOLS_UAB", True)
    assert error == "Cannot enable FEATURE_TOOLS_UAB: requires FEATURE_TOOLS_HOST_BRIDGE to be enabled first"


def test_validate_flag_dependencies_blocks_disabling_parent_with_enabled_child(monkeypatch):
    import feature_flags as ff

    monkeypatch.setattr(ff, "FEATURE_TOOLS_HOST_BRIDGE", True, raising=False)
    monkeypatch.setattr(ff, "FEATURE_TOOLS_UAB", True, raising=False)

    error = flags_api._validate_flag_dependencies("FEATURE_TOOLS_HOST_BRIDGE", False)
    assert error == (
        "Cannot disable FEATURE_TOOLS_HOST_BRIDGE: FEATURE_TOOLS_UAB depends on it "
        "(disable FEATURE_TOOLS_UAB first)"
    )


@pytest.mark.asyncio
async def test_set_flag_rejects_enabling_uab_without_dependencies(monkeypatch):
    import feature_flags as ff

    monkeypatch.setattr(ff, "FEATURE_TOOLS_FABRIC", False, raising=False)
    monkeypatch.setattr(ff, "FEATURE_TOOLS_HOST_BRIDGE", False, raising=False)

    calls = []
    monkeypatch.setattr(ff, "set_flag", lambda name, value: calls.append((name, value)))

    response = await flags_api.set_flag(
        "FEATURE_TOOLS_UAB",
        request=object(),
        value=True,
        _authz=None,
    )

    assert response.status_code == 400
    assert _response_json(response)["error"] == (
        "Cannot enable FEATURE_TOOLS_UAB: requires FEATURE_TOOLS_FABRIC to be enabled first"
    )
    assert calls == []


@pytest.mark.asyncio
async def test_toggle_flag_rejects_disabling_parent_with_live_dependent(monkeypatch):
    import feature_flags as ff

    monkeypatch.setattr(ff, "FEATURE_TOOLS_FABRIC", True, raising=False)
    monkeypatch.setattr(ff, "FEATURE_TOOLS_HOST_BRIDGE", True, raising=False)
    monkeypatch.setattr(ff, "FEATURE_TOOLS_UAB", True, raising=False)

    calls = []
    monkeypatch.setattr(ff, "toggle_flag", lambda name: calls.append(name))

    response = await flags_api.toggle_flag(
        "FEATURE_TOOLS_HOST_BRIDGE",
        request=object(),
        _authz=None,
    )

    assert response.status_code == 400
    assert _response_json(response)["error"] == (
        "Cannot disable FEATURE_TOOLS_HOST_BRIDGE: FEATURE_TOOLS_UAB depends on it "
        "(disable FEATURE_TOOLS_UAB first)"
    )
    assert calls == []


@pytest.mark.asyncio
async def test_set_flag_allows_enable_once_dependencies_are_satisfied(monkeypatch):
    import feature_flags as ff

    monkeypatch.setattr(ff, "FEATURE_TOOLS_FABRIC", True, raising=False)
    monkeypatch.setattr(ff, "FEATURE_TOOLS_HOST_BRIDGE", True, raising=False)
    monkeypatch.setattr(ff, "FEATURE_TOOLS_UAB", False, raising=False)

    calls = []

    def _set_flag(name, value):
        calls.append((name, value))
        setattr(ff, name, value)

    monkeypatch.setattr(ff, "set_flag", _set_flag)

    response = await flags_api.set_flag(
        "FEATURE_TOOLS_UAB",
        request=object(),
        value=True,
        _authz=None,
    )

    assert response["flag"] == "FEATURE_TOOLS_UAB"
    assert response["enabled"] is True
    assert response["hot_toggled"] is False
    assert calls == [("FEATURE_TOOLS_UAB", True)]
