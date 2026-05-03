import json
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.responses import HTMLResponse, JSONResponse

from src.incidents.models import (
    IncidentCategory,
    IncidentRecord,
    IncidentSeverity,
    IncidentStatus,
)
from src.incidents.store import IncidentStore, get_incident_store
from src.tools.contracts import RiskLevel
from src.tools.receipts_uab import (
    AppControlReceipt,
    AppControlReceiptStore,
    AppSessionEntry,
    create_app_control_receipt,
    get_uab_receipt_store,
    reset_uab_receipt_store,
)
from src.ui.panels import cost_panel


def _fake_module(**attrs):
    mod = types.ModuleType("fake")
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


def _preserve_module_globals(monkeypatch, module, names):
    for name in names:
        monkeypatch.setattr(module, name, getattr(module, name, None), raising=False)


def test_uab_receipt_round_trip_session_rollup_and_filters(tmp_path):
    store = AppControlReceiptStore(data_dir=str(tmp_path))
    session = store.start_session(42, "Slack", framework="electron", connection_method="cdp")

    read_receipt = AppControlReceipt(
        app_name="Slack",
        app_pid=42,
        action_type="query",
        element_id="channel-list",
        risk_level=RiskLevel.LOW.value,
        elements_returned=3,
        chain_id="chain-1",
    )
    write_receipt = AppControlReceipt(
        app_name="Slack",
        app_pid=42,
        action_type="act",
        action_performed="click",
        mutating=True,
        element_id="send-button",
        risk_level=RiskLevel.HIGH.value,
        chain_id="chain-1",
    )

    store.store_receipt(read_receipt)
    store.store_receipt(write_receipt)

    assert AppControlReceipt.from_dict(read_receipt.to_dict()).receipt_id == read_receipt.receipt_id
    assert AppSessionEntry.from_dict(session.to_dict()).session_id == session.session_id
    assert session.total_actions == 2
    assert session.read_only_actions == 1
    assert session.mutating_actions == 1
    assert session.action_summary == {"query": 1, "click": 1}
    assert session.elements_touched == ["channel-list", "send-button"]
    assert session.max_risk_level == RiskLevel.HIGH.value

    assert store.get_active_sessions()[42].session_id == session.session_id
    assert store.get_recent_receipts(app_name="slack")[0].receipt_id == write_receipt.receipt_id
    assert store.get_recent_receipts(mutating_only=True) == [write_receipt]
    assert store.get_recent_receipts(action_type="query") == [read_receipt]
    assert store.get_receipts_for_chain("chain-1") == [read_receipt, write_receipt]

    closed = store.end_session(42)
    assert closed is session
    assert closed.disconnected_at
    assert store.end_session(42) is None
    summaries = store.get_session_summaries()
    assert summaries[0]["active"] is False
    assert summaries[0]["session_id"] == session.session_id


def test_uab_receipt_builder_defaults_failure_and_singleton(tmp_path):
    reset_uab_receipt_store()
    store = get_uab_receipt_store(data_dir=str(tmp_path))
    assert get_uab_receipt_store(data_dir=str(tmp_path / "other")) is store
    reset_uab_receipt_store()
    assert get_uab_receipt_store(data_dir=str(tmp_path / "other")) is not store

    receipt = create_app_control_receipt(
        action_type="act",
        app_name="Outlook",
        app_pid=7,
        action_performed="sendEmail",
        element_label="Send",
    )
    assert receipt.mutating is True
    assert receipt.risk_level in {RiskLevel.MEDIUM.value, RiskLevel.HIGH.value}
    receipt.fail("mailbox unavailable")
    assert receipt.success is False
    assert receipt.error_message == "mailbox unavailable"

    override = create_app_control_receipt(
        action_type="query",
        app_name="Calculator",
        mutating=True,
        risk_level=RiskLevel.HIGH.value,
    )
    assert override.mutating is True
    assert override.risk_level == RiskLevel.HIGH.value


def test_incident_store_crud_filters_dedup_and_rebuild(tmp_path):
    store = IncidentStore(str(tmp_path))
    incident = IncidentRecord.create(
        trigger_receipt_id="receipt-1",
        category=IncidentCategory.SECURITY_EVENT,
        severity=IncidentSeverity.HIGH,
        playbook_name="security-escalation",
        dedup_key="same-root-cause",
    )
    incident.opened_at = datetime.now(timezone.utc).isoformat()

    store.create(incident)
    loaded = store.get(incident.incident_id)
    assert loaded is not None
    assert loaded.trigger_receipt_id == "receipt-1"

    loaded.status = IncidentStatus.INVESTIGATING.value
    loaded.severity = IncidentSeverity.CRITICAL.value
    store.update(loaded)

    listed = store.list_incidents(status=IncidentStatus.INVESTIGATING.value)
    assert listed[0]["incident_id"] == incident.incident_id
    assert store.list_incidents(category=IncidentCategory.COST_ANOMALY.value) == []
    assert store.find_by_dedup_key("same-root-cause", window_seconds=300) == incident.incident_id
    assert store.find_by_trigger_receipt("receipt-1") == incident.incident_id
    counts = store.count_open()
    assert counts[IncidentSeverity.CRITICAL.value] == 1

    loaded.status = IncidentStatus.CLOSED.value
    store.update(loaded)
    assert store.find_by_dedup_key("same-root-cause", window_seconds=300) is None
    assert store.count_open()[IncidentSeverity.CRITICAL.value] == 0
    store.close()

    rebuilt = IncidentStore(str(tmp_path))
    assert rebuilt.get(incident.incident_id).status == IncidentStatus.CLOSED.value
    assert rebuilt.list_incidents(limit=1, offset=0)[0]["incident_id"] == incident.incident_id


def test_incident_store_handles_corrupt_index_missing_files_and_singleton(tmp_path, monkeypatch):
    incidents_dir = tmp_path / "incidents"
    incidents_dir.mkdir()
    (incidents_dir / "_index.json").write_text("{not-json", encoding="utf-8")
    old_incident = IncidentRecord.create(
        trigger_receipt_id="receipt-old",
        category=IncidentCategory.AVAILABILITY_INCIDENT,
        severity=IncidentSeverity.LOW,
        playbook_name="availability",
        dedup_key="old",
    )
    old_incident.opened_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    (incidents_dir / "old.json").write_text(json.dumps(old_incident.to_dict()), encoding="utf-8")

    store = IncidentStore(str(tmp_path))
    assert store.get("missing") is None
    assert store.find_by_dedup_key("old", window_seconds=1) is None

    monkeypatch.setattr("src.incidents.store._store_instance", None)
    assert get_incident_store() is None
    assert get_incident_store(str(tmp_path)) is store or isinstance(get_incident_store(str(tmp_path)), IncidentStore)


class _Column:
    def __init__(self, streamlit):
        self.streamlit = streamlit

    def metric(self, *args):
        self.streamlit.calls.append(("metric", args))

    def __enter__(self):
        return self.streamlit

    def __exit__(self, *_):
        return False


class _FakeStreamlit:
    def __init__(self, button_result=False, selected_month="2026-03"):
        self.calls = []
        self.button_result = button_result
        self.selected_month = selected_month

    def header(self, text):
        self.calls.append(("header", text))

    def subheader(self, text):
        self.calls.append(("subheader", text))

    def columns(self, spec):
        count = spec if isinstance(spec, int) else len(spec)
        return [_Column(self) for _ in range(count)]

    def metric(self, *args):
        self.calls.append(("metric", args))

    def divider(self):
        self.calls.append(("divider", None))

    def table(self, rows):
        self.calls.append(("table", rows))

    def info(self, text):
        self.calls.append(("info", text))

    def bar_chart(self, data):
        self.calls.append(("bar_chart", data))

    def expander(self, label):
        self.calls.append(("expander", label))
        return self

    def selectbox(self, *args, **kwargs):
        self.calls.append(("selectbox", args, kwargs))
        return self.selected_month

    def write(self, text):
        self.calls.append(("write", text))

    def caption(self, text):
        self.calls.append(("caption", text))

    def button(self, *args, **kwargs):
        self.calls.append(("button", args, kwargs))
        return self.button_result

    def success(self, text):
        self.calls.append(("success", text))

    def warning(self, text):
        self.calls.append(("warning", text))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_cost_panel_formatters_http_helpers_and_full_render(monkeypatch):
    assert cost_panel._fmt_tokens(999) == "999"
    assert cost_panel._fmt_tokens(1_500) == "1.5K"
    assert cost_panel._fmt_tokens(2_000_000) == "2.0M"
    assert cost_panel._fmt_cost(2.5) == "$2.50"
    assert cost_panel._fmt_cost(0.05) == "$0.050"
    assert cost_panel._fmt_cost(0.0004) == "$0.0004"

    payloads = {
        "/usage/summary": {
            "usage": {
                "total_cost_est": 1.25,
                "total_tokens_est": 1234,
                "total_requests": 5,
                "by_model": {"local-llm": {"requests": 3, "tokens": 1000, "cost": 0}},
            }
        },
        "/usage/monthly": {
            "monthly": {
                "month": "2026-04",
                "total_cost": 2.5,
                "total_tokens": 2500,
                "total_requests": 7,
                "by_model": {"gemini": {"requests": 2, "tokens": 1500, "cost": 2.5}},
                "by_day": {
                    "2026-04-01": {"requests": 2, "tokens": 1000, "cost": 1.0},
                    "2026-04-02": {"requests": 5, "tokens": 1500, "cost": 1.5},
                },
            },
            "available_months": ["2026-03", "2026-04"],
        },
        "/usage/savings": {"savings": {"estimated_savings": 4.5}},
    }

    def fake_get(url, params=None, timeout=10):
        path = url.replace(cost_panel._GATEWAY_URL, "")
        if params and params.get("month"):
            return types.SimpleNamespace(
                status_code=200,
                json=lambda: {"monthly": {"total_requests": 9, "total_tokens": 9000, "total_cost": 9.0}},
            )
        return types.SimpleNamespace(status_code=200, json=lambda: payloads[path])

    monkeypatch.setattr(cost_panel.http_requests, "get", fake_get)
    monkeypatch.setattr(
        cost_panel.http_requests,
        "post",
        lambda *_args, **_kwargs: types.SimpleNamespace(status_code=200, json=lambda: {"message": "reset"}),
    )

    st = _FakeStreamlit(button_result=True)
    cost_panel.render_cost_panel(st)
    assert ("success", "reset") in st.calls
    assert any(call[0] == "bar_chart" for call in st.calls)
    assert any(call[0] == "table" for call in st.calls)


def test_cost_panel_empty_and_failed_gateway_paths(monkeypatch):
    monkeypatch.setattr(
        cost_panel.http_requests,
        "get",
        lambda *_args, **_kwargs: types.SimpleNamespace(status_code=503, json=lambda: {}),
    )
    monkeypatch.setattr(cost_panel.http_requests, "post", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("down")))

    assert cost_panel._get("/unavailable") == {}
    assert cost_panel._post("/unavailable") == {}

    st = _FakeStreamlit(button_result=True)
    cost_panel.render_cost_panel(st)
    assert any(call == ("caption", "Only current month available.") for call in st.calls)
    assert any(call[0] == "warning" for call in st.calls)


