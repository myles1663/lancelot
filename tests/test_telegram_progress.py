"""
Lancelot -- Telegram ToolFlow Progress Bridge Tests
====================================================
Tests for TelegramProgressBridge lifecycle: quest start, tool call
progress updates, completion, and failure. All Telegram API calls
are mocked.
"""

import asyncio
import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from toolflow.telegram_bridge import (
    TelegramProgressBridge,
    _MAX_VISIBLE_STEPS,
    _STATUS_ICONS,
)


def _run(coro):
    """Helper to run an async coroutine synchronously."""
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(coro)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_bot():
    """A mock TelegramBot with the methods used by the bridge."""
    bot = MagicMock()
    bot.chat_id = "999888"
    bot.send_message_with_keyboard = MagicMock(return_value=42)
    bot.edit_message = MagicMock(return_value=True)
    return bot


@pytest.fixture
def bridge(mock_bot):
    """A TelegramProgressBridge with a mocked bot."""
    return TelegramProgressBridge(mock_bot)


def _make_event(event_type, quest_id="quest-1", channel="telegram", **extra):
    """Build a mock EventBus Event with payload."""
    payload = {"quest_id": quest_id, "channel": channel}
    payload.update(extra)
    event = MagicMock()
    event.type = event_type
    event.payload = payload
    return event


# ---------------------------------------------------------------------------
# Quest lifecycle tests
# ---------------------------------------------------------------------------

class TestQuestStarted:
    """Tests for quest_started event handling."""

    def test_sends_initial_message(self, bridge, mock_bot):
        """Should send a progress message when a telegram quest starts."""
        event = _make_event("toolflow.quest_started")
        _run(bridge.on_toolflow_event(event))

        mock_bot.send_message_with_keyboard.assert_called_once()
        text = mock_bot.send_message_with_keyboard.call_args[0][0]
        assert "WORKING" in text

    def test_tracks_quest_state(self, bridge, mock_bot):
        """Should store quest state with message_id."""
        event = _make_event("toolflow.quest_started")
        _run(bridge.on_toolflow_event(event))

        assert "quest-1" in bridge._active_quests
        state = bridge._active_quests["quest-1"]
        assert state["message_id"] == 42
        assert state["steps"] == []

    def test_ignores_non_telegram_channel(self, bridge, mock_bot):
        """Should skip quests from non-telegram channels."""
        event = _make_event("toolflow.quest_started", channel="api")
        _run(bridge.on_toolflow_event(event))

        mock_bot.send_message_with_keyboard.assert_not_called()
        assert "quest-1" not in bridge._active_quests

    def test_handles_send_failure(self, bridge, mock_bot):
        """Should not track quest if initial message send fails."""
        mock_bot.send_message_with_keyboard.return_value = None

        event = _make_event("toolflow.quest_started")
        _run(bridge.on_toolflow_event(event))

        assert "quest-1" not in bridge._active_quests


# ---------------------------------------------------------------------------
# Tool call progress tests
# ---------------------------------------------------------------------------

class TestToolCallStarted:
    """Tests for tool_call_started event handling."""

    def test_appends_step_and_edits(self, bridge, mock_bot):
        """Should add a step and edit the progress message."""
        # Start a quest first
        _run(bridge.on_toolflow_event(_make_event("toolflow.quest_started")))

        # Tool call started
        event = _make_event(
            "toolflow.tool_call_started",
            tool_name="network_client",
            tool_inputs_summary="url=https://example.com",
            iteration=1,
        )
        _run(bridge.on_toolflow_event(event))

        state = bridge._active_quests["quest-1"]
        assert len(state["steps"]) == 1
        assert state["steps"][0]["tool_name"] == "network_client"
        assert state["steps"][0]["status"] == "running"

        mock_bot.edit_message.assert_called_once()
        text = mock_bot.edit_message.call_args[0][1]
        assert "network_client" in text

    def test_ignores_unknown_quest(self, bridge, mock_bot):
        """Should skip if quest_id is not being tracked."""
        event = _make_event("toolflow.tool_call_started", quest_id="unknown")
        _run(bridge.on_toolflow_event(event))

        mock_bot.edit_message.assert_not_called()


