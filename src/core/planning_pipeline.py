"""
Planning Pipeline — End-to-end wire-up for Honest Closure.
===========================================================

Wires the intent classifier, plan builder, renderer, governor,
and output gate into a single pipeline that enforces:

    CLASSIFIED:PLAN_REQUEST → PLANNING → ARTIFACT_EMIT → COMPLETED

No intermediate waiting or stalling for PLAN_REQUEST.

State transitions:
    1. RECEIVED    — Request comes in
    2. CLASSIFIED  — Intent determined
    3. PLANNING    — Plan builder runs (PLAN_REQUEST path)
    4. ARTIFACT_EMIT — Artifact rendered to markdown
    5. COMPLETED   — Terminal outcome emitted

Public API:
    PlanningPipeline(env_context=None)
    pipeline.process(user_text) -> PipelineResult
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from plan_types import OutcomeType, PlanArtifact
from intent_classifier import classify_intent, IntentType
from plan_builder import build_plan_artifact, EnvContext
from plan_renderer import render_plan_artifact_markdown
from response_governor import (
    detect_forbidden_async_language,
    enforce_no_simulated_work,
    ResponseContext,
    JobContext,
)
from output_gate import renderable_artifact_gate

logger = logging.getLogger(__name__)


# =============================================================================
# Pipeline State
# =============================================================================


class PipelineState(str, Enum):
    """State transitions for the planning pipeline."""
    RECEIVED = "received"
    CLASSIFIED = "classified"
    PLANNING = "planning"
    ARTIFACT_EMIT = "artifact_emit"
    COMPLETED = "completed"
    BLOCKED_NEEDS_INPUT = "blocked_needs_input"
    CANNOT_COMPLETE = "cannot_complete"


# =============================================================================
# Pipeline Result
# =============================================================================


@dataclass
class PipelineResult:
    """Result of processing a request through the planning pipeline."""
    outcome: OutcomeType
    intent: IntentType
    rendered_output: str
    artifact: Optional[PlanArtifact] = None
    state_trace: List[str] = field(default_factory=list)
    """Ordered list of states traversed."""


# =============================================================================
# Pipeline
# =============================================================================


class PlanningPipeline:
    """
    End-to-end planning pipeline enforcing Honest Closure.

    For PLAN_REQUEST:
        classify → build plan → render → governor check → output gate → COMPLETED

    For other intents, returns the classification and defers to the
    caller (orchestrator) for handling.
    """

    def __init__(self, env_context: Optional[EnvContext] = None):
        self._env_context = env_context or EnvContext()

    def process(
        self,
        user_text: str,
        job_context: Optional[JobContext] = None,
    ) -> PipelineResult:
        """
        Process a user request through the planning pipeline.

        Args:
            user_text: The raw user message.
            job_context: Optional async job context.

        Returns:
            PipelineResult with outcome, rendered output, and state trace.
        """
        trace: List[str] = []

        # ── 1. RECEIVED ───────────────────────────────────────────────
        trace.append(PipelineState.RECEIVED.value)

        # ── 2. CLASSIFIED ─────────────────────────────────────────────
        intent = classify_intent(user_text)
        trace.append(f"{PipelineState.CLASSIFIED.value}:{intent.value}")
        logger.info("Planning pipeline: classified as %s", intent.value)

        # ── Route by intent ───────────────────────────────────────────
        if intent == IntentType.PLAN_REQUEST:
            return self._handle_plan_request(user_text, job_context, intent, trace)

        if intent == IntentType.MIXED_REQUEST:
            return self._handle_mixed_request(user_text, job_context, intent, trace)

        if intent == IntentType.EXEC_REQUEST:
            # EXEC_REQUEST still needs a PlanArtifact — the orchestrator
            # compiles it into a TaskGraph → permission prompt → execution.
            return self._handle_plan_request(user_text, job_context, intent, trace)

        # For KNOWLEDGE_REQUEST, AMBIGUOUS — return classification
        # and let the orchestrator handle downstream.
        return PipelineResult(
            outcome=OutcomeType.COMPLETED_WITH_RECEIPT,
            intent=intent,
            rendered_output="",  # No plan to render
            state_trace=trace,
        )

    # ------------------------------------------------------------------
    # PLAN_REQUEST Handler
    # ------------------------------------------------------------------

    def _handle_plan_request(
        self,
        user_text: str,
        job_context: Optional[JobContext],
        intent: IntentType,
        trace: List[str],
    ) -> PipelineResult:
        """Handle a pure PLAN_REQUEST — must emit PlanArtifact same turn."""
        # ── 3. PLANNING ───────────────────────────────────────────────
        trace.append(PipelineState.PLANNING.value)
        artifact = build_plan_artifact(user_text, self._env_context)

        # ── 4. ARTIFACT_EMIT ──────────────────────────────────────────
        trace.append(PipelineState.ARTIFACT_EMIT.value)

        # Output gate check
        gate_result = renderable_artifact_gate(artifact)
        if not gate_result.allowed:
            trace.append(PipelineState.CANNOT_COMPLETE.value)
            return PipelineResult(
                outcome=OutcomeType.CANNOT_COMPLETE,
                intent=intent,
                rendered_output="Unable to produce a valid plan artifact.",
                state_trace=trace,
            )

        # Render to markdown
        rendered = render_plan_artifact_markdown(artifact)

        # Governor check on rendered output
        gov_result = enforce_no_simulated_work(
            ResponseContext(text=rendered),
            job_context,
        )
        if not gov_result.passed:
            logger.warning(
                "Governor blocked plan output: %s", gov_result.reason
            )
            trace.append(PipelineState.CANNOT_COMPLETE.value)
            return PipelineResult(
                outcome=gov_result.recommended_outcome or OutcomeType.CANNOT_COMPLETE,
                intent=intent,
                rendered_output="Plan output contained disallowed language.",
                state_trace=trace,
            )

        # ── 5. COMPLETED ─────────────────────────────────────────────
        trace.append(PipelineState.COMPLETED.value)
        return PipelineResult(
            outcome=OutcomeType.COMPLETED_WITH_PLAN_ARTIFACT,
            intent=intent,
            rendered_output=rendered,
            artifact=artifact,
            state_trace=trace,
        )

    # ------------------------------------------------------------------
    # MIXED_REQUEST Handler
    # ------------------------------------------------------------------

    def _handle_mixed_request(
        self,
        user_text: str,
        job_context: Optional[JobContext],
        intent: IntentType,
        trace: List[str],
    ) -> PipelineResult:
        """
        Handle MIXED_REQUEST — emit plan immediately, propose execution
        as a separate follow-on step via next_action.
        """
        # Emit plan immediately (same as PLAN_REQUEST)
        result = self._handle_plan_request(user_text, job_context, intent, trace)

        # If plan succeeded, ensure next_action references execution
        if result.artifact and result.outcome == OutcomeType.COMPLETED_WITH_PLAN_ARTIFACT:
            # The plan builder already sets next_action, but for mixed requests
            # we want to make it clear that execution is a separate step
            if not any("execut" in step.lower() for step in result.artifact.plan_steps):
                result.artifact.plan_steps.append(
                    "Execute the plan once reviewed and approved"
                )
            # Re-render with updated steps
            result.rendered_output = render_plan_artifact_markdown(result.artifact)

        return result
