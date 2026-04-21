"""Tests for HIVE Sub-Agent Runtime."""

import sys
import threading
import time
import pytest

from src.core.soul.store import AutonomyPosture, Soul
from src.hive.integration.governance_bridge import GovernanceBridge, GovernanceResult
from src.hive.types import (
    AgentState,
    CollapseReason,
    SubAgentRecord,
    TaskSpec,
)
from src.hive.registry import AgentRegistry
from src.hive.receipt_manager import HiveReceiptManager
from src.hive.runtime import SubAgentRuntime
from src.hive.scoped_soul import ScopedCapabilityBoundary


@pytest.fixture
def registry():
    return AgentRegistry(max_concurrent_agents=10)


@pytest.fixture
def receipt_mgr(tmp_path):
    return HiveReceiptManager(data_dir=str(tmp_path))


@pytest.fixture(autouse=True)
def reset_receipt_service():
    """Reset receipt service singleton in ALL module references."""
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


class _ApproveGovernance:
    def validate_action(self, **kwargs):
        return GovernanceResult(
            approved=True,
            tier="T0",
            reason="allowed",
        )


def _make_runtime(
    registry,
    receipt_mgr,
    action_executor=None,
    governance=None,
    timeout=300,
    max_actions=50,
):
    spec = TaskSpec(timeout_seconds=timeout, max_actions=max_actions)
    record = registry.register(spec)
    registry.transition(record.agent_id, AgentState.READY)
    registry.transition(record.agent_id, AgentState.EXECUTING)
    runtime = SubAgentRuntime(
        agent_record=record,
        registry=registry,
        receipt_manager=receipt_mgr,
        governance_bridge=governance,
        action_executor=action_executor,
    )
    return runtime, record


