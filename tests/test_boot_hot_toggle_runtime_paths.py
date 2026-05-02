import sys
import types

import pytest

import boot
from test_composition_coverage import (
    BOOT_BOUND_GLOBALS,
    CONTROL_PLANE_GLOBALS,
    _AsyncService,
    _FakeApp,
    _FakeOrchestrator,
    _FakeSubsystemManager,
    _SyncService,
    _enable_boot_feature_flags,
    _install_boot_success_modules,
    _preserve_module_globals,
    _reset_control_plane_runtime,
)


class _TelegramBot:
    def __init__(self):
        self.started = False
        self.attached = []
        self._actioncard_event_bridge_wired = False

    def start_polling(self):
        self.started = True

    def send_message(self, content):
        return content

    def sanitize_for_telegram(self, content):
        return content

    def handle_actioncard_event(self, *_args):
        return None

    def handle_actioncard_resolved_event(self, *_args):
        return None

    def attach_actioncard_runtime(self, *, resolver=None, store=None):
        self.attached.append((resolver, store))


def _boot_globals(monkeypatch):
    from src.core import control_plane

    _enable_boot_feature_flags(monkeypatch)
    _preserve_module_globals(monkeypatch, boot, BOOT_BOUND_GLOBALS)
    _preserve_module_globals(monkeypatch, control_plane, CONTROL_PLANE_GLOBALS)
    calls = _install_boot_success_modules(monkeypatch)
    sys.modules["actioncard_api"].shutdown_actioncard_api = lambda: calls.append(("shutdown_actioncard_api", (), {}))
    sys.modules["src.mcp.api"].shutdown_mcp_api = lambda: calls.append(("shutdown_mcp_api", (), {}))
    sys.modules["timetravel.api"].shutdown_timetravel_api = lambda: calls.append(("shutdown_timetravel_api", (), {}))
    sys.modules["a2a.api"].shutdown_a2a_api = lambda: calls.append(("shutdown_a2a_api", (), {}))
    sys.modules["a2a.server"].shutdown_a2a_server = lambda: calls.append(("shutdown_a2a_server", (), {}))
    sys.modules["src.incidents.api"].shutdown_incidents_api = lambda: calls.append(("shutdown_incidents_api", (), {}))
    sys.modules["src.incidents.playbook_api"].shutdown_playbook_api = lambda: calls.append(("shutdown_playbook_api", (), {}))
    sys.modules["src.core.model_usage_policy"].set_local_model_roles_status = (
        lambda status: calls.append(("set_local_model_roles_status", (status,), {}))
    )

    app = _FakeApp()
    subsystem_manager = _FakeSubsystemManager()
    orchestrator = _FakeOrchestrator()
    orchestrator.receipt_service = object()
    orchestrator.attach_connector_registry = lambda registry: setattr(orchestrator, "connector_registry", registry)
    orchestrator.initialize_provider = lambda: setattr(orchestrator, "provider", object())
    orchestrator.skill_factory = types.SimpleNamespace(
        approve_proposal=lambda *_, **__: setattr(orchestrator, "skill_approved", True),
        reject_proposal=lambda *_: setattr(orchestrator, "skill_rejected", True),
        actioncard_factory=None,
    )
    orchestrator.job_executor = types.SimpleNamespace(
        approve_job=lambda *_, **__: True,
    )
    telegram_bot = _TelegramBot()

    boot.bind_gateway_globals(
        API_TOKEN="token",
        _app_version="test",
        _boot_vault=None,
        app=app,
        antigravity=_AsyncService(),
        chat_poller=None,
        forge_dispatcher=types.SimpleNamespace(register_platform=lambda **_: calls.append(("register_platform", (), _))),
        librarian=_SyncService(),
        logger=boot.logger,
        main_orchestrator=orchestrator,
        mfa_guard=object(),
        onboarding_orch=types.SimpleNamespace(
            snapshot=types.SimpleNamespace(credential_status="none", state="PENDING", save=lambda: None)
        ),
        secret_cache=types.SimpleNamespace(
            is_bootstrapped=lambda: True,
            reload=lambda *_: {"LANCELOT_API_TOKEN": True},
            get=lambda *_: "token",
        ),
        sentry=object(),
        subsystem_manager=subsystem_manager,
        telegram_bot=telegram_bot,
        verify_token=lambda *_: True,
        _bootstrap_model_discovery=lambda: False,
        _bootstrap_model_router=lambda: False,
        _restore_persisted_provider=lambda *_: calls.append(("restore_provider", (), {})),
        _init_memory=lambda: {},
        _shutdown_memory=lambda *_: None,
        _init_soul=lambda: {},
        _shutdown_soul=lambda *_: None,
        _init_skills=lambda: {},
        _shutdown_skills=lambda *_: None,
        _init_scheduler=lambda: {},
        _shutdown_scheduler=lambda *_: None,
        _init_health_monitor=lambda: {},
        _shutdown_health_monitor=lambda *_: None,
        _init_host_bridge=lambda: {},
        _shutdown_host_bridge=lambda *_: None,
        _init_uab=lambda: {},
        _shutdown_uab=lambda *_: None,
        _init_hive=lambda: {"lifecycle": types.SimpleNamespace(kill_all=lambda *_, **__: ["a1"])},
        _shutdown_hive=lambda *_: None,
        _init_federation=lambda: {},
        _shutdown_federation=lambda *_: None,
    )
    return app, subsystem_manager, orchestrator, telegram_bot, calls, control_plane


