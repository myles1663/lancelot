"""
Tests for TaskRunner (Fix Pack V1 PR5).
Execute TaskRun step-by-step, calling tools/skills, emitting receipts, updating state.
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.tasking.schema import (
    RunStatus,
    StepType,
    TaskGraph,
    TaskRun,
    TaskStep,
)
from src.core.tasking.store import TaskStore
from src.core.tasking.runner import TaskRunner, StepResult, TaskRunResult
from src.core.execution_authority.schema import (
    AuthResult,
    ExecutionToken,
    TokenStatus,
)
from src.core.execution_authority.store import ExecutionTokenStore
from src.core.execution_authority.minter import PermissionMinter


# =========================================================================
# Test Fixtures
# =========================================================================


@pytest.fixture
def task_store(tmp_path):
    return TaskStore(tmp_path / "tasks.db")


@pytest.fixture
def token_store(tmp_path):
    return ExecutionTokenStore(tmp_path / "tokens.db")


@pytest.fixture
def minter(token_store):
    return PermissionMinter(store=token_store)


@pytest.fixture
def runner(task_store, token_store, minter):
    return TaskRunner(
        task_store=task_store,
        token_store=token_store,
        minter=minter,
    )


def _make_graph_and_run(task_store, steps, token_id=""):
    """Helper to create a graph and run in the store."""
    graph = TaskGraph(goal="test", steps=steps)
    task_store.save_graph(graph)
    run = TaskRun(
        task_graph_id=graph.id,
        execution_token_id=token_id,
    )
    task_store.create_run(run)
    return graph, run


# =========================================================================
# Basic Execution Tests
# =========================================================================


class TestBasicExecution:
    def test_single_step_succeeds(self, runner, task_store):
        """A single non-skill step executes with placeholder output."""
        steps = [TaskStep(step_id="s1", type=StepType.FILE_EDIT.value)]
        graph, run = _make_graph_and_run(task_store, steps)

        result = runner.run(run.id)
        assert result.status == RunStatus.SUCCEEDED.value
        assert len(result.step_results) == 1
        assert result.step_results[0].success is True

    def test_multi_step_all_succeed(self, runner, task_store):
        """Multiple steps execute in order and all succeed."""
        steps = [
            TaskStep(step_id="s1", type=StepType.FILE_EDIT.value),
            TaskStep(step_id="s2", type=StepType.COMMAND.value, dependencies=["s1"]),
            TaskStep(step_id="s3", type=StepType.VERIFY.value, dependencies=["s2"]),
        ]
        graph, run = _make_graph_and_run(task_store, steps)

        result = runner.run(run.id)
        assert result.status == RunStatus.SUCCEEDED.value
        assert len(result.step_results) == 3
        for sr in result.step_results:
            assert sr.success is True

    def test_run_not_found(self, runner):
        """Running a nonexistent TaskRun returns FAILED."""
        result = runner.run("nonexistent-id")
        assert result.status == "FAILED"

    def test_graph_not_found(self, runner, task_store):
        """Running a TaskRun with missing graph returns FAILED."""
        run = TaskRun(task_graph_id="missing-graph")
        task_store.create_run(run)
        result = runner.run(run.id)
        assert result.status == RunStatus.FAILED.value

    def test_state_transitions_to_running_then_succeeded(self, runner, task_store):
        """TaskRun transitions QUEUED → RUNNING → SUCCEEDED."""
        steps = [TaskStep(step_id="s1", type=StepType.TOOL_CALL.value)]
        graph, run = _make_graph_and_run(task_store, steps)

        # Before run
        assert task_store.get_run(run.id).status == RunStatus.QUEUED.value

        result = runner.run(run.id)

        # After run
        assert task_store.get_run(run.id).status == RunStatus.SUCCEEDED.value
        assert result.status == RunStatus.SUCCEEDED.value


# =========================================================================
# HUMAN_INPUT → BLOCKED Tests
# =========================================================================


class TestHumanInputBlocked:
    def test_human_input_blocks_run(self, runner, task_store):
        """A HUMAN_INPUT step transitions the run to BLOCKED."""
        steps = [
            TaskStep(step_id="s1", type=StepType.FILE_EDIT.value),
            TaskStep(step_id="s2", type=StepType.HUMAN_INPUT.value, dependencies=["s1"]),
            TaskStep(step_id="s3", type=StepType.COMMAND.value, dependencies=["s2"]),
        ]
        graph, run = _make_graph_and_run(task_store, steps)

        result = runner.run(run.id)
        assert result.status == RunStatus.BLOCKED.value
        assert result.blocked_step == "s2"
        # s1 should have succeeded, s2 should be the blocking step, s3 not executed
        assert len(result.step_results) == 2
        assert result.step_results[0].success is True  # s1

    def test_blocked_run_state_persisted(self, runner, task_store):
        """BLOCKED state is persisted in the store."""
        steps = [TaskStep(step_id="s1", type=StepType.HUMAN_INPUT.value)]
        graph, run = _make_graph_and_run(task_store, steps)

        runner.run(run.id)
        persisted = task_store.get_run(run.id)
        assert persisted.status == RunStatus.BLOCKED.value
        assert persisted.current_step_id == "s1"


# =========================================================================
# Token Authority Tests
# =========================================================================


class TestTokenAuthority:
    def test_allowed_tool_passes(self, runner, task_store, token_store, minter):
        """Step with allowed tool type succeeds."""
        token = minter.mint_from_approval(
            scope="test", tools=[],  # Empty = all tools allowed
        )
        steps = [TaskStep(step_id="s1", type=StepType.FILE_EDIT.value)]
        graph, run = _make_graph_and_run(task_store, steps, token_id=token.id)

        result = runner.run(run.id)
        assert result.status == RunStatus.SUCCEEDED.value

    def test_denied_tool_fails_run(self, runner, task_store, token_store, minter):
        """Step with denied tool type fails the run."""
        token = minter.mint_from_approval(
            scope="test", tools=["read_file"],  # Only read_file allowed
        )
        steps = [TaskStep(step_id="s1", type=StepType.FILE_EDIT.value)]
        graph, run = _make_graph_and_run(task_store, steps, token_id=token.id)

        result = runner.run(run.id)
        assert result.status == RunStatus.FAILED.value
        assert "Authority denied" in result.step_results[0].error

    def test_token_action_count_incremented(self, runner, task_store, token_store, minter):
        """Token's actions_used is incremented after each successful step."""
        token = minter.mint_from_approval(scope="test", max_actions=10)
        steps = [
            TaskStep(step_id="s1", type=StepType.TOOL_CALL.value),
            TaskStep(step_id="s2", type=StepType.TOOL_CALL.value, dependencies=["s1"]),
        ]
        graph, run = _make_graph_and_run(task_store, steps, token_id=token.id)

        runner.run(run.id)
        updated_token = token_store.get(token.id)
        assert updated_token.actions_used == 2


