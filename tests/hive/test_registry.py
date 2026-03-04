"""Tests for HIVE Agent Registry — state machine, capacity, thread safety."""

import threading
import pytest

from src.hive.types import (
    AgentState,
    CollapseReason,
    TaskSpec,
    ControlMethod,
)
from src.hive.registry import AgentRegistry
from src.hive.errors import AgentCollapsedError, MaxAgentsExceededError


class TestAgentRegistration:
    def test_register_creates_spawning_agent(self):
        reg = AgentRegistry(max_concurrent_agents=5)
        spec = TaskSpec(description="Test task")
        record = reg.register(spec)
        assert record.state == AgentState.SPAWNING
        assert record.agent_id
        assert record.task_spec.description == "Test task"

    def test_register_with_quest_id(self):
        reg = AgentRegistry()
        spec = TaskSpec()
        record = reg.register(spec, quest_id="quest-123")
        assert record.quest_id == "quest-123"

    def test_register_with_soul_hash(self):
        reg = AgentRegistry()
        spec = TaskSpec()
        record = reg.register(spec, scoped_soul_hash="abc123")
        assert record.scoped_soul_hash == "abc123"

    def test_register_adds_initial_history(self):
        reg = AgentRegistry()
        spec = TaskSpec()
        record = reg.register(spec)
        assert len(record.state_history) == 1
        assert record.state_history[0]["from"] is None
        assert record.state_history[0]["to"] == "spawning"


class TestStateTransitions:
    def test_spawning_to_ready(self):
        reg = AgentRegistry()
        spec = TaskSpec()
        record = reg.register(spec)
        updated = reg.transition(record.agent_id, AgentState.READY)
        assert updated.state == AgentState.READY

    def test_full_lifecycle(self):
        reg = AgentRegistry()
        spec = TaskSpec()
        record = reg.register(spec)
        reg.transition(record.agent_id, AgentState.READY)
        reg.transition(record.agent_id, AgentState.EXECUTING)
        reg.transition(record.agent_id, AgentState.COMPLETING)
        updated = reg.transition(
            record.agent_id,
            AgentState.COLLAPSED,
            collapse_reason=CollapseReason.COMPLETED,
        )
        assert updated.state == AgentState.COLLAPSED
        assert updated.collapse_reason == CollapseReason.COMPLETED
        assert updated.collapsed_at is not None

    def test_pause_resume_cycle(self):
        reg = AgentRegistry()
        spec = TaskSpec()
        record = reg.register(spec)
        reg.transition(record.agent_id, AgentState.READY)
        reg.transition(record.agent_id, AgentState.EXECUTING)
        reg.transition(record.agent_id, AgentState.PAUSED)
        updated = reg.transition(record.agent_id, AgentState.EXECUTING)
        assert updated.state == AgentState.EXECUTING

    def test_invalid_transition_raises(self):
        reg = AgentRegistry()
        spec = TaskSpec()
        record = reg.register(spec)
        with pytest.raises(ValueError, match="Invalid transition"):
            reg.transition(record.agent_id, AgentState.EXECUTING)

    def test_collapsed_transition_raises(self):
        reg = AgentRegistry()
        spec = TaskSpec()
        record = reg.register(spec)
        reg.transition(
            record.agent_id,
            AgentState.COLLAPSED,
            collapse_reason=CollapseReason.ERROR,
        )
        with pytest.raises(AgentCollapsedError):
            reg.transition(record.agent_id, AgentState.READY)

    def test_state_history_records_all_transitions(self):
        reg = AgentRegistry()
        spec = TaskSpec()
        record = reg.register(spec)
        reg.transition(record.agent_id, AgentState.READY)
        reg.transition(record.agent_id, AgentState.EXECUTING)
        # Record is now in archive after collapse, but get() searches archive
        reg.transition(
            record.agent_id,
            AgentState.COLLAPSED,
            collapse_reason=CollapseReason.COMPLETED,
        )
        retrieved = reg.get(record.agent_id)
        assert retrieved is not None
        assert len(retrieved.state_history) == 4  # spawning, ready, executing, collapsed

    def test_any_state_can_collapse(self):
        for state in [AgentState.SPAWNING, AgentState.READY,
                      AgentState.EXECUTING, AgentState.PAUSED,
                      AgentState.COMPLETING]:
            reg = AgentRegistry()
            spec = TaskSpec()
            record = reg.register(spec)
            # Navigate to the target state
            if state in (AgentState.READY, AgentState.EXECUTING,
                        AgentState.PAUSED, AgentState.COMPLETING):
                reg.transition(record.agent_id, AgentState.READY)
            if state in (AgentState.EXECUTING, AgentState.PAUSED,
                        AgentState.COMPLETING):
                reg.transition(record.agent_id, AgentState.EXECUTING)
            if state == AgentState.PAUSED:
                reg.transition(record.agent_id, AgentState.PAUSED)
            if state == AgentState.COMPLETING:
                reg.transition(record.agent_id, AgentState.COMPLETING)

            updated = reg.transition(
                record.agent_id,
                AgentState.COLLAPSED,
                collapse_reason=CollapseReason.OPERATOR_KILL,
            )
            assert updated.state == AgentState.COLLAPSED


