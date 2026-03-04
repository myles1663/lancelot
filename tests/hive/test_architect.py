"""Tests for HIVE Architect Agent."""

import json
import pytest

from src.hive.types import (
    CollapseReason,
    ControlMethod,
    DecomposedTask,
    InterventionType,
    OperatorIntervention,
    TaskPriority,
    TaskResult,
    TaskSpec,
)
from src.hive.config import HiveConfig
from src.hive.decomposer import TaskDecomposer
from src.hive.registry import AgentRegistry
from src.hive.receipt_manager import HiveReceiptManager
from src.hive.scoped_soul import ScopedSoulGenerator
from src.hive.lifecycle import AgentLifecycleManager
from src.hive.architect import ArchitectAgent


class MockRouterResult:
    def __init__(self, output=None):
        self.output = output
        self.data = None
        self.executed = True


class MockModelRouter:
    def __init__(self, response=None):
        self._response = response
        self.calls = []

    def route(self, task_type, text, **kwargs):
        self.calls.append({"task_type": task_type, "text": text})
        return MockRouterResult(output=self._response)


def _make_decomposition_response(n_subtasks=2, execution_order=None):
    subtasks = [
        {
            "description": f"Subtask {i}",
            "priority": "normal",
            "control_method": "supervised",
            "execution_group": i,
        }
        for i in range(n_subtasks)
    ]
    return json.dumps({
        "subtasks": subtasks,
        "execution_order": execution_order or [[i] for i in range(n_subtasks)],
        "rationale": "Test decomposition",
    })


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
def config():
    return HiveConfig(max_concurrent_agents=10)


@pytest.fixture
def registry():
    return AgentRegistry(max_concurrent_agents=10)


@pytest.fixture
def receipt_mgr(tmp_path):
    return HiveReceiptManager(data_dir=str(tmp_path))


@pytest.fixture
def action_results():
    return []


@pytest.fixture
def executor(action_results):
    def _exec(action):
        action_results.append(action)
        return {"result": "ok"}
    return _exec


@pytest.fixture
def lifecycle(config, registry, receipt_mgr, executor):
    mgr = AgentLifecycleManager(
        config=config,
        registry=registry,
        receipt_manager=receipt_mgr,
        soul_generator=ScopedSoulGenerator(),
        action_executor=executor,
    )
    yield mgr
    mgr.shutdown()


def _make_architect(lifecycle, receipt_mgr, n_subtasks=2, execution_order=None, router=None):
    """Helper to build an ArchitectAgent with mock decomposer."""
    response = _make_decomposition_response(n_subtasks, execution_order)
    router = router or MockModelRouter(response=response)
    decomposer = TaskDecomposer(model_router=router)

    config = HiveConfig(max_concurrent_agents=10)
    architect = ArchitectAgent(
        config=config,
        decomposer=decomposer,
        lifecycle=lifecycle,
        receipt_manager=receipt_mgr,
    )
    return architect, router


@pytest.mark.asyncio
class TestExecuteTask:
    async def test_basic_execution(self, lifecycle, receipt_mgr):
        architect, _ = _make_architect(lifecycle, receipt_mgr, n_subtasks=2)
        result = await architect.execute_task("Do something")
        assert result["quest_id"] is not None
        assert result["success"] is True
        assert len(result["results"]) == 2

    async def test_quest_id_generated(self, lifecycle, receipt_mgr):
        architect, _ = _make_architect(lifecycle, receipt_mgr)
        result = await architect.execute_task("Test quest")
        assert len(result["quest_id"]) == 36  # UUID format

    async def test_sequential_group_execution(self, lifecycle, receipt_mgr):
        # 3 subtasks in sequential groups: [[0], [1], [2]]
        architect, _ = _make_architect(
            lifecycle, receipt_mgr,
            n_subtasks=3,
            execution_order=[[0], [1], [2]],
        )
        result = await architect.execute_task("Sequential task")
        assert result["success"] is True
        assert len(result["results"]) == 3

    async def test_parallel_group_execution(self, lifecycle, receipt_mgr):
        # 3 subtasks all in one group: [[0, 1, 2]]
        architect, _ = _make_architect(
            lifecycle, receipt_mgr,
            n_subtasks=3,
            execution_order=[[0, 1, 2]],
        )
        result = await architect.execute_task("Parallel task")
        assert result["success"] is True
        assert len(result["results"]) == 3

    async def test_plan_included_in_result(self, lifecycle, receipt_mgr):
        architect, _ = _make_architect(lifecycle, receipt_mgr, n_subtasks=2)
        result = await architect.execute_task("Plan check")
        assert result["plan"]["subtask_count"] == 2
        assert result["plan"]["execution_order"] == [["0"], ["1"]]

    async def test_decomposition_failure(self, lifecycle, receipt_mgr):
        router = MockModelRouter(response="not json")
        architect, _ = _make_architect(lifecycle, receipt_mgr, router=router)
        result = await architect.execute_task("Bad plan")
        assert result["success"] is False
        assert "error" in result


