"""
Tests for output_gate — Prevent Planner Leakage.
"""

import os
import sys
import pytest

# Ensure src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))

from output_gate import (
    renderable_artifact_gate,
    promote_to_plan_artifact,
)
from plan_types import (
    OutcomeType,
    PlanArtifact,
    RiskItem,
)


def _make_valid_plan() -> PlanArtifact:
    return PlanArtifact(
        goal="Test goal",
        context=["Test context"],
        assumptions=["Test assumption"],
        plan_steps=["Step 1", "Step 2", "Step 3"],
        decision_points=["Decision 1"],
        risks=[RiskItem(risk="Risk 1", mitigation="Mitigation 1")],
        done_when=["Done condition"],
        next_action="Next step",
    )


# =========================================================================
# Approved Types Pass Through
# =========================================================================


class TestApprovedTypes:
    def test_plan_artifact_passes(self):
        plan = _make_valid_plan()
        result = renderable_artifact_gate(plan)
        assert result.allowed is True
        assert result.outcome == OutcomeType.COMPLETED_WITH_PLAN_ARTIFACT
        assert result.artifact is plan

    def test_receipt_artifact_passes(self):
        receipt = {"action_type": "file_write", "status": "completed"}
        result = renderable_artifact_gate(receipt)
        assert result.allowed is True
        assert result.outcome == OutcomeType.COMPLETED_WITH_RECEIPT

    def test_answer_artifact_passes(self):
        answer = {"answer": "A microservice is a small, independent service."}
        result = renderable_artifact_gate(answer)
        assert result.allowed is True

    def test_error_artifact_passes(self):
        error = {"error": "Unable to connect to the database."}
        result = renderable_artifact_gate(error)
        assert result.allowed is True
        assert result.outcome == OutcomeType.CANNOT_COMPLETE


# =========================================================================
# Unapproved Types → Promotion Attempt
# =========================================================================


class TestPromotionAttempt:
    def test_string_promoted(self):
        result = renderable_artifact_gate("Here is a detailed analysis of the system.")
        assert result.allowed is True
        assert result.outcome == OutcomeType.COMPLETED_WITH_PLAN_ARTIFACT
        assert isinstance(result.artifact, PlanArtifact)
        assert "promoted" in result.reason.lower()

    def test_dict_with_text_promoted(self):
        output = {"text": "The recommended approach is to use a queue."}
        result = renderable_artifact_gate(output)
        assert result.allowed is True
        assert result.outcome == OutcomeType.COMPLETED_WITH_PLAN_ARTIFACT
        assert isinstance(result.artifact, PlanArtifact)

    def test_dict_with_content_promoted(self):
        output = {"content": "Use Redis for caching."}
        result = renderable_artifact_gate(output)
        assert result.allowed is True
        assert isinstance(result.artifact, PlanArtifact)

    def test_dict_with_message_promoted(self):
        output = {"message": "Consider using event sourcing."}
        result = renderable_artifact_gate(output)
        assert result.allowed is True
        assert isinstance(result.artifact, PlanArtifact)


# =========================================================================
# Promotion Fails → CANNOT_COMPLETE
# =========================================================================


class TestPromotionFails:
    def test_empty_string_fails(self):
        result = renderable_artifact_gate("")
        assert result.allowed is False
        assert result.outcome == OutcomeType.CANNOT_COMPLETE

    def test_none_fails(self):
        result = renderable_artifact_gate(None)
        assert result.allowed is False
        assert result.outcome == OutcomeType.CANNOT_COMPLETE

    def test_empty_dict_fails(self):
        result = renderable_artifact_gate({})
        assert result.allowed is False
        assert result.outcome == OutcomeType.CANNOT_COMPLETE

    def test_number_fails(self):
        result = renderable_artifact_gate(42)
        assert result.allowed is False
        assert result.outcome == OutcomeType.CANNOT_COMPLETE

    def test_list_fails(self):
        result = renderable_artifact_gate(["step 1", "step 2"])
        assert result.allowed is False
        assert result.outcome == OutcomeType.CANNOT_COMPLETE

    def test_failure_includes_alternatives(self):
        result = renderable_artifact_gate(None)
        assert result.allowed is False
        assert isinstance(result.artifact, dict)
        assert "error" in result.artifact
        assert "alternatives" in result.artifact


# =========================================================================
# Leakage Detection in Promotion
# =========================================================================


class TestLeakageInPromotion:
    def test_draft_marker_blocks_promotion(self):
        result = renderable_artifact_gate("DRAFT: Here is a preliminary plan")
        assert result.allowed is False
        assert result.outcome == OutcomeType.CANNOT_COMPLETE

    def test_planner_marker_blocks_promotion(self):
        result = renderable_artifact_gate("PLANNER: internal reasoning")
        assert result.allowed is False
        assert result.outcome == OutcomeType.CANNOT_COMPLETE

    def test_internal_marker_blocks_promotion(self):
        result = renderable_artifact_gate("[INTERNAL] This should not appear")
        assert result.allowed is False
        assert result.outcome == OutcomeType.CANNOT_COMPLETE

    def test_scratchpad_marker_blocks_promotion(self):
        result = renderable_artifact_gate("[SCRATCHPAD] thinking aloud")
        assert result.allowed is False
        assert result.outcome == OutcomeType.CANNOT_COMPLETE

    def test_clean_string_promotes_successfully(self):
        result = renderable_artifact_gate("Use PostgreSQL for the new service.")
        assert result.allowed is True
        assert isinstance(result.artifact, PlanArtifact)


# =========================================================================
# promote_to_plan_artifact Direct Tests
# =========================================================================


class TestPromoteDirectly:
    def test_string_promotes(self):
        plan = promote_to_plan_artifact("Migrate the database to PostgreSQL")
        assert plan is not None
        assert isinstance(plan, PlanArtifact)

    def test_dict_with_text_promotes(self):
        plan = promote_to_plan_artifact({"text": "Use Docker Compose for deployment"})
        assert plan is not None

    def test_empty_string_returns_none(self):
        plan = promote_to_plan_artifact("")
        assert plan is None

    def test_none_returns_none(self):
        plan = promote_to_plan_artifact(None)
        assert plan is None

    def test_leaked_content_returns_none(self):
        plan = promote_to_plan_artifact("DRAFT: preliminary work")
        assert plan is None

    def test_number_returns_none(self):
        plan = promote_to_plan_artifact(42)
        assert plan is None
