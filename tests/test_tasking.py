"""
Tests for TaskGraph, TaskRun, TaskStore, and PlanCompiler (Fix Pack V1 PR4).
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.tasking.schema import (
    RunStatus,
    StepType,
    TaskGraph,
    TaskRun,
    TaskStep,
)
from src.core.tasking.store import TaskStore
from src.core.tasking.compiler import PlanCompiler


# =========================================================================
# TaskStep Tests
# =========================================================================


class TestTaskStep:
    def test_creation(self):
        step = TaskStep(
            step_id="s1",
            type=StepType.FILE_EDIT.value,
            inputs={"path": "config.yaml"},
            acceptance_check="File exists",
        )
        assert step.step_id == "s1"
        assert step.type == "FILE_EDIT"

    def test_to_dict_roundtrip(self):
        step = TaskStep(
            step_id="s1",
            type=StepType.COMMAND.value,
            inputs={"command": "npm install"},
            risk_level="MED",
            dependencies=["s0"],
        )
        d = step.to_dict()
        step2 = TaskStep.from_dict(d)
        assert step2.step_id == step.step_id
        assert step2.type == step.type
        assert step2.dependencies == ["s0"]


# =========================================================================
# TaskGraph Tests
# =========================================================================


class TestTaskGraph:
    def test_creation(self):
        graph = TaskGraph(
            goal="Migrate database",
            steps=[
                TaskStep(step_id="s1", type=StepType.COMMAND.value),
                TaskStep(step_id="s2", type=StepType.VERIFY.value, dependencies=["s1"]),
            ],
        )
        assert len(graph.steps) == 2
        assert graph.goal == "Migrate database"

    def test_to_dict_roundtrip(self):
        graph = TaskGraph(
            goal="Test goal",
            steps=[
                TaskStep(step_id="s1", type=StepType.FILE_EDIT.value,
                         inputs={"path": "test.py"}),
            ],
        )
        d = graph.to_dict()
        graph2 = TaskGraph.from_dict(d)
        assert graph2.goal == graph.goal
        assert len(graph2.steps) == 1
        assert graph2.steps[0].step_id == "s1"


# =========================================================================
# TaskRun Tests
# =========================================================================


class TestTaskRun:
    def test_creation(self):
        run = TaskRun(
            task_graph_id="graph-1",
            execution_token_id="token-1",
        )
        assert run.status == RunStatus.QUEUED.value
        assert run.receipts_index == []

    def test_to_dict_roundtrip(self):
        run = TaskRun(
            task_graph_id="g1",
            execution_token_id="t1",
            status=RunStatus.RUNNING.value,
            current_step_id="s2",
            receipts_index=["r1", "r2"],
        )
        d = run.to_dict()
        run2 = TaskRun.from_dict(d)
        assert run2.status == "RUNNING"
        assert run2.receipts_index == ["r1", "r2"]


# =========================================================================
# TaskStore Tests
# =========================================================================


class TestTaskStore:
    @pytest.fixture
    def store(self, tmp_path):
        return TaskStore(tmp_path / "tasks.db")

    # --- TaskGraph persistence ---

    def test_save_and_get_graph(self, store):
        graph = TaskGraph(
            goal="Test goal",
            steps=[TaskStep(step_id="s1", type=StepType.TOOL_CALL.value)],
        )
        store.save_graph(graph)
        retrieved = store.get_graph(graph.id)
        assert retrieved is not None
        assert retrieved.goal == "Test goal"
        assert len(retrieved.steps) == 1

    def test_get_graph_nonexistent(self, store):
        assert store.get_graph("nonexistent") is None

    def test_get_latest_graph_for_session(self, store):
        from datetime import datetime, timezone, timedelta
        t1 = datetime.now(timezone.utc).isoformat()
        t2 = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
        g1 = TaskGraph(goal="First", session_id="sess-1", created_at=t1)
        g2 = TaskGraph(goal="Second", session_id="sess-1", created_at=t2)
        g3 = TaskGraph(goal="Other", session_id="sess-2")
        store.save_graph(g1)
        store.save_graph(g2)
        store.save_graph(g3)
        latest = store.get_latest_graph_for_session("sess-1")
        assert latest is not None
        assert latest.goal == "Second"

    # --- TaskRun persistence ---

    def test_create_and_get_run(self, store):
        graph = TaskGraph(goal="test")
        store.save_graph(graph)
        run = TaskRun(task_graph_id=graph.id, execution_token_id="tok-1")
        store.create_run(run)
        retrieved = store.get_run(run.id)
        assert retrieved is not None
        assert retrieved.task_graph_id == graph.id
        assert retrieved.status == RunStatus.QUEUED.value

    def test_update_status(self, store):
        graph = TaskGraph(goal="test")
        store.save_graph(graph)
        run = TaskRun(task_graph_id=graph.id)
        store.create_run(run)
        store.update_status(run.id, RunStatus.RUNNING.value, current_step="s1")
        retrieved = store.get_run(run.id)
        assert retrieved.status == "RUNNING"
        assert retrieved.current_step_id == "s1"

    def test_update_status_failed(self, store):
        graph = TaskGraph(goal="test")
        store.save_graph(graph)
        run = TaskRun(task_graph_id=graph.id)
        store.create_run(run)
        store.update_status(run.id, RunStatus.FAILED.value, error="Something broke")
        retrieved = store.get_run(run.id)
        assert retrieved.status == "FAILED"
        assert retrieved.last_error == "Something broke"

    def test_add_receipt(self, store):
        graph = TaskGraph(goal="test")
        store.save_graph(graph)
        run = TaskRun(task_graph_id=graph.id)
        store.create_run(run)
        store.add_receipt(run.id, "receipt-1")
        store.add_receipt(run.id, "receipt-2")
        retrieved = store.get_run(run.id)
        assert retrieved.receipts_index == ["receipt-1", "receipt-2"]

    def test_get_active_run(self, store):
        graph = TaskGraph(goal="test")
        store.save_graph(graph)
        run = TaskRun(task_graph_id=graph.id, status=RunStatus.RUNNING.value)
        store.create_run(run)
        active = store.get_active_run()
        assert active is not None
        assert active.id == run.id

    def test_get_active_run_none(self, store):
        assert store.get_active_run() is None

    def test_list_runs(self, store):
        graph = TaskGraph(goal="test")
        store.save_graph(graph)
        for i in range(5):
            run = TaskRun(task_graph_id=graph.id, session_id="sess-1")
            store.create_run(run)
        runs = store.list_runs(limit=3, session_id="sess-1")
        assert len(runs) == 3

    # --- State transition: QUEUED → RUNNING → SUCCEEDED ---

    def test_state_transition_success(self, store):
        graph = TaskGraph(goal="test")
        store.save_graph(graph)
        run = TaskRun(task_graph_id=graph.id)
        store.create_run(run)
        assert store.get_run(run.id).status == "QUEUED"
        store.update_status(run.id, RunStatus.RUNNING.value)
        assert store.get_run(run.id).status == "RUNNING"
        store.update_status(run.id, RunStatus.SUCCEEDED.value)
        assert store.get_run(run.id).status == "SUCCEEDED"

    # --- State transition: QUEUED → RUNNING → FAILED ---

    def test_state_transition_failure(self, store):
        graph = TaskGraph(goal="test")
        store.save_graph(graph)
        run = TaskRun(task_graph_id=graph.id)
        store.create_run(run)
        store.update_status(run.id, RunStatus.RUNNING.value)
        store.update_status(run.id, RunStatus.FAILED.value, error="timeout")
        retrieved = store.get_run(run.id)
        assert retrieved.status == "FAILED"
        assert retrieved.last_error == "timeout"

    # --- State transition: RUNNING → BLOCKED → RUNNING → SUCCEEDED ---

    def test_state_transition_blocked(self, store):
        graph = TaskGraph(goal="test")
        store.save_graph(graph)
        run = TaskRun(task_graph_id=graph.id)
        store.create_run(run)
        store.update_status(run.id, RunStatus.RUNNING.value)
        store.update_status(run.id, RunStatus.BLOCKED.value, current_step="s3")
        assert store.get_run(run.id).status == "BLOCKED"
        store.update_status(run.id, RunStatus.RUNNING.value)
        store.update_status(run.id, RunStatus.SUCCEEDED.value)
        assert store.get_run(run.id).status == "SUCCEEDED"


# =========================================================================
# PlanCompiler Tests
# =========================================================================


class TestPlanCompiler:
    def test_compile_plan_artifact(self):
        from plan_types import PlanArtifact, RiskItem
        artifact = PlanArtifact(
            goal="Deploy service",
            context=["ctx"],
            assumptions=["a"],
            plan_steps=[
                "Create file for configuration",
                "Run deployment command",
                "Verify service health",
            ],
            decision_points=["d"],
            risks=[RiskItem(risk="r", mitigation="m")],
            done_when=["Service is healthy", "Logs show no errors", "Health endpoint returns 200"],
            next_action="Create config",
        )
        compiler = PlanCompiler()
        graph = compiler.compile_plan_artifact(artifact, session_id="test-sess")
        assert graph.goal == "Deploy service"
        assert len(graph.steps) == 3
        assert graph.session_id == "test-sess"
        # Check step types are inferred from descriptions
        assert graph.steps[0].type == StepType.FILE_EDIT.value  # "Create file ..."
        assert graph.steps[1].type == StepType.COMMAND.value     # "Run ... command"
        assert graph.steps[2].type == StepType.VERIFY.value      # "Verify ..."
        # Check sequential dependencies
        assert graph.steps[0].dependencies == []
        assert graph.steps[1].dependencies == ["step-1"]
        assert graph.steps[2].dependencies == ["step-2"]

    def test_compile_plan_artifact_acceptance_checks(self):
        from plan_types import PlanArtifact, RiskItem
        artifact = PlanArtifact(
            goal="Test",
            context=["c"],
            assumptions=["a"],
            plan_steps=["Step 1", "Step 2", "Step 3"],
            decision_points=["d"],
            risks=[RiskItem(risk="r", mitigation="m")],
            done_when=["Check 1", "Check 2"],
            next_action="Do it",
        )
        compiler = PlanCompiler()
        graph = compiler.compile_plan_artifact(artifact)
        assert graph.steps[0].acceptance_check == "Check 1"
        assert graph.steps[1].acceptance_check == "Check 2"
        assert graph.steps[2].acceptance_check == ""  # Not enough done_when items

    def test_compile_plan_artifact_produces_valid_steps(self):
        from plan_types import PlanArtifact, RiskItem
        artifact = PlanArtifact(
            goal="Migrate DB",
            context=["c"],
            assumptions=["a"],
            plan_steps=["Backup the database", "Execute migration script", "Test results", "Deploy"],
            decision_points=["d"],
            risks=[RiskItem(risk="r", mitigation="m")],
            done_when=["done"],
            next_action="Backup",
        )
        compiler = PlanCompiler()
        graph = compiler.compile_plan_artifact(artifact)
        for step in graph.steps:
            assert step.step_id
            assert step.type in [s.value for s in StepType]
            assert step.risk_level in ("LOW", "MED", "HIGH")
