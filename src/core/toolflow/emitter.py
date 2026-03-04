# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
ToolFlowEmitter — publishes ToolFlowEvents through the EventBus.

Injected into the orchestrator. Feature-gated by FEATURE_TOOL_FLOW_STREAMING.
When disabled, all methods are no-ops with zero overhead.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from toolflow.events import ToolFlowEvent, ToolFlowEventType

logger = logging.getLogger(__name__)

# Max chars for tool input/output summaries to avoid flooding events
_MAX_SUMMARY_LEN = 200


def _truncate(text: str, max_len: int = _MAX_SUMMARY_LEN) -> str:
    """Truncate text with ellipsis."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def _summarize_inputs(inputs: Dict[str, Any]) -> str:
    """Create a sanitized summary of tool inputs (no secrets, truncated)."""
    parts = []
    for key, val in inputs.items():
        val_str = str(val)
        if any(s in key.lower() for s in ("token", "secret", "password", "key", "auth")):
            val_str = "***"
        parts.append(f"{key}={_truncate(val_str, 80)}")
    return _truncate(", ".join(parts))


class ToolFlowEmitter:
    """Emits ToolFlowEvents through the EventBus.

    All events carry quest_id for correlation with receipts.
    When FEATURE_TOOL_FLOW_STREAMING is False, _emit() is a no-op.
    """

    def __init__(self, event_bus, enabled: bool = True):
        self._event_bus = event_bus
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value

    def quest_started(self, quest_id: str, channel: str = "api",
                      max_iterations: int = 10) -> None:
        """Emit when agentic loop begins."""
        self._emit(ToolFlowEvent(
            quest_id=quest_id,
            event_type=ToolFlowEventType.QUEST_STARTED.value,
            channel=channel,
            max_iterations=max_iterations,
        ))

    def iteration_started(self, quest_id: str, iteration: int,
                          channel: str = "api") -> None:
        """Emit when a new agentic loop iteration begins."""
        self._emit(ToolFlowEvent(
            quest_id=quest_id,
            event_type=ToolFlowEventType.ITERATION_STARTED.value,
            iteration=iteration,
            channel=channel,
        ))

    def tool_call_started(self, quest_id: str, iteration: int,
                          tool_name: str, tool_inputs: Dict[str, Any],
                          channel: str = "api") -> None:
        """Emit when a tool call begins. Inputs are sanitized."""
        self._emit(ToolFlowEvent(
            quest_id=quest_id,
            event_type=ToolFlowEventType.TOOL_CALL_STARTED.value,
            iteration=iteration,
            tool_name=tool_name,
            tool_inputs_summary=_summarize_inputs(tool_inputs),
            channel=channel,
        ))

    def tool_call_completed(self, quest_id: str, iteration: int,
                            tool_name: str, result: str,
                            outputs_summary: str = "",
                            channel: str = "api") -> None:
        """Emit when a tool call completes (success or failure)."""
        self._emit(ToolFlowEvent(
            quest_id=quest_id,
            event_type=ToolFlowEventType.TOOL_CALL_COMPLETED.value,
            iteration=iteration,
            tool_name=tool_name,
            tool_result=result,
            tool_outputs_summary=_truncate(outputs_summary) if outputs_summary else None,
            channel=channel,
        ))

    def tool_call_blocked(self, quest_id: str, iteration: int,
                          tool_name: str, approval_id: str,
                          channel: str = "api") -> None:
        """Emit when a tool call is blocked pending approval."""
        self._emit(ToolFlowEvent(
            quest_id=quest_id,
            event_type=ToolFlowEventType.TOOL_CALL_BLOCKED.value,
            iteration=iteration,
            tool_name=tool_name,
            approval_id=approval_id,
            channel=channel,
        ))

    def iteration_completed(self, quest_id: str, iteration: int,
                            channel: str = "api") -> None:
        """Emit when an agentic loop iteration finishes."""
        self._emit(ToolFlowEvent(
            quest_id=quest_id,
            event_type=ToolFlowEventType.ITERATION_COMPLETED.value,
            iteration=iteration,
            channel=channel,
        ))

    def quest_completed(self, quest_id: str, total_calls: int,
                        successful_calls: int, duration_ms: int,
                        channel: str = "api") -> None:
        """Emit when the agentic loop finishes successfully."""
        self._emit(ToolFlowEvent(
            quest_id=quest_id,
            event_type=ToolFlowEventType.QUEST_COMPLETED.value,
            total_tool_calls=total_calls,
            successful_tool_calls=successful_calls,
            duration_ms=duration_ms,
            channel=channel,
        ))

    def quest_failed(self, quest_id: str, error: str,
                     duration_ms: int = 0,
                     channel: str = "api") -> None:
        """Emit when the agentic loop fails."""
        self._emit(ToolFlowEvent(
            quest_id=quest_id,
            event_type=ToolFlowEventType.QUEST_FAILED.value,
            error=error,
            duration_ms=duration_ms,
            channel=channel,
        ))

    def _emit(self, event: ToolFlowEvent) -> None:
        """Publish event via EventBus. No-op when disabled."""
        if not self._enabled:
            return
        try:
            self._event_bus.publish_sync(event.to_event_bus())
        except Exception as exc:
            logger.warning("Failed to emit toolflow event %s: %s",
                           event.event_type, exc)
