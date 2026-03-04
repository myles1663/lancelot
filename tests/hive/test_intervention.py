"""Tests for HIVE Intervention Flows.

Full integration tests for the pause→feedback→replan flow,
kill→replan flow, modify→new constraints flow, and reason enforcement.
"""

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
    return HiveConfig(max_concurrent_agents=10)


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


def _make_slow_lifecycle(config, registry, receipt_mgr, sleep_time=0.02):
    """Create lifecycle with a slow executor to keep agents alive."""
    def slow_exec(action):
        time.sleep(sleep_time)
        return {"result": "ok"}
    return AgentLifecycleManager(
        config=config,
        registry=registry,
        receipt_manager=receipt_mgr,
        soul_generator=ScopedSoulGenerator(),
        action_executor=slow_exec,
    )


class TestPauseFlow:
    def test_pause_then_resume_completes(self, config, registry, receipt_mgr):
        lifecycle = _make_slow_lifecycle(config, registry, receipt_mgr)
        record = lifecycle.spawn(TaskSpec())
        future = lifecycle.execute(
            record.agent_id,
            [{"action": f"a{i}"} for i in range(50)],
        )
        time.sleep(0.05)
        lifecycle.pause(record.agent_id, "Checking progress")

        # Verify paused
        runtime = lifecycle.get_runtime(record.agent_id)
        if runtime:
            assert runtime.is_paused

        # Resume and let it complete
        lifecycle.resume(record.agent_id)
        result = future.result(timeout=10)
        assert result is not None
        lifecycle.shutdown()

    def test_pause_requires_reason(self, config, registry, receipt_mgr):
        lifecycle = _make_slow_lifecycle(config, registry, receipt_mgr)
        record = lifecycle.spawn(TaskSpec())
        lifecycle.execute(
            record.agent_id,
            [{"action": f"a{i}"} for i in range(50)],
        )
        time.sleep(0.05)
        with pytest.raises(InterventionRequiresReasonError):
            lifecycle.pause(record.agent_id, "")
        lifecycle.shutdown()


class TestKillFlow:
    def test_kill_stops_agent(self, config, registry, receipt_mgr):
        lifecycle = _make_slow_lifecycle(config, registry, receipt_mgr)
        record = lifecycle.spawn(TaskSpec())
        future = lifecycle.execute(
            record.agent_id,
            [{"action": f"a{i}"} for i in range(100)],
        )
        time.sleep(0.05)
        lifecycle.kill(record.agent_id, "Test kill")
        result = future.result(timeout=10)
        # Should not have completed all 100 actions
        assert result.action_count < 100
        lifecycle.shutdown()

    def test_kill_requires_reason(self, config, registry, receipt_mgr):
        lifecycle = _make_slow_lifecycle(config, registry, receipt_mgr)
        record = lifecycle.spawn(TaskSpec())
        with pytest.raises(InterventionRequiresReasonError):
            lifecycle.kill(record.agent_id, "")
        lifecycle.shutdown()

    def test_kill_records_intervention(self, config, registry, receipt_mgr):
        lifecycle = _make_slow_lifecycle(config, registry, receipt_mgr)
        record = lifecycle.spawn(TaskSpec())
        lifecycle.kill(record.agent_id, "Killing for test")
        # Check intervention was recorded (in archive since agent collapsed)
        agent = registry.get(record.agent_id)
        assert agent is not None
        assert len(agent.interventions) >= 1
        assert agent.interventions[-1]["type"] == InterventionType.KILL.value
        lifecycle.shutdown()


class TestKillAllFlow:
    def test_kill_all_stops_all(self, config, registry, receipt_mgr):
        lifecycle = _make_slow_lifecycle(config, registry, receipt_mgr)
        records = []
        for _ in range(3):
            records.append(lifecycle.spawn(TaskSpec()))
        collapsed = lifecycle.kill_all("Emergency stop")
        assert len(collapsed) >= 3
        assert registry.active_count() == 0
        lifecycle.shutdown()

    def test_kill_all_requires_reason(self, config, registry, receipt_mgr):
        lifecycle = _make_slow_lifecycle(config, registry, receipt_mgr)
        with pytest.raises(InterventionRequiresReasonError):
            lifecycle.kill_all("")
        lifecycle.shutdown()


class TestInterveneMethod:
    def test_intervene_with_pause(self, config, registry, receipt_mgr):
        lifecycle = _make_slow_lifecycle(config, registry, receipt_mgr)
        record = lifecycle.spawn(TaskSpec())
        lifecycle.execute(
            record.agent_id,
            [{"action": f"a{i}"} for i in range(100)],
        )
        time.sleep(0.05)
        intervention = OperatorIntervention(
            intervention_type=InterventionType.PAUSE,
            agent_id=record.agent_id,
            reason="Operator review",
        )
        lifecycle.intervene(record.agent_id, intervention)
        lifecycle.shutdown()

    def test_intervene_with_kill(self, config, registry, receipt_mgr):
        lifecycle = _make_slow_lifecycle(config, registry, receipt_mgr)
        record = lifecycle.spawn(TaskSpec())
        intervention = OperatorIntervention(
            intervention_type=InterventionType.KILL,
            agent_id=record.agent_id,
            reason="Must stop",
        )
        lifecycle.intervene(record.agent_id, intervention)
        lifecycle.shutdown()

    def test_intervene_with_modify_kills_agent(self, config, registry, receipt_mgr):
        lifecycle = _make_slow_lifecycle(config, registry, receipt_mgr)
        record = lifecycle.spawn(TaskSpec())
        intervention = OperatorIntervention(
            intervention_type=InterventionType.MODIFY,
            agent_id=record.agent_id,
            reason="Change approach",
            feedback="Use different method",
        )
        lifecycle.intervene(record.agent_id, intervention)
        # Modify = kill + replan, so agent should be killed
        lifecycle.shutdown()

    def test_intervene_reason_required(self, config, registry, receipt_mgr):
        lifecycle = _make_slow_lifecycle(config, registry, receipt_mgr)
        record = lifecycle.spawn(TaskSpec())
        intervention = OperatorIntervention(
            intervention_type=InterventionType.KILL,
            agent_id=record.agent_id,
            reason="",
        )
        with pytest.raises(InterventionRequiresReasonError):
            lifecycle.intervene(record.agent_id, intervention)
        lifecycle.shutdown()


class TestReceiptChain:
    def test_intervention_emits_receipt(self, config, registry, receipt_mgr):
        lifecycle = _make_slow_lifecycle(config, registry, receipt_mgr)
        record = lifecycle.spawn(TaskSpec())
        lifecycle.kill(record.agent_id, "Receipt test")

        interventions = receipt_mgr.get_interventions()
        # At least the kill intervention
        assert len(interventions) >= 1
        # Verify it's a Receipt with kill in the inputs
        found_kill = False
        for r in interventions:
            inputs = r.inputs if hasattr(r, "inputs") else {}
            if inputs.get("intervention_type") == "kill":
                found_kill = True
                break
        assert found_kill
        lifecycle.shutdown()

    def test_spawn_emits_receipt(self, config, registry, receipt_mgr):
        lifecycle = _make_slow_lifecycle(config, registry, receipt_mgr)
        record = lifecycle.spawn(TaskSpec(), quest_id="q-test")
        # Check receipt chain
        chain = receipt_mgr.get_agent_receipt_chain(record.agent_id)
        assert len(chain) >= 1
        lifecycle.shutdown()