class TestExecutionLoop:
    def test_simple_execution(self, registry, receipt_mgr):
        results = []
        def executor(action):
            results.append(action)
            return {"done": True}

        runtime, record = _make_runtime(registry, receipt_mgr, executor)
        actions = [{"action": "step1"}, {"action": "step2"}]
        result = runtime.run(actions)
        assert result.success is True
        assert len(results) == 2

    def test_runtime_overwrites_injected_scope_fields_before_executor(self, registry, receipt_mgr):
        observed = []
        spec = TaskSpec(allowed_apps=["notepad"], allowed_categories=["query"])
        record = registry.register(spec)
        registry.transition(record.agent_id, AgentState.READY)
        registry.transition(record.agent_id, AgentState.EXECUTING)
        scoped_soul = Soul(
            version="v1",
            mission="Test",
            allegiance="Test",
            autonomy_posture=AutonomyPosture(
                level="scoped",
                description="Query only",
                allowed_autonomous=["uab_query", "uab_state"],
                requires_approval=[],
            ),
        )

        def executor(action):
            observed.append(action)
            return {"done": True}

        runtime = SubAgentRuntime(
            agent_record=record,
            registry=registry,
            receipt_manager=receipt_mgr,
            scoped_soul=scoped_soul,
            action_executor=executor,
        )

        result = runtime.run(
            [
                {
                    "action": "execute_subtask",
                    "agent_id": "spoofed-agent",
                    "scoped_soul": "spoofed-soul",
                    "allowed_apps": ["chrome"],
                    "allowed_categories": ["uab"],
                }
            ]
        )

        assert result.success is True
        assert len(observed) == 1
        executed = observed[0]
        assert executed["agent_id"] == record.agent_id
        assert executed["scoped_soul"] == ScopedCapabilityBoundary(
            allowed_autonomous=("uab_query", "uab_state"),
            requires_approval=(),
        )
        assert executed["scoped_soul_document"] is scoped_soul
        assert executed["allowed_apps"] == ["notepad"]
        assert executed["allowed_categories"] == ["query"]

    def test_runtime_uses_spawn_time_scope_boundary_not_later_raw_soul_mutation(self, registry, receipt_mgr):
        observed = []
        spec = TaskSpec(allowed_apps=["notepad"], allowed_categories=["uab"])
        record = registry.register(spec)
        registry.transition(record.agent_id, AgentState.READY)
        registry.transition(record.agent_id, AgentState.EXECUTING)
        scoped_soul = Soul(
            version="v1",
            mission="Test",
            allegiance="Test",
            autonomy_posture=AutonomyPosture(
                level="scoped",
                description="Query only",
                allowed_autonomous=["uab_query", "uab_state"],
                requires_approval=[],
            ),
        )

        def executor(action):
            observed.append(action["scoped_soul"])
            return {"done": True}

        runtime = SubAgentRuntime(
            agent_record=record,
            registry=registry,
            receipt_manager=receipt_mgr,
            scoped_soul=scoped_soul,
            action_executor=executor,
        )

        scoped_soul.autonomy_posture.allowed_autonomous.append("uab_click")

        result = runtime.run([{"action": "state", "capability": "uab_click"}])

        assert result.success is False
        assert result.collapse_reason == CollapseReason.SOUL_VIOLATION
        assert observed == []

    def test_empty_actions(self, registry, receipt_mgr):
        runtime, record = _make_runtime(registry, receipt_mgr)
        result = runtime.run([])
        assert result.success is True
        assert result.action_count == 0

    def test_executor_exception_collapses(self, registry, receipt_mgr):
        def bad_executor(action):
            raise RuntimeError("Boom!")

        runtime, record = _make_runtime(registry, receipt_mgr, bad_executor)
        result = runtime.run([{"action": "fail"}])
        assert result.success is False
        assert result.error_message == "Boom!"
        assert result.collapse_reason == CollapseReason.ERROR

    def test_completed_marker_does_not_mask_runtime_error(self, registry, receipt_mgr, monkeypatch):
        runtime, record = _make_runtime(registry, receipt_mgr)
        runtime._collapse_reason = CollapseReason.COMPLETED

        def fail_wait(timeout=None):
            raise RuntimeError("pause boom")

        monkeypatch.setattr(runtime, "_wait_for_unpause", fail_wait)

        result = runtime.run([{"action": "step1"}])

        assert result.success is False
        assert result.error_message == "pause boom"

    def test_action_receipt_uses_parent_chain(self, registry, receipt_mgr):
        def executor(action):
            return {"done": True}

        runtime, record = _make_runtime(registry, receipt_mgr, executor)
        record.quest_id = "quest-parent-chain"
        record.latest_receipt_id = "parent-receipt-1"
        result = runtime.run([{"action": "step1"}])
        assert result.success is True

        receipts = receipt_mgr.get_task_receipt_tree(record.quest_id)
        action_receipts = [r for r in receipts if r.action_name == "agent_action:step1"]
        assert len(action_receipts) == 1
        assert action_receipts[0].parent_id == "parent-receipt-1"

    def test_approved_governance_check_receipt_chains_to_action_receipt(self, registry, receipt_mgr):
        def executor(action):
            return {"done": True}

        runtime, record = _make_runtime(
            registry,
            receipt_mgr,
            action_executor=executor,
            governance=_ApproveGovernance(),
        )
        record.quest_id = "quest-governed-success"
        result = runtime.run([{"action": "step1", "capability": "governed_step"}])
        assert result.success is True

        receipts = receipt_mgr.get_task_receipt_tree(record.quest_id)
        governance_receipts = [r for r in receipts if r.action_name == "governance_check"]
        action_receipts = [r for r in receipts if r.action_name == "agent_action:step1"]

        assert len(governance_receipts) == 1
        assert governance_receipts[0].inputs["capability"] == "governed_step"
        assert governance_receipts[0].inputs["approved"] is True
        assert len(action_receipts) == 1
        assert action_receipts[0].parent_id == governance_receipts[0].id

    def test_scoped_soul_violation_before_governance_emits_action_receipt(self, registry, receipt_mgr):
        spec = TaskSpec(allowed_apps=["notepad"])
        record = registry.register(spec)
        registry.transition(record.agent_id, AgentState.READY)
        registry.transition(record.agent_id, AgentState.EXECUTING)
        runtime = SubAgentRuntime(
            agent_record=record,
            registry=registry,
            receipt_manager=receipt_mgr,
            action_executor=lambda action: {"done": True},
        )
        record.quest_id = "quest-scoped-soul-denied"

        result = runtime.run(
            [
                {
                    "action": "execute_subtask",
                    "capability": "uab_automation",
                    "context": {"target_app": "chrome"},
                }
            ]
        )

        assert result.success is False
        assert result.collapse_reason == CollapseReason.SOUL_VIOLATION

        receipts = receipt_mgr.get_task_receipt_tree(record.quest_id)
        action_receipts = [r for r in receipts if r.action_name == "agent_action:execute_subtask"]

        assert len(action_receipts) == 1
        assert action_receipts[0].inputs["action_result"]["type"] == "soul_violation"

    def test_exact_category_matching_blocks_fuzzy_substring_capabilities(self, registry, receipt_mgr):
        spec = TaskSpec(allowed_categories=["read"])
        record = registry.register(spec)
        registry.transition(record.agent_id, AgentState.READY)
        registry.transition(record.agent_id, AgentState.EXECUTING)
        runtime = SubAgentRuntime(
            agent_record=record,
            registry=registry,
            receipt_manager=receipt_mgr,
            action_executor=lambda action: {"done": True},
        )

        result = runtime.run(
            [
                {
                    "action": "thread_review",
                    "capability": "thread_review",
                    "context": {},
                }
            ]
        )

        assert result.success is False
        assert result.collapse_reason == CollapseReason.SOUL_VIOLATION
        assert "outside scoped categories" in (result.error_message or "")

    def test_runtime_collapses_when_hive_kill_switch_is_active(self, registry, receipt_mgr, monkeypatch):
        called = []

        def executor(action):
            called.append(action)
            return {"done": True}

        monkeypatch.setitem(
            sys.modules,
            "src.core.feature_flags",
            type("Flags", (), {"FEATURE_HIVE": False})(),
        )
        monkeypatch.delitem(sys.modules, "feature_flags", raising=False)

        governance = GovernanceBridge(
            risk_classifier=None,
            enforce_kill_switches=True,
        )
        runtime, record = _make_runtime(
            registry,
            receipt_mgr,
            action_executor=executor,
            governance=governance,
        )

        result = runtime.run([{"action": "step1", "capability": "classify_intent"}])

        assert result.success is False
        assert result.collapse_reason == CollapseReason.GOVERNANCE_DENIED
        assert called == []


