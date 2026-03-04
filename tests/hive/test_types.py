"""Tests for HIVE types — enums, dataclasses, state transitions."""

import uuid
import pytest

from src.hive.types import (
    AgentState,
    CollapseReason,
    ControlMethod,
    DecomposedTask,
    InterventionType,
    OperatorIntervention,
    SubAgentRecord,
    TaskPriority,
    TaskResult,
    TaskSpec,
    VALID_TRANSITIONS,
)


# ── AgentState Enum ──────────────────────────────────────────────────

class TestAgentState:
    def test_all_six_states_exist(self):
        states = list(AgentState)
        assert len(states) == 6
        assert AgentState.SPAWNING in states
        assert AgentState.READY in states
        assert AgentState.EXECUTING in states
        assert AgentState.PAUSED in states
        assert AgentState.COMPLETING in states
        assert AgentState.COLLAPSED in states

    def test_state_values_are_lowercase_strings(self):
        for state in AgentState:
            assert state.value == state.value.lower()
            assert isinstance(state.value, str)

    def test_collapsed_is_terminal(self):
        assert VALID_TRANSITIONS[AgentState.COLLAPSED] == []

    def test_spawning_transitions_to_ready_or_collapsed(self):
        valid = VALID_TRANSITIONS[AgentState.SPAWNING]
        assert AgentState.READY in valid
        assert AgentState.COLLAPSED in valid
        assert len(valid) == 2

    def test_executing_can_pause(self):
        valid = VALID_TRANSITIONS[AgentState.EXECUTING]
        assert AgentState.PAUSED in valid

    def test_paused_can_resume(self):
        valid = VALID_TRANSITIONS[AgentState.PAUSED]
        assert AgentState.EXECUTING in valid

    def test_every_state_can_collapse(self):
        for state in AgentState:
            if state == AgentState.COLLAPSED:
                continue
            assert AgentState.COLLAPSED in VALID_TRANSITIONS[state], (
                f"{state} should be able to transition to COLLAPSED"
            )

    def test_all_states_have_transition_entries(self):
        for state in AgentState:
            assert state in VALID_TRANSITIONS


# ── ControlMethod Enum ───────────────────────────────────────────────

class TestControlMethod:
    def test_three_methods(self):
        assert len(list(ControlMethod)) == 3

    def test_values(self):
        assert ControlMethod.FULLY_AUTONOMOUS.value == "fully_autonomous"
        assert ControlMethod.SUPERVISED.value == "supervised"
        assert ControlMethod.MANUAL_CONFIRM.value == "manual_confirm"


# ── TaskPriority Enum ────────────────────────────────────────────────

class TestTaskPriority:
    def test_ordering(self):
        assert TaskPriority.CRITICAL < TaskPriority.HIGH
        assert TaskPriority.HIGH < TaskPriority.NORMAL
        assert TaskPriority.NORMAL < TaskPriority.LOW

    def test_int_values(self):
        assert int(TaskPriority.CRITICAL) == 0
        assert int(TaskPriority.LOW) == 3


# ── InterventionType Enum ────────────────────────────────────────────

class TestInterventionType:
    def test_five_types(self):
        assert len(list(InterventionType)) == 5

    def test_values(self):
        assert InterventionType.PAUSE.value == "pause"
        assert InterventionType.KILL_ALL.value == "kill_all"


# ── CollapseReason Enum ──────────────────────────────────────────────

class TestCollapseReason:
    def test_all_reasons_exist(self):
        reasons = list(CollapseReason)
        assert len(reasons) == 8
        assert CollapseReason.COMPLETED in reasons
        assert CollapseReason.OPERATOR_KILL in reasons
        assert CollapseReason.SOUL_VIOLATION in reasons


# ── TaskSpec Dataclass ───────────────────────────────────────────────

