"""
Acceptance Test Suite — Honest Closure + PlanArtifact (AC-1 through AC-4).
==========================================================================

Uses real pipeline components — no mocks of the functions under test.

AC-1: No Simulated Progress
    Without job_id, forbidden phrases never appear.

AC-2: Planning Completion
    PLAN_REQUEST always returns PlanArtifact in the same turn.

AC-3: Honest Inability
    KNOWLEDGE_REQUEST without data returns honest "can't verify" style
    response and alternatives, not fabricated certainty.

AC-4: No Draft Leakage
    No "DRAFT:" or internal planner markers in user output.
"""

import os
import sys
import pytest

# Ensure src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))

from planning_pipeline import PlanningPipeline, PipelineResult
from plan_builder import EnvContext, build_plan_artifact
from plan_renderer import render_plan_artifact_markdown
from plan_types import (
    OutcomeType,
    IntentType,
    PlanArtifact,
    RiskItem,
    validate_plan_artifact,
    assert_no_planner_leakage,
)
from intent_classifier import classify_intent
from response_governor import (
    detect_forbidden_async_language,
    enforce_no_simulated_work,
    ResponseContext,
    JobContext,
)
from output_gate import renderable_artifact_gate


# =========================================================================
# AC-1: No Simulated Progress
# =========================================================================


class TestAC1_NoSimulatedProgress:
    """
    Without job_id, forbidden phrases never appear in any output
    from the planning pipeline.
    """

    PLANNING_PROMPTS = [
        "Plan the database migration to PostgreSQL",
        "Design a microservices architecture for our platform",
        "Create an approach for scaling the notification system",
        "How would we implement caching for the API?",
        "Outline a strategy for zero-downtime deployments",
    ]

    FORBIDDEN_PATTERNS = [
        "i'm working on it",
        "i am working on it",
        "i'm investigating",
        "i am investigating",
        "please allow me time",
        "i will report back",
        "i'm processing your request",
        "i am processing your request",
        "working on that now",
        "let me work on that",
        "give me a moment",
        "currently processing",
        "please wait while i",
        "i'll get back to you",
        "i will get back to you",
    ]

    @pytest.mark.parametrize("prompt", PLANNING_PROMPTS)
    def test_planning_output_has_no_forbidden_phrases(self, prompt):
        pipeline = PlanningPipeline()
        result = pipeline.process(prompt)
        text_lower = result.rendered_output.lower()
        for phrase in self.FORBIDDEN_PATTERNS:
            assert phrase not in text_lower, (
                f"Forbidden phrase '{phrase}' found in output for prompt: {prompt}"
            )

    def test_governor_detects_violations(self):
        """Direct governor test: forbidden text blocked without job_id."""
        for phrase in self.FORBIDDEN_PATTERNS:
            matches = detect_forbidden_async_language(phrase)
            assert len(matches) >= 1, f"Governor missed: {phrase}"

    def test_governor_allows_with_job_id(self):
        """Forbidden text allowed when backed by a real job."""
        ctx = ResponseContext(text="I'm working on it")
        job = JobContext(job_id="job-real-123", status="running")
        result = enforce_no_simulated_work(ctx, job)
        assert result.passed is True

    def test_plan_builder_output_clean(self):
        """Plan builder itself never produces forbidden phrases."""
        for prompt in self.PLANNING_PROMPTS:
            artifact = build_plan_artifact(prompt)
            all_text = " ".join([
                artifact.goal,
                " ".join(artifact.context),
                " ".join(artifact.assumptions),
                " ".join(artifact.plan_steps),
                " ".join(artifact.decision_points),
                " ".join(f"{r.risk} {r.mitigation}" for r in artifact.risks),
                " ".join(artifact.done_when),
                artifact.next_action,
            ]).lower()
            violations = detect_forbidden_async_language(all_text)
            assert violations == [], f"Plan builder leaked: {violations}"


# =========================================================================
# AC-2: Planning Completion in Same Turn
# =========================================================================