class TestPauseResume:
    def test_pause_blocks_execution(self, registry, receipt_mgr):
        execution_order = []

        def slow_executor(action):
            execution_order.append(action["action"])
            return {}

        runtime, record = _make_runtime(registry, receipt_mgr, slow_executor)

        # Pause immediately
        runtime.pause("Test pause")
        assert runtime.is_paused

        # Run in background thread
        result_holder = [None]
        def run():
            result_holder[0] = runtime.run([{"action": "a1"}])

        t = threading.Thread(target=run)
        t.start()

        # Give it a moment to be blocked on pause
        time.sleep(0.1)
        assert len(execution_order) == 0  # Should still be paused

        # Resume
        runtime.resume()
        t.join(timeout=5)
        assert result_holder[0] is not None

    def test_resume_continues(self, registry, receipt_mgr):
        runtime, record = _make_runtime(registry, receipt_mgr)
        runtime.pause("Pause")
        assert runtime.is_paused
        runtime.resume()
        assert not runtime.is_paused


class TestCollapseSignal:
    def test_collapse_stops_execution(self, registry, receipt_mgr):
        call_count = [0]
        def executor(action):
            call_count[0] += 1
            return {}

        runtime, record = _make_runtime(registry, receipt_mgr, executor)
        runtime.request_collapse(CollapseReason.OPERATOR_KILL, "Test kill")

        result = runtime.run([{"action": "a1"}, {"action": "a2"}])
        assert call_count[0] == 0  # Should not execute any actions
        assert runtime.is_collapse_requested

    def test_collapse_unblocks_pause(self, registry, receipt_mgr):
        runtime, record = _make_runtime(registry, receipt_mgr)
        runtime.pause("Pause")

        def collapse_after_delay():
            time.sleep(0.1)
            runtime.request_collapse(CollapseReason.OPERATOR_KILL, "Kill")

        t = threading.Thread(target=collapse_after_delay)
        t.start()

        result = runtime.run([{"action": "a1"}])
        t.join(timeout=5)
        assert runtime.is_collapse_requested


class TestMaxActions:
    def test_max_actions_enforced(self, registry, receipt_mgr):
        call_count = [0]
        def executor(action):
            call_count[0] += 1
            return {}

        runtime, record = _make_runtime(
            registry, receipt_mgr, executor, max_actions=3,
        )
        actions = [{"action": f"a{i}"} for i in range(10)]
        result = runtime.run(actions)
        assert call_count[0] == 3
        assert result.collapse_reason == CollapseReason.MAX_ACTIONS_EXCEEDED


class TestTimeout:
    def test_timeout_collapses_agent(self, registry, receipt_mgr):
        def slow_executor(action):
            time.sleep(1.5)
            return {}

        runtime, record = _make_runtime(
            registry, receipt_mgr, slow_executor, timeout=1,
        )
        # Need multiple actions — timeout is checked BEFORE each action
        actions = [{"action": f"slow{i}"} for i in range(5)]
        result = runtime.run(actions)
        # After the first action (1.5s > 1s timeout), the second iteration detects timeout
        assert result.collapse_reason == CollapseReason.TIMEOUT
        assert result.action_count < 5  # Should not complete all actions
