"""Subsystem startup and shutdown helpers for the runtime gateway."""
from __future__ import annotations
import asyncio
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from gateway_health import summarize_local_model_router_readiness
# Bound from gateway.py at import time so these helpers keep the original runtime objects.
logger = logging.getLogger("lancelot.gateway.boot_support")
app = None
main_orchestrator = None
onboarding_orch = None
librarian = None
antigravity = None
mfa_guard = None
webhook_auth = None
sentry = None
forge_vault = None
forge_sandbox = None
forge_discovery = None
forge_dispatcher = None
chat_poller = None
telegram_bot = None
scheduler_service = None
def bind_gateway_globals(**kwargs):
    globals().update(kwargs)

def _init_memory():
    """Initialize structured memory subsystem."""
    from memory.store import CoreBlockStore
    from memory.sqlite_store import MemoryStoreManager
    from memory.compiler import ContextCompilerService

    mem_data_dir = Path("/home/lancelot/data")
    core_store = CoreBlockStore(data_dir=mem_data_dir)
    core_store.initialize()
    user_md = mem_data_dir / "USER.md"
    if user_md.exists():
        core_store.bootstrap_from_user_file(str(user_md))

    store_manager = MemoryStoreManager(data_dir=mem_data_dir)
    compiler_svc = ContextCompilerService(
        data_dir=mem_data_dir,
        core_store=core_store,
        memory_manager=store_manager,
    )
    main_orchestrator.set_memory_enabled(True)
    main_orchestrator.context_compiler = compiler_svc
    logger.info("Structured memory initialized and wired.")
    return {"core_store": core_store, "store_manager": store_manager, "compiler": compiler_svc}


def _shutdown_memory(objects):
    """Shut down structured memory subsystem."""
    main_orchestrator.set_memory_enabled(False)
    main_orchestrator.context_compiler = None
    logger.info("Structured memory shut down.")
def _init_soul():
    """Initialize Soul subsystem."""
    from soul.store import load_active_soul, SoulStoreError
    from soul.api import router as soul_router

    active_soul = load_active_soul()
    if active_soul is None:
        logger.warning("No active soul found; Soul subsystem starting without a soul document")
        main_orchestrator.soul = None
        return {"soul": None}

    main_orchestrator.soul = active_soul
    if getattr(main_orchestrator, "_risk_classifier", None) is not None:
        try:
            main_orchestrator.refresh_soul_policy(active_soul)
        except Exception as exc:
            logger.warning("Risk classifier Soul update failed during Soul init: %s", exc)
    logger.info("Soul loaded: version=%s", active_soul.version)
    return {"soul": active_soul}


def _shutdown_soul(objects):
    """Shut down Soul subsystem."""
    main_orchestrator.soul = None
    logger.info("Soul shut down.")


def _init_skills():
    """Initialize Skills subsystem."""
    from skills.registry import SkillRegistry
    from skills.executor import SkillExecutor
    from skills.factory import SkillFactory

    skill_registry = SkillRegistry(data_dir="/home/lancelot/data")
    for builtin_name in ("echo", "command_runner", "repo_writer", "service_runner",
                         "network_client", "telegram_send", "warroom_send", "schedule_job",
                         "health_check", "document_creator", "skill_manager",
                         "memory_query"):
        skill_registry.ensure_system_skill(builtin_name)

    executor = SkillExecutor(registry=skill_registry)
    skill_factory = SkillFactory(
        data_dir="/home/lancelot/data",
        trust_ledger=getattr(main_orchestrator, "trust_ledger", None),
    )

    main_orchestrator.skill_executor = executor
    main_orchestrator.skill_factory = skill_factory
    main_orchestrator.skill_registry = skill_registry
    if main_orchestrator.task_runner:
        main_orchestrator.task_runner.skill_executor = executor
    logger.info("Skills initialized: %d skills (factory enabled)", len(skill_registry.list_skills()))
    return {"registry": skill_registry, "executor": executor, "factory": skill_factory}


def _shutdown_skills(objects):
    """Shut down Skills subsystem."""
    main_orchestrator.skill_executor = None
    if main_orchestrator.task_runner:
        main_orchestrator.task_runner.skill_executor = None
    logger.info("Skills shut down.")


def _init_scheduler():
    """Initialize Scheduler subsystem."""
    global scheduler_service
    from scheduler.service import SchedulerService

    service = SchedulerService(
        data_dir="/home/lancelot/data/scheduler",
        config_dir="config",
    )
    scheduler_service = service
    count = service.register_from_config()
    main_orchestrator.scheduler_service = service
    logger.info("Scheduler initialized: %d jobs registered", count)

    memory_job_executor = None
    MEMORY_JOB_SKILLS = set()
    execute_memory_job = None
    skill_executor = main_orchestrator.skill_executor
    memory_jobs_inserted = 0
    memory_jobs_updated = 0

    if main_orchestrator.is_memory_enabled() and main_orchestrator.context_compiler:
        try:
            from memory.jobs import (
                MEMORY_JOB_SKILLS,
                MemoryJobExecutor,
                execute_memory_job,
                get_memory_job_specs,
            )

            compiler_svc = main_orchestrator.context_compiler
            memory_job_executor = MemoryJobExecutor(
                core_store=compiler_svc.core_store,
                store_manager=compiler_svc.memory_manager,
                data_dir=Path("/home/lancelot/data"),
            )
            memory_jobs_inserted, memory_jobs_updated = service.ensure_job_specs(get_memory_job_specs())
            logger.info(
                "Memory scheduler jobs ensured: %d inserted, %d updated",
                memory_jobs_inserted,
                memory_jobs_updated,
            )
        except Exception as e:
            logger.error("Memory scheduler job initialization failed: %s", e)
            memory_job_executor = None

    # Connect job executor if skills or memory jobs are available
    job_exec = None
    if skill_executor or memory_job_executor:
        from scheduler.executor import JobExecutor

        def _execute_scheduled_job(name, inputs):
            if memory_job_executor and name in MEMORY_JOB_SKILLS:
                result = execute_memory_job(memory_job_executor, name, inputs)
                if not result.success:
                    raise RuntimeError("; ".join(result.errors) or f"Memory job {name} failed")
                return result.to_dict()
            if skill_executor:
                return skill_executor.run(name, inputs)
            raise RuntimeError(f"No executor available for scheduled job '{name}'")

        job_exec = JobExecutor(
            scheduler_service=service,
            skill_execute_fn=_execute_scheduled_job,
        )
        main_orchestrator.job_executor = job_exec
        job_exec.start_tick_loop()
        logger.info(
            "Job executor wired (skills=%s, memory_jobs=%s).",
            "yes" if skill_executor else "no",
            "yes" if memory_job_executor else "no",
        )

    # Init scheduler API
    try:
        from scheduler_api import init_scheduler_api
        init_scheduler_api(service=service, executor=job_exec)
        logger.info("Scheduler API initialized.")
    except Exception as e:
        logger.warning("Scheduler API initialization failed: %s", e)

    return {
        "service": service,
        "job_executor": job_exec,
        "memory_job_executor": memory_job_executor,
        "memory_jobs_inserted": memory_jobs_inserted,
        "memory_jobs_updated": memory_jobs_updated,
    }


