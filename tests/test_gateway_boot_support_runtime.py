import asyncio
import os
import sys
import types
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _module(**attrs):
    return types.SimpleNamespace(**attrs)


def test_memory_soul_and_skills_wire_runtime_objects(monkeypatch, tmp_path):
    from src.core import gateway_boot_support as gbs

    calls = []

    class CoreBlockStore:
        def __init__(self, data_dir):
            self.data_dir = data_dir
            calls.append(("core_store", str(data_dir)))

        def initialize(self):
            calls.append("memory_initialize")

        def bootstrap_from_user_file(self, path):
            calls.append(("bootstrap_user", path))

    class MemoryStoreManager:
        def __init__(self, data_dir):
            self.data_dir = data_dir

    class ContextCompilerService:
        def __init__(self, data_dir, core_store, memory_manager):
            self.data_dir = data_dir
            self.core_store = core_store
            self.memory_manager = memory_manager

    class SkillRegistry:
        def __init__(self, data_dir):
            self.data_dir = data_dir
            self.skills = []

        def ensure_system_skill(self, name):
            self.skills.append(name)

        def list_skills(self):
            return self.skills

    class SkillExecutor:
        def __init__(self, registry):
            self.registry = registry

    class SkillFactory:
        def __init__(self, data_dir, trust_ledger=None):
            self.data_dir = data_dir
            self.trust_ledger = trust_ledger

    monkeypatch.setitem(sys.modules, "memory.store", _module(CoreBlockStore=CoreBlockStore))
    monkeypatch.setitem(sys.modules, "memory.sqlite_store", _module(MemoryStoreManager=MemoryStoreManager))
    monkeypatch.setitem(sys.modules, "memory.compiler", _module(ContextCompilerService=ContextCompilerService))
    monkeypatch.setitem(sys.modules, "skills.registry", _module(SkillRegistry=SkillRegistry))
    monkeypatch.setitem(sys.modules, "skills.executor", _module(SkillExecutor=SkillExecutor))
    monkeypatch.setitem(sys.modules, "skills.factory", _module(SkillFactory=SkillFactory))
    monkeypatch.setitem(
        sys.modules,
        "soul.store",
        _module(
            SoulStoreError=RuntimeError,
            load_active_soul=lambda: types.SimpleNamespace(version="v-test"),
        ),
    )
    monkeypatch.setitem(sys.modules, "soul.api", _module(router=object()))

    task_runner = types.SimpleNamespace(skill_executor=None)
    orchestrator = types.SimpleNamespace(
        task_runner=task_runner,
        trust_ledger="ledger",
        context_compiler=None,
        soul=None,
        _risk_classifier=object(),
        refresh_soul_policy=lambda soul: calls.append(("refresh_soul", soul.version)),
        set_memory_enabled=lambda enabled: calls.append(("memory_enabled", enabled)),
    )
    gbs.bind_gateway_globals(main_orchestrator=orchestrator)

    memory_objects = gbs._init_memory()
    soul_objects = gbs._init_soul()
    skill_objects = gbs._init_skills()

    assert ("memory_enabled", True) in calls
    assert orchestrator.context_compiler is memory_objects["compiler"]
    assert soul_objects["soul"].version == "v-test"
    assert orchestrator.soul.version == "v-test"
    assert orchestrator.skill_executor is skill_objects["executor"]
    assert task_runner.skill_executor is skill_objects["executor"]
    assert "command_runner" in skill_objects["registry"].list_skills()

    gbs._shutdown_skills(skill_objects)
    gbs._shutdown_soul(soul_objects)
    gbs._shutdown_memory(memory_objects)

    assert orchestrator.skill_executor is None
    assert task_runner.skill_executor is None
    assert orchestrator.soul is None
    assert orchestrator.context_compiler is None
    assert ("memory_enabled", False) in calls