class TestToolCallCompleted:
    """Tests for tool_call_completed event handling."""

    def test_updates_step_status(self, bridge, mock_bot):
        """Should mark the running step as done."""
        _run(bridge.on_toolflow_event(_make_event("toolflow.quest_started")))
        _run(bridge.on_toolflow_event(_make_event(
            "toolflow.tool_call_started",
            tool_name="repo_writer", iteration=1,
        )))

        # Complete the tool call
        event = _make_event(
            "toolflow.tool_call_completed",
            tool_name="repo_writer",
            tool_result="SUCCESS",
            tool_outputs_summary="Wrote 3 files",
        )
        _run(bridge.on_toolflow_event(event))

        state = bridge._active_quests["quest-1"]
        assert state["steps"][0]["status"] == "done"
        assert state["steps"][0]["result"] == "Wrote 3 files"

    def test_marks_failure(self, bridge, mock_bot):
        """Should mark step as failed when result is FAILURE."""
        _run(bridge.on_toolflow_event(_make_event("toolflow.quest_started")))
        _run(bridge.on_toolflow_event(_make_event(
            "toolflow.tool_call_started",
            tool_name="network_client", iteration=1,
        )))

        event = _make_event(
            "toolflow.tool_call_completed",
            tool_name="network_client",
            tool_result="FAILURE",
        )
        _run(bridge.on_toolflow_event(event))

        state = bridge._active_quests["quest-1"]
        assert state["steps"][0]["status"] == "failed"


class TestToolCallBlocked:
    """Tests for tool_call_blocked event handling."""

    def test_marks_step_blocked(self, bridge, mock_bot):
        """Should mark step as blocked pending approval."""
        _run(bridge.on_toolflow_event(_make_event("toolflow.quest_started")))
        _run(bridge.on_toolflow_event(_make_event(
            "toolflow.tool_call_started",
            tool_name="deploy", iteration=1,
        )))

        event = _make_event(
            "toolflow.tool_call_blocked",
            tool_name="deploy",
            approval_id="ap-123",
        )
        _run(bridge.on_toolflow_event(event))

        state = bridge._active_quests["quest-1"]
        assert state["steps"][0]["status"] == "blocked"


# ---------------------------------------------------------------------------
# Quest completion / failure tests
# ---------------------------------------------------------------------------

class TestQuestCompleted:
    """Tests for quest_completed event handling."""

    def test_final_edit_and_cleanup(self, bridge, mock_bot):
        """Should send final edit with summary and clean up state."""
        _run(bridge.on_toolflow_event(_make_event("toolflow.quest_started")))

        event = _make_event(
            "toolflow.quest_completed",
            total_tool_calls=3,
            successful_tool_calls=3,
            duration_ms=2500,
        )
        _run(bridge.on_toolflow_event(event))

        # Should have edited with completion text
        text = mock_bot.edit_message.call_args[0][1]
        assert "COMPLETE" in text
        assert "3/3" in text
        assert "2500ms" in text

        # Quest should be cleaned up
        assert "quest-1" not in bridge._active_quests

    def test_cleanup_on_untracked_quest(self, bridge, mock_bot):
        """Should not raise for untracked quest_id."""
        event = _make_event("toolflow.quest_completed", quest_id="unknown")
        _run(bridge.on_toolflow_event(event))
        # Should not raise


class TestQuestFailed:
    """Tests for quest_failed event handling."""

    def test_final_edit_with_error(self, bridge, mock_bot):
        """Should send final edit with error message and clean up."""
        _run(bridge.on_toolflow_event(_make_event("toolflow.quest_started")))

        event = _make_event(
            "toolflow.quest_failed",
            error="Model timeout after 30s",
        )
        _run(bridge.on_toolflow_event(event))

        text = mock_bot.edit_message.call_args[0][1]
        assert "FAILED" in text
        assert "Model timeout" in text
        assert "quest-1" not in bridge._active_quests


# ---------------------------------------------------------------------------
# Progress text building tests
# ---------------------------------------------------------------------------