def _shutdown_scheduler(objects):
    """Shut down Scheduler subsystem."""
    global scheduler_service
    if objects.get("job_executor"):
        objects["job_executor"].stop()
        logger.info("Job executor stopped.")
    main_orchestrator.scheduler_service = None
    main_orchestrator.job_executor = None
    scheduler_service = None
    logger.info("Scheduler shut down.")


def _init_health_monitor():
    """Initialize Health Monitor subsystem."""
    from health.monitor import HealthMonitor, HealthCheck
    from health.api import set_snapshot_provider

    def _local_llm_ready():
        local_model = getattr(main_orchestrator, "local_model", None)
        if local_model is not None:
            try:
                if local_model.is_healthy():
                    return True
            except Exception:
                pass
        role_lane = summarize_local_model_router_readiness(
            getattr(main_orchestrator, "local_model_roles", None)
        )
        return bool(role_lane.get("ready"))

    def _local_llm_health_details():
        from src.core.model_usage_policy import set_local_model_availability

        role_lane = summarize_local_model_router_readiness(
            getattr(main_orchestrator, "local_model_roles", None)
        )

        if getattr(main_orchestrator, "local_model", None) is None:
            details = {
                "local_llm_loaded": False,
                "local_llm_status": "unavailable",
                "local_llm_last_verified_at": None,
                "local_llm_last_checked_at": None,
                "local_llm_last_error": "Local model client not initialized",
                "local_llm_consecutive_failures": 0,
                "local_llm_last_smoke_elapsed_ms": None,
            }
            if role_lane.get("ready"):
                details.update({
                    "local_llm_loaded": True,
                    "local_llm_status": "ok",
                    "local_llm_last_verified_at": role_lane.get("last_verified_at"),
                    "local_llm_last_checked_at": role_lane.get("last_checked_at"),
                    "local_llm_last_error": None,
                    "local_llm_last_smoke_elapsed_ms": role_lane.get("last_smoke_elapsed_ms"),
                })
                set_local_model_availability(
                    True,
                    "Role-specific local model lanes ready",
                    loaded=True,
                    ready=True,
                    last_verified_at=details["local_llm_last_verified_at"],
                    last_checked_at=details["local_llm_last_checked_at"],
                    last_error=None,
                    consecutive_failures=0,
                    last_smoke_elapsed_ms=details["local_llm_last_smoke_elapsed_ms"],
                )
                return details
            set_local_model_availability(
                False,
                details["local_llm_last_error"],
                loaded=bool(role_lane.get("loaded", False)),
                ready=False,
                last_error=details["local_llm_last_error"],
                consecutive_failures=0,
                last_smoke_elapsed_ms=role_lane.get("last_smoke_elapsed_ms"),
            )
            return details
        try:
            data = main_orchestrator.local_model.health()
            details = {
                "local_llm_loaded": bool(data.get("loaded", data.get("ready"))),
                "local_llm_status": data.get("status", "ok"),
                "local_llm_last_verified_at": data.get("last_verified_at"),
                "local_llm_last_checked_at": data.get("last_checked_at"),
                "local_llm_last_error": data.get("last_error"),
                "local_llm_consecutive_failures": data.get("consecutive_failures", 0),
                "local_llm_last_smoke_elapsed_ms": data.get("last_smoke_elapsed_ms"),
            }
            if not data.get("ready") and role_lane.get("ready"):
                details.update({
                    "local_llm_loaded": True,
                    "local_llm_status": "ok",
                    "local_llm_last_verified_at": role_lane.get("last_verified_at"),
                    "local_llm_last_checked_at": role_lane.get("last_checked_at"),
                    "local_llm_last_error": None,
                    "local_llm_consecutive_failures": 0,
                    "local_llm_last_smoke_elapsed_ms": role_lane.get("last_smoke_elapsed_ms"),
                })
                set_local_model_availability(
                    True,
                    "Role-specific local model lanes ready",
                    loaded=True,
                    ready=True,
                    last_verified_at=details["local_llm_last_verified_at"],
                    last_checked_at=details["local_llm_last_checked_at"],
                    last_error=None,
                    consecutive_failures=0,
                    last_smoke_elapsed_ms=details["local_llm_last_smoke_elapsed_ms"],
                )
                return details
            set_local_model_availability(
                bool(data.get("ready")),
                data.get("last_error") or ("Local model ready" if data.get("ready") else "Local model not ready"),
                loaded=details["local_llm_loaded"],
                ready=bool(data.get("ready")),
                last_verified_at=details["local_llm_last_verified_at"],
                last_checked_at=details["local_llm_last_checked_at"],
                last_error=details["local_llm_last_error"],
                consecutive_failures=details["local_llm_consecutive_failures"],
                last_smoke_elapsed_ms=details["local_llm_last_smoke_elapsed_ms"],
            )
            return details
        except Exception as exc:
            details = {
                "local_llm_loaded": False,
                "local_llm_status": "unavailable",
                "local_llm_last_verified_at": None,
                "local_llm_last_checked_at": None,
                "local_llm_last_error": str(exc),
                "local_llm_consecutive_failures": 0,
                "local_llm_last_smoke_elapsed_ms": None,
            }
            if role_lane.get("ready"):
                details.update({
                    "local_llm_loaded": True,
                    "local_llm_status": "ok",
                    "local_llm_last_verified_at": role_lane.get("last_verified_at"),
                    "local_llm_last_checked_at": role_lane.get("last_checked_at"),
                    "local_llm_last_error": None,
                    "local_llm_last_smoke_elapsed_ms": role_lane.get("last_smoke_elapsed_ms"),
                })
                set_local_model_availability(
                    True,
                    "Role-specific local model lanes ready",
                    loaded=True,
                    ready=True,
                    last_verified_at=details["local_llm_last_verified_at"],
                    last_checked_at=details["local_llm_last_checked_at"],
                    last_error=None,
                    consecutive_failures=0,
                    last_smoke_elapsed_ms=details["local_llm_last_smoke_elapsed_ms"],
                )
                return details
            set_local_model_availability(
                False,
                str(exc),
                loaded=bool(role_lane.get("loaded", False)),
                ready=False,
                last_error=str(exc),
                consecutive_failures=0,
                last_smoke_elapsed_ms=role_lane.get("last_smoke_elapsed_ms"),
            )
            return details

    checks = [
        HealthCheck(
            name="llm_provider",
            check_fn=lambda: main_orchestrator.provider is not None,
            degraded_reason="LLM provider not initialized",
        ),
        HealthCheck(
            name="onboarding_ready",
                check_fn=lambda: onboarding_orch.determine_state() == "READY",
            degraded_reason="Onboarding not complete",
        ),
        HealthCheck(
            name="local_llm",
            check_fn=_local_llm_ready,
            degraded_reason="Local LLM not ready for inference",
            snapshot_details_fn=_local_llm_health_details,
        ),
    ]
    if main_orchestrator.scheduler_service:
        checks.append(HealthCheck(
            name="scheduler",
            check_fn=lambda: bool(
                main_orchestrator.job_executor
                and main_orchestrator.job_executor.is_running
            ),
            degraded_reason="Scheduler not running",
            snapshot_details_fn=lambda: {
                "last_scheduler_tick_at": (
                    main_orchestrator.scheduler_service.last_scheduler_tick_at
                    if main_orchestrator.scheduler_service
                    else None
                )
            },
        ))

    monitor = HealthMonitor(checks=checks, interval_s=30.0)
    monitor.start_monitor()
    # Readiness must be cheap and predictable. The monitor refreshes health in
    # the background; the API serves the cached snapshot so model probes cannot
    # make operator UI or container probes look hung.
    set_snapshot_provider(lambda: monitor.latest_snapshot)
    logger.info("Health monitor started.")
    return {"monitor": monitor}


