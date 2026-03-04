"""Tests for HIVE Receipt Manager — typed receipt emission."""

import os
import tempfile
import pytest

from src.hive.receipt_manager import HiveReceiptManager
from src.hive.types import (
    AgentState,
    CollapseReason,
    ControlMethod,
    DecomposedTask,
    InterventionType,
    SubAgentRecord,
    TaskResult,
    TaskSpec,
)


@pytest.fixture
def receipt_mgr(tmp_path):
    """Create a receipt manager with a temporary data directory."""
    return HiveReceiptManager(data_dir=str(tmp_path))


@pytest.fixture(autouse=True)
def reset_receipt_service():
    """Reset the receipt service singleton between tests.

    Must reset BOTH 'src.shared.receipts' and bare 'receipts' modules
    because Docker PYTHONPATH makes them separate module objects.
    """
    import sys
    modules_to_reset = []
    for mod_name in ("src.shared.receipts", "receipts"):
        mod = sys.modules.get(mod_name)
        if mod and hasattr(mod, "_service_instance"):
            modules_to_reset.append((mod, mod._service_instance))
            mod._service_instance = None
    yield
    for mod, old_val in modules_to_reset:
        mod._service_instance = old_val


class TestTaskReceipts:
    def test_record_task_received(self, receipt_mgr):
        rid = receipt_mgr.record_task_received(
            goal="Test goal",
            quest_id="quest-1",
        )
        assert rid  # non-empty receipt ID

    def test_record_decomposition(self, receipt_mgr):
        dt = DecomposedTask(
            quest_id="quest-1",
            goal="Test goal",
            subtasks=[TaskSpec(), TaskSpec()],
            execution_order=[["t1", "t2"]],
        )
        rid = receipt_mgr.record_decomposition(dt)
        assert rid

    def test_record_task_completed(self, receipt_mgr):
        results = [
            TaskResult(task_id="t1", success=True),
            TaskResult(task_id="t2", success=False),
        ]
        rid = receipt_mgr.record_task_completed(
            quest_id="quest-1",
            results=results,
        )
        assert rid

    def test_record_task_failed(self, receipt_mgr):
        rid = receipt_mgr.record_task_failed(
            quest_id="quest-1",
            error="Decomposition failed",
        )
        assert rid


class TestAgentReceipts:
    def test_record_agent_spawned(self, receipt_mgr):
        record = SubAgentRecord(
            quest_id="quest-1",
            scoped_soul_hash="abc123",
        )
        rid = receipt_mgr.record_agent_spawned(record)
        assert rid

    def test_record_agent_state_transition(self, receipt_mgr):
        rid = receipt_mgr.record_agent_state_transition(
            agent_id="agent-1",
            from_state=AgentState.SPAWNING,
            to_state=AgentState.READY,
            quest_id="quest-1",
        )
        assert rid

    def test_record_agent_action(self, receipt_mgr):
        rid = receipt_mgr.record_agent_action(
            agent_id="agent-1",
            action_name="uab_click",
            action_inputs={"target": "button"},
            action_result={"success": True},
            quest_id="quest-1",
        )
        assert rid

    def test_record_agent_paused(self, receipt_mgr):
        rid = receipt_mgr.record_agent_paused(
            agent_id="agent-1",
            reason="Investigating behavior",
            quest_id="quest-1",
        )
        assert rid

    def test_record_agent_resumed(self, receipt_mgr):
        rid = receipt_mgr.record_agent_resumed(
            agent_id="agent-1",
            quest_id="quest-1",
        )
        assert rid

    def test_record_agent_collapsed(self, receipt_mgr):
        rid = receipt_mgr.record_agent_collapsed(
            agent_id="agent-1",
            reason=CollapseReason.COMPLETED,
            message="Task finished",
            quest_id="quest-1",
        )
        assert rid


class TestInterventionReceipts:
    def test_record_intervention(self, receipt_mgr):
        rid = receipt_mgr.record_intervention(
            intervention_type=InterventionType.PAUSE,
            agent_id="agent-1",
            reason="Need to check progress",
            quest_id="quest-1",
        )
        assert rid

    def test_record_replan(self, receipt_mgr):
        rid = receipt_mgr.record_replan(
            quest_id="quest-1",
            original_plan_summary="Do X then Y",
            new_plan_summary="Do Z instead",
            trigger="operator_modify",
        )
        assert rid

    def test_record_governance_check(self, receipt_mgr):
        rid = receipt_mgr.record_governance_check(
            agent_id="agent-1",
            capability="shell_exec",
            approved=False,
            tier="T3",
            quest_id="quest-1",
        )
        assert rid


class TestReceiptMetadata:
    def test_receipts_include_hive_subsystem(self, receipt_mgr):
        from src.shared.receipts import get_receipt_service
        rid = receipt_mgr.record_task_received(
            goal="Check metadata",
            quest_id="quest-meta",
        )
        service = get_receipt_service(receipt_mgr._data_dir)
        receipt = service.get(rid)
        assert receipt is not None
        assert receipt.metadata.get("hive_subsystem") == "task"

    def test_agent_receipts_include_agent_id(self, receipt_mgr):
        from src.shared.receipts import get_receipt_service
        record = SubAgentRecord(quest_id="quest-meta")
        rid = receipt_mgr.record_agent_spawned(record)
        service = get_receipt_service(receipt_mgr._data_dir)
        receipt = service.get(rid)
        assert receipt is not None
        assert receipt.metadata.get("hive_agent_id") == record.agent_id

    def test_quest_id_propagated(self, receipt_mgr):
        from src.shared.receipts import get_receipt_service
        rid = receipt_mgr.record_task_received(
            goal="Quest propagation test",
            quest_id="quest-prop",
        )
        service = get_receipt_service(receipt_mgr._data_dir)
        receipt = service.get(rid)
        assert receipt is not None
        assert receipt.quest_id == "quest-prop"

    def test_parent_id_chaining(self, receipt_mgr):
        from src.shared.receipts import get_receipt_service
        parent_rid = receipt_mgr.record_task_received(
            goal="Parent",
            quest_id="quest-chain",
        )
        child_rid = receipt_mgr.record_decomposition(
            DecomposedTask(quest_id="quest-chain", goal="Child"),
            parent_receipt_id=parent_rid,
        )
        service = get_receipt_service(receipt_mgr._data_dir)
        child = service.get(child_rid)
        assert child is not None
        assert child.parent_id == parent_rid


class TestQueryHelpers:
    def test_get_task_receipt_tree(self, receipt_mgr):
        quest_id = "quest-tree"
        receipt_mgr.record_task_received(goal="Test", quest_id=quest_id)
        receipt_mgr.record_task_completed(
            quest_id=quest_id,
            results=[TaskResult(success=True)],
        )
        tree = receipt_mgr.get_task_receipt_tree(quest_id)
        assert len(tree) == 2

    def test_get_interventions(self, receipt_mgr):
        quest_id = "quest-int"
        receipt_mgr.record_intervention(
            intervention_type=InterventionType.PAUSE,
            agent_id="a1",
            reason="Check",
            quest_id=quest_id,
        )
        interventions = receipt_mgr.get_interventions(quest_id)
        assert len(interventions) == 1
