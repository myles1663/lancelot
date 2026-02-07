"""
Tests for planning_pipeline — End-to-end wire-up for Honest Closure.
Covers Prompts 8 and 9 from the blueprint.
"""

import os
import sys
import pytest

# Ensure src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))

from planning_pipeline import PlanningPipeline, PipelineResult, PipelineState
from plan_builder import EnvContext
from plan_types import OutcomeType, IntentType, PlanArtifact
from response_governor import JobContext


# =========================================================================
# PLAN_REQUEST → Same-Turn PlanArtifact (Prompt 8)
# =========================================================================


class TestPlanRequestPipeline:
    def test_plan_request_completes_same_turn(self):
        pipeline = PlanningPipeline()
        result = pipeline.process("Plan the database migration to PostgreSQL")
        assert result.outcome == OutcomeType.COMPLETED_WITH_PLAN_ARTIFACT

    def test_plan_request_returns_rendered_markdown(self):
        pipeline = PlanningPipeline()
        result = pipeline.process("Design the authentication system")
        assert "## Goal" in result.rendered_output
        assert "## Plan Steps" in result.rendered_output

    def test_plan_request_has_artifact(self):
        pipeline = PlanningPipeline()
        result = pipeline.process("Create an approach for scaling the API")
        assert result.artifact is not None
        assert isinstance(result.artifact, PlanArtifact)

    def test_plan_request_no_forbidden_phrases(self):
        pipeline = PlanningPipeline()
        result = pipeline.process("Plan the deployment strategy")
        # Should not contain any forbidden async phrases
        assert "working on it" not in result.rendered_output.lower()
        assert "investigating" not in result.rendered_output.lower()
        assert "report back" not in result.rendered_output.lower()

    def test_plan_request_no_draft_leakage(self):
        pipeline = PlanningPipeline()
        result = pipeline.process("Design a caching strategy")
        assert "DRAFT:" not in result.rendered_output
        assert "PLANNER:" not in result.rendered_output
        assert "[INTERNAL]" not in result.rendered_output
        assert "[SCRATCHPAD]" not in result.rendered_output


# =========================================================================
# State Trace (Prompt 8)
# =========================================================================


class TestStateTrace:
    def test_plan_request_trace(self):
        pipeline = PlanningPipeline()
        result = pipeline.process("Plan the migration")
        # Should go through: received → classified:plan_request → planning → artifact_emit → completed
        assert "received" in result.state_trace
        assert any("classified:plan_request" in s for s in result.state_trace)
        assert "planning" in result.state_trace
        assert "artifact_emit" in result.state_trace
        assert "completed" in result.state_trace

    def test_no_stalling_states(self):
        pipeline = PlanningPipeline()
        result = pipeline.process("Design a REST API")
        # Should not have any "waiting" or "pending" states
        for state in result.state_trace:
            assert "waiting" not in state.lower()
            assert "pending" not in state.lower()
            assert "processing" not in state.lower()

    def test_exec_request_classified_correctly(self):
        pipeline = PlanningPipeline()
        result = pipeline.process("Deploy the application now")
        assert result.intent == IntentType.EXEC_REQUEST

    def test_knowledge_request_classified(self):
        pipeline = PlanningPipeline()
        result = pipeline.process("What is a microservice?")
        assert result.intent == IntentType.KNOWLEDGE_REQUEST


# =========================================================================
# MIXED_REQUEST → Plan + Execution Proposal (Prompt 9)
# =========================================================================


class TestMixedRequest:
    def test_mixed_request_returns_plan(self):
        pipeline = PlanningPipeline()
        result = pipeline.process("Plan and implement the authentication system")
        assert result.outcome == OutcomeType.COMPLETED_WITH_PLAN_ARTIFACT
        assert result.artifact is not None

    def test_mixed_request_has_rendered_output(self):
        pipeline = PlanningPipeline()
        result = pipeline.process("Design the API and deploy it")
        assert "## Goal" in result.rendered_output
        assert "## Plan Steps" in result.rendered_output

    def test_mixed_request_no_simulated_work(self):
        pipeline = PlanningPipeline()
        result = pipeline.process("Create a blueprint then build the service")
        assert "working on it" not in result.rendered_output.lower()

    def test_mixed_request_intent_is_mixed(self):
        pipeline = PlanningPipeline()
        result = pipeline.process("Plan and implement the auth system")
        assert result.intent == IntentType.MIXED_REQUEST


# =========================================================================
# Env Context Integration
# =========================================================================


class TestEnvContextIntegration:
    def test_with_env_context(self):
        ctx = EnvContext(
            available_tools=["Docker", "PostgreSQL"],
            known_facts={"Database": "MySQL 8.0"},
            os_info="Ubuntu 22.04",
        )
        pipeline = PlanningPipeline(env_context=ctx)
        result = pipeline.process("Plan the database migration")
        assert result.outcome == OutcomeType.COMPLETED_WITH_PLAN_ARTIFACT
        # Context should influence the output
        assert "Docker" in result.rendered_output or "PostgreSQL" in result.rendered_output


# =========================================================================
# Edge Cases
# =========================================================================


class TestEdgeCases:
    def test_empty_input_defers_to_caller(self):
        pipeline = PlanningPipeline()
        result = pipeline.process("")
        # Empty defaults to AMBIGUOUS → deferred to orchestrator/Gemini
        assert result.intent == IntentType.AMBIGUOUS
        assert result.rendered_output == ""

    def test_gibberish_defers_to_caller(self):
        pipeline = PlanningPipeline()
        result = pipeline.process("asdf jkl xyz qwerty")
        # Gibberish defaults to AMBIGUOUS → deferred to orchestrator/Gemini
        assert result.intent == IntentType.AMBIGUOUS
        assert result.rendered_output == ""

    def test_exec_request_defers_to_caller(self):
        pipeline = PlanningPipeline()
        result = pipeline.process("Deploy the application")
        # Exec requests are classified but not handled by the planning pipeline
        assert result.intent == IntentType.EXEC_REQUEST
        assert result.rendered_output == ""  # No plan rendered


# =========================================================================
# Integration: Full Pipeline (AC-1 through AC-4)
# =========================================================================


class TestAcceptanceCriteria:
    """Covers AC-1 through AC-4 from the spec."""

    def test_ac1_no_simulated_progress(self):
        """AC-1: Without job_id, forbidden phrases never appear."""
        pipeline = PlanningPipeline()
        result = pipeline.process("Plan the system upgrade")
        text = result.rendered_output.lower()
        assert "i'm working on it" not in text
        assert "i'm investigating" not in text
        assert "please allow me time" not in text
        assert "i will report back" not in text
        assert "i'm processing" not in text

    def test_ac2_planning_completion_same_turn(self):
        """AC-2: PLAN_REQUEST always returns PlanArtifact in same turn."""
        pipeline = PlanningPipeline()
        result = pipeline.process("Design a microservices architecture")
        assert result.outcome == OutcomeType.COMPLETED_WITH_PLAN_ARTIFACT
        assert result.artifact is not None
        assert "## Goal" in result.rendered_output
        assert "## Plan Steps" in result.rendered_output

    def test_ac4_no_draft_leakage(self):
        """AC-4: No DRAFT: or internal planner markers in user output."""
        pipeline = PlanningPipeline()
        result = pipeline.process("Plan the deployment pipeline")
        assert "DRAFT:" not in result.rendered_output
        assert "PLANNER:" not in result.rendered_output
        assert "[INTERNAL]" not in result.rendered_output
        assert "[SCRATCHPAD]" not in result.rendered_output
        assert "PLANNING_INTERNAL" not in result.rendered_output
