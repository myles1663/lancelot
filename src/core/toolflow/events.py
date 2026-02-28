# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under AGPL-3.0. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
ToolFlowEvent — data structures for real-time tool execution progress.

These events are emitted during the agentic loop and represent actual progress
(not simulated). They carry the quest_id for grouping with receipts.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class ToolFlowEventType(str, Enum):
    """Types of tool flow progress events."""
    QUEST_STARTED = "toolflow.quest_started"
    ITERATION_STARTED = "toolflow.iteration_started"
    TOOL_CALL_STARTED = "toolflow.tool_call_started"
    TOOL_CALL_COMPLETED = "toolflow.tool_call_completed"
    TOOL_CALL_BLOCKED = "toolflow.tool_call_blocked"
    ITERATION_COMPLETED = "toolflow.iteration_completed"
    QUEST_COMPLETED = "toolflow.quest_completed"
    QUEST_FAILED = "toolflow.quest_failed"


@dataclass
class ToolFlowEvent:
    """A single tool flow progress event emitted during the agentic loop."""

    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    quest_id: str = ""
    event_type: str = ToolFlowEventType.QUEST_STARTED.value
    timestamp: float = field(default_factory=time.time)
    channel: str = "api"

    # Iteration tracking
    iteration: int = 0
    max_iterations: int = 10

    # Tool call details (for TOOL_CALL_* events)
    tool_name: Optional[str] = None
    tool_inputs_summary: Optional[str] = None
    tool_result: Optional[str] = None
    tool_outputs_summary: Optional[str] = None

    # Blocked details (for TOOL_CALL_BLOCKED)
    approval_id: Optional[str] = None

    # Quest summary (for QUEST_COMPLETED / QUEST_FAILED)
    total_tool_calls: int = 0
    successful_tool_calls: int = 0
    duration_ms: Optional[int] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary, omitting None values."""
        return {k: v for k, v in self.__dict__.items() if v is not None}

    def to_event_bus(self):
        """Convert to an EventBus Event for broadcasting."""
        from event_bus import Event
        return Event(
            type=self.event_type,
            payload=self.to_dict(),
            timestamp=self.timestamp,
        )