class _QueryRequest:
    def __init__(self, **params):
        self.query_params = params


class _OAuthManager:
    def __init__(self, success=True):
        self.success = success
        self.exchanges = []
        self.revoked = False

    def exchange_code(self, code, state):
        self.exchanges.append((code, state))
        return self.success

    def get_status(self):
        return {"status": "healthy"}

    def revoke(self):
        self.revoked = True


def _bind_oauth_routes(monkeypatch, routes):
    _preserve_module_globals(
        monkeypatch,
        routes,
        (
            "_WORKSPACE_ROOT",
            "_bootstrap_model_discovery",
            "_require_request_capability",
            "error_response",
            "main_orchestrator",
            "onboarding_orch",
            "render_callback_exception_page",
            "render_callback_page",
            "verify_token",
        ),
    )
    calls = []
    snapshot = types.SimpleNamespace(
        credential_status="oauth_pending",
        state="PENDING",
        save=lambda: calls.append("snapshot_saved"),
    )
    orchestrator = types.SimpleNamespace(
        provider=None,
        _provider_name="gemini",
        active_provider_name="gemini",
        initialize_provider=lambda: setattr(orchestrator, "provider", object()),
    )

    routes.bind_gateway_globals(
        _bootstrap_model_discovery=lambda: calls.append("discovery"),
        _require_request_capability=lambda *_args, **_kwargs: None,
        error_response=lambda status, message, request_id=None: JSONResponse(
            {"error": message, "request_id": request_id},
            status_code=status,
        ),
        main_orchestrator=orchestrator,
        onboarding_orch=types.SimpleNamespace(snapshot=snapshot),
        render_callback_exception_page=lambda name: HTMLResponse(f"{name} failed", status_code=500),
        render_callback_page=lambda title, desc, status_code=200: HTMLResponse(
            f"{title}:{desc}",
            status_code=status_code,
        ),
        verify_token=lambda request: True,
    )
    return calls, snapshot, orchestrator


@pytest.mark.asyncio
async def test_gateway_oauth_callbacks_cover_success_and_failure_paths(monkeypatch):
    from src.core import gateway_oauth_routes as routes

    calls, snapshot, orchestrator = _bind_oauth_routes(monkeypatch, routes)

    assert (await routes.oauth_anthropic_callback(_QueryRequest(error="denied"))).status_code == 400
    assert (await routes.oauth_anthropic_callback(_QueryRequest())).status_code == 400

    anthropic_manager = _OAuthManager(success=True)
    monkeypatch.setitem(
        sys.modules,
        "oauth_token_manager",
        _fake_module(get_oauth_manager=lambda: anthropic_manager),
    )
    monkeypatch.setitem(
        sys.modules,
        "onboarding_snapshot",
        _fake_module(OnboardingState=types.SimpleNamespace(READY="READY")),
    )
    response = await routes.oauth_anthropic_callback(_QueryRequest(code="code", state="state"))
    assert response.status_code == 200
    assert anthropic_manager.exchanges == [("code", "state")]
    assert orchestrator.provider is not None
    assert snapshot.credential_status == "verified"
    assert "discovery" in calls

    failing_manager = _OAuthManager(success=False)
    monkeypatch.setitem(
        sys.modules,
        "oauth_token_manager",
        _fake_module(get_oauth_manager=lambda: failing_manager),
    )
    assert (await routes.oauth_anthropic_callback(_QueryRequest(code="bad", state="state"))).status_code == 400

    monkeypatch.setitem(
        sys.modules,
        "oauth_token_manager",
        _fake_module(get_oauth_manager=lambda: None),
    )
    assert (await routes.oauth_anthropic_callback(_QueryRequest(code="bad", state="state"))).status_code == 500


