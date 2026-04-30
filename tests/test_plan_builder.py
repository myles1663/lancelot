"""
Tests for plan_builder — Assumption-Bounded Plan Builder.
"""

import os
import sys
import pytest

# Ensure src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))

from plan_builder import build_plan_artifact, EnvContext
from plan_types import (
    PlanArtifact,
    RiskItem,
    validate_plan_artifact,
    assert_no_planner_leakage,
)


# =========================================================================
# Always Returns Valid Artifact
# =========================================================================


class TestAlwaysValid:
    """build_plan_artifact MUST always return a PlanArtifact that passes validation."""

    def test_simple_plan_request(self):
        artifact = build_plan_artifact("Plan the database migration")
        errors = validate_plan_artifact(artifact)
        assert errors == [], f"Validation errors: {errors}"

    def test_long_plan_request(self):
        artifact = build_plan_artifact(
            "Design a comprehensive microservices architecture for our e-commerce "
            "platform, including user management, order processing, inventory "
            "tracking, and payment gateway integration"
        )
        errors = validate_plan_artifact(artifact)
        assert errors == []

    def test_minimal_plan_request(self):
        artifact = build_plan_artifact("plan")
        errors = validate_plan_artifact(artifact)
        assert errors == []

    def test_empty_text(self):
        artifact = build_plan_artifact("")
        errors = validate_plan_artifact(artifact)
        assert errors == []

    def test_whitespace_only(self):
        artifact = build_plan_artifact("   ")
        errors = validate_plan_artifact(artifact)
        assert errors == []

    def test_with_env_context(self):
        ctx = EnvContext(
            available_tools=["Docker", "PostgreSQL", "Python"],
            known_facts={"Database": "Currently running MySQL 8.0"},
            constraints=["Must complete within 4 hours"],
            os_info="Ubuntu 22.04 LTS",
        )
        artifact = build_plan_artifact("Migrate from MySQL to PostgreSQL", ctx)
        errors = validate_plan_artifact(artifact)
        assert errors == []

    def test_with_empty_env_context(self):
        ctx = EnvContext()
        artifact = build_plan_artifact("Design a caching strategy", ctx)
        errors = validate_plan_artifact(artifact)
        assert errors == []


# =========================================================================
# Artifact Content Quality
# =========================================================================


class TestContentQuality:
    def test_goal_is_derived_from_input(self):
        artifact = build_plan_artifact("Plan the API gateway design")
        assert "API" in artifact.goal or "gateway" in artifact.goal

    def test_context_includes_env_info(self):
        ctx = EnvContext(os_info="Windows Server 2022")
        artifact = build_plan_artifact("Set up CI/CD", ctx)
        assert any("Windows" in c for c in artifact.context)

    def test_context_includes_tools(self):
        ctx = EnvContext(available_tools=["Jenkins", "Docker"])
        artifact = build_plan_artifact("Set up CI/CD", ctx)
        assert any("Jenkins" in c for c in artifact.context)

    def test_context_includes_known_facts(self):
        ctx = EnvContext(known_facts={"Language": "Python 3.11"})
        artifact = build_plan_artifact("Refactor the codebase", ctx)
        assert any("Python" in c for c in artifact.context)

    def test_context_includes_constraints(self):
        ctx = EnvContext(constraints=["Budget limited to $500"])
        artifact = build_plan_artifact("Deploy to cloud", ctx)
        assert any("Budget" in c or "$500" in c for c in artifact.context)


# =========================================================================
# Assumption Injection
# =========================================================================


class TestAssumptionInjection:
    def test_assumptions_present_when_no_known_facts(self):
        artifact = build_plan_artifact("Plan the migration")
        assert len(artifact.assumptions) >= 1
        assert any("assumption" in a.lower() for a in artifact.assumptions)

    def test_assumptions_present_when_no_tools(self):
        artifact = build_plan_artifact("Plan the deployment")
        assert any("tool" in a.lower() for a in artifact.assumptions)

    def test_assumptions_with_full_context(self):
        ctx = EnvContext(
            available_tools=["Docker"],
            known_facts={"Runtime": "Node.js 20"},
        )
        artifact = build_plan_artifact("Deploy the app", ctx)
        # Should still have at least one assumption
        assert len(artifact.assumptions) >= 1

    def test_verification_step_for_assumptions(self):
        artifact = build_plan_artifact("Plan the system upgrade")
        # Plan steps should include a verification step for assumptions
        step_text = " ".join(artifact.plan_steps).lower()
        assert "verify" in step_text or "confirm" in step_text


# =========================================================================
# Never Stalls
# =========================================================================


class TestNeverStalls:
    """The plan builder must always return an artifact — never stall."""

    def test_returns_artifact_for_vague_input(self):
        artifact = build_plan_artifact("do something")
        assert isinstance(artifact, PlanArtifact)

    def test_returns_artifact_for_gibberish(self):
        artifact = build_plan_artifact("asdf jkl qwerty uiop")
        assert isinstance(artifact, PlanArtifact)
        errors = validate_plan_artifact(artifact)
        assert errors == []

    def test_returns_artifact_for_single_word(self):
        artifact = build_plan_artifact("migrate")
        assert isinstance(artifact, PlanArtifact)
        errors = validate_plan_artifact(artifact)
        assert errors == []


# =========================================================================
# No Planner Leakage
# =========================================================================


class TestNoPlannerLeakage:
    def _all_text(self, artifact: PlanArtifact) -> str:
        """Concatenate all text fields for leakage checking."""
        parts = [
            artifact.goal,
            artifact.next_action,
            *artifact.context,
            *artifact.assumptions,
            *artifact.plan_steps,
            *artifact.decision_points,
            *artifact.done_when,
        ]
        for r in artifact.risks:
            parts.extend([r.risk, r.mitigation])
        return "\n".join(parts)

    def test_no_draft_leakage(self):
        artifact = build_plan_artifact("Plan the system architecture")
        text = self._all_text(artifact)
        leaks = assert_no_planner_leakage(text)
        assert leaks == [], f"Leakage detected: {leaks}"

    def test_no_internal_markers(self):
        artifact = build_plan_artifact("Design a REST API")
        text = self._all_text(artifact)
        assert "DRAFT:" not in text
        assert "PLANNER:" not in text
        assert "[INTERNAL]" not in text
        assert "[SCRATCHPAD]" not in text


# =========================================================================
# Required Fields Structure
# =========================================================================


class TestFieldStructure:
    def test_plan_steps_minimum_three(self):
        artifact = build_plan_artifact("Plan the deployment")
        assert len(artifact.plan_steps) >= 3

    def test_risks_have_mitigations(self):
        artifact = build_plan_artifact("Plan the migration")
        for risk in artifact.risks:
            assert isinstance(risk, RiskItem)
            assert risk.risk.strip() != ""
            assert risk.mitigation.strip() != ""

    def test_next_action_non_empty(self):
        artifact = build_plan_artifact("Plan the upgrade")
        assert artifact.next_action.strip() != ""

    def test_done_when_non_empty(self):
        artifact = build_plan_artifact("Plan the refactor")
        assert len(artifact.done_when) >= 1

    def test_decision_points_non_empty(self):
        artifact = build_plan_artifact("Design the system")
        assert len(artifact.decision_points) >= 1


# =========================================================================
# Return Type
# =========================================================================


class TestReturnType:
    def test_returns_plan_artifact(self):
        result = build_plan_artifact("Plan something")
        assert isinstance(result, PlanArtifact)
