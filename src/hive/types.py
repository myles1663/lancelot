"""
HIVE Types — core data models for the HIVE Agent Mesh.

Defines state machines, task specifications, agent records, and
intervention structures used throughout the HIVE subsystem.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, IntEnum
from typing import Any, Dict, List, Optional


# ── Agent State Machine ──────────────────────────────────────────────

class AgentState(str, Enum):
    """Sub-agent lifecycle states.

    Valid transitions:
        SPAWNING → READY → EXECUTING → COMPLETING → COLLAPSED
                             ↕ PAUSED
                   (any state) → COLLAPSED
    """
    SPAWNING = "spawning"
    READY = "ready"
    EXECUTING = "executing"
    PAUSED = "paused"
    COMPLETING = "completing"
    COLLAPSED = "collapsed"


# Valid state transitions — used by AgentRegistry to enforce the state machine.
VALID_TRANSITIONS: Dict[AgentState, List[AgentState]] = {
    AgentState.SPAWNING: [AgentState.READY, AgentState.COLLAPSED],
    AgentState.READY: [AgentState.EXECUTING, AgentState.COLLAPSED],
    AgentState.EXECUTING: [AgentState.PAUSED, AgentState.COMPLETING, AgentState.COLLAPSED],
    AgentState.PAUSED: [AgentState.EXECUTING, AgentState.COLLAPSED],
    AgentState.COMPLETING: [AgentState.COLLAPSED],
    AgentState.COLLAPSED: [],  # Terminal — no transitions out
}


class ControlMethod(str, Enum):
    """How much autonomy a sub-agent has."""
    FULLY_AUTONOMOUS = "fully_autonomous"
    SUPERVISED = "supervised"
    MANUAL_CONFIRM = "manual_confirm"


class TaskPriority(IntEnum):
    """Task priority levels (lower = more urgent)."""
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3


class InterventionType(str, Enum):
    """Types of operator intervention."""
    PAUSE = "pause"
    RESUME = "resume"
    KILL = "kill"
    MODIFY = "modify"
    KILL_ALL = "kill_all"


class CollapseReason(str, Enum):
    """Why a sub-agent was collapsed."""
    COMPLETED = "completed"
    OPERATOR_KILL = "operator_kill"
    OPERATOR_KILL_ALL = "operator_kill_all"
    SOUL_VIOLATION = "soul_violation"
    GOVERNANCE_DENIED = "governance_denied"
    TIMEOUT = "timeout"
    ERROR = "error"
    MAX_ACTIONS_EXCEEDED = "max_actions_exceeded"


# ── Task Specification ───────────────────────────────────────────────

@dataclass
class TaskSpec:
    """Specification for a single sub-agent task."""
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    description: str = ""
    control_method: ControlMethod = ControlMethod.SUPERVISED
    priority: TaskPriority = TaskPriority.NORMAL
    timeout_seconds: int = 300
    max_actions: int = 50
    allowed_apps: List[str] = field(default_factory=list)
    allowed_categories: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    parent_task_id: Optional[str] = None
    execution_group: int = 0  # Tasks in same group run concurrently


@dataclass
class DecomposedTask:
    """Result of task decomposition — a set of subtasks with ordering."""
    quest_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    goal: str = ""
    subtasks: List[TaskSpec] = field(default_factory=list)
    execution_order: List[List[str]] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    decomposed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )

    @property
    def total_subtasks(self) -> int:
        return len(self.subtasks)


# ── Agent Records ────────────────────────────────────────────────────

@dataclass
class SubAgentRecord:
    """Runtime record for a spawned sub-agent."""
    agent_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    task_spec: TaskSpec = field(default_factory=TaskSpec)
    state: AgentState = AgentState.SPAWNING
    spawned_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    state_changed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    action_count: int = 0
    collapse_reason: Optional[CollapseReason] = None
    collapse_message: Optional[str] = None
    collapsed_at: Optional[str] = None
    scoped_soul_hash: Optional[str] = None
    quest_id: Optional[str] = None
    state_history: List[Dict[str, Any]] = field(default_factory=list)
    interventions: List[Dict[str, Any]] = field(default_factory=list)


# ── Task Result ──────────────────────────────────────────────────────

@dataclass
class TaskResult:
    """Result from a sub-agent's task execution."""
    task_id: str = ""
    agent_id: str = ""
    success: bool = False
    outputs: Dict[str, Any] = field(default_factory=dict)
    error_message: Optional[str] = None
    action_count: int = 0
    duration_ms: int = 0
    collapse_reason: Optional[CollapseReason] = None


# ── Operator Intervention ────────────────────────────────────────────

@dataclass
class OperatorIntervention:
    """Record of an operator intervention on an agent or task."""
    intervention_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    intervention_type: InterventionType = InterventionType.PAUSE
    agent_id: Optional[str] = None
    task_id: Optional[str] = None
    reason: str = ""
    feedback: Optional[str] = None
    constraints: Optional[Dict[str, Any]] = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    resolved: bool = False
    resolution: Optional[str] = None