@pytest.mark.asyncio
async def test_gateway_codex_and_google_oauth_routes(monkeypatch):
    from src.core import gateway_oauth_routes as routes

    calls, _, orchestrator = _bind_oauth_routes(monkeypatch, routes)
    saved_config = {}
    codex_manager = _OAuthManager(success=True)
    google_manager = _OAuthManager(success=True)

    monkeypatch.setitem(
        sys.modules,
        "openai_codex_oauth_manager",
        _fake_module(get_openai_codex_manager=lambda: codex_manager),
    )
    monkeypatch.setitem(
        sys.modules,
        "providers.api",
        _fake_module(
            _read_current_config=lambda: {},
            _save_config=lambda cfg: saved_config.update(cfg),
            _update_env_file=lambda *_: calls.append("env_updated"),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "onboarding_snapshot",
        _fake_module(OnboardingState=types.SimpleNamespace(READY="READY")),
    )

    orchestrator.provider = None
    orchestrator._provider_name = "gemini"
    response = await routes.oauth_codex_callback(_QueryRequest(code="code", state="state"))
    assert response.status_code == 200
    assert saved_config["active_provider"] == "openai-codex"
    assert ("code", "state") in codex_manager.exchanges

    monkeypatch.setitem(
        sys.modules,
        "openai_codex_oauth_manager",
        _fake_module(get_openai_codex_manager=lambda: _OAuthManager(success=False)),
    )
    assert (await routes.oauth_codex_callback(_QueryRequest(code="bad", state="state"))).status_code == 400
    assert (await routes.oauth_codex_callback(_QueryRequest(error="denied"))).status_code == 400

    monkeypatch.setitem(
        sys.modules,
        "google_oauth_manager",
        _fake_module(get_google_oauth_manager=lambda: google_manager),
    )
    assert (await routes.google_oauth_callback(_QueryRequest(code="code", state="state"))).status_code == 200
    assert google_manager.exchanges == [("code", "state")]

    monkeypatch.setitem(
        sys.modules,
        "google_oauth_manager",
        _fake_module(get_google_oauth_manager=lambda: _OAuthManager(success=False)),
    )
    assert (await routes.google_oauth_callback(_QueryRequest(code="bad", state="state"))).status_code == 400
    assert (await routes.google_oauth_callback(_QueryRequest())).status_code == 400


@pytest.mark.asyncio
async def test_gateway_google_oauth_admin_endpoints_and_workspace_files(tmp_path, monkeypatch):
    from src.core import gateway_oauth_routes as routes

    _bind_oauth_routes(monkeypatch, routes)
    manager = _OAuthManager(success=True)
    monkeypatch.setitem(
        sys.modules,
        "google_oauth_manager",
        _fake_module(get_google_oauth_manager=lambda: manager),
    )
    monkeypatch.setattr(routes, "_WORKSPACE_ROOT", tmp_path, raising=False)
    monkeypatch.setitem(sys.modules, "feature_flags", _fake_module(FEATURE_GOOGLE_OAUTH=True))

    status = await routes.google_oauth_status(_QueryRequest())
    assert status["status"] == "healthy"
    assert status["feature_enabled"] is True
    revoked = await routes.google_oauth_revoke(_QueryRequest())
    assert revoked["status"] == "revoked"
    assert manager.revoked is True

    monkeypatch.setitem(
        sys.modules,
        "google_oauth_manager",
        _fake_module(get_google_oauth_manager=lambda: None),
    )
    assert (await routes.google_oauth_revoke(_QueryRequest())).status_code == 500

    routes.bind_gateway_globals(
        _require_request_capability=lambda *_args, **kwargs: JSONResponse({"error": "no"}, status_code=403),
    )
    assert (await routes.google_oauth_status(_QueryRequest())).status_code == 403

    routes.bind_gateway_globals(
        verify_token=lambda request: False,
        error_response=lambda status, message, request_id=None: JSONResponse(
            {"error": message, "request_id": request_id},
            status_code=status,
        ),
    )
    assert (await routes.serve_workspace_file("report.txt", _QueryRequest())).status_code == 401

    routes.bind_gateway_globals(verify_token=lambda request: True)
    assert (await routes.serve_workspace_file("../secret.txt", _QueryRequest())).status_code == 403
    assert (await routes.serve_workspace_file("missing.txt", _QueryRequest())).status_code == 404

    report = tmp_path / "report.txt"
    report.write_text("hello", encoding="utf-8")
    response = await routes.serve_workspace_file("report.txt", _QueryRequest())
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("inline")


class _Instrument:
    def __init__(self, name):
        self.name = name
        self.adds = []
        self.records = []
        self.sets = []

    def add(self, value, attrs=None):
        self.adds.append((value, attrs or {}))

    def record(self, value):
        self.records.append(value)

    def set(self, value):
        self.sets.append(value)


class _Meter:
    def __init__(self):
        self.instruments = {}

    def _make(self, name):
        instrument = _Instrument(name)
        self.instruments[name] = instrument
        return instrument

    def create_counter(self, name, **_):
        return self._make(name)

    def create_up_down_counter(self, name, **_):
        return self._make(name)

    def create_histogram(self, name, **_):
        return self._make(name)

    def create_gauge(self, name, **_):
        return self._make(name)


def test_observability_metrics_records_direct_and_receipt_driven_updates(monkeypatch):
    from src.observability import metrics

    _preserve_module_globals(
        monkeypatch,
        metrics,
        (
            "_actions_blocked",
            "_actions_total",
            "_cost_usd_rate",
            "_cost_usd_total",
            "_hive_active_agents",
            "_kill_switches_active",
            "_mcp_tool_calls",
            "_meter",
            "_receipts_chain_lag",
            "_soul_version_changes",
            "_t3_approvals_pending",
            "_t3_approvals_response_time",
            "_trust_tier_distribution",
        ),
    )

    # No-op before initialization.
    metrics.record_action("noop", 0)
    metrics.record_blocked_action("none")
    metrics.record_cost(1.0)

    meter = _Meter()
    metrics.init_metrics(meter)

    metrics.record_action("tool", 2)
    metrics.record_blocked_action("SOUL_DENIED")
    metrics.record_kill_switch_change(1)
    metrics.record_t3_pending_change(1)
    metrics.record_t3_response_time(42.5)
    metrics.record_soul_version_change()
    metrics.record_trust_tier_change(1, 3)
    metrics.record_cost(0.25, provider="gemini", model="flash")
    metrics.record_cost(0.1)
    metrics.set_cost_rate(1.5)
    metrics.record_mcp_call("server", "tool", "success")
    metrics.record_hive_agent_change(2)
    metrics.set_chain_lag(99)

    assert meter.instruments["lancelot.actions.total"].adds[-1] == (
        1,
        {"risk_tier": "T2", "receipt_type": "tool"},
    )
    assert meter.instruments["lancelot.actions.blocked"].adds[-1] == (1, {"block_reason": "SOUL_DENIED"})
    assert meter.instruments["lancelot.kill_switches.active"].adds[-1] == (1, {})
    assert meter.instruments["lancelot.t3_approvals.pending"].adds[-1] == (1, {})
    assert meter.instruments["lancelot.t3_approvals.response_time_ms"].records[-1] == 42.5
    assert meter.instruments["lancelot.soul.version_changes"].adds[-1] == (1, {})
    assert meter.instruments["lancelot.trust_ledger.tier_distribution"].adds[-1] == (3, {"tier": "T1"})
    assert meter.instruments["lancelot.cost.usd_total"].adds[-2] == (
        0.25,
        {"provider": "gemini", "model": "flash"},
    )
    assert meter.instruments["lancelot.cost.usd_rate"].sets == [1.5]
    assert meter.instruments["lancelot.mcp.tool_calls"].adds[-1] == (
        1,
        {"server_id": "server", "tool_name": "tool", "status": "success"},
    )
    assert meter.instruments["lancelot.hive.active_agents"].adds[-1] == (2, {})
    assert meter.instruments["lancelot.receipts.chain_lag_ms"].sets == [99]

    for receipt in [
        {"action_type": "kill_switch_issued", "tier": 3},
        {"action_type": "kill_switch_lifted", "tier": 3},
        {"action_type": "t3_approval_request", "tier": 3},
        {"action_type": "t3_approved", "tier": 3, "duration_ms": 10},
        {"action_type": "t3_rejected", "tier": 3, "duration_ms": 11},
        {"action_type": "soul_updated", "tier": 2},
        {"action_type": "soul_version_pinned", "tier": 2},
        {"action_type": "mcp_tool_call", "tier": 2, "inputs": {"server_id": "s", "tool_name": "t"}},
        {"action_type": "mcp_tool_blocked", "tier": 2, "inputs": {"server_id": "s", "tool_name": "t"}},
        {"action_type": "agent_deployed", "tier": 1},
        {"action_type": "agent_stopped", "tier": 1},
        {"action_type": "blocked_network", "status": "failure", "tier": 2},
        {"action_type": "task_execution", "tier": 1, "outputs": {"cost_usd": "0.33", "provider": "p", "model": "m"}},
        {"action_type": "task_execution", "tier": 1, "outputs": {"cost_usd": "bad"}},
    ]:
        metrics.update_metrics_from_receipt(receipt)

    assert meter.instruments["lancelot.kill_switches.active"].adds[-2:] == [(1, {}), (-1, {})]
    assert meter.instruments["lancelot.t3_approvals.pending"].adds[-3:] == [(1, {}), (-1, {}), (-1, {})]
    assert meter.instruments["lancelot.t3_approvals.response_time_ms"].records[-2:] == [10.0, 11.0]
    assert meter.instruments["lancelot.mcp.tool_calls"].adds[-2:] == [
        (1, {"server_id": "s", "tool_name": "t", "status": "success"}),
        (1, {"server_id": "s", "tool_name": "t", "status": "blocked"}),
    ]
    assert meter.instruments["lancelot.hive.active_agents"].adds[-2:] == [(1, {}), (-1, {})]
    assert meter.instruments["lancelot.cost.usd_total"].adds[-1] == (
        0.33,
        {"provider": "p", "model": "m"},
    )


def test_host_execution_provider_run_and_file_operations(tmp_path, monkeypatch):
    from src.tools.contracts import ExecResult, ProviderState
    from src.tools.providers.host_execution import HostExecConfig, HostExecutionProvider

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = HostExecutionProvider(
        HostExecConfig(command_allowlist=[sys.executable], max_stdout_chars=5, max_stderr_chars=5),
        workspace=str(workspace),
    )

    health = provider.health_check()
    assert health.state == ProviderState.HEALTHY
    assert "No container isolation" in health.metadata["warning"]

    denied = provider.run("rm -rf /", cwd=str(workspace))
    assert denied.exit_code == 126
    assert "blocked" in denied.stderr

    not_allowed = provider.run("git status", cwd=str(workspace))
    assert not_allowed.exit_code == 126
    assert "allowlist" in not_allowed.stderr

    outside = provider.run([sys.executable, "-V"], cwd=str(tmp_path))
    assert outside.exit_code == 126
    assert "outside workspace" in outside.stderr

    success = provider.run([sys.executable, "-c", "print('abcdef')"], cwd=str(workspace))
    assert success.exit_code == 0
    assert success.truncated is True
    assert success.stdout.startswith("abcde")

    def raise_timeout(*_, **__):
        raise provider.subprocess.TimeoutExpired(["python"], 1) if hasattr(provider, "subprocess") else __import__("subprocess").TimeoutExpired(["python"], 1)

    monkeypatch.setattr("src.tools.providers.host_execution.subprocess.run", raise_timeout)
    timeout = provider.run([sys.executable, "-c", "print('slow')"], cwd=str(workspace), timeout_s=1)
    assert timeout.exit_code == 124
    assert timeout.timed_out is True

    monkeypatch.setattr(
        "src.tools.providers.host_execution.subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    error = provider.run([sys.executable, "-c", "print('bad')"], cwd=str(workspace))
    assert error.exit_code == 1
    assert "boom" in error.stderr

    file_path = workspace / "notes" / "a.txt"
    change = provider.write(str(file_path), "hello")
    assert change.action == "created"
    assert provider.read(str(file_path)) == "hello"
    modified = provider.write(str(file_path), "updated", atomic=False)
    assert modified.action == "modified"
    assert "notes" in provider.list(str(workspace))
    assert provider.list(str(workspace), recursive=True) == ["notes\\a.txt"] or provider.list(str(workspace), recursive=True) == ["notes/a.txt"]
    deleted = provider.delete(str(file_path))
    assert deleted.action == "deleted"
    assert provider.delete(str(file_path)).action == "error"
    assert provider.read(str(tmp_path / "outside.txt")).startswith("Error:")
    assert provider.list(str(tmp_path))[0].startswith("Error:")

    assert provider._is_denied_command("mkfs /dev/sda") is True
    assert provider._is_allowed_command(f"{sys.executable} -V") is True
    assert provider._is_allowed_command("") is False
    assert provider._bound_output("abcdef", 3) == ("abc\n... (truncated)", True)
    assert provider._prepare_command([])[0] == "Empty command"
    assert provider._prepare_command("")[0] == "Empty command"


def test_host_execution_provider_repo_operations_and_patch_paths(tmp_path):
    from src.tools.contracts import ExecResult
    from src.tools.providers.host_execution import HostExecutionProvider

    workspace = tmp_path / "repo"
    workspace.mkdir()
    tracked = workspace / "tracked.txt"
    tracked.write_text("old\n", encoding="utf-8")

    class FakeHostExecutionProvider(HostExecutionProvider):
        def __init__(self):
            super().__init__(workspace=str(workspace))
            self.commands = []
            self.apply_should_fail = False

        def run(self, command, cwd, **kwargs):
            self.commands.append(command)
            joined = " ".join(command) if isinstance(command, list) else command
            if joined == "git status --porcelain":
                return ExecResult(0, "MM modified.txt\nA  added.txt\nD  deleted.txt\n?? new.txt\n", "", 0, command=joined, working_dir=cwd)
            if joined.startswith("git diff"):
                return ExecResult(0, "diff output", "", 0, command=joined, working_dir=cwd)
            if joined == "git apply --check .tmp_patch":
                return ExecResult(0, "", "", 0, command=joined, working_dir=cwd)
            if joined == "git apply .tmp_patch":
                if self.apply_should_fail:
                    return ExecResult(1, "", "patch failed", 0, command=joined, working_dir=cwd)
                tracked.write_text("new\n", encoding="utf-8")
                return ExecResult(0, "", "", 0, command=joined, working_dir=cwd)
            if joined.startswith("git commit"):
                return ExecResult(0, "", "", 0, command=joined, working_dir=cwd)
            if joined == "git rev-parse HEAD":
                return ExecResult(0, "abc123\n", "", 0, command=joined, working_dir=cwd)
            if joined.startswith("git checkout") or joined.startswith("git branch") or joined.startswith("git add"):
                return ExecResult(0, "", "", 0, command=joined, working_dir=cwd)
            return ExecResult(1, "", "no match", 0, command=joined, working_dir=cwd)

    provider = FakeHostExecutionProvider()
    assert provider.status(str(workspace)) == {
        "modified": ["modified.txt"],
        "added": ["added.txt"],
        "deleted": ["deleted.txt"],
        "untracked": ["new.txt"],
    }
    assert provider.diff(str(workspace)) == "diff output"
    assert provider.diff(str(workspace), ref="HEAD~1") == "diff output"
    assert provider.commit(str(workspace), "msg", files=["tracked.txt"]) == "abc123"
    assert provider.commit(str(workspace), "msg") == "Error: host_execution.commit requires an explicit file list"
    assert provider.branch(str(workspace), "feature") is True
    assert provider.branch(str(workspace), "feature", checkout=False) is True
    assert provider.checkout(str(workspace), "main") is True

    patch = "--- a/tracked.txt\n+++ b/tracked.txt\n@@\n-old\n+new\n"
    dry_run = provider.apply_patch(str(workspace), patch, dry_run=True)
    assert dry_run.success is True
    applied = provider.apply_patch(str(workspace), patch)
    assert applied.success is True
    assert applied.files_changed[0].path == "tracked.txt"
    assert provider.apply_patch(str(workspace), "../bad").success is False

    provider.apply_should_fail = True
    failed = provider.apply_patch(str(workspace), patch)
    assert failed.success is False
    assert failed.rejected_hunks == ["patch failed"]

    assert provider._extract_files_from_patch("+++ b/a.txt\n+++ /dev/null\n+++ plain.txt\tmeta") == [
        "a.txt",
        "plain.txt",
    ]


def test_host_execution_apply_diff_success_and_failure(tmp_path, monkeypatch):
    from src.tools.providers.host_execution import HostExecutionProvider

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "file.txt"
    target.write_text("old\n", encoding="utf-8")
    provider = HostExecutionProvider(workspace=str(workspace))

    def successful_patch(command, cwd, input, **_):
        target.write_text("new\n", encoding="utf-8")
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr("src.tools.providers.host_execution.subprocess.run", successful_patch)
    assert provider.apply_diff(str(target), "patch").action == "modified"

    monkeypatch.setattr(
        "src.tools.providers.host_execution.subprocess.run",
        lambda *_args, **_kwargs: types.SimpleNamespace(returncode=1),
    )
    assert provider.apply_diff(str(target), "patch").action == "error"

    monkeypatch.setattr(
        "src.tools.providers.host_execution.subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("patch missing")),
    )
    assert provider.apply_diff(str(target), "patch").action == "error"


class _FakeOpenAIResponse:
    def __init__(self, content="text", tool_calls=None, prompt_tokens=3, completion_tokens=4):
        self.choices = [
            types.SimpleNamespace(
                message=types.SimpleNamespace(content=content, tool_calls=tool_calls or [])
            )
        ]
        self.usage = types.SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )


class _FakeOpenAIClient:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls = []
        self.model_ids = ["gpt-4o-mini", "text-embedding-3-small", "o3"]
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create_completion)
        )
        self.models = types.SimpleNamespace(
            list=lambda: [types.SimpleNamespace(id=model_id) for model_id in self.model_ids],
            retrieve=lambda model_id: types.SimpleNamespace(id=model_id),
        )
        _FakeOpenAIClient.instances.append(self)

    def _create_completion(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeOpenAIResponse()


def _install_fake_openai(monkeypatch):
    _FakeOpenAIClient.instances = []
    monkeypatch.setitem(sys.modules, "openai", _fake_module(OpenAI=_FakeOpenAIClient))


def test_openai_provider_client_generates_messages_tools_and_models(monkeypatch):
    from providers.tool_schema import NormalizedToolDeclaration
    from src.core.providers.openai_client import OpenAIProviderClient

    _install_fake_openai(monkeypatch)
    client = OpenAIProviderClient(api_key="key", base_url="https://api.example")
    assert client.provider_name == "openai"
    assert _FakeOpenAIClient.instances[-1].kwargs == {
        "api_key": "key",
        "base_url": "https://api.example",
    }

    result = client.generate("gpt-4o-mini", [{"role": "user", "content": "hi"}], system_instruction="system")
    assert result.text == "text"
    assert result.usage == {"input_tokens": 3, "output_tokens": 4}
    assert _FakeOpenAIClient.instances[-1].calls[-1]["messages"][0] == {"role": "system", "content": "system"}

    declaration = NormalizedToolDeclaration(
        name="lookup",
        description="lookup data",
        parameters={"type": "object", "properties": {}},
    )
    tool_result = client.generate_with_tools(
        "gpt-4o-mini",
        [{"role": "user", "content": "use tool"}],
        "system",
        [declaration],
        tool_config={"mode": "ANY"},
    )
    assert tool_result.text == "text"
    assert _FakeOpenAIClient.instances[-1].calls[-1]["tool_choice"] == "required"
    assert _FakeOpenAIClient.instances[-1].calls[-1]["tools"][0]["type"] == "function"

    client.generate_with_tools(
        "gpt-4o-mini",
        [{"role": "user", "content": "no tools"}],
        "",
        [{"type": "function"}],
        tool_config={"mode": "NONE"},
    )
    assert _FakeOpenAIClient.instances[-1].calls[-1]["tool_choice"] == "none"

    assert client.build_tool_response_message([("call-1", "lookup", {"ok": True})]) == [
        {"role": "tool", "tool_call_id": "call-1", "content": "{'ok': True}"}
    ]
    image_msg = client.build_user_message("caption", images=[(b"abc", "image/png")])
    assert image_msg["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert client.build_user_message("plain") == {"role": "user", "content": "plain"}

    models = client.list_models()
    assert [model.id for model in models] == ["gpt-4o-mini", "o3"]
    assert models[0].capability_tier == "fast"
    assert models[1].capability_tier == "deep"
    assert client.validate_model("gpt-4o-mini") is True


def test_openai_provider_client_codex_refresh_retry_and_parse_paths(monkeypatch):
    from providers.base import ProviderAuthError
    from src.core.providers.openai_client import OpenAIProviderClient

    _install_fake_openai(monkeypatch)
    client = OpenAIProviderClient(auth_token="old-token")
    assert client.provider_name == "openai-codex"
    assert _FakeOpenAIClient.instances[-1].kwargs["api_key"] == "old-token"

    client.update_auth_token("new-token")
    assert client._auth_token == "new-token"
    assert _FakeOpenAIClient.instances[-1].kwargs["api_key"] == "new-token"

    assert client.validate_model("gpt-5.4") is True
    assert client.validate_model("gpt-5.1-codex") is False
    assert client.validate_model("not-real") is False
    assert [model.id for model in client.list_models()]

    tool_call = types.SimpleNamespace(
        id="call-1",
        function=types.SimpleNamespace(name="tool", arguments='{"x": 1}'),
    )
    parsed = client._parse_response(_FakeOpenAIResponse(content=None, tool_calls=[tool_call], prompt_tokens=None, completion_tokens=None))
    assert parsed.tool_calls[0].args == {"x": 1}
    assert parsed.usage == {"input_tokens": 0, "output_tokens": 0}

    invalid_tool_call = types.SimpleNamespace(
        id="call-2",
        function=types.SimpleNamespace(name="tool", arguments="{bad"),
    )
    assert client._parse_response(_FakeOpenAIResponse(tool_calls=[invalid_tool_call])).tool_calls[0].args == {"raw": "{bad"}

    monkeypatch.setattr(client, "_try_oauth_refresh", lambda: True)
    attempts = {"count": 0}

    def auth_then_success():
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("401 unauthorized")
        return "ok"

    assert client._call_with_retry(auth_then_success, max_retries=1, base_delay=0) == "ok"

    monkeypatch.setattr(client, "_try_oauth_refresh", lambda: False)
    try:
        client._call_with_retry(lambda: (_ for _ in ()).throw(RuntimeError("401 unauthorized")), max_retries=0)
    except ProviderAuthError as exc:
        assert exc.provider == "openai-codex"
    else:
        raise AssertionError("expected ProviderAuthError")

    api_key_client = OpenAIProviderClient(api_key="key")
    try:
        api_key_client._call_with_retry(lambda: (_ for _ in ()).throw(RuntimeError("invalid api key")), max_retries=0)
    except ProviderAuthError as exc:
        assert exc.provider == "openai"
    else:
        raise AssertionError("expected ProviderAuthError")

    sleeps = []
    monkeypatch.setattr("src.core.providers.openai_client.time.sleep", lambda delay: sleeps.append(delay))
    retry_attempts = {"count": 0}

    def transient_then_success():
        retry_attempts["count"] += 1
        if retry_attempts["count"] == 1:
            raise RuntimeError("503 service_unavailable")
        return "recovered"

    assert api_key_client._call_with_retry(transient_then_success, max_retries=1, base_delay=0.5) == "recovered"
    assert sleeps == [0.5]

    monkeypatch.setitem(
        sys.modules,
        "openai_codex_oauth_manager",
        _fake_module(get_openai_codex_manager=lambda: types.SimpleNamespace(get_valid_token=lambda: "refreshed")),
    )
    refresh_client = OpenAIProviderClient(auth_token="before")
    assert refresh_client._try_oauth_refresh() is True
    assert refresh_client._auth_token == "refreshed"


def _json_response_body(response):
    return json.loads(response.body.decode("utf-8"))


@pytest.mark.asyncio
async def test_setup_api_recovery_endpoints_and_filesystem_paths(tmp_path, monkeypatch):
    from src.core import setup_api

    _preserve_module_globals(
        monkeypatch,
        setup_api,
        (
            "_audit_logger",
            "_connector_vault",
            "_connector_vault_config_path",
            "_connector_vault_error",
            "_data_dir",
            "_receipt_service",
            "_startup_time",
            "_verify_request",
        ),
    )

    audit_calls = []
    receipt_service = types.SimpleNamespace()

    class AuditLogger:
        def log_event(self, *args, **kwargs):
            audit_calls.append((args, kwargs))

    class VaultHealth:
        def to_dict(self):
            return {"status": "healthy"}

    class VaultEntry:
        type = "api_key"
        created_at = "now"

    class Vault:
        def __init__(self):
            self._entries = {"api:key": VaultEntry()}
            self.deleted = []

        def health_snapshot(self, last_error=None):
            return VaultHealth()

        def list_keys(self):
            return list(self._entries.keys())

        def list_entry_metadata(self):
            return [
                {"key": key, "type": entry.type, "created_at": entry.created_at}
                for key, entry in self._entries.items()
            ]

        def retrieve(self, key):
            return "abcd1234wxyz"

        def delete(self, key):
            if key in self._entries:
                self.deleted.append(key)
                del self._entries[key]
                return True
            return False

    vault = Vault()
    setup_api.init_setup_api(
        data_dir=str(tmp_path),
        startup_time=1.0,
        audit_logger=AuditLogger(),
        connector_vault=vault,
        connector_vault_config_path=str(tmp_path / "vault.yaml"),
        receipt_service=receipt_service,
        verify_request=lambda request: True,
    )

    monkeypatch.setattr(setup_api, "read_current_version", lambda: "1.2.3")
    info = await setup_api.system_info()
    assert info["version"] == "1.2.3"
    assert info["data_dir"]["path"] == str(tmp_path)

    assert (await setup_api.restart_container(_QueryRequest(), setup_api.ConfirmRequest(confirm=False))).status_code == 400
    assert (await setup_api.shutdown_container(_QueryRequest(), setup_api.ConfirmRequest(confirm=False))).status_code == 400

    class Timer:
        def __init__(self, delay, fn):
            self.delay = delay
            self.fn = fn

        def start(self):
            return None

    stop_calls = []
    monkeypatch.setitem(
        sys.modules,
        "subsystem_manager",
        _fake_module(subsystem_manager=types.SimpleNamespace(stop_all=lambda: stop_calls.append("stopped"))),
    )
    import threading

    monkeypatch.setattr(threading, "Timer", Timer)
    monkeypatch.setattr("src.core.setup_api.os._exit", lambda code: None)

    restart = await setup_api.restart_container(_QueryRequest(), setup_api.ConfirmRequest(confirm=True))
    shutdown = await setup_api.shutdown_container(_QueryRequest(), setup_api.ConfirmRequest(confirm=True))
    assert restart["status"] == "restarting"
    assert shutdown["status"] == "shutting_down"
    assert stop_calls == ["stopped", "stopped"]

    (tmp_path / "audit.log").write_text("one\ntwo\nthree\n", encoding="utf-8")
    assert (await setup_api.get_logs(lines=2, file="audit"))["lines"] == ["two", "three"]
    assert (await setup_api.get_logs(file="missing")).status_code == 400

    assert await setup_api.vault_status() == {"status": "healthy"}
    keys = await setup_api.list_vault_keys()
    assert keys["total"] == 1
    masked = await setup_api.list_vault_masked()
    assert masked["keys"][0]["masked_value"] == "abcd****wxyz"
    assert setup_api._mask_value("short") == "****"
    deleted = await setup_api.delete_vault_key("api:key", _QueryRequest())
    assert deleted == {"status": "deleted", "key": "api:key"}
    assert (await setup_api.delete_vault_key("missing", _QueryRequest())).status_code == 404

    monkeypatch.setitem(
        sys.modules,
        "src.connectors.vault",
        _fake_module(
            CredentialVault=types.SimpleNamespace(
                reset_storage=lambda config_path: {"archived_files": ["vault.db"], "archive_dir": "archive"},
                inspect_health=lambda **_: VaultHealth(),
            )
        ),
    )
    reset = await setup_api.reset_connector_vault(
        _QueryRequest(),
        setup_api.VaultResetRequest(confirm=True, confirmation_text="RESET CONNECTOR VAULT"),
    )
    assert reset["status"] == "resetting"
    assert reset["archived_files"] == ["vault.db"]
    assert (
        await setup_api.reset_connector_vault(
            _QueryRequest(),
            setup_api.VaultResetRequest(confirm=False, confirmation_text=""),
        )
    ).status_code == 400

    monkeypatch.setitem(
        sys.modules,
        "feature_flags",
        _fake_module(
            reload_flags=lambda: audit_calls.append(("reload_flags", {})),
            clear_persisted_flag_state=lambda: audit_calls.append(("clear_flags", {})),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "gateway",
        _fake_module(scheduler_service=types.SimpleNamespace(register_from_config=lambda: 2)),
    )
    monkeypatch.setitem(
        sys.modules,
        "connectors.registry",
        _fake_module(ConnectorRegistry=lambda **_: object()),
    )
    reload_result = await setup_api.reload_config(_QueryRequest())
    assert reload_result["status"] == "reloaded"
    assert reload_result["results"]["scheduler"] == "reloaded (2 jobs)"

    (tmp_path / "core_blocks.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".flag_state.json").write_text("{}", encoding="utf-8")
    scheduler_dir = tmp_path / "scheduler"
    scheduler_dir.mkdir()
    (scheduler_dir / "jobs.json").write_text("{}", encoding="utf-8")
    soul_dir = tmp_path / "soul"
    soul_dir.mkdir()
    (soul_dir / "active.yaml").write_text("mission: test", encoding="utf-8")
    export = await setup_api.export_backup(_QueryRequest())
    assert export.status_code == 200
    assert export.media_type == "application/zip"

    (tmp_path / "memory.db").write_text("db", encoding="utf-8")
    (tmp_path / "memory.sqlite").write_text("db", encoding="utf-8")
    purged = await setup_api.purge_memory(_QueryRequest(), setup_api.ConfirmRequest(confirm=True))
    assert sorted(purged["purged_files"]) == ["core_blocks.json", "memory.db", "memory.sqlite"]
    assert (await setup_api.purge_memory(_QueryRequest(), setup_api.ConfirmRequest(confirm=False))).status_code == 400

    (tmp_path / ".flag_state.json").write_text("{}", encoding="utf-8")
    reset_flags = await setup_api.reset_flags(_QueryRequest(), setup_api.ConfirmRequest(confirm=True))
    assert reset_flags["status"] == "reset"
    assert (await setup_api.reset_flags(_QueryRequest(), setup_api.ConfirmRequest(confirm=False))).status_code == 400

    (tmp_path / "delete-me.txt").write_text("x", encoding="utf-8")
    assert (
        await setup_api.factory_reset(
            _QueryRequest(),
            setup_api.FactoryResetRequest(confirm=False, confirmation_text=""),
        )
    ).status_code == 400
    factory = await setup_api.factory_reset(
        _QueryRequest(),
        setup_api.FactoryResetRequest(confirm=True, confirmation_text="RESET"),
    )
    assert factory["status"] == "reset_complete"
    assert not (tmp_path / "delete-me.txt").exists()


def test_setup_api_auth_and_fallback_helpers(monkeypatch):
    from fastapi import HTTPException
    from src.core import setup_api

    _preserve_module_globals(monkeypatch, setup_api, ("_audit_logger", "_verify_request"))
    monkeypatch.setattr(setup_api, "_verify_request", None, raising=False)
    try:
        setup_api._require_authenticated_request(_QueryRequest())
    except HTTPException as exc:
        assert exc.status_code == 503
    else:
        raise AssertionError("expected HTTPException")

    monkeypatch.setattr(setup_api, "_verify_request", lambda request: False, raising=False)
    try:
        setup_api._require_authenticated_request(_QueryRequest())
    except HTTPException as exc:
        assert exc.status_code == 401
    else:
        raise AssertionError("expected HTTPException")

    monkeypatch.setattr(setup_api, "_verify_request", lambda request: True, raising=False)
    setup_api._require_authenticated_request(_QueryRequest())

    assert _json_response_body(setup_api._safe_error(418, "teapot")) == {"error": "teapot", "status": 418}
    assert setup_api._resolve_audit_user(None) == "WarRoom"

    class BadAudit:
        def log_event(self, *_, **__):
            raise RuntimeError("audit down")

    monkeypatch.setattr(setup_api, "_audit_logger", BadAudit(), raising=False)
    setup_api._audit("EVENT", "details", request=None)


class _URLResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _install_feature_flags_module(monkeypatch, **overrides):
    state = {
        "FEATURE_SOUL": True,
        "FEATURE_SKILLS": True,
        "FEATURE_TOOLS_FABRIC": True,
        "FEATURE_TOOLS_HOST_BRIDGE": False,
        "FEATURE_HOST_WRITE_COMMANDS": False,
        "FEATURE_TOOLS_UAB": False,
        "RESTART_REQUIRED_FLAGS": frozenset({"FEATURE_SOUL"}),
    }
    state.update(overrides)

    def toggle_flag(name):
        state[name] = not state[name]
        setattr(module, name, state[name])
        return state[name]

    def set_flag(name, value):
        state[name] = value
        setattr(module, name, value)

    module = _fake_module(
        toggle_flag=toggle_flag,
        set_flag=set_flag,
        **state,
    )
    monkeypatch.setitem(sys.modules, "feature_flags", module)
    return module


@pytest.mark.asyncio
async def test_flags_api_feature_toggles_host_agent_and_uab_paths(tmp_path, monkeypatch):
    from src.core import flags_api

    _preserve_module_globals(
        monkeypatch,
        flags_api,
        ("_audit_logger", "_network_allowlist", "HOST_AGENT_URL", "UAB_DAEMON_URL", "WRITE_COMMANDS_PATH"),
    )
    ff = _install_feature_flags_module(monkeypatch)
    audit_calls = []
    receipt_calls = []
    flags_api.init_flags_api(types.SimpleNamespace(log_event=lambda *args, **kwargs: audit_calls.append((args, kwargs))))

    monkeypatch.setitem(
        sys.modules,
        "src.core.governance_receipts",
        _fake_module(emit_governance_receipt=lambda *args, **kwargs: receipt_calls.append((args, kwargs))),
    )

    flags = await flags_api.get_flags()
    assert flags["flags"]["FEATURE_TOOLS_HOST_BRIDGE"]["has_editor"] == "host_agent"
    assert flags["flags"]["FEATURE_SOUL"]["restart_required"] is True

    class Allowlist:
        path = "allowlist.yaml"

        def __init__(self):
            self.domains = ["example.com"]

        def load_config(self):
            return {"domains": self.domains}

        def set_domains(self, domains):
            self.domains = sorted(set(domains))
            return self.domains

    allowlist = Allowlist()
    monkeypatch.setattr(flags_api, "_network_allowlist", allowlist, raising=False)
    assert (await flags_api.get_network_allowlist())["domains"] == ["example.com"]
    reloaded = []
    monkeypatch.setitem(
        sys.modules,
        "gateway",
        _fake_module(
            main_orchestrator=types.SimpleNamespace(
                network_interceptor=types.SimpleNamespace(
                    ALLOW_LIST=["old"],
                    reload_allowlist=lambda: reloaded.append("network"),
                )
            )
        ),
    )
    updated = await flags_api.update_network_allowlist(flags_api.AllowlistUpdate(domains=["b.com", "a.com", "b.com"]))
    assert updated == {"domains": ["a.com", "b.com"], "count": 2}
    assert reloaded == ["network"]

    monkeypatch.delenv("HOST_AGENT_TOKEN", raising=False)
    assert flags_api._get_host_agent_token_state() == ("", "missing")
    monkeypatch.setenv("HOST_AGENT_TOKEN", "lancelot-host-agent")
    assert flags_api._get_host_agent_token_state() == ("", "legacy_default")
    monkeypatch.setenv("HOST_AGENT_TOKEN", "secret")
    assert flags_api._get_host_agent_token_state() == ("secret", "configured")

    monkeypatch.setattr(flags_api, "HOST_AGENT_URL", "http://localhost:9111", raising=False)
    urlopen_calls = []

    def urlopen_success(request, timeout=0):
        urlopen_calls.append((getattr(request, "full_url", str(request)), timeout))
        if "health" in getattr(request, "full_url", str(request)):
            return _URLResponse(
                {
                    "platform": "Windows",
                    "platform_version": "11",
                    "hostname": "host",
                    "agent_version": "1",
                }
            )
        return _URLResponse({"ok": True})

    monkeypatch.setattr("urllib.request.urlopen", urlopen_success)
    host_status = await flags_api.get_host_agent_status()
    assert host_status["reachable"] is True
    assert host_status["auth_configured"] is True
    shutdown = await flags_api.shutdown_host_agent()
    assert shutdown["status"] == "shutdown_sent"

    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("down")))
    assert (await flags_api.get_host_agent_status())["reachable"] is False
    assert (await flags_api.shutdown_host_agent()).status_code == 502

    monkeypatch.delenv("HOST_AGENT_TOKEN", raising=False)
    assert (await flags_api.shutdown_host_agent()).status_code == 503

    write_path = tmp_path / "host_write_commands.yaml"
    monkeypatch.setattr(flags_api, "WRITE_COMMANDS_PATH", str(write_path), raising=False)
    write_path.write_text("# comment\nrm\n\n del\n", encoding="utf-8")
    assert (await flags_api.get_host_write_commands())["commands"] == ["rm", "del"]
    write_update = await flags_api.update_host_write_commands(flags_api.WriteCommandsUpdate(raw="# c\nmove\ncopy\n"))
    assert write_update == {"commands": ["move", "copy"], "count": 2}
    assert (await flags_api.get_host_write_status())["enabled"] is False
    assert (await flags_api.toggle_host_write_commands())["enabled"] is True

    class SubsystemManager:
        def __init__(self):
            self.running = False
            self.started = []
            self.stopped = []

        def get_by_flag(self, name):
            if name == "FEATURE_TOOLS_HOST_BRIDGE":
                return types.SimpleNamespace(name="host_bridge")
            return None

        def is_running(self, name):
            return self.running

        def start(self, name):
            self.running = True
            self.started.append(name)

        def stop(self, name):
            self.running = False
            self.stopped.append(name)

    subsystem_manager = SubsystemManager()
    monkeypatch.setitem(sys.modules, "subsystem_manager", _fake_module(subsystem_manager=subsystem_manager))
    monkeypatch.setenv("HOST_AGENT_TOKEN", "secret")
    monkeypatch.setattr("urllib.request.urlopen", urlopen_success)

    enabled = await flags_api.toggle_flag("FEATURE_TOOLS_HOST_BRIDGE", _QueryRequest())
    assert enabled["enabled"] is True
    assert enabled["hot_toggled"] is True
    assert enabled["agent_reachable"] is True
    assert subsystem_manager.started == ["host_bridge"]
    assert audit_calls
    assert receipt_calls

    ff.FEATURE_HOST_WRITE_COMMANDS = False
    disabled = await flags_api.toggle_flag("FEATURE_TOOLS_HOST_BRIDGE", _QueryRequest())
    assert disabled["enabled"] is False
    assert subsystem_manager.stopped == ["host_bridge"]

    unknown = await flags_api.toggle_flag("FEATURE_NOT_REAL", _QueryRequest())
    assert unknown.status_code == 400

    ff.FEATURE_TOOLS_FABRIC = False
    dep_error = await flags_api.set_flag("FEATURE_TOOLS_UAB", _QueryRequest(), value=True)
    assert dep_error.status_code == 400
    ff.FEATURE_TOOLS_FABRIC = True
    ff.FEATURE_TOOLS_HOST_BRIDGE = True
    set_result = await flags_api.set_flag("FEATURE_TOOLS_UAB", _QueryRequest(), value=True)
    assert set_result["enabled"] is True

    assert flags_api._restart_required_for_flag("FEATURE_SOUL") is True

    monkeypatch.setattr(
        flags_api,
        "_uab_rpc",
        lambda method, params=None, timeout=3: {
            "version": "1",
            "connectedApps": 2,
            "supportedFrameworks": ["electron"],
            "uptimeSeconds": 10,
            "standaloneFeatures": ["clipboard"],
            "connections": [
                {"pid": 1, "name": "App", "framework": "electron", "method": "cdp", "elementCount": 4},
                "invalid",
            ],
        },
        raising=False,
    )
    assert (await flags_api.get_uab_status())["reachable"] is True
    assert (await flags_api.get_uab_connected_apps())["apps"][0]["pid"] == 1

    store = types.SimpleNamespace(
        get_recent_receipts=lambda **_: [types.SimpleNamespace(to_dict=lambda: {"receipt_id": "r1"})],
        get_session_summaries=lambda limit=20: [{"session_id": "s1"}],
    )
    monkeypatch.setitem(
        sys.modules,
        "src.tools.receipts_uab",
        _fake_module(get_uab_receipt_store=lambda: store),
    )
    assert (await flags_api.get_uab_receipts())["receipts"] == [{"receipt_id": "r1"}]
    assert (await flags_api.get_uab_sessions())["sessions"] == [{"session_id": "s1"}]

    monkeypatch.setattr(flags_api, "_uab_rpc", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("down")), raising=False)
    assert (await flags_api.get_uab_status())["reachable"] is False
    assert (await flags_api.get_uab_connected_apps()) == {"apps": []}


def test_onboarding_provider_identity_oauth_and_api_key_paths(tmp_path, monkeypatch):
    from src.ui import onboarding

    for key in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "XAI_API_KEY",
        "NVIDIA_API_KEY",
        "LANCELOT_PROVIDER",
        "LANCELOT_AUTH_MODE",
        "LANCELOT_PROVIDER_MODE",
    ):
        monkeypatch.delenv(key, raising=False)

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    persisted = data_dir / "provider_config.json"
    persisted.write_text(json.dumps({"active_provider": "openai"}), encoding="utf-8")
    monkeypatch.setenv("LANCELOT_DATA_DIR", str(data_dir))
    assert onboarding._load_persisted_provider() == "openai"

    monkeypatch.setitem(
        sys.modules,
        "src.core.providers.codex_cli_client",
        _fake_module(has_codex_cli_auth=lambda: True),
    )
    assert onboarding._has_codex_cli_auth() is True
    monkeypatch.setitem(
        sys.modules,
        "src.core.providers.codex_cli_client",
        _fake_module(has_codex_cli_auth=lambda: (_ for _ in ()).throw(RuntimeError("missing"))),
    )
    assert onboarding._has_codex_cli_auth() is False

    orch = onboarding.OnboardingOrchestrator(data_dir=str(data_dir))
    orch.env_file = str(tmp_path / ".env")
    assert orch.state == "WELCOME"
    bonded = orch._bond_identity("Myles")
    assert "Welcome, Myles" in bonded
    assert orch.state == "FLAGSHIP_SELECTION"

    assert "Invalid selection" in orch._handle_flagship_selection("nope")
    anthropic = orch._handle_flagship_selection("anthropic")
    assert "Anthropic Selected" in anthropic
    assert orch.state == "HANDSHAKE"
    assert orch.temp_data["provider"] == "anthropic"

    monkeypatch.setitem(
        sys.modules,
        "oauth_token_manager",
        _fake_module(
            get_oauth_manager=lambda: types.SimpleNamespace(
                generate_auth_url=lambda: ("https://auth", "state"),
                get_token_status=lambda: {"configured": True},
            )
        ),
    )
    oauth_prompt = orch._handle_auth_options("oauth")
    assert "Anthropic OAuth Setup" in oauth_prompt
    assert orch.state == "ANTHROPIC_OAUTH_WAITING"
    assert "OAuth Authorized" in orch._handle_anthropic_oauth_waiting("done")
    assert orch.state == "PROVIDER_MODE_SELECTION"

    orch.temp_data["provider"] = "openai"
    orch.state = "HANDSHAKE"
    assert "Invalid key format" in orch._verify_api_key("bad")
    monkeypatch.setattr(orch, "_validate_api_key_live", lambda provider, key: {"valid": False, "error": "no"})
    assert "API Key Invalid" in orch._verify_api_key("sk-test")
    monkeypatch.setattr(orch, "_validate_api_key_live", lambda provider, key: {"valid": True, "warning": "network skipped"})
    verified = orch._verify_api_key("sk-test")
    assert "API Key Verified" in verified
    assert "network skipped" in verified
    assert orch._get_env_value("OPENAI_API_KEY") == "sk-test"

    assert "Invalid selection" in orch._handle_provider_mode("bad")
    mode_msg = orch._handle_provider_mode("api")
    assert "API mode selected" in mode_msg
    assert orch._get_env_value("LANCELOT_PROVIDER_MODE") == "api"

    orch.temp_data["provider"] = "openai-codex"
    monkeypatch.setattr(onboarding, "_has_codex_cli_auth", lambda: True)
    codex = orch._initiate_openai_codex_setup()
    assert "Codex CLI Auth Detected" in codex
    assert orch.snapshot.flagship_provider == "openai-codex"