def test_scheduler_wires_skill_and_memory_job_execution(monkeypatch):
    from src.core import gateway_boot_support as gbs

    calls = []

    class SchedulerService:
        def __init__(self, data_dir, config_dir):
            self.data_dir = data_dir
            self.config_dir = config_dir
            self.last_scheduler_tick_at = None

        def register_from_config(self):
            calls.append("register_from_config")
            return 2

        def ensure_job_specs(self, specs):
            calls.append(("ensure_job_specs", specs))
            return 1, 1

    class JobExecutor:
        def __init__(self, scheduler_service, skill_execute_fn):
            self.scheduler_service = scheduler_service
            self.skill_execute_fn = skill_execute_fn
            self.started = False
            self.stopped = False

        def start_tick_loop(self):
            self.started = True

        def stop(self):
            self.stopped = True

    class MemoryJobExecutor:
        def __init__(self, core_store, store_manager, data_dir):
            self.core_store = core_store
            self.store_manager = store_manager
            self.data_dir = data_dir

    def execute_memory_job(executor, name, inputs):
        return types.SimpleNamespace(
            success=True,
            errors=[],
            to_dict=lambda: {"job": name, "inputs": inputs},
        )

    skill_executor = types.SimpleNamespace(run=lambda name, inputs: {"skill": name, "inputs": inputs})
    compiler = types.SimpleNamespace(core_store="core", memory_manager="store")
    orchestrator = types.SimpleNamespace(
        skill_executor=skill_executor,
        context_compiler=compiler,
        scheduler_service=None,
        job_executor=None,
        is_memory_enabled=lambda: True,
    )
    monkeypatch.setitem(sys.modules, "scheduler.service", _module(SchedulerService=SchedulerService))
    monkeypatch.setitem(sys.modules, "scheduler.executor", _module(JobExecutor=JobExecutor))
    monkeypatch.setitem(
        sys.modules,
        "memory.jobs",
        _module(
            MEMORY_JOB_SKILLS={"memory_compact"},
            MemoryJobExecutor=MemoryJobExecutor,
            execute_memory_job=execute_memory_job,
            get_memory_job_specs=lambda: [{"name": "memory_compact"}],
        ),
    )
    monkeypatch.setitem(sys.modules, "scheduler_api", _module(init_scheduler_api=lambda **kwargs: calls.append(("api", kwargs))))
    gbs.bind_gateway_globals(main_orchestrator=orchestrator)

    objects = gbs._init_scheduler()

    assert calls[0] == "register_from_config"
    assert objects["memory_jobs_inserted"] == 1
    assert objects["job_executor"].started is True
    assert objects["job_executor"].skill_execute_fn("memory_compact", {"quest": "q1"}) == {
        "job": "memory_compact",
        "inputs": {"quest": "q1"},
    }
    assert objects["job_executor"].skill_execute_fn("telegram_send", {"text": "hi"}) == {
        "skill": "telegram_send",
        "inputs": {"text": "hi"},
    }

    gbs._shutdown_scheduler(objects)
    assert objects["job_executor"].stopped is True
    assert orchestrator.scheduler_service is None
    assert orchestrator.job_executor is None


def test_scheduler_handles_memory_and_api_startup_failures(monkeypatch):
    from src.core import gateway_boot_support as gbs

    calls = []

    class SchedulerService:
        def __init__(self, data_dir, config_dir):
            self.data_dir = data_dir
            self.config_dir = config_dir

        def register_from_config(self):
            return 1

    class JobExecutor:
        def __init__(self, scheduler_service, skill_execute_fn):
            self.skill_execute_fn = skill_execute_fn
            self.is_running = False

        def start_tick_loop(self):
            calls.append("started")

    monkeypatch.setitem(sys.modules, "scheduler.service", _module(SchedulerService=SchedulerService))
    monkeypatch.setitem(sys.modules, "scheduler.executor", _module(JobExecutor=JobExecutor))
    monkeypatch.setitem(sys.modules, "memory.jobs", _module(get_memory_job_specs=lambda: (_ for _ in ()).throw(RuntimeError("memory unavailable"))))
    monkeypatch.setitem(sys.modules, "scheduler_api", _module(init_scheduler_api=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("api unavailable"))))

    orchestrator = types.SimpleNamespace(
        skill_executor=None,
        context_compiler=types.SimpleNamespace(core_store="core", memory_manager="store"),
        scheduler_service=None,
        job_executor=None,
        is_memory_enabled=lambda: True,
    )
    gbs.bind_gateway_globals(main_orchestrator=orchestrator)

    objects = gbs._init_scheduler()

    assert objects["job_executor"] is None
    assert objects["memory_job_executor"] is None
    assert orchestrator.scheduler_service is objects["service"]


