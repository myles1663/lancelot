"""
War Room Artifact — structured verbose output for the War Room.

Types:
    PLAN_ARTIFACT_FULL, ASSUMPTIONS, DECISION_POINTS, RISKS,
    TASK_GRAPH, TASK_RUN_TIMELINE, VERIFIER_REPORT, TOOL_TRACE,
    SECURITY_ENFORCEMENT_LOG
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


class ArtifactType(str, Enum):
    """Types of War Room artifacts."""
    PLAN_ARTIFACT_FULL = "PLAN_ARTIFACT_FULL"
    ASSUMPTIONS = "ASSUMPTIONS"
    DECISION_POINTS = "DECISION_POINTS"
    RISKS = "RISKS"
    TASK_GRAPH = "TASK_GRAPH"
    TASK_RUN_TIMELINE = "TASK_RUN_TIMELINE"
    VERIFIER_REPORT = "VERIFIER_REPORT"
    TOOL_TRACE = "TOOL_TRACE"
    SECURITY_ENFORCEMENT_LOG = "SECURITY_ENFORCEMENT_LOG"
    RESEARCH_REPORT = "RESEARCH_REPORT"  # V29: Auto-document for long research content


@dataclass
class WarRoomArtifact:
    """A structured artifact persisted for the War Room."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: str = ArtifactType.TOOL_TRACE.value
    content: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    session_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WarRoomArtifact":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
