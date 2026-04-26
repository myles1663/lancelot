from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("lancelot.gateway.boot")

def bind_gateway_globals(**kwargs):
    globals().update(kwargs)

@dataclass
class BootEnvironment:
    provider: str = ""
    credential_var: str = ""
    api_token_configured: bool = False
    startup_time: float = 0.0

@dataclass
class BootCore:
    started_steps: list[str] = field(default_factory=list)
    optional_failures: list[str] = field(default_factory=list)

@dataclass
class BootResult:
    core: BootCore
    env: BootEnvironment


@dataclass
class BootConfig:
    api_token: str | None = None
    app_version: str = ""
    boot_vault: object | None = None
    verify_token: object | None = None
    secret_cache: object | None = None


def _publish_local_model_runtime_status(main_orchestrator) -> None:
    """Publish local model and role readiness after policy initialization."""
    from src.core.model_usage_policy import (
        set_local_model_availability,
        set_local_model_roles_status,
    )

    local_model = getattr(main_orchestrator, "local_model", None)
    if local_model is not None:
        try:
            local_health = local_model.health()
            local_ready = bool(local_health.get("ready"))
            local_loaded = bool(local_health.get("loaded", local_ready))
            local_reason = local_health.get("last_error") or (
                "Local model ready" if local_ready else "Local model not ready"
            )
            set_local_model_availability(
                local_ready,
                local_reason,
                loaded=local_loaded,
                ready=local_ready,
                last_verified_at=local_health.get("last_verified_at"),
                last_checked_at=local_health.get("last_checked_at"),
                last_error=local_health.get("last_error"),
                consecutive_failures=local_health.get("consecutive_failures"),
                last_smoke_elapsed_ms=local_health.get("last_smoke_elapsed_ms"),
            )
            if local_ready:
                logger.info("Local model connected and ready")
            else:
                logger.warning("Local model loaded but not ready: %s", local_reason)
        except Exception as local_exc:
            set_local_model_availability(
                False,
                f"Local model readiness check failed: {local_exc}",
                loaded=False,
                ready=False,
                last_error=str(local_exc),
            )
            logger.warning("Local model readiness check failed: %s", local_exc)

    local_roles = getattr(main_orchestrator, "local_model_roles", None)
    if local_roles is not None:
        try:
            set_local_model_roles_status(local_roles.status())
        except Exception as role_status_exc:
            logger.warning("Local model role status check failed: %s", role_status_exc)


@dataclass(frozen=True)
class BootTask:
    """Reviewable boot step metadata for the gateway composition root."""
    name: str
    criticality: str
    dependencies: tuple[str, ...] = ()


BOOT_MANIFEST: tuple[BootTask, ...] = (
    BootTask("event_bus_loop_capture", "critical"),
    BootTask("environment_validation", "critical", ("event_bus_loop_capture",)),
    BootTask("core_runtime_services", "critical", ("environment_validation",)),
    BootTask("orchestrator_runtime_wiring", "critical", ("core_runtime_services",)),
    BootTask("feature_flag_snapshot", "optional", ("orchestrator_runtime_wiring",)),
    BootTask("shared_api_auth", "optional", ("orchestrator_runtime_wiring",)),
    BootTask("runtime_soul_refresh_hooks", "optional", ("orchestrator_runtime_wiring",)),
    BootTask("subsystem_router_mounts", "optional", ("shared_api_auth",)),
    BootTask("subsystem_registration", "critical", ("subsystem_router_mounts",)),
    BootTask("core_subsystem_startup", "critical", ("subsystem_registration",)),
    BootTask("local_model_boot", "optional", ("core_subsystem_startup",)),
    BootTask("control_plane_api", "optional", ("core_subsystem_startup",)),
    BootTask("war_room_apis", "optional", ("control_plane_api",)),
    BootTask("tools_and_flags_apis", "optional", ("war_room_apis",)),
    BootTask("connector_runtime", "optional", ("tools_and_flags_apis",)),
    BootTask("mcp_subsystem", "optional", ("connector_runtime",)),
    BootTask("oauth_managers", "optional", ("connector_runtime",)),
    BootTask("health_monitor", "optional", ("oauth_managers",)),
    BootTask("setup_and_recovery_apis", "optional", ("health_monitor",)),
    BootTask("usage_tracking", "optional", ("setup_and_recovery_apis",)),
    BootTask("provider_discovery", "optional", ("usage_tracking",)),
    BootTask("channel_bridges", "optional", ("provider_discovery",)),
    BootTask("secret_reload_handler", "optional", ("provider_discovery",)),
    BootTask("observability", "optional", ("provider_discovery",)),
    BootTask("time_travel", "optional", ("observability",)),
    BootTask("a2a_protocol", "optional", ("time_travel",)),
    BootTask("incident_response", "optional", ("a2a_protocol",)),
)