def test_health_monitor_reports_local_model_paths(monkeypatch):
    from src.core import gateway_boot_support as gbs

    availability = []
    snapshot_provider = {}

    class HealthCheck:
        def __init__(self, name, check_fn, degraded_reason, snapshot_details_fn=None):
            self.name = name
            self.check_fn = check_fn
            self.degraded_reason = degraded_reason
            self.snapshot_details_fn = snapshot_details_fn

    class HealthMonitor:
        def __init__(self, checks, interval_s):
            self.checks = checks
            self.interval_s = interval_s
            self.latest_snapshot = {"status": "ok"}
            self.started = False
            self.stopped = False

        def start_monitor(self):
            self.started = True

        def stop_monitor(self):
            self.stopped = True

    monkeypatch.setitem(sys.modules, "health.monitor", _module(HealthMonitor=HealthMonitor, HealthCheck=HealthCheck))
    monkeypatch.setitem(sys.modules, "health.api", _module(set_snapshot_provider=lambda fn: snapshot_provider.setdefault("fn", fn)))
    monkeypatch.setitem(
        sys.modules,
        "src.core.model_usage_policy",
        _module(set_local_model_availability=lambda *args, **kwargs: availability.append((args, kwargs))),
    )
    monkeypatch.setattr(
        gbs,
        "summarize_local_model_router_readiness",
        lambda roles: dict(roles or {}),
    )

    onboarding = types.SimpleNamespace(determine_state=lambda: "READY")
    scheduler_service = types.SimpleNamespace(last_scheduler_tick_at="2026-05-01T00:00:00Z")
    orchestrator = types.SimpleNamespace(
        provider=object(),
        local_model=None,
        local_model_roles={
            "ready": True,
            "last_verified_at": "verified",
            "last_checked_at": "checked",
            "last_smoke_elapsed_ms": 7,
        },
        scheduler_service=scheduler_service,
        job_executor=types.SimpleNamespace(is_running=True),
    )
    gbs.bind_gateway_globals(main_orchestrator=orchestrator, onboarding_orch=onboarding)

    objects = gbs._init_health_monitor()
    checks = {check.name: check for check in objects["monitor"].checks}

    assert objects["monitor"].started is True
    assert checks["llm_provider"].check_fn() is True
    assert checks["onboarding_ready"].check_fn() is True
    assert checks["scheduler"].check_fn() is True
    assert checks["scheduler"].snapshot_details_fn()["last_scheduler_tick_at"] == "2026-05-01T00:00:00Z"
    assert checks["local_llm"].check_fn() is True
    assert checks["local_llm"].snapshot_details_fn()["local_llm_status"] == "ok"
    assert availability[-1][0][0] is True
    assert snapshot_provider["fn"]() == {"status": "ok"}

    class FailingLocalModel:
        def is_healthy(self):
            raise RuntimeError("probe failed")

        def health(self):
            raise RuntimeError("health failed")

    orchestrator.local_model = FailingLocalModel()
    orchestrator.local_model_roles = {"ready": False, "loaded": True, "last_smoke_elapsed_ms": 9}

    assert checks["local_llm"].check_fn() is False
    failure_details = checks["local_llm"].snapshot_details_fn()
    assert failure_details["local_llm_status"] == "unavailable"
    assert failure_details["local_llm_last_error"] == "health failed"
    assert availability[-1][0][0] is False

    gbs._shutdown_health_monitor(objects)
    assert objects["monitor"].stopped is True


def test_tool_fabric_hot_toggles_register_and_unregister_providers(monkeypatch):
    from src.core import gateway_boot_support as gbs

    calls = []

    class Fabric:
        config = types.SimpleNamespace(default_workspace="C:/workspace")

        def register_provider(self, provider):
            calls.append(("register", provider.provider_id))

        def unregister_provider(self, provider_id):
            calls.append(("unregister", provider_id))

        def update_router_preferences(self):
            calls.append("router")

    class HostBridgeProvider:
        provider_id = "host_bridge"

        def __init__(self, workspace):
            self.workspace = workspace
            self.config = types.SimpleNamespace(agent_url="http://127.0.0.1:8765")

    class UABProvider:
        provider_id = "uab_bridge"

        def __init__(self):
            self.config = types.SimpleNamespace(daemon_url="http://127.0.0.1:3939")

    monkeypatch.setitem(sys.modules, "src.tools.fabric", _module(get_tool_fabric=lambda: Fabric()))
    monkeypatch.setitem(sys.modules, "src.tools.providers.host_bridge", _module(HostBridgeProvider=HostBridgeProvider))
    monkeypatch.setitem(sys.modules, "src.tools.providers.uab_bridge", _module(UABProvider=UABProvider))

    host_objects = gbs._init_host_bridge()
    gbs._shutdown_host_bridge(host_objects)
    uab_objects = gbs._init_uab()
    gbs._shutdown_uab(uab_objects)

    assert host_objects["provider"].workspace == "C:/workspace"
    assert [call for call in calls if isinstance(call, tuple)] == [
        ("register", "host_bridge"),
        ("unregister", "host_bridge"),
        ("register", "uab_bridge"),
        ("unregister", "uab_bridge"),
    ]


def test_peer_key_resolution_prefers_registry_and_handles_topology_fallback(monkeypatch):
    from src.core import gateway_boot_support as gbs

    registry = types.SimpleNamespace(get_peer_public_key=lambda instance_id: b"registry-key")
    topology = types.SimpleNamespace(get_peer=lambda instance_id: types.SimpleNamespace(public_key_hex="aabb"))
    assert gbs._resolve_peer_key(registry, topology, "peer-1") == b"registry-key"

    failing_registry = types.SimpleNamespace(get_peer_public_key=lambda instance_id: (_ for _ in ()).throw(RuntimeError("db down")))
    assert gbs._resolve_peer_key(failing_registry, topology, "peer-1") == bytes.fromhex("aabb")

    bad_topology = types.SimpleNamespace(get_peer=lambda instance_id: types.SimpleNamespace(public_key_hex="not-hex"))
    assert gbs._resolve_peer_key(failing_registry, bad_topology, "peer-1") is None
    empty_topology = types.SimpleNamespace(get_peer=lambda instance_id: None)
    assert gbs._resolve_peer_key(failing_registry, empty_topology, "peer-1") is None