class TestBuildProgressText:
    """Tests for the _build_progress_text formatter."""

    def test_running_header(self, bridge):
        """Running status shows WORKING header."""
        state = {"steps": []}
        text = bridge._build_progress_text(state, status="running")
        assert "WORKING" in text

    def test_completed_header(self, bridge):
        """Completed status shows COMPLETE header."""
        state = {"steps": []}
        text = bridge._build_progress_text(state, status="completed")
        assert "COMPLETE" in text

    def test_failed_header(self, bridge):
        """Failed status shows FAILED header."""
        state = {"steps": []}
        text = bridge._build_progress_text(state, status="failed")
        assert "FAILED" in text

    def test_shows_steps_with_status_icons(self, bridge):
        """Steps should show status icons and tool names."""
        state = {
            "steps": [
                {"tool_name": "search", "status": "done", "inputs_summary": "", "result": "ok"},
                {"tool_name": "write", "status": "running", "inputs_summary": "", "result": None},
            ],
        }
        text = bridge._build_progress_text(state, status="running")
        assert "[OK]" in text
        assert "search" in text
        assert "..." in text
        assert "write" in text

    def test_truncates_to_max_visible_steps(self, bridge):
        """Should only show last N steps and indicate hidden count."""
        steps = [
            {"tool_name": f"tool_{i}", "status": "done", "inputs_summary": "", "result": "ok"}
            for i in range(_MAX_VISIBLE_STEPS + 3)
        ]
        state = {"steps": steps}
        text = bridge._build_progress_text(state, status="running")

        assert "earlier step(s) omitted" in text
        # Should show last _MAX_VISIBLE_STEPS tools
        assert f"tool_{_MAX_VISIBLE_STEPS + 2}" in text
        # Should NOT show the very first tool
        assert "tool_0" not in text

    def test_shows_inputs_summary(self, bridge):
        """Should show truncated inputs for each step."""
        state = {
            "steps": [
                {
                    "tool_name": "network_client",
                    "status": "running",
                    "inputs_summary": "url=https://example.com/api/data",
                    "result": None,
                },
            ],
        }
        text = bridge._build_progress_text(state, status="running")
        assert "url=https://example.com" in text

    def test_summary_line(self, bridge):
        """Should include summary when provided."""
        state = {"steps": []}
        text = bridge._build_progress_text(
            state, status="completed",
            summary="Done: 5/5 tools succeeded (3200ms)",
        )
        assert "Done: 5/5 tools succeeded (3200ms)" in text


# ---------------------------------------------------------------------------
# Event filtering tests
# ---------------------------------------------------------------------------

class TestEventFiltering:
    """Tests for non-toolflow event handling."""

    def test_ignores_non_toolflow_events(self, bridge, mock_bot):
        """Should silently ignore events without toolflow. prefix."""
        event = _make_event("health_change", quest_id="q1")
        event.type = "health_change"
        _run(bridge.on_toolflow_event(event))

        mock_bot.send_message_with_keyboard.assert_not_called()
        mock_bot.edit_message.assert_not_called()

    def test_ignores_empty_quest_id(self, bridge, mock_bot):
        """Should skip events with no quest_id."""
        event = _make_event("toolflow.quest_started", quest_id="")
        _run(bridge.on_toolflow_event(event))

        mock_bot.send_message_with_keyboard.assert_not_called()

    def test_handles_exception_in_handler(self, bridge, mock_bot):
        """Should catch and log exceptions without raising."""
        mock_bot.send_message_with_keyboard.side_effect = RuntimeError("Network error")

        event = _make_event("toolflow.quest_started")
        # Should not raise
        _run(bridge.on_toolflow_event(event))


# ---------------------------------------------------------------------------
# Multi-step lifecycle test
# ---------------------------------------------------------------------------

class TestFullLifecycle:
    """End-to-end lifecycle test with multiple tool calls."""

    def test_full_quest_lifecycle(self, bridge, mock_bot):
        """Test complete lifecycle: start -> 3 tool calls -> complete."""
        # 1. Quest starts
        _run(bridge.on_toolflow_event(_make_event("toolflow.quest_started")))
        assert mock_bot.send_message_with_keyboard.call_count == 1

        # 2. First tool call
        _run(bridge.on_toolflow_event(_make_event(
            "toolflow.tool_call_started",
            tool_name="search", iteration=1,
        )))
        assert mock_bot.edit_message.call_count == 1

        # 3. First tool completes
        _run(bridge.on_toolflow_event(_make_event(
            "toolflow.tool_call_completed",
            tool_name="search", tool_result="SUCCESS",
        )))
        assert mock_bot.edit_message.call_count == 2

        # 4. Second tool call
        _run(bridge.on_toolflow_event(_make_event(
            "toolflow.tool_call_started",
            tool_name="repo_writer", iteration=2,
        )))
        assert mock_bot.edit_message.call_count == 3

        # 5. Second tool completes
        _run(bridge.on_toolflow_event(_make_event(
            "toolflow.tool_call_completed",
            tool_name="repo_writer", tool_result="SUCCESS",
        )))
        assert mock_bot.edit_message.call_count == 4

        # 6. Quest completes
        _run(bridge.on_toolflow_event(_make_event(
            "toolflow.quest_completed",
            total_tool_calls=2, successful_tool_calls=2, duration_ms=1500,
        )))
        assert mock_bot.edit_message.call_count == 5
        assert "quest-1" not in bridge._active_quests

        # Verify the final message contains the summary
        final_text = mock_bot.edit_message.call_args[0][1]
        assert "COMPLETE" in final_text
        assert "2/2" in final_text
