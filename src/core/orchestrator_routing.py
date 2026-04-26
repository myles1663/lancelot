"""Intent and routing helpers for the Lancelot orchestrator.

This module holds routing decisions that depend on conversation state but do
not need to live inside the orchestration shell. Keeping these helpers here
makes the boundary explicit: classification is allowed to inspect runtime
state, but it should not execute tools or mutate task state except for the
explicit user profile update path.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

from orch_helpers.intent_helpers import (
    extract_literal_terms as _extract_literal_terms,
    is_conversational as _is_conversational,
    is_continuation as _is_continuation,
    is_low_risk_exec as _is_low_risk_exec,
    needs_research as _needs_research,
    wants_action as _wants_action,
)
from orchestrator_ext import _verify_intent_with_llm as _verify_intent_with_llm_impl
from plan_types import PlanArtifact, RiskItem

_logger = logging.getLogger(__name__)


SIMPLE_ACTION_MAP = {
    "file_writer": [
        "create a file",
        "create file",
        "make a file",
        "write a file",
        "write file",
        "create a new file",
        "make file",
    ],
    "telegram": [
        "send a message to telegram",
        "send telegram",
        "message on telegram",
        "send a telegram message",
        "telegram message",
    ],
    "email": [
        "send an email",
        "send email",
        "email to",
        "send a mail",
    ],
    "command_runner": [
        "run command",
        "execute command",
        "run script",
        "run a command",
        "execute a command",
        "run a script",
    ],
}


def is_simple_for_local(runtime: Any, prompt: str) -> bool:
    """Return whether the local model should handle a prompt.

    The local lane is intentionally conservative. Continuations and requests
    that need full identity, tool, or research context stay on the frontier
    lane even if the text is short.
    """
    if len(prompt) > 500:
        return False

    if runtime._is_continuation(prompt):
        return False

    prompt_lower = prompt.lower()

    complex_keywords = {
        "plan",
        "architect",
        "analyze",
        "compare",
        "strategy",
        "debug",
        "refactor",
        "design",
        "evaluate",
        "explain",
        "research",
        "investigate",
        "build",
        "implement",
        "create",
        "write code",
        "deploy",
        "migrate",
        "figure out",
        "find out",
        "find a way",
        "look into",
        "explore",
        "recommend",
        "options for",
        "realtime",
        "real-time",
        "voice chat",
        "voice call",
        "tell me about",
        "describe your",
        "how do you",
        "how does your",
        "what is your",
        "your memory",
        "your architecture",
        "about yourself",
        "code",
        "prompt",
        "claude",
        "look at",
        "review",
        "assess",
    }
    if any(keyword in prompt_lower for keyword in complex_keywords):
        return False

    simple_keywords = {
        "status",
        "check",
        "list",
        "what time",
        "version",
        "running",
        "health",
        "uptime",
        "ls",
        "who",
        "show",
        "what services",
        "docker",
        "disk",
        "memory usage",
        "how much",
        "is it running",
        "what is the",
    }
    return any(keyword in prompt_lower for keyword in simple_keywords)


def needs_research(prompt: str) -> bool:
    return _needs_research(prompt)


def wants_action(prompt: str) -> bool:
    return _wants_action(prompt)


def is_low_risk_exec(prompt: str) -> bool:
    return _is_low_risk_exec(prompt)


def build_simple_action_plan(user_message: str, action_map: dict[str, list[str]] | None = None):
    """Build a targeted PlanArtifact for unambiguous single-skill requests."""
    action_map = action_map or SIMPLE_ACTION_MAP
    msg_lower = user_message.lower()
    matched_skill = None
    for skill, patterns in action_map.items():
        if any(pattern in msg_lower for pattern in patterns):
            matched_skill = skill
            break

    if not matched_skill:
        return None

    _logger.debug(
        "simple_action_short_circuit",
        extra={"skill": matched_skill},
    )

    goal = user_message.strip()
    if goal and not goal.endswith((".", "!", "?")):
        goal += "."

    return PlanArtifact(
        goal=goal,
        context=[f"Single-action request mapped to skill: {matched_skill}"],
        assumptions=["User request is a straightforward single-skill operation."],
        plan_steps=[
            user_message.strip(),
            "Verify the operation completed successfully",
            "Report the result to the user",
        ],
        decision_points=["Confirm the action details before execution"],
        risks=[
            RiskItem(
                risk="Action may have unintended side effects",
                mitigation="Permission gate ensures user approval before execution",
            )
        ],
        done_when=[f"The requested action ({matched_skill}) has been completed and confirmed"],
        next_action=user_message.strip(),
    )


def extract_literal_terms(text: str) -> list:
    return _extract_literal_terms(text)


def is_conversational(prompt: str) -> bool:
    return _is_conversational(prompt)


def check_name_update(runtime: Any, message: str) -> None:
    """Persist explicit user-name updates into USER.md."""
    msg_lower = message.lower().strip()
    match = re.match(
        r"(?:call me|my name is|i'm|i am|please call me|you can call me)\s+([A-Za-z][A-Za-z\s]{0,30})",
        msg_lower,
    )
    if not match:
        return

    new_name = match.group(1).strip().title()
    if not new_name or len(new_name) < 2:
        return

    user_md_path = os.path.join(runtime.data_dir, "USER.md")
    try:
        if not os.path.exists(user_md_path):
            return

        with open(user_md_path, "r", encoding="utf-8") as handle:
            content = handle.read()

        updated = re.sub(
            r"^(- Name:\s*).*$",
            f"\\g<1>{new_name}",
            content,
            flags=re.MULTILINE,
        )
        if updated == content:
            return

        with open(user_md_path, "w", encoding="utf-8") as handle:
            handle.write(updated)

        context_env = getattr(runtime, "context_env", None)
        if context_env is not None:
            context_env.read_file("USER.md")

        _logger.info(
            "user_name_updated",
            extra={"new_name": new_name},
        )
    except Exception as exc:
        _logger.warning(
            "user_name_update_failed",
            extra={"error": str(exc), "path": user_md_path},
        )


def previous_was_substantive(runtime: Any) -> bool:
    """Return whether recent conversation state should keep routing on frontier."""
    context_env = getattr(runtime, "context_env", None)
    if context_env is None:
        return False

    history = getattr(context_env, "history", [])
    if len(history) < 2:
        return False

    for entry in history[-2:]:
        content = entry.get("content", "")
        role = entry.get("role", "")
        if role == "assistant" and len(content) > 200:
            return True
        if role == "assistant" and any(
            marker in content
            for marker in (
                "scheduled",
                "created",
                "executed",
                "searched",
                "fetched",
                "Tool:",
                "Result:",
                "ACTION:",
                "SKILL:",
            )
        ):
            return True

    receipts = getattr(context_env, "receipts", None)
    if receipts:
        now = time.time()
        if any(now - receipt.get("timestamp", 0) < 120 for receipt in receipts[-5:]):
            return True

    return False


def is_continuation(message: str) -> bool:
    return _is_continuation(message)


def verify_intent_with_llm(runtime: Any, user_message: str, keyword_intent: Any) -> Any:
    return _verify_intent_with_llm_impl(runtime, user_message, keyword_intent)
