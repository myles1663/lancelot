"""
HIVE Architect Agent — persistent singleton orchestrating task execution.

The Architect receives a high-level goal, decomposes it into subtasks,
spawns sub-agents, executes them in dependency order, handles interventions,
and assembles final results.

Key rules:
- Retry spawns NEW agents (never revives collapsed ones)
- Never retry identical plans after intervention — replan with feedback
- Critical failures trigger replan or abort
"""

from __future__ import annotations

import logging
import uuid
from concurrent.futures import Future
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from src.hive.types import (
    AgentState,
    CollapseReason,
    DecomposedTask,
    InterventionType,
    OperatorIntervention,
    SubAgentRecord,
    TaskResult,
    TaskSpec,
)
from src.hive.errors import (
    HiveError,
    TaskDecompositionError,
)
from src.hive.config import HiveConfig
from src.hive.decomposer import TaskDecomposer
from src.hive.lifecycle import AgentLifecycleManager
from src.hive.receipt_manager import HiveReceiptManager

logger = logging.getLogger(__name__)


class ArchitectAgent:
    """Persistent singleton that orchestrates HIVE task execution.

    Lifecycle:
    1. Receive goal → generate quest_id
    2. Decompose into subtasks (via TaskDecomposer)
    3. For each execution group:
       a. Spawn agents
       b. Execute concurrently
       c. Collect results
    4. Handle interventions (replan with feedback)
    5. Assemble and return results
    """

    def __init__(
        self,
        config: HiveConfig,
        decomposer: TaskDecomposer,
        lifecycle: AgentLifecycleManager,
        receipt_manager: HiveReceiptManager,
    ):
        self._config = config
        self._decomposer = decomposer
        self._lifecycle = lifecycle
        self._receipts = receipt_manager

        # Current task state
        self._current_quest_id: Optional[str] = None
        self._current_goal: Optional[str] = None
        self._current_plan: Optional[DecomposedTask] = None
        self._status: str = "idle"
        self._results: Dict[str, TaskResult] = {}
        self._plan_history: List[str] = []  # Hashes of previous plans

    # ── Main Entry Point ─────────────────────────────────────────────

    async def execute_task(
        self,
        goal: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute a high-level task.

        Args:
            goal: High-level goal description.
            context: Additional context.

        Returns:
            Dict with quest_id, success, results, and metadata.
        """
        quest_id = str(uuid.uuid4())
        self._current_quest_id = quest_id
        self._current_goal = goal
        self._status = "decomposing"
        self._results = {}
        self._plan_history = []

        self._receipts.record_task_received(
            goal=goal,
            quest_id=quest_id,
            context=context,
        )

        try:
            # Decompose the goal
            plan = await self._decompose(goal, context, quest_id)
            self._current_plan = plan
            self._plan_history.append(self._plan_hash(plan))

            self._receipts.record_decomposition(
                decomposed=plan,
            )

            # Execute groups in order
            self._status = "executing"
            group_results = await self._execute_plan(plan, quest_id)

            # Assemble results
            self._status = "completing"
            success = all(r.success for r in group_results)

            self._receipts.record_task_completed(
                quest_id=quest_id,
                results=group_results,
            )

            self._status = "idle"
            return {
                "quest_id": quest_id,
                "success": success,
                "results": [
                    {
                        "agent_id": r.agent_id,
                        "success": r.success,
                        "action_count": r.action_count,
                        "error": r.error_message,
                    }
                    for r in group_results
                ],
                "plan": {
                    "subtask_count": len(plan.subtasks),
                    "execution_order": plan.execution_order,
                },
            }

        except Exception as exc:
            logger.error("Architect task failed: %s", exc)
            self._status = "failed"
            self._receipts.record_task_failed(
                quest_id=quest_id,
                error=str(exc),
            )
            return {
                "quest_id": quest_id,
                "success": False,
                "error": str(exc),
                "results": [],
            }

    # ── Plan Execution ───────────────────────────────────────────────

    async def _execute_plan(
        self,
        plan: DecomposedTask,
        quest_id: str,
    ) -> List[TaskResult]:
        """Execute a decomposed plan group by group.

        Groups are executed sequentially; agents within a group
        execute concurrently.
        """
        all_results: List[TaskResult] = []

        for group_idx, group in enumerate(plan.execution_order):
            logger.info(
                "Quest %s: executing group %d/%d (%d agents)",
                quest_id, group_idx + 1, len(plan.execution_order), len(group),
            )

            group_results = await self._execute_group(
                plan, group, quest_id,
            )
            all_results.extend(group_results)

            # Check for critical failures
            failures = [r for r in group_results if not r.success]
            if failures:
                critical = any(
                    r.collapse_reason == CollapseReason.OPERATOR_KILL
                    or r.collapse_reason == CollapseReason.OPERATOR_KILL_ALL
                    for r in failures
                )
                if critical:
                    logger.warning(
                        "Quest %s: operator kill detected, stopping execution",
                        quest_id,
                    )
                    break

        return all_results

    async def _execute_group(
        self,
        plan: DecomposedTask,
        group_indices: List,
        quest_id: str,
    ) -> List[TaskResult]:
        """Execute a single group of subtasks concurrently."""
        futures: List[tuple[str, Future]] = []

        for raw_idx in group_indices:
            idx = int(raw_idx) if isinstance(raw_idx, str) else raw_idx
            if idx >= len(plan.subtasks):
                continue
            spec = plan.subtasks[idx]

            # Spawn agent
            record = self._lifecycle.spawn(spec, quest_id=quest_id)

            # Build actions for the agent with full context for UAB execution
            task_context = plan.context.get("original_context", {}) if plan.context else {}
            actions = [{
                "action": "execute_subtask",
                "spec": spec.description,
                "context": task_context,
                "capability": "app_control" if task_context.get("target_pid") else "execute",
            }]

            # Execute
            future = self._lifecycle.execute(record.agent_id, actions)
            futures.append((record.agent_id, future))

        # Collect results
        results = []
        for agent_id, future in futures:
            try:
                result = future.result(timeout=self._config.default_task_timeout)
                results.append(result)
                self._results[agent_id] = result
            except Exception as exc:
                logger.error("Agent %s failed: %s", agent_id, exc)
                results.append(TaskResult(
                    task_id="",
                    agent_id=agent_id,
                    success=False,
                    error_message=str(exc),
                    collapse_reason=CollapseReason.ERROR,
                ))

        return results

    # ── Decomposition ────────────────────────────────────────────────

    async def _decompose(
        self,
        goal: str,
        context: Optional[Dict[str, Any]],
        quest_id: str,
    ) -> DecomposedTask:
        """Decompose goal into subtasks via TaskDecomposer."""
        return await self._decomposer.decompose(
            goal=goal,
            context=context,
            quest_id=quest_id,
        )

    # ── Intervention Handling ────────────────────────────────────────

    async def handle_intervention(
        self,
        intervention: OperatorIntervention,
        feedback: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Handle an operator intervention.

        For MODIFY interventions, triggers a replan with the operator's
        feedback. Never retries identical plans.

        Returns status dict.
        """
        if intervention.intervention_type == InterventionType.KILL_ALL:
            collapsed = self._lifecycle.kill_all(
                intervention.reason or "Operator kill all",
            )
            self._status = "idle"
            return {
                "action": "kill_all",
                "collapsed": collapsed,
            }

        if intervention.intervention_type == InterventionType.KILL:
            if intervention.agent_id:
                self._lifecycle.kill(
                    intervention.agent_id,
                    intervention.reason or "Operator kill",
                )
            return {
                "action": "kill",
                "agent_id": intervention.agent_id,
            }

        if intervention.intervention_type == InterventionType.MODIFY:
            # Replan with feedback — never retry identical plan
            return await self._replan(
                reason=intervention.reason,
                feedback=feedback or intervention.feedback,
                constraints=intervention.constraints,
            )

        if intervention.intervention_type == InterventionType.PAUSE:
            if intervention.agent_id:
                self._lifecycle.pause(
                    intervention.agent_id,
                    intervention.reason or "Operator pause",
                )
            return {
                "action": "pause",
                "agent_id": intervention.agent_id,
            }

        if intervention.intervention_type == InterventionType.RESUME:
            if intervention.agent_id:
                self._lifecycle.resume(intervention.agent_id)
            return {
                "action": "resume",
                "agent_id": intervention.agent_id,
            }

        return {"action": "unknown", "type": intervention.intervention_type.value}

    async def _replan(
        self,
        reason: str,
        feedback: Optional[str] = None,
        constraints: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Replan with operator feedback.

        Never retries an identical plan — the new plan must differ.
        """
        if not self._current_goal:
            return {"action": "replan", "error": "No active goal to replan"}

        # Kill all current agents
        self._lifecycle.kill_all(f"Replan: {reason}")

        # Build enriched context with feedback
        context = {
            "replan_reason": reason,
            "operator_feedback": feedback,
            "constraints": constraints,
            "previous_results": {
                agent_id: {
                    "success": r.success,
                    "error": r.error_message,
                }
                for agent_id, r in self._results.items()
            },
        }

        self._status = "replanning"

        try:
            new_plan = await self._decompose(
                self._current_goal, context, self._current_quest_id,
            )

            # Check for identical plan
            new_hash = self._plan_hash(new_plan)
            if new_hash in self._plan_history:
                self._status = "idle"
                self._receipts.record_replan(
                    quest_id=self._current_quest_id,
                    original_plan_summary=f"Previous plan ({len(self._plan_history) - 1} revisions)",
                    new_plan_summary=f"Identical plan detected — {len(new_plan.subtasks)} subtasks",
                    trigger=reason,
                )
                return {
                    "action": "replan",
                    "error": "New plan is identical to a previous plan — aborting",
                    "aborted": True,
                }

            self._plan_history.append(new_hash)
            self._current_plan = new_plan

            self._receipts.record_replan(
                quest_id=self._current_quest_id,
                original_plan_summary=f"Plan revision {len(self._plan_history) - 1}",
                new_plan_summary=f"New plan: {len(new_plan.subtasks)} subtasks",
                trigger=reason,
            )

            # Execute new plan
            self._status = "executing"
            group_results = await self._execute_plan(
                new_plan, self._current_quest_id,
            )

            success = all(r.success for r in group_results)
            self._status = "idle"

            return {
                "action": "replan",
                "success": success,
                "new_subtask_count": len(new_plan.subtasks),
                "results": [
                    {
                        "agent_id": r.agent_id,
                        "success": r.success,
                        "error": r.error_message,
                    }
                    for r in group_results
                ],
            }

        except Exception as exc:
            self._status = "failed"
            return {
                "action": "replan",
                "error": str(exc),
            }

    # ── Status ───────────────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """Get current architect status for War Room display."""
        return {
            "status": self._status,
            "quest_id": self._current_quest_id,
            "goal": self._current_goal,
            "plan": {
                "subtask_count": len(self._current_plan.subtasks) if self._current_plan else 0,
                "execution_order": self._current_plan.execution_order if self._current_plan else [],
            },
            "results_count": len(self._results),
            "plan_revision_count": len(self._plan_history),
        }

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _plan_hash(plan: DecomposedTask) -> str:
        """Compute a hash of a plan for identity comparison."""
        import hashlib
        content = "|".join(
            f"{s.description}:{s.priority}:{s.control_method}"
            for s in plan.subtasks
        )
        content += "|" + str(plan.execution_order)
        return hashlib.sha256(content.encode()).hexdigest()[:16]