class TestAC2_PlanningCompletion:
    """
    PLAN_REQUEST always returns a PlanArtifact in the same turn.
    No stalling, no intermediate states.
    """

    PLAN_PROMPTS = [
        "Plan the database migration",
        "Design the API gateway",
        "How should we structure the codebase?",
        "Create a blueprint for the notification service",
        "What approach should we take for authentication?",
        "Outline the deployment strategy",
        "Draft a roadmap for the project",
        "Propose an architecture for the data pipeline",
    ]

    @pytest.mark.parametrize("prompt", PLAN_PROMPTS)
    def test_plan_request_returns_artifact(self, prompt):
        pipeline = PlanningPipeline()
        result = pipeline.process(prompt)
        assert result.outcome == OutcomeType.COMPLETED_WITH_PLAN_ARTIFACT, (
            f"Expected COMPLETED_WITH_PLAN_ARTIFACT for: {prompt}, got: {result.outcome}"
        )

    @pytest.mark.parametrize("prompt", PLAN_PROMPTS)
    def test_plan_request_has_all_sections(self, prompt):
        pipeline = PlanningPipeline()
        result = pipeline.process(prompt)
        md = result.rendered_output
        required_sections = [
            "## Goal",
            "## Context",
            "## Assumptions",
            "## Plan Steps",
            "## Decision Points",
            "## Risks",
            "## Done When",
            "## Next Action",
        ]
        for section in required_sections:
            assert section in md, f"Missing section '{section}' for prompt: {prompt}"

    @pytest.mark.parametrize("prompt", PLAN_PROMPTS)
    def test_plan_request_artifact_valid(self, prompt):
        pipeline = PlanningPipeline()
        result = pipeline.process(prompt)
        assert result.artifact is not None
        errors = validate_plan_artifact(result.artifact)
        assert errors == [], f"Validation errors for '{prompt}': {errors}"

    def test_state_trace_no_stalling(self):
        """State trace must go straight through without stalling."""
        pipeline = PlanningPipeline()
        result = pipeline.process("Plan the migration")
        stalling_words = {"waiting", "pending", "processing", "busy", "stalled"}
        for state in result.state_trace:
            state_words = set(state.lower().split(":"))
            assert not (state_words & stalling_words), f"Stalling state found: {state}"

    def test_empty_input_still_completes(self):
        """Even empty input should produce a plan (defaults to PLAN_REQUEST)."""
        pipeline = PlanningPipeline()
        result = pipeline.process("")
        assert result.outcome == OutcomeType.COMPLETED_WITH_PLAN_ARTIFACT

    def test_with_env_context_still_completes(self):
        """Adding env context should not prevent completion."""
        ctx = EnvContext(
            available_tools=["Docker", "Kubernetes"],
            known_facts={"Region": "us-east-1"},
            constraints=["Budget: $500/month"],
            os_info="Alpine Linux 3.18",
        )
        pipeline = PlanningPipeline(env_context=ctx)
        result = pipeline.process("Design a container orchestration strategy")
        assert result.outcome == OutcomeType.COMPLETED_WITH_PLAN_ARTIFACT


# =========================================================================
# AC-3: Honest Inability
# =========================================================================


class TestAC3_HonestInability:
    """
    KNOWLEDGE_REQUEST without data returns honest "can't verify / don't know"
    style and alternatives, not fabricated certainty.

    Note: The planning pipeline classifies but defers KNOWLEDGE_REQUEST to
    the orchestrator. We test the classifier + plan builder behavior for
    honesty here.
    """

    def test_knowledge_request_classified_correctly(self):
        assert classify_intent("What is Docker?") == IntentType.KNOWLEDGE_REQUEST
        assert classify_intent("Explain how JWT tokens work") == IntentType.KNOWLEDGE_REQUEST
        assert classify_intent("Why does this error occur?") == IntentType.KNOWLEDGE_REQUEST

    def test_knowledge_request_deferred(self):
        """Knowledge requests should be deferred, not faked with a plan."""
        pipeline = PlanningPipeline()
        result = pipeline.process("What is a microservice?")
        assert result.intent == IntentType.KNOWLEDGE_REQUEST
        # Should NOT produce a plan artifact for pure knowledge questions
        assert result.artifact is None

    def test_plan_builder_uses_assumptions_for_unknowns(self):
        """When info is missing, the plan builder states assumptions honestly."""
        artifact = build_plan_artifact("Plan the Kubernetes migration")
        # Should have explicit assumptions
        assert len(artifact.assumptions) >= 1
        assert any(
            "assumption" in a.lower() for a in artifact.assumptions
        ), "No explicit assumption found"

    def test_cannot_complete_has_alternatives(self):
        """CANNOT_COMPLETE outcome should include alternatives."""
        result = renderable_artifact_gate(None)  # Will fail → CANNOT_COMPLETE
        assert result.outcome == OutcomeType.CANNOT_COMPLETE
        assert "alternatives" in result.artifact


