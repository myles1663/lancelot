"""
Lancelot — ToolFlow Events Unit Tests
======================================
Tests for ToolFlowEvent dataclass, ToolFlowEventType enum,
and ToolFlowEmitter with mock EventBus.
"""

import time
import pytest
from unittest.mock import MagicMock, patch

from toolflow.events import ToolFlowEvent, ToolFlowEventType
from toolflow.emitter import ToolFlowEmitter, _summarize_inputs, _truncate


class TestToolFlowEventType:
    """Tests for the ToolFlowEventType enum."""

    def test_all_event_types_prefixed(self):
        """All event types start with 'toolflow.' namespace."""
        for member in ToolFlowEventType:
            assert member.value.startswith("toolflow."), f"{member.name} missing prefix"

    def test_expected_event_types_exist(self):
        """All planned event types are defined."""
        expected = [
            "QUEST_STARTED", "ITERATION_STARTED",
            "TOOL_CALL_STARTED", "TOOL_CALL_COMPLETED", "TOOL_CALL_BLOCKED",
            "ITERATION_COMPLETED", "QUEST_COMPLETED", "QUEST_FAILED",
        ]
        for name in expected:
            assert hasattr(ToolFlowEventType, name), f"Missing: {name}"

    def test_event_type_is_string(self):
        """Event types are strings (for EventBus compatibility)."""
        assert isinstance(ToolFlowEventType.QUEST_STARTED.value, str)


class TestToolFlowEvent:
    """Tests for the ToolFlowEvent dataclass."""

    def test_default_creation(self):
        """Event creates with sensible defaults."""
        event = ToolFlowEvent()
        assert event.event_id  # Non-empty UUID
        assert len(event.event_id) == 36
        assert event.quest_id == ""
        assert event.event_type == ToolFlowEventType.QUEST_STARTED.value
        assert event.timestamp > 0
        assert event.channel == "api"
        assert event.iteration == 0
        assert event.max_iterations == 10

    def test_creation_with_values(self):
        """Event accepts all parameters."""
        event = ToolFlowEvent(
            quest_id="quest-123",
            event_type=ToolFlowEventType.TOOL_CALL_STARTED.value,
            channel="telegram",
            iteration=3,
            max_iterations=10,
            tool_name="network_client",
            tool_inputs_summary="url=https://example.com",
        )
        assert event.quest_id == "quest-123"
        assert event.event_type == "toolflow.tool_call_started"
        assert event.channel == "telegram"
        assert event.iteration == 3
        assert event.tool_name == "network_client"

    def test_to_dict_omits_none(self):
        """Serialization omits None values."""
        event = ToolFlowEvent(
            quest_id="q1",
            event_type=ToolFlowEventType.QUEST_STARTED.value,
        )
        d = event.to_dict()
        assert "quest_id" in d
        assert "tool_name" not in d  # None → omitted
        assert "tool_result" not in d

    def test_to_dict_includes_non_none(self):
        """Serialization includes all non-None values."""
        event = ToolFlowEvent(
            quest_id="q1",
            event_type=ToolFlowEventType.TOOL_CALL_COMPLETED.value,
            tool_name="repo_writer",
            tool_result="SUCCESS",
        )
        d = event.to_dict()
        assert d["tool_name"] == "repo_writer"
        assert d["tool_result"] == "SUCCESS"

    def test_to_event_bus(self):
        """Conversion to EventBus Event has correct type and payload."""
        event = ToolFlowEvent(
            quest_id="q1",
            event_type=ToolFlowEventType.QUEST_COMPLETED.value,
            total_tool_calls=5,
            duration_ms=3200,
        )
        bus_event = event.to_event_bus()
        assert bus_event.type == "toolflow.quest_completed"
        assert bus_event.payload["quest_id"] == "q1"
        assert bus_event.payload["total_tool_calls"] == 5

    def test_unique_event_ids(self):
        """Each event gets a unique ID."""
        events = [ToolFlowEvent() for _ in range(10)]
        ids = {e.event_id for e in events}
        assert len(ids) == 10