def _shutdown_health_monitor(objects):
    """Shut down Health Monitor subsystem."""
    if objects.get("monitor"):
        objects["monitor"].stop_monitor()
    logger.info("Health monitor stopped.")


# Tool Fabric provider subsystems
# These init/shutdown functions let the SubsystemManager hot-toggle
# individual providers inside the already-running ToolFabric.


def _init_host_bridge():
    """Hot-start the Host Bridge provider inside Tool Fabric."""
    from src.tools.fabric import get_tool_fabric
    from src.tools.providers.host_bridge import HostBridgeProvider

    fabric = get_tool_fabric()
    provider = HostBridgeProvider(workspace=fabric.config.default_workspace)
    fabric.register_provider(provider)
    fabric.update_router_preferences()
    logger.warning(
        "HOST BRIDGE hot-started; commands will be sent to host agent at %s",
        provider.config.agent_url,
    )
    return {"provider": provider}


def _shutdown_host_bridge(objects):
    """Hot-stop the Host Bridge provider."""
    from src.tools.fabric import get_tool_fabric
    fabric = get_tool_fabric()
    fabric.unregister_provider("host_bridge")
    fabric.update_router_preferences()
    logger.info("Host Bridge provider unregistered.")


def _init_uab():
    """Hot-start the UAB provider inside Tool Fabric."""
    from src.tools.fabric import get_tool_fabric
    from src.tools.providers.uab_bridge import UABProvider

    fabric = get_tool_fabric()
    provider = UABProvider()
    fabric.register_provider(provider)
    fabric.update_router_preferences()
    logger.warning(
        "UAB BRIDGE hot-started; desktop app control via daemon at %s",
        provider.config.daemon_url,
    )
    return {"provider": provider}


def _shutdown_uab(objects):
    """Hot-stop the UAB provider."""
    from src.tools.fabric import get_tool_fabric
    fabric = get_tool_fabric()
    fabric.unregister_provider("uab_bridge")
    fabric.update_router_preferences()
    logger.info("UAB Bridge provider unregistered.")

def _init_hive():
    """Initialize the HIVE Agent Mesh subsystem."""
    from gateway_hive_support import init_hive

    return init_hive(
        main_orchestrator=main_orchestrator,
        sentry=sentry,
        subsystem_manager=subsystem_manager,
        logger=logger,
    )


def _get_uab_provider():
    """Get the UABProvider instance from ToolFabric if available."""
    from gateway_hive_support import get_uab_provider

    return get_uab_provider(logger)


def _shutdown_hive(objects):
    """Shut down the HIVE Agent Mesh subsystem."""
    from gateway_hive_support import shutdown_hive

    return shutdown_hive(objects, logger)

def _resolve_peer_key(peer_registry, topology, instance_id: str):
    """Resolve a peer's public key from registry or topology.

    Checks PeerRegistryStore first (persistent), falls back to
    TopologyRegistry (runtime).
    """
    # Try persistent peer registry first
    try:
        key = peer_registry.get_peer_public_key(instance_id)
        if key:
            return key
    except Exception as exc:
        logger.warning(
            "Peer registry public key lookup failed for %s; falling back to topology: %s",
            instance_id,
            exc,
        )
    # Fall back to topology registry
    peer = topology.get_peer(instance_id)
    if peer and peer.public_key_hex:
        try:
            return bytes.fromhex(peer.public_key_hex)
        except ValueError:
            return None
    return None