# =========================================================================
# AC-4: No Draft Leakage
# =========================================================================


class TestAC4_NoDraftLeakage:
    """
    User output never contains planner-internal markers.
    """

    PLAN_PROMPTS = [
        "Plan the database migration",
        "Design a REST API",
        "How would we implement caching?",
        "Outline the CI/CD pipeline",
        "Create a deployment strategy",
    ]

    LEAK_MARKERS = [
        "DRAFT:",
        "PLANNER:",
        "[INTERNAL]",
        "[SCRATCHPAD]",
        "PLANNING_INTERNAL",
    ]

    @pytest.mark.parametrize("prompt", PLAN_PROMPTS)
    def test_no_leak_markers_in_pipeline_output(self, prompt):
        pipeline = PlanningPipeline()
        result = pipeline.process(prompt)
        for marker in self.LEAK_MARKERS:
            assert marker not in result.rendered_output, (
                f"Leak marker '{marker}' found in output for: {prompt}"
            )

    def test_leakage_detector_catches_all_markers(self):
        """Direct test: all known markers are caught by the detector."""
        for marker in self.LEAK_MARKERS:
            leaks = assert_no_planner_leakage(f"Some text {marker} more text")
            assert marker in leaks, f"Leakage detector missed: {marker}"

    def test_renderer_blocks_leaked_content(self):
        """Renderer raises ValueError if leaked content is present."""
        artifact = PlanArtifact(
            goal="PLANNER: internal goal",
            context=["context"],
            assumptions=["assumption"],
            plan_steps=["step 1", "step 2", "step 3"],
            decision_points=["decision"],
            risks=[RiskItem(risk="risk", mitigation="mitigation")],
            done_when=["done"],
            next_action="next",
        )
        with pytest.raises(ValueError, match="leakage"):
            render_plan_artifact_markdown(artifact)

    def test_output_gate_blocks_leaked_strings(self):
        """Output gate refuses to promote leaked content."""
        for marker in self.LEAK_MARKERS:
            result = renderable_artifact_gate(f"{marker} some content")
            assert result.outcome == OutcomeType.CANNOT_COMPLETE, (
                f"Gate didn't block leaked string: {marker}"
            )

    def test_clean_artifact_renders_without_leaks(self):
        """A clean artifact should render without any markers."""
        artifact = build_plan_artifact("Plan a clean migration")
        md = render_plan_artifact_markdown(artifact)
        leaks = assert_no_planner_leakage(md)
        assert leaks == [], f"Clean artifact has leaks: {leaks}"


# =========================================================================
# Cross-Cutting: Full Pipeline Determinism
# =========================================================================


class TestDeterminism:
    """Pipeline should produce deterministic results for identical inputs."""

    def test_same_input_same_outcome(self):
        pipeline = PlanningPipeline()
        r1 = pipeline.process("Plan the database migration")
        r2 = pipeline.process("Plan the database migration")
        assert r1.outcome == r2.outcome
        assert r1.intent == r2.intent
        assert r1.rendered_output == r2.rendered_output

    def test_same_input_same_state_trace(self):
        pipeline = PlanningPipeline()
        r1 = pipeline.process("Design the API")
        r2 = pipeline.process("Design the API")
        assert r1.state_trace == r2.state_trace
