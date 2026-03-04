"""Tests for HIVE Task Decomposer."""

import json
import pytest

from src.hive.decomposer import TaskDecomposer
from src.hive.types import ControlMethod, TaskPriority
from src.hive.errors import TaskDecompositionError


class MockRouterResult:
    """Mock RouterResult."""

    def __init__(self, output=None):
        self.output = output
        self.data = None
        self.executed = True


class MockModelRouter:
    """Mock ModelRouter for testing."""

    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.calls = []

    def route(self, task_type, text, **kwargs):
        self.calls.append({"task_type": task_type, "text": text, **kwargs})
        if self._error:
            raise self._error
        return MockRouterResult(output=self._response)


class MockUABBridge:
    """Mock UAB bridge for testing."""

    def __init__(self, apps=None):
        self._apps = apps or []

    async def get_available_apps(self):
        return self._apps


def _make_decomposer(response=None, router_error=None, uab_apps=None, max_subtasks=20):
    """Helper to create a decomposer with mocks."""
    router = MockModelRouter(response=response, error=router_error)
    uab = MockUABBridge(apps=uab_apps) if uab_apps is not None else None
    return TaskDecomposer(
        model_router=router,
        uab_bridge=uab,
        max_subtasks=max_subtasks,
    ), router


def _valid_response(subtasks=None, execution_order=None):
    """Build a valid JSON response string."""
    subtasks = subtasks or [
        {
            "description": "Read the file",
            "priority": "normal",
            "control_method": "fully_autonomous",
            "execution_group": 0,
            "allowed_categories": ["read"],
            "timeout_seconds": 60,
        },
        {
            "description": "Analyze content",
            "priority": "high",
            "control_method": "supervised",
            "execution_group": 1,
        },
    ]
    return json.dumps({
        "subtasks": subtasks,
        "execution_order": execution_order or [[0], [1]],
        "rationale": "Step 1 reads, step 2 analyzes",
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


@pytest.mark.asyncio
class TestDecompose:
    async def test_basic_decomposition(self):
        decomposer, router = _make_decomposer(response=_valid_response())
        result = await decomposer.decompose("Analyze the file")
        assert result.goal == "Analyze the file"
        assert len(result.subtasks) == 2
        assert result.execution_order == [["0"], ["1"]]
        assert result.quest_id is not None

    async def test_subtask_properties(self):
        decomposer, _ = _make_decomposer(response=_valid_response())
        result = await decomposer.decompose("Test task")
        s0 = result.subtasks[0]
        assert s0.description == "Read the file"
        assert s0.priority == TaskPriority.NORMAL
        assert s0.control_method == ControlMethod.FULLY_AUTONOMOUS
        assert s0.timeout_seconds == 60

    async def test_quest_id_passed_through(self):
        decomposer, _ = _make_decomposer(response=_valid_response())
        result = await decomposer.decompose("Test", quest_id="q-123")
        assert result.quest_id == "q-123"

    async def test_context_passed_to_prompt(self):
        decomposer, router = _make_decomposer(response=_valid_response())
        await decomposer.decompose("Test", context={"key": "value"})
        assert len(router.calls) == 1
        assert '"key": "value"' in router.calls[0]["text"]

    async def test_uses_flagship_deep_lane(self):
        decomposer, router = _make_decomposer(response=_valid_response())
        await decomposer.decompose("Plan something")
        assert router.calls[0]["task_type"] == "plan"

    async def test_uab_apps_included(self):
        apps = [{"name": "notepad", "pid": 1234}]
        decomposer, router = _make_decomposer(
            response=_valid_response(),
            uab_apps=apps,
        )
        await decomposer.decompose("Test with UAB")
        assert "notepad" in router.calls[0]["text"]

    async def test_no_uab_bridge_works(self):
        decomposer, _ = _make_decomposer(response=_valid_response())
        result = await decomposer.decompose("Test without UAB")
        assert len(result.subtasks) == 2


@pytest.mark.asyncio
class TestDecomposerErrors:
    async def test_empty_goal_raises(self):
        decomposer, _ = _make_decomposer(response=_valid_response())
        with pytest.raises(TaskDecompositionError, match="empty"):
            await decomposer.decompose("")

    async def test_no_router_raises(self):
        decomposer = TaskDecomposer()
        with pytest.raises(TaskDecompositionError, match="No ModelRouter"):
            await decomposer.decompose("Test")

    async def test_router_error_raises(self):
        decomposer, _ = _make_decomposer(
            router_error=RuntimeError("LLM down"),
        )
        with pytest.raises(TaskDecompositionError, match="LLM call failed"):
            await decomposer.decompose("Test")

    async def test_invalid_json_raises(self):
        decomposer, _ = _make_decomposer(response="not json")
        with pytest.raises(TaskDecompositionError, match="Failed to parse"):
            await decomposer.decompose("Test")

    async def test_missing_subtasks_field_raises(self):
        decomposer, _ = _make_decomposer(
            response=json.dumps({"other": "data"}),
        )
        with pytest.raises(TaskDecompositionError, match="missing 'subtasks'"):
            await decomposer.decompose("Test")

    async def test_null_output_raises(self):
        router = MockModelRouter(response=None)
        decomposer = TaskDecomposer(model_router=router)
        with pytest.raises(TaskDecompositionError, match="no output"):
            await decomposer.decompose("Test")


@pytest.mark.asyncio
class TestMaxSubtasks:
    async def test_max_subtasks_enforced(self):
        subtasks = [
            {"description": f"Task {i}", "priority": "normal", "control_method": "supervised"}
            for i in range(10)
        ]
        response = json.dumps({
            "subtasks": subtasks,
            "execution_order": [[i] for i in range(10)],
        })
        decomposer, _ = _make_decomposer(response=response, max_subtasks=3)
        result = await decomposer.decompose("Many tasks")
        assert len(result.subtasks) == 3


@pytest.mark.asyncio
class TestParsing:
    async def test_strips_markdown_fences(self):
        raw = "```json\n" + _valid_response() + "\n```"
        decomposer, _ = _make_decomposer(response=raw)
        result = await decomposer.decompose("Fenced response")
        assert len(result.subtasks) == 2

    async def test_priority_mapping(self):
        subtasks = [
            {"description": "Critical", "priority": "critical", "control_method": "supervised"},
            {"description": "High", "priority": "high", "control_method": "supervised"},
            {"description": "Normal", "priority": "normal", "control_method": "supervised"},
            {"description": "Low", "priority": "low", "control_method": "supervised"},
        ]
        response = json.dumps({
            "subtasks": subtasks,
            "execution_order": [[0, 1, 2, 3]],
        })
        decomposer, _ = _make_decomposer(response=response)
        result = await decomposer.decompose("Priority test")
        assert result.subtasks[0].priority == TaskPriority.CRITICAL
        assert result.subtasks[1].priority == TaskPriority.HIGH
        assert result.subtasks[2].priority == TaskPriority.NORMAL
        assert result.subtasks[3].priority == TaskPriority.LOW

    async def test_control_method_mapping(self):
        subtasks = [
            {"description": "Auto", "priority": "normal", "control_method": "fully_autonomous"},
            {"description": "Super", "priority": "normal", "control_method": "supervised"},
            {"description": "Manual", "priority": "normal", "control_method": "manual_confirm"},
        ]
        response = json.dumps({
            "subtasks": subtasks,
            "execution_order": [[0, 1, 2]],
        })
        decomposer, _ = _make_decomposer(response=response)
        result = await decomposer.decompose("Control test")
        assert result.subtasks[0].control_method == ControlMethod.FULLY_AUTONOMOUS
        assert result.subtasks[1].control_method == ControlMethod.SUPERVISED
        assert result.subtasks[2].control_method == ControlMethod.MANUAL_CONFIRM


@pytest.mark.asyncio
class TestValidation:
    async def test_invalid_execution_order_index_filtered(self):
        """Out-of-bound indices in execution_order are silently filtered."""
        response = json.dumps({
            "subtasks": [{"description": "One", "priority": "normal", "control_method": "supervised"}],
            "execution_order": [[0], [5]],
        })
        decomposer, _ = _make_decomposer(response=response)
        result = await decomposer.decompose("Bad order")
        # Index 5 is filtered out, only group [0] remains
        assert len(result.execution_order) == 1
        assert result.execution_order[0] == ["0"]

    async def test_duplicate_execution_order_index(self):
        response = json.dumps({
            "subtasks": [
                {"description": "A", "priority": "normal", "control_method": "supervised"},
                {"description": "B", "priority": "normal", "control_method": "supervised"},
            ],
            "execution_order": [[0, 0]],
        })
        decomposer, _ = _make_decomposer(response=response)
        with pytest.raises(TaskDecompositionError, match="multiple times"):
            await decomposer.decompose("Dup order")
