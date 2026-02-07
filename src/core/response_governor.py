"""
Response Governor — "No Simulated Work" Policy Enforcement.
============================================================

Detects and blocks forbidden async-progress language when no real
async job exists. This is a hard enforcement gate, not a guideline.

Forbidden phrases (without a real job_id):
    - "I'm working on it"
    - "I'm investigating"
    - "Please allow me time"
    - "I will report back"
    - "I'm processing your request"

If forbidden language is detected and no job_id exists, the response
must be rewritten to one of:
    - COMPLETED_WITH_PLAN_ARTIFACT
    - NEEDS_INPUT
    - CANNOT_COMPLETE

Public API:
    detect_forbidden_async_language(text: str) -> List[str]
    enforce_no_simulated_work(response_ctx, job_context) -> EnforcementResult
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from plan_types import OutcomeType


# =============================================================================
# Forbidden Phrases
# =============================================================================

FORBIDDEN_PHRASES: list[str] = [
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

# Compile patterns for efficient matching
_FORBIDDEN_PATTERNS: list[re.Pattern] = [
    re.compile(re.escape(phrase), re.IGNORECASE)
    for phrase in FORBIDDEN_PHRASES
]


# =============================================================================
# Job Context
# =============================================================================


@dataclass
class JobContext:
    """Describes whether a real async job exists."""
    job_id: Optional[str] = None
    status: Optional[str] = None

    @property
    def has_real_job(self) -> bool:
        return self.job_id is not None and self.job_id.strip() != ""


# =============================================================================
# Response Context
# =============================================================================


@dataclass
class ResponseContext:
    """The response being checked by the governor."""
    text: str
    outcome: Optional[OutcomeType] = None


# =============================================================================
# Enforcement Result
# =============================================================================


@dataclass
class EnforcementResult:
    """Result of the governor enforcement check."""
    passed: bool
    """True if the response is allowed as-is."""

    violations: List[str]
    """List of forbidden phrases found."""

    recommended_outcome: Optional[OutcomeType] = None
    """If blocked, the recommended outcome to use instead."""

    reason: Optional[str] = None
    """Human-readable explanation."""


# =============================================================================
# Detection
# =============================================================================


def detect_forbidden_async_language(text: str) -> List[str]:
    """
    Scan text for forbidden async-progress phrases.

    Returns:
        List of matched forbidden phrases. Empty if clean.
    """
    if not text:
        return []

    found: List[str] = []
    for pattern in _FORBIDDEN_PATTERNS:
        if pattern.search(text):
            found.append(pattern.pattern.replace("\\", ""))
    return found


# =============================================================================
# Enforcement
# =============================================================================


def enforce_no_simulated_work(
    response_ctx: ResponseContext,
    job_context: Optional[JobContext] = None,
) -> EnforcementResult:
    """
    Enforce the "No Simulated Work" policy.

    If forbidden phrases are detected AND no real job_id exists,
    the response is blocked and a recommended outcome is provided.

    If a real job_id exists, forbidden phrases are allowed (the
    progress language is backed by real work).

    Args:
        response_ctx: The response to check.
        job_context: Optional job context with job_id.

    Returns:
        EnforcementResult indicating whether the response passes.
    """
    job_ctx = job_context or JobContext()

    violations = detect_forbidden_async_language(response_ctx.text)

    # No violations → pass
    if not violations:
        return EnforcementResult(
            passed=True,
            violations=[],
        )

    # Violations exist but real job exists → allowed
    if job_ctx.has_real_job:
        return EnforcementResult(
            passed=True,
            violations=violations,
            reason=f"Async language allowed — backed by job {job_ctx.job_id}",
        )

    # Violations exist and no real job → BLOCKED
    # Recommend an outcome based on the current response
    recommended = OutcomeType.CANNOT_COMPLETE

    return EnforcementResult(
        passed=False,
        violations=violations,
        recommended_outcome=recommended,
        reason=(
            "Response contains simulated progress language with no real async job. "
            f"Detected: {', '.join(violations)}"
        ),
    )