@pytest.mark.asyncio
class TestStatus:
    async def test_idle_status(self, lifecycle, receipt_mgr):
        architect, _ = _make_architect(lifecycle, receipt_mgr)
        status = architect.get_status()
        assert status["status"] == "idle"
        assert status["quest_id"] is None

    async def test_status_after_execution(self, lifecycle, receipt_mgr):
        architect, _ = _make_architect(lifecycle, receipt_mgr)
        await architect.execute_task("Status test")
        status = architect.get_status()
        assert status["status"] == "idle"
        assert status["quest_id"] is not None
        assert status["plan"]["subtask_count"] == 2
        assert status["results_count"] == 2


@pytest.mark.asyncio
class TestInterventionHandling:
    async def test_kill_all_intervention(self, lifecycle, receipt_mgr, registry):
        architect, _ = _make_architect(lifecycle, receipt_mgr)
        # Spawn some agents first
        for _ in range(3):
            lifecycle.spawn(TaskSpec())

        intervention = OperatorIntervention(
            intervention_type=InterventionType.KILL_ALL,
            reason="Emergency stop",
        )
        result = await architect.handle_intervention(intervention)
        assert result["action"] == "kill_all"
        assert registry.active_count() == 0

    async def test_kill_single_agent(self, lifecycle, receipt_mgr, registry):
        architect, _ = _make_architect(lifecycle, receipt_mgr)
        record = lifecycle.spawn(TaskSpec())

        intervention = OperatorIntervention(
            intervention_type=InterventionType.KILL,
            agent_id=record.agent_id,
            reason="Test kill",
        )
        result = await architect.handle_intervention(intervention)
        assert result["action"] == "kill"
        assert result["agent_id"] == record.agent_id

    async def test_pause_intervention(self, config, registry, receipt_mgr):
        import time
        def slow_exec(action):
            time.sleep(0.02)
            return {"result": "ok"}
        mgr = AgentLifecycleManager(
            config=config,
            registry=registry,
            receipt_manager=receipt_mgr,
            soul_generator=ScopedSoulGenerator(),
            action_executor=slow_exec,
        )
        architect, _ = _make_architect(mgr, receipt_mgr)
        record = mgr.spawn(TaskSpec())
        mgr.execute(record.agent_id, [{"action": f"s{i}"} for i in range(100)])
        time.sleep(0.05)

        intervention = OperatorIntervention(
            intervention_type=InterventionType.PAUSE,
            agent_id=record.agent_id,
            reason="Check progress",
        )
        result = await architect.handle_intervention(intervention)
        assert result["action"] == "pause"
        mgr.shutdown()

    async def test_resume_intervention(self, config, registry, receipt_mgr):
        import time
        def slow_exec(action):
            time.sleep(0.02)
            return {"result": "ok"}
        mgr = AgentLifecycleManager(
            config=config,
            registry=registry,
            receipt_manager=receipt_mgr,
            soul_generator=ScopedSoulGenerator(),
            action_executor=slow_exec,
        )
        architect, _ = _make_architect(mgr, receipt_mgr)
        record = mgr.spawn(TaskSpec())
        mgr.execute(record.agent_id, [{"action": f"s{i}"} for i in range(100)])
        time.sleep(0.05)
        mgr.pause(record.agent_id, "Pause first")

        intervention = OperatorIntervention(
            intervention_type=InterventionType.RESUME,
            agent_id=record.agent_id,
            reason="Continue",
        )
        result = await architect.handle_intervention(intervention)
        assert result["action"] == "resume"
        mgr.shutdown()


@pytest.mark.asyncio
class TestReplan:
    async def test_modify_triggers_replan(self, lifecycle, receipt_mgr):
        # First execute a task
        architect, router = _make_architect(lifecycle, receipt_mgr)
        await architect.execute_task("Original task")

        # Now modify — the router will return the SAME plan, so it should abort
        intervention = OperatorIntervention(
            intervention_type=InterventionType.MODIFY,
            reason="Change approach",
            feedback="Use a different strategy",
        )
        result = await architect.handle_intervention(intervention)
        assert result["action"] == "replan"
        # Same plan hash → aborted
        assert result.get("aborted") is True

    async def test_never_retry_identical_plan(self, lifecycle, receipt_mgr):
        architect, _ = _make_architect(lifecycle, receipt_mgr)
        await architect.execute_task("Task")

        intervention = OperatorIntervention(
            intervention_type=InterventionType.MODIFY,
            reason="Retry",
        )
        result = await architect.handle_intervention(intervention)
        # The mock always returns the same plan, so it should detect identical
        assert result.get("aborted") is True
        assert "identical" in result.get("error", "").lower()

    async def test_plan_revision_count_tracked(self, lifecycle, receipt_mgr):
        architect, _ = _make_architect(lifecycle, receipt_mgr)
        await architect.execute_task("Track revisions")
        status = architect.get_status()
        assert status["plan_revision_count"] == 1  # Initial plan