class TestHelperFunctions:
    """Tests for emitter helper functions."""

    def test_truncate_short_string(self):
        """Short strings pass through unchanged."""
        assert _truncate("hello", 100) == "hello"

    def test_truncate_long_string(self):
        """Long strings get truncated with ellipsis."""
        result = _truncate("a" * 300, 200)
        assert len(result) == 200
        assert result.endswith("...")

    def test_truncate_exact_length(self):
        """Strings at exactly max_len pass through."""
        s = "a" * 200
        assert _truncate(s, 200) == s

    def test_summarize_inputs_basic(self):
        """Input summary shows key=value pairs."""
        result = _summarize_inputs({"url": "https://example.com", "method": "GET"})
        assert "url=" in result
        assert "method=" in result

    def test_summarize_inputs_redacts_secrets(self):
        """Sensitive keys get redacted."""
        result = _summarize_inputs({
            "api_token": "sk-12345",
            "password": "hunter2",
            "auth_header": "Bearer xxx",
        })
        assert "sk-12345" not in result
        assert "hunter2" not in result
        assert "Bearer xxx" not in result
        assert "***" in result

    def test_summarize_inputs_truncates_values(self):
        """Long values are truncated."""
        result = _summarize_inputs({"data": "x" * 1000})
        assert len(result) <= 200


class TestToolFlowEmitter:
    """Tests for the ToolFlowEmitter class."""

    def _make_emitter(self, enabled=True):
        bus = MagicMock()
        return ToolFlowEmitter(event_bus=bus, enabled=enabled), bus

    def test_quest_started_emits(self):
        """quest_started publishes to EventBus."""
        emitter, bus = self._make_emitter()
        emitter.quest_started("q1", channel="telegram", max_iterations=10)
        bus.publish_sync.assert_called_once()
        event = bus.publish_sync.call_args[0][0]
        assert event.type == "toolflow.quest_started"
        assert event.payload["quest_id"] == "q1"

    def test_tool_call_started_emits(self):
        """tool_call_started publishes with tool name and sanitized inputs."""
        emitter, bus = self._make_emitter()
        emitter.tool_call_started("q1", 2, "network_client",
                                  {"url": "https://example.com"}, "api")
        bus.publish_sync.assert_called_once()
        event = bus.publish_sync.call_args[0][0]
        assert event.type == "toolflow.tool_call_started"
        assert event.payload["tool_name"] == "network_client"

    def test_tool_call_completed_emits(self):
        """tool_call_completed publishes with result."""
        emitter, bus = self._make_emitter()
        emitter.tool_call_completed("q1", 2, "repo_writer", "SUCCESS",
                                    "Wrote 3 files", "api")
        bus.publish_sync.assert_called_once()
        payload = bus.publish_sync.call_args[0][0].payload
        assert payload["tool_result"] == "SUCCESS"

    def test_tool_call_blocked_emits(self):
        """tool_call_blocked publishes with approval_id."""
        emitter, bus = self._make_emitter()
        emitter.tool_call_blocked("q1", 1, "deploy", "approval-xyz", "api")
        bus.publish_sync.assert_called_once()
        payload = bus.publish_sync.call_args[0][0].payload
        assert payload["approval_id"] == "approval-xyz"

    def test_quest_completed_emits(self):
        """quest_completed publishes with summary stats."""
        emitter, bus = self._make_emitter()
        emitter.quest_completed("q1", total_calls=5, successful_calls=4,
                                duration_ms=3200, channel="api")
        payload = bus.publish_sync.call_args[0][0].payload
        assert payload["total_tool_calls"] == 5
        assert payload["successful_tool_calls"] == 4
        assert payload["duration_ms"] == 3200

    def test_quest_failed_emits(self):
        """quest_failed publishes with error message."""
        emitter, bus = self._make_emitter()
        emitter.quest_failed("q1", "Model timeout", duration_ms=5000)
        payload = bus.publish_sync.call_args[0][0].payload
        assert payload["error"] == "Model timeout"

    def test_disabled_emitter_no_op(self):
        """When disabled, no events are published."""
        emitter, bus = self._make_emitter(enabled=False)
        emitter.quest_started("q1")
        emitter.tool_call_started("q1", 1, "test", {})
        emitter.quest_completed("q1", 0, 0, 0)
        bus.publish_sync.assert_not_called()

    def test_enable_toggle(self):
        """Emitter can be enabled/disabled at runtime."""
        emitter, bus = self._make_emitter(enabled=False)
        assert not emitter.enabled
        emitter.enabled = True
        emitter.quest_started("q1")
        bus.publish_sync.assert_called_once()

    def test_emit_handles_exception(self):
        """Emitter logs but doesn't raise on EventBus failures."""
        emitter, bus = self._make_emitter()
        bus.publish_sync.side_effect = RuntimeError("No event loop")
        # Should not raise
        emitter.quest_started("q1")

    def test_iteration_events(self):
        """iteration_started and iteration_completed emit correctly."""
        emitter, bus = self._make_emitter()
        emitter.iteration_started("q1", 3, "api")
        emitter.iteration_completed("q1", 3, "api")
        assert bus.publish_sync.call_count == 2
        types = [call[0][0].type for call in bus.publish_sync.call_args_list]
        assert "toolflow.iteration_started" in types
        assert "toolflow.iteration_completed" in types