@pytest.mark.asyncio
async def test_boot_registered_hot_toggle_subsystems_start_and_shutdown(monkeypatch):
    app, subsystem_manager, orchestrator, telegram_bot, calls, control_plane = _boot_globals(monkeypatch)

    try:
        await boot.boot(app, boot.BootConfig())

        toolflow_init = subsystem_manager.registered["toolflow_streaming"][1]
        toolflow_shutdown = subsystem_manager.registered["toolflow_streaming"][2]
        objects = toolflow_init()
        assert objects["emitter"] is orchestrator.toolflow_emitter
        assert getattr(objects["emitter"], "enabled") is True
        toolflow_shutdown(objects)
        assert orchestrator.toolflow_emitter is None
        assert objects["emitter"].enabled is False

        actioncards_init = subsystem_manager.registered["actioncards"][1]
        actioncards_shutdown = subsystem_manager.registered["actioncards"][2]
        action_objects = actioncards_init()
        resolver = action_objects["resolver"]
        assert "governance" in resolver.handlers
        assert "scheduler" in resolver.handlers
        assert "soul" in resolver.handlers
        assert "skills" in resolver.handlers
        assert telegram_bot.attached[-1] == (resolver, action_objects["store"])
        assert telegram_bot._actioncard_event_bridge_wired is True

        assert resolver.handlers["governance"]("item-1", "approve", actor="Myles", operator_id="op-1")["status"] == "approved"
        assert resolver.handlers["governance"]("item-1", "deny", actor="Myles", operator_id="op-1")["status"] == "denied"
        assert resolver.handlers["governance"]("item-1", "other")["status"] == "error"
        assert resolver.handlers["scheduler"]("job-1", "approve", actor="Myles")["status"] == "approved"
        assert resolver.handlers["scheduler"]("job-1", "deny")["status"] == "denied"
        assert resolver.handlers["soul"]("proposal-1", "approve")["status"] == "approved"
        assert resolver.handlers["soul"]("proposal-1", "reject")["status"] == "denied"
        assert resolver.handlers["soul"]("proposal-1", "bogus")["status"] == "error"
        assert resolver.handlers["skills"]("skill-1", "approve")["status"] == "approved"
        assert resolver.handlers["skills"]("skill-1", "reject")["status"] == "denied"
        assert resolver.handlers["skills"]("skill-1", "bogus")["status"] == "error"

        actioncards_shutdown(action_objects)
        assert not hasattr(app.state, "actioncard_store")
        assert orchestrator.actioncard_factory is None
        assert telegram_bot.attached[-1] == (None, None)

        mcp_init = subsystem_manager.registered["mcp"][1]
        mcp_shutdown = subsystem_manager.registered["mcp"][2]
        mcp_objects = mcp_init()
        assert {"registry", "evaluator", "network_policy", "receipt_service"}.issubset(mcp_objects)
        mcp_shutdown(mcp_objects)

        google_init = subsystem_manager.registered["google_oauth"][1]
        google_shutdown = subsystem_manager.registered["google_oauth"][2]
        google_objects = google_init()
        assert "manager" in google_objects
        google_shutdown(google_objects)
    finally:
        _reset_control_plane_runtime(control_plane)