class TestCapacity:
    def test_can_spawn_when_empty(self):
        reg = AgentRegistry(max_concurrent_agents=2)
        assert reg.can_spawn() is True

    def test_cannot_spawn_at_capacity(self):
        reg = AgentRegistry(max_concurrent_agents=2)
        reg.register(TaskSpec())
        reg.register(TaskSpec())
        assert reg.can_spawn() is False

    def test_register_raises_at_capacity(self):
        reg = AgentRegistry(max_concurrent_agents=1)
        reg.register(TaskSpec())
        with pytest.raises(MaxAgentsExceededError):
            reg.register(TaskSpec())

    def test_paused_agents_count_toward_capacity(self):
        reg = AgentRegistry(max_concurrent_agents=2)
        r1 = reg.register(TaskSpec())
        reg.transition(r1.agent_id, AgentState.READY)
        reg.transition(r1.agent_id, AgentState.EXECUTING)
        reg.transition(r1.agent_id, AgentState.PAUSED)
        r2 = reg.register(TaskSpec())
        # Both count — cannot spawn a third
        assert reg.can_spawn() is False

    def test_collapsed_frees_capacity(self):
        reg = AgentRegistry(max_concurrent_agents=1)
        r1 = reg.register(TaskSpec())
        reg.transition(
            r1.agent_id,
            AgentState.COLLAPSED,
            collapse_reason=CollapseReason.COMPLETED,
        )
        assert reg.can_spawn() is True


class TestQueries:
    def test_get_active_agent(self):
        reg = AgentRegistry()
        spec = TaskSpec()
        record = reg.register(spec)
        found = reg.get(record.agent_id)
        assert found is not None
        assert found.agent_id == record.agent_id

    def test_get_archived_agent(self):
        reg = AgentRegistry()
        spec = TaskSpec()
        record = reg.register(spec)
        reg.transition(
            record.agent_id,
            AgentState.COLLAPSED,
            collapse_reason=CollapseReason.COMPLETED,
        )
        found = reg.get(record.agent_id)
        assert found is not None
        assert found.state == AgentState.COLLAPSED

    def test_get_missing_returns_none(self):
        reg = AgentRegistry()
        assert reg.get("nonexistent") is None

    def test_list_active(self):
        reg = AgentRegistry()
        r1 = reg.register(TaskSpec())
        r2 = reg.register(TaskSpec())
        reg.transition(
            r1.agent_id,
            AgentState.COLLAPSED,
            collapse_reason=CollapseReason.COMPLETED,
        )
        active = reg.list_active()
        assert len(active) == 1
        assert active[0].agent_id == r2.agent_id

    def test_list_by_state(self):
        reg = AgentRegistry()
        r1 = reg.register(TaskSpec())
        r2 = reg.register(TaskSpec())
        reg.transition(r1.agent_id, AgentState.READY)
        spawning = reg.list_by_state(AgentState.SPAWNING)
        ready = reg.list_by_state(AgentState.READY)
        assert len(spawning) == 1
        assert len(ready) == 1

    def test_active_count(self):
        reg = AgentRegistry()
        assert reg.active_count() == 0
        reg.register(TaskSpec())
        reg.register(TaskSpec())
        assert reg.active_count() == 2

    def test_get_full_roster(self):
        reg = AgentRegistry(max_concurrent_agents=5)
        r1 = reg.register(TaskSpec())
        reg.register(TaskSpec())
        reg.transition(
            r1.agent_id,
            AgentState.COLLAPSED,
            collapse_reason=CollapseReason.COMPLETED,
        )
        roster = reg.get_full_roster()
        assert roster["active_count"] == 1
        assert roster["archived_count"] == 1
        assert roster["max_capacity"] == 5


class TestCollapseAll:
    def test_collapse_all(self):
        reg = AgentRegistry()
        r1 = reg.register(TaskSpec())
        r2 = reg.register(TaskSpec())
        reg.transition(r1.agent_id, AgentState.READY)
        collapsed = reg.collapse_all(
            reason=CollapseReason.OPERATOR_KILL_ALL,
            message="Emergency shutdown",
        )
        assert len(collapsed) == 2
        assert reg.active_count() == 0

    def test_collapse_all_skips_already_collapsed(self):
        reg = AgentRegistry()
        r1 = reg.register(TaskSpec())
        r2 = reg.register(TaskSpec())
        reg.transition(
            r1.agent_id,
            AgentState.COLLAPSED,
            collapse_reason=CollapseReason.COMPLETED,
        )
        collapsed = reg.collapse_all()
        assert len(collapsed) == 1
        assert collapsed[0] == r2.agent_id


class TestInterventions:
    def test_record_intervention(self):
        reg = AgentRegistry()
        spec = TaskSpec()
        record = reg.register(spec)
        reg.record_intervention(record.agent_id, {
            "type": "pause",
            "reason": "Investigating behavior",
        })
        found = reg.get(record.agent_id)
        assert len(found.interventions) == 1
        assert found.interventions[0]["reason"] == "Investigating behavior"


class TestActionCount:
    def test_increment_action_count(self):
        reg = AgentRegistry()
        spec = TaskSpec()
        record = reg.register(spec)
        count = reg.increment_action_count(record.agent_id)
        assert count == 1
        count = reg.increment_action_count(record.agent_id)
        assert count == 2


class TestThreadSafety:
    def test_concurrent_registrations(self):
        reg = AgentRegistry(max_concurrent_agents=100)
        errors = []

        def register_agent():
            try:
                reg.register(TaskSpec())
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=register_agent) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert reg.active_count() == 50

    def test_concurrent_transitions(self):
        reg = AgentRegistry(max_concurrent_agents=10)
        records = [reg.register(TaskSpec()) for _ in range(10)]
        errors = []

        def transition_agent(record):
            try:
                reg.transition(record.agent_id, AgentState.READY)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=transition_agent, args=(r,))
            for r in records
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        ready = reg.list_by_state(AgentState.READY)
        assert len(ready) == 10
