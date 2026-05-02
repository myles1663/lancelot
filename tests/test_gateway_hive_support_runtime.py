import sys
from types import SimpleNamespace

from src.core import gateway_hive_support as hive_support


class Logger:
    def __init__(self):
        self.messages = []

    def info(self, *args):
        self.messages.append(("info", args))

    def warning(self, *args):
        self.messages.append(("warning", args))

    def error(self, *args):
        self.messages.append(("error", args))


def test_orchestrator_router_adapter_uses_deep_model_then_fallback():
    logger = Logger()
    calls = []
    orch = SimpleNamespace(
        provider=object(),
        model_name="fast-model",
        get_deep_model=lambda: "deep-model",
        build_frontier_user_message=lambda text: {"content": text},
    )

    def provider_generate(**kwargs):
        calls.append(kwargs)
        if kwargs["model"] == "deep-model":
            raise RuntimeError("deep failed")
        return SimpleNamespace(text='{"tasks": []}')

    orch.provider_generate = provider_generate
    adapter = hive_support.OrchestratorRouterAdapter(orch, logger)

    result = adapter.route("analysis", "split this")

    assert result.output == '{"tasks": []}'
    assert [call["model"] for call in calls] == ["deep-model", "fast-model"]
    assert any(level == "error" for level, _ in logger.messages)

    orch.provider = None
    assert adapter.route("analysis", "split this").output is None


def test_orchestrator_router_adapter_returns_none_when_both_models_fail():
    logger = Logger()
    orch = SimpleNamespace(
        provider=object(),
        model_name="fast-model",
        get_deep_model=lambda: "deep-model",
        build_frontier_user_message=lambda text: {"content": text},
        provider_generate=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("down")),
    )
    adapter = hive_support.OrchestratorRouterAdapter(orch, logger)

    assert adapter.route("analysis", "split this").output is None
    assert any(level == "warning" for level, _ in logger.messages)


def test_uab_health_summary_and_provider_startup_states(monkeypatch):
    health = SimpleNamespace(
        state=SimpleNamespace(value="degraded"),
        metadata={"daemon_url": "http://uab"},
        error_message="not ready",
    )
    provider = SimpleNamespace(config=SimpleNamespace(daemon_url="http://config"))

    assert hive_support.summarize_uab_provider_health(provider, health) == {
        "state": "degraded",
        "daemon_url": "http://uab",
        "error": "not ready",
    }

    custom_provider = SimpleNamespace(summarize_health=lambda h: {"state": "healthy"})
    assert hive_support.summarize_uab_provider_health(custom_provider, health) == {"state": "healthy"}

    class HealthyUAB:
        def health_check(self):
            return SimpleNamespace()

        def summarize_health(self, health):
            return {"state": "healthy", "daemon_url": "http://uab", "error": ""}

    monkeypatch.setitem(
        sys.modules,
        "src.tools.providers.uab_bridge",
        SimpleNamespace(UABProvider=HealthyUAB),
    )
    logger = Logger()
    provider, status = hive_support.get_uab_provider(logger)
    assert isinstance(provider, HealthyUAB)
    assert status["state"] == "healthy"

    class OfflineUAB(HealthyUAB):
        def summarize_health(self, health):
            return {"state": "unhealthy", "daemon_url": "http://uab", "error": "offline"}

    monkeypatch.setitem(
        sys.modules,
        "src.tools.providers.uab_bridge",
        SimpleNamespace(UABProvider=OfflineUAB),
    )
    provider, status = hive_support.get_uab_provider(logger)
    assert isinstance(provider, OfflineUAB)
    assert status["state"] == "unhealthy"
    assert any(level == "warning" for level, _ in logger.messages)

    class BrokenUAB:
        def __init__(self):
            raise RuntimeError("imported but broken")

    monkeypatch.setitem(
        sys.modules,
        "src.tools.providers.uab_bridge",
        SimpleNamespace(UABProvider=BrokenUAB),
    )
    provider, status = hive_support.get_uab_provider(logger)
    assert provider is None
    assert status["state"] == "unavailable"