def test_boot_local_model_status_ready_degraded_and_role_failure(monkeypatch, caplog):
    availability = []
    roles = []
    monkeypatch.setitem(
        __import__("sys").modules,
        "src.core.model_usage_policy",
        types.SimpleNamespace(
            set_local_model_availability=lambda *args, **kwargs: availability.append((args, kwargs)),
            set_local_model_roles_status=lambda status: roles.append(status),
        ),
    )

    ready = types.SimpleNamespace(
        local_model=types.SimpleNamespace(
            health=lambda: {
                "ready": True,
                "loaded": True,
                "last_error": None,
                "last_verified_at": "now",
                "last_checked_at": "now",
                "consecutive_failures": 0,
                "last_smoke_elapsed_ms": 1,
            }
        ),
        local_model_roles=types.SimpleNamespace(status=lambda: {"planner": "ready"}),
    )
    boot._publish_local_model_runtime_status(ready)
    assert availability[-1][0][0] is True
    assert roles == [{"planner": "ready"}]

    degraded = types.SimpleNamespace(
        local_model=types.SimpleNamespace(health=lambda: {"ready": False, "loaded": True, "last_error": "no model"}),
        local_model_roles=types.SimpleNamespace(status=lambda: (_ for _ in ()).throw(RuntimeError("roles down"))),
    )
    boot._publish_local_model_runtime_status(degraded)
    assert availability[-1][0][0] is False
    assert availability[-1][0][1] == "no model"

    broken = types.SimpleNamespace(
        local_model=types.SimpleNamespace(health=lambda: (_ for _ in ()).throw(RuntimeError("health down"))),
        local_model_roles=None,
    )
    boot._publish_local_model_runtime_status(broken)
    assert availability[-1][0][0] is False
    assert "health down" in availability[-1][0][1]


def test_boot_environment_validation_branches(monkeypatch):
    monkeypatch.setenv("LANCELOT_PROVIDER", "openai-codex")
    monkeypatch.setattr(boot, "_codex_auth_available", lambda: True)
    env = boot._validate_boot_environment("token")
    assert env.provider == "openai-codex"
    assert env.api_token_configured is True

    monkeypatch.setattr(boot, "_codex_auth_available", lambda: False)
    assert boot._validate_boot_environment(None).api_token_configured is False

    monkeypatch.setenv("LANCELOT_PROVIDER", "unknown")
    assert boot._validate_boot_environment("token").credential_var == ""


@pytest.mark.asyncio
async def test_boot_runtime_helpers_cover_success_and_failure_paths(monkeypatch):
    calls = []

    event_bus = types.SimpleNamespace(set_loop=lambda loop: calls.append(("set_loop", loop)))
    monkeypatch.setitem(sys.modules, "event_bus", types.SimpleNamespace(event_bus=event_bus))
    boot._capture_event_bus_loop()
    assert calls[-1][0] == "set_loop"

    monkeypatch.setitem(
        sys.modules,
        "providers.codex_cli_client",
        types.SimpleNamespace(has_codex_cli_auth=lambda: True),
    )
    assert boot._codex_auth_available() is True

    monkeypatch.setitem(
        sys.modules,
        "providers.codex_cli_client",
        types.SimpleNamespace(has_codex_cli_auth=lambda: (_ for _ in ()).throw(RuntimeError("auth probe failed"))),
    )
    assert boot._codex_auth_available() is False

    monkeypatch.setitem(
        sys.modules,
        "feature_flags",
        types.SimpleNamespace(
            FEATURE_TOOLS_ANTIGRAVITY=True,
            log_feature_flags=lambda: calls.append(("flags",)),
        ),
    )
    async def _start_antigravity():
        calls.append(("antigravity",))

    monkeypatch.setattr(boot, "librarian", types.SimpleNamespace(start=lambda: calls.append(("librarian",))), raising=False)
    monkeypatch.setattr(boot, "antigravity", types.SimpleNamespace(start=_start_antigravity), raising=False)
    await boot._start_core_runtime_services()
    assert ("librarian",) in calls
    assert ("antigravity",) in calls

    monkeypatch.setitem(
        sys.modules,
        "feature_flags",
        types.SimpleNamespace(FEATURE_TOOLS_ANTIGRAVITY=False, log_feature_flags=lambda: calls.append(("flags",))),
    )
    await boot._start_core_runtime_services()
    boot._log_boot_feature_flags()
    assert ("flags",) in calls

    monkeypatch.setitem(
        sys.modules,
        "src.core.api_auth",
        types.SimpleNamespace(init_api_auth=lambda verifier: calls.append(("api_auth", verifier("token")))),
    )
    monkeypatch.setattr(boot, "verify_token", lambda token: token == "token", raising=False)
    boot._init_shared_api_auth()
    assert ("api_auth", True) in calls

    orchestrator = types.SimpleNamespace(
        refresh_soul_policy=lambda soul: calls.append(("soul", soul)),
    )
    monkeypatch.setattr(boot, "main_orchestrator", orchestrator, raising=False)
    monkeypatch.setitem(
        sys.modules,
        "src.a2a.agent_card",
        types.SimpleNamespace(invalidate_card=lambda: calls.append(("invalidate_card",))),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.timetravel.api",
        types.SimpleNamespace(update_timetravel_soul=lambda soul: calls.append(("timetravel", soul))),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.mcp.api",
        types.SimpleNamespace(update_mcp_soul=lambda soul: calls.append(("mcp", soul))),
    )
    lifecycle = types.SimpleNamespace(update_parent_soul=lambda soul: calls.append(("hive", soul)))
    emitter = types.SimpleNamespace(emit_once=lambda: calls.append(("federation_emit",)))
    entries = {
        "hive": types.SimpleNamespace(running=True, objects={"lifecycle": lifecycle}),
        "federation": types.SimpleNamespace(running=True, objects={"emitter": emitter}),
    }
    monkeypatch.setattr(boot, "subsystem_manager", types.SimpleNamespace(get=lambda name: entries.get(name)), raising=False)
    app = types.SimpleNamespace(state=types.SimpleNamespace())
    applier = boot._build_runtime_soul_applier(app)
    applier({"version": "v2"})

    assert ("soul", {"version": "v2"}) in calls
    assert ("invalidate_card",) in calls
    assert ("timetravel", {"version": "v2"}) in calls
    assert ("mcp", {"version": "v2"}) in calls
    assert ("hive", {"version": "v2"}) in calls
    assert ("federation_emit",) in calls
    assert app.state.active_soul == {"version": "v2"}


