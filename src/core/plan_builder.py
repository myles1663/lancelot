"""
Plan Builder — Assumption-Bounded Plan Builder.
================================================

Produces a valid PlanArtifact for PLAN_REQUEST inputs.
Never stalls — if information is missing, it proceeds with explicit
assumptions and includes verification steps in the plan.

Core rules:
    1. Always returns a valid PlanArtifact (passes validate_plan_artifact).
    2. Planning must not stall — no waiting states.
    3. Missing info → explicit assumption + verification step.
    4. Never produces "DRAFT:" or planner-internal markers.

Public API:
    build_plan_artifact(user_text, env_context=None) -> PlanArtifact
"""

from __future__ import annotations

import re
from dataclasses import field
from typing import Dict, List, Optional

from plan_types import (
    PlanArtifact,
    RiskItem,
    validate_plan_artifact,
    assert_no_planner_leakage,
)


# =============================================================================
# Environment Context
# =============================================================================


class EnvContext:
    """
    Describes the environment in which planning occurs.

    Carries known facts, available tools, and constraints that the
    plan builder can use to avoid unnecessary assumptions.
    """

    def __init__(
        self,
        available_tools: Optional[List[str]] = None,
        known_facts: Optional[Dict[str, str]] = None,
        constraints: Optional[List[str]] = None,
        os_info: Optional[str] = None,
    ):
        self.available_tools = available_tools or []
        self.known_facts = known_facts or {}
        self.constraints = constraints or []
        self.os_info = os_info or "Unknown"


# =============================================================================
# Text Analysis Helpers
# =============================================================================

def _extract_goal(user_text: str) -> str:
    """Extract a goal statement from user text."""
    text = user_text.strip()
    # Remove common prefixes
    for prefix in [
        "plan ", "design ", "create a plan for ", "make a plan for ",
        "how would we ", "how should we ", "how would you ",
        "come up with a plan for ", "draft a plan for ",
        "outline ", "propose ", "develop a strategy for ",
    ]:
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
            break

    # Capitalize first letter, ensure it ends with a period
    if text:
        text = text[0].upper() + text[1:]
    if text and not text.endswith((".","!","?")):
        text += "."

    return text


def _extract_keywords(text: str) -> List[str]:
    """Extract meaningful keywords from text for context building."""
    # Simple extraction — split on whitespace, filter short/common words
    stop_words = {
        "a", "an", "the", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "can", "shall",
        "to", "of", "in", "for", "on", "with", "at", "by", "from",
        "up", "about", "into", "through", "during", "before", "after",
        "above", "below", "between", "out", "off", "over", "under",
        "again", "further", "then", "once", "and", "but", "or", "nor",
        "not", "so", "it", "its", "this", "that", "these", "those",
        "i", "me", "my", "we", "our", "you", "your", "he", "she",
        "they", "them", "their", "how", "what", "which", "who",
        "plan", "design", "create", "make", "implement",
    }
    words = re.findall(r"[a-z]+", text.lower())
    return [w for w in words if len(w) > 2 and w not in stop_words]


# =============================================================================
# Plan Builder
# =============================================================================


def build_plan_artifact(
    user_text: str,
    env_context: Optional[EnvContext] = None,
) -> PlanArtifact:
    """
    Build a valid PlanArtifact from user text.

    Always returns a complete, valid artifact. Never stalls.
    If information is missing, proceeds with explicit assumptions
    and includes verification steps.

    Args:
        user_text: The raw user planning request.
        env_context: Optional environment context with known facts/tools.

    Returns:
        A PlanArtifact that passes validate_plan_artifact().
    """
    ctx = env_context or EnvContext()

    # ── Goal ──────────────────────────────────────────────────────────
    goal = _extract_goal(user_text)
    if not goal.strip():
        goal = "Accomplish the requested task."

    # ── Context ───────────────────────────────────────────────────────
    context: List[str] = []

    if ctx.os_info and ctx.os_info != "Unknown":
        context.append(f"Operating environment: {ctx.os_info}")

    if ctx.available_tools:
        context.append(f"Available tools: {', '.join(ctx.available_tools)}")

    for key, value in ctx.known_facts.items():
        context.append(f"{key}: {value}")

    if ctx.constraints:
        for c in ctx.constraints:
            context.append(f"Constraint: {c}")

    # Always add at least one context item derived from the request
    keywords = _extract_keywords(user_text)
    if keywords:
        context.append(f"Key topics: {', '.join(keywords[:6])}")

    if not context:
        context.append("Request received; no additional environment context provided.")

    # ── Assumptions ───────────────────────────────────────────────────
    assumptions: List[str] = []

    # If env context is sparse, add explicit assumptions
    if not ctx.known_facts:
        assumptions.append(
            "Assumption: Specific environment details have not been verified — "
            "plan proceeds with general best practices."
        )

    if not ctx.available_tools:
        assumptions.append(
            "Assumption: Required tooling is available or can be installed."
        )

    if not assumptions:
        assumptions.append(
            "Assumption: Environment context provided is accurate and current."
        )

    # ── Plan Steps ────────────────────────────────────────────────────
    plan_steps: List[str] = [
        f"Analyze requirements: Review and clarify the objective — {goal.rstrip('.')}",
        "Gather information: Collect any missing details and verify assumptions",
        "Design solution: Outline the approach based on requirements and constraints",
        "Validate approach: Review the proposed solution against acceptance criteria",
        "Execute plan: Implement the solution step by step",
    ]

    # Add verification steps for assumptions
    if len(assumptions) > 0:
        plan_steps.append(
            "Verify assumptions: Confirm all stated assumptions hold true before proceeding further"
        )

    # ── Decision Points ───────────────────────────────────────────────
    decision_points: List[str] = [
        "Choose implementation approach based on available tools and constraints",
    ]

    if not ctx.known_facts:
        decision_points.append(
            "Decide whether to proceed with assumptions or gather more information first"
        )

    # ── Risks ─────────────────────────────────────────────────────────
    risks: List[RiskItem] = [
        RiskItem(
            risk="Unverified assumptions may not hold in the actual environment",
            mitigation="Include verification steps early in execution; adjust plan as facts emerge",
        ),
    ]

    if not ctx.available_tools:
        risks.append(
            RiskItem(
                risk="Required tools or dependencies may not be available",
                mitigation="Identify tool requirements upfront and have fallback options ready",
            )
        )

    # ── Done When ─────────────────────────────────────────────────────
    done_when: List[str] = [
        f"The plan for '{goal.rstrip('.')}' is fully specified with actionable steps",
        "All assumptions are documented",
        "Risks and mitigations are identified",
    ]

    # ── Next Action ───────────────────────────────────────────────────
    next_action = plan_steps[0] if plan_steps else "Review the plan and begin execution."

    # ── Assemble Artifact ─────────────────────────────────────────────
    artifact = PlanArtifact(
        goal=goal,
        context=context,
        assumptions=assumptions,
        plan_steps=plan_steps,
        decision_points=decision_points,
        risks=risks,
        done_when=done_when,
        next_action=next_action,
    )

    # ── Validation Gate ───────────────────────────────────────────────
    errors = validate_plan_artifact(artifact)
    if errors:
        # This should never happen if the builder is correct, but safety net
        raise RuntimeError(
            f"Plan builder produced invalid artifact: {'; '.join(errors)}"
        )

    return artifact