def _init_federation():
    """Initialize the Federation subsystem (control plane + data plane)."""
    from src.federation.config import load_federation_config
    from src.federation.identity import load_or_generate_identity
    from src.federation.heartbeat import HeartbeatEmitter
    from src.federation.topology import TopologyRegistry
    from src.federation.divergence import DivergenceDetector
    from src.federation.divergence import ReconciliationOutcome, reconcile_divergence
    from src.federation.receipt_manager import FederationReceiptManager
    from src.federation.api import init_federation_api, init_federation_transport
    from src.federation.auth import FederationAuth
    from src.federation.soul_compat import hash_soul
    from src.federation.transport import FederationTransport
    from src.federation.peer_registry import PeerRegistryStore
    from src.federation.peer_protocol import PeerRegistrationProtocol
    from src.federation.command_relay import CommandRelay
    from src.federation.soul_transport import SoulTransport
    from src.federation.soul_propagation import SoulPropagationEngine
    from src.federation.handoff_protocol import HandoffProtocol
    from src.federation.cost_reporter import CostReporter
    from src.federation.cost_aggregation import FederatedCostAggregator
    from src.federation.budget import FederationBudgetTracker
    from src.federation.heartbeat_mesh import HeartbeatMesh
    from src.federation.audit import FederationAuditEngine
    from src.federation.runtime_budget_source import RuntimeBudgetResolver
    from src.federation.runtime_budget_control import (
        handle_federation_cost_threshold_change,
    )

    data_dir = os.environ.get("LANCELOT_DATA_DIR", "lancelot_data")
    config = load_federation_config()
    identity = load_or_generate_identity(data_dir=data_dir)

    fed_data_dir = os.path.join(data_dir, "federation")
    graph_data_dir = os.path.join(data_dir, "federation")
    os.makedirs(fed_data_dir, exist_ok=True)
    topology = TopologyRegistry(
        self_instance_id=identity.instance_id,
        staleness_warning_s=config.staleness_warning_s,
        staleness_critical_s=config.staleness_critical_s,
        staleness_lost_s=config.staleness_lost_s,
        max_peers=config.max_peers,
        persistence_path=config.topology_path,
    )

    divergence = DivergenceDetector(
        instance_id=identity.instance_id,
        staleness_lost_s=config.staleness_lost_s,
    )

    receipt_mgr = FederationReceiptManager(
        instance_id=identity.instance_id,
        data_dir=data_dir,
    )

    # Audit engine: cross-instance audit trail
    audit_engine = FederationAuditEngine(
        max_entries=10000,
        persistence_path=os.path.join(fed_data_dir, "audit_log.json"),
    )

    # --- Control Plane (existing) ---
    emitter = HeartbeatEmitter(
        instance_id=identity.instance_id,
        interval_s=config.heartbeat_interval_s,
    )

    budget_tracker = FederationBudgetTracker()
    cost_aggregator = None
    runtime_budget_resolver = RuntimeBudgetResolver(
        topology_data_dir=graph_data_dir,
        identity=identity,
        fallback_daily_ceiling_usd=float(config.daily_budget_ceiling_usd),
    )

    def _record_cost_threshold_change(old_threshold, new_threshold) -> None:
        try:
            handle_federation_cost_threshold_change(
                old_threshold,
                new_threshold,
                cost_aggregator=cost_aggregator,
                receipt_mgr=receipt_mgr,
                audit_engine=audit_engine,
                identity=identity,
                soul_hash_provider=lambda: hash_soul(
                    getattr(app.state, "active_soul", getattr(main_orchestrator, "soul", None))
                ) if getattr(app.state, "active_soul", getattr(main_orchestrator, "soul", None)) is not None else "",
                pause_runtime_fn=_federation_local_pause_handler,
            )
        except Exception as exc:
            logger.warning("Failed to record federation cost threshold change: %s", exc)

    def _federation_usage_provider():
        tracker = getattr(main_orchestrator, "usage_tracker", None)
        usage_summary = tracker.summary() if tracker else {}
        total_cost = float(usage_summary.get("total_cost_est", 0.0) or 0.0)
        total_tokens = int(usage_summary.get("total_tokens_est", 0) or 0)

        hive_entry = subsystem_manager.get("hive")
        active_spawns = 0
        if hive_entry and hive_entry.running:
            registry = hive_entry.objects.get("registry")
            if registry is not None:
                try:
                    active_spawns = int(registry.active_count())
                except Exception:
                    active_spawns = 0

        # Federation budget tracking is cost-governance oriented today, not
        # a second token-budget system. We surface live cost/usage from the
        # main orchestrator and active HIVE spawn pressure from the registry.
        daily_ceiling = runtime_budget_resolver.resolve_daily_ceiling_usd()
        projected_today = total_cost

        return {
            "actual_today_usd": total_cost,
            "projected_today_usd": projected_today,
            "daily_ceiling_usd": daily_ceiling,
            "active_spawns": active_spawns,
            "spawn_cost_rate_usd_hr": 0.0,
            "total_tokens_today": total_tokens,
            "budget_tracker": budget_tracker.get_snapshot(),
        }

    def _runtime_federation_soul():
        return getattr(app.state, "active_soul", getattr(main_orchestrator, "soul", None))

    def _runtime_federation_soul_hash() -> str:
        current_soul = _runtime_federation_soul()
        return hash_soul(current_soul) if current_soul is not None else ""

    def _record_divergence(peer_instance_id, snapshot) -> None:
        receipt_mgr.record_divergence(
            peer_instance_id=peer_instance_id,
            staleness_seconds=divergence.get_divergence_duration_s(),
            soul_version_hash=snapshot.soul_hash_at_divergence,
        )
        audit_engine.record(
            event_type="divergence_detected",
            instance_id=identity.instance_id,
            soul_version_hash=snapshot.soul_hash_at_divergence,
            details={
                "peer_instance_id": peer_instance_id,
                "snapshot": snapshot.to_dict(),
            },
        )

    def _reconcile_after_reconnect(peer_instance_id, detector) -> None:
        snapshot = detector.divergence_snapshot
        if snapshot is None:
            return
        outcome, conflicts = reconcile_divergence(
            divergence_snapshot=snapshot,
            reconnection_soul_hash=_runtime_federation_soul_hash(),
            reconnection_budget_pct=float(
                cost_reporter.get_aggregate_status().get("utilization_pct", 0.0)
            ) if cost_reporter else 0.0,
        )
        detector.mark_reconciled(outcome, conflicts)
        receipt_mgr.record_reconnection(
            peer_instance_id=peer_instance_id,
            divergence_duration_s=detector.get_divergence_duration_s(),
            reconciliation_result=outcome.value,
        )
        audit_engine.record(
            event_type="reconciliation_completed",
            instance_id=identity.instance_id,
            soul_version_hash=_runtime_federation_soul_hash(),
            details={
                "peer_instance_id": peer_instance_id,
                "outcome": outcome.value,
                "conflicts": [
                    {
                        "conflict_type": c.conflict_type,
                        "description": c.description,
                        "resolution": c.resolution,
                        "affected_component": c.affected_component,
                    }
                    for c in conflicts
                ],
            },
        )
        if outcome == ReconciliationOutcome.COMPATIBLE:
            detector.reset_to_connected()

    emitter.set_providers(
        soul_hash=lambda: (
            _runtime_federation_soul_hash()
        ),
        mode=lambda: topology.deployment_mode.value,
        budget=lambda: (
            float(_federation_usage_provider().get("actual_today_usd", 0.0))
            / max(float(_federation_usage_provider().get("daily_ceiling_usd", 10.0)), 0.0001)
        ) * 100.0,
        peer_count=lambda: topology.peer_count(),
    )

    # --- Data Plane (transport layer) ---

    # 1. Persistent peer registry (SQLite)
    peer_db_path = config.peer_db_path or os.path.join(fed_data_dir, "peers.sqlite")
    peer_registry = PeerRegistryStore(db_path=peer_db_path)

    # 2. Auth (Ed25519 request signing + verification)
    auth = FederationAuth(
        identity=identity,
        peer_key_resolver=lambda instance_id: _resolve_peer_key(peer_registry, topology, instance_id),
        timestamp_window_s=config.auth_timestamp_window_s,
        nonce_cache_size=config.nonce_cache_size,
        nonce_store=peer_registry,
    )

    # 3. Resilient HTTP transport (circuit breakers, retries, connection pooling)
    transport = FederationTransport(
        auth=auth,
        max_retries=config.retry_max_attempts,
        base_backoff_s=config.retry_base_backoff_s,
        circuit_breaker_threshold=config.circuit_breaker_threshold,
        circuit_breaker_recovery_s=config.circuit_breaker_recovery_s,
        connect_timeout_s=config.connect_timeout_s,
        read_timeout_s=config.read_timeout_s,
    )

    def _handle_federation_peer_removed(instance_id: str) -> None:
        if heartbeat_mesh:
            heartbeat_mesh.on_peer_removed(instance_id)
        if cost_aggregator:
            cost_aggregator.remove_instance(instance_id)

    # 4. Peer registration handshake protocol
    peer_protocol = PeerRegistrationProtocol(
        identity=identity,
        topology=topology,
        transport=transport,
        receipt_mgr=receipt_mgr,
        audit=audit_engine,
        self_address=config.self_address,
        on_peer_registered=lambda instance_id, address: heartbeat_mesh.on_peer_added(instance_id, address) if heartbeat_mesh else None,
        on_peer_removed=_handle_federation_peer_removed,
        persistence_path=os.path.join(fed_data_dir, "pending_registrations.json"),
    )

    # 5. Command relay (kill switch + pause propagation)
    def _federation_local_kill_handler(reason: str) -> int:
        hive_entry = subsystem_manager.get("hive")
        lifecycle = hive_entry.objects.get("lifecycle") if hive_entry and hive_entry.running else None
        if lifecycle is None:
            logger.warning("Federation kill requested but HIVE lifecycle is not available")
            return 0
        collapsed = lifecycle.kill_all(
            reason,
            operator_id="federation-peer",
            session_id="federation-peer",
        )
        return len(collapsed)

    def _cancel_federation_approval_queues(reason: str) -> dict:
        scheduler_pending = 0
        scheduler_granted = 0
        sentry_pending_denied = 0

        job_executor = getattr(main_orchestrator, "job_executor", None)
        if job_executor is not None:
            try:
                cleared = job_executor.clear_approval_state(
                    reason=reason,
                    operator_id="federation-peer",
                    session_id="federation-peer",
                    actor="Federation Peer",
                )
                scheduler_pending = int(cleared.get("pending_cleared", 0) or 0)
                scheduler_granted = int(cleared.get("granted_cleared", 0) or 0)
            except Exception as exc:
                logger.warning(
                    "Failed to clear scheduler approvals during federation full stop: %s",
                    exc,
        )

        try:
            sentry.cleanup_expired()
            for req_id, req in list(sentry.pending_requests.items()):
                if req.get("status") == "PENDING" and sentry.deny_request(req_id):
                    sentry_pending_denied += 1
        except Exception as exc:
            logger.warning(
                "Failed to clear sentry approvals during federation full stop: %s",
                exc,
            )

        return {
            "scheduler_pending_cleared": scheduler_pending,
            "scheduler_granted_cleared": scheduler_granted,
            "sentry_pending_denied": sentry_pending_denied,
        }

    def _federation_local_pause_handler(
        reason: str,
        *,
        full_stop: bool = False,
        source: str = "federation",
    ) -> dict:
        from src.core.runtime_pause import pause_runtime

        pause_runtime(
            reason,
            operator_id="federation-peer",
            operator_name="Federation Peer",
            session_id="federation-peer",
            source=source,
        )

        approval_queue_result = {
            "scheduler_pending_cleared": 0,
            "scheduler_granted_cleared": 0,
            "sentry_pending_denied": 0,
        }
        if full_stop:
            approval_queue_result = _cancel_federation_approval_queues(reason)

        hive_entry = subsystem_manager.get("hive")
        lifecycle = hive_entry.objects.get("lifecycle") if hive_entry and hive_entry.running else None
        registry = hive_entry.objects.get("registry") if hive_entry and hive_entry.running else None
        if lifecycle is None or registry is None:
            return {
                "paused_agents": 0,
                "already_paused_agents": 0,
                "execution_state": "paused",
                "full_stop": full_stop,
                **approval_queue_result,
            }

        from src.hive.types import AgentState

        paused_agents = 0
        failed_agents = []

        for record in registry.list_by_state(AgentState.EXECUTING):
            try:
                lifecycle.pause(
                    record.agent_id,
                    reason,
                    operator_id="federation-peer",
                    session_id="federation-peer",
                )
                paused_agents += 1
            except KeyError:
                # Agent may have completed between roster read and pause request.
                continue
            except Exception:
                failed_agents.append(record.agent_id)

        if failed_agents:
            raise RuntimeError(
                f"Failed to pause active agents: {', '.join(sorted(failed_agents))}"
            )

        already_paused_agents = len(registry.list_by_state(AgentState.PAUSED))
        execution_state = "paused" if (paused_agents or already_paused_agents) else "idle"
        return {
            "paused_agents": paused_agents,
            "already_paused_agents": already_paused_agents,
            "execution_state": execution_state,
            "full_stop": full_stop,
            **approval_queue_result,
        }

    def _federation_local_resume_handler(reason: str) -> dict:
        from src.core.runtime_pause import resume_runtime

        resume_runtime(
            operator_id="federation-peer",
            operator_name="Federation Peer",
            session_id="federation-peer",
            source="federation",
        )

        hive_entry = subsystem_manager.get("hive")
        lifecycle = hive_entry.objects.get("lifecycle") if hive_entry and hive_entry.running else None
        registry = hive_entry.objects.get("registry") if hive_entry and hive_entry.running else None
        if lifecycle is None or registry is None:
            return {
                "resumed_agents": 0,
                "execution_state": "running",
            }

        from src.hive.types import AgentState

        resumed_agents = 0
        failed_agents = []

        for record in registry.list_by_state(AgentState.PAUSED):
            try:
                lifecycle.resume(
                    record.agent_id,
                    operator_id="federation-peer",
                    session_id="federation-peer",
                )
                resumed_agents += 1
            except KeyError:
                continue
            except Exception:
                failed_agents.append(record.agent_id)

        if failed_agents:
            raise RuntimeError(
                f"Failed to resume paused agents: {', '.join(sorted(failed_agents))}"
            )

        return {
            "resumed_agents": resumed_agents,
            "execution_state": "running",
        }

    from src.federation.kill_switch import FederatedKillSwitch

    kill_switch = FederatedKillSwitch(
        self_instance_id=identity.instance_id,
        peer_ids=[p.instance_id for p in topology.list_peers()],
        local_kill_handler=_federation_local_kill_handler,
        persistence_path=os.path.join(fed_data_dir, "kill_commands.json"),
    )

    command_relay = CommandRelay(
        identity=identity,
        transport=transport,
        topology=topology,
        kill_switch=kill_switch,
        local_pause_handler=_federation_local_pause_handler,
        local_resume_handler=_federation_local_resume_handler,
        receipt_mgr=receipt_mgr,
        audit=audit_engine,
    )

    propagation_engine = SoulPropagationEngine(
        self_instance_id=identity.instance_id,
        peer_ids=[p.instance_id for p in topology.list_peers()],
        persistence_path=os.path.join(fed_data_dir, "soul_propagation.json"),
    )

    # 6. Soul transport (push/pull with tier-aware propagation)
    soul_transport = SoulTransport(
        identity=identity,
        transport=transport,
        topology=topology,
        propagation_engine=propagation_engine,
        receipt_mgr=receipt_mgr,
        audit=audit_engine,
        handoff_timeout_s=config.handoff_timeout_s,
        current_soul_provider=lambda: getattr(app.state, "active_soul", getattr(main_orchestrator, "soul", None)),
        runtime_reload_callback=getattr(app.state, "apply_runtime_soul", None),
        soul_dir=os.environ.get("SOUL_DIR", None),
        heartbeat_emitter=emitter,
        local_pause_handler=_federation_local_pause_handler,
        local_resume_handler=_federation_local_resume_handler,
    )

    # 7. Handoff protocol (task delegation between peers)
    handoff_protocol = HandoffProtocol(
        identity=identity,
        transport=transport,
        topology=topology,
        receipt_mgr=receipt_mgr,
        audit=audit_engine,
        handoff_timeout_s=config.handoff_timeout_s,
        current_soul_provider=lambda: getattr(main_orchestrator, "soul", None),
        persistence_path=os.path.join(fed_data_dir, "active_handoffs.json"),
    )

    # 8. Cost reporter (periodic budget reporting to peers)
    cost_aggregator = FederatedCostAggregator(
        on_threshold_change=_record_cost_threshold_change,
        persistence_path=os.path.join(fed_data_dir, "cost_aggregate.json"),
        stale_after_s=max(config.cost_report_interval_s * 2.0, config.staleness_lost_s),
    )
    cost_reporter = CostReporter(
        identity=identity,
        transport=transport,
        topology=topology,
        cost_aggregator=cost_aggregator,
        usage_provider=_federation_usage_provider,
        interval_s=config.cost_report_interval_s,
    )

    # 9. Heartbeat mesh (SSE subscription to peer health streams)
    heartbeat_mesh = HeartbeatMesh(
        topology=topology,
        divergence_detector=divergence,
        auth=auth,
        connect_timeout_s=config.connect_timeout_s,
        read_timeout_s=120.0,
        current_soul_hash_provider=lambda: (
            _runtime_federation_soul_hash()
        ),
        active_task_count_provider=lambda: int(bool(getattr(main_orchestrator, "agent_busy", False))),
        hive_spawn_count_provider=lambda: (
            hive_entry.objects["registry"].active_count()
            if (
                (hive_entry := subsystem_manager.get("hive"))
                and hive_entry.running
                and hive_entry.objects.get("registry") is not None
            ) else 0
        ),
        hive_spawn_states_provider=lambda: (
            {
                record.agent_id: record.state.value
                for record in hive_entry.objects["registry"].list_active()
            }
            if (
                (hive_entry := subsystem_manager.get("hive"))
                and hive_entry.running
                and hive_entry.objects.get("registry") is not None
            ) else {}
        ),
        pending_handoffs_provider=lambda: (
            [
                {
                    "handoff_id": handoff["handoff_id"],
                    "state": handoff["status"],
                    "target_instance_id": handoff.get("target_instance_id", ""),
                }
                for handoff in handoff_protocol.list_active_handoffs()
            ] if handoff_protocol else []
        ),
        budget_utilization_provider=lambda: float(
            cost_reporter.get_aggregate_status().get("utilization_pct", 0.0)
        ) if cost_reporter else 0.0,
        on_diverged=_record_divergence,
        on_reconnecting=_reconcile_after_reconnect,
    )

    def _federation_spawn_gate(task_spec) -> None:
        model_tier = (
            str(task_spec.context.get("model_tier", "")).strip()
            if getattr(task_spec, "context", None) else ""
        ) or "T2"
        estimated_tokens = 0
        if getattr(task_spec, "context", None):
            try:
                estimated_tokens = int(task_spec.context.get("estimated_tokens", 0) or 0)
            except Exception:
                estimated_tokens = 0

        if divergence.state.value == "diverged" and model_tier.upper() == "T3":
            divergence_duration_s = divergence.get_divergence_duration_s()
            receipt_mgr.record_budget_threshold(
                threshold_level="diverged",
                utilization_pct=float(cost_aggregator.get_aggregate().utilization_pct),
                action_taken="block_t3_spawn",
            )
            audit_engine.record(
                event_type="divergence_detected",
                instance_id=identity.instance_id,
                soul_version_hash=_runtime_federation_soul_hash(),
                risk_tier="T3",
                details={
                    "action": "spawn_blocked",
                    "reason": "federation_diverged",
                    "model_tier": model_tier,
                    "estimated_tokens": estimated_tokens,
                    "divergence_duration_s": divergence_duration_s,
                },
            )
            raise RuntimeError(
                "Federation divergence active - T3 spawns are blocked until reconciliation completes"
            )

        if (
            heartbeat_mesh
            and getattr(heartbeat_mesh, "divergence_evaluation_failed", False)
            and model_tier.upper() == "T3"
        ):
            raise RuntimeError(
                "Federation divergence evaluation unavailable - T3 spawns are blocked until mesh health recovers"
            )

        allowed, reason = cost_aggregator.check_spawn_allowed(identity.instance_id)
        if not allowed:
            raise RuntimeError(f"Federation spawn budget blocked: {reason}")

        decision, reason = budget_tracker.check_spawn(
            model_tier=model_tier,
            estimated_tokens=estimated_tokens,
        )
        if decision.value == "blocked":
            raise RuntimeError(f"Federation spawn budget blocked: {reason}")
        if decision.value == "restricted":
            receipt_mgr.record_budget_threshold(
                threshold_level=budget_tracker.threshold_level.value,
                utilization_pct=budget_tracker.utilization_pct,
                action_taken="spawn_allowed_with_notice",
            )

    def _federation_record_spawn(record) -> None:
        model_tier = (
            str(record.task_spec.context.get("model_tier", "")).strip()
            if getattr(record.task_spec, "context", None) else ""
        ) or "T2"
        estimated_tokens = 0
        if getattr(record.task_spec, "context", None):
            try:
                estimated_tokens = int(record.task_spec.context.get("estimated_tokens", 0) or 0)
            except Exception:
                estimated_tokens = 0
        budget_tracker.record_spawn(
            agent_id=record.agent_id,
            instance_id=identity.instance_id,
            model_tier=model_tier,
            estimated_tokens=estimated_tokens,
        )
        receipt_mgr.record_spawn_receipt(
            agent_id=record.agent_id,
            model_tier=model_tier,
            estimated_cost=float(estimated_tokens),
            federation_quest_id=record.quest_id,
        )

    def _federation_record_collapse(record, result) -> None:
        actual_tokens = 0
        outputs = getattr(result, "outputs", {}) or {}
        try:
            actual_tokens = int(outputs.get("actual_tokens", 0) or 0)
        except Exception:
            actual_tokens = 0
        budget_tracker.record_collapse(record.agent_id, actual_tokens=actual_tokens)

    # Wire control plane API endpoints
    init_federation_api(
        identity=identity,
        heartbeat_emitter=emitter,
        config=config,
        topology_registry=topology,
        divergence_detector=divergence,
    )

    # Wire data plane transport handlers into API
    init_federation_transport(
        peer_protocol=peer_protocol,
        command_relay=command_relay,
        soul_transport=soul_transport,
        handoff_protocol=handoff_protocol,
        cost_reporter=cost_reporter,
        auth=auth,
        audit_engine=audit_engine,
        transport=transport,
        heartbeat_mesh=heartbeat_mesh,
    )

    hive_entry = subsystem_manager.get("hive")
    if hive_entry and hive_entry.running and hive_entry.objects.get("lifecycle") is not None:
        try:
            hive_entry.objects["lifecycle"].update_spawn_controls(
                spawn_gate=_federation_spawn_gate,
                spawn_record_hook=_federation_record_spawn,
                collapse_record_hook=_federation_record_collapse,
            )
        except Exception as exc:
            logger.warning("Failed to wire federation budget governance into HIVE lifecycle: %s", exc)

    # Initialize Graph Builder API
    try:
        from src.federation.graph_api import init_graph_api
        init_graph_api(graph_data_dir)
    except Exception as e:
        logger.warning("Graph Builder API init failed: %s", e)

    # Start synchronous background heartbeat emitter
    emitter.start()

    # Schedule async transport startup (will run when event loop is available)
    async def _start_transport_layer():
        try:
            await transport.start()
            logger.info("Federation transport started (httpx connection pool ready)")
        except Exception as e:
            logger.warning("Federation transport start failed: %s", e)
        try:
            await heartbeat_mesh.start()
            logger.info("Federation heartbeat mesh started")
        except Exception as e:
            logger.warning("Federation heartbeat mesh start failed: %s", e)
        try:
            await cost_reporter.start()
            logger.info("Federation cost reporter started")
        except Exception as e:
            logger.warning("Federation cost reporter start failed: %s", e)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_start_transport_layer())
    except RuntimeError:
        # No event loop yet; startup_event will start it.
        pass

    logger.info(
        "Federation initialized: instance=%s, fingerprint=%s, heartbeat=%.1fs, mode=%s, transport=ACTIVE",
        identity.instance_id, identity.fingerprint, config.heartbeat_interval_s,
        topology.deployment_mode.value,
    )
    return {
        "config": config,
        "identity": identity,
        "emitter": emitter,
        "topology": topology,
        "divergence": divergence,
        "receipt_mgr": receipt_mgr,
        "peer_registry": peer_registry,
        "auth": auth,
        "transport": transport,
        "peer_protocol": peer_protocol,
        "command_relay": command_relay,
        "propagation_engine": propagation_engine,
        "soul_transport": soul_transport,
        "handoff_protocol": handoff_protocol,
        "budget_tracker": budget_tracker,
        "cost_aggregator": cost_aggregator,
        "cost_reporter": cost_reporter,
        "heartbeat_mesh": heartbeat_mesh,
        "audit_engine": audit_engine,
        "spawn_gate": _federation_spawn_gate,
        "spawn_record_hook": _federation_record_spawn,
        "collapse_record_hook": _federation_record_collapse,
    }


