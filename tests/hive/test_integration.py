"""Integration tests for HIVE Agent Mesh.

End-to-end tests covering: submit → decompose → spawn → execute → collapse → receipts.
"""

import json
from dataclasses import FrozenInstanceError, dataclass
import pytest

from src.core.soul.store import AutonomyPosture, Soul
from src.shared.receipts import get_receipt_service
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
from src.hive.integration.governance_bridge import GovernanceResult
from src.hive.integration.uab_executor import HiveUABExecutor


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


@dataclass
class _ConnectResult:
    success: bool = True
    error_message: str | None = None
    duration_ms: int = 1
    state_changes: dict | None = None


@dataclass
class _StateResult:
    window_title: str = "Notepad"
    focused: bool = True


class _MockUABProvider:
    def __init__(self):
        self.act_calls = []

    def connect(self, pid):
        return _ConnectResult()

    def state(self, pid):
        return _StateResult()

    def enumerate(self, pid):
        return [
            {
                "id": "edit1",
                "type": "edit",
                "label": "Editor",
                "actions": ["click", "type"],
            }
        ]

    def act(self, pid, element_id, action, params):
        self.act_calls.append((pid, element_id, action, params))
        return _ConnectResult(state_changes={})


class _AuditedGovernance:
    def __init__(self, deny_capabilities=None):
        self._deny = {cap.lower() for cap in (deny_capabilities or set())}
        self.validations = []
        self.updates = []

    def validate_action(self, **kwargs):
        self.validations.append(dict(kwargs))
        capability = str(kwargs.get("capability", "")).lower()
        if capability in self._deny:
            return GovernanceResult(
                approved=False,
                tier="T3",
                reason=f"Denied by test for {capability}",
                requires_operator_approval=False,
            )
        return GovernanceResult(
            approved=True,
            tier="T0",
            reason="allowed",
        )

    def update_trust(self, capability, scope, success):
        self.updates.append((capability, scope, success))


def _make_parent_soul_with_uab_capabilities(*capabilities: str) -> Soul:
    return Soul(
        version="v1",
        mission="Support software reliably",
        allegiance="Customer and operator trust",
        autonomy_posture=AutonomyPosture(
            level="supervised",
            description="test",
            allowed_autonomous=list(capabilities),
            requires_approval=[],
        ),
    )


def _make_uab_lifecycle(config, registry, receipt_mgr, governance, parent_soul=None):
    provider = _MockUABProvider()
    executor = HiveUABExecutor(
        uab_provider=provider,
        governance_bridge=governance,
    )
    lifecycle = AgentLifecycleManager(
        config=config,
        registry=registry,
        receipt_manager=receipt_mgr,
        soul_generator=ScopedSoulGenerator(),
        governance_bridge=governance,
        parent_soul=parent_soul,
        action_executor=executor,
    )
    return lifecycle, provider


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
        lifecycle.kill(record.agent_id, "Kill switch test", operator_id="test-op", session_id="test-sess")
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
        lifecycle.kill(record.agent_id, "Test intervention chain", operator_id="test-op", session_id="test-sess")

        interventions = receipt_mgr.get_interventions(quest_id="q-int-test")
        assert len(interventions) >= 1


