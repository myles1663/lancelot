"""Integration tests for HIVE Agent Mesh.

End-to-end tests covering: submit → decompose → spawn → execute → collapse → receipts.
"""

import json
import pytest

from src.hive.types import (
    AgentState,
    CollapseReason,
    InterventionType,
    OperatorIntervention,
    TaskResult,
    TaskSpec,
)
from src.hive.config import HiveConfig
from src.hive.registry import AgentRegistry
from src.hive.receipt_manager import HiveReceiptManager
from src.hive.scoped_soul import ScopedSoulGenerator
from src.hive.lifecycle import AgentLifecycleManager
from src.hive.decomposer import TaskDecomposer
from src.hive.architect import ArchitectAgent


class MockRouterResult:
    def __init__(self, output):
        self.output = output
        self.data = None
        self.executed = True


class MockModelRouter:
    def __init__(self, response):
        self._response = response
        self.call_count = 0

    def route(self, task_type, text, **kwargs):
        self.call_count += 1
        return MockRouterResult(output=self._response)


def _decomposition_response(n=2):
    return json.dumps({
        "subtasks": [
            {"description": f"Step {i}", "priority": "normal", "control_method": "supervised"}
            for i in range(n)
        ],
        "execution_order": [[i] for i in range(n)],
        "rationale": "Integration test plan",
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
def action_log():
    return []


@pytest.fixture
def lifecycle(config, registry, receipt_mgr, action_log):
    def executor(action):
        action_log.append(action)
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


@pytest.mark.asyncio
class TestEndToEnd:
    async def test_full_task_lifecycle(self, config, lifecycle, receipt_mgr, action_log):
        """Submit → decompose → spawn → execute → collapse → receipts."""
        router = MockModelRouter(_decomposition_response(2))
        decomposer = TaskDecomposer(model_router=router)
        architect = ArchitectAgent(
            config=config,
            decomposer=decomposer,
            lifecycle=lifecycle,
            receipt_manager=receipt_mgr,
        )

        result = await architect.execute_task("Build a report")
        assert result["quest_id"] is not None
        assert result["success"] is True
        assert len(result["results"]) == 2
        assert all(r["success"] for r in result["results"])

        # Actions were executed
        assert len(action_log) == 2

        # Router was called once for decomposition
        assert router.call_count == 1

    async def test_task_with_failure(self, config, registry, receipt_mgr):
        """Test task where executor raises an error."""
        call_count = [0]
        def failing_executor(action):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("Executor failed")
            return {"result": "ok"}

        lifecycle = AgentLifecycleManager(
            config=config,
            registry=registry,
            receipt_manager=receipt_mgr,
            soul_generator=ScopedSoulGenerator(),
            action_executor=failing_executor,
        )

        router = MockModelRouter(_decomposition_response(3))
        decomposer = TaskDecomposer(model_router=router)
        architect = ArchitectAgent(
            config=config,
            decomposer=decomposer,
            lifecycle=lifecycle,
            receipt_manager=receipt_mgr,
        )

        result = await architect.execute_task("Risky task")
        # Some agents may fail, but the task completes
        assert result["quest_id"] is not None
        lifecycle.shutdown()

    async def test_receipt_tree_built(self, config, lifecycle, receipt_mgr):
        """Verify receipt chain is built during execution."""
        router = MockModelRouter(_decomposition_response(2))
        decomposer = TaskDecomposer(model_router=router)
        architect = ArchitectAgent(
            config=config,
            decomposer=decomposer,
            lifecycle=lifecycle,
            receipt_manager=receipt_mgr,
        )

        result = await architect.execute_task("Receipt check")
        quest_id = result["quest_id"]

        # Task receipt tree should have entries
        tree = receipt_mgr.get_task_receipt_tree(quest_id)
        assert len(tree) > 0

    async def test_kill_during_execution(self, config, registry, receipt_mgr):
        """Kill switch during active execution."""
        import time
        def slow_executor(action):
            time.sleep(0.05)
            return {"result": "ok"}

        lifecycle = AgentLifecycleManager(
            config=config,
            registry=registry,
            receipt_manager=receipt_mgr,
            soul_generator=ScopedSoulGenerator(),
            action_executor=slow_executor,
        )

        # Spawn and execute an agent with many actions
        record = lifecycle.spawn(TaskSpec())
        future = lifecycle.execute(
            record.agent_id,
            [{"action": f"slow{i}"} for i in range(100)],
        )
        time.sleep(0.1)

        # Kill it
        lifecycle.kill(record.agent_id, "Kill switch test")
        result = future.result(timeout=10)

        # Should have been killed before completing all actions
        assert result.action_count < 100
        lifecycle.shutdown()


@pytest.mark.asyncio
class TestCapacityIntegration:
    async def test_concurrent_agent_limit(self, config, lifecycle, receipt_mgr, registry):
        """Verify max_concurrent_agents is enforced end-to-end."""
        from src.hive.errors import MaxAgentsExceededError

        # Spawn up to capacity
        for _ in range(10):
            lifecycle.spawn(TaskSpec())

        # One more should fail
        with pytest.raises(MaxAgentsExceededError):
            lifecycle.spawn(TaskSpec())


@pytest.mark.asyncio
class TestInterventionIntegration:
    async def test_intervention_receipt_chain(self, config, lifecycle, receipt_mgr, registry):
        """Verify interventions produce receipts in correct order."""
        record = lifecycle.spawn(TaskSpec(), quest_id="q-int-test")
        lifecycle.kill(record.agent_id, "Test intervention chain")

        interventions = receipt_mgr.get_interventions(quest_id="q-int-test")
        assert len(interventions) >= 1
