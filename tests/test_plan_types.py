"""
Tests for plan_types — Outcome/Intent enums, PlanArtifact, and validation.
"""

import os
import sys
import pytest

# Ensure src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))

from plan_types import (
    OutcomeType,
    IntentType,
    PlanArtifact,
    RiskItem,
    validate_plan_artifact,
    assert_no_planner_leakage,
    PlanArtifactValidationError,
)


# =========================================================================
# OutcomeType Enum
# =========================================================================


class TestOutcomeType:
    def test_values_exist(self):
        assert OutcomeType.COMPLETED_WITH_RECEIPT == "completed_with_receipt"
        assert OutcomeType.COMPLETED_WITH_PLAN_ARTIFACT == "completed_with_plan_artifact"
        assert OutcomeType.CANNOT_COMPLETE == "cannot_complete"
        assert OutcomeType.NEEDS_INPUT == "needs_input"

    def test_is_str_enum(self):
        assert isinstance(OutcomeType.CANNOT_COMPLETE, str)

    def test_serialization_roundtrip(self):
        val = OutcomeType.COMPLETED_WITH_PLAN_ARTIFACT
        assert OutcomeType(val.value) == val

    def test_all_members(self):
        assert len(OutcomeType) == 4


# =========================================================================
# IntentType Enum
# =========================================================================


class TestIntentType:
    def test_values_exist(self):
        assert IntentType.PLAN_REQUEST == "plan_request"
        assert IntentType.EXEC_REQUEST == "exec_request"
        assert IntentType.MIXED_REQUEST == "mixed_request"
        assert IntentType.KNOWLEDGE_REQUEST == "knowledge_request"
        assert IntentType.AMBIGUOUS == "ambiguous"

    def test_is_str_enum(self):
        assert isinstance(IntentType.PLAN_REQUEST, str)

    def test_serialization_roundtrip(self):
        val = IntentType.MIXED_REQUEST
        assert IntentType(val.value) == val

    def test_all_members(self):
        assert len(IntentType) == 5


# =========================================================================
# PlanArtifact Validation
# =========================================================================


def _make_valid_artifact() -> PlanArtifact:
    """Helper: returns a fully valid PlanArtifact."""
    return PlanArtifact(
        goal="Migrate database to PostgreSQL",
        context=["Current DB is SQLite", "Production traffic is low"],
        assumptions=["Downtime window of 2 hours is acceptable"],
        plan_steps=[
            "Export SQLite data to CSV",
            "Create PostgreSQL schema",
            "Import CSV into PostgreSQL",
            "Update connection strings",
        ],
        decision_points=["Choose managed vs self-hosted PostgreSQL"],
        risks=[RiskItem(risk="Data loss during migration", mitigation="Run parallel databases for 48h")],
        done_when=["PostgreSQL is primary database and all queries work"],
        next_action="Export current SQLite schema for review",
    )


class TestPlanArtifactValidation:
    def test_valid_artifact_passes(self):
        artifact = _make_valid_artifact()
        errors = validate_plan_artifact(artifact)
        assert errors == []

    def test_missing_goal_fails(self):
        artifact = _make_valid_artifact()
        artifact.goal = ""
        errors = validate_plan_artifact(artifact)
        assert any("goal" in e for e in errors)

    def test_missing_context_fails(self):
        artifact = _make_valid_artifact()
        artifact.context = []
        errors = validate_plan_artifact(artifact)
        assert any("context" in e for e in errors)

    def test_missing_assumptions_fails(self):
        artifact = _make_valid_artifact()
        artifact.assumptions = []
        errors = validate_plan_artifact(artifact)
        assert any("assumptions" in e for e in errors)

    def test_too_few_plan_steps_fails(self):
        artifact = _make_valid_artifact()
        artifact.plan_steps = ["Step 1", "Step 2"]  # needs 3+
        errors = validate_plan_artifact(artifact)
        assert any("plan_steps" in e for e in errors)

    def test_three_plan_steps_passes(self):
        artifact = _make_valid_artifact()
        artifact.plan_steps = ["Step 1", "Step 2", "Step 3"]
        errors = validate_plan_artifact(artifact)
        assert not any("plan_steps" in e for e in errors)

    def test_missing_decision_points_fails(self):
        artifact = _make_valid_artifact()
        artifact.decision_points = []
        errors = validate_plan_artifact(artifact)
        assert any("decision_points" in e for e in errors)

    def test_missing_risks_fails(self):
        artifact = _make_valid_artifact()
        artifact.risks = []
        errors = validate_plan_artifact(artifact)
        assert any("risks" in e for e in errors)

    def test_missing_done_when_fails(self):
        artifact = _make_valid_artifact()
        artifact.done_when = []
        errors = validate_plan_artifact(artifact)
        assert any("done_when" in e for e in errors)

    def test_missing_next_action_fails(self):
        artifact = _make_valid_artifact()
        artifact.next_action = ""
        errors = validate_plan_artifact(artifact)
        assert any("next_action" in e for e in errors)

    def test_whitespace_only_goal_fails(self):
        artifact = _make_valid_artifact()
        artifact.goal = "   "
        errors = validate_plan_artifact(artifact)
        assert any("goal" in e for e in errors)

    def test_multiple_errors_reported(self):
        artifact = PlanArtifact()  # All defaults — everything missing
        errors = validate_plan_artifact(artifact)
        assert len(errors) >= 8  # All required fields fail


# =========================================================================
# Planner Leakage Detection
# =========================================================================


class TestPlannerLeakage:
    def test_clean_text_passes(self):
        leaks = assert_no_planner_leakage("Here is your migration plan.")
        assert leaks == []

    def test_draft_detected(self):
        leaks = assert_no_planner_leakage("DRAFT: Here is a preliminary plan")
        assert "DRAFT:" in leaks

    def test_planner_marker_detected(self):
        leaks = assert_no_planner_leakage("PLANNER: internal reasoning step")
        assert "PLANNER:" in leaks

    def test_internal_marker_detected(self):
        leaks = assert_no_planner_leakage("[INTERNAL] This should not show")
        assert "[INTERNAL]" in leaks

    def test_scratchpad_detected(self):
        leaks = assert_no_planner_leakage("[SCRATCHPAD] thinking...")
        assert "[SCRATCHPAD]" in leaks

    def test_multiple_markers_all_reported(self):
        text = "DRAFT: plan\nPLANNER: step\n[INTERNAL] note"
        leaks = assert_no_planner_leakage(text)
        assert len(leaks) == 3


# =========================================================================
# RiskItem
# =========================================================================


class TestRiskItem:
    def test_create_risk(self):
        r = RiskItem(risk="Server overload", mitigation="Add rate limiting")
        assert r.risk == "Server overload"
        assert r.mitigation == "Add rate limiting"