def test_federation_startup_wires_transport_and_governance_hooks(monkeypatch, tmp_path):
    from src.core import gateway_boot_support as gbs

    calls = []
    monkeypatch.setenv("LANCELOT_DATA_DIR", str(tmp_path / "data"))

    identity = types.SimpleNamespace(
        instance_id="local-instance",
        fingerprint="fp-local",
        public_key_hex=lambda: "abcd",
    )
    config = types.SimpleNamespace(
        staleness_warning_s=10.0,
        staleness_critical_s=20.0,
        staleness_lost_s=30.0,
        max_peers=10,
        topology_path=str(tmp_path / "topology.json"),
        daily_budget_ceiling_usd=25.0,
        heartbeat_interval_s=2.0,
        auth_timestamp_window_s=60,
        nonce_cache_size=100,
        retry_max_attempts=1,
        retry_base_backoff_s=0.01,
        circuit_breaker_threshold=3,
        circuit_breaker_recovery_s=5.0,
        connect_timeout_s=1.0,
        read_timeout_s=1.0,
        handoff_timeout_s=30.0,
        cost_report_interval_s=5.0,
        self_address="https://local.example",
        peer_db_path="",
    )

    class HeartbeatEmitter:
        def __init__(self, instance_id, interval_s=2.0):
            self.instance_id = instance_id
            self.interval_s = interval_s
            self.providers = {}
            self.started = False
            self.stopped = False

        def set_providers(self, **providers):
            self.providers = providers

        def start(self):
            self.started = True
            calls.append("emitter_start")

        def stop(self):
            self.stopped = True
            calls.append("emitter_stop")

    class TopologyRegistry:
        def __init__(self, **kwargs):
            self.deployment_mode = types.SimpleNamespace(value="standalone")
            self.peers = []

        def list_peers(self):
            return self.peers

        def peer_count(self):
            return len(self.peers)

        def get_peer(self, instance_id):
            return None

    class DivergenceDetector:
        def __init__(self, **kwargs):
            self.state = types.SimpleNamespace(value="connected")
            self.divergence_snapshot = None
            self.last_reconciliation = None
            self.divergence_evaluation_failed = False

        def get_divergence_duration_s(self):
            return 12.0

        def mark_reconciled(self, outcome, conflicts):
            calls.append(("mark_reconciled", outcome, conflicts))

        def reset_to_connected(self):
            self.state.value = "connected"

    class FederationReceiptManager:
        def __init__(self, **kwargs):
            self.calls = []

        def record_divergence(self, **kwargs):
            self.calls.append(("divergence", kwargs))

        def record_reconnection(self, **kwargs):
            self.calls.append(("reconnection", kwargs))

        def record_budget_threshold(self, **kwargs):
            self.calls.append(("budget_threshold", kwargs))

        def record_spawn_receipt(self, **kwargs):
            self.calls.append(("spawn", kwargs))

    class FederationAuditEngine:
        def __init__(self, **kwargs):
            self.entries = []

        def record(self, **kwargs):
            self.entries.append(kwargs)

    class FederationTransport:
        def __init__(self, **kwargs):
            self.started = False

        async def start(self):
            self.started = True
            calls.append("transport_start")

    class PeerRegistryStore:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def get_peer_public_key(self, instance_id):
            return b"peer-key"

    class Passive:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def list_active_handoffs(self):
            return []

    class CostAggregator:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.allowed = True
            self.reason = ""

        def check_spawn_allowed(self, instance_id):
            return self.allowed, self.reason

        def get_aggregate(self):
            return types.SimpleNamespace(utilization_pct=42.0)

        def remove_instance(self, instance_id):
            calls.append(("remove_cost", instance_id))

    class BudgetTracker:
        def __init__(self):
            self.threshold_level = types.SimpleNamespace(value="normal")
            self.utilization_pct = 10.0
            self.decision = types.SimpleNamespace(value="allowed")
            self.reason = "ok"
            self.spawns = []
            self.collapses = []

        def check_spawn(self, **kwargs):
            return self.decision, self.reason

        def record_spawn(self, **kwargs):
            self.spawns.append(kwargs)

        def record_collapse(self, agent_id, actual_tokens=0):
            self.collapses.append((agent_id, actual_tokens))

        def get_snapshot(self):
            return {"threshold": self.threshold_level.value}

    class CostReporter:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.running = False

        async def start(self):
            self.running = True
            calls.append("cost_start")

        def get_aggregate_status(self):
            return {"utilization_pct": 20.0}

    class HeartbeatMesh:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.running = False
            self.divergence_evaluation_failed = False

        async def start(self):
            self.running = True
            calls.append("mesh_start")

        def on_peer_removed(self, instance_id):
            calls.append(("remove_mesh", instance_id))

    class RuntimeBudgetResolver:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def resolve_daily_ceiling_usd(self):
            return 25.0

    class FederatedKillSwitch:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FederationAuth:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class ReconciliationOutcome:
        COMPATIBLE = types.SimpleNamespace(value="compatible")

    monkeypatch.setitem(sys.modules, "src.federation.config", _module(load_federation_config=lambda: config))
    monkeypatch.setitem(sys.modules, "src.federation.identity", _module(load_or_generate_identity=lambda data_dir: identity))
    monkeypatch.setitem(sys.modules, "src.federation.heartbeat", _module(HeartbeatEmitter=HeartbeatEmitter))
    monkeypatch.setitem(sys.modules, "src.federation.topology", _module(TopologyRegistry=TopologyRegistry))
    monkeypatch.setitem(
        sys.modules,
        "src.federation.divergence",
        _module(
            DivergenceDetector=DivergenceDetector,
            ReconciliationOutcome=ReconciliationOutcome,
            reconcile_divergence=lambda **kwargs: (ReconciliationOutcome.COMPATIBLE, []),
        ),
    )
    monkeypatch.setitem(sys.modules, "src.federation.receipt_manager", _module(FederationReceiptManager=FederationReceiptManager))
    monkeypatch.setitem(
        sys.modules,
        "src.federation.api",
        _module(
            init_federation_api=lambda **kwargs: calls.append(("init_api", kwargs)),
            init_federation_transport=lambda **kwargs: calls.append(("init_transport", kwargs)),
            shutdown_federation_api=lambda: calls.append("shutdown_api"),
        ),
    )
    monkeypatch.setitem(sys.modules, "src.federation.auth", _module(FederationAuth=FederationAuth))
    monkeypatch.setitem(sys.modules, "src.federation.soul_compat", _module(hash_soul=lambda soul: "soul-hash" if soul else ""))
    monkeypatch.setitem(sys.modules, "src.federation.transport", _module(FederationTransport=FederationTransport))
    monkeypatch.setitem(sys.modules, "src.federation.peer_registry", _module(PeerRegistryStore=PeerRegistryStore))
    monkeypatch.setitem(sys.modules, "src.federation.peer_protocol", _module(PeerRegistrationProtocol=Passive))
    monkeypatch.setitem(sys.modules, "src.federation.command_relay", _module(CommandRelay=Passive))
    monkeypatch.setitem(sys.modules, "src.federation.soul_transport", _module(SoulTransport=Passive))
    monkeypatch.setitem(sys.modules, "src.federation.soul_propagation", _module(SoulPropagationEngine=Passive))
    monkeypatch.setitem(sys.modules, "src.federation.handoff_protocol", _module(HandoffProtocol=Passive))
    monkeypatch.setitem(sys.modules, "src.federation.cost_reporter", _module(CostReporter=CostReporter))
    monkeypatch.setitem(sys.modules, "src.federation.cost_aggregation", _module(FederatedCostAggregator=CostAggregator))
    monkeypatch.setitem(sys.modules, "src.federation.budget", _module(FederationBudgetTracker=BudgetTracker))
    monkeypatch.setitem(sys.modules, "src.federation.heartbeat_mesh", _module(HeartbeatMesh=HeartbeatMesh))
    monkeypatch.setitem(sys.modules, "src.federation.audit", _module(FederationAuditEngine=FederationAuditEngine))
    monkeypatch.setitem(sys.modules, "src.federation.runtime_budget_source", _module(RuntimeBudgetResolver=RuntimeBudgetResolver))
    monkeypatch.setitem(
        sys.modules,
        "src.federation.runtime_budget_control",
        _module(handle_federation_cost_threshold_change=lambda *args, **kwargs: calls.append("threshold_change")),
    )
    monkeypatch.setitem(sys.modules, "src.federation.kill_switch", _module(FederatedKillSwitch=FederatedKillSwitch))
    monkeypatch.setitem(sys.modules, "src.federation.graph_api", _module(init_graph_api=lambda path: calls.append(("graph", path))))

    lifecycle_calls = []

    class AgentState:
        EXECUTING = object()
        PAUSED = object()

    executing_record = types.SimpleNamespace(agent_id="agent-executing")
    paused_record = types.SimpleNamespace(agent_id="agent-paused")

    class Registry:
        def active_count(self):
            return 2

        def list_active(self):
            return []

        def list_by_state(self, state):
            if state is AgentState.EXECUTING:
                return [executing_record]
            if state is AgentState.PAUSED:
                return [paused_record]
            return []

    lifecycle = types.SimpleNamespace(
        update_spawn_controls=lambda **kwargs: lifecycle_calls.append(kwargs),
        kill_all=lambda *args, **kwargs: ["agent-1"],
        pause=lambda *args, **kwargs: lifecycle_calls.append(("pause", args, kwargs)),
        resume=lambda *args, **kwargs: lifecycle_calls.append(("resume", args, kwargs)),
    )
    hive_entry = types.SimpleNamespace(
        running=True,
        objects={
            "lifecycle": lifecycle,
            "registry": Registry(),
        },
    )
    subsystem_manager = types.SimpleNamespace(get=lambda name: hive_entry if name == "hive" else None)
    app = types.SimpleNamespace(state=types.SimpleNamespace(active_soul=object(), apply_runtime_soul=lambda soul: None))
    orchestrator = types.SimpleNamespace(
        soul=object(),
        usage_tracker=types.SimpleNamespace(summary=lambda: {"total_cost_est": 3.5, "total_tokens_est": 1200}),
        agent_busy=True,
        job_executor=types.SimpleNamespace(
            clear_approval_state=lambda **kwargs: {"pending_cleared": 2, "granted_cleared": 1},
        ),
    )
    sentry = types.SimpleNamespace(
        pending_requests={"req-1": {"status": "PENDING"}, "req-2": {"status": "DONE"}},
        cleanup_expired=lambda: None,
        deny_request=lambda req_id: True,
    )
    monkeypatch.setitem(sys.modules, "src.hive.types", _module(AgentState=AgentState))
    monkeypatch.setitem(
        sys.modules,
        "src.core.runtime_pause",
        _module(
            pause_runtime=lambda *args, **kwargs: lifecycle_calls.append(("runtime_pause", args, kwargs)),
            resume_runtime=lambda *args, **kwargs: lifecycle_calls.append(("runtime_resume", args, kwargs)),
        ),
    )
    gbs.bind_gateway_globals(
        app=app,
        main_orchestrator=orchestrator,
        subsystem_manager=subsystem_manager,
        sentry=sentry,
    )

    objects = gbs._init_federation()

    assert objects["identity"] is identity
    assert objects["emitter"].started is True
    assert lifecycle_calls and lifecycle_calls[0]["spawn_gate"] is objects["spawn_gate"]
    assert any(call[0] == "init_api" for call in calls)
    assert any(call[0] == "init_transport" for call in calls)
    assert ("graph", str(tmp_path / "data" / "federation")) in calls

    # Heartbeat/runtime providers expose live budget, Soul, HIVE, and handoff state.
    assert objects["emitter"].providers["budget"]() == pytest.approx(14.0)
    assert objects["emitter"].providers["peer_count"]() == 0
    assert objects["heartbeat_mesh"].kwargs["current_soul_hash_provider"]() == "soul-hash"
    assert objects["heartbeat_mesh"].kwargs["active_task_count_provider"]() == 1
    assert objects["heartbeat_mesh"].kwargs["hive_spawn_count_provider"]() == 2
    assert objects["heartbeat_mesh"].kwargs["hive_spawn_states_provider"]() == {}
    assert objects["heartbeat_mesh"].kwargs["pending_handoffs_provider"]() == []
    assert objects["heartbeat_mesh"].kwargs["budget_utilization_provider"]() == 20.0

    snapshot = types.SimpleNamespace(
        soul_hash_at_divergence="peer-soul",
        to_dict=lambda: {"peer": "stale"},
    )
    objects["heartbeat_mesh"].kwargs["on_diverged"]("peer-1", snapshot)
    assert objects["receipt_mgr"].calls[-1][0] == "divergence"
    assert objects["audit_engine"].entries[-1]["event_type"] == "divergence_detected"

    objects["divergence"].divergence_snapshot = snapshot
    objects["heartbeat_mesh"].kwargs["on_reconnecting"]("peer-1", objects["divergence"])
    assert objects["receipt_mgr"].calls[-1][0] == "reconnection"
    assert ("mark_reconciled", ReconciliationOutcome.COMPATIBLE, []) in calls

    objects["peer_protocol"].kwargs["on_peer_removed"]("peer-2")
    assert ("remove_mesh", "peer-2") in calls
    assert ("remove_cost", "peer-2") in calls

    kill_switch = objects["command_relay"].kwargs["kill_switch"]
    assert kill_switch.kwargs["local_kill_handler"]("peer kill") == 1
    original_get = subsystem_manager.get
    subsystem_manager.get = lambda name: None
    assert kill_switch.kwargs["local_kill_handler"]("peer kill") == 0
    assert objects["command_relay"].kwargs["local_pause_handler"]("peer pause") == {
        "paused_agents": 0,
        "already_paused_agents": 0,
        "execution_state": "paused",
        "full_stop": False,
        "scheduler_pending_cleared": 0,
        "scheduler_granted_cleared": 0,
        "sentry_pending_denied": 0,
    }
    assert objects["command_relay"].kwargs["local_resume_handler"]("peer resume") == {
        "resumed_agents": 0,
        "execution_state": "running",
    }
    subsystem_manager.get = original_get

    # Exercise returned governance hooks with realistic spawn/collapse records.
    task_spec = types.SimpleNamespace(context={"model_tier": "T2", "estimated_tokens": "55"})
    objects["spawn_gate"](task_spec)
    record = types.SimpleNamespace(
        agent_id="agent-1",
        quest_id="quest-1",
        task_spec=task_spec,
    )
    objects["spawn_record_hook"](record)
    objects["collapse_record_hook"](record, types.SimpleNamespace(outputs={"actual_tokens": "34"}))

    assert objects["budget_tracker"].spawns[0]["agent_id"] == "agent-1"
    assert objects["budget_tracker"].collapses == [("agent-1", 34)]

    objects["divergence"].state.value = "diverged"
    with pytest.raises(RuntimeError, match="divergence active"):
        objects["spawn_gate"](types.SimpleNamespace(context={"model_tier": "T3"}))

    objects["divergence"].state.value = "connected"
    objects["heartbeat_mesh"].divergence_evaluation_failed = True
    with pytest.raises(RuntimeError, match="evaluation unavailable"):
        objects["spawn_gate"](types.SimpleNamespace(context={"model_tier": "T3"}))

    objects["heartbeat_mesh"].divergence_evaluation_failed = False
    objects["cost_aggregator"].allowed = False
    objects["cost_aggregator"].reason = "fleet budget locked"
    with pytest.raises(RuntimeError, match="fleet budget locked"):
        objects["spawn_gate"](types.SimpleNamespace(context={"model_tier": "T2"}))

    objects["cost_aggregator"].allowed = True
    objects["budget_tracker"].decision = types.SimpleNamespace(value="restricted")
    objects["budget_tracker"].threshold_level = types.SimpleNamespace(value="spawn_restricted")
    objects["spawn_gate"](types.SimpleNamespace(context={"model_tier": "T2"}))
    assert objects["receipt_mgr"].calls[-1][0] == "budget_threshold"

    objects["budget_tracker"].decision = types.SimpleNamespace(value="blocked")
    objects["budget_tracker"].reason = "local budget exceeded"
    with pytest.raises(RuntimeError, match="local budget exceeded"):
        objects["spawn_gate"](types.SimpleNamespace(context={"model_tier": "T2", "estimated_tokens": object()}))

    objects["budget_tracker"].decision = types.SimpleNamespace(value="allowed")
    bad_record = types.SimpleNamespace(
        agent_id="agent-bad",
        quest_id="quest-bad",
        task_spec=types.SimpleNamespace(context={"model_tier": "T1", "estimated_tokens": object()}),
    )
    objects["spawn_record_hook"](bad_record)
    objects["collapse_record_hook"](bad_record, types.SimpleNamespace(outputs={"actual_tokens": object()}))
    assert objects["budget_tracker"].spawns[-1]["estimated_tokens"] == 0
    assert objects["budget_tracker"].collapses[-1] == ("agent-bad", 0)

    pause_result = objects["command_relay"].kwargs["local_pause_handler"]("peer pause", full_stop=True)
    resume_result = objects["command_relay"].kwargs["local_resume_handler"]("peer resume")

    assert pause_result["paused_agents"] == 1
    assert pause_result["already_paused_agents"] == 1
    assert pause_result["scheduler_pending_cleared"] == 2
    assert pause_result["sentry_pending_denied"] == 1
    assert resume_result == {"resumed_agents": 1, "execution_state": "running"}
    assert any(isinstance(call, tuple) and call[0] == "pause" for call in lifecycle_calls)
    assert any(isinstance(call, tuple) and call[0] == "resume" for call in lifecycle_calls)

    failing_lifecycle = types.SimpleNamespace(
        pause=lambda agent_id, *args, **kwargs: (_ for _ in ()).throw(RuntimeError("pause failed")),
        resume=lambda agent_id, *args, **kwargs: (_ for _ in ()).throw(RuntimeError("resume failed")),
    )
    hive_entry.objects["lifecycle"] = failing_lifecycle
    with pytest.raises(RuntimeError, match="Failed to pause active agents"):
        objects["command_relay"].kwargs["local_pause_handler"]("peer pause")
    with pytest.raises(RuntimeError, match="Failed to resume paused agents"):
        objects["command_relay"].kwargs["local_resume_handler"]("peer resume")

    gbs._shutdown_federation(objects)
    assert "emitter_stop" in calls
    assert "shutdown_api" in calls