def test_onboarding_comms_auth_final_checks_and_process_paths(tmp_path, monkeypatch):
    from src.ui import onboarding

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    orch = onboarding.OnboardingOrchestrator(data_dir=str(data_dir))
    orch.env_file = str(tmp_path / ".env")
    (data_dir / "USER.md").write_text("# User", encoding="utf-8")

    assert "not yet available" in orch._handle_comms_selection("slack")
    assert "Invalid selection" in orch._handle_comms_selection("invalid")
    assert "Telegram Selected" in orch._handle_comms_selection("telegram")
    assert orch.state == "COMMS_TELEGRAM_TOKEN"
    assert "Token Accepted" in orch._handle_telegram_token("123456:abcdefghijklmnopqrstuvwxyz")
    assert orch.state == "COMMS_TELEGRAM_CHAT"

    class Response:
        status_code = 200
        text = "{}"

        def json(self):
            return {}

    monkeypatch.setattr("requests.post", lambda *_args, **_kwargs: Response())
    handshake = orch._handle_telegram_chat("12345")
    assert "Handshake Initiated" in handshake
    code = orch.temp_data["verification_code"]
    assert "Verification Failed" in orch._verify_handshake("wrong")

    orch.temp_data.update(
        {
            "verification_code": code,
            "comms_type": "telegram",
            "telegram_token": "123456:abcdefghijklmnopqrstuvwxyz",
            "telegram_chat_id": "12345",
        }
    )
    verified = orch._verify_handshake(code)
    assert "Handshake Verified" in verified
    assert orch.state in {"AUTH_MODEL_SELECTION", "LOCAL_AUTH_SETUP", "FINAL_CHECKS", "READY"}

    assert "Invalid selection" in orch._handle_auth_model_selection("bad")
    assert "Local authentication selected" in orch._handle_auth_model_selection("local")
    assert "Username must" in orch._handle_local_auth_setup("!")
    assert "password" in orch._handle_local_auth_setup("admin").lower()
    assert "at least 8" in orch._handle_local_auth_setup("short")
    assert "Confirm" in orch._handle_local_auth_setup("long-password")
    assert "do not match" in orch._handle_local_auth_setup("different")
    assert "Confirm" in orch._handle_local_auth_setup("long-password")

    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: _URLResponse({"result": {"ok": True}}))
    local_done = orch._handle_local_auth_setup("long-password")
    assert "Local authentication configured" in local_done
    assert orch.state == "READY"
    assert "OnboardingComplete" in (data_dir / "USER.md").read_text(encoding="utf-8")

    enterprise = onboarding.OnboardingOrchestrator(data_dir=str(tmp_path / "enterprise"))
    enterprise.env_file = str(tmp_path / "enterprise.env")
    assert "Enterprise SSO selected" in enterprise._handle_auth_model_selection("oidc")
    assert "must start" in enterprise._handle_enterprise_auth_setup("issuer")
    assert "client ID" in enterprise._handle_enterprise_auth_setup("https://issuer")
    assert "required" in enterprise._handle_enterprise_auth_setup("")
    assert "client secret" in enterprise._handle_enterprise_auth_setup("client")
    assert "required" in enterprise._handle_enterprise_auth_setup("")
    assert "allowed OIDC groups" in enterprise._handle_enterprise_auth_setup("secret")
    assert "Enterprise SSO configured" in enterprise._handle_enterprise_auth_setup("open")

    cooldown = onboarding.OnboardingOrchestrator(data_dir=str(tmp_path / "cooldown"))
    cooldown.state = "COOLDOWN"
    cooldown.snapshot.cooldown_remaining = lambda: 61
    assert "System is in cooldown" in cooldown.process("User", "hello")
    monkeypatch.setattr(onboarding.recovery_commands, "try_handle", lambda text, snapshot: "RECOVERED")
    assert cooldown.process("User", "STATUS") == "RECOVERED"


