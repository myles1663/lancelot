"""HIVE subsystem wiring for the gateway boot sequence."""

from __future__ import annotations


class OrchestratorRouterAdapter:
    """Adapt the orchestrator provider to HIVE's task-decomposition router."""

    def __init__(self, orchestrator, logger):
        self._orch = orchestrator
        self._logger = logger

    def route(self, task_type: str, text: str, **kwargs):
        from dataclasses import dataclass
        from typing import Optional

        @dataclass
        class _Result:
            output: Optional[str] = None

        provider = self._orch.provider
        if provider is None:
            return _Result(output=None)

        try:
            deep_model = self._orch.get_deep_model()
            messages = [self._orch.build_frontier_user_message(text)]
            result = self._orch.provider_generate(
                model=deep_model,
                messages=messages,
                system_instruction="You are a task decomposer. Return only valid JSON.",
                config={"max_tokens": 4096},
            )
            return _Result(output=result.text if result and result.text else None)
        except Exception as exc:
            self._logger.error(
                "HIVE router adapter failed task decomposition with deep model for task_type=%s: %s",
                task_type,
                exc,
            )
            try:
                messages = [self._orch.build_frontier_user_message(text)]
                result = self._orch.provider_generate(
                    model=self._orch.model_name,
                    messages=messages,
                    system_instruction="You are a task decomposer. Return only valid JSON.",
                    config={"max_tokens": 4096},
                )
                return _Result(output=result.text if result and result.text else None)
            except Exception as fallback_exc:
                self._logger.warning(
                    "HIVE router adapter fallback model also failed for task_type=%s: %s",
                    task_type,
                    fallback_exc,
                )
                return _Result(output=None)


def get_uab_provider(logger):
    """Return a UAB provider when the daemon can be reached or may recover later."""
    from src.core.uab_runtime_adapter import get_uab_provider as adapter_get_uab_provider

    return adapter_get_uab_provider(logger)


def summarize_uab_provider_health(provider, health):
    """Return UAB startup status through the provider's public contract."""
    from src.core.uab_runtime_adapter import summarize_uab_provider_health as adapter_summarize

    return adapter_summarize(provider, health)


def init_hive(*, main_orchestrator, sentry, subsystem_manager, logger):
    """Initialize the HIVE Agent Mesh subsystem."""
    import os

    from feature_flags import FEATURE_HIVE_UAB
    from src.hive.api import init_hive_api
    from src.hive.architect import ArchitectAgent
    from src.hive.config import load_hive_config
    from src.hive.decomposer import TaskDecomposer
    from src.hive.integration.governance_bridge import GovernanceBridge
    from src.hive.integration.uab_executor import HiveUABExecutor
    from src.hive.lifecycle import AgentLifecycleManager
    from src.hive.receipt_manager import HiveReceiptManager
    from src.hive.registry import AgentRegistry
    from src.hive.scoped_soul import ScopedSoulGenerator

    config = load_hive_config()
    registry = AgentRegistry(max_concurrent_agents=config.max_concurrent_agents)
    data_dir = os.environ.get("LANCELOT_DATA_DIR", "lancelot_data")
    receipt_mgr = HiveReceiptManager(data_dir=data_dir)
    soul_gen = ScopedSoulGenerator()
    parent_soul = getattr(main_orchestrator, "soul", None)
    governance_bridge = GovernanceBridge(
        risk_classifier=getattr(main_orchestrator, "_risk_classifier", None),
        trust_ledger=getattr(main_orchestrator, "trust_ledger", None),
        decision_log=getattr(main_orchestrator, "decision_log", None),
        mcp_sentry=sentry,
        enforce_kill_switches=True,
    )

    router_adapter = OrchestratorRouterAdapter(main_orchestrator, logger)

    action_executor = None
    action_executor_state = "none"
    if FEATURE_HIVE_UAB:
        uab_provider, uab_status = get_uab_provider(logger)
        if uab_provider:
            action_executor = HiveUABExecutor(
                uab_provider=uab_provider,
                llm_router=router_adapter,
                governance_bridge=governance_bridge,
            )
            startup_state = uab_status["state"]
            action_executor_state = "active" if startup_state == "healthy" else f"recovery:{startup_state}"
            if startup_state == "healthy":
                logger.info("HIVE UAB executor wired; sub-agents can execute governed desktop actions")
            else:
                logger.info(
                    "HIVE UAB executor wired for recovery; desktop actions remain unavailable "
                    "until the daemon is healthy (startup_state=%s, daemon_url=%s, error=%s)",
                    startup_state,
                    uab_status["daemon_url"],
                    uab_status["error"],
                )
        else:
            logger.warning("HIVE_UAB enabled but no UABProvider found; sub-agents will run without UAB")

    lifecycle = AgentLifecycleManager(
        config=config,
        registry=registry,
        receipt_manager=receipt_mgr,
        soul_generator=soul_gen,
        governance_bridge=governance_bridge,
        parent_soul=parent_soul,
        action_executor=action_executor,
    )

    federation_entry = subsystem_manager.get("federation")
    if federation_entry and federation_entry.running:
        try:
            lifecycle.update_spawn_controls(
                spawn_gate=federation_entry.objects.get("spawn_gate"),
                spawn_record_hook=federation_entry.objects.get("spawn_record_hook"),
                collapse_record_hook=federation_entry.objects.get("collapse_record_hook"),
            )
        except Exception as exc:
            logger.warning("Failed to wire existing federation budget governance into HIVE lifecycle: %s", exc)

    decomposer = TaskDecomposer(model_router=router_adapter)
    architect = ArchitectAgent(
        config=config,
        decomposer=decomposer,
        lifecycle=lifecycle,
        receipt_manager=receipt_mgr,
    )

    init_hive_api(architect, lifecycle, registry, receipt_mgr, config, audit_logger=main_orchestrator.audit_logger)

    logger.info(
        "HIVE Agent Mesh initialized: max_agents=%d, timeout=%ds, uab_executor=%s",
        config.max_concurrent_agents,
        config.default_task_timeout,
        action_executor_state,
    )
    return {
        "config": config,
        "registry": registry,
        "receipt_mgr": receipt_mgr,
        "lifecycle": lifecycle,
        "architect": architect,
    }


def shutdown_hive(objects, logger):
    """Shut down the HIVE Agent Mesh subsystem."""
    from src.hive.api import shutdown_hive_api

    if objects.get("lifecycle"):
        try:
            objects["lifecycle"].shutdown()
        except Exception as exc:
            logger.warning("HIVE lifecycle shutdown failed: %s", exc)
    shutdown_hive_api()
    logger.info("HIVE Agent Mesh shut down.")