@pytest.mark.asyncio
class TestGovernedUABIntegration:
    async def test_governed_uab_execution_emits_receipts_and_updates_trust(
        self,
        config,
        registry,
        receipt_mgr,
    ):
        governance = _AuditedGovernance()
        lifecycle, provider = _make_uab_lifecycle(
            config,
            registry,
            receipt_mgr,
            governance,
            parent_soul=_make_parent_soul_with_uab_capabilities(
                "uab_automation",
                "uab_click",
                "uab_type",
                "uab_query",
                "uab_state",
            ),
        )

        try:
            record = lifecycle.spawn(
                TaskSpec(
                    allowed_apps=["notepad"],
                    allowed_categories=["uab"],
                ),
                quest_id="quest-uab-success",
            )
            future = lifecycle.execute(
                record.agent_id,
                [
                    {
                        "action": "execute_subtask",
                        "capability": "uab_automation",
                        "spec": "type 'hello world'",
                        "context": {"target_pid": 202, "target_app": "notepad"},
                    }
                ],
            )
            result = future.result(timeout=5)
        finally:
            lifecycle.shutdown()

        assert result.success is True
        assert provider.act_calls == [
            (202, "edit1", "click", {}),
            (202, "edit1", "type", {"text": "hello world"}),
        ]
        assert [call["capability"] for call in governance.validations] == [
            "uab_automation",
            "uab_click",
            "uab_type",
        ]
        assert governance.updates == [
            ("uab_click", "notepad", True),
            ("uab_type", "notepad", True),
        ]

        receipts = receipt_mgr.get_task_receipt_tree("quest-uab-success")
        service = get_receipt_service(receipt_mgr._data_dir)
        governance_receipt = next(r for r in receipts if r.action_name == "governance_check")
        action_receipt = next(r for r in receipts if r.action_name == "agent_action:execute_subtask")

        assert governance_receipt.inputs["capability"] == "uab_automation"
        assert governance_receipt.inputs["approved"] is True
        assert action_receipt.parent_id == governance_receipt.id
        assert service.validate_parent_chain(quest_id="quest-uab-success") == []

    async def test_parent_soul_without_mutating_uab_capability_blocks_step_execution(
        self,
        config,
        registry,
        receipt_mgr,
    ):
        governance = _AuditedGovernance()
        lifecycle, provider = _make_uab_lifecycle(
            config,
            registry,
            receipt_mgr,
            governance,
            parent_soul=_make_parent_soul_with_uab_capabilities(
                "uab_automation",
                "uab_query",
                "uab_state",
            ),
        )

        try:
            record = lifecycle.spawn(
                TaskSpec(
                    allowed_apps=["notepad"],
                    allowed_categories=["uab"],
                ),
                quest_id="quest-uab-parent-soul-denied",
            )
            future = lifecycle.execute(
                record.agent_id,
                [
                    {
                        "action": "execute_subtask",
                        "capability": "uab_automation",
                        "spec": "type 'hello world'",
                        "context": {"target_pid": 202, "target_app": "notepad"},
                    }
                ],
            )
            result = future.result(timeout=5)
        finally:
            lifecycle.shutdown()

        assert result.success is False
        assert result.collapse_reason == CollapseReason.SOUL_VIOLATION
        assert provider.act_calls == []
        assert [call["capability"] for call in governance.validations] == ["uab_automation"]
        assert governance.updates == []

        receipts = receipt_mgr.get_task_receipt_tree("quest-uab-parent-soul-denied")
        action_receipt = next(r for r in receipts if r.action_name == "agent_action:execute_subtask")
        assert "does not permit UAB capability 'uab_click'" in (
            action_receipt.inputs["action_result"]["error"]
        )

    async def test_governance_denial_blocks_uab_execution_before_mutation(
        self,
        config,
        registry,
        receipt_mgr,
    ):
        governance = _AuditedGovernance(deny_capabilities={"uab_automation"})
        lifecycle, provider = _make_uab_lifecycle(config, registry, receipt_mgr, governance)

        try:
            record = lifecycle.spawn(
                TaskSpec(
                    allowed_apps=["notepad"],
                    allowed_categories=["uab"],
                ),
                quest_id="quest-uab-denied",
            )
            future = lifecycle.execute(
                record.agent_id,
                [
                    {
                        "action": "execute_subtask",
                        "capability": "uab_automation",
                        "spec": "type 'hello world'",
                        "context": {"target_pid": 202, "target_app": "notepad"},
                    }
                ],
            )
            result = future.result(timeout=5)
        finally:
            lifecycle.shutdown()

        assert result.success is False
        assert result.collapse_reason == CollapseReason.GOVERNANCE_DENIED
        assert provider.act_calls == []
        assert governance.updates == []

        receipts = receipt_mgr.get_task_receipt_tree("quest-uab-denied")
        service = get_receipt_service(receipt_mgr._data_dir)
        governance_receipt = next(r for r in receipts if r.action_name == "governance_check")

        assert governance_receipt.inputs["capability"] == "uab_automation"
        assert governance_receipt.inputs["approved"] is False
        assert not any(r.action_name == "agent_action:execute_subtask" for r in receipts)
        assert service.validate_parent_chain(quest_id="quest-uab-denied") == []

    async def test_restrictive_task_scope_blocks_disallowed_uab_app_before_governance(
        self,
        config,
        registry,
        receipt_mgr,
    ):
        governance = _AuditedGovernance()
        lifecycle, provider = _make_uab_lifecycle(config, registry, receipt_mgr, governance)

        try:
            record = lifecycle.spawn(
                TaskSpec(
                    allowed_apps=["notepad"],
                    allowed_categories=["uab"],
                ),
                quest_id="quest-uab-scoped-denied",
            )
            future = lifecycle.execute(
                record.agent_id,
                [
                    {
                        "action": "execute_subtask",
                        "capability": "uab_automation",
                        "spec": "type 'hello world'",
                        "context": {"target_pid": 202, "target_app": "chrome"},
                    }
                ],
            )
            result = future.result(timeout=5)
        finally:
            lifecycle.shutdown()

        assert result.success is False
        assert result.collapse_reason == CollapseReason.SOUL_VIOLATION
        assert provider.act_calls == []
        assert governance.validations == []

        receipts = receipt_mgr.get_task_receipt_tree("quest-uab-scoped-denied")
        service = get_receipt_service(receipt_mgr._data_dir)
        action_receipt = next(r for r in receipts if r.action_name == "agent_action:execute_subtask")

        assert action_receipt.inputs["action_result"]["type"] == "soul_violation"
        assert not any(r.action_name == "governance_check" for r in receipts)
        assert service.validate_parent_chain(quest_id="quest-uab-scoped-denied") == []

    async def test_injected_allowed_categories_cannot_widen_uab_execution_scope(
        self,
        config,
        registry,
        receipt_mgr,
    ):
        governance = _AuditedGovernance()
        lifecycle, provider = _make_uab_lifecycle(config, registry, receipt_mgr, governance)

        try:
            record = lifecycle.spawn(
                TaskSpec(
                    allowed_apps=["notepad"],
                    allowed_categories=["query"],
                ),
                quest_id="quest-uab-injected-category-widening",
            )
            future = lifecycle.execute(
                record.agent_id,
                [
                    {
                        "action": "execute_subtask",
                        "capability": "execute_subtask",
                        "spec": "type 'hello world'",
                        "context": {"target_pid": 202, "target_app": "notepad"},
                        "allowed_categories": ["uab"],
                    }
                ],
            )
            result = future.result(timeout=5)
        finally:
            lifecycle.shutdown()

        assert result.success is False
        assert result.collapse_reason == CollapseReason.SOUL_VIOLATION
        assert provider.act_calls == []
        assert [call["capability"] for call in governance.validations] == ["execute_subtask"]
        assert governance.updates == []

        receipts = receipt_mgr.get_task_receipt_tree("quest-uab-injected-category-widening")
        service = get_receipt_service(receipt_mgr._data_dir)
        action_receipt = next(r for r in receipts if r.action_name == "agent_action:execute_subtask")

        assert "outside scoped categories ['query']" in action_receipt.inputs["action_result"]["error"]
        assert service.validate_parent_chain(quest_id="quest-uab-injected-category-widening") == []

    async def test_injected_scoped_soul_cannot_widen_uab_execution_scope(
        self,
        config,
        registry,
        receipt_mgr,
    ):
        governance = _AuditedGovernance()
        lifecycle, provider = _make_uab_lifecycle(
            config,
            registry,
            receipt_mgr,
            governance,
            parent_soul=_make_parent_soul_with_uab_capabilities(
                "uab_automation",
                "uab_query",
                "uab_state",
            ),
        )
        injected_scoped_soul = {
            "allowed_autonomous": [
                "uab_automation",
                "uab_query",
                "uab_state",
                "uab_click",
                "uab_type",
            ]
        }

        try:
            record = lifecycle.spawn(
                TaskSpec(
                    allowed_apps=["notepad"],
                    allowed_categories=["uab"],
                ),
                quest_id="quest-uab-injected-soul-widening",
            )
            future = lifecycle.execute(
                record.agent_id,
                [
                    {
                        "action": "execute_subtask",
                        "capability": "uab_automation",
                        "spec": "type 'hello world'",
                        "context": {"target_pid": 202, "target_app": "notepad"},
                        "scoped_soul": injected_scoped_soul,
                    }
                ],
            )
            result = future.result(timeout=5)
        finally:
            lifecycle.shutdown()

        assert result.success is False
        assert result.collapse_reason == CollapseReason.SOUL_VIOLATION
        assert provider.act_calls == []
        assert [call["capability"] for call in governance.validations] == ["uab_automation"]
        assert governance.updates == []

        receipts = receipt_mgr.get_task_receipt_tree("quest-uab-injected-soul-widening")
        service = get_receipt_service(receipt_mgr._data_dir)
        action_receipt = next(r for r in receipts if r.action_name == "agent_action:execute_subtask")

        assert "does not permit UAB capability 'uab_click'" in action_receipt.inputs["action_result"]["error"]
        assert service.validate_parent_chain(quest_id="quest-uab-injected-soul-widening") == []

    async def test_post_spawn_category_mutation_cannot_widen_uab_execution_scope(
        self,
        config,
        registry,
        receipt_mgr,
    ):
        governance = _AuditedGovernance()
        lifecycle, provider = _make_uab_lifecycle(config, registry, receipt_mgr, governance)
        spec = TaskSpec(
            allowed_apps=["notepad"],
            allowed_categories=["query"],
        )

        try:
            record = lifecycle.spawn(
                spec,
                quest_id="quest-uab-post-spawn-category-mutation",
            )
            with pytest.raises(FrozenInstanceError):
                spec.allowed_categories = ("uab",)
            with pytest.raises(FrozenInstanceError):
                record.task_spec.allowed_categories = ("uab",)

            future = lifecycle.execute(
                record.agent_id,
                [
                    {
                        "action": "execute_subtask",
                        "capability": "execute_subtask",
                        "spec": "type 'hello world'",
                        "context": {"target_pid": 202, "target_app": "notepad"},
                    }
                ],
            )
            result = future.result(timeout=5)
        finally:
            lifecycle.shutdown()

        assert result.success is False
        assert result.collapse_reason == CollapseReason.SOUL_VIOLATION
        assert provider.act_calls == []
        assert [call["capability"] for call in governance.validations] == ["execute_subtask"]

        receipts = receipt_mgr.get_task_receipt_tree("quest-uab-post-spawn-category-mutation")
        action_receipt = next(r for r in receipts if r.action_name == "agent_action:execute_subtask")
        assert "outside scoped categories ['query']" in action_receipt.inputs["action_result"]["error"]

    async def test_post_spawn_app_mutation_cannot_widen_uab_execution_scope(
        self,
        config,
        registry,
        receipt_mgr,
    ):
        governance = _AuditedGovernance()
        lifecycle, provider = _make_uab_lifecycle(config, registry, receipt_mgr, governance)
        spec = TaskSpec(
            allowed_apps=["notepad"],
            allowed_categories=["uab"],
        )

        try:
            record = lifecycle.spawn(
                spec,
                quest_id="quest-uab-post-spawn-app-mutation",
            )
            with pytest.raises(FrozenInstanceError):
                spec.allowed_apps = ("chrome",)
            with pytest.raises(FrozenInstanceError):
                record.task_spec.allowed_apps = ("chrome",)

            future = lifecycle.execute(
                record.agent_id,
                [
                    {
                        "action": "execute_subtask",
                        "capability": "uab_automation",
                        "spec": "type 'hello world'",
                        "context": {"target_pid": 202, "target_app": "chrome"},
                    }
                ],
            )
            result = future.result(timeout=5)
        finally:
            lifecycle.shutdown()

        assert result.success is False
        assert result.collapse_reason == CollapseReason.SOUL_VIOLATION
        assert provider.act_calls == []
        assert governance.validations == []

        receipts = receipt_mgr.get_task_receipt_tree("quest-uab-post-spawn-app-mutation")
        action_receipt = next(r for r in receipts if r.action_name == "agent_action:execute_subtask")
        assert "forbids app 'chrome'" in action_receipt.inputs["action_result"]["error"]

    async def test_post_spawn_raw_scoped_soul_mutation_cannot_widen_uab_execution_scope(
        self,
        config,
        registry,
        receipt_mgr,
    ):
        governance = _AuditedGovernance()
        lifecycle, provider = _make_uab_lifecycle(
            config,
            registry,
            receipt_mgr,
            governance,
            parent_soul=_make_parent_soul_with_uab_capabilities(
                "uab_automation",
                "uab_query",
                "uab_state",
            ),
        )

        try:
            record = lifecycle.spawn(
                TaskSpec(
                    allowed_apps=["notepad"],
                    allowed_categories=["uab"],
                ),
                quest_id="quest-uab-post-spawn-soul-mutation",
            )
            runtime = lifecycle.get_runtime(record.agent_id)
            assert runtime is not None
            runtime._scoped_soul.autonomy_posture.allowed_autonomous.append("uab_click")
            runtime._scoped_soul.autonomy_posture.allowed_autonomous.append("uab_type")

            future = lifecycle.execute(
                record.agent_id,
                [
                    {
                        "action": "execute_subtask",
                        "capability": "uab_automation",
                        "spec": "type 'hello world'",
                        "context": {"target_pid": 202, "target_app": "notepad"},
                    }
                ],
            )
            result = future.result(timeout=5)
        finally:
            lifecycle.shutdown()

        assert result.success is False
        assert result.collapse_reason == CollapseReason.SOUL_VIOLATION
        assert provider.act_calls == []
        assert [call["capability"] for call in governance.validations] == ["uab_automation"]

        receipts = receipt_mgr.get_task_receipt_tree("quest-uab-post-spawn-soul-mutation")
        action_receipt = next(r for r in receipts if r.action_name == "agent_action:execute_subtask")
        assert "does not permit UAB capability 'uab_click'" in action_receipt.inputs["action_result"]["error"]