def test_model_discovery_profiles_overrides_stack_and_provider_swap(tmp_path):
    from providers.base import ModelInfo
    from model_discovery import ModelDiscovery

    profiles = tmp_path / "profiles.yaml"
    profiles.write_text(
        """
profiles:
  fast-model:
    context_window: 16000
    cost_input_per_1k: 0.1
    cost_output_per_1k: 0.2
    capability_tier: fast
    supports_tools: true
  deep-model:
    context_window: 128000
    cost_input_per_1k: 1.0
    cost_output_per_1k: 2.0
    capability_tier: deep
    supports_tools: true
  cache-model:
    context_window: 8000
    cost_input_per_1k: 0.01
    cost_output_per_1k: 0.01
    capability_tier: standard
    supports_tools: false
""",
        encoding="utf-8",
    )

    class Provider:
        provider_name = "test"

        def __init__(self, models):
            self.models = models

        def list_models(self):
            return list(self.models)

    discovery = ModelDiscovery(
        Provider(
            [
                ModelInfo(id="fast-model", display_name="Fast"),
                ModelInfo(id="deep-model", display_name="Deep"),
                ModelInfo(id="cache-model", display_name="Cache"),
            ]
        ),
        profiles_path=str(profiles),
        lane_overrides={"fast": "deep-model"},
    )

    discovery.refresh()

    assert discovery.provider_name == "test"
    assert discovery.get_lane_model("fast") == "deep-model"
    assert discovery.get_lane_model("deep") == "deep-model"
    assert discovery.get_lane_model("cache") == "cache-model"
    assert discovery.get_model_profile("fast-model")["context_window"] == 16000
    assert discovery.get_model_profile("unknown") == {"id": "unknown", "display_name": "unknown"}
    stack = discovery.get_stack()
    assert stack["models_count"] == 3
    assert stack["lanes"]["cache"]["model"] == "cache-model"
    assert stack["last_refresh"] is not None

    discovery.set_lane_override("cache", "fast-model")
    assert discovery.lane_assignments["cache"] == "fast-model"
    discovery.reset_overrides()
    assert discovery.get_lane_model("fast") == "fast-model"

    replacement = Provider([ModelInfo(id="replacement", display_name="Replacement", supports_tools=True)])
    replacement.provider_name = "replacement-provider"
    discovery.replace_provider(replacement, lane_overrides={"deep": "replacement"})
    assert discovery.provider_name == "replacement-provider"
    assert discovery.get_lane_model("deep") == "replacement"


