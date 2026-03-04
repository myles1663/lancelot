"""Tests for HIVE Agent Lifecycle Manager."""

import time
import pytest

from src.hive.types import (
    AgentState,
    CollapseReason,
    InterventionType,
    OperatorIntervention,
    TaskSpec,
)
from src.hive.config import HiveConfig
from src.hive.registry import AgentRegistry
from src.hive.receipt_manager import HiveReceiptManager
from src.hive.scoped_soul import ScopedSoulGenerator
from src.hive.lifecycle import AgentLifecycleManager
from src.hive.errors import InterventionRequiresReasonError


@pytest.fixture
def config():
    return HiveConfig(max_concurrent_agents=5)


@pytest.fixture
def registry():
    return AgentRegistry(max_concurrent_agents=5)


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


@pytest.fixture
def lifecycle(config, registry, receipt_mgr):
    results = []
    def executor(action):
        results.append(action)
        return {"result": "ok"}
    mgr = AgentLifecycleManager(
        config=config,
        registry=registry,
        receipt_manager=receipt_mgr,
        soul_generator=ScopedSoulGenerator(),
        action_executor=executor,
    )
    yield mgr
    mgr.shutdown()


class TestSpawn:
    def test_spawn_creates_ready_agent(self, lifecycle, registry):
        spec = TaskSpec(description="Test spawn")
        record = lifecycle.spawn(spec, quest_id="q1")
        assert record.state == AgentState.READY
        assert record.quest_id == "q1"
        assert registry.active_count() == 1

    def test_spawn_multiple(self, lifecycle, registry):
        for i in range(3):
            lifecycle.spawn(TaskSpec(description=f"Task {i}"))
        assert registry.active_count() == 3


class TestExecute:
    def test_execute_runs_actions(self, lifecycle, registry):
        spec = TaskSpec(description="Test execute")
        record = lifecycle.spawn(spec)
        future = lifecycle.execute(
            record.agent_id,
            [{"action": "step1"}, {"action": "step2"}],
        )
        result = future.result(timeout=10)
        assert result.success is True
        assert result.action_count == 2

    def test_execute_collapses_on_completion(self, lifecycle, registry):
        spec = TaskSpec()
        record = lifecycle.spawn(spec)
        future = lifecycle.execute(record.agent_id, [{"action": "done"}])
        result = future.result(timeout=10)
        # Agent should be collapsed (in archive)
        found = registry.get(record.agent_id)
        assert found is not None
        assert found.state == AgentState.COLLAPSED


class TestKill:
    def test_kill_agent(self, lifecycle, registry):
        spec = TaskSpec()
        record = lifecycle.spawn(spec)
        future = lifecycle.execute(
            record.agent_id,
            [{"action": f"step{i}"} for i in range(100)],
        )
        time.sleep(0.05)
        lifecycle.kill(record.agent_id, "Test kill")
        result = future.result(timeout=10)
        # Should have been killed before completing all 100 actions

    def test_kill_requires_reason(self, lifecycle, registry):
        spec = TaskSpec()
        record = lifecycle.spawn(spec)
        with pytest.raises(InterventionRequiresReasonError):
            lifecycle.kill(record.agent_id, "")


class TestKillAll:
    def test_kill_all(self, lifecycle, registry):
        for _ in range(3):
            lifecycle.spawn(TaskSpec())
        collapsed = lifecycle.kill_all("Emergency")
        assert len(collapsed) >= 3
        assert registry.active_count() == 0

    def test_kill_all_requires_reason(self, lifecycle):
        with pytest.raises(InterventionRequiresReasonError):
            lifecycle.kill_all("")


class TestPauseResume:
    def test_pause_requires_reason(self, lifecycle, registry):
        spec = TaskSpec()
        record = lifecycle.spawn(spec)
        lifecycle.execute(record.agent_id, [{"action": f"s{i}"} for i in range(100)])
        time.sleep(0.05)
        with pytest.raises(InterventionRequiresReasonError):
            lifecycle.pause(record.agent_id, "")


class TestIntervene:
    def test_intervene_pause(self, config, registry, receipt_mgr):
        def slow_executor(action):
            time.sleep(0.02)
            return {"result": "ok"}
        mgr = AgentLifecycleManager(
            config=config,
            registry=registry,
            receipt_manager=receipt_mgr,
            soul_generator=ScopedSoulGenerator(),
            action_executor=slow_executor,
        )
        spec = TaskSpec()
        record = mgr.spawn(spec)
        mgr.execute(record.agent_id, [{"action": f"s{i}"} for i in range(100)])
        time.sleep(0.05)
        intervention = OperatorIntervention(
            intervention_type=InterventionType.PAUSE,
            agent_id=record.agent_id,
            reason="Check progress",
        )
        mgr.intervene(record.agent_id, intervention)
        mgr.shutdown()

    def test_intervene_requires_reason(self, lifecycle, registry):
        spec = TaskSpec()
        record = lifecycle.spawn(spec)
        intervention = OperatorIntervention(
            intervention_type=InterventionType.KILL,
            agent_id=record.agent_id,
            reason="",
        )
        with pytest.raises(InterventionRequiresReasonError):
            lifecycle.intervene(record.agent_id, intervention)


class TestCapacityEnforcement:
    def test_capacity_enforced_via_registry(self, lifecycle, registry):
        for _ in range(5):
            lifecycle.spawn(TaskSpec())
        from src.hive.errors import MaxAgentsExceededError
        with pytest.raises(MaxAgentsExceededError):
            lifecycle.spawn(TaskSpec())
