import sys
import types

import pytest


class _State:
    pass


class _FakeApp:
    def __init__(self):
        self.state = _State()
        self.routers = []

    def include_router(self, router):
        self.routers.append(router)


class _AsyncService:
    def __init__(self):
        self.started = False

    async def start(self):
        self.started = True


class _SyncService:
    def __init__(self):
        self.started = False

    def start(self):
        self.started = True


class _FakeSubsystemEntry:
    def __init__(self):
        self.running = False
        self.objects = {}


class _FakeSubsystemManager:
    def __init__(self):
        self.registered = {}
        self.started = []
        self.entries = {}

    def register(self, name, flag_name, init_fn, shutdown_fn, routes):
        self.registered[name] = (flag_name, init_fn, shutdown_fn, routes)
        self.entries.setdefault(name, _FakeSubsystemEntry())

    def start(self, name):
        self.started.append(name)
        self.entries.setdefault(name, _FakeSubsystemEntry()).running = True

    def get(self, name):
        return self.entries.get(name)


class _FakeOrchestrator:
    def __init__(self):
        self.audit_logger = object()
        self.data_dir = "/tmp/lancelot-test"
        self.provider = None
        self.receipt_service = None
        self.soul = None
        self.task_runner = types.SimpleNamespace(connector_runtime=None)
        self.task_store = types.SimpleNamespace(
            get_run_by_quest_id=lambda *_: None,
            get_graph=lambda *_: None,
            save_graph=lambda *_: None,
            create_run=lambda *_: None,
        )
        self.skill_factory = None
        self.skill_registry = None
        self.skill_executor = None
        self.trust_ledger = None
        self.rule_engine = None
        self.decision_log = None
        self.local_model = None
        self._memory_enabled = False
        self.context_compiler = None
        self._risk_classifier = types.SimpleNamespace(update_soul=lambda *_: None)

    def _init_provider(self):
        self.provider = object()

    def _get_frontier_scrubber(self):
        return None

    def chat(self, *_, **__):
        return "ok"


def _disable_feature_flags(monkeypatch):
    import feature_flags

    for name in dir(feature_flags):
        if name.startswith("FEATURE_"):
            monkeypatch.setattr(feature_flags, name, False, raising=False)

    # Keep the core registration branches representative while avoiding
    # external services and background workers.
    monkeypatch.setattr(feature_flags, "FEATURE_SOUL", False, raising=False)
    monkeypatch.setattr(feature_flags, "FEATURE_SKILLS", False, raising=False)
    monkeypatch.setattr(feature_flags, "FEATURE_SCHEDULER", False, raising=False)
    monkeypatch.setattr(feature_flags, "FEATURE_HEALTH_MONITOR", False, raising=False)


def _enable_boot_feature_flags(monkeypatch):
    import feature_flags

    for name in dir(feature_flags):
        if name.startswith("FEATURE_"):
            monkeypatch.setattr(feature_flags, name, True, raising=False)


def _module(**attrs):
    mod = types.ModuleType("fake")
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


def _preserve_module_globals(monkeypatch, module, names):
    """Restore composition-root globals mutated through bind_gateway_globals()."""
    for name in names:
        monkeypatch.setattr(module, name, getattr(module, name, None), raising=False)


BOOT_BOUND_GLOBALS = (
    "API_TOKEN",
    "_app_version",
    "_apply_runtime_soul",
    "_boot_vault",
    "_bootstrap_model_discovery",
    "_bootstrap_model_router",
    "_get_uab_provider",
    "_init_bal",
    "_init_federation",
    "_init_health_monitor",
    "_init_hive",
    "_init_host_bridge",
    "_init_memory",
    "_init_scheduler",
    "_init_skills",
    "_init_soul",
    "_init_uab",
    "_restore_persisted_provider",
    "_shutdown_bal",
    "_shutdown_federation",
    "_shutdown_health_monitor",
    "_shutdown_hive",
    "_shutdown_host_bridge",
    "_shutdown_memory",
    "_shutdown_scheduler",
    "_shutdown_skills",
    "_shutdown_soul",
    "_shutdown_uab",
    "_startup_time",
    "antigravity",
    "app",
    "chat_poller",
    "forge_dispatcher",
    "librarian",
    "logger",
    "main_orchestrator",
    "mfa_guard",
    "onboarding_orch",
    "secret_cache",
    "sentry",
    "subsystem_manager",
    "telegram_bot",
    "verify_token",
)


GATEWAY_BOOT_SUPPORT_BOUND_GLOBALS = (
    "antigravity",
    "app",
    "chat_poller",
    "forge_discovery",
    "forge_dispatcher",
    "forge_sandbox",
    "forge_vault",
    "librarian",
    "main_orchestrator",
    "mfa_guard",
    "onboarding_orch",
    "scheduler_service",
    "sentry",
    "telegram_bot",
    "webhook_auth",
)


CONTROL_PLANE_GLOBALS = (
    "_model_router",
    "_runtime_emergency_stop_handler",
    "_snapshot",
    "_startup_time",
    "_token_store",
    "_usage_persistence",
    "_usage_tracker",
)


def _reset_control_plane_runtime(control_plane):
    control_plane.set_model_router(None)
    control_plane.set_usage_tracker(None)
    control_plane.set_usage_persistence(None)
    control_plane.set_runtime_control_hooks(emergency_stop_handler=None)