# =========================================================================
# Receipt Tracking Tests
# =========================================================================


class TestReceiptTracking:
    def test_receipts_tracked_in_run(self, runner, task_store):
        """Receipt IDs are added to TaskRun's receipts_index."""
        steps = [
            TaskStep(step_id="s1", type=StepType.FILE_EDIT.value),
            TaskStep(step_id="s2", type=StepType.COMMAND.value, dependencies=["s1"]),
        ]
        graph, run = _make_graph_and_run(task_store, steps)

        result = runner.run(run.id)
        # Each step emits at least STEP_STARTED + STEP_COMPLETED = 2 receipts per step
        assert len(result.receipts) >= 4  # 2 steps * 2 receipts each

        # Verify they're persisted in the store
        persisted = task_store.get_run(run.id)
        assert len(persisted.receipts_index) >= 4


# =========================================================================
# Dependency Order Tests
# =========================================================================


class TestDependencyOrder:
    def test_dependencies_respected(self, runner, task_store):
        """Steps with dependencies execute after their dependencies."""
        steps = [
            TaskStep(step_id="s3", type=StepType.VERIFY.value, dependencies=["s1", "s2"]),
            TaskStep(step_id="s1", type=StepType.FILE_EDIT.value),
            TaskStep(step_id="s2", type=StepType.COMMAND.value, dependencies=["s1"]),
        ]
        graph, run = _make_graph_and_run(task_store, steps)

        result = runner.run(run.id)
        assert result.status == RunStatus.SUCCEEDED.value
        # Verify execution order: s1 before s2 before s3
        ids = [sr.step_id for sr in result.step_results]
        assert ids.index("s1") < ids.index("s2")
        assert ids.index("s2") < ids.index("s3")

    def test_independent_steps_execute(self, runner, task_store):
        """Independent steps (no deps) all execute."""
        steps = [
            TaskStep(step_id="s1", type=StepType.FILE_EDIT.value),
            TaskStep(step_id="s2", type=StepType.COMMAND.value),
            TaskStep(step_id="s3", type=StepType.VERIFY.value),
        ]
        graph, run = _make_graph_and_run(task_store, steps)

        result = runner.run(run.id)
        assert result.status == RunStatus.SUCCEEDED.value
        assert len(result.step_results) == 3


# =========================================================================
# Verify Step Tests
# =========================================================================


