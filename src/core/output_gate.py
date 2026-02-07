"""
Output Gate — Prevent Planner Leakage.
=======================================

Ensures only approved artifact types can be rendered to the user.
If an unapproved type is produced, attempts promotion to PlanArtifact.
If promotion fails, returns CANNOT_COMPLETE.

Allowed artifact types:
    - PlanArtifact
    - ReceiptArtifact (dict with "action_type")
    - AnswerArtifact  (dict with "answer")
    - ErrorArtifact   (dict with "error")

Public API:
    renderable_artifact_gate(output) -> GateResult
    promote_to_plan_artifact(output) -> Optional[PlanArtifact]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from plan_types import (
    OutcomeType,
    PlanArtifact,
    RiskItem,
    validate_plan_artifact,
    assert_no_planner_leakage,
)


# =============================================================================
# Gate Result
# =============================================================================


@dataclass
class GateResult:
    """Result of the output gate check."""
    allowed: bool
    """True if the output is allowed as-is or was successfully promoted."""

    outcome: OutcomeType
    """The terminal outcome."""

    artifact: Any
    """The (possibly promoted) artifact."""

    reason: Optional[str] = None
    """Explanation of the gate decision."""


# =============================================================================
# Type Detection
# =============================================================================


def _is_plan_artifact(output: Any) -> bool:
    """Check if output is a PlanArtifact."""
    return isinstance(output, PlanArtifact)


def _is_receipt_artifact(output: Any) -> bool:
    """Check if output is a ReceiptArtifact (dict with 'action_type')."""
    return isinstance(output, dict) and "action_type" in output


def _is_answer_artifact(output: Any) -> bool:
    """Check if output is an AnswerArtifact (dict with 'answer')."""
    return isinstance(output, dict) and "answer" in output


def _is_error_artifact(output: Any) -> bool:
    """Check if output is an ErrorArtifact (dict with 'error')."""
    return isinstance(output, dict) and "error" in output


def _is_approved_type(output: Any) -> bool:
    """Check if the output is one of the four approved artifact types."""
    return (
        _is_plan_artifact(output)
        or _is_receipt_artifact(output)
        or _is_answer_artifact(output)
        or _is_error_artifact(output)
    )


# =============================================================================
# Promotion
# =============================================================================


def promote_to_plan_artifact(output: Any) -> Optional[PlanArtifact]:
    """
    Attempt to promote an unapproved output to a PlanArtifact.

    Handles common cases:
    - Raw string → wrapped as a single-step plan
    - Dict with text-like content → extracted and wrapped

    Returns:
        A valid PlanArtifact if promotion succeeds, None otherwise.
    """
    text = None

    if isinstance(output, str):
        text = output.strip()
    elif isinstance(output, dict):
        # Try common keys
        for key in ("text", "content", "message", "response", "result"):
            if key in output and isinstance(output[key], str):
                text = output[key].strip()
                break

    if not text:
        return None

    # Check for planner leakage in the source text
    leaks = assert_no_planner_leakage(text)
    if leaks:
        return None  # Can't promote leaked content

    # Build a minimal but valid PlanArtifact from the text
    artifact = PlanArtifact(
        goal="Complete the requested task.",
        context=["Promoted from unstructured planner output."],
        assumptions=["Assumption: The original output intent has been preserved."],
        plan_steps=[
            "Review the following output from the planner",
            f"Content: {text[:500]}",  # Truncate if very long
            "Determine next steps based on the above",
        ],
        decision_points=["Validate whether this promoted plan meets the original request"],
        risks=[
            RiskItem(
                risk="Promoted output may lose structure or nuance",
                mitigation="Review carefully and request clarification if needed",
            )
        ],
        done_when=["The promoted plan has been reviewed and accepted or replaced"],
        next_action="Review the promoted plan output and confirm or refine.",
    )

    errors = validate_plan_artifact(artifact)
    if errors:
        return None

    return artifact


# =============================================================================
# Gate
# =============================================================================


def renderable_artifact_gate(output: Any) -> GateResult:
    """
    Check if the output is an approved artifact type.

    If not, attempt promotion to PlanArtifact.
    If promotion fails, return CANNOT_COMPLETE.

    Args:
        output: The artifact to check.

    Returns:
        GateResult with the decision.
    """
    # Already an approved type
    if _is_plan_artifact(output):
        return GateResult(
            allowed=True,
            outcome=OutcomeType.COMPLETED_WITH_PLAN_ARTIFACT,
            artifact=output,
            reason="Output is a valid PlanArtifact.",
        )

    if _is_receipt_artifact(output):
        return GateResult(
            allowed=True,
            outcome=OutcomeType.COMPLETED_WITH_RECEIPT,
            artifact=output,
            reason="Output is a valid ReceiptArtifact.",
        )

    if _is_answer_artifact(output):
        return GateResult(
            allowed=True,
            outcome=OutcomeType.COMPLETED_WITH_RECEIPT,
            artifact=output,
            reason="Output is a valid AnswerArtifact.",
        )

    if _is_error_artifact(output):
        return GateResult(
            allowed=True,
            outcome=OutcomeType.CANNOT_COMPLETE,
            artifact=output,
            reason="Output is an ErrorArtifact.",
        )

    # Not an approved type — attempt promotion
    promoted = promote_to_plan_artifact(output)
    if promoted is not None:
        return GateResult(
            allowed=True,
            outcome=OutcomeType.COMPLETED_WITH_PLAN_ARTIFACT,
            artifact=promoted,
            reason="Output promoted to PlanArtifact.",
        )

    # Promotion failed → CANNOT_COMPLETE
    return GateResult(
        allowed=False,
        outcome=OutcomeType.CANNOT_COMPLETE,
        artifact={
            "error": "Output could not be rendered — unrecognized artifact type and promotion failed.",
            "alternatives": [
                "Rephrase your request as a planning question.",
                "Ask for a specific action or piece of information.",
            ],
        },
        reason="Unapproved output type; promotion to PlanArtifact failed.",
    )