class TestTaskSpec:
    def test_default_construction(self):
        spec = TaskSpec()
        assert spec.task_id  # non-empty UUID
        assert spec.description == ""
        assert spec.control_method == ControlMethod.SUPERVISED
        assert spec.priority == TaskPriority.NORMAL
        assert spec.timeout_seconds == 300
        assert spec.max_actions == 50
        assert spec.allowed_apps == []
        assert spec.execution_group == 0

    def test_custom_construction(self):
        spec = TaskSpec(
            description="Test task",
            control_method=ControlMethod.FULLY_AUTONOMOUS,
            priority=TaskPriority.CRITICAL,
            timeout_seconds=60,
            max_actions=10,
            allowed_apps=["notepad"],
            execution_group=1,
        )
        assert spec.description == "Test task"
        assert spec.control_method == ControlMethod.FULLY_AUTONOMOUS
        assert spec.priority == TaskPriority.CRITICAL
        assert spec.timeout_seconds == 60
        assert spec.allowed_apps == ["notepad"]
        assert spec.execution_group == 1

    def test_task_id_is_unique(self):
        specs = [TaskSpec() for _ in range(10)]
        ids = {s.task_id for s in specs}
        assert len(ids) == 10


# ── DecomposedTask Dataclass ─────────────────────────────────────────

class TestDecomposedTask:
    def test_default_construction(self):
        dt = DecomposedTask()
        assert dt.quest_id
        assert dt.goal == ""
        assert dt.subtasks == []
        assert dt.execution_order == []
        assert dt.total_subtasks == 0

    def test_subtask_count(self):
        dt = DecomposedTask(
            subtasks=[TaskSpec(), TaskSpec(), TaskSpec()],
        )
        assert dt.total_subtasks == 3

    def test_has_timestamp(self):
        dt = DecomposedTask()
        assert dt.decomposed_at  # ISO timestamp string


# ── SubAgentRecord Dataclass ─────────────────────────────────────────

class TestSubAgentRecord:
    def test_default_state_is_spawning(self):
        record = SubAgentRecord()
        assert record.state == AgentState.SPAWNING

    def test_has_timestamps(self):
        record = SubAgentRecord()
        assert record.spawned_at
        assert record.state_changed_at

    def test_collapse_fields_default_none(self):
        record = SubAgentRecord()
        assert record.collapse_reason is None
        assert record.collapse_message is None
        assert record.collapsed_at is None

    def test_history_and_interventions_empty(self):
        record = SubAgentRecord()
        assert record.state_history == []
        assert record.interventions == []


# ── TaskResult Dataclass ─────────────────────────────────────────────

class TestTaskResult:
    def test_default_is_failure(self):
        result = TaskResult()
        assert result.success is False

    def test_success_result(self):
        result = TaskResult(
            task_id="t1",
            agent_id="a1",
            success=True,
            outputs={"data": "value"},
            action_count=5,
            duration_ms=1200,
        )
        assert result.success is True
        assert result.outputs == {"data": "value"}
        assert result.action_count == 5


# ── OperatorIntervention Dataclass ───────────────────────────────────

class TestOperatorIntervention:
    def test_default_construction(self):
        oi = OperatorIntervention()
        assert oi.intervention_id
        assert oi.intervention_type == InterventionType.PAUSE
        assert oi.reason == ""
        assert oi.resolved is False

    def test_kill_intervention(self):
        oi = OperatorIntervention(
            intervention_type=InterventionType.KILL,
            agent_id="agent-123",
            reason="Agent stuck in loop",
        )
        assert oi.intervention_type == InterventionType.KILL
        assert oi.agent_id == "agent-123"
        assert oi.reason == "Agent stuck in loop"

    def test_modify_with_constraints(self):
        oi = OperatorIntervention(
            intervention_type=InterventionType.MODIFY,
            agent_id="agent-456",
            reason="Reduce scope",
            constraints={"allowed_apps": ["notepad"]},
        )
        assert oi.constraints == {"allowed_apps": ["notepad"]}
