"""Approval and proceed-message helpers for orchestrator task execution."""

from __future__ import annotations

import logging
from typing import Any

from src.core.tasking.authority import (
    format_step_requirement_issues,
    list_graph_authorities,
    validate_graph_requirements,
)


_logger = logging.getLogger("orchestrator.approval")

STRONG_PROCEED_PHRASES = (
    "proceed",
    "go ahead",
    "approved",
    "approve",
    "yes, proceed",
    "yes proceed",
    "execute",
    "run it",
    "start execution",
    "yes go ahead",
    "confirmed",
    "confirm",
)

CONTEXTUAL_PROCEED_PHRASES = (
    "do it",
    "set it up",
    "get it done",
    "make it happen",
    "wire it up",
    "hook it up",
    "let's go",
    "do this",
    "yes do it",
    "yes, do it",
    "sounds good",
    "ok sounds good",
    "okay sounds good",
    "looks good",
    "that works",
    "works for me",
    "go for it",
)

_RISK_ORDER = {"LOW": 0, "MED": 1, "HIGH": 2}


def _matches_phrase(message: str, phrases: tuple[str, ...]) -> bool:
    return any(message.startswith(phrase) or message == phrase for phrase in phrases)


def has_pending_plan(runtime: Any) -> bool:
    """Return whether the current runtime has a pending plan or task graph."""
    if getattr(runtime, "_last_plan_artifact", None) is not None:
        return True

    try:
        task_store = getattr(runtime, "task_store", None)
        if task_store:
            session_id = getattr(runtime, "_current_session_id", "")
            return task_store.get_latest_graph_for_session(session_id) is not None
    except Exception as exc:
        _logger.warning("Failed to inspect pending graph for proceed detection: %s", exc)

    return False


def is_proceed_message(runtime: Any, message: str) -> bool:
    """Detect explicit approval/proceed language."""
    lower = message.strip().lower()

    if _matches_phrase(lower, STRONG_PROCEED_PHRASES):
        return True

    return has_pending_plan(runtime) and _matches_phrase(lower, CONTEXTUAL_PROCEED_PHRASES)


def request_permission(runtime: Any, graph: Any) -> str:
    """Format a permission request for a task graph."""
    requirement_issues = validate_graph_requirements(graph.steps)
    if requirement_issues:
        return (
            "**Cannot request approval yet:** the executable plan is missing required inputs.\n\n"
            f"{format_step_requirement_issues(requirement_issues)}\n\n"
            "Please provide the missing input and I will generate a new governed execution request."
        )

    assembler = getattr(runtime, "assembler", None)
    if assembler:
        authorities = list_graph_authorities(graph.steps)
        tools_needed = set(authorities["tools"]) | set(authorities["skills"])
        return assembler.assemble_permission_request(
            what_i_will_do=[step.inputs.get("description", step.type) for step in graph.steps],
            tools_enabled=tools_needed,
            risk_tier=_highest_risk(graph.steps),
            limits={"duration": 300, "actions": len(graph.steps) * 2},
        )

    steps_desc = "\n".join(f"- {step.type}: {step.inputs}" for step in graph.steps[:5])
    return f"**Permission required** to execute {len(graph.steps)} steps:\n{steps_desc}\n\nApprove or Deny?"


def handle_approval(runtime: Any, session_id: str = "") -> str:
    """Mint execution authority after the Commander approves a pending graph."""
    minter = getattr(runtime, "minter", None)
    task_store = getattr(runtime, "task_store", None)
    if not minter or not task_store:
        return "Execution authority not available."

    graph = task_store.get_latest_graph_for_session(session_id)
    if not graph:
        return "No pending plan to approve."

    requirement_issues = validate_graph_requirements(graph.steps)
    if requirement_issues:
        return (
            "Approval was not accepted because the pending plan is incomplete.\n\n"
            f"{format_step_requirement_issues(requirement_issues)}\n\n"
            "Please provide the missing input and I will regenerate the permission request."
        )

    authorities = list_graph_authorities(graph.steps)
    operator_id, operator_name = _resolve_operator_identity(runtime, session_id)
    minter.mint_from_approval(
        scope=graph.goal,
        tools=authorities["tools"],
        skills=authorities["skills"],
        risk_tier=_highest_risk(graph.steps),
        max_actions=len(graph.steps) * 2,
        session_id=session_id,
        operator_id=operator_id,
        operator_name=operator_name,
    )

    return runtime._handle_proceed("proceed", session_id=session_id)


def _highest_risk(steps: list[Any]) -> str:
    risk_levels = [step.risk_level for step in steps]
    if not risk_levels:
        return "LOW"
    return max(risk_levels, key=lambda risk: _RISK_ORDER.get(risk, 0))


def _resolve_operator_identity(runtime: Any, session_id: str) -> tuple[str, str]:
    operator_id = ""
    operator_name = ""
    try:
        warroom_state = getattr(runtime, "warroom_state", None)
        if warroom_state and session_id:
            session = warroom_state.get_session(session_id)
            identity = session.get("operator_identity") if session else None
            if identity is not None:
                operator_id = getattr(identity, "operator_id", "") or ""
                operator_name = getattr(identity, "display_name", "") or operator_id
    except Exception as exc:
        _logger.warning("Failed to resolve operator identity for session %s: %s", session_id, exc)
    return operator_id, operator_name