def test_model_discovery_bootstrap_restores_lanes_and_provider_api(monkeypatch):
    from src.core import gateway_boot_support as gbs

    calls = []

    class ModelDiscovery:
        def __init__(self, provider, profiles_path, lane_overrides, fallback_lanes=None):
            if fallback_lanes is not None:
                raise TypeError("old constructor")
            self.provider = provider
            self.profiles_path = profiles_path
            self.lane_overrides = lane_overrides
            self.discovered_models = ["fast-model", "deep-model"]
            self.lane_assignments = {
                "fast": "fast-model",
                "deep": "deep-model",
                "cache": "persisted-cache",
            }

        def refresh(self):
            calls.append("refresh")

    class ProfileRegistry:
        def has_provider(self, provider_name):
            return True

        def get_profile(self, provider_name):
            return types.SimpleNamespace(
                fast=types.SimpleNamespace(model="profile-fast"),
                deep=types.SimpleNamespace(model="profile-deep"),
                cache=types.SimpleNamespace(model="profile-cache"),
            )

    monkeypatch.setitem(sys.modules, "model_discovery", _module(ModelDiscovery=ModelDiscovery))
    monkeypatch.setitem(sys.modules, "provider_profile", _module(ProfileRegistry=ProfileRegistry))
    monkeypatch.setitem(
        sys.modules,
        "providers.api",
        _module(
            load_persisted_config=lambda: {"lane_overrides": {"cache": "persisted-cache", "slow": "persisted-slow"}},
            init_provider_api=lambda discovery, orchestrator: calls.append(("provider_api", discovery, orchestrator)),
            ensure_persisted_active_provider=lambda provider_name: calls.append(("persist", provider_name)) or True,
        ),
    )
    monkeypatch.setattr(gbs, "bootstrap_model_router", lambda: calls.append("router") or True)

    lane_calls = []
    orchestrator = types.SimpleNamespace(
        provider=types.SimpleNamespace(provider_name="openai"),
        set_lane_model=lambda lane, model: lane_calls.append((lane, model)),
    )
    gbs.bind_gateway_globals(main_orchestrator=types.SimpleNamespace(provider=None))
    assert gbs._bootstrap_model_discovery() is False

    gbs.bind_gateway_globals(main_orchestrator=orchestrator)
    assert gbs._bootstrap_model_discovery() is True

    assert "refresh" in calls
    assert ("provider_api", calls[1][1], orchestrator) in calls
    assert ("persist", "openai") in calls
    assert "router" in calls
    assert ("fast", "fast-model") in lane_calls
    assert ("slow", "persisted-slow") in lane_calls