def _shutdown_federation(objects):
    """Shut down the Federation subsystem (control plane + data plane).

    Note: async transport objects (transport, heartbeat_mesh, cost_reporter)
    are stopped in shutdown_event() before this runs, since this function
    is called synchronously by subsystem_manager.stop_all().
    """
    from src.federation.api import shutdown_federation_api

    if objects.get("emitter"):
        try:
            objects["emitter"].stop()
        except Exception as exc:
            logger.warning("Federation emitter shutdown failed: %s", exc)

    shutdown_federation_api()
    logger.info("Federation shut down.")
def _bootstrap_model_discovery():
    """Create ModelDiscovery + wire into Provider API when provider becomes available.

    Called at startup and again after OAuth hot-initializes the provider.
    Safe to call multiple times; skips if provider is still None.
    """
    if main_orchestrator.provider is None:
        return False

    try:
        from model_discovery import ModelDiscovery
        from providers.api import (
            ensure_persisted_active_provider,
            init_provider_api,
            load_persisted_config,
        )

        _persisted_config = load_persisted_config()
        _persisted_lane_overrides = _persisted_config.get("lane_overrides", {})

        _fallback_lanes = {}
        try:
            from provider_profile import ProfileRegistry
            _registry = ProfileRegistry()
            _prov_name = main_orchestrator.provider.provider_name
            if _registry.has_provider(_prov_name):
                _profile = _registry.get_profile(_prov_name)
                _fallback_lanes["fast"] = _profile.fast.model
                _fallback_lanes["deep"] = _profile.deep.model
                if _profile.cache:
                    _fallback_lanes["cache"] = _profile.cache.model
        except Exception as exc:
            logger.warning("Model discovery profile lookup failed; using persisted/env overrides only: %s", exc)

        _lane_overrides = dict(_persisted_lane_overrides)

        try:
            discovery = ModelDiscovery(
                provider=main_orchestrator.provider,
                profiles_path="config/model_profiles.yaml",
                lane_overrides=_lane_overrides,
                fallback_lanes=_fallback_lanes,
            )
        except TypeError:
            discovery = ModelDiscovery(
                provider=main_orchestrator.provider,
                profiles_path="config/model_profiles.yaml",
                lane_overrides=_lane_overrides,
            )
        discovery.refresh()

        for _lane, _model_id in discovery.lane_assignments.items():
            try:
                main_orchestrator.set_lane_model(_lane, _model_id)
            except Exception as _e:
                logger.warning("Failed to apply lane assignment %s=%s: %s", _lane, _model_id, _e)

        for _lane, _model_id in _persisted_lane_overrides.items():
            if discovery.lane_assignments.get(_lane) == _model_id:
                continue
            try:
                main_orchestrator.set_lane_model(_lane, _model_id)
            except Exception as _e:
                logger.warning("Failed to apply lane override %s=%s: %s", _lane, _model_id, _e)

        init_provider_api(discovery, orchestrator=main_orchestrator)
        bootstrap_model_router()
        if ensure_persisted_active_provider(main_orchestrator.provider.provider_name):
            logger.info(
                "Persisted active provider to durable config: %s",
                main_orchestrator.provider.provider_name,
            )
        logger.info(
            "Model discovery: %d models found, lanes: %s",
            len(discovery.discovered_models),
            discovery.lane_assignments,
        )
        return True
    except Exception as e:
        logger.warning("Model discovery bootstrap failed: %s", e)
        return False


