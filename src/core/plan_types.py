"""
Plan Types — Honest Closure + PlanArtifact Domain Types
=======================================================

First-class types for the planning subsystem:

- OutcomeType: Terminal outcomes for every request
- IntentType: Intent classification labels for routing
- PlanArtifact: Structured planning deliverable

These types enforce the core principle:
> Lancelot may not lie about work. Lancelot may always finish thinking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


# =============================================================================
# Outcome Types
# =============================================================================


class OutcomeType(str, Enum):
    """Terminal outcome for every request. Removes ambiguity around 'done'."""

    COMPLETED_WITH_RECEIPT = "completed_with_receipt"
    """Tool execution or state mutation completed."""

    COMPLETED_WITH_PLAN_ARTIFACT = "completed_with_plan_artifact"
    """Planning/design task completed with a PlanArtifact."""

    CANNOT_COMPLETE = "cannot_complete"
    """Honest refusal with reason and safe alternatives."""

    NEEDS_INPUT = "needs_input"
    """Blocked pending minimal missing input from the user."""


# =============================================================================
# Intent Types
# =============================================================================


class IntentType(str, Enum):
    """Intent classification labels for routing requests."""

    PLAN_REQUEST = "plan_request"
    """User asked for a plan, design, approach, or architecture."""

    EXEC_REQUEST = "exec_request"
    """User asked for implementation, execution, or deployment."""

    MIXED_REQUEST = "mixed_request"
    """Request contains both planning and execution intent."""

    KNOWLEDGE_REQUEST = "knowledge_request"
    """User asked a question or requested information."""

    AMBIGUOUS = "ambiguous"
    """Cannot determine intent (router defaults to PLAN_REQUEST)."""


# =============================================================================
# PlanArtifact
# =============================================================================


@dataclass
class RiskItem:
    """A single risk with its mitigation strategy."""
    risk: str
    mitigation: str


@dataclass
class PlanArtifact:
    """
    First-class planning deliverable.

    A PlanArtifact is a governed, user-visible artifact that satisfies
    planning or design requests without requiring tools, browsing, or
    execution. It represents completed cognitive work.

    All required fields must be populated for the artifact to be valid.
    """

    # Required fields
    goal: str = ""
    """One clear sentence describing the objective."""

    context: List[str] = field(default_factory=list)
    """2-6 bullets describing constraints, environment, or known facts."""

    assumptions: List[str] = field(default_factory=list)
    """Explicit statements of what is assumed due to lack of verification."""

    plan_steps: List[str] = field(default_factory=list)
    """Ordered, actionable steps."""

    decision_points: List[str] = field(default_factory=list)
    """Key choices that alter the path forward."""

    risks: List[RiskItem] = field(default_factory=list)
    """Identified risks and suggested mitigations."""

    done_when: List[str] = field(default_factory=list)
    """What 'complete' means for this plan (not implementation)."""

    next_action: str = ""
    """The single best immediate next step."""

    # Optional fields
    mvp_path: Optional[str] = None
    test_plan: Optional[str] = None
    estimate: Optional[str] = None
    references: Optional[List[str]] = None


# =============================================================================
# Validation
# =============================================================================


class PlanArtifactValidationError(Exception):
    """Raised when a PlanArtifact fails validation."""

    def __init__(self, errors: List[str]):
        self.errors = errors
        super().__init__(f"PlanArtifact validation failed: {'; '.join(errors)}")


def validate_plan_artifact(artifact: PlanArtifact) -> List[str]:
    """
    Validate a PlanArtifact has all required fields populated.

    Returns:
        Empty list if valid, list of error strings if invalid.
    """
    errors = []

    if not artifact.goal or not artifact.goal.strip():
        errors.append("goal is required")

    if not artifact.context or len(artifact.context) < 1:
        errors.append("context requires at least 1 item")

    if not artifact.assumptions or len(artifact.assumptions) < 1:
        errors.append("assumptions requires at least 1 item")

    if not artifact.plan_steps or len(artifact.plan_steps) < 3:
        errors.append("plan_steps requires at least 3 items")

    if not artifact.decision_points or len(artifact.decision_points) < 1:
        errors.append("decision_points requires at least 1 item")

    if not artifact.risks or len(artifact.risks) < 1:
        errors.append("risks requires at least 1 item")

    if not artifact.done_when or len(artifact.done_when) < 1:
        errors.append("done_when requires at least 1 item")

    if not artifact.next_action or not artifact.next_action.strip():
        errors.append("next_action is required")

    return errors


def assert_no_planner_leakage(text: str) -> List[str]:
    """
    Check text for planner-internal markers that should never reach users.

    Returns:
        Empty list if clean, list of detected markers if leakage found.
    """
    markers = [
        "DRAFT:",
        "PLANNER:",
        "[INTERNAL]",
        "[SCRATCHPAD]",
        "PLANNING_INTERNAL",
    ]
    found = []
    for marker in markers:
        if marker in text:
            found.append(marker)
    return found
