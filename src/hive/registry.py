"""
HIVE Agent Registry — thread-safe sub-agent state management.

Maintains the roster of all active and archived sub-agents,
enforces the state machine, and tracks capacity limits.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.hive.types import (
    AgentState,
    CollapseReason,
    SubAgentRecord,
    TaskSpec,
    VALID_TRANSITIONS,
)
from src.hive.errors import (
    AgentCollapsedError,
    MaxAgentsExceededError,
)

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Thread-safe registry for HIVE sub-agents.

    Enforces the state machine, tracks capacity, and maintains
    an append-only archive of collapsed agents.
    """

    def __init__(self, max_concurrent_agents: int = 10):
        self._max = max_concurrent_agents
        self._agents: Dict[str, SubAgentRecord] = {}
        self._archive: List[SubAgentRecord] = []
        self._locks: Dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()

    # ── Registration ─────────────────────────────────────────────────

    def register(
        self,
        task_spec: TaskSpec,
        quest_id: Optional[str] = None,
        scoped_soul_hash: Optional[str] = None,
    ) -> SubAgentRecord:
        """Register a new sub-agent in SPAWNING state.

        Raises MaxAgentsExceededError if capacity is full.
        """
        with self._global_lock:
            if not self.can_spawn():
                raise MaxAgentsExceededError(self._max)

            record = SubAgentRecord(
                task_spec=task_spec,
                state=AgentState.SPAWNING,
                quest_id=quest_id,
                scoped_soul_hash=scoped_soul_hash,
            )
            record.state_history.append({
                "from": None,
                "to": AgentState.SPAWNING.value,
                "at": record.spawned_at,
            })

            self._agents[record.agent_id] = record
            self._locks[record.agent_id] = threading.Lock()

            logger.info(
                "Agent registered: id=%s, task=%s, quest=%s",
                record.agent_id, task_spec.task_id, quest_id,
            )
            return record

    # ── State Transitions ────────────────────────────────────────────

    def transition(
        self,
        agent_id: str,
        new_state: AgentState,
        collapse_reason: Optional[CollapseReason] = None,
        collapse_message: Optional[str] = None,
    ) -> SubAgentRecord:
        """Transition an agent to a new state.

        Enforces the state machine. Raises ValueError on invalid transition.
        Raises AgentCollapsedError if agent is already collapsed.
        """
        lock = self._get_lock(agent_id)
        with lock:
            record = self._agents.get(agent_id)
            if record is None:
                # Check archive — agent may already be collapsed
                for archived in self._archive:
                    if archived.agent_id == agent_id:
                        raise AgentCollapsedError(agent_id)
                raise KeyError(f"Agent {agent_id} not found in registry")
            current = record.state

            if current == AgentState.COLLAPSED:
                raise AgentCollapsedError(agent_id)

            valid = VALID_TRANSITIONS.get(current, [])
            if new_state not in valid:
                raise ValueError(
                    f"Invalid transition: {current.value} → {new_state.value} "
                    f"for agent {agent_id}"
                )

            now = datetime.now(timezone.utc).isoformat()
            record.state_history.append({
                "from": current.value,
                "to": new_state.value,
                "at": now,
            })
            record.state = new_state
            record.state_changed_at = now

            if new_state == AgentState.COLLAPSED:
                record.collapse_reason = collapse_reason
                record.collapse_message = collapse_message
                record.collapsed_at = now
                self._archive_agent(agent_id)

            logger.info(
                "Agent transition: id=%s, %s → %s",
                agent_id, current.value, new_state.value,
            )
            return record

    # ── Queries ──────────────────────────────────────────────────────

    def get(self, agent_id: str) -> Optional[SubAgentRecord]:
        """Get an agent record by ID (active or archived)."""
        with self._global_lock:
            record = self._agents.get(agent_id)
            if record:
                return record
            for archived in self._archive:
                if archived.agent_id == agent_id:
                    return archived
            return None

    def list_active(self) -> List[SubAgentRecord]:
        """List all non-collapsed agents."""
        with self._global_lock:
            return [
                r for r in self._agents.values()
                if r.state != AgentState.COLLAPSED
            ]

    def list_by_state(self, state: AgentState) -> List[SubAgentRecord]:
        """List agents in a specific state."""
        with self._global_lock:
            return [r for r in self._agents.values() if r.state == state]

    def can_spawn(self) -> bool:
        """Check if there's capacity for a new agent.

        Paused agents count toward the limit.
        """
        active_count = sum(
            1 for r in self._agents.values()
            if r.state != AgentState.COLLAPSED
        )
        return active_count < self._max

    def active_count(self) -> int:
        """Number of non-collapsed agents."""
        with self._global_lock:
            return sum(
                1 for r in self._agents.values()
                if r.state != AgentState.COLLAPSED
            )

    def get_full_roster(self) -> Dict[str, Any]:
        """Get full roster snapshot for War Room display."""
        with self._global_lock:
            active = [
                r for r in self._agents.values()
                if r.state != AgentState.COLLAPSED
            ]
            return {
                "active": active,
                "archived": list(self._archive),
                "active_count": len(active),
                "archived_count": len(self._archive),
                "max_capacity": self._max,
            }

    def get_history(self, agent_id: str) -> List[Dict[str, Any]]:
        """Get state transition history for an agent."""
        record = self.get(agent_id)
        if record is None:
            return []
        return list(record.state_history)

    # ── Bulk Operations ──────────────────────────────────────────────

    def collapse_all(
        self,
        reason: CollapseReason = CollapseReason.OPERATOR_KILL_ALL,
        message: Optional[str] = None,
    ) -> List[str]:
        """Collapse all non-collapsed agents. Returns list of collapsed IDs."""
        with self._global_lock:
            collapsed_ids = []
            agent_ids = list(self._agents.keys())

        for agent_id in agent_ids:
            lock = self._get_lock(agent_id)
            with lock:
                record = self._agents.get(agent_id)
                if record and record.state != AgentState.COLLAPSED:
                    now = datetime.now(timezone.utc).isoformat()
                    record.state_history.append({
                        "from": record.state.value,
                        "to": AgentState.COLLAPSED.value,
                        "at": now,
                    })
                    record.state = AgentState.COLLAPSED
                    record.state_changed_at = now
                    record.collapse_reason = reason
                    record.collapse_message = message
                    record.collapsed_at = now
                    collapsed_ids.append(agent_id)

        # Archive all collapsed agents
        with self._global_lock:
            for agent_id in collapsed_ids:
                self._archive_agent_unlocked(agent_id)

        logger.warning(
            "Collapse all: %d agents collapsed, reason=%s",
            len(collapsed_ids), reason.value,
        )
        return collapsed_ids

    # ── Intervention Recording ───────────────────────────────────────

    def record_intervention(
        self,
        agent_id: str,
        intervention: Dict[str, Any],
    ) -> None:
        """Record an operator intervention on an agent.

        Also checks the archive — agent may have already collapsed
        by the time the intervention is recorded (race condition).
        """
        lock = self._get_lock(agent_id)
        with lock:
            record = self._agents.get(agent_id)
            if record is None:
                # Check archive — agent may have collapsed already
                with self._global_lock:
                    for archived in self._archive:
                        if archived.agent_id == agent_id:
                            archived.interventions.append(intervention)
                            return
                raise KeyError(f"Agent {agent_id} not found in registry")
            record.interventions.append(intervention)

    # ── Action Count ─────────────────────────────────────────────────

    def increment_action_count(self, agent_id: str) -> int:
        """Increment and return the agent's action count."""
        lock = self._get_lock(agent_id)
        with lock:
            record = self._get_or_raise(agent_id)
            record.action_count += 1
            return record.action_count

    # ── Private Helpers ──────────────────────────────────────────────

    def _get_lock(self, agent_id: str) -> threading.Lock:
        """Get the per-agent lock, creating if needed."""
        with self._global_lock:
            if agent_id not in self._locks:
                self._locks[agent_id] = threading.Lock()
            return self._locks[agent_id]

    def _get_or_raise(self, agent_id: str) -> SubAgentRecord:
        """Get agent record or raise KeyError."""
        record = self._agents.get(agent_id)
        if record is None:
            raise KeyError(f"Agent {agent_id} not found in registry")
        return record

    def _archive_agent(self, agent_id: str) -> None:
        """Move a collapsed agent to the archive. Caller must hold agent lock."""
        with self._global_lock:
            self._archive_agent_unlocked(agent_id)

    def _archive_agent_unlocked(self, agent_id: str) -> None:
        """Move a collapsed agent to the archive. Caller must hold global lock."""
        record = self._agents.pop(agent_id, None)
        if record:
            self._archive.append(record)