async def boot(app, config):
        global _startup_time
        _startup_time = time.time()
    
        # Capture the main event loop for cross-thread EventBus publishing
        try:
            from event_bus import event_bus as _eb
            _eb.set_loop(asyncio.get_running_loop())
        except Exception as exc:
            logger.debug("Event bus loop capture skipped during startup: %s", exc)
    
        # F8: Validate environment on startup
        _provider = os.getenv("LANCELOT_PROVIDER", "gemini")
        _key_vars = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "xai": "XAI_API_KEY"}
        _key_var = _key_vars.get(_provider, "GEMINI_API_KEY")
        if not os.getenv(_key_var) and not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
            logger.warning("No %s set. LLM features may be unavailable.", _key_var)
        if not API_TOKEN:
            logger.warning("LANCELOT_API_TOKEN not set. Running in dev mode (no auth required).")
    
        # Start core gateway-owned services before optional subsystems attach.
        librarian.start()
        await antigravity.start()
    
        # Attach runtime-owned services to the orchestrator.
        main_orchestrator.sentry = sentry
        main_orchestrator.mfa_guard = mfa_guard
        main_orchestrator.antigravity = antigravity
    
        # ===== FEATURE FLAG LOGGING =====
        try:
            from feature_flags import log_feature_flags
            log_feature_flags()
        except Exception as e:
            logger.warning(f"Feature flag logging failed: {e}")
    
        # Shared backend auth for War Room/control-plane routers.
        try:
            from src.core.api_auth import init_api_auth
            init_api_auth(verify_token)
        except Exception as e:
            logger.warning("Shared API auth initialization failed: %s", e)
    
        def _apply_runtime_soul(active_soul):
            """Refresh live runtime policy objects after Soul activation."""
            main_orchestrator.soul = active_soul
    
            risk_classifier = getattr(main_orchestrator, "_risk_classifier", None)
            if risk_classifier is not None:
                risk_classifier.update_soul(active_soul)
    
            try:
                from src.a2a.agent_card import invalidate_card
                invalidate_card()
            except Exception as exc:
                logger.warning("A2A agent card invalidation failed after Soul update: %s", exc)
    
            try:
                from src.timetravel.api import update_timetravel_soul
                update_timetravel_soul(active_soul)
            except Exception as exc:
                logger.warning("Time-Travel Soul refresh failed: %s", exc)
    
            try:
                from src.mcp.api import update_mcp_soul
                update_mcp_soul(active_soul)
            except Exception as exc:
                logger.warning("MCP Soul refresh failed: %s", exc)
    
            try:
                hive_entry = subsystem_manager.get("hive")
                lifecycle = hive_entry.objects.get("lifecycle") if hive_entry and hive_entry.running else None
                if lifecycle is not None:
                    lifecycle.update_parent_soul(active_soul)
            except Exception as exc:
                logger.warning("HIVE Soul refresh failed: %s", exc)
    
            app.state.active_soul = active_soul
    
            try:
                federation_entry = subsystem_manager.get("federation")
                emitter = federation_entry.objects.get("emitter") if federation_entry and federation_entry.running else None
                if emitter is not None:
                    emitter.emit_once()
            except Exception as exc:
                logger.warning("Federation heartbeat Soul refresh failed: %s", exc)
    
        app.state.apply_runtime_soul = _apply_runtime_soul
    
        try:
            from soul.api import init_soul_runtime
            init_soul_runtime(_apply_runtime_soul)
        except Exception as e:
            logger.warning("Soul runtime refresh initialization failed: %s", e)
    
        # ===== ALWAYS MOUNT SUBSYSTEM ROUTERS =====
        # Routes are gated by middleware when their flag is OFF.
        try:
            from memory.api import router as memory_router
            app.include_router(memory_router)
        except Exception as e:
            logger.warning("Memory API router mount failed: %s", e)
    
        try:
            from soul.api import router as soul_router
            app.include_router(soul_router)
        except Exception as e:
            logger.warning("Soul API router mount failed: %s", e)
    
        try:
            from soul.template_api import router as template_router
            app.include_router(template_router)
        except Exception as e:
            logger.warning("Soul Template API router mount failed: %s", e)
    
        try:
            from scheduler_api import router as scheduler_router
            app.include_router(scheduler_router)
        except Exception as e:
            logger.warning("Scheduler API router mount failed: %s", e)
    
        try:
            from health.api import router as health_api_router
            app.include_router(health_api_router)
        except Exception as e:
            logger.warning("Health API router mount failed: %s", e)
    
        try:
            from bal.clients.api import router as bal_client_router
            app.include_router(bal_client_router)
        except Exception as e:
            logger.warning("BAL Client API router mount failed: %s", e)
    
        try:
            from src.hive.api import router as hive_router
            app.include_router(hive_router)
        except Exception as e:
            logger.warning("HIVE Agent Mesh API router mount failed: %s", e)
    
        try:
            from src.federation.api import router as federation_router
            app.include_router(federation_router)
        except Exception as e:
            logger.warning("Federation API router mount failed: %s", e)
    
        try:
            from src.federation.graph_api import graph_router
            app.include_router(graph_router)
        except Exception as e:
            logger.warning("Graph Builder API router mount failed: %s", e)
    
        try:
            from src.mcp.api import router as mcp_router
            app.include_router(mcp_router)
        except Exception as e:
            logger.warning("MCP API router mount failed: %s", e)
    
        # ===== REGISTER SUBSYSTEMS WITH HOT-TOGGLE MANAGER =====
        subsystem_manager.register("memory", "FEATURE_MEMORY_VNEXT", _init_memory, _shutdown_memory, ["/memory"])
        subsystem_manager.register("soul", "FEATURE_SOUL", _init_soul, _shutdown_soul, ["/soul"])
        subsystem_manager.register("skills", "FEATURE_SKILLS", _init_skills, _shutdown_skills, [])
        subsystem_manager.register("scheduler", "FEATURE_SCHEDULER", _init_scheduler, _shutdown_scheduler, ["/api/scheduler"])
        subsystem_manager.register("health_monitor", "FEATURE_HEALTH_MONITOR", _init_health_monitor, _shutdown_health_monitor, ["/health"])
        subsystem_manager.register("bal", "FEATURE_BAL", _init_bal, _shutdown_bal, ["/api/v1/clients"])
    
        # Tool Fabric provider subsystems (hot-toggle individual providers)
        subsystem_manager.register("host_bridge", "FEATURE_TOOLS_HOST_BRIDGE", _init_host_bridge, _shutdown_host_bridge, [])
        subsystem_manager.register("uab_bridge", "FEATURE_TOOLS_UAB", _init_uab, _shutdown_uab, [])
        subsystem_manager.register("hive", "FEATURE_HIVE", _init_hive, _shutdown_hive, ["/api/hive"])
        subsystem_manager.register("federation", "FEATURE_FEDERATION", _init_federation, _shutdown_federation, ["/api/federation"])
    
        # ===== CONDITIONALLY START SUBSYSTEMS =====
        from feature_flags import (
            FEATURE_MEMORY_VNEXT, FEATURE_SOUL, FEATURE_SKILLS,
            FEATURE_SCHEDULER, FEATURE_HEALTH_MONITOR, FEATURE_BAL,
            FEATURE_TOOLS_HOST_BRIDGE, FEATURE_TOOLS_UAB, FEATURE_HIVE,
            FEATURE_FEDERATION,
        )
    
        if FEATURE_MEMORY_VNEXT:
            try:
                subsystem_manager.start("memory")
            except Exception as e:
                logger.error("Structured memory initialization failed: %s", e)
                main_orchestrator._memory_enabled = False
        else:
            logger.info("Structured memory disabled by feature flag.")
    
        if FEATURE_SOUL:
            try:
                subsystem_manager.start("soul")
            except Exception as e:
                logger.warning("Soul initialization failed: %s", e)
        else:
            logger.info("Soul disabled by feature flag.")
    
        if FEATURE_SKILLS:
            try:
                subsystem_manager.start("skills")
                # Register Skills API for War Room proposal management
                from skills_api import router as skills_api_router, init_skills_api
                init_skills_api(
                    factory=main_orchestrator.skill_factory,
                    registry=main_orchestrator.skill_registry,
                    executor=main_orchestrator.skill_executor,
                )
                app.include_router(skills_api_router)
                logger.info("Skills API initialized.")
            except Exception as e:
                logger.warning("Skills initialization failed: %s", e)
    
        if FEATURE_SCHEDULER:
            try:
                subsystem_manager.start("scheduler")
            except Exception as e:
                logger.warning("Scheduler initialization failed: %s", e)
    
        # ===== MARK PROVIDER SUBSYSTEMS RUNNING IF ALREADY BOOTED =====
        # _setup_default_providers() already registered these at ToolFabric init,
        # so just mark the SubsystemManager entries as running (no double-init).
        if FEATURE_TOOLS_HOST_BRIDGE:
            entry = subsystem_manager.get("host_bridge")
            if entry and not entry.running:
                entry.running = True
                logger.info("Host Bridge provider marked running (booted at init)")
        if FEATURE_TOOLS_UAB:
            entry = subsystem_manager.get("uab_bridge")
            if entry and not entry.running:
                entry.running = True
                logger.info("UAB Bridge provider marked running (booted at init)")
    
        if FEATURE_HIVE:
            try:
                subsystem_manager.start("hive")
            except Exception as e:
                logger.warning("HIVE Agent Mesh initialization failed: %s", e)
        else:
            logger.info("HIVE Agent Mesh disabled by feature flag.")
    
        if FEATURE_FEDERATION:
            try:
                subsystem_manager.start("federation")
            except Exception as e:
                logger.warning("Federation initialization failed: %s", e)
        else:
            logger.info("Federation disabled by feature flag.")
    
        # ===== LOCAL MODEL CLIENT =====
        try:
            from feature_flags import FEATURE_LOCAL_AGENTIC
            from src.core.local_model_roles import LocalModelRoleRouter
            from local_model_client import LocalModelClient
    
            _local_model = LocalModelClient()
            main_orchestrator.local_model = _local_model
            _local_model_roles = LocalModelRoleRouter.from_env()
            main_orchestrator.local_model_roles = _local_model_roles
            _publish_local_model_runtime_status(main_orchestrator)
    
            if not FEATURE_LOCAL_AGENTIC:
                logger.info("Local execution feature disabled; local model remains installed for scrub and health checks")
        except Exception as e:
            try:
                from src.core.model_usage_policy import set_local_model_availability
    
                set_local_model_availability(
                    False,
                    f"Local model initialization failed: {e}",
                    loaded=False,
                    ready=False,
                    last_error=str(e),
                )
            except Exception as availability_exc:
                logger.warning(
                    "Failed to publish local model initialization failure to usage policy state: %s",
                    availability_exc,
                )
            logger.warning("Local model client initialization failed: %s", e)
    
        if FEATURE_BAL:
            try:
                subsystem_manager.start("bal")
            except Exception as e:
                logger.warning("BAL initialization failed: %s", e)
        else:
            logger.info("BAL disabled by feature flag.")
    
        # ===== PHASE 6: CONTROL PLANE =====
        try:
            from src.core.control_plane import init_control_plane, set_runtime_control_hooks
            from src.core.control_plane import router as cp_router
    
            def _runtime_emergency_stop_handler(
                *,
                reason: str,
                operator_id: str = "",
                operator_name: str = "",
                session_id: str = "",
            ) -> dict:
                hive_entry = subsystem_manager.get("hive")
                lifecycle = hive_entry.objects.get("lifecycle") if hive_entry and hive_entry.running else None
                if lifecycle is None:
                    raise RuntimeError("HIVE emergency stop engine is not available")
    
                collapsed = lifecycle.kill_all(
                    reason,
                    operator_id=operator_id or "operator",
                    session_id=session_id or "system",
                )
                return {
                    "stopped_hive_agents": len(collapsed),
                    "stopped_agent_ids": collapsed,
                    "execution_state": "emergency_stopped",
                }
    
            init_control_plane(data_dir="/home/lancelot/data")
            set_runtime_control_hooks(emergency_stop_handler=_runtime_emergency_stop_handler)
            app.include_router(cp_router)
            _publish_local_model_runtime_status(main_orchestrator)
            logger.info("Control plane initialized.")
        except Exception as e:
            logger.warning(f"Control plane initialization failed: {e}")
    
        # ===== WAR ROOM APIs =====
        # Receipts API
        try:
            import receipts_api as _receipts_api_module
            from receipts_api import router as receipts_router, init_receipts_api
            from src.core.governance_receipts import init_governance_receipts
            init_receipts_api(data_dir="/home/lancelot/data")
            if _receipts_api_module._receipt_service is not None:
                init_governance_receipts(_receipts_api_module._receipt_service)
            app.include_router(receipts_router)
            logger.info("Receipts API initialized.")
        except Exception as e:
            logger.warning(f"Receipts API initialization failed: {e}")
    
        # Compliance Export API
        try:
            from compliance.api import router as compliance_router, init_compliance_api
            from receipts_api import _receipt_service as _compliance_receipt_svc
            if _compliance_receipt_svc is not None:
                init_compliance_api(
                    receipt_service=_compliance_receipt_svc,
                    data_dir="/home/lancelot/data",
                )
                app.include_router(compliance_router)
                logger.info("Compliance Export API initialized.")
            else:
                logger.warning("Compliance Export API skipped: receipt service not available")
        except Exception as e:
            logger.warning(f"Compliance Export API initialization failed: {e}")
    
        # Governance + Trust + APL APIs â€” wire to existing subsystem instances
        _trust_ledger_inst = getattr(main_orchestrator, 'trust_ledger', None)
        _rule_engine_inst = None
        _decision_log_inst = None
        try:
            from governance.approval_learning.rule_engine import RuleEngine
            _rule_engine_inst = getattr(main_orchestrator, 'rule_engine', None)
            _decision_log_inst = getattr(main_orchestrator, 'decision_log', None)
        except ImportError as exc:
            logger.debug("Governance rule engine unavailable during API wiring: %s", exc)
    
        try:
            from governance_api import router as gov_router, init_governance_api
            init_governance_api(
                trust_ledger=_trust_ledger_inst,
                rule_engine=_rule_engine_inst,
                decision_log=_decision_log_inst,
                mcp_sentry=sentry,
            )
            app.include_router(gov_router)
            logger.info("Governance API initialized.")
        except Exception as e:
            logger.warning(f"Governance API initialization failed: {e}")
    
        try:
            from trust_api import router as trust_router, init_trust_api
            init_trust_api(trust_ledger=_trust_ledger_inst)
            app.include_router(trust_router)
            logger.info("Trust API initialized.")
        except Exception as e:
            logger.warning(f"Trust API initialization failed: {e}")
    
        try:
            from apl_api import router as apl_router, init_apl_api
            init_apl_api(rule_engine=_rule_engine_inst, decision_log=_decision_log_inst)
            app.include_router(apl_router)
            logger.info("APL API initialized.")
        except Exception as e:
            logger.warning(f"APL API initialization failed: {e}")
    
        # ===== TOOLS API =====
        try:
            from tools_api import router as tools_router, init_tools_api
            init_tools_api()
            app.include_router(tools_router)
            logger.info("Tools API initialized.")
        except Exception as e:
            logger.warning(f"Tools API initialization failed: {e}")
    
        # ===== FLAGS API =====
        try:
            from flags_api import router as flags_router, init_flags_api
            init_flags_api(audit_logger=main_orchestrator.audit_logger)
            app.include_router(flags_router)
            logger.info("Flags API initialized.")
        except Exception as e:
            logger.warning(f"Flags API initialization failed: {e}")
    
        # ===== TOOL FLOW STREAMING + ACTION CARDS =====
        try:
            from feature_flags import FEATURE_TOOL_FLOW_STREAMING, FEATURE_ACTION_CARDS
            from event_bus import event_bus as _event_bus
    
            # Tool Flow Streaming â€” emitter injected into orchestrator
            if FEATURE_TOOL_FLOW_STREAMING:
                from toolflow.emitter import ToolFlowEmitter
                _toolflow_emitter = ToolFlowEmitter(event_bus=_event_bus, enabled=True)
                main_orchestrator.toolflow_emitter = _toolflow_emitter
                logger.info("ToolFlow streaming enabled â€” emitter injected into orchestrator")
            else:
                logger.info("ToolFlow streaming disabled by feature flag")
    
            # ActionCards â€” store, factory, resolver, API
            if FEATURE_ACTION_CARDS:
                from actioncard.store import ActionCardStore
                from actioncard.factory import ActionCardFactory
                from actioncard.resolver import ActionCardResolver
                from actioncard_api import router as actioncard_router, init_actioncard_api
    
                _ac_store = ActionCardStore(data_dir=main_orchestrator.data_dir)
                _ac_factory = ActionCardFactory(card_store=_ac_store, event_bus=_event_bus)
                _ac_resolver = ActionCardResolver(
                    card_store=_ac_store,
                    event_bus=_event_bus,
                    receipt_service=main_orchestrator.receipt_service,
                )
    
                # Register approval handlers for each subsystem
                # Governance (sentry) handler
                try:
                    from governance_api import _approve_item_direct, _deny_item_direct
                    from src.core.operator_identity import OperatorIdentity
    
                    def _gov_handler(item_id, button_id, **context):
                        identity = OperatorIdentity(
                            operator_id=context.get("operator_id", "") or "",
                            display_name=context.get("actor", "") or "",
                            session_id=context.get("session_id", "") or "",
                        )
                        card = context.get("card")
                        metadata = getattr(card, "metadata", {}) if card is not None else {}
                        batch_request_ids = metadata.get("approval_request_ids") or []
                        if metadata.get("approval_type") == "sentry_t3_batch" and batch_request_ids:
                            action = "approve" if button_id == "approve" else "deny" if button_id in ("deny", "reject") else ""
                            if not action:
                                return {"status": "error", "message": f"Unknown button: {button_id}"}
                            results = []
                            failures = []
                            for request_id in batch_request_ids:
                                if action == "approve":
                                    result = _approve_item_direct(
                                        request_id,
                                        reason="Approved via grouped ActionCard",
                                        identity=identity if identity.operator_id and identity.display_name else None,
                                    )
                                else:
                                    result = _deny_item_direct(
                                        request_id,
                                        reason="Denied via grouped ActionCard",
                                        identity=identity if identity.operator_id and identity.display_name else None,
                                    )
                                if result:
                                    results.append(result)
                                else:
                                    failures.append(request_id)
                            if failures:
                                return {
                                    "status": "error",
                                    "message": f"Could not {action} grouped request(s): {', '.join(failures)}",
                                    "items": results,
                                }
                            return {
                                "status": "approved" if action == "approve" else "denied",
                                "message": (
                                    f"{'Approved' if action == 'approve' else 'Denied'} "
                                    f"{len(results)} grouped governance request(s)"
                                ),
                                "items": results,
                            }
                        if button_id == "approve":
                            result = _approve_item_direct(
                                item_id,
                                reason="Approved via ActionCard",
                                identity=identity if identity.operator_id and identity.display_name else None,
                            )
                            return result or {"status": "error", "message": f"Approval item {item_id} not found"}
                        elif button_id in ("deny", "reject"):
                            result = _deny_item_direct(
                                item_id,
                                reason="Denied via ActionCard",
                                identity=identity if identity.operator_id and identity.display_name else None,
                            )
                            return result or {"status": "error", "message": f"Approval item {item_id} not found"}
                        return {"status": "error", "message": f"Unknown button: {button_id}"}
                    _ac_resolver.register_handler("governance", _gov_handler)
                except Exception as _e:
                    logger.debug("Governance handler not available for ActionCards: %s", _e)
    
                # Scheduler handler
                try:
                    if main_orchestrator.job_executor:
                        def _sched_handler(job_id, button_id, **context):
                            if button_id == "approve":
                                ok = main_orchestrator.job_executor.approve_job(
                                    job_id,
                                    operator_id=context.get("operator_id", "") or "",
                                    session_id=context.get("session_id", "") or "",
                                    actor=context.get("actor", "") or "",
                                )
                                return {"status": "approved" if ok else "error",
                                        "message": "Approved" if ok else "Not pending"}
                            return {"status": "denied", "message": "Denied"}
                        _ac_resolver.register_handler("scheduler", _sched_handler)
                except Exception as _e:
                    logger.debug("Scheduler handler not available for ActionCards: %s", _e)
    
                # Soul handler
                try:
                    from soul.api import _approve_proposal_direct, _reject_proposal_direct
    
                    def _soul_handler(proposal_id, button_id, **context):
                        actor = context.get("actor", "") or context.get("operator_id", "") or "operator"
                        if button_id == "approve":
                            result = _approve_proposal_direct(proposal_id, actor=actor)
                            result["message"] = f"Soul proposal {proposal_id} approved via ActionCard"
                            return result
                        elif button_id in ("deny", "reject"):
                            result = _reject_proposal_direct(proposal_id, actor=actor)
                            result["message"] = f"Soul proposal {proposal_id} denied via ActionCard"
                            return result
                        return {"status": "error", "message": f"Unknown button: {button_id}"}
                    _ac_resolver.register_handler("soul", _soul_handler)
                except Exception as _e:
                    logger.debug("Soul handler not available for ActionCards: %s", _e)
    
                # Skills handler
                try:
                    def _skills_handler(proposal_id, button_id, **context):
                        actor = context.get("actor", "") or context.get("operator_id", "") or "operator"
                        if button_id == "approve":
                            if main_orchestrator.skill_factory:
                                main_orchestrator.skill_factory.approve_proposal(
                                    proposal_id,
                                    approved_by=actor,
                                )
                                return {"status": "approved", "message": f"Skill proposal {proposal_id} approved"}
                            return {"status": "error", "message": "Skill factory not available"}
                        elif button_id in ("reject", "deny"):
                            if main_orchestrator.skill_factory:
                                main_orchestrator.skill_factory.reject_proposal(proposal_id)
                                return {"status": "denied", "message": f"Skill proposal {proposal_id} rejected"}
                            return {"status": "error", "message": "Skill factory not available"}
                        return {"status": "error", "message": f"Unknown button: {button_id}"}
                    _ac_resolver.register_handler("skills", _skills_handler)
                except Exception as _e:
                    logger.debug("Skills handler not available for ActionCards: %s", _e)
    
                init_actioncard_api(_ac_store, _ac_resolver)
                app.include_router(actioncard_router)
    
                # Store references for use by other subsystems
                app.state.actioncard_store = _ac_store
                app.state.actioncard_factory = _ac_factory
                app.state.actioncard_resolver = _ac_resolver
    
                # Wire ActionCard factory into approval subsystems
                try:
                    from soul.api import init_soul_actioncards
                    init_soul_actioncards(_ac_factory)
                except Exception as _e:
                    logger.debug("Soul ActionCard wiring skipped: %s", _e)
                try:
                    if main_orchestrator.skill_factory:
                        main_orchestrator.skill_factory.actioncard_factory = _ac_factory
                except Exception as _e:
                    logger.debug("Skills ActionCard wiring skipped: %s", _e)
                # Wire ActionCard factory into orchestrator for sentry escalation cards
                main_orchestrator.actioncard_factory = _ac_factory
    
                logger.info("ActionCards enabled â€” store, factory, resolver, API initialized")
            else:
                logger.info("ActionCards disabled by feature flag")
        except Exception as e:
            logger.warning("ToolFlow/ActionCards initialization failed: %s", e)
    
        # ===== CONNECTORS SUBSYSTEM =====
        # Always mount the management API so War Room can list/configure connectors.
        # Connector registration in the runtime registry is gated by FEATURE_CONNECTORS.
        _connector_vault_error = None
        try:
            from connectors.registry import ConnectorRegistry
            from connectors.base import ConnectorStatus
            from connectors.vault import CredentialVault as ConnectorVault
            from connectors.runtime import ConnectorRuntime
            from connectors.credential_api import router as cred_router, init_credential_api
            from connectors_api import router as connectors_mgmt_router, init_connectors_api
    
            _connector_registry = ConnectorRegistry(config_path="config/connectors.yaml")
            _connector_vault = _boot_vault if _boot_vault else ConnectorVault(config_path="config/vault.yaml")
    
            # Register enabled connectors even when credentials are missing so
            # operators can see configuration gaps instead of hidden connectors.
            from feature_flags import FEATURE_CONNECTORS
            if FEATURE_CONNECTORS:
                _conn_config = _connector_registry._config.get("connectors", {})
                for _cid, _ccfg in _conn_config.items():
                    if _ccfg.get("enabled", False):
                        try:
                            from connectors_api import register_connector_with_vault_access
                            from src.connectors.google_feature_gate import (
                                google_connector_disabled_reason,
                                is_google_connector_enabled,
                            )
    
                            _backend = _ccfg.get("backend")
                            if not is_google_connector_enabled(_cid, _backend):
                                logger.info(
                                    "Skipping connector %s registration: %s",
                                    _cid,
                                    google_connector_disabled_reason(_cid, _backend),
                                )
                                continue
    
                            _conn = register_connector_with_vault_access(
                                _connector_registry,
                                _connector_vault,
                                _cid,
                                _ccfg,
                            )
                            if _conn:
                                if _conn.status == ConnectorStatus.CONFIGURED:
                                    logger.info(f"Connector registered + configured: {_cid}")
                                else:
                                    logger.info(f"Connector registered but pending credentials: {_cid}")
                        except Exception as _e:
                            logger.warning(f"Failed to register connector {_cid}: {_e}")
    
            _connector_policy_engine = None
            try:
                from src.tools.fabric import get_tool_fabric
                _connector_policy_engine = getattr(get_tool_fabric(), "_policy_engine", None)
            except Exception as _e:
                logger.debug("Connector policy engine unavailable: %s", _e)
    
            # Mount management APIs even if governed execution wiring later degrades.
            init_credential_api(_connector_registry, _connector_vault)
            init_connectors_api(_connector_registry, _connector_vault)
            app.include_router(cred_router)
            app.include_router(connectors_mgmt_router)
    
            try:
                _connector_runtime = ConnectorRuntime(
                    registry=_connector_registry,
                    vault=_connector_vault,
                    risk_classifier=getattr(main_orchestrator, "_risk_classifier", None),
                    policy_engine=_connector_policy_engine,
                    receipt_service=getattr(main_orchestrator, "receipt_service", None),
                    trust_ledger=getattr(main_orchestrator, "trust_ledger", None),
                )
                for _entry in _connector_registry.list_connectors():
                    _connector_runtime.register_connector(_entry.manifest.id)
    
                main_orchestrator.connector_runtime = _connector_runtime
                if getattr(main_orchestrator, "task_runner", None) is not None:
                    main_orchestrator.task_runner.connector_runtime = _connector_runtime
                app.state.connector_runtime = _connector_runtime
            except Exception as _e:
                logger.warning("Connector runtime degraded: %s", _e)
    
            # Expose the live connector registry for runtime capability reporting.
            main_orchestrator._connector_registry = _connector_registry
    
            # Seed vault with current workspace path from docker-compose.yml
            if not _connector_vault.exists("shared_workspace.host_path"):
                try:
                    import re as _re
                    _compose_file = Path("/home/lancelot/app/docker-compose.yml")
                    if _compose_file.exists():
                        _compose_text = _compose_file.read_text(encoding="utf-8")
                        _ws_match = _re.search(
                            r'-\s*["\']?(.+?):/home/lancelot/workspace',
                            _compose_text,
                        )
                        if _ws_match:
                            _ws_path = _ws_match.group(1).strip().strip('"').strip("'")
                            _connector_vault.store(
                                "shared_workspace.host_path", _ws_path, type="config",
                            )
                            logger.info("Seeded vault with workspace path: %s", _ws_path)
                except Exception as _e:
                    logger.debug("Could not seed workspace path: %s", _e)
    
            logger.info("Connectors subsystem initialized (FEATURE_CONNECTORS=%s).", FEATURE_CONNECTORS)
        except Exception as e:
            _connector_vault_error = str(e)
            logger.warning(f"Connectors initialization failed: {e}")
    
        # ===== MCP SUBSYSTEM =====
        try:
            from feature_flags import FEATURE_MCP
            if FEATURE_MCP:
                from src.mcp.api import init_mcp_api
                from src.mcp.argument_screen import MCPArgumentScreener
                from src.mcp.network_policy import MCPNetworkPolicy
                from src.mcp.permissions import MCPPermissionEvaluator
                from src.mcp.proxy import GovernedMCPProxy
                from src.mcp.receipts import MCPReceiptManager
                from src.mcp.registry import MCPServerRegistry
                from src.mcp.response_guard import MCPResponseGuard
    
                _mcp_vault = _connector_vault if '_connector_vault' in dir() else _boot_vault
                _mcp_registry = MCPServerRegistry(vault=_mcp_vault)
                _mcp_evaluator = MCPPermissionEvaluator()
    
                try:
                    _mcp_soul = getattr(main_orchestrator, "soul", None)
                    if _mcp_soul is not None:
                        if hasattr(_mcp_soul, "model_dump"):
                            _mcp_evaluator.load_from_soul(_mcp_soul.model_dump())
                        elif hasattr(_mcp_soul, "dict"):
                            _mcp_evaluator.load_from_soul(_mcp_soul.dict())
                except Exception as _mcp_soul_exc:
                    logger.warning("MCP Soul permission load failed: %s", _mcp_soul_exc)
    
                _mcp_network_policy = MCPNetworkPolicy(
                    network_interceptor=getattr(main_orchestrator, "network_interceptor", None),
                )
    
                _mcp_receipt_service = getattr(main_orchestrator, "receipt_service", None)
                _mcp_proxy = None
                if _mcp_receipt_service is not None:
                    try:
                        _mcp_proxy = GovernedMCPProxy(
                            permission_evaluator=_mcp_evaluator,
                            registry=_mcp_registry,
                            receipt_manager=MCPReceiptManager(_mcp_receipt_service),
                            argument_screener=MCPArgumentScreener(
                                input_sanitizer=getattr(main_orchestrator, "sanitizer", None),
                            ),
                            response_guard=MCPResponseGuard(),
                            network_interceptor=getattr(main_orchestrator, "network_interceptor", None),
                            input_sanitizer=getattr(main_orchestrator, "sanitizer", None),
                        )
                    except Exception as _mcp_proxy_exc:
                        logger.warning("MCP proxy initialization failed: %s", _mcp_proxy_exc)
    
                init_mcp_api(
                    registry=_mcp_registry,
                    evaluator=_mcp_evaluator,
                    proxy=_mcp_proxy,
                    vault=_mcp_vault,
                    network_policy=_mcp_network_policy,
                    receipt_service=_mcp_receipt_service,
                )
                logger.info("MCP subsystem initialized.")
        except Exception as e:
            logger.warning(f"MCP initialization failed: {e}")
    
        # ===== OAUTH TOKEN MANAGER =====
        try:
            from oauth_token_manager import OAuthTokenManager, set_oauth_manager
            from onboarding_snapshot import OnboardingState
            _oauth_vault = _connector_vault if '_connector_vault' in dir() else None
            if _oauth_vault:
                _oauth_mgr = OAuthTokenManager(vault=_oauth_vault)
                set_oauth_manager(_oauth_mgr)
                _oauth_mgr.start_background_refresh()
                logger.info("OAuth token manager initialized.")
    
                # Recover the provider when OAuth becomes available after import-time boot.
                from oauth_token_manager import get_oauth_token as _get_oauth
                if main_orchestrator.provider is None and _get_oauth():
                    logger.info("Re-initializing provider with OAuth token...")
                    main_orchestrator._init_provider()
                    if main_orchestrator.provider:
                        logger.info("Provider initialized via OAuth (post-startup recovery).")
    
                # Advance onboarding if a valid OAuth token is already present.
                if _get_oauth():
                    try:
                        snap = onboarding_orch.snapshot
                        if snap.credential_status in ("oauth_pending", "none"):
                            snap.credential_status = "verified"
                            if snap.state != OnboardingState.READY:
                                snap.state = OnboardingState.READY
                            snap.save()
                            logger.info("Onboarding auto-updated to READY (OAuth token found).")
                    except Exception as _e:
                        logger.warning("Onboarding OAuth recovery failed: %s", _e)
            else:
                logger.warning("OAuth token manager skipped â€” connector vault not available.")
        except Exception as e:
            logger.warning("OAuth token manager initialization failed: %s", e)
    
        # ===== OPENAI CODEX OAUTH TOKEN MANAGER =====
        try:
            from openai_codex_oauth_manager import OpenAICodexOAuthManager, set_openai_codex_manager
            _codex_vault = _connector_vault if '_connector_vault' in dir() else None
            if _codex_vault:
                _codex_mgr = OpenAICodexOAuthManager(vault=_codex_vault, port=1455)
                set_openai_codex_manager(_codex_mgr)
                _codex_mgr.start_background_refresh()
                logger.info("OpenAI Codex OAuth token manager initialized.")
    
                # If Codex OAuth token is now available and provider is openai-codex
                # but wasn't initialized at startup, re-init the provider.
                from openai_codex_oauth_manager import get_codex_oauth_token as _get_codex_token
                _current_provider = os.getenv("LANCELOT_PROVIDER", "gemini")
                if _current_provider == "openai-codex" and main_orchestrator.provider is None and _get_codex_token():
                    logger.info("Re-initializing provider with Codex OAuth token...")
                    main_orchestrator._init_provider()
                    if main_orchestrator.provider:
                        logger.info("Provider initialized via Codex OAuth (post-startup recovery).")
    
                # Update onboarding from oauth_pending if Codex OAuth is connected
                if _current_provider == "openai-codex" and _get_codex_token():
                    try:
                        from onboarding_snapshot import OnboardingState
                        snap = onboarding_orch.snapshot
                        if snap.credential_status in ("oauth_pending", "none"):
                            snap.credential_status = "verified"
                            if snap.state != OnboardingState.READY:
                                snap.state = OnboardingState.READY
                            snap.save()
                            logger.info("Onboarding auto-updated to READY (Codex OAuth token found).")
                    except Exception as _e:
                        logger.warning("Onboarding Codex OAuth recovery failed: %s", _e)
            else:
                logger.warning("Codex OAuth token manager skipped â€” connector vault not available.")
        except Exception as e:
            logger.warning("Codex OAuth token manager initialization failed: %s", e)
    
        # ===== GOOGLE OAUTH MANAGER =====
        try:
            from google_oauth_manager import GoogleOAuthManager, set_google_oauth_manager
            from feature_flags import FEATURE_GOOGLE_OAUTH
            if FEATURE_GOOGLE_OAUTH and '_connector_vault' in dir() and _connector_vault:
                _google_mgr = GoogleOAuthManager(vault=_connector_vault)
                set_google_oauth_manager(_google_mgr)
                if _google_mgr.recover_from_vault():
                    logger.info("Google OAuth tokens recovered on startup.")
                else:
                    logger.info("Google OAuth: no existing tokens, awaiting user setup.")
            else:
                logger.info("Google OAuth disabled (FEATURE_GOOGLE_OAUTH=%s).", FEATURE_GOOGLE_OAUTH)
        except Exception as e:
            logger.warning("Google OAuth initialization failed: %s", e)
    
        # Start health monitoring after OAuth/provider recovery so the first
        # readiness snapshot reflects the settled provider state.
        if FEATURE_HEALTH_MONITOR:
            try:
                subsystem_manager.start("health_monitor")
            except Exception as e:
                logger.warning("Health monitor initialization failed: %s", e)
    
        # ===== AUTH API =====
        try:
            from src.core.auth_api import router as auth_router, init_auth_api
            init_auth_api(audit_logger=main_orchestrator.audit_logger)
            app.include_router(auth_router)
            logger.info("Auth API initialized.")
        except Exception as e:
            logger.warning(f"Auth API initialization failed: {e}")
    
        # ===== SETUP & RECOVERY API =====
        try:
            from setup_api import router as setup_router, init_setup_api
            from receipts_api import _receipt_service as _setup_receipt_svc
            init_setup_api(
                data_dir="/home/lancelot/data",
                startup_time=_startup_time or time.time(),
                audit_logger=main_orchestrator.audit_logger,
                connector_vault=_connector_vault if '_connector_vault' in dir() else None,
                connector_vault_error=_connector_vault_error,
                connector_vault_config_path="config/vault.yaml",
                receipt_service=_setup_receipt_svc,
                verify_request=verify_token,
            )
            app.include_router(setup_router)
            logger.info("Setup API initialized.")
        except Exception as e:
            logger.warning(f"Setup API initialization failed: {e}")
    
        # ===== UPDATE CHECKER + API =====
        try:
            from update_checker import UpdateChecker
            from update_api import router as update_router, init_update_api
    
            _update_checker = UpdateChecker()
            init_update_api(_update_checker)
            _update_checker.start()
            app.include_router(update_router)
            logger.info("Update checker started (version=%s).", _app_version)
        except Exception as e:
            logger.warning(f"Update checker initialization failed: {e}")
    
        # ===== PHASE 6b: USAGE TRACKER + PERSISTENCE =====
        try:
            from usage_tracker import UsageTracker
            from usage_persistence import UsagePersistence
            from src.core.control_plane import set_usage_tracker, set_usage_persistence
    
            _usage_persistence = UsagePersistence(data_dir="/home/lancelot/data")
            set_usage_persistence(_usage_persistence)
    
            if _bootstrap_model_router():
                logger.info("Usage tracker + model router initialized.")
            else:
                _usage_tracker = UsageTracker()
                _usage_tracker.set_persistence(_usage_persistence)
                set_usage_tracker(_usage_tracker)
                main_orchestrator.usage_tracker = _usage_tracker
                logger.info("Usage tracker + persistence initialized (router unavailable).")
        except Exception as e:
            logger.warning(f"Usage tracker initialization failed: {e}")
    
        # ===== PHASE 7: MODEL DISCOVERY + PROVIDER API =====
        try:
            from providers.api import router as provider_router, init_provider_api, load_persisted_config
    
            # If a persisted provider exists, restore it even when boot env selected
            # a provider with no credentials and left the orchestrator uninitialized.
            _persisted_config = load_persisted_config()
            _persisted_provider = _persisted_config.get("active_provider")
            if _persisted_provider:
                _restore_persisted_provider(_persisted_provider, main_orchestrator)
    
            if not _bootstrap_model_discovery():
                init_provider_api(None, orchestrator=main_orchestrator)
                logger.warning("Provider not initialized â€” model discovery skipped")
            app.include_router(provider_router)
        except Exception as e:
            logger.warning(f"Model discovery initialization failed: {e}")
    
        # ===== TELEGRAM TOOL FLOW + ACTIONCARD BRIDGES =====
        try:
            from feature_flags import FEATURE_TOOL_FLOW_STREAMING, FEATURE_ACTION_CARDS
            from event_bus import event_bus as _tg_event_bus
    
            # Wire ToolFlow progress streaming to Telegram
            if FEATURE_TOOL_FLOW_STREAMING and telegram_bot:
                from toolflow.telegram_bridge import TelegramProgressBridge
                _tg_bridge = TelegramProgressBridge(telegram_bot)
                _tg_event_bus.subscribe_all(_tg_bridge.on_toolflow_event)
                logger.info("Telegram ToolFlow progress bridge enabled")
    
            # Wire ActionCard events to Telegram
            if FEATURE_ACTION_CARDS and telegram_bot:
                _tg_event_bus.subscribe("actioncard_presented", telegram_bot._on_actioncard_event)
                _tg_event_bus.subscribe("actioncard_resolved", telegram_bot._on_actioncard_resolved_event)
    
                # Inject resolver and store references for callback handling
                if hasattr(app.state, "actioncard_resolver"):
                    telegram_bot._action_card_resolver = app.state.actioncard_resolver
                if hasattr(app.state, "actioncard_store"):
                    telegram_bot._action_card_store = app.state.actioncard_store
    
                logger.info("Telegram ActionCard event bridges enabled")
        except Exception as e:
            logger.warning("Telegram event bridge initialization failed: %s", e)
    
        # Start Communications Polling
        if telegram_bot:
            telegram_bot.start_polling()
            forge_dispatcher.register_platform(
                name="telegram",
                handler=lambda content: telegram_bot.send_message(
                    telegram_bot._sanitize_for_telegram(content)
                ),
                mode="local"
            )
        elif chat_poller:
            chat_poller.start_polling()
            forge_dispatcher.register_platform(
                name="google_chat",
                handler=lambda content: chat_poller.send_message(content),
                mode="local"
            )
        
        # ===== SIGHUP SECRET RELOAD HANDLER =====
        import platform as _plat
        if _plat.system() != "Windows":
            import signal
            def _sighup_handler(signum, frame):
                """Reload secrets from vault on SIGHUP (Linux/macOS only)."""
                global API_TOKEN
                try:
                    if _boot_vault and secret_cache.is_bootstrapped():
                        changed = secret_cache.reload(_boot_vault)
                        changed_count = sum(1 for v in changed.values() if v)
                        if changed.get("LANCELOT_API_TOKEN"):
                            API_TOKEN = secret_cache.get("LANCELOT_API_TOKEN")
                        logger.info("SIGHUP: secrets reloaded (%d changed)", changed_count)
                except Exception as _e:
                    logger.error("SIGHUP: secret reload failed: %s", _e)
            signal.signal(signal.SIGHUP, _sighup_handler)
            logger.info("SIGHUP handler registered for secret rotation.")
    
        # ===== OBSERVABILITY: OTel Export + Receipt Bridge + API =====
        try:
            from feature_flags import FEATURE_OBSERVABILITY
            if FEATURE_OBSERVABILITY:
                # Mount observability config API
                from observability.api import router as observability_router
                app.include_router(observability_router)
                logger.info("Observability API initialized.")
                from observability.config import load_config as _load_obs_config
                from observability.otel_provider import init_otel
                from observability.receipt_bridge import configure_bridge
    
                _obs_config = _load_obs_config()
    
                # Metrics API
                from observability.metrics_api import router as metrics_api_router, init_metrics_api
                try:
                    from receipts_api import _receipt_service as _metrics_receipt_svc
                    if _metrics_receipt_svc:
                        init_metrics_api(_metrics_receipt_svc, data_dir="/home/lancelot/data")
                        app.include_router(metrics_api_router)
                        logger.info("Metrics API initialized.")
                except Exception as _e:
                    logger.warning("Metrics API initialization failed: %s", _e)
    
                # Webhooks
                if _obs_config.webhooks.enabled and _obs_config.webhooks.endpoints:
                    from observability.webhook_engine import init_webhook_engine
                    init_webhook_engine(
                        endpoints=_obs_config.webhooks.endpoints,
                        deployment_id=os.getenv("LANCELOT_DEPLOYMENT_ID", ""),
                        delivery_timeout_s=_obs_config.webhooks.delivery_timeout_s,
                        max_retries=_obs_config.webhooks.max_retries,
                        data_dir=main_orchestrator.data_dir,
                    )
                    logger.info("Webhook engine initialized (%d endpoints)",
                                len(_obs_config.webhooks.endpoints))
    
                # OTel
                if _obs_config.otel.enabled and _obs_config.otel.endpoint:
                    _otel_ok = init_otel(
                        endpoint=_obs_config.otel.endpoint,
                        auth_header=_obs_config.otel.auth_header,
                        export_interval_ms=_obs_config.otel.export_interval_s * 1000,
                        resource_attributes=_obs_config.otel.resource_attributes,
                    )
                    configure_bridge(
                        enabled=_otel_ok,
                        sampling_rate=_obs_config.otel.sampling_rate_t0_t1,
                    )
                    if _otel_ok:
                        logger.info("OTel export initialized (endpoint=%s)", _obs_config.otel.endpoint)
                    else:
                        logger.warning("OTel export initialization failed â€” bridge disabled")
                else:
                    logger.info("FEATURE_OBSERVABILITY enabled but OTel exporter not configured")
        except Exception as e:
            logger.warning(f"Observability initialization failed: {e}")
    
        _optional_receipt_service = getattr(main_orchestrator, "receipt_service", None)
    
        # â”€â”€ Time-Travel Debugging â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        try:
            from feature_flags import FEATURE_TIME_TRAVEL
            if FEATURE_TIME_TRAVEL:
                from timetravel.api import router as timetravel_router, init_timetravel_api
                app.include_router(timetravel_router)
    
                # Initialize with receipt service and live Soul provider
                _tt_soul = lambda: getattr(main_orchestrator, "soul", None)
    
                def _apply_timetravel_modifications(graph_dict, modifications):
                    for field_path, value in (modifications or {}).items():
                        parts = str(field_path).split(".")
                        cursor = graph_dict
                        for raw_part in parts[:-1]:
                            part = int(raw_part) if isinstance(cursor, list) and raw_part.isdigit() else raw_part
                            cursor = cursor[part]
                        leaf = parts[-1]
                        leaf_key = int(leaf) if isinstance(cursor, list) and leaf.isdigit() else leaf
                        cursor[leaf_key] = value
                    return graph_dict
    
                def _execute_timetravel_quest(*, mode, source_quest_id, new_quest_id, modifications, operator_id, session_id):
                    from datetime import datetime, timezone
                    from src.core.tasking.schema import TaskGraph, TaskRun
    
                    source_run = main_orchestrator.task_store.get_run_by_quest_id(source_quest_id)
                    if source_run is None:
                        raise RuntimeError(f"Source quest is not replayable by TaskRun: {source_quest_id}")
    
                    source_graph = main_orchestrator.task_store.get_graph(source_run.task_graph_id)
                    if source_graph is None:
                        raise RuntimeError(
                            f"TaskGraph not found for source quest {source_quest_id}: {source_run.task_graph_id}"
                        )
    
                    cloned_graph = source_graph.to_dict()
                    cloned_graph["id"] = str(uuid.uuid4())
                    cloned_graph["created_at"] = datetime.now(timezone.utc).isoformat()
                    cloned_graph["session_id"] = session_id or source_graph.session_id or source_run.session_id
    
                    if mode == "fork" and modifications:
                        cloned_graph = _apply_timetravel_modifications(cloned_graph, modifications)
    
                    replay_graph = TaskGraph.from_dict(cloned_graph)
                    main_orchestrator.task_store.save_graph(replay_graph)
    
                    replay_run = TaskRun(
                        task_graph_id=replay_graph.id,
                        execution_token_id=source_run.execution_token_id,
                        session_id=session_id or source_run.session_id,
                        operator_id=operator_id or source_run.operator_id,
                        quest_id=new_quest_id,
                    )
                    main_orchestrator.task_store.create_run(replay_run)
                    result = main_orchestrator.task_runner.run(replay_run.id)
                    return {
                        "run_id": replay_run.id,
                        "task_graph_id": replay_graph.id,
                        "status": result.status,
                        "step_count": len(result.step_results),
                    }
    
                if _optional_receipt_service is not None:
                    init_timetravel_api(
                        receipt_service=_optional_receipt_service,
                        soul=_tt_soul,
                        soul_dir=None,
                        quest_executor=_execute_timetravel_quest,
                        trust_ledger=getattr(main_orchestrator, "trust_ledger", None),
                        data_dir=main_orchestrator.data_dir,
                    )
                    logger.info("FEATURE_TIME_TRAVEL enabled â€” API mounted at /api/timetravel")
                else:
                    logger.warning("Time-Travel: receipt service unavailable")
        except Exception as e:
            logger.warning(f"Time-Travel initialization failed: {e}")
    
        # â”€â”€ A2A Protocol â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        try:
            from feature_flags import FEATURE_A2A
            if FEATURE_A2A:
                from a2a.registry import A2ARegistry
                from a2a.server import a2a_server_router, init_a2a_server
                from a2a.api import router as a2a_api_router, init_a2a_api
                from a2a.inbound_pipeline import InboundPipeline
                from a2a.outbound_pipeline import OutboundPipeline
                from a2a.client import A2AClient
                from a2a.types import A2AArtifact, A2AMessagePart
    
                # Initialize registry
                _a2a_registry = A2ARegistry()
    
                # Load Soul for A2A permissions
                _a2a_soul_provider = lambda: getattr(main_orchestrator, "soul", None)
    
                _a2a_client = A2AClient(_optional_receipt_service)
                _a2a_vault = _connector_vault if '_connector_vault' in dir() else None
    
                # Initialize pipelines
                _a2a_inbound = InboundPipeline(
                    _a2a_registry,
                    _optional_receipt_service,
                    _a2a_soul_provider,
                    vault=_a2a_vault,
                    a2a_client=_a2a_client,
                )
                _a2a_outbound = OutboundPipeline(
                    _a2a_registry,
                    _optional_receipt_service,
                    _a2a_soul_provider,
                    vault=_a2a_vault,
                    a2a_client=_a2a_client,
                    frontier_scrubber=(
                        lambda: main_orchestrator._get_frontier_scrubber()
                        if main_orchestrator is not None
                        else None
                    ),
                )
    
                def _execute_inbound_a2a_task(*, task, caller, quest_id):
                    """Route inbound A2A work through the live orchestrator."""
                    text_parts = []
                    if task.message:
                        for part in task.message.parts:
                            if part.text:
                                text_parts.append(part.text)
                            elif part.data is not None:
                                text_parts.append(json.dumps(part.data, sort_keys=True))
                            elif part.file_uri:
                                text_parts.append(f"[file] {part.file_uri}")
    
                    user_message = "\n".join(p for p in text_parts if p).strip()
                    if not user_message:
                        raise ValueError("Inbound A2A task contained no executable content")
    
                    envelope = (
                        f"[External A2A task from {caller.display_name or caller.agent_id}"
                        f" ({caller.agent_framework})]\n{user_message}"
                    )
                    response_text = main_orchestrator.chat(
                        envelope,
                        channel="api",
                        quest_id=quest_id,
                    )
                    artifacts = [
                        A2AArtifact(
                            parts=[A2AMessagePart(type="text", text=response_text)],
                            metadata={
                                "quest_id": quest_id,
                                "source": "lancelot",
                                "external_peer": caller.agent_id,
                            },
                        ).to_dict()
                    ]
                    return {
                        "status": "completed",
                        "artifacts": artifacts,
                        "message": "Task executed successfully.",
                    }
    
                # Mount protocol-standard endpoints at root
                init_a2a_server(
                    _a2a_soul_provider,
                    _optional_receipt_service,
                    _a2a_registry,
                    _a2a_inbound,
                    task_executor=_execute_inbound_a2a_task,
                    data_dir="/home/lancelot/data",
                )
                app.include_router(a2a_server_router)
    
                # Mount management API
                init_a2a_api(_a2a_registry, _optional_receipt_service, _a2a_soul_provider, _a2a_outbound, _a2a_client)
                app.include_router(a2a_api_router)
    
                logger.info("FEATURE_A2A enabled â€” protocol at /a2a/, management at /api/a2a/")
        except Exception as e:
            logger.warning(f"A2A initialization failed: {e}")
    
        # â”€â”€ Incident Response Playbooks â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        try:
            from feature_flags import FEATURE_INCIDENT_RESPONSE
            if FEATURE_INCIDENT_RESPONSE:
                from src.incidents.api import router as incidents_router, init_incidents_api
                from src.incidents.playbook_api import router as playbook_router, init_playbook_api
                from src.incidents.receipt_hook import configure as configure_incident_hook
    
                init_incidents_api(_optional_receipt_service, "/home/lancelot/data")
                app.include_router(incidents_router)
    
                _playbooks_dir = os.path.join(os.path.dirname(__file__), "..", "..", "playbooks")
                init_playbook_api(_playbooks_dir)
                app.include_router(playbook_router)
    
                configure_incident_hook(enabled=True, data_dir="/home/lancelot/data")
    
                logger.info("FEATURE_INCIDENT_RESPONSE enabled â€” API at /api/incidents/, /api/playbooks/")
        except Exception as e:
            logger.warning(f"Incident Response initialization failed: {e}")
    
        logger.info("Lancelot Gateway started.")
        return BootResult(
            core=BootCore(started_steps=["gateway_boot"]),
            env=BootEnvironment(
                provider=_provider,
                credential_var=_key_var,
                api_token_configured=bool(API_TOKEN),
                startup_time=_startup_time,
            ),
        )