def test_model_discovery_handles_missing_profiles_and_provider_errors(tmp_path):
    from model_discovery import ModelDiscovery, _load_profiles

    assert _load_profiles(str(tmp_path / "missing.yaml")) == {}

    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("profiles: [", encoding="utf-8")
    assert _load_profiles(str(bad_yaml)) == {}

    class FailingProvider:
        provider_name = "broken"

        def list_models(self):
            raise RuntimeError("provider unavailable")

    discovery = ModelDiscovery(FailingProvider(), profiles_path=str(tmp_path / "missing.yaml"), lane_overrides={"fast": "manual"})
    discovery.refresh()

    assert discovery.discovered_models == []
    assert discovery.lane_assignments == {"fast": "manual"}
    assert discovery.get_stack()["provider"] == "broken"


def test_model_discovery_profile_fallbacks_do_not_pin_newer_models(tmp_path):
    from providers.base import ModelInfo
    from model_discovery import ModelDiscovery

    profiles = tmp_path / "profiles.yaml"
    profiles.write_text(
        """
profiles:
  gpt-5.4:
    capability_tier: deep
    context_window: 1000000
    supports_tools: true
    cost_output_per_1k: 0.015
  gpt-5.5:
    capability_tier: deep
    context_window: 1000000
    supports_tools: true
    cost_output_per_1k: 0.03
""",
        encoding="utf-8",
    )

    class Provider:
        provider_name = "openai"

        def list_models(self):
            return [
                ModelInfo(id="gpt-5.4", display_name="GPT-5.4", supports_tools=True),
                ModelInfo(id="gpt-5.5", display_name="GPT-5.5", supports_tools=True),
            ]

    discovery = ModelDiscovery(
        Provider(),
        profiles_path=str(profiles),
        fallback_lanes={"deep": "gpt-5.4"},
    )
    discovery.refresh()

    assert discovery.get_lane_model("deep") == "gpt-5.5"
    assert discovery.get_stack()["lanes"]["deep"]["source"] == "auto"

    class FailingProvider:
        provider_name = "openai"

        def list_models(self):
            raise RuntimeError("provider unavailable")

    fallback = ModelDiscovery(
        FailingProvider(),
        profiles_path=str(profiles),
        fallback_lanes={"deep": "gpt-5.4"},
    )
    fallback.refresh()

    assert fallback.get_lane_model("deep") == "gpt-5.4"
    assert fallback.get_stack()["lanes"]["deep"]["source"] == "fallback"