def test_init_hive_wires_runtime_objects_and_existing_federation_controls(monkeypatch):
    class Config:
        max_concurrent_agents = 3
        default_task_timeout = 30

    class Lifecycle:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.spawn_controls = None
            self.shutdown_called = False

        def update_spawn_controls(self, **kwargs):
            self.spawn_controls = kwargs

        def shutdown(self):
            self.shutdown_called = True

    class Architect:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class Registry:
        def __init__(self, max_concurrent_agents):
            self.max_concurrent_agents = max_concurrent_agents

    init_calls = []
    monkeypatch.setattr("feature_flags.FEATURE_HIVE_UAB", True, raising=False)
    monkeypatch.setattr(hive_support, "get_uab_provider", lambda logger: (object(), {"state": "healthy", "daemon_url": "http://uab", "error": ""}))
    monkeypatch.setitem(sys.modules, "src.hive.api", SimpleNamespace(init_hive_api=lambda *args, **kwargs: init_calls.append((args, kwargs))))
    monkeypatch.setitem(sys.modules, "src.hive.config", SimpleNamespace(load_hive_config=lambda: Config()))
    monkeypatch.setitem(sys.modules, "src.hive.registry", SimpleNamespace(AgentRegistry=Registry))
    monkeypatch.setitem(sys.modules, "src.hive.receipt_manager", SimpleNamespace(HiveReceiptManager=lambda data_dir: SimpleNamespace(data_dir=data_dir)))
    monkeypatch.setitem(sys.modules, "src.hive.scoped_soul", SimpleNamespace(ScopedSoulGenerator=lambda: "soul-gen"))
    monkeypatch.setitem(sys.modules, "src.hive.integration.governance_bridge", SimpleNamespace(GovernanceBridge=lambda **kwargs: SimpleNamespace(**kwargs)))
    monkeypatch.setitem(sys.modules, "src.hive.integration.uab_executor", SimpleNamespace(HiveUABExecutor=lambda **kwargs: SimpleNamespace(**kwargs)))
    monkeypatch.setitem(sys.modules, "src.hive.lifecycle", SimpleNamespace(AgentLifecycleManager=Lifecycle))
    monkeypatch.setitem(sys.modules, "src.hive.decomposer", SimpleNamespace(TaskDecomposer=lambda model_router: SimpleNamespace(model_router=model_router)))
    monkeypatch.setitem(sys.modules, "src.hive.architect", SimpleNamespace(ArchitectAgent=Architect))

    federation_entry = SimpleNamespace(
        running=True,
        objects={
            "spawn_gate": "gate",
            "spawn_record_hook": "spawn",
            "collapse_record_hook": "collapse",
        },
    )
    subsystem_manager = SimpleNamespace(get=lambda name: federation_entry if name == "federation" else None)
    logger = Logger()
    orchestrator = SimpleNamespace(
        soul="parent-soul",
        _risk_classifier="risk",
        trust_ledger="trust",
        decision_log="decision",
        audit_logger="audit",
        provider=object(),
    )

    objects = hive_support.init_hive(
        main_orchestrator=orchestrator,
        sentry="sentry",
        subsystem_manager=subsystem_manager,
        logger=logger,
    )

    assert objects["registry"].max_concurrent_agents == 3
    assert objects["lifecycle"].spawn_controls["spawn_gate"] == "gate"
    assert objects["lifecycle"].kwargs["action_executor"] is not None
    assert init_calls


def test_init_hive_handles_federation_control_wiring_failure(monkeypatch):
    class Config:
        max_concurrent_agents = 1
        default_task_timeout = 10

    class Lifecycle:
        def __init__(self, **kwargs):
            pass

        def update_spawn_controls(self, **kwargs):
            raise RuntimeError("federation down")

    monkeypatch.setattr("feature_flags.FEATURE_HIVE_UAB", False, raising=False)
    monkeypatch.setitem(sys.modules, "src.hive.api", SimpleNamespace(init_hive_api=lambda *args, **kwargs: None))
    monkeypatch.setitem(sys.modules, "src.hive.config", SimpleNamespace(load_hive_config=lambda: Config()))
    monkeypatch.setitem(sys.modules, "src.hive.registry", SimpleNamespace(AgentRegistry=lambda max_concurrent_agents: object()))
    monkeypatch.setitem(sys.modules, "src.hive.receipt_manager", SimpleNamespace(HiveReceiptManager=lambda data_dir: object()))
    monkeypatch.setitem(sys.modules, "src.hive.scoped_soul", SimpleNamespace(ScopedSoulGenerator=lambda: object()))
    monkeypatch.setitem(sys.modules, "src.hive.integration.governance_bridge", SimpleNamespace(GovernanceBridge=lambda **kwargs: object()))
    monkeypatch.setitem(sys.modules, "src.hive.integration.uab_executor", SimpleNamespace(HiveUABExecutor=lambda **kwargs: object()))
    monkeypatch.setitem(sys.modules, "src.hive.lifecycle", SimpleNamespace(AgentLifecycleManager=Lifecycle))
    monkeypatch.setitem(sys.modules, "src.hive.decomposer", SimpleNamespace(TaskDecomposer=lambda model_router: object()))
    monkeypatch.setitem(sys.modules, "src.hive.architect", SimpleNamespace(ArchitectAgent=lambda **kwargs: object()))
    logger = Logger()
    subsystem_manager = SimpleNamespace(get=lambda name: SimpleNamespace(running=True, objects={}))

    hive_support.init_hive(
        main_orchestrator=SimpleNamespace(audit_logger="audit"),
        sentry=None,
        subsystem_manager=subsystem_manager,
        logger=logger,
    )

    assert any("Failed to wire existing federation" in args[0] for level, args in logger.messages if level == "warning")


def test_shutdown_hive_stops_lifecycle_and_api(monkeypatch):
    shutdown_calls = []
    monkeypatch.setitem(sys.modules, "src.hive.api", SimpleNamespace(shutdown_hive_api=lambda: shutdown_calls.append("api")))
    logger = Logger()
    lifecycle = SimpleNamespace(shutdown=lambda: shutdown_calls.append("lifecycle"))

    hive_support.shutdown_hive({"lifecycle": lifecycle}, logger)

    assert shutdown_calls == ["lifecycle", "api"]
    assert any(level == "info" for level, _ in logger.messages)

    failing = SimpleNamespace(shutdown=lambda: (_ for _ in ()).throw(RuntimeError("stop failed")))
    hive_support.shutdown_hive({"lifecycle": failing}, logger)
    assert shutdown_calls[-1] == "api"
    assert any("shutdown failed" in args[0] for level, args in logger.messages if level == "warning")