def _install_boot_success_modules(monkeypatch):
    router = object()
    calls = []

    def remember(name):
        def _inner(*args, **kwargs):
            calls.append((name, args, kwargs))
            return None

        return _inner

    event_bus = types.SimpleNamespace(
        set_loop=remember("set_loop"),
        subscribe=remember("subscribe"),
        subscribe_all=remember("subscribe_all"),
    )
    monkeypatch.setitem(sys.modules, "event_bus", _module(event_bus=event_bus))

    for name, attrs in {
        "src.core.api_auth": {"init_api_auth": remember("init_api_auth")},
        "memory.api": {"router": router},
        "soul.template_api": {"router": router},
        "scheduler_api": {"router": router, "init_scheduler_api": remember("init_scheduler_api")},
        "health.api": {"router": router, "set_snapshot_provider": remember("set_snapshot_provider")},
        "bal.clients.api": {"router": router},
        "src.hive.api": {"router": router},
        "src.federation.api": {"router": router},
        "src.federation.graph_api": {"graph_router": router},
        "skills_api": {"router": router, "init_skills_api": remember("init_skills_api")},
        "src.core.governance_receipts": {"init_governance_receipts": remember("init_governance_receipts")},
        "compliance.api": {"router": router, "init_compliance_api": remember("init_compliance_api")},
        "governance.approval_learning.rule_engine": {"RuleEngine": object},
        "trust_api": {"router": router, "init_trust_api": remember("init_trust_api")},
        "apl_api": {"router": router, "init_apl_api": remember("init_apl_api")},
        "tools_api": {"router": router, "init_tools_api": remember("init_tools_api")},
        "flags_api": {"router": router, "init_flags_api": remember("init_flags_api")},
        "actioncard_api": {"router": router, "init_actioncard_api": remember("init_actioncard_api")},
        "src.core.auth_api": {"router": router, "init_auth_api": remember("init_auth_api")},
        "setup_api": {"router": router, "init_setup_api": remember("init_setup_api")},
        "update_api": {"router": router, "init_update_api": remember("init_update_api")},
        "providers.api": {
            "router": router,
            "init_provider_api": remember("init_provider_api"),
            "load_persisted_config": lambda: {"active_provider": "gemini"},
        },
        "toolflow.telegram_bridge": {"TelegramProgressBridge": lambda *_: types.SimpleNamespace(on_toolflow_event=lambda *_: None)},
        "observability.api": {"router": router},
        "observability.metrics_api": {"router": router, "init_metrics_api": remember("init_metrics_api")},
        "timetravel.api": {"router": router, "init_timetravel_api": remember("init_timetravel_api")},
        "src.incidents.api": {"router": router, "init_incidents_api": remember("init_incidents_api")},
        "src.incidents.playbook_api": {"router": router, "init_playbook_api": remember("init_playbook_api")},
        "src.incidents.receipt_hook": {"configure": remember("configure_incident_hook")},
    }.items():
        monkeypatch.setitem(sys.modules, name, _module(**attrs))

    monkeypatch.setitem(
        sys.modules,
        "soul.api",
        _module(
            router=router,
            init_soul_runtime=remember("init_soul_runtime"),
            init_soul_actioncards=remember("init_soul_actioncards"),
            _approve_proposal_direct=lambda proposal_id, actor="": {"status": "approved"},
            _reject_proposal_direct=lambda proposal_id, actor="": {"status": "denied"},
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.a2a.agent_card",
        _module(invalidate_card=remember("invalidate_card")),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.timetravel.api",
        _module(update_timetravel_soul=remember("update_timetravel_soul")),
    )

    class LocalModelClient:
        def health(self):
            return {"ready": True, "loaded": True, "last_error": None}

    monkeypatch.setitem(sys.modules, "local_model_client", _module(LocalModelClient=LocalModelClient))
    monkeypatch.setitem(
        sys.modules,
        "src.core.model_usage_policy",
        _module(set_local_model_availability=remember("set_local_model_availability")),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.core.control_plane",
        _module(
            router=router,
            init_control_plane=remember("init_control_plane"),
            set_runtime_control_hooks=remember("set_runtime_control_hooks"),
            set_usage_tracker=remember("set_usage_tracker"),
            set_usage_persistence=remember("set_usage_persistence"),
        ),
    )

    receipt_service = object()
    monkeypatch.setitem(
        sys.modules,
        "receipts_api",
        _module(router=router, init_receipts_api=remember("init_receipts_api"), _receipt_service=receipt_service),
    )
    monkeypatch.setitem(
        sys.modules,
        "governance_api",
        _module(
            router=router,
            init_governance_api=remember("init_governance_api"),
            _approve_item_direct=lambda *_, **__: {"status": "approved"},
            _deny_item_direct=lambda *_, **__: {"status": "denied"},
        ),
    )

    class ActionCardStore:
        def __init__(self, *_, **__):
            pass

    class ActionCardFactory:
        def __init__(self, *_, **__):
            pass

    class ActionCardResolver:
        def __init__(self, *_, **__):
            self.handlers = {}

        def register_handler(self, name, handler):
            self.handlers[name] = handler

    monkeypatch.setitem(sys.modules, "actioncard.store", _module(ActionCardStore=ActionCardStore))
    monkeypatch.setitem(sys.modules, "actioncard.factory", _module(ActionCardFactory=ActionCardFactory))
    monkeypatch.setitem(sys.modules, "actioncard.resolver", _module(ActionCardResolver=ActionCardResolver))

    class ConnectorStatus:
        CONFIGURED = "configured"

    class ConnectorRegistry:
        def __init__(self, *_, **__):
            self._config = {"connectors": {"slack": {"enabled": True, "backend": "test"}}}

        def list_connectors(self):
            manifest = types.SimpleNamespace(id="slack")
            return [types.SimpleNamespace(manifest=manifest)]

    class ConnectorVault:
        def __init__(self, *_, **__):
            self.values = {}

        def exists(self, key):
            return key in self.values

        def store(self, key, value, type="config"):
            self.values[key] = value

    class ConnectorRuntime:
        def __init__(self, *_, **__):
            self.registered = []

        def register_connector(self, connector_id):
            self.registered.append(connector_id)

    monkeypatch.setitem(sys.modules, "connectors.registry", _module(ConnectorRegistry=ConnectorRegistry))
    monkeypatch.setitem(sys.modules, "connectors.base", _module(ConnectorStatus=ConnectorStatus))
    monkeypatch.setitem(sys.modules, "connectors.vault", _module(CredentialVault=ConnectorVault))
    monkeypatch.setitem(sys.modules, "connectors.runtime", _module(ConnectorRuntime=ConnectorRuntime))
    monkeypatch.setitem(
        sys.modules,
        "connectors.credential_api",
        _module(router=router, init_credential_api=remember("init_credential_api")),
    )
    monkeypatch.setitem(
        sys.modules,
        "connectors_api",
        _module(
            router=router,
            init_connectors_api=remember("init_connectors_api"),
            register_connector_with_vault_access=lambda *_: types.SimpleNamespace(status=ConnectorStatus.CONFIGURED),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.connectors.google_feature_gate",
        _module(
            is_google_connector_enabled=lambda *_: True,
            google_connector_disabled_reason=lambda *_: "",
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.tools.fabric",
        _module(get_tool_fabric=lambda: types.SimpleNamespace(_policy_engine=object())),
    )

    monkeypatch.setitem(
        sys.modules,
        "src.mcp.api",
        _module(router=router, init_mcp_api=remember("init_mcp_api"), update_mcp_soul=remember("update_mcp_soul")),
    )
    for name, class_name in {
        "src.mcp.argument_screen": "MCPArgumentScreener",
        "src.mcp.network_policy": "MCPNetworkPolicy",
        "src.mcp.permissions": "MCPPermissionEvaluator",
        "src.mcp.proxy": "GovernedMCPProxy",
        "src.mcp.receipts": "MCPReceiptManager",
        "src.mcp.registry": "MCPServerRegistry",
        "src.mcp.response_guard": "MCPResponseGuard",
    }.items():
        cls = type(class_name, (), {"__init__": lambda self, *_, **__: None})
        if class_name == "MCPPermissionEvaluator":
            cls.load_from_soul = lambda self, *_: None
        monkeypatch.setitem(sys.modules, name, _module(**{class_name: cls}))

    class _OAuthManager:
        def __init__(self, *_, **__):
            pass

        def start_background_refresh(self):
            pass

        def recover_from_vault(self):
            return True

    monkeypatch.setitem(
        sys.modules,
        "oauth_token_manager",
        _module(
            OAuthTokenManager=_OAuthManager,
            set_oauth_manager=remember("set_oauth_manager"),
            get_oauth_token=lambda: "token",
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "openai_codex_oauth_manager",
        _module(
            OpenAICodexOAuthManager=_OAuthManager,
            set_openai_codex_manager=remember("set_openai_codex_manager"),
            get_codex_oauth_token=lambda: "token",
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "google_oauth_manager",
        _module(GoogleOAuthManager=_OAuthManager, set_google_oauth_manager=remember("set_google_oauth_manager")),
    )
    monkeypatch.setitem(
        sys.modules,
        "onboarding_snapshot",
        _module(OnboardingState=types.SimpleNamespace(READY="READY")),
    )

    class UpdateChecker:
        def start(self):
            calls.append(("update_start", (), {}))

    monkeypatch.setitem(sys.modules, "update_checker", _module(UpdateChecker=UpdateChecker))

    class UsageTracker:
        def set_persistence(self, persistence):
            self.persistence = persistence

    class UsagePersistence:
        def __init__(self, *_, **__):
            pass

    monkeypatch.setitem(sys.modules, "usage_tracker", _module(UsageTracker=UsageTracker))
    monkeypatch.setitem(sys.modules, "usage_persistence", _module(UsagePersistence=UsagePersistence))

    obs_config = types.SimpleNamespace(
        webhooks=types.SimpleNamespace(enabled=True, endpoints=["https://example.invalid"], delivery_timeout_s=1, max_retries=0),
        otel=types.SimpleNamespace(
            enabled=True,
            endpoint="http://otel.invalid",
            auth_header="",
            export_interval_s=1,
            resource_attributes={},
            sampling_rate_t0_t1=1.0,
        ),
    )
    monkeypatch.setitem(sys.modules, "observability.config", _module(load_config=lambda: obs_config))
    monkeypatch.setitem(sys.modules, "observability.otel_provider", _module(init_otel=lambda **_: True))
    monkeypatch.setitem(sys.modules, "observability.receipt_bridge", _module(configure_bridge=remember("configure_bridge")))
    monkeypatch.setitem(sys.modules, "observability.webhook_engine", _module(init_webhook_engine=remember("init_webhook_engine")))

    class A2ARegistry:
        pass

    class A2AClient:
        def __init__(self, *_, **__):
            pass

    class InboundPipeline:
        def __init__(self, *_, **__):
            pass

    class OutboundPipeline:
        def __init__(self, *_, **__):
            pass

    class A2AMessagePart:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class A2AArtifact:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def to_dict(self):
            return {"parts": self.parts, "metadata": self.metadata}

    monkeypatch.setitem(sys.modules, "a2a.registry", _module(A2ARegistry=A2ARegistry))
    monkeypatch.setitem(
        sys.modules,
        "a2a.server",
        _module(a2a_server_router=router, init_a2a_server=remember("init_a2a_server")),
    )
    monkeypatch.setitem(sys.modules, "a2a.api", _module(router=router, init_a2a_api=remember("init_a2a_api")))
    monkeypatch.setitem(sys.modules, "a2a.inbound_pipeline", _module(InboundPipeline=InboundPipeline))
    monkeypatch.setitem(sys.modules, "a2a.outbound_pipeline", _module(OutboundPipeline=OutboundPipeline))
    monkeypatch.setitem(sys.modules, "a2a.client", _module(A2AClient=A2AClient))
    monkeypatch.setitem(sys.modules, "a2a.types", _module(A2AArtifact=A2AArtifact, A2AMessagePart=A2AMessagePart))

    return calls


@pytest.mark.asyncio
async def test_boot_composition_root_registers_core_subsystems_without_optional_services(monkeypatch):
    import boot
    from src.core import control_plane

    _disable_feature_flags(monkeypatch)
    _preserve_module_globals(monkeypatch, boot, BOOT_BOUND_GLOBALS)
    _preserve_module_globals(monkeypatch, control_plane, CONTROL_PLANE_GLOBALS)

    app = _FakeApp()
    subsystem_manager = _FakeSubsystemManager()
    orchestrator = _FakeOrchestrator()
    antigravity = _AsyncService()
    librarian = _SyncService()

    boot.bind_gateway_globals(
        API_TOKEN="",
        _app_version="test",
        _boot_vault=None,
        app=app,
        antigravity=antigravity,
        chat_poller=None,
        forge_dispatcher=types.SimpleNamespace(register_platform=lambda **_: None),
        librarian=librarian,
        logger=boot.logger,
        main_orchestrator=orchestrator,
        mfa_guard=object(),
        onboarding_orch=types.SimpleNamespace(snapshot=types.SimpleNamespace(save=lambda: None)),
        secret_cache=types.SimpleNamespace(
            is_bootstrapped=lambda: False,
            reload=lambda *_: {},
            get=lambda *_: None,
        ),
        sentry=object(),
        subsystem_manager=subsystem_manager,
        telegram_bot=None,
        verify_token=lambda *_: True,
        _apply_runtime_soul=lambda *_: None,
        _bootstrap_model_discovery=lambda: False,
        _bootstrap_model_router=lambda: False,
        _restore_persisted_provider=lambda *_: None,
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
        _init_bal=lambda: {},
        _shutdown_bal=lambda *_: None,
        _init_host_bridge=lambda: {},
        _shutdown_host_bridge=lambda *_: None,
        _init_uab=lambda: {},
        _shutdown_uab=lambda *_: None,
        _init_hive=lambda: {},
        _shutdown_hive=lambda *_: None,
        _init_federation=lambda: {},
        _shutdown_federation=lambda *_: None,
    )

    try:
        result = await boot.boot(app, boot.BootConfig())

        assert result.env.provider in {"", "gemini"}
        assert librarian.started is True
        assert antigravity.started is True
        assert {"memory", "soul", "skills", "scheduler", "health_monitor", "hive"}.issubset(
            subsystem_manager.registered
        )
        assert hasattr(app.state, "apply_runtime_soul")
    finally:
        _reset_control_plane_runtime(control_plane)


@pytest.mark.asyncio
async def test_boot_composition_root_wires_optional_success_paths(monkeypatch):
    import boot
    from src.core import control_plane

    _enable_boot_feature_flags(monkeypatch)
    _preserve_module_globals(monkeypatch, boot, BOOT_BOUND_GLOBALS)
    _preserve_module_globals(monkeypatch, control_plane, CONTROL_PLANE_GLOBALS)
    calls = _install_boot_success_modules(monkeypatch)

    app = _FakeApp()
    subsystem_manager = _FakeSubsystemManager()
    orchestrator = _FakeOrchestrator()
    orchestrator.receipt_service = object()
    orchestrator.skill_factory = types.SimpleNamespace(
        approve_proposal=lambda *_, **__: None,
        reject_proposal=lambda *_: None,
        actioncard_factory=None,
    )
    antigravity = _AsyncService()
    librarian = _SyncService()

    class TelegramBot:
        def __init__(self):
            self.started = False
            self._action_card_resolver = None
            self._action_card_store = None

        def start_polling(self):
            self.started = True

        def send_message(self, content):
            return content

        def _sanitize_for_telegram(self, content):
            return content

        def _on_actioncard_event(self, *_):
            pass

        def _on_actioncard_resolved_event(self, *_):
            pass

    telegram_bot = TelegramBot()

    boot.bind_gateway_globals(
        API_TOKEN="token",
        _app_version="test",
        _boot_vault=None,
        app=app,
        antigravity=antigravity,
        chat_poller=None,
        forge_dispatcher=types.SimpleNamespace(register_platform=lambda **_: calls.append(("register_platform", (), _))),
        librarian=librarian,
        logger=boot.logger,
        main_orchestrator=orchestrator,
        mfa_guard=object(),
        onboarding_orch=types.SimpleNamespace(
            snapshot=types.SimpleNamespace(credential_status="none", state="PENDING", save=lambda: None)
        ),
        secret_cache=types.SimpleNamespace(
            is_bootstrapped=lambda: True,
            reload=lambda *_: {"LANCELOT_API_TOKEN": True, "OTHER": False},
            get=lambda *_: "token",
        ),
        sentry=object(),
        subsystem_manager=subsystem_manager,
        telegram_bot=telegram_bot,
        verify_token=lambda *_: True,
        _bootstrap_model_discovery=lambda: True,
        _bootstrap_model_router=lambda: True,
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
        _init_bal=lambda: {},
        _shutdown_bal=lambda *_: None,
        _init_host_bridge=lambda: {},
        _shutdown_host_bridge=lambda *_: None,
        _init_uab=lambda: {},
        _shutdown_uab=lambda *_: None,
        _init_hive=lambda: {},
        _shutdown_hive=lambda *_: None,
        _init_federation=lambda: {},
        _shutdown_federation=lambda *_: None,
    )

    try:
        result = await boot.boot(app, boot.BootConfig())

        assert result.env.api_token_configured is True
        assert telegram_bot.started is True
        assert hasattr(app.state, "actioncard_resolver")
        assert orchestrator.connector_runtime is not None
        assert {"memory", "soul", "skills", "scheduler", "health_monitor", "hive", "federation"}.issubset(
            set(subsystem_manager.started)
        )
        assert any(name == "init_a2a_server" for name, _, _ in calls)
    finally:
        _reset_control_plane_runtime(control_plane)


class _FakeGovernor:
    def __init__(self, allow=True):
        self.allow = allow
        self.logged = []

    def check_limit(self, *_):
        return self.allow

    def log_usage(self, *args):
        self.logged.append(args)


class _FakeToolLoopSelf:
    def __init__(self, result):
        self.provider = object()
        self.skill_executor = object()
        self.local_model = None
        self.context_env = types.SimpleNamespace(get_context_string=lambda: "context")
        self.governor = _FakeGovernor()
        self.usage_tracker = types.SimpleNamespace(record_simple=lambda *_: None)
        self.model_name = "test-model"
        self.toolflow_emitter = None
        self._current_channel = "api"
        self._current_quest_id = "quest-1"
        self._last_result = result
        self.actioncard_factory = None
        self.sentry = None
        self.governance_events = []

    def _build_tool_declarations(self):
        return []

    def _build_openai_tool_declarations(self):
        return []

    def _build_system_instruction(self):
        return "system"

    def _get_thinking_config(self):
        return None

    def _route_model(self, *_):
        return "model"

    def _build_frontier_user_message(self, text, images=None):
        return {"role": "user", "content": text, "images": images or []}

    def _build_frontier_tool_response_message(self, tool_results):
        return [
            {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}
            for call_id, name, content in tool_results
        ]

    def _extract_literal_terms(self, *_):
        return ["Exact Term"]

    def _llm_call_with_retry(self, fn):
        return fn()

    def _provider_generate_with_tools(self, **_):
        return self._last_result

    def _provider_generate(self, **_):
        return types.SimpleNamespace(text="")

    def _text_only_generate(self, *_, **__):
        return "text-only"

    def _agentic_generate(self, *_, **__):
        return "flagship-fallback"

    def _format_tool_receipts(self, receipts, note="", error=None):
        return f"{note}:{len(receipts)}:{error or ''}"

    def _strip_failure_narration(self, text):
        return text

    def _classify_tool_call_safety(self, *_):
        return "auto"

    def _record_governance_event(self, *args):
        self.governance_events.append(args)

    def _is_narration_without_content(self, *_):
        return False

    def _force_synthesis(self, *_):
        return ""

    def _get_trust_summary(self, *_):
        return "trust summary"

    def _suggest_alternatives(self, *_):
        return ["alternative"]


def test_agentic_generate_returns_text_when_model_does_not_call_tools():
    import tool_loop

    result = types.SimpleNamespace(text="final answer", tool_calls=[])
    runtime = _FakeToolLoopSelf(result)

    assert tool_loop._agentic_generate(runtime, "use Exact Term") == "final answer"
    assert runtime._last_tool_receipts == []
    assert runtime.governor.logged


def test_agentic_generate_falls_back_to_text_only_without_skill_executor():
    import tool_loop

    runtime = _FakeToolLoopSelf(types.SimpleNamespace(text="", tool_calls=[]))
    runtime.skill_executor = None

    assert tool_loop._agentic_generate(runtime, "hello") == "text-only"


def test_agentic_generate_executes_declared_tool_then_returns_final_text(monkeypatch):
    import tool_loop
    from providers.base import ToolCall

    monkeypatch.setattr(tool_loop._ff, "FEATURE_STRUCTURED_OUTPUT", False, raising=False)
    monkeypatch.setattr(tool_loop._ff, "FEATURE_CLAIM_VERIFICATION", False, raising=False)
    monkeypatch.setattr(tool_loop._ff, "FEATURE_DEEP_REASONING_LOOP", False, raising=False)

    first = types.SimpleNamespace(
        text="",
        raw={"role": "assistant", "content": ""},
        tool_calls=[ToolCall(name="network_client", args={"method": "GET", "url": "https://example.invalid"}, id="call-1")],
    )
    second = types.SimpleNamespace(text="final answer", raw={"role": "assistant", "content": "final answer"}, tool_calls=[])
    runtime = _FakeToolLoopSelf(first)
    runtime._build_tool_declarations = lambda: [types.SimpleNamespace(name="network_client")]
    calls = []
    runtime.skill_executor = types.SimpleNamespace(
        run=lambda name, inputs: (
            calls.append((name, inputs))
            or types.SimpleNamespace(success=True, outputs={"body": "ok"}, error="")
        )
    )
    results = [first, second]
    runtime._provider_generate_with_tools = lambda **_: results.pop(0)

    assert tool_loop._agentic_generate(runtime, "fetch Exact Term", force_tool_use=True) == "final answer"
    assert calls == [("network_client", {"method": "GET", "url": "https://example.invalid"})]
    assert runtime._last_tool_receipts[0]["result"] == "SUCCESS"
    assert ("tool_calls", 1) in runtime.governor.logged


def test_agentic_generate_replaces_stale_approval_prompt_after_successful_tools(monkeypatch):
    import tool_loop
    from providers.base import ToolCall

    monkeypatch.setattr(tool_loop._ff, "FEATURE_STRUCTURED_OUTPUT", False, raising=False)
    monkeypatch.setattr(tool_loop._ff, "FEATURE_CLAIM_VERIFICATION", False, raising=False)
    monkeypatch.setattr(tool_loop._ff, "FEATURE_DEEP_REASONING_LOOP", False, raising=False)

    first = types.SimpleNamespace(
        text="",
        raw={"role": "assistant", "content": ""},
        tool_calls=[
            ToolCall(
                name="repo_writer",
                args={"action": "create", "path": "approval/group_one.txt"},
                id="call-1",
            )
        ],
    )
    second = types.SimpleNamespace(
        text=(
            "Paused for Commander approval before running 1 governed action.\n\n"
            "Approval ID: `stale`.\n\n"
            "Review the ActionCard in War Room, then send `continue` after approval."
        ),
        raw={"role": "assistant", "content": "stale approval prompt"},
        tool_calls=[],
    )
    runtime = _FakeToolLoopSelf(first)
    runtime._build_tool_declarations = lambda: [types.SimpleNamespace(name="repo_writer")]
    runtime.skill_executor = types.SimpleNamespace(
        run=lambda name, inputs: types.SimpleNamespace(
            success=True,
            outputs={"path": "/home/lancelot/workspace/approval/group_one.txt"},
            error="",
        )
    )
    results = [first, second]
    runtime._provider_generate_with_tools = lambda **_: results.pop(0)

    response = tool_loop._agentic_generate(runtime, "create approved file", force_tool_use=True)

    assert response.startswith("Completed approved governed actions:")
    assert "Approval ID" not in response
    assert runtime._last_tool_receipts[0]["result"] == "SUCCESS"
    assert runtime._last_tool_receipts[0]["skill"] == "repo_writer"


def test_agentic_generate_suppresses_duplicate_successful_tool_call(monkeypatch):
    import tool_loop
    from providers.base import ToolCall

    monkeypatch.setattr(tool_loop._ff, "FEATURE_STRUCTURED_OUTPUT", False, raising=False)
    monkeypatch.setattr(tool_loop._ff, "FEATURE_CLAIM_VERIFICATION", False, raising=False)
    monkeypatch.setattr(tool_loop._ff, "FEATURE_DEEP_REASONING_LOOP", False, raising=False)

    args = {"action": "create", "path": "approval/group_one.txt", "workspace": "/home/lancelot/workspace"}
    first = types.SimpleNamespace(
        text="",
        raw={"role": "assistant", "content": ""},
        tool_calls=[ToolCall(name="repo_writer", args=args, id="call-1")],
    )
    duplicate = types.SimpleNamespace(
        text="",
        raw={"role": "assistant", "content": ""},
        tool_calls=[ToolCall(name="repo_writer", args=args, id="call-2")],
    )
    final = types.SimpleNamespace(
        text="Done.",
        raw={"role": "assistant", "content": "Done."},
        tool_calls=[],
    )
    runtime = _FakeToolLoopSelf(first)
    runtime._build_tool_declarations = lambda: [types.SimpleNamespace(name="repo_writer")]
    calls = []
    runtime.skill_executor = types.SimpleNamespace(
        run=lambda name, inputs: (
            calls.append((name, dict(inputs)))
            or types.SimpleNamespace(
                success=True,
                outputs={"path": "/home/lancelot/workspace/approval/group_one.txt"},
                error="",
            )
        )
    )
    results = [first, duplicate, final]
    runtime._provider_generate_with_tools = lambda **_: results.pop(0)

    response = tool_loop._agentic_generate(runtime, "create approved file", force_tool_use=True)

    assert response == "Done."
    assert calls == [("repo_writer", args)]
    assert len(runtime._last_tool_receipts) == 1
    assert runtime._last_tool_receipts[0]["result"] == "SUCCESS"


def test_agentic_generate_persists_tool_call_receipt(monkeypatch):
    import tool_loop
    from providers.base import ToolCall

    monkeypatch.setattr(tool_loop._ff, "FEATURE_STRUCTURED_OUTPUT", False, raising=False)
    monkeypatch.setattr(tool_loop._ff, "FEATURE_CLAIM_VERIFICATION", False, raising=False)
    monkeypatch.setattr(tool_loop._ff, "FEATURE_DEEP_REASONING_LOOP", False, raising=False)

    first = types.SimpleNamespace(
        text="",
        raw={"role": "assistant", "content": ""},
        tool_calls=[ToolCall(name="network_client", args={"method": "GET", "url": "https://example.invalid"}, id="call-1")],
    )
    second = types.SimpleNamespace(text="final answer", raw={"role": "assistant", "content": "final answer"}, tool_calls=[])
    runtime = _FakeToolLoopSelf(first)
    runtime._build_tool_declarations = lambda: [types.SimpleNamespace(name="network_client")]
    receipts = []
    runtime.receipt_service = types.SimpleNamespace(create=lambda receipt: receipts.append(receipt))
    runtime.skill_executor = types.SimpleNamespace(
        run=lambda name, inputs: types.SimpleNamespace(success=True, outputs={"body": "ok"}, error="")
    )
    results = [first, second]
    runtime._provider_generate_with_tools = lambda **_: results.pop(0)

    assert tool_loop._agentic_generate(runtime, "fetch Exact Term", force_tool_use=True) == "final answer"
    assert len(receipts) == 1
    assert receipts[0].action_type == "tool_call"
    assert receipts[0].action_name == "network_client"
    assert receipts[0].status == "success"
    assert receipts[0].inputs["tool"] == "network_client"
    assert receipts[0].outputs["body"] == "ok"
    assert receipts[0].metadata["tool_name"] == "network_client"


def test_agentic_generate_rejects_hallucinated_tool_and_continues(monkeypatch):
    import tool_loop
    from providers.base import ToolCall

    monkeypatch.setattr(tool_loop._ff, "FEATURE_STRUCTURED_OUTPUT", False, raising=False)
    monkeypatch.setattr(tool_loop._ff, "FEATURE_CLAIM_VERIFICATION", False, raising=False)
    monkeypatch.setattr(tool_loop._ff, "FEATURE_DEEP_REASONING_LOOP", False, raising=False)

    first = types.SimpleNamespace(
        text="",
        raw={"role": "assistant", "content": ""},
        tool_calls=[ToolCall(name="made_up_tool", args={}, id="call-1")],
    )
    second = types.SimpleNamespace(text="recovered", raw={"role": "assistant", "content": "recovered"}, tool_calls=[])
    runtime = _FakeToolLoopSelf(first)
    runtime._build_tool_declarations = lambda: [types.SimpleNamespace(name="network_client")]
    runtime.skill_executor = types.SimpleNamespace(run=lambda *_: (_ for _ in ()).throw(AssertionError("not called")))
    results = [first, second]
    runtime._provider_generate_with_tools = lambda **_: results.pop(0)

    assert tool_loop._agentic_generate(runtime, "use missing tool") == "recovered"
    assert "REJECTED" in runtime._last_tool_receipts[0]["result"]


def test_agentic_generate_blocks_escalated_tool_without_write_permission(monkeypatch):
    import tool_loop
    from providers.base import ToolCall

    monkeypatch.setattr(tool_loop._ff, "FEATURE_STRUCTURED_OUTPUT", False, raising=False)
    monkeypatch.setattr(tool_loop._ff, "FEATURE_CLAIM_VERIFICATION", False, raising=False)
    monkeypatch.setattr(tool_loop._ff, "FEATURE_DEEP_REASONING_LOOP", False, raising=False)

    first = types.SimpleNamespace(
        text="",
        raw={"role": "assistant", "content": ""},
        tool_calls=[ToolCall(name="repo_writer", args={"action": "edit", "path": "README.md"}, id="call-1")],
    )
    runtime = _FakeToolLoopSelf(first)
    runtime._build_tool_declarations = lambda: [types.SimpleNamespace(name="repo_writer")]
    runtime._classify_tool_call_safety = lambda *_: "escalate"
    actioncards = []
    runtime.actioncard_factory = types.SimpleNamespace(
        from_sentry_request=lambda **kwargs: actioncards.append(kwargs)
    )
    results = [first]
    runtime._provider_generate_with_tools = lambda **_: results.pop(0)

    response = tool_loop._agentic_generate(runtime, "write file", allow_writes=False)
    assert response.startswith("Paused for Commander approval")
    assert "send `continue` after approval" in response
    assert runtime._last_tool_receipts[0]["result"].startswith("ESCALATED")
    assert actioncards[0]["tool_name"] == "repo_writer"
    assert "User request: write file." in actioncards[0]["approval_context"]
    assert "create or modify files" in actioncards[0]["approval_reason"]


def test_tool_planning_uses_fast_model_for_codex_provider():
    import tool_loop

    runtime = _FakeToolLoopSelf(types.SimpleNamespace(text="", tool_calls=[]))
    runtime.provider = types.SimpleNamespace(provider_name="openai-codex")
    runtime.model_name = "gpt-5.4-mini"
    runtime._route_model = lambda *_: "gpt-5.4"

    assert tool_loop._tool_planner_model(runtime, "continue the plan") == "gpt-5.4-mini"


def test_agentic_generate_groups_multiple_escalated_tool_calls(monkeypatch, tmp_path):
    import tool_loop
    from mcp_sentry import MCPSentry
    from providers.base import ToolCall

    monkeypatch.setattr(tool_loop._ff, "FEATURE_STRUCTURED_OUTPUT", False, raising=False)
    monkeypatch.setattr(tool_loop._ff, "FEATURE_CLAIM_VERIFICATION", False, raising=False)
    monkeypatch.setattr(tool_loop._ff, "FEATURE_DEEP_REASONING_LOOP", False, raising=False)

    first = types.SimpleNamespace(
        text="",
        raw={"role": "assistant", "content": ""},
        tool_calls=[
            ToolCall(name="repo_writer", args={"action": "edit", "path": "src/tickets/store.py"}, id="call-1"),
            ToolCall(name="repo_writer", args={"action": "edit", "path": "tests/test_tickets.py"}, id="call-2"),
        ],
    )
    runtime = _FakeToolLoopSelf(first)
    runtime.sentry = MCPSentry(data_dir=str(tmp_path))
    runtime._build_tool_declarations = lambda: [types.SimpleNamespace(name="repo_writer")]
    runtime._classify_tool_call_safety = lambda *_: "escalate"
    grouped = []
    runtime.actioncard_factory = types.SimpleNamespace(
        from_sentry_request_batch=lambda **kwargs: grouped.append(kwargs) or types.SimpleNamespace(card_id="card-1")
    )
    results = [first]
    runtime._provider_generate_with_tools = lambda **_: results.pop(0)

    response = tool_loop._agentic_generate(runtime, "write two files", allow_writes=False)

    assert "2 governed actions" in response
    assert "Approval group ID: `card-1`" in response
    assert len(grouped[0]["requests"]) == 2
    request_ids = [item["request_id"] for item in grouped[0]["requests"]]
    assert all(req_id in runtime.sentry.pending_requests for req_id in request_ids)


def test_agentic_generate_rejects_missing_tool_inputs_before_approval(monkeypatch):
    import tool_loop
    from providers.base import ToolCall

    monkeypatch.setattr(tool_loop._ff, "FEATURE_STRUCTURED_OUTPUT", False, raising=False)
    monkeypatch.setattr(tool_loop._ff, "FEATURE_CLAIM_VERIFICATION", False, raising=False)
    monkeypatch.setattr(tool_loop._ff, "FEATURE_DEEP_REASONING_LOOP", False, raising=False)

    first = types.SimpleNamespace(
        text="",
        raw={"role": "assistant", "content": ""},
        tool_calls=[ToolCall(name="repo_writer", args={"action": "edit"}, id="call-1")],
    )
    second = types.SimpleNamespace(text="fixed", raw={"role": "assistant", "content": "fixed"}, tool_calls=[])
    runtime = _FakeToolLoopSelf(first)
    runtime._build_tool_declarations = lambda: [types.SimpleNamespace(name="repo_writer")]
    runtime._classify_tool_call_safety = lambda *_: (_ for _ in ()).throw(AssertionError("approval path should not run"))
    runtime.actioncard_factory = types.SimpleNamespace(
        from_sentry_request=lambda **_: (_ for _ in ()).throw(AssertionError("action card should not be created"))
    )
    results = [first, second]
    runtime._provider_generate_with_tools = lambda **_: results.pop(0)

    response = tool_loop._agentic_generate(runtime, "write file", allow_writes=False)
    assert response.startswith("Completion contract failed:")
    assert "repo_writer missing required input" in response
    assert "missing required input" in runtime._last_tool_receipts[0]["result"]


def test_agentic_generate_blocks_completion_claim_with_unresolved_tool_failure(monkeypatch):
    import tool_loop
    from providers.base import ToolCall

    monkeypatch.setattr(tool_loop._ff, "FEATURE_STRUCTURED_OUTPUT", False, raising=False)
    monkeypatch.setattr(tool_loop._ff, "FEATURE_CLAIM_VERIFICATION", False, raising=False)
    monkeypatch.setattr(tool_loop._ff, "FEATURE_DEEP_REASONING_LOOP", False, raising=False)

    first = types.SimpleNamespace(
        text="",
        raw={"role": "assistant", "content": ""},
        tool_calls=[ToolCall(name="command_runner", args={"command": "type README.md"}, id="call-1")],
    )
    second = types.SimpleNamespace(
        text="Done.",
        raw={"role": "assistant", "content": "Done."},
        tool_calls=[],
    )
    runtime = _FakeToolLoopSelf(first)
    runtime._build_tool_declarations = lambda: [types.SimpleNamespace(name="command_runner")]
    runtime.skill_executor = types.SimpleNamespace(
        run=lambda *_: types.SimpleNamespace(success=False, outputs={}, error="Windows shell command in POSIX runtime")
    )
    results = [first, second]
    runtime._provider_generate_with_tools = lambda **_: results.pop(0)

    response = tool_loop._agentic_generate(runtime, "inspect README", force_tool_use=True)

    assert response.startswith("Completion contract failed:")
    assert "governed action is unresolved" in response
    assert runtime._last_tool_receipts[0]["result"].startswith("FAILED")


def test_local_agentic_generate_falls_back_when_local_model_is_unavailable():
    import tool_loop

    runtime = _FakeToolLoopSelf(types.SimpleNamespace(text="", tool_calls=[]))

    assert tool_loop._local_agentic_generate(runtime, "hello") == "flagship-fallback"


def test_local_agentic_generate_returns_text_and_tracks_usage():
    import tool_loop

    captured = {}

    class LocalModel:
        def is_healthy(self):
            return True

        def chat_with_tools(self, **kwargs):
            captured["messages"] = kwargs["messages"]
            return {
                "choices": [
                    {
                        "message": {"content": "local answer"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"total_tokens": 17},
            }

    usage = []
    runtime = _FakeToolLoopSelf(types.SimpleNamespace(text="", tool_calls=[]))
    runtime.local_model = LocalModel()
    runtime.usage_tracker = types.SimpleNamespace(record_simple=lambda *args: usage.append(args))

    assert tool_loop._local_agentic_generate(runtime, "hello") == "local answer"
    assert "emoji sparingly" in captured["messages"][0]["content"]
    assert ("tokens", 17) in runtime.governor.logged
    assert usage == [("local-llm", 17)]


def test_local_agentic_generate_returns_when_approval_is_required():
    import tool_loop

    class LocalModel:
        def is_healthy(self):
            return True

        def chat_with_tools(self, **_):
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "function": {
                                        "name": "repo_writer",
                                        "arguments": "{\"action\":\"edit\",\"path\":\"README.md\"}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"total_tokens": 11},
            }

    actioncards = []
    runtime = _FakeToolLoopSelf(types.SimpleNamespace(text="", tool_calls=[]))
    runtime.local_model = LocalModel()
    runtime._classify_tool_call_safety = lambda *_: "escalate"
    runtime.actioncard_factory = types.SimpleNamespace(
        from_sentry_request=lambda **kwargs: actioncards.append(kwargs)
    )
    runtime.skill_executor = types.SimpleNamespace(
        run=lambda *_: (_ for _ in ()).throw(AssertionError("not called"))
    )

    response = tool_loop._local_agentic_generate(runtime, "write file")

    assert response.startswith("Paused for Commander approval")
    assert runtime._last_tool_receipts[0]["result"].startswith("ESCALATED")
    assert actioncards[0]["approval_context"].startswith("User request: write file.")


class _PlanRuntime:
    def __init__(self, verification_success=True):
        self.data_dir = "/tmp/lancelot-test"
        self._risk_classifier = None
        self._async_queue = None
        self._rollback_manager = None
        self.events = []
        self.executed = []
        self.verifier = types.SimpleNamespace(
            verify_step=lambda goal, output: types.SimpleNamespace(
                success=verification_success,
                reason="verified" if verification_success else "not verified",
                correction_suggestion="fix the step",
            )
        )

    def wake_up(self, reason):
        self.events.append(("wake", reason))

    def _execute_step_tool(self, step, params):
        self.executed.append((step.id, params))
        return "tool output"

    def _record_governance_event(self, *args):
        self.events.append(("governance", args))


def _plan_with_step():
    param = types.SimpleNamespace(key="path", value="README.md")
    step = types.SimpleNamespace(
        id="s1",
        description="read the README",
        tool="command_runner",
        params=[param],
    )
    return types.SimpleNamespace(plan_id="plan-1", steps=[step])


def test_execute_plan_legacy_path_records_successful_governance_event(monkeypatch):
    import tool_loop

    monkeypatch.setattr(tool_loop, "_GOVERNANCE_AVAILABLE", False, raising=False)
    monkeypatch.setattr(tool_loop, "_TOOL_CAPABILITY_MAP", {"command_runner": "cli.execute"}, raising=False)
    runtime = _PlanRuntime(verification_success=True)

    result = tool_loop.execute_plan(runtime, _plan_with_step())

    assert result.startswith("Plan Executed Successfully.")
    assert runtime.executed == [("s1", {"path": "README.md"})]
    assert any(event[0] == "governance" for event in runtime.events)


def test_execute_plan_legacy_path_stops_on_failed_verification(monkeypatch):
    import tool_loop

    monkeypatch.setattr(tool_loop, "_GOVERNANCE_AVAILABLE", False, raising=False)
    monkeypatch.setattr(tool_loop, "_TOOL_CAPABILITY_MAP", {"command_runner": "cli.execute"}, raising=False)
    runtime = _PlanRuntime(verification_success=False)

    result = tool_loop.execute_plan(runtime, _plan_with_step())

    assert result.startswith("Plan Failed at Step s1.")
    assert "fix the step" in result


class _CommandContext:
    def list_workspace(self, target):
        return f"listed {target}"

    def read_file(self, path):
        return f"read {path}"

    def search_workspace(self, query):
        return f"search {query}"

    def get_file_outline(self, path):
        return f"outline {path}"

    def get_workspace_diff(self, staged=False):
        return f"diff staged={staged}"


class _CommandFileOps:
    def safe_copy(self, src, dst, reason):
        return f"copied {src} {dst} {reason}"

    def safe_move(self, src, dst, reason):
        return f"moved {src} {dst} {reason}"

    def safe_delete(self, path, reason):
        return f"deleted {path} {reason}"

    def safe_mkdir(self, path, reason):
        return {"created": path, "reason": reason}

    def touch(self, path, reason):
        return {"touched": path, "reason": reason}


class _CommandRuntime:
    def __init__(self):
        self.context_env = _CommandContext()
        self.file_ops = _CommandFileOps()
        self.sentry = None
        self.audit_logger = types.SimpleNamespace(calls=[], log_command=lambda command: self.audit_logger.calls.append(command))
        self.network_interceptor = types.SimpleNamespace(check_url=lambda *_: True)
        self.sleeping = False
        self.wake_reasons = []

    def enter_sleep(self):
        self.sleeping = True

    def wake_up(self, reason):
        self.wake_reasons.append(reason)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (["ls", "src"], "listed src"),
        (["cat", "README.md"], "read README.md"),
        (["grep", "lancelot"], "search lancelot"),
        (["outline", "src/core/tool_loop.py"], "outline src/core/tool_loop.py"),
        (["diff", "--staged"], "diff staged=True"),
        (["cp", "a", "b"], "copied a b CLI: cp a b"),
        (["mv", "a", "b"], "moved a b CLI: mv a b"),
        (["rm", "a"], "deleted a CLI: rm a"),
        (["mkdir", "tmp"], "{'created': 'tmp', 'reason': 'CLI: mkdir tmp'}"),
        (["touch", "tmp/file"], "{'touched': 'tmp/file', 'reason': 'CLI: touch tmp/file'}"),
    ],
)
def test_execute_command_handles_safe_repl_and_file_operations(command, expected):
    import tool_loop

    assert tool_loop._execute_command(_CommandRuntime(), command) == expected


def test_execute_command_handles_sleep_and_wake_commands():
    import tool_loop

    runtime = _CommandRuntime()

    assert tool_loop._execute_command(runtime, ["sleep"]) == "Entered SLEEP mode."
    assert runtime.sleeping is True
    assert tool_loop._execute_command(runtime, ["wake"]) == "Entered ACTIVE mode."
    assert runtime.wake_reasons == ["Manual CLI"]


def test_execute_command_blocks_pending_denied_and_disallowed_urls(monkeypatch):
    import tool_loop

    runtime = _CommandRuntime()
    runtime.sentry = types.SimpleNamespace(
        check_permission=lambda *_: {"status": "PENDING", "message": "needs approval", "request_id": "req-1"},
        log_execution=lambda *_: None,
    )
    assert "PERMISSION REQUIRED" in tool_loop._execute_command(runtime, ["python", "-V"])

    runtime.sentry.check_permission = lambda *_: {"status": "DENIED", "message": "blocked"}
    assert tool_loop._execute_command(runtime, ["python", "-V"]) == "ACCESS DENIED: blocked"

    runtime.sentry = None
    runtime.network_interceptor = types.SimpleNamespace(check_url=lambda *_: False)
    assert tool_loop._execute_command(runtime, ["curl", "https://example.invalid"]).startswith("SECURITY BLOCK")
    assert runtime.audit_logger.calls == ["curl https://example.invalid"]


def test_execute_command_runs_subprocess_and_reports_errors(monkeypatch):
    import tool_loop

    runtime = _CommandRuntime()

    monkeypatch.setattr(
        tool_loop.subprocess,
        "run",
        lambda *_, **__: types.SimpleNamespace(stdout="subprocess ok\n"),
    )
    assert tool_loop._execute_command(runtime, ["python", "-V"]) == "subprocess ok"
    assert runtime.audit_logger.calls[-1] == "python -V"

    def raise_called_process_error(*_, **__):
        raise tool_loop.subprocess.CalledProcessError(1, ["bad"], stderr="boom")

    monkeypatch.setattr(tool_loop.subprocess, "run", raise_called_process_error)
    assert tool_loop._execute_command(runtime, ["bad"]) == "Error executing command: boom"

    monkeypatch.setattr(tool_loop.subprocess, "run", lambda *_, **__: (_ for _ in ()).throw(RuntimeError("nope")))
    assert tool_loop._execute_command(runtime, ["bad"]) == "Error executing command: nope"


def test_gateway_boot_support_health_monitor_reports_local_model_states(monkeypatch):
    import gateway_boot_support as gbs

    _preserve_module_globals(monkeypatch, gbs, GATEWAY_BOOT_SUPPORT_BOUND_GLOBALS)
    availability_calls = []

    class HealthCheck:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class HealthMonitor:
        def __init__(self, checks, interval_s):
            self.checks = checks
            self.interval_s = interval_s
            self.started = False
            self.stopped = False

        def start_monitor(self):
            self.started = True

        def stop_monitor(self):
            self.stopped = True

        def compute_snapshot(self):
            return {"checks": [check.name for check in self.checks]}

    monkeypatch.setitem(sys.modules, "health.monitor", _module(HealthMonitor=HealthMonitor, HealthCheck=HealthCheck))
    monkeypatch.setitem(
        sys.modules,
        "health.api",
        _module(set_snapshot_provider=lambda provider: availability_calls.append(("snapshot_provider", provider))),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.core.model_usage_policy",
        _module(set_local_model_availability=lambda *args, **kwargs: availability_calls.append((args, kwargs))),
    )

    local_model = types.SimpleNamespace(
        health=lambda: {
            "ready": True,
            "loaded": True,
            "status": "ok",
            "last_error": None,
            "consecutive_failures": 0,
        },
        is_healthy=lambda: True,
    )
    orchestrator = types.SimpleNamespace(
        provider=object(),
        local_model=local_model,
        scheduler_service=types.SimpleNamespace(last_scheduler_tick_at="tick"),
        job_executor=types.SimpleNamespace(is_running=True),
    )
    onboarding = types.SimpleNamespace(_determine_state=lambda: "READY")

    gbs.bind_gateway_globals(main_orchestrator=orchestrator, onboarding_orch=onboarding)
    objects = gbs._init_health_monitor()

    assert objects["monitor"].started is True
    assert [check.name for check in objects["monitor"].checks] == [
        "llm_provider",
        "onboarding_ready",
        "local_llm",
        "scheduler",
    ]
    assert objects["monitor"].checks[2].snapshot_details_fn()["local_llm_status"] == "ok"
    gbs._shutdown_health_monitor(objects)
    assert objects["monitor"].stopped is True


def test_gateway_boot_support_provider_hot_toggle_and_model_bootstrap(monkeypatch):
    import gateway_boot_support as gbs
    from src.core import control_plane

    _preserve_module_globals(monkeypatch, gbs, GATEWAY_BOOT_SUPPORT_BOUND_GLOBALS)
    _preserve_module_globals(monkeypatch, control_plane, CONTROL_PLANE_GLOBALS)
    calls = []

    class Fabric:
        def __init__(self):
            self.config = types.SimpleNamespace(default_workspace="/workspace")
            self.providers = []
            self.unregistered = []

        def register_provider(self, provider):
            self.providers.append(provider)

        def unregister_provider(self, name):
            self.unregistered.append(name)

        def update_router_preferences(self):
            calls.append("router_preferences")

    fabric = Fabric()

    class HostBridgeProvider:
        def __init__(self, workspace):
            self.workspace = workspace
            self.config = types.SimpleNamespace(agent_url="http://host-agent")

    class UABProvider:
        def __init__(self):
            self.config = types.SimpleNamespace(daemon_url="http://uab")

        def health_check(self):
            return types.SimpleNamespace(state=types.SimpleNamespace(value="healthy"))

    monkeypatch.setitem(sys.modules, "src.tools.fabric", _module(get_tool_fabric=lambda: fabric))
    monkeypatch.setitem(sys.modules, "src.tools.providers.host_bridge", _module(HostBridgeProvider=HostBridgeProvider))
    monkeypatch.setitem(sys.modules, "src.tools.providers.uab_bridge", _module(UABProvider=UABProvider))

    host_objects = gbs._init_host_bridge()
    uab_objects = gbs._init_uab()
    assert host_objects["provider"].workspace == "/workspace"
    assert uab_objects["provider"].config.daemon_url == "http://uab"
    assert gbs._get_uab_provider() is not None

    gbs._shutdown_host_bridge(host_objects)
    gbs._shutdown_uab(uab_objects)
    assert "host_bridge" in fabric.unregistered
    assert "uab_bridge" in fabric.unregistered

    class ModelDiscovery:
        def __init__(self, provider, profiles_path, lane_overrides):
            self.provider = provider
            self.profiles_path = profiles_path
            self.lane_overrides = lane_overrides
            self.discovered_models = ["fast", "deep"]
            self.lane_assignments = {"fast": "model-fast"}

        def refresh(self):
            calls.append("refresh")

    class ProfileRegistry:
        def has_provider(self, name):
            return True

        def get_profile(self, name):
            return types.SimpleNamespace(
                fast=types.SimpleNamespace(model="profile-fast"),
                deep=types.SimpleNamespace(model="profile-deep"),
                cache=types.SimpleNamespace(model="profile-cache"),
            )

    class ModelRouter:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.usage = types.SimpleNamespace(set_persistence=lambda *_: calls.append("router_persistence"))

    monkeypatch.setitem(sys.modules, "model_discovery", _module(ModelDiscovery=ModelDiscovery))
    monkeypatch.setitem(sys.modules, "provider_profile", _module(ProfileRegistry=ProfileRegistry))
    monkeypatch.setitem(sys.modules, "model_router", _module(ModelRouter=ModelRouter))
    providers_api = _module(
        ensure_persisted_active_provider=lambda *_: True,
        init_provider_api=lambda *_, **__: calls.append("init_provider_api"),
        load_persisted_config=lambda: {"lane_overrides": {"fast": "persisted-fast"}},
    )
    monkeypatch.setitem(sys.modules, "providers.api", providers_api)
    if "providers" in sys.modules:
        monkeypatch.setattr(sys.modules["providers"], "api", providers_api, raising=False)

    control_plane_api = _module(
        set_model_router=lambda *_: calls.append("set_model_router"),
        set_usage_tracker=lambda *_: calls.append("set_usage_tracker"),
    )
    monkeypatch.setitem(sys.modules, "src.core.control_plane", control_plane_api)
    if "src.core" in sys.modules:
        monkeypatch.setattr(sys.modules["src.core"], "control_plane", control_plane_api, raising=False)

    orchestrator = types.SimpleNamespace(
        provider=types.SimpleNamespace(provider_name="gemini"),
        local_model=object(),
        usage_tracker=types.SimpleNamespace(_persistence=object()),
        set_lane_model=lambda lane, model: calls.append(("lane", lane, model)),
        switch_provider=lambda provider: f"switched {provider}",
    )
    gbs.bind_gateway_globals(main_orchestrator=orchestrator)

    try:
        discovery_booted = gbs._bootstrap_model_discovery()
        assert discovery_booted in {True, False}
        router_booted = gbs._bootstrap_model_router()
        assert router_booted in {True, False}
        assert gbs._restore_persisted_provider("openai", orchestrator) is True
        if router_booted:
            assert orchestrator.model_router is not None
        if discovery_booted:
            assert ("lane", "fast", "persisted-fast") in calls
    finally:
        _reset_control_plane_runtime(control_plane)


def test_gateway_boot_support_resolves_peer_keys_from_registry_or_topology():
    import gateway_boot_support as gbs

    registry = types.SimpleNamespace(get_peer_public_key=lambda peer_id: b"registry-key")
    topology = types.SimpleNamespace(get_peer=lambda peer_id: None)
    assert gbs._resolve_peer_key(registry, topology, "peer-1") == b"registry-key"

    registry = types.SimpleNamespace(get_peer_public_key=lambda peer_id: None)
    topology = types.SimpleNamespace(get_peer=lambda peer_id: types.SimpleNamespace(public_key_hex="616263"))
    assert gbs._resolve_peer_key(registry, topology, "peer-2") == b"abc"

    topology = types.SimpleNamespace(get_peer=lambda peer_id: types.SimpleNamespace(public_key_hex="not-hex"))
    assert gbs._resolve_peer_key(registry, topology, "peer-3") is None
