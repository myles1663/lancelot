import json
import sys
import types

import pytest
from fastapi.responses import JSONResponse

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
    assert flags["FEATURE_RESPONSE_ASSEMBLER"]["hot_toggleable"] is True
    assert flags["FEATURE_RESPONSE_ASSEMBLER"]["hot_toggle_mode"] == "dynamic"


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
    assert response["hot_toggled"] is True
    assert response["hot_toggle_mode"] == "dynamic"
    assert calls == [("FEATURE_TOOLS_UAB", True)]


def test_flags_audit_user_resolution_prefers_display_operator_and_fallback(monkeypatch):
    monkeypatch.setattr(
        "src.core.auth_api.resolve_operator_identity",
        lambda request: types.SimpleNamespace(display_name="Arthur", operator_id="op-1"),
    )
    monkeypatch.setattr("src.core.auth_api.get_api_key_identity", lambda request: None)
    assert flags_api._resolve_audit_user(object()) == "Arthur"

    monkeypatch.setattr("src.core.auth_api.resolve_operator_identity", lambda request: None)
    monkeypatch.setattr(
        "src.core.auth_api.get_api_key_identity",
        lambda request: types.SimpleNamespace(display_name="", operator_id="api-op"),
    )
    assert flags_api._resolve_audit_user(object()) == "api-op"

    monkeypatch.setattr(
        "src.core.auth_api.resolve_operator_identity",
        lambda request: (_ for _ in ()).throw(RuntimeError("auth down")),
    )
    assert flags_api._resolve_audit_user(object()) == "operator"


@pytest.mark.asyncio
async def test_network_allowlist_and_host_write_paths_cover_success_and_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        flags_api,
        "_network_allowlist",
        types.SimpleNamespace(
            path=str(tmp_path / "network_allowlist.yaml"),
            load_config=lambda: {"domains": ["api.example.com"]},
            set_domains=lambda domains: sorted(set(domains)),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "gateway",
        types.SimpleNamespace(
            main_orchestrator=types.SimpleNamespace(
                network_interceptor=types.SimpleNamespace(
                    ALLOW_LIST=["api.example.com"],
                    reload_allowlist=lambda: None,
                )
            )
        ),
    )

    assert await flags_api.get_network_allowlist() == {
        "domains": ["api.example.com"],
        "path": str(tmp_path / "network_allowlist.yaml"),
    }
    assert await flags_api.update_network_allowlist(flags_api.AllowlistUpdate(domains=["b.test", "a.test"]), _authz=None) == {
        "domains": ["a.test", "b.test"],
        "count": 2,
    }

    monkeypatch.setattr(
        flags_api,
        "_network_allowlist",
        types.SimpleNamespace(
            path="broken",
            load_config=lambda: (_ for _ in ()).throw(RuntimeError("read failed")),
            set_domains=lambda domains: (_ for _ in ()).throw(RuntimeError("write failed")),
        ),
    )
    assert (await flags_api.get_network_allowlist()).status_code == 500
    assert (await flags_api.update_network_allowlist(flags_api.AllowlistUpdate(domains=["x"]), _authz=None)).status_code == 500

    write_path = tmp_path / "host_write_commands.yaml"
    write_path.write_text("# comment\nrm\ndel\n", encoding="utf-8")
    monkeypatch.setattr(flags_api, "WRITE_COMMANDS_PATH", str(write_path))
    assert await flags_api.get_host_write_commands() == {
        "commands": ["rm", "del"],
        "raw": "# comment\nrm\ndel\n",
        "path": str(write_path),
    }
    updated = await flags_api.update_host_write_commands(
        flags_api.WriteCommandsUpdate(raw="# allowed\ncopy\nmove\n"),
        _authz=None,
    )
    assert updated == {"commands": ["copy", "move"], "count": 2}

    monkeypatch.setattr(flags_api, "WRITE_COMMANDS_PATH", str(tmp_path / "missing-dir" / "commands.yaml"))
    monkeypatch.setattr(flags_api.os, "makedirs", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("mkdir failed")))
    assert (await flags_api.update_host_write_commands(flags_api.WriteCommandsUpdate(raw="rm"), _authz=None)).status_code == 500


@pytest.mark.asyncio
async def test_host_agent_status_shutdown_and_toggle_paths(monkeypatch):
    import feature_flags as ff

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps(
                {
                    "platform": "Windows",
                    "platform_version": "11",
                    "hostname": "workstation",
                    "agent_version": "1.0",
                }
            ).encode("utf-8")

    monkeypatch.setenv("HOST_AGENT_TOKEN", "host-secret")
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: Response())
    status = await flags_api.get_host_agent_status()
    assert status["reachable"] is True
    assert status["auth_state"] == "configured"
    assert status["platform"] == "Windows"

    shutdown = await flags_api.shutdown_host_agent(_authz=None)
    assert shutdown["status"] == "shutdown_sent"
    assert shutdown["agent_response"]["agent_version"] == "1.0"

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    assert (await flags_api.shutdown_host_agent(_authz=None)).status_code == 502
    assert (await flags_api.get_host_agent_status())["reachable"] is False

    monkeypatch.setenv("HOST_AGENT_TOKEN", "lancelot-host-agent")
    legacy = await flags_api.shutdown_host_agent(_authz=None)
    assert legacy.status_code == 503
    assert "legacy default" in json.loads(legacy.body.decode("utf-8"))["error"]

    monkeypatch.setattr(ff, "FEATURE_HOST_WRITE_COMMANDS", False, raising=False)
    monkeypatch.setattr(ff, "toggle_flag", lambda name: True)
    assert await flags_api.toggle_host_write_commands(_authz=None) == {"enabled": True}