def test_soul_template_registry_loads_filters_applies_and_invalidates(tmp_path, monkeypatch):
    from src.core.soul import amendments, linter, store, templates

    templates.invalidate_cache()
    monkeypatch.setattr(templates, "_template_cache", None)

    class FakeSoul:
        def __init__(self, **data):
            self.data = data

    monkeypatch.setattr(templates, "Soul", FakeSoul)
    monkeypatch.setattr(linter, "lint", lambda soul: [])
    monkeypatch.setattr(linter, "lint_or_raise", lambda soul: None)
    monkeypatch.setattr(store, "get_active_version", lambda soul_dir=None: "v1")
    proposal = types.SimpleNamespace(
        id="proposal-1",
        proposed_version="v2",
        diff_summary="changed mission",
        status=types.SimpleNamespace(value="PENDING"),
    )
    monkeypatch.setattr(amendments, "create_proposal", lambda **kwargs: proposal)

    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / "analyst.yaml").write_text(
        """
_template_metadata:
  name: analyst
  display_name: Analyst
  description: Research analyst
  industry: finance
  tags: [research]
mission: Draft reports
config:
  risk: medium
""",
        encoding="utf-8",
    )
    (template_dir / "missing-metadata.yaml").write_text("mission: missing metadata", encoding="utf-8")
    (template_dir / "not-a-map.yaml").write_text("- item", encoding="utf-8")

    loaded = templates.load_templates(str(template_dir), force_reload=True)
    assert [t.name for t in loaded] == ["analyst"]
    assert loaded[0].to_dict()["metadata"]["display_name"] == "Analyst"
    assert templates.get_template("analyst", str(template_dir)).display_name == "Analyst"
    assert templates.get_template("missing", str(template_dir)) is None
    assert templates.list_template_metadata(str(template_dir), industry="finance")[0]["name"] == "analyst"
    assert templates.list_template_metadata(str(template_dir), industry="healthcare") == []

    result = templates.apply_template(
        "analyst",
        customizations={"config": {"risk": "low"}, "mission": "Summarize filings"},
        operator_id="operator-1",
        session_id="session-1",
        soul_dir=str(tmp_path / "soul"),
        templates_dir=str(template_dir),
    )

    assert result == {
        "proposal_id": "proposal-1",
        "proposed_version": "v2",
        "diff_summary": "changed mission",
        "template_name": "analyst",
        "template_version": "1.0",
        "fields_customized": ["config", "mission"],
        "status": "PENDING",
    }

    with pytest.raises(templates.SoulStoreError):
        templates.apply_template("unknown", templates_dir=str(template_dir))

    templates.invalidate_cache()
    assert templates._template_cache is None


def test_incident_playbook_registry_loads_variants_filters_and_invalidates(tmp_path, monkeypatch):
    from src.incidents import playbooks

    playbooks.invalidate_cache()
    monkeypatch.setattr(playbooks, "_playbook_cache", {})
    monkeypatch.setattr(playbooks, "_cache_loaded", False)
    monkeypatch.setattr(playbooks, "_default_dir", None)

    playbook_dir = tmp_path / "playbooks"
    playbook_dir.mkdir()
    (playbook_dir / "base.yaml").write_text(
        """
_playbook_metadata:
  name: base
  display_name: Base Response
  category: security
  industry: finance
trigger:
  receipt_types: [kill_switch_issued]
paging:
  primary: secops
steps:
  - step: 1
    title: Acknowledge
    description: Confirm receipt
  - step: 2
    title: Contain
    description: Stop spread
""",
        encoding="utf-8",
    )
    (playbook_dir / "variant.yaml").write_text(
        """
_playbook_metadata:
  name: finance-base
  display_name: Finance Response
  category: security
  industry: finance
extends: base
paging:
  primary: finance-secops
variant_steps:
  - step: 99
    insert_after: 1
    title: Notify Risk
    description: Notify risk owner
""",
        encoding="utf-8",
    )
    (playbook_dir / "unknown-variant.yaml").write_text(
        """
_playbook_metadata:
  name: orphan
  display_name: Orphan
extends: missing
variant_steps:
  - step: 1
    title: Ignored
    description: Ignored
""",
        encoding="utf-8",
    )
    (playbook_dir / "invalid.yaml").write_text("steps: []", encoding="utf-8")
    (playbook_dir / "not-a-map.yaml").write_text("- item", encoding="utf-8")
    (playbook_dir / "ignore.txt").write_text("ignored", encoding="utf-8")

    loaded = playbooks.load_playbooks(str(playbook_dir))

    assert set(loaded) == {"base", "finance-base"}
    assert [step.title for step in loaded["finance-base"].steps] == [
        "Acknowledge",
        "Notify Risk",
        "Contain",
    ]
    assert [step.step for step in loaded["finance-base"].steps] == [1, 2, 3]
    assert loaded["finance-base"].paging["primary"] == "finance-secops"
    assert playbooks.get_playbook("base").metadata.display_name == "Base Response"
    assert [m["name"] for m in playbooks.list_playbook_metadata(category="security", industry="finance")] == [
        "base",
        "finance-base",
    ]
    assert playbooks.list_playbook_metadata(category="availability") == []

    playbooks.invalidate_cache()
    assert playbooks.get_playbook("missing") is None