class TestVerifySteps:
    def test_verify_step_passes_without_verifier(self, runner, task_store):
        """A VERIFY step passes when no verifier is available."""
        steps = [
            TaskStep(step_id="s1", type=StepType.VERIFY.value,
                     acceptance_check="All good"),
        ]
        graph, run = _make_graph_and_run(task_store, steps)

        result = runner.run(run.id)
        assert result.status == RunStatus.SUCCEEDED.value
        assert result.step_results[0].outputs.get("verified") is True


# =========================================================================
# Skill Executor Integration Tests
# =========================================================================


class _FakeSkillResult:
    def __init__(self, success=True, outputs=None, error=None):
        self.success = success
        self.outputs = outputs or {"result": "done"}
        self.error = error


class _FakeSkillExecutor:
    """Fake skill executor for testing."""
    def __init__(self, results=None):
        self.calls = []
        self.results = results or {}

    def run(self, skill_name, inputs):
        self.calls.append((skill_name, inputs))
        if skill_name in self.results:
            return self.results[skill_name]
        return _FakeSkillResult(success=True, outputs={"echo": inputs})


class TestSkillExecutorIntegration:
    def test_skill_call_delegates_to_executor(self, task_store, token_store, minter):
        """SKILL_CALL steps delegate to skill_executor.run()."""
        fake_executor = _FakeSkillExecutor()
        runner = TaskRunner(
            task_store=task_store,
            token_store=token_store,
            minter=minter,
            skill_executor=fake_executor,
        )
        steps = [
            TaskStep(step_id="s1", type=StepType.SKILL_CALL.value,
                     inputs={"skill_name": "echo", "data": "hello"}),
        ]
        graph, run = _make_graph_and_run(task_store, steps)

        result = runner.run(run.id)
        assert result.status == RunStatus.SUCCEEDED.value
        assert len(fake_executor.calls) == 1
        assert fake_executor.calls[0][0] == "echo"

    def test_skill_failure_fails_run(self, task_store, token_store, minter):
        """A failing skill causes the run to fail."""
        fake_executor = _FakeSkillExecutor(
            results={"bad_skill": _FakeSkillResult(success=False, error="Boom")}
        )
        runner = TaskRunner(
            task_store=task_store,
            token_store=token_store,
            minter=minter,
            skill_executor=fake_executor,
        )
        steps = [
            TaskStep(step_id="s1", type=StepType.SKILL_CALL.value,
                     inputs={"skill_name": "bad_skill"}),
        ]
        graph, run = _make_graph_and_run(task_store, steps)

        result = runner.run(run.id)
        assert result.status == RunStatus.FAILED.value
        assert "bad_skill" in result.step_results[0].error

    def test_file_edit_maps_to_repo_writer(self, task_store, token_store, minter):
        """FILE_EDIT steps map to 'repo_writer' skill."""
        fake_executor = _FakeSkillExecutor()
        runner = TaskRunner(
            task_store=task_store,
            token_store=token_store,
            minter=minter,
            skill_executor=fake_executor,
        )
        steps = [
            TaskStep(step_id="s1", type=StepType.FILE_EDIT.value,
                     inputs={"path": "config.yaml", "content": "key: value"}),
        ]
        graph, run = _make_graph_and_run(task_store, steps)

        runner.run(run.id)
        assert fake_executor.calls[0][0] == "repo_writer"

    def test_command_maps_to_command_runner(self, task_store, token_store, minter):
        """COMMAND steps map to 'command_runner' skill."""
        fake_executor = _FakeSkillExecutor()
        runner = TaskRunner(
            task_store=task_store,
            token_store=token_store,
            minter=minter,
            skill_executor=fake_executor,
        )
        steps = [
            TaskStep(step_id="s1", type=StepType.COMMAND.value,
                     inputs={"command": "ls -la"}),
        ]
        graph, run = _make_graph_and_run(task_store, steps)

        runner.run(run.id)
        assert fake_executor.calls[0][0] == "command_runner"


# =========================================================================
# Receipt Service Integration Tests
# =========================================================================


class TestReceiptServiceIntegration:
    def test_receipts_emitted_with_service(self, task_store, token_store, minter, tmp_path):
        """When receipt_service is provided, receipts are emitted."""
        from src.shared.receipts import ReceiptService
        receipt_svc = ReceiptService(str(tmp_path / "receipts"))

        runner = TaskRunner(
            task_store=task_store,
            token_store=token_store,
            minter=minter,
            receipt_service=receipt_svc,
        )
        steps = [TaskStep(step_id="s1", type=StepType.TOOL_CALL.value)]
        graph, run = _make_graph_and_run(task_store, steps)

        result = runner.run(run.id)
        assert result.status == RunStatus.SUCCEEDED.value

        # Check receipts were persisted
        all_receipts = receipt_svc.list()
        assert len(all_receipts) >= 2  # STEP_STARTED + STEP_COMPLETED