@pytest.mark.asyncio
async def test_toggle_and_set_flag_hot_toggle_and_error_paths(monkeypatch):
    import feature_flags as ff

    receipt_calls = []
    monkeypatch.setattr("src.core.governance_receipts.emit_governance_receipt", lambda *args, **kwargs: receipt_calls.append((args, kwargs)))
    monkeypatch.setattr(ff, "FEATURE_TOOL_FLOW_STREAMING", False, raising=False)
    monkeypatch.setattr(ff, "RESTART_REQUIRED_FLAGS", frozenset({"FEATURE_TOOL_FLOW_STREAMING"}), raising=False)

    state = {"running": False}
    manager = types.SimpleNamespace(
        get_by_flag=lambda name: types.SimpleNamespace(name="toolflow") if name == "FEATURE_TOOL_FLOW_STREAMING" else None,
        is_running=lambda name: state["running"],
        start=lambda name: state.update(running=True),
        stop=lambda name: state.update(running=False),
    )
    monkeypatch.setitem(sys.modules, "subsystem_manager", types.SimpleNamespace(subsystem_manager=manager))

    def toggle(name):
        value = not getattr(ff, name)
        setattr(ff, name, value)
        return value

    monkeypatch.setattr(ff, "toggle_flag", toggle)
    response = await flags_api.toggle_flag("FEATURE_TOOL_FLOW_STREAMING", request=object(), _authz=None)
    assert response["enabled"] is True
    assert response["hot_toggled"] is True
    assert response["restart_required"] is True

    response = await flags_api.toggle_flag("FEATURE_TOOL_FLOW_STREAMING", request=object(), _authz=None)
    assert response["enabled"] is False
    assert response["hot_toggled"] is True

    monkeypatch.setattr(ff, "FEATURE_TOOL_FLOW_STREAMING", False, raising=False)
    manager.start = lambda name: (_ for _ in ()).throw(RuntimeError("start failed"))
    response = await flags_api.toggle_flag("FEATURE_TOOL_FLOW_STREAMING", request=object(), _authz=None)
    assert response["hot_toggled"] is False
    assert "subsystem toggle failed" in response["message"]

    assert (await flags_api.toggle_flag("FEATURE_UNKNOWN", request=object(), _authz=None)).status_code == 400

    manager.get_by_flag = lambda name: types.SimpleNamespace(name="toolflow")
    manager.is_running = lambda name: True
    manager.stop = lambda name: (_ for _ in ()).throw(RuntimeError("stop failed"))
    monkeypatch.setattr(ff, "set_flag", lambda name, value: setattr(ff, name, value))
    response = await flags_api.set_flag("FEATURE_TOOL_FLOW_STREAMING", request=object(), value=False, _authz=None)
    assert response["enabled"] is False
    assert response["hot_toggled"] is False

    monkeypatch.setattr(ff, "set_flag", lambda name, value: (_ for _ in ()).throw(ValueError("bad flag")))
    response = await flags_api.set_flag("FEATURE_TOOL_FLOW_STREAMING", request=object(), value=True, _authz=None)
    assert response.status_code == 400
    assert json.loads(response.body.decode("utf-8"))["error"] == "bad flag"


@pytest.mark.asyncio
async def test_uab_receipts_sessions_and_error_paths(monkeypatch):
    receipts = [types.SimpleNamespace(to_dict=lambda: {"id": "r1"})]
    store = types.SimpleNamespace(
        get_recent_receipts=lambda **kwargs: receipts,
        get_session_summaries=lambda limit=20: [{"session_id": "s1", "count": 1}],
    )
    monkeypatch.setitem(
        sys.modules,
        "src.tools.receipts_uab",
        types.SimpleNamespace(get_uab_receipt_store=lambda: store),
    )

    assert await flags_api.get_uab_receipts(limit=5, app_name="Chrome", mutating_only=True, action_type="click") == {
        "receipts": [{"id": "r1"}]
    }
    assert await flags_api.get_uab_sessions(limit=2) == {"sessions": [{"session_id": "s1", "count": 1}]}

    monkeypatch.setitem(
        sys.modules,
        "src.tools.receipts_uab",
        types.SimpleNamespace(get_uab_receipt_store=lambda: (_ for _ in ()).throw(RuntimeError("receipt store down"))),
    )
    assert "receipt store down" in (await flags_api.get_uab_receipts())["error"]
    assert "receipt store down" in (await flags_api.get_uab_sessions())["error"]

    monkeypatch.setattr(flags_api, "_uab_rpc", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("uab offline")))
    assert (await flags_api.get_uab_status())["reachable"] is False
    assert await flags_api.get_uab_connected_apps() == {"apps": []}