def _restore_persisted_provider(persisted_provider: str, orchestrator=None) -> bool:
    """Restore a persisted provider selection into the orchestrator when possible."""
    if not persisted_provider:
        return False

    orchestrator = orchestrator or main_orchestrator
    if orchestrator is None:
        return False

    if orchestrator.provider is None:
        try:
            result_msg = orchestrator.switch_provider(persisted_provider)
            logger.info(
                "Restored persisted provider from empty startup state: %s (%s)",
                persisted_provider,
                result_msg,
            )
            return True
        except Exception as exc:
            logger.warning(
                "Failed to restore persisted provider '%s' from empty startup state: %s",
                persisted_provider,
                exc,
            )
            return False

    current_provider = orchestrator.provider.provider_name
    if persisted_provider == current_provider:
        return False

    try:
        result_msg = orchestrator.switch_provider(persisted_provider)
        logger.info("Restored persisted provider: %s (%s)", persisted_provider, result_msg)
        return True
    except Exception as exc:
        logger.warning(
            "Failed to restore persisted provider '%s': %s; keeping %s",
            persisted_provider,
            exc,
            current_provider,
        )
        return False


def _bootstrap_model_router() -> bool:
    """Create the live ModelRouter and wire it into the orchestrator + control plane."""
    if main_orchestrator.provider is None:
        return False

    try:
        from model_router import ModelRouter
        from provider_profile import ProfileRegistry
        from src.core.control_plane import set_model_router, set_usage_tracker

        router = ModelRouter(
            registry=ProfileRegistry(),
            local_client=getattr(main_orchestrator, "local_model", None),
            local_roles=getattr(main_orchestrator, "local_model_roles", None),
            provider_client=main_orchestrator.provider,
        )

        existing_tracker = getattr(main_orchestrator, "usage_tracker", None)
        persistence = getattr(existing_tracker, "_persistence", None)
        if persistence is not None:
            router.usage.set_persistence(persistence)

        main_orchestrator.model_router = router
        main_orchestrator.usage_tracker = router.usage
        set_model_router(router)
        set_usage_tracker(router.usage)
        logger.info(
            "Model router wired live (local_model=%s, provider=%s).",
            "ready" if getattr(main_orchestrator, "local_model", None) else "none",
            main_orchestrator.provider.provider_name,
        )
        return True
    except Exception as exc:
        logger.warning("Model router bootstrap failed: %s", exc)
        return False


init_memory, shutdown_memory, init_soul, shutdown_soul = _init_memory, _shutdown_memory, _init_soul, _shutdown_soul
init_skills, shutdown_skills, init_scheduler, shutdown_scheduler = _init_skills, _shutdown_skills, _init_scheduler, _shutdown_scheduler
init_health_monitor, shutdown_health_monitor = _init_health_monitor, _shutdown_health_monitor
init_host_bridge, shutdown_host_bridge, init_uab, shutdown_uab = _init_host_bridge, _shutdown_host_bridge, _init_uab, _shutdown_uab
init_hive, shutdown_hive, resolve_peer_key = _init_hive, _shutdown_hive, _resolve_peer_key
init_federation, shutdown_federation = _init_federation, _shutdown_federation
bootstrap_model_discovery = _bootstrap_model_discovery
restore_persisted_provider = _restore_persisted_provider
bootstrap_model_router = _bootstrap_model_router