def test_boot_runtime_helper_import_failures_are_non_fatal(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name in {
            "event_bus",
            "providers.codex_cli_client",
            "feature_flags",
            "src.core.api_auth",
        }:
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    boot._capture_event_bus_loop()
    assert boot._codex_auth_available() is False
    boot._log_boot_feature_flags()
    boot._init_shared_api_auth()


@pytest.mark.asyncio
async def test_boot_hot_toggle_timetravel_a2a_and_incident_subsystems(monkeypatch):
    app, subsystem_manager, orchestrator, telegram_bot, calls, control_plane = _boot_globals(monkeypatch)

    try:
        await boot.boot(app, boot.BootConfig())

        timetravel_init = subsystem_manager.registered["time_travel"][1]
        timetravel_shutdown = subsystem_manager.registered["time_travel"][2]
        tt_objects = timetravel_init()
        assert tt_objects["receipt_service"] is orchestrator.receipt_service
        timetravel_shutdown(tt_objects)
        assert any(call[0] == "shutdown_timetravel_api" for call in calls)

        a2a_init = subsystem_manager.registered["a2a"][1]
        a2a_shutdown = subsystem_manager.registered["a2a"][2]
        a2a_objects = a2a_init()
        assert {"registry", "client", "inbound", "outbound", "receipt_service"}.issubset(a2a_objects)
        server_call = [call for call in calls if call[0] == "init_a2a_server"][-1]
        task_executor = server_call[2]["task_executor"]
        caller = types.SimpleNamespace(display_name="Remote", agent_id="agent-1", agent_framework="a2a")
        task = types.SimpleNamespace(
            message=types.SimpleNamespace(
                parts=[
                    types.SimpleNamespace(text="Summarize receipts", data=None, file_uri=""),
                    types.SimpleNamespace(text="", data={"id": 1}, file_uri=""),
                    types.SimpleNamespace(text="", data=None, file_uri="file://proof.txt"),
                ]
            )
        )
        result = task_executor(task=task, caller=caller, quest_id="quest-1")
        assert result["status"] == "completed"
        assert result["artifacts"][0]["metadata"]["external_peer"] == "agent-1"
        with pytest.raises(ValueError, match="no executable content"):
            task_executor(
                task=types.SimpleNamespace(message=types.SimpleNamespace(parts=[])),
                caller=caller,
                quest_id="quest-2",
            )
        a2a_shutdown(a2a_objects)
        assert any(call[0] == "shutdown_a2a_api" for call in calls)
        assert any(call[0] == "shutdown_a2a_server" for call in calls)

        incident_init = subsystem_manager.registered["incident_response"][1]
        incident_shutdown = subsystem_manager.registered["incident_response"][2]
        incident_objects = incident_init()
        assert incident_objects["receipt_service"] is orchestrator.receipt_service
        assert incident_objects["playbooks_dir"].endswith("playbooks")
        incident_shutdown(incident_objects)
        assert any(call[0] == "shutdown_incidents_api" for call in calls)
        assert any(call[0] == "shutdown_playbook_api" for call in calls)
    finally:
        _reset_control_plane_runtime(control_plane)
