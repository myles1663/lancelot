"""
Tasking Schema — TaskGraph, TaskStep, and TaskRun data models.

A TaskGraph is a compiled plan with executable steps.
A TaskRun is an execution instance of a TaskGraph.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class StepType(str, Enum):
    """Types of executable steps in a TaskGraph."""
    TOOL_CALL = "TOOL_CALL"
    SKILL_CALL = "SKILL_CALL"
    FILE_EDIT = "FILE_EDIT"
    COMMAND = "COMMAND"
    VERIFY = "VERIFY"
    HUMAN_INPUT = "HUMAN_INPUT"


class RunStatus(str, Enum):
    """Lifecycle status of a TaskRun."""
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class TaskStep:
    """A single executable step in a TaskGraph."""
    step_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    type: str = StepType.TOOL_CALL.value
    inputs: Dict[str, Any] = field(default_factory=dict)
    expected_outputs: Dict[str, Any] = field(default_factory=dict)
    acceptance_check: str = ""
    risk_level: str = "LOW"
    dependencies: List[str] = field(default_factory=list)
    rollback_hint: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "type": self.type,
            "inputs": self.inputs,
            "expected_outputs": self.expected_outputs,
            "acceptance_check": self.acceptance_check,
            "risk_level": self.risk_level,
            "dependencies": self.dependencies,
            "rollback_hint": self.rollback_hint,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskStep":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class TaskGraph:
    """A compiled plan — a DAG of executable steps."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    goal: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    planner_version: str = "v1"
    steps: List[TaskStep] = field(default_factory=list)
    session_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal,
            "created_at": self.created_at,
            "planner_version": self.planner_version,
            "steps": [s.to_dict() for s in self.steps],
            "session_id": self.session_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskGraph":
        steps = [TaskStep.from_dict(s) for s in data.get("steps", [])]
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            goal=data.get("goal", ""),
            created_at=data.get("created_at", ""),
            planner_version=data.get("planner_version", "v1"),
            steps=steps,
            session_id=data.get("session_id", ""),
        )


@dataclass
class TaskRun:
    """An execution instance of a TaskGraph."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    task_graph_id: str = ""
    execution_token_id: str = ""
    status: str = RunStatus.QUEUED.value
    current_step_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    receipts_index: List[str] = field(default_factory=list)
    last_error: Optional[str] = None
    session_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "task_graph_id": self.task_graph_id,
            "execution_token_id": self.execution_token_id,
            "status": self.status,
            "current_step_id": self.current_step_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "receipts_index": self.receipts_index,
            "last_error": self.last_error,
            "session_id": self.session_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskRun":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
