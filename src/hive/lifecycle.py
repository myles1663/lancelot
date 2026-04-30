"""
HIVE Agent Lifecycle Manager — spawn, execute, pause, kill sub-agents.

Manages the complete lifecycle: spawn → execute → collapse.
Retry spawns NEW agents (never revives collapsed ones).
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Any, Callable, Dict, List, Optional

from src.hive.types import (
    AgentState,
    CollapseReason,
    InterventionType,
    OperatorIntervention,
    SubAgentRecord,
    TaskResult,
    TaskSpec,
)
from src.hive.errors import (
    AgentCollapsedError,
    AgentSpawnDeniedError,
    InterventionRequiresReasonError,
    MaxAgentsExceededError,
)
from src.hive.config import HiveConfig
from src.hive.registry import AgentRegistry
from src.hive.receipt_manager import HiveReceiptManager
from src.hive.scoped_soul import ScopedSoulGenerator
from src.hive.runtime import SubAgentRuntime
from src.hive.integration.governance_bridge import GovernanceBridge
from src.core.runtime_pause import get_runtime_pause_status, is_runtime_paused

logger = logging.getLogger(__name__)


class AgentLifecycleManager:
    """Manages the lifecycle of HIVE sub-agents.

    Spawn → execute (in thread) → collapse. Provides operator controls
    for pause, resume, kill, modify, and kill_all.
    """

    def __init__(
        self,
        config: HiveConfig,
        registry: AgentRegistry,
        receipt_manager: HiveReceiptManager,
        soul_generator: ScopedSoulGenerator,
        governance_bridge: Optional[GovernanceBridge] = None,
        parent_soul=None,
        action_executor: Optional[Callable] = None,
        spawn_gate: Optional[Callable[[TaskSpec], None]] = None,
        spawn_record_hook: Optional[Callable[[SubAgentRecord], None]] = None,
        collapse_record_hook: Optional[Callable[[SubAgentRecord, TaskResult], None]] = None,
    ):
        self._config = config
        self._registry = registry
        self._receipts = receipt_manager
        self._soul_gen = soul_generator
        self._governance = governance_bridge
        self._parent_soul = parent_soul
        self._action_executor = action_executor
        self._spawn_gate = spawn_gate
        self._spawn_record_hook = spawn_record_hook
        self._collapse_record_hook = collapse_record_hook

        # Thread pool for agent execution
        self._executor = ThreadPoolExecutor(
            max_workers=config.max_concurrent_agents,
            thread_name_prefix="hive-agent",
        )

        # Active runtimes keyed by agent_id
        self._runtimes: Dict[str, SubAgentRuntime] = {}
        self._futures: Dict[str, Future] = {}
        self._lock = threading.Lock()

    def update_parent_soul(self, parent_soul) -> None:
        """Refresh the parent Soul used for future scoped-agent spawns."""
        self._parent_soul = parent_soul

    def update_spawn_controls(
        self,
        *,
        spawn_gate: Optional[Callable[[TaskSpec], None]] = None,
        spawn_record_hook: Optional[Callable[[SubAgentRecord], None]] = None,
        collapse_record_hook: Optional[Callable[[SubAgentRecord, TaskResult], None]] = None,
    ) -> None:
        """Refresh runtime hooks used for federation spawn governance."""
        self._spawn_gate = spawn_gate
        self._spawn_record_hook = spawn_record_hook
        self._collapse_record_hook = collapse_record_hook

    # ── Spawn ────────────────────────────────────────────────────────

    def spawn(
        self,
        task_spec: TaskSpec,
        quest_id: Optional[str] = None,
        actions: Optional[List[Dict[str, Any]]] = None,
        parent_receipt_id: Optional[str] = None,
        operator_id: Optional[str] = None,
        session_id: Optional[str] = None,
        operator_name: Optional[str] = None,
    ) -> SubAgentRecord:
        """Spawn a new sub-agent.

        Steps:
        1. Check capacity
        2. Register in SPAWNING state
        3. Generate scoped soul
        4. Create runtime
        5. Transition to READY

        Returns the agent record.
        """
        if is_runtime_paused():
            pause_state = get_runtime_pause_status()
            raise AgentSpawnDeniedError(
                pause_state.get("reason") or "Runtime paused by operator"
            )
        if self._spawn_gate:
            self._spawn_gate(task_spec)

        # Generate scoped soul
        scoped_soul = None
        scope_boundary = None
        soul_hash = None
        if self._parent_soul:
            scoped_soul = self._soul_gen.generate(self._parent_soul, task_spec)
            if not self._soul_gen.validate_more_restrictive(scoped_soul, self._parent_soul):
                raise AgentSpawnDeniedError(
                    "Generated scoped Soul is not more restrictive than parent"
                )
            scope_boundary = self._soul_gen.build_execution_boundary(
                self._parent_soul,
                task_spec,
            )
            soul_hash = ScopedSoulGenerator.hash_soul(scoped_soul)

        # Register
        record = self._registry.register(
            task_spec=task_spec,
            quest_id=quest_id,
            scoped_soul_hash=soul_hash,
            operator_id=operator_id,
            session_id=session_id,
            operator_name=operator_name,
            parent_receipt_id=parent_receipt_id,
        )

        if self._spawn_record_hook:
            try:
                self._spawn_record_hook(record)
            except Exception as exc:
                logger.error("Agent %s federation spawn recording failed: %s", record.agent_id, exc)
                try:
                    self._registry.transition(
                        record.agent_id,
                        AgentState.COLLAPSED,
                        collapse_reason=CollapseReason.ERROR,
                        collapse_message=str(exc),
                    )
                except (AgentCollapsedError, ValueError, KeyError):
                    logger.warning(
                        "Agent %s could not be force-collapsed after spawn record failure",
                        record.agent_id,
                    )
                raise AgentSpawnDeniedError(
                    f"Federation spawn governance recording failed: {exc}"
                ) from exc

        # Create runtime
        runtime = SubAgentRuntime(
            agent_record=record,
            registry=self._registry,
            receipt_manager=self._receipts,
            governance_bridge=self._governance,
            scoped_soul=scoped_soul,
            scope_boundary=scope_boundary,
            action_executor=self._action_executor,
        )

        with self._lock:
            self._runtimes[record.agent_id] = runtime

        # Transition to READY
        self._registry.transition(record.agent_id, AgentState.READY)
        spawn_receipt_id = self._receipts.record_agent_spawned(
            record,
            parent_receipt_id=parent_receipt_id,
        )
        transition_receipt_id = self._receipts.record_agent_state_transition(
            agent_id=record.agent_id,
            from_state=AgentState.SPAWNING,
            to_state=AgentState.READY,
            quest_id=quest_id,
            parent_receipt_id=spawn_receipt_id,
            operator_id=record.operator_id,
            session_id=record.session_id,
            operator_name=record.operator_name,
        )
        record.latest_receipt_id = transition_receipt_id

        logger.info("Agent spawned: %s (quest=%s)", record.agent_id, quest_id)
        return record

    # ── Execute ──────────────────────────────────────────────────────

    def execute(
        self,
        agent_id: str,
        actions: List[Dict[str, Any]],
    ) -> Future:
        """Start agent execution in a thread.

        Transitions READY → EXECUTING, then runs the action loop.
        Returns a Future that resolves to TaskResult.
        """
        with self._lock:
            runtime = self._runtimes.get(agent_id)
        if runtime is None:
            raise KeyError(f"No runtime for agent {agent_id}")
        record = runtime.get_record()

        self._registry.transition(agent_id, AgentState.EXECUTING)
        transition_receipt_id = self._receipts.record_agent_state_transition(
            agent_id=agent_id,
            from_state=AgentState.READY,
            to_state=AgentState.EXECUTING,
            quest_id=self._get_quest_id(agent_id),
            parent_receipt_id=runtime.latest_receipt_id(),
            operator_id=record.operator_id,
            session_id=record.session_id,
            operator_name=record.operator_name,
        )
        runtime.set_latest_receipt_id(transition_receipt_id)

        def _run():
            try:
                result = runtime.run(actions)
                # Transition to COMPLETING then COLLAPSED
                try:
                    self._registry.transition(agent_id, AgentState.COMPLETING)
                except (AgentCollapsedError, ValueError, KeyError):
                    logger.warning("Agent %s could not transition to COMPLETING after runtime success", agent_id)
                collapse_reason = result.collapse_reason or CollapseReason.COMPLETED
                try:
                    self._registry.transition(
                        agent_id,
                        AgentState.COLLAPSED,
                        collapse_reason=collapse_reason,
                        collapse_message=result.error_message,
                    )
                except (AgentCollapsedError, ValueError, KeyError):
                    logger.warning("Agent %s could not transition to COLLAPSED after runtime success", agent_id)

                collapse_receipt_id = self._receipts.record_agent_collapsed(
                    agent_id=agent_id,
                    reason=collapse_reason,
                    message=result.error_message,
                    quest_id=self._get_quest_id(agent_id),
                    parent_receipt_id=runtime.latest_receipt_id(),
                    operator_id=record.operator_id,
                    session_id=record.session_id,
                    operator_name=record.operator_name,
                )
                runtime.set_latest_receipt_id(collapse_receipt_id)
                if self._collapse_record_hook:
                    try:
                        self._collapse_record_hook(record, result)
                    except Exception as exc:
                        logger.warning(
                            "Agent %s federation collapse recording failed: %s",
                            agent_id,
                            exc,
                        )
                return result
            except Exception as exc:
                logger.error("Agent %s execution error: %s", agent_id, exc)
                try:
                    self._registry.transition(
                        agent_id,
                        AgentState.COLLAPSED,
                        collapse_reason=CollapseReason.ERROR,
                        collapse_message=str(exc),
                    )
                except (AgentCollapsedError, ValueError, KeyError):
                    logger.warning("Agent %s could not transition to COLLAPSED after runtime error", agent_id)
                result = TaskResult(
                    task_id=record.task_spec.task_id,
                    agent_id=agent_id,
                    success=False,
                    error_message=str(exc),
                    collapse_reason=CollapseReason.ERROR,
                )
                if self._collapse_record_hook:
                    try:
                        self._collapse_record_hook(record, result)
                    except Exception as hook_exc:
                        logger.warning(
                            "Agent %s federation collapse recording failed: %s",
                            agent_id,
                            hook_exc,
                        )
                return result
            finally:
                with self._lock:
                    self._runtimes.pop(agent_id, None)
                    self._futures.pop(agent_id, None)

        future = self._executor.submit(_run)
        with self._lock:
            self._futures[agent_id] = future
        return future

    # ── Operator Controls ────────────────────────────────────────────

    def pause(
        self,
        agent_id: str,
        reason: str,
        operator_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        """Pause an executing agent."""
        if not reason.strip():
            raise InterventionRequiresReasonError("pause")

        with self._lock:
            runtime = self._runtimes.get(agent_id)
        if runtime is None:
            raise KeyError(f"No active runtime for agent {agent_id}")

        runtime.pause(reason)
        try:
            self._registry.transition(agent_id, AgentState.PAUSED)
        except (ValueError, AgentCollapsedError):
            logger.warning("Agent %s could not transition to PAUSED during operator pause", agent_id)

        self._registry.record_intervention(agent_id, {
            "type": InterventionType.PAUSE.value,
            "reason": reason,
        })

    def resume(
        self,
        agent_id: str,
        operator_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        """Resume a paused agent."""
        with self._lock:
            runtime = self._runtimes.get(agent_id)
        if runtime is None:
            raise KeyError(f"No active runtime for agent {agent_id}")

        runtime.resume()
        try:
            self._registry.transition(agent_id, AgentState.EXECUTING)
        except (ValueError, AgentCollapsedError):
            logger.warning("Agent %s could not transition back to EXECUTING during operator resume", agent_id)
        record = runtime.get_record()
        self._receipts.record_agent_resumed(
            agent_id=agent_id,
            quest_id=self._get_quest_id(agent_id),
            parent_receipt_id=runtime.latest_receipt_id(),
            operator_id=record.operator_id,
            session_id=record.session_id,
            operator_name=record.operator_name,
        )

    def kill(
        self,
        agent_id: str,
        reason: str,
        operator_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        """Kill an agent (request collapse)."""
        if not reason.strip():
            raise InterventionRequiresReasonError("kill")

        with self._lock:
            runtime = self._runtimes.get(agent_id)

        if runtime:
            runtime.request_collapse(CollapseReason.OPERATOR_KILL, reason)
        else:
            # Agent may not be executing — force collapse via registry
            try:
                self._registry.transition(
                    agent_id,
                    AgentState.COLLAPSED,
                    collapse_reason=CollapseReason.OPERATOR_KILL,
                    collapse_message=reason,
                )
            except (AgentCollapsedError, ValueError, KeyError):
                logger.warning("Agent %s could not be force-collapsed during operator kill", agent_id)

        self._registry.record_intervention(agent_id, {
            "type": InterventionType.KILL.value,
            "reason": reason,
        })
        receipt_id = self._receipts.record_intervention(
            intervention_type=InterventionType.KILL,
            agent_id=agent_id,
            reason=reason,
            quest_id=self._get_quest_id(agent_id),
            operator_id=operator_id,
            session_id=session_id,
            parent_receipt_id=getattr(self._registry.get(agent_id), "latest_receipt_id", None),
        )
        record = self._registry.get(agent_id)
        if record is not None:
            record.latest_receipt_id = receipt_id

    def kill_all(
        self,
        reason: str,
        operator_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> List[str]:
        """Kill all active agents."""
        if not reason.strip():
            raise InterventionRequiresReasonError("kill_all")

        # Signal all runtimes to collapse
        with self._lock:
            for runtime in self._runtimes.values():
                runtime.request_collapse(
                    CollapseReason.OPERATOR_KILL_ALL, reason,
                )

        # Also collapse via registry for non-executing agents
        collapsed = self._registry.collapse_all(
            reason=CollapseReason.OPERATOR_KILL_ALL,
            message=reason,
        )

        receipt_id = self._receipts.record_intervention(
            intervention_type=InterventionType.KILL_ALL,
            reason=reason,
            operator_id=operator_id,
            session_id=session_id,
        )
        for agent_id in collapsed:
            record = self._registry.get(agent_id)
            if record is not None:
                record.latest_receipt_id = receipt_id

        logger.warning("Kill all: %d agents collapsed", len(collapsed))
        return collapsed

    def intervene(
        self,
        agent_id: str,
        intervention: OperatorIntervention,
        operator_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        """Process an operator intervention."""
        if not intervention.reason.strip():
            raise InterventionRequiresReasonError(intervention.intervention_type.value)

        if intervention.intervention_type == InterventionType.PAUSE:
            self.pause(agent_id, intervention.reason, operator_id=operator_id, session_id=session_id)
        elif intervention.intervention_type == InterventionType.RESUME:
            self.resume(agent_id, operator_id=operator_id, session_id=session_id)
        elif intervention.intervention_type == InterventionType.KILL:
            self.kill(agent_id, intervention.reason, operator_id=operator_id, session_id=session_id)
        elif intervention.intervention_type == InterventionType.MODIFY:
            # Modify = kill + replan (handled by architect)
            self.kill(agent_id, intervention.reason, operator_id=operator_id, session_id=session_id)

        record = self._registry.get(agent_id)
        receipt_id = self._receipts.record_intervention(
            intervention_type=intervention.intervention_type,
            agent_id=agent_id,
            reason=intervention.reason,
            feedback=intervention.feedback,
            quest_id=self._get_quest_id(agent_id),
            operator_id=operator_id,
            session_id=session_id,
            parent_receipt_id=getattr(record, "latest_receipt_id", None),
        )
        if record is not None:
            record.latest_receipt_id = receipt_id

    # ── Helpers ──────────────────────────────────────────────────────

    def get_runtime(self, agent_id: str) -> Optional[SubAgentRuntime]:
        """Get the active runtime for an agent."""
        with self._lock:
            return self._runtimes.get(agent_id)

    def _get_quest_id(self, agent_id: str) -> Optional[str]:
        """Get quest_id for an agent."""
        record = self._registry.get(agent_id)
        return record.quest_id if record else None

    def shutdown(self) -> None:
        """Shut down the lifecycle manager and its thread pool.

        This is a system-initiated action (no human operator), so we
        collapse agents directly without emitting intervention receipts
        that would require operator identity.
        """
        # Signal all runtimes to collapse
        with self._lock:
            for runtime in self._runtimes.values():
                runtime.request_collapse(
                    CollapseReason.OPERATOR_KILL_ALL,
                    "Lifecycle manager shutdown",
                )

        # Collapse via registry for non-executing agents
        self._registry.collapse_all(
            reason=CollapseReason.OPERATOR_KILL_ALL,
            message="Lifecycle manager shutdown",
        )

        self._executor.shutdown(wait=False)