def test_model_discovery_bootstrap_reports_failures(monkeypatch):
    from src.core import gateway_boot_support as gbs

    monkeypatch.setitem(sys.modules, "model_discovery", _module(ModelDiscovery=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("discovery failed"))))
    monkeypatch.setitem(
        sys.modules,
        "providers.api",
        _module(
            load_persisted_config=lambda: {},
            init_provider_api=lambda *args, **kwargs: None,
            ensure_persisted_active_provider=lambda provider_name: False,
        ),
    )
    monkeypatch.setitem(sys.modules, "provider_profile", _module(ProfileRegistry=lambda: (_ for _ in ()).throw(RuntimeError("profile failed"))))
    gbs.bind_gateway_globals(main_orchestrator=types.SimpleNamespace(provider=types.SimpleNamespace(provider_name="openai")))

    assert gbs._bootstrap_model_discovery() is False


def test_restore_persisted_provider_paths():
    from src.core import gateway_boot_support as gbs

    calls = []
    assert gbs._restore_persisted_provider("") is False
    assert gbs._restore_persisted_provider("openai", orchestrator=None) is False

    empty_orch = types.SimpleNamespace(
        provider=None,
        switch_provider=lambda provider: calls.append(("switch-empty", provider)) or "ok",
    )
    assert gbs._restore_persisted_provider("openai", orchestrator=empty_orch) is True

    same_orch = types.SimpleNamespace(
        provider=types.SimpleNamespace(provider_name="openai"),
        switch_provider=lambda provider: calls.append(("switch-same", provider)),
    )
    assert gbs._restore_persisted_provider("openai", orchestrator=same_orch) is False

    diff_orch = types.SimpleNamespace(
        provider=types.SimpleNamespace(provider_name="anthropic"),
        switch_provider=lambda provider: calls.append(("switch-diff", provider)) or "ok",
    )
    assert gbs._restore_persisted_provider("openai", orchestrator=diff_orch) is True

    failing_orch = types.SimpleNamespace(
        provider=None,
        switch_provider=lambda provider: (_ for _ in ()).throw(RuntimeError("missing credential")),
    )
    assert gbs._restore_persisted_provider("openai", orchestrator=failing_orch) is False
    assert ("switch-empty", "openai") in calls
    assert ("switch-diff", "openai") in calls


