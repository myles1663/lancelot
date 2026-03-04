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
    ):
        self._config = config
        self._registry = registry
        self._receipts = receipt_manager
        self._soul_gen = soul_generator
        self._governance = governance_bridge
        self._parent_soul = parent_soul
        self._action_executor = action_executor

        # Thread pool for agent execution
        self._executor = ThreadPoolExecutor(
            max_workers=config.max_concurrent_agents,
            thread_name_prefix="hive-agent",
        )

        # Active runtimes keyed by agent_id
        self._runtimes: Dict[str, SubAgentRuntime] = {}
        self._futures: Dict[str, Future] = {}
        self._lock = threading.Lock()

    # ── Spawn ────────────────────────────────────────────────────────

    def spawn(
        self,
        task_spec: TaskSpec,
        quest_id: Optional[str] = None,
        actions: Optional[List[Dict[str, Any]]] = None,
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
        # Generate scoped soul
        scoped_soul = None
        soul_hash = None
        if self._parent_soul:
            scoped_soul = self._soul_gen.generate(self._parent_soul, task_spec)
            soul_hash = ScopedSoulGenerator.hash_soul(scoped_soul)

        # Register
        record = self._registry.register(
            task_spec=task_spec,
            quest_id=quest_id,
            scoped_soul_hash=soul_hash,
        )

        # Create runtime
        runtime = SubAgentRuntime(
            agent_record=record,
            registry=self._registry,
            receipt_manager=self._receipts,
            governance_bridge=self._governance,
            scoped_soul=scoped_soul,
            action_executor=self._action_executor,
        )

        with self._lock:
            self._runtimes[record.agent_id] = runtime

        # Transition to READY
        self._registry.transition(record.agent_id, AgentState.READY)
        self._receipts.record_agent_spawned(record)
        self._receipts.record_agent_state_transition(
            agent_id=record.agent_id,
            from_state=AgentState.SPAWNING,
            to_state=AgentState.READY,
            quest_id=quest_id,
        )

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
        self._registry.transition(agent_id, AgentState.EXECUTING)
        self._receipts.record_agent_state_transition(
            agent_id=agent_id,
            from_state=AgentState.READY,
            to_state=AgentState.EXECUTING,
            quest_id=self._get_quest_id(agent_id),
        )

        with self._lock:
            runtime = self._runtimes.get(agent_id)
        if runtime is None:
            raise KeyError(f"No runtime for agent {agent_id}")

        def _run():
            try:
                result = runtime.run(actions)
                # Transition to COMPLETING then COLLAPSED
                try:
                    self._registry.transition(agent_id, AgentState.COMPLETING)
                except (AgentCollapsedError, ValueError, KeyError):
                    pass
                collapse_reason = result.collapse_reason or CollapseReason.COMPLETED
                try:
                    self._registry.transition(
                        agent_id,
                        AgentState.COLLAPSED,
                        collapse_reason=collapse_reason,
                        collapse_message=result.error_message,
                    )
                except (AgentCollapsedError, ValueError, KeyError):
                    pass

                self._receipts.record_agent_collapsed(
                    agent_id=agent_id,
                    reason=collapse_reason,
                    message=result.error_message,
                    quest_id=self._get_quest_id(agent_id),
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
                    pass
                return TaskResult(
                    task_id=runtime._record.task_spec.task_id,
                    agent_id=agent_id,
                    success=False,
                    error_message=str(exc),
                    collapse_reason=CollapseReason.ERROR,
                )
            finally:
                with self._lock:
                    self._runtimes.pop(agent_id, None)
                    self._futures.pop(agent_id, None)

        future = self._executor.submit(_run)
        with self._lock:
            self._futures[agent_id] = future
        return future

    # ── Operator Controls ────────────────────────────────────────────

    def pause(self, agent_id: str, reason: str) -> None:
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
            pass

        self._registry.record_intervention(agent_id, {
            "type": InterventionType.PAUSE.value,
            "reason": reason,
        })

    def resume(self, agent_id: str) -> None:
        """Resume a paused agent."""
        with self._lock:
            runtime = self._runtimes.get(agent_id)
        if runtime is None:
            raise KeyError(f"No active runtime for agent {agent_id}")

        runtime.resume()
        try:
            self._registry.transition(agent_id, AgentState.EXECUTING)
        except (ValueError, AgentCollapsedError):
            pass

    def kill(self, agent_id: str, reason: str) -> None:
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
                pass

        self._registry.record_intervention(agent_id, {
            "type": InterventionType.KILL.value,
            "reason": reason,
        })
        self._receipts.record_intervention(
            intervention_type=InterventionType.KILL,
            agent_id=agent_id,
            reason=reason,
            quest_id=self._get_quest_id(agent_id),
        )

    def kill_all(self, reason: str) -> List[str]:
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

        self._receipts.record_intervention(
            intervention_type=InterventionType.KILL_ALL,
            reason=reason,
        )

        logger.warning("Kill all: %d agents collapsed", len(collapsed))
        return collapsed

    def intervene(
        self,
        agent_id: str,
        intervention: OperatorIntervention,
    ) -> None:
        """Process an operator intervention."""
        if not intervention.reason.strip():
            raise InterventionRequiresReasonError(intervention.intervention_type.value)

        if intervention.intervention_type == InterventionType.PAUSE:
            self.pause(agent_id, intervention.reason)
        elif intervention.intervention_type == InterventionType.RESUME:
            self.resume(agent_id)
        elif intervention.intervention_type == InterventionType.KILL:
            self.kill(agent_id, intervention.reason)
        elif intervention.intervention_type == InterventionType.MODIFY:
            # Modify = kill + replan (handled by architect)
            self.kill(agent_id, intervention.reason)

        self._receipts.record_intervention(
            intervention_type=intervention.intervention_type,
            agent_id=agent_id,
            reason=intervention.reason,
            feedback=intervention.feedback,
            quest_id=self._get_quest_id(agent_id),
        )

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
        """Shut down the lifecycle manager and its thread pool."""
        self.kill_all("Lifecycle manager shutdown")
        self._executor.shutdown(wait=False)
