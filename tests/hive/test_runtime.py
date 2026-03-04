"""Tests for HIVE Sub-Agent Runtime."""

import threading
import time
import pytest

from src.hive.types import (
    AgentState,
    CollapseReason,
    SubAgentRecord,
    TaskSpec,
)
from src.hive.registry import AgentRegistry
from src.hive.receipt_manager import HiveReceiptManager
from src.hive.runtime import SubAgentRuntime


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


def _make_runtime(registry, receipt_mgr, action_executor=None, timeout=300, max_actions=50):
    spec = TaskSpec(timeout_seconds=timeout, max_actions=max_actions)
    record = registry.register(spec)
    registry.transition(record.agent_id, AgentState.READY)
    registry.transition(record.agent_id, AgentState.EXECUTING)
    runtime = SubAgentRuntime(
        agent_record=record,
        registry=registry,
        receipt_manager=receipt_mgr,
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