def test_model_router_bootstrap_wires_usage_tracker_and_control_plane(monkeypatch):
    from src.core import gateway_boot_support as gbs

    calls = []

    class Usage:
        def __init__(self):
            self.persistence = None

        def set_persistence(self, persistence):
            self.persistence = persistence
            calls.append(("persistence", persistence))

    class ModelRouter:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.usage = Usage()

    monkeypatch.setitem(sys.modules, "model_router", _module(ModelRouter=ModelRouter))
    monkeypatch.setitem(sys.modules, "provider_profile", _module(ProfileRegistry=lambda: "registry"))
    monkeypatch.setitem(
        sys.modules,
        "src.core.control_plane",
        _module(
            set_model_router=lambda router: calls.append(("router", router)),
            set_usage_tracker=lambda usage: calls.append(("usage", usage)),
        ),
    )

    gbs.bind_gateway_globals(main_orchestrator=types.SimpleNamespace(provider=None))
    assert gbs._bootstrap_model_router() is False

    orchestrator = types.SimpleNamespace(
        provider=types.SimpleNamespace(provider_name="openai"),
        local_model="local",
        local_model_roles={"fast": "ready"},
        usage_tracker=types.SimpleNamespace(_persistence="disk"),
        model_router=None,
    )
    gbs.bind_gateway_globals(main_orchestrator=orchestrator)
    assert gbs._bootstrap_model_router() is True

    assert orchestrator.model_router.kwargs["local_client"] == "local"
    assert orchestrator.usage_tracker.persistence == "disk"
    assert calls[-2][0] == "router"
    assert calls[-1][0] == "usage"

    monkeypatch.setitem(sys.modules, "model_router", _module(ModelRouter=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("router failed"))))
    assert gbs._bootstrap_model_router() is False
