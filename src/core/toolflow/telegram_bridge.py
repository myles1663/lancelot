# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under AGPL-3.0. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
TelegramProgressBridge — bridges ToolFlow events to Telegram message editing.

For each quest_id, sends one progress message and edits it as tool calls
progress through the agentic loop. Subscribes to toolflow.* events via EventBus.

Only shows the last 5 steps to avoid hitting Telegram's 4096-char limit.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Maximum number of tool steps to display (avoids Telegram char limit)
_MAX_VISIBLE_STEPS = 5

# Status indicators (Telegram Markdown v1 safe — no square brackets)
_STATUS_ICONS = {
    "running": "⏳",
    "done": "✅",
    "failed": "❌",
    "blocked": "🔒",
}


class TelegramProgressBridge:
    """Bridges ToolFlow events to Telegram message editing.

    For each quest_id, sends one message and edits it as tool calls progress.
    Subscribes to toolflow.* events via EventBus.
    """

    def __init__(self, telegram_bot):
        self._bot = telegram_bot
        # quest_id -> {"message_id": int, "chat_id": str, "steps": list, "started_at": float}
        self._active_quests: Dict[str, Dict[str, Any]] = {}

    async def on_toolflow_event(self, event) -> None:
        """EventBus subscriber callback (async).

        Routes each toolflow.* event type to the appropriate handler.
        Non-toolflow events are silently ignored.
        """
        event_type = event.type
        payload = event.payload

        if not event_type.startswith("toolflow."):
            return

        quest_id = payload.get("quest_id", "")
        if not quest_id:
            return

        try:
            if event_type == "toolflow.quest_started":
                self._on_quest_started(quest_id, payload)
            elif event_type == "toolflow.tool_call_started":
                self._on_tool_call_started(quest_id, payload)
            elif event_type == "toolflow.tool_call_completed":
                self._on_tool_call_completed(quest_id, payload)
            elif event_type == "toolflow.tool_call_blocked":
                self._on_tool_call_blocked(quest_id, payload)
            elif event_type == "toolflow.quest_completed":
                self._on_quest_completed(quest_id, payload)
            elif event_type == "toolflow.quest_failed":
                self._on_quest_failed(quest_id, payload)
        except Exception as exc:
            logger.warning("TelegramProgressBridge: Error handling %s: %s", event_type, exc)

    def _on_quest_started(self, quest_id: str, payload: Dict[str, Any]) -> None:
        """Send initial progress message when a quest begins."""
        channel = payload.get("channel", "api")
        if channel != "telegram":
            return

        state = {
            "message_id": None,
            "chat_id": self._bot.chat_id,
            "steps": [],
            "started_at": time.time(),
        }

        text = self._build_progress_text(state, quest_id=quest_id, status="running")
        message_id = self._bot.send_message_with_keyboard(text, keyboard=None, chat_id=state["chat_id"])

        if message_id:
            state["message_id"] = message_id
            self._active_quests[quest_id] = state
            logger.debug("TelegramProgressBridge: Quest %s started, message_id=%s", quest_id, message_id)
        else:
            logger.warning("TelegramProgressBridge: Failed to send initial message for quest %s", quest_id)

    def _on_tool_call_started(self, quest_id: str, payload: Dict[str, Any]) -> None:
        """Append a new step and edit the progress message."""
        state = self._active_quests.get(quest_id)
        if not state or not state.get("message_id"):
            return

        tool_name = payload.get("tool_name", "unknown")
        inputs_summary = payload.get("tool_inputs_summary", "")
        iteration = payload.get("iteration", 0)

        step = {
            "tool_name": tool_name,
            "inputs_summary": inputs_summary,
            "iteration": iteration,
            "status": "running",
            "result": None,
        }
        state["steps"].append(step)

        text = self._build_progress_text(state, quest_id=quest_id, status="running")
        self._bot.edit_message(state["message_id"], text, chat_id=state["chat_id"])

    def _on_tool_call_completed(self, quest_id: str, payload: Dict[str, Any]) -> None:
        """Update the current step status and edit the progress message."""
        state = self._active_quests.get(quest_id)
        if not state or not state.get("message_id"):
            return

        tool_name = payload.get("tool_name", "unknown")

        # Find the matching running step (most recent with this tool_name)
        for step in reversed(state["steps"]):
            if step["tool_name"] == tool_name and step["status"] == "running":
                result = payload.get("tool_result", "")
                step["status"] = "done" if result != "FAILURE" else "failed"
                step["result"] = payload.get("tool_outputs_summary", result)
                break

        text = self._build_progress_text(state, quest_id=quest_id, status="running")
        self._bot.edit_message(state["message_id"], text, chat_id=state["chat_id"])

    def _on_tool_call_blocked(self, quest_id: str, payload: Dict[str, Any]) -> None:
        """Mark a step as blocked (pending approval)."""
        state = self._active_quests.get(quest_id)
        if not state or not state.get("message_id"):
            return

        tool_name = payload.get("tool_name", "unknown")

        for step in reversed(state["steps"]):
            if step["tool_name"] == tool_name and step["status"] == "running":
                step["status"] = "blocked"
                step["result"] = "Pending approval"
                break

        text = self._build_progress_text(state, quest_id=quest_id, status="running")
        self._bot.edit_message(state["message_id"], text, chat_id=state["chat_id"])

    def _on_quest_completed(self, quest_id: str, payload: Dict[str, Any]) -> None:
        """Final edit with completion summary, then clean up."""
        state = self._active_quests.get(quest_id)
        if not state or not state.get("message_id"):
            # Clean up just in case
            self._active_quests.pop(quest_id, None)
            return

        total_calls = payload.get("total_tool_calls", 0)
        successful = payload.get("successful_tool_calls", 0)
        duration_ms = payload.get("duration_ms", 0)

        text = self._build_progress_text(
            state, quest_id=quest_id, status="completed",
            summary=f"Done: {successful}/{total_calls} tools succeeded ({duration_ms}ms)",
        )
        self._bot.edit_message(state["message_id"], text, chat_id=state["chat_id"])

        # Cleanup
        self._active_quests.pop(quest_id, None)

    def _on_quest_failed(self, quest_id: str, payload: Dict[str, Any]) -> None:
        """Final edit with failure message, then clean up."""
        state = self._active_quests.get(quest_id)
        if not state or not state.get("message_id"):
            self._active_quests.pop(quest_id, None)
            return

        error = payload.get("error", "Unknown error")

        text = self._build_progress_text(
            state, quest_id=quest_id, status="failed",
            summary=f"Failed: {error}",
        )
        self._bot.edit_message(state["message_id"], text, chat_id=state["chat_id"])

        # Cleanup
        self._active_quests.pop(quest_id, None)

    def _build_progress_text(
        self,
        state: Dict[str, Any],
        quest_id: str = "",
        status: str = "running",
        summary: Optional[str] = None,
    ) -> str:
        """Build Markdown progress text from quest state.

        Only shows the last _MAX_VISIBLE_STEPS steps to stay within
        Telegram's 4096-character message limit.
        """
        lines = []

        # Header
        if status == "completed":
            lines.append("✅ Quest finished")
        elif status == "failed":
            lines.append("❌ Quest did not complete")
        else:
            lines.append("⏳ Processing your request...")

        # Steps (show only the most recent N)
        steps = state.get("steps", [])
        visible_steps = steps[-_MAX_VISIBLE_STEPS:]
        hidden_count = len(steps) - len(visible_steps)

        if hidden_count > 0:
            lines.append(f"  ... {hidden_count} earlier step(s) omitted")

        for step in visible_steps:
            icon = _STATUS_ICONS.get(step["status"], "?")
            tool_line = f"  {icon} {step['tool_name']}"
            if step.get("inputs_summary"):
                # Truncate long inputs for display
                inputs = step["inputs_summary"][:80]
                tool_line += f" ({inputs})"
            lines.append(tool_line)

        # Summary line
        if summary:
            lines.append("")
            lines.append(summary)

        text = "\n".join(lines)
        # Replace underscores with spaces for clean display
        # (Telegram Markdown v1 does NOT support \_ escaping — only MarkdownV2 does)
        text = text.replace("_", " ")
        return text