def test_receipt_bridge_exports_metrics_webhooks_incidents_and_never_blocks(monkeypatch):
    from src.observability import receipt_bridge

    calls = []

    class FakeSpan:
        def __init__(self):
            self.status = None
            self.ended = False

        def set_status(self, code, message):
            self.status = (code, message)

        def end(self):
            self.ended = True
            calls.append("span_end")

    span = FakeSpan()

    class FakeTracer:
        def start_span(self, **kwargs):
            calls.append(("span", kwargs))
            return span

    class TraceFlags:
        SAMPLED = 1

        def __init__(self, value):
            self.value = value

    class SpanContext:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    trace_mod = _fake_module(
        SpanKind=types.SimpleNamespace(INTERNAL="internal"),
        StatusCode=types.SimpleNamespace(ERROR="error"),
        NonRecordingSpan=lambda ctx: ("nonrecording", ctx),
        SpanContext=SpanContext,
        TraceFlags=TraceFlags,
        set_span_in_context=lambda span_ctx: ("parent-context", span_ctx),
    )
    monkeypatch.setitem(sys.modules, "opentelemetry", _fake_module(trace=trace_mod, context=_fake_module()))
    monkeypatch.setitem(sys.modules, "opentelemetry.trace", trace_mod)
    monkeypatch.setitem(
        sys.modules,
        "src.observability.otel_provider",
        _fake_module(is_initialized=lambda: True, get_tracer=lambda: FakeTracer()),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.observability.span_mapper",
        _fake_module(
            should_export=lambda action_type, tier, sample_rate: True,
            span_name=lambda action_type: f"receipt.{action_type}",
            is_error_receipt=lambda action_type, status: status == "failed",
            receipt_to_span_attrs=lambda receipt: {"receipt.id": receipt["id"]},
            _deterministic_trace_id=lambda value: b"\x00" * 15 + b"\x01",
            _deterministic_span_id=lambda value: b"\x00" * 7 + b"\x02",
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.observability.metrics",
        _fake_module(update_metrics_from_receipt=lambda receipt: calls.append(("metrics", receipt["id"]))),
    )
    engine = types.SimpleNamespace(on_receipt=lambda receipt: calls.append(("webhook", receipt["id"])))
    monkeypatch.setitem(
        sys.modules,
        "src.observability.webhook_engine",
        _fake_module(get_webhook_engine=lambda: engine),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.incidents.receipt_hook",
        _fake_module(on_receipt_for_incidents=lambda receipt: calls.append(("incident", receipt["id"]))),
    )

    receipt_bridge.configure_bridge(enabled=True, sampling_rate=2.0)
    receipt_bridge.on_receipt_written(
        {
            "id": "receipt-1",
            "quest_id": "quest-1",
            "parent_id": "receipt-parent",
            "action_type": "tool.denied",
            "tier": 3,
            "status": "failed",
            "error_message": "policy blocked",
            "duration_ms": 3,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )

    assert receipt_bridge._sampling_rate == 1.0
    assert span.status == ("error", "policy blocked")
    assert "span_end" in calls
    assert ("metrics", "receipt-1") in calls
    assert ("webhook", "receipt-1") in calls
    assert ("incident", "receipt-1") in calls

    receipt_bridge.configure_bridge(enabled=False, sampling_rate=-1.0)
    calls.clear()
    receipt_bridge.on_receipt_written({"id": "receipt-2"})
    assert calls == []
    assert receipt_bridge._sampling_rate == 0.0


def test_receipt_bridge_swallows_optional_export_failures(monkeypatch):
    from src.observability import receipt_bridge

    monkeypatch.setattr(receipt_bridge, "_export_span", lambda receipt: (_ for _ in ()).throw(RuntimeError("otel down")))
    monkeypatch.setitem(
        sys.modules,
        "src.observability.metrics",
        _fake_module(update_metrics_from_receipt=lambda receipt: (_ for _ in ()).throw(RuntimeError("metrics down"))),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.observability.webhook_engine",
        _fake_module(get_webhook_engine=lambda: types.SimpleNamespace(on_receipt=lambda receipt: (_ for _ in ()).throw(RuntimeError("webhooks down")))),
    )
    monkeypatch.setitem(sys.modules, "src.incidents.receipt_hook", None)

    receipt_bridge.configure_bridge(enabled=True, sampling_rate=0.5)
    receipt_bridge.on_receipt_written({"id": "receipt-safe", "action_type": "x"})


def test_mcp_permissions_fail_closed_wildcards_and_federation_ceiling():
    from src.mcp.permissions import (
        MCPPermissionEvaluator,
        MCPRiskTier,
        MCPServerPermission,
        PermissionCheckResult,
        _tier_severity,
    )

    assert MCPServerPermission.from_dict(
        {"server_id": "stripe", "allowed_tools": ["*"], "risk_tier": "not-a-tier"}
    ) == MCPServerPermission(
        server_id="stripe",
        allowed_tools=frozenset({"*"}),
        risk_tier=MCPRiskTier.T2,
        wildcard=True,
    )

    evaluator = MCPPermissionEvaluator()
    evaluator.load_from_soul(
        {
            "version": "soul-v3",
            "mcp_permissions": [
                {"server_id": "github", "allowed_tools": ["read", "write"], "risk_tier": "T1"},
                {"server_id": "stripe", "allowed_tools": ["*"], "risk_tier": "T3"},
                {"allowed_tools": ["ignored"]},
            ],
        }
    )

    assert evaluator.soul_version == "soul-v3"
    assert set(evaluator.permitted_servers) == {"github", "stripe"}
    assert evaluator.get_server_permission("github").risk_tier == MCPRiskTier.T1
    assert evaluator.check_server_access("missing").to_dict() == {
        "allowed": False,
        "server_id": "missing",
        "tool_name": "",
        "risk_tier": "T2",
        "block_reason": "Server 'missing' not permitted by active Soul",
        "soul_version": "soul-v3",
    }
    assert evaluator.check_tool_access("github", "read").allowed is True
    denied = evaluator.check_tool_access("github", "delete")
    assert denied.allowed is False
    assert "Permitted: ['read', 'write']" in denied.block_reason
    assert evaluator.check_tool_access("stripe", "charge").allowed is True
    assert evaluator.get_allowed_tools("missing") == set()

    evaluator.load_permissions(
        [
            MCPServerPermission("github", frozenset({"read", "write"}), MCPRiskTier.T0),
            MCPServerPermission("stripe", frozenset({"*"}), MCPRiskTier.T1, wildcard=True),
            MCPServerPermission("calendar", frozenset({"write"}), MCPRiskTier.T1),
            MCPServerPermission("orphan", frozenset({"run"}), MCPRiskTier.T2),
        ],
        soul_version="child",
    )
    violations = evaluator.enforce_federation_ceiling(
        [
            MCPServerPermission("github", frozenset({"read"}), MCPRiskTier.T2),
            MCPServerPermission("stripe", frozenset({"charge"}), MCPRiskTier.T3),
            MCPServerPermission("calendar", frozenset({"read"}), MCPRiskTier.T1),
        ]
    )

    assert any("tools removed" in v for v in violations)
    assert any("wildcard downgraded" in v for v in violations)
    assert any("orphan" in v for v in violations)
    assert any("no tools remaining" in v for v in violations)
    assert evaluator.check_tool_access("github", "read").risk_tier == MCPRiskTier.T2
    assert evaluator.get_allowed_tools("github") == {"read"}
    assert evaluator.get_allowed_tools("stripe") == {"charge"}
    assert evaluator.check_server_access("calendar").allowed is False
    assert PermissionCheckResult(True, "github", "read", MCPRiskTier.T1).to_dict()["risk_tier"] == "T1"
    assert _tier_severity(MCPRiskTier.T3) > _tier_severity(MCPRiskTier.T0)


def test_gateway_admin_router_admin_and_ucp_paths(monkeypatch):
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    import feature_flags
    from gateway_admin_router import create_gateway_admin_router

    request_ids = iter([f"req-{i}" for i in range(20)])
    permission_calls = []

    def error_response(status_code, message, request_id=None):
        return JSONResponse(
            status_code=status_code,
            content={"error": message, "request_id": request_id},
        )

    def require_request_capability(request, capability, request_id=None):
        permission_calls.append((capability, request_id))
        if request.headers.get("x-deny") == "1":
            return error_response(403, f"missing {capability}", request_id=request_id)
        return None

    class WebhookAuth:
        def verify_remote_header(self, header):
            return header == "Bearer webhook"

    class Sentry:
        def __init__(self):
            self.permission = {"status": "APPROVED"}

        def approve_request(self, request_id):
            return request_id == "approval-ok"

        def check_permission(self, action, context):
            return self.permission

    class ForgeDiscovery:
        def scrape_docs(self, url):
            return f"docs:{url}"

        def generate_manifest(self, doc_text):
            return {"source": doc_text, "endpoints": ["/one", "/two"]}

        def generate_wrapper_script(self, manifest):
            return "wrapper"

    class ForgeDispatcher:
        def dispatch_from_prompt(self, prompt, content):
            return [{"prompt": prompt, "content": content}]

    class UcpConnector:
        def discover_merchant(self, merchant_url):
            return {"merchant_url": merchant_url}

        def search_products(self, merchant_url, query):
            return [{"merchant_url": merchant_url, "query": query}]

        def initiate_transaction(self, merchant_url, product_id, params):
            return {"merchant_url": merchant_url, "product_id": product_id, "params": params}

        def confirm_transaction(self, transaction_id):
            return {"transaction_id": transaction_id, "confirmed": True}

    sentry = Sentry()
    router = create_gateway_admin_router(
        error_response=error_response,
        require_request_capability=require_request_capability,
        make_request_id=lambda: next(request_ids),
        webhook_auth=WebhookAuth(),
        sentry=sentry,
        forge_discovery=ForgeDiscovery(),
        forge_dispatcher=ForgeDispatcher(),
        ucp_connector=UcpConnector(),
        logger=types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None),
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    approved = client.post(
        "/mcp_callback",
        headers={"authorization": "Bearer webhook"},
        json={"request_id": "approval-ok", "action": "APPROVE"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"].startswith("Request Approved")
    assert client.post("/mcp_callback", headers={"x-deny": "1"}, json={"request_id": "x"}).status_code == 403
    assert client.post("/mcp_callback", json={"request_id": "missing", "action": "APPROVE"}).status_code == 400
    assert client.post("/mcp_callback", json={"request_id": "ignored", "action": "DENY"}).json()["status"] == "Action ignored."

    discovered = client.post("/forge/discover", json={"url": "https://docs.example"}).json()
    assert discovered["endpoint_count"] == 2
    assert client.post("/forge/discover", json={"url": ""}).status_code == 400
    dispatched = client.post("/forge/dispatch", json={"content": "ship it", "prompt": "[x]"}).json()
    assert dispatched["dispatched_count"] == 1
    assert client.post("/forge/dispatch", json={"content": "", "prompt": "[x]"}).status_code == 400

    assert client.post("/ucp/discover", json={"merchant_url": "https://shop"}).json()["manifest"]["merchant_url"] == "https://shop"
    assert client.post("/ucp/discover", json={"merchant_url": ""}).status_code == 400
    search = client.post("/ucp/search", json={"merchant_url": "https://shop", "query": "boots"}).json()
    assert search["result_count"] == 1
    assert client.post("/ucp/search", json={"merchant_url": "", "query": "boots"}).status_code == 400

    sentry.permission = {"status": "PENDING", "message": "approval needed", "request_id": "sentry-1"}
    pending = client.post(
        "/ucp/transact",
        json={"merchant_url": "https://shop", "product_id": "sku", "params": {"qty": 1}},
    )
    assert pending.json()["status"] == "pending_approval"
    sentry.permission = {"status": "DENIED", "message": "blocked"}
    assert client.post("/ucp/transact", json={"merchant_url": "https://shop", "product_id": "sku"}).status_code == 403
    sentry.permission = {"status": "APPROVED"}
    assert client.post("/ucp/transact", json={"merchant_url": "https://shop", "product_id": ""}).status_code == 400
    transaction = client.post(
        "/ucp/transact",
        json={"merchant_url": "https://shop", "product_id": "sku", "params": {"qty": 1}},
    ).json()
    assert transaction["transaction"]["product_id"] == "sku"
    assert client.post("/ucp/confirm", json={"transaction_id": ""}).status_code == 400
    assert client.post("/ucp/confirm", json={"transaction_id": "tx-1"}).json()["result"]["confirmed"] is True

    monkeypatch.setattr(feature_flags, "FEATURE_GOOGLE_OAUTH", False, raising=False)
    assert client.post("/api/google-oauth/start", json={"client_id": "id", "client_secret": "secret"}).status_code == 403

    manager = types.SimpleNamespace(generate_auth_url=lambda client_id, client_secret: f"https://oauth/{client_id}/{client_secret}")
    monkeypatch.setitem(
        sys.modules,
        "google_oauth_manager",
        _fake_module(
            GoogleOAuthManager=lambda vault=None: manager,
            get_google_oauth_manager=lambda: manager,
            set_google_oauth_manager=lambda manager: None,
        ),
    )
    monkeypatch.setattr(feature_flags, "FEATURE_GOOGLE_OAUTH", True, raising=False)
    assert client.post("/api/google-oauth/start", json={"client_id": "", "client_secret": "secret"}).status_code == 400
    oauth = client.post("/api/google-oauth/start", json={"client_id": "id", "client_secret": "secret"})
    assert oauth.json()["auth_url"] == "https://oauth/id/secret"

    assert ("platform.admin", "req-5") in permission_calls
