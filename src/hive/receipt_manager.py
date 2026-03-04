"""
HIVE Receipt Manager — typed methods for all HIVE receipt types.

Wraps emit_hive_receipt() with specific methods for each event type,
ensuring consistent metadata and receipt chaining.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.hive.receipts import emit_hive_receipt
from src.hive.types import (
    AgentState,
    CollapseReason,
    DecomposedTask,
    InterventionType,
    SubAgentRecord,
    TaskResult,
    TaskSpec,
)

logger = logging.getLogger(__name__)


class HiveReceiptManager:
    """Typed receipt emission for all HIVE events.

    Every HIVE action is receipt-traced through this manager.
    Receipts use quest_id for task grouping and parent_id for hierarchy.
    """

    def __init__(self, data_dir: str = "/home/lancelot/data"):
        self._data_dir = data_dir

    # ── Task Events ──────────────────────────────────────────────────

    def record_task_received(
        self,
        goal: str,
        quest_id: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Record that a new task was received. Returns receipt ID."""
        receipt = emit_hive_receipt(
            event_type="task",
            action_name="task_received",
            inputs={"goal": goal, "context": context or {}},
            quest_id=quest_id,
            data_dir=self._data_dir,
        )
        return receipt.id

    def record_decomposition(
        self,
        decomposed: DecomposedTask,
        parent_receipt_id: Optional[str] = None,
    ) -> str:
        """Record task decomposition result."""
        receipt = emit_hive_receipt(
            event_type="task",
            action_name="task_decomposed",
            inputs={
                "goal": decomposed.goal,
                "subtask_count": decomposed.total_subtasks,
                "execution_order": decomposed.execution_order,
            },
            parent_id=parent_receipt_id,
            quest_id=decomposed.quest_id,
            metadata={"subtask_ids": [s.task_id for s in decomposed.subtasks]},
            data_dir=self._data_dir,
        )
        return receipt.id

    def record_task_completed(
        self,
        quest_id: str,
        results: List[TaskResult],
        parent_receipt_id: Optional[str] = None,
    ) -> str:
        """Record task completion with aggregated results."""
        success_count = sum(1 for r in results if r.success)
        receipt = emit_hive_receipt(
            event_type="task",
            action_name="task_completed",
            inputs={
                "total_agents": len(results),
                "succeeded": success_count,
                "failed": len(results) - success_count,
            },
            parent_id=parent_receipt_id,
            quest_id=quest_id,
            data_dir=self._data_dir,
        )
        return receipt.id

    def record_task_failed(
        self,
        quest_id: str,
        error: str,
        parent_receipt_id: Optional[str] = None,
    ) -> str:
        """Record task failure."""
        receipt = emit_hive_receipt(
            event_type="task",
            action_name="task_failed",
            inputs={"error": error},
            parent_id=parent_receipt_id,
            quest_id=quest_id,
            data_dir=self._data_dir,
        )
        return receipt.id

    # ── Agent Events ─────────────────────────────────────────────────

    def record_agent_spawned(
        self,
        record: SubAgentRecord,
        parent_receipt_id: Optional[str] = None,
    ) -> str:
        """Record agent spawn."""
        receipt = emit_hive_receipt(
            event_type="agent",
            action_name="agent_spawned",
            inputs={
                "agent_id": record.agent_id,
                "task_id": record.task_spec.task_id,
                "control_method": record.task_spec.control_method.value,
                "scoped_soul_hash": record.scoped_soul_hash,
            },
            parent_id=parent_receipt_id,
            quest_id=record.quest_id,
            metadata={"hive_agent_id": record.agent_id},
            data_dir=self._data_dir,
        )
        return receipt.id

    def record_agent_state_transition(
        self,
        agent_id: str,
        from_state: AgentState,
        to_state: AgentState,
        quest_id: Optional[str] = None,
        parent_receipt_id: Optional[str] = None,
    ) -> str:
        """Record agent state transition."""
        receipt = emit_hive_receipt(
            event_type="agent",
            action_name="agent_state_transition",
            inputs={
                "agent_id": agent_id,
                "from_state": from_state.value,
                "to_state": to_state.value,
            },
            parent_id=parent_receipt_id,
            quest_id=quest_id,
            metadata={"hive_agent_id": agent_id},
            data_dir=self._data_dir,
        )
        return receipt.id

    def record_agent_action(
        self,
        agent_id: str,
        action_name: str,
        action_inputs: Dict[str, Any],
        action_result: Optional[Dict[str, Any]] = None,
        quest_id: Optional[str] = None,
        parent_receipt_id: Optional[str] = None,
    ) -> str:
        """Record a single agent action (UAB call, LLM call, etc.)."""
        receipt = emit_hive_receipt(
            event_type="agent",
            action_name=f"agent_action:{action_name}",
            inputs={
                "agent_id": agent_id,
                "action": action_name,
                "action_inputs": action_inputs,
                "action_result": action_result,
            },
            parent_id=parent_receipt_id,
            quest_id=quest_id,
            metadata={"hive_agent_id": agent_id},
            data_dir=self._data_dir,
        )
        return receipt.id

    def record_agent_paused(
        self,
        agent_id: str,
        reason: str,
        quest_id: Optional[str] = None,
        parent_receipt_id: Optional[str] = None,
    ) -> str:
        """Record agent pause."""
        receipt = emit_hive_receipt(
            event_type="agent",
            action_name="agent_paused",
            inputs={"agent_id": agent_id, "reason": reason},
            parent_id=parent_receipt_id,
            quest_id=quest_id,
            metadata={"hive_agent_id": agent_id},
            data_dir=self._data_dir,
        )
        return receipt.id

    def record_agent_resumed(
        self,
        agent_id: str,
        quest_id: Optional[str] = None,
        parent_receipt_id: Optional[str] = None,
    ) -> str:
        """Record agent resume."""
        receipt = emit_hive_receipt(
            event_type="agent",
            action_name="agent_resumed",
            inputs={"agent_id": agent_id},
            parent_id=parent_receipt_id,
            quest_id=quest_id,
            metadata={"hive_agent_id": agent_id},
            data_dir=self._data_dir,
        )
        return receipt.id

    def record_agent_collapsed(
        self,
        agent_id: str,
        reason: CollapseReason,
        message: Optional[str] = None,
        quest_id: Optional[str] = None,
        parent_receipt_id: Optional[str] = None,
    ) -> str:
        """Record agent collapse."""
        receipt = emit_hive_receipt(
            event_type="agent",
            action_name="agent_collapsed",
            inputs={
                "agent_id": agent_id,
                "collapse_reason": reason.value,
                "collapse_message": message,
            },
            parent_id=parent_receipt_id,
            quest_id=quest_id,
            metadata={"hive_agent_id": agent_id},
            data_dir=self._data_dir,
        )
        return receipt.id

    # ── Intervention Events ──────────────────────────────────────────

    def record_intervention(
        self,
        intervention_type: InterventionType,
        agent_id: Optional[str] = None,
        reason: str = "",
        feedback: Optional[str] = None,
        quest_id: Optional[str] = None,
        parent_receipt_id: Optional[str] = None,
    ) -> str:
        """Record an operator intervention."""
        receipt = emit_hive_receipt(
            event_type="intervention",
            action_name=f"intervention:{intervention_type.value}",
            inputs={
                "intervention_type": intervention_type.value,
                "agent_id": agent_id,
                "reason": reason,
                "feedback": feedback,
            },
            parent_id=parent_receipt_id,
            quest_id=quest_id,
            data_dir=self._data_dir,
        )
        return receipt.id

    def record_replan(
        self,
        quest_id: str,
        original_plan_summary: str,
        new_plan_summary: str,
        trigger: str,
        parent_receipt_id: Optional[str] = None,
    ) -> str:
        """Record a replan event (new plan after failure or intervention)."""
        receipt = emit_hive_receipt(
            event_type="task",
            action_name="task_replanned",
            inputs={
                "original_plan": original_plan_summary,
                "new_plan": new_plan_summary,
                "trigger": trigger,
            },
            parent_id=parent_receipt_id,
            quest_id=quest_id,
            data_dir=self._data_dir,
        )
        return receipt.id

    def record_governance_check(
        self,
        agent_id: str,
        capability: str,
        approved: bool,
        tier: Optional[str] = None,
        quest_id: Optional[str] = None,
        parent_receipt_id: Optional[str] = None,
    ) -> str:
        """Record a governance check result."""
        receipt = emit_hive_receipt(
            event_type="agent",
            action_name="governance_check",
            inputs={
                "agent_id": agent_id,
                "capability": capability,
                "approved": approved,
                "tier": tier,
            },
            parent_id=parent_receipt_id,
            quest_id=quest_id,
            metadata={"hive_agent_id": agent_id},
            data_dir=self._data_dir,
        )
        return receipt.id

    # ── Query Helpers ────────────────────────────────────────────────

    def get_task_receipt_tree(self, quest_id: str) -> List:
        """Get all receipts for a HIVE quest."""
        try:
            from receipts import get_receipt_service
        except ImportError:
            from src.shared.receipts import get_receipt_service
        service = get_receipt_service(self._data_dir)
        return service.get_quest_receipts(quest_id)

    def get_agent_receipt_chain(self, agent_id: str) -> List:
        """Get all receipts for a specific agent."""
        try:
            from receipts import get_receipt_service
        except ImportError:
            from src.shared.receipts import get_receipt_service
        service = get_receipt_service(self._data_dir)
        receipts = service.search(
            query=agent_id,
            action_types=[
                "hive_agent_event",
                "hive_task_event",
                "hive_intervention_event",
            ],
        )
        return receipts

    def get_interventions(self, quest_id: Optional[str] = None) -> List:
        """Get intervention receipts, optionally filtered by quest."""
        try:
            from receipts import get_receipt_service
        except ImportError:
            from src.shared.receipts import get_receipt_service
        service = get_receipt_service(self._data_dir)
        return service.list(
            action_type="hive_intervention_event",
            quest_id=quest_id,
        )
