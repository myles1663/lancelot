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
    # v2: Patterns caught from real Lancelot stalling output
    "i will provide",
    "i'll provide",
    "actively compiling",
    "actively processing",
    "deliver it shortly",
    "deliver shortly",
    "i am currently",
    "i'm currently",
    "compiling a detailed",
    "compiling the",
    "i will have",
    "i'll have",
    "in the process of",
    "i will deliver",
    "i'll deliver",
    "allow me to",
    "working on this",
    "preparing a detailed",
    "preparing a comprehensive",
    "i am actively",
    "i'm actively",
    # v3: Fake work proposal patterns
    "i will now proceed with",
    "i'll now proceed with",
    "i will proceed with",
    "i'll proceed with",
    "feasibility study",
    "feasibility assessment",
    "feasibility analysis",
    "i recommend starting with",
    "assess the viability",
    "assessing the viability",
    "once the feasibility is confirmed",
    "once feasibility is confirmed",
    "i will begin by",
    "i'll begin by",
    "let me begin by",
    "i will start by researching",
    "i'll start by researching",
    "prototype development",
    "proof of concept phase",
    "initial research phase",
    "research phase",
    "discovery phase",
    "assessment phase",
    "i will conduct",
    "i'll conduct",
    "i will research",
    "i'll research",
    "i will investigate",
    "i'll investigate",
    "i will analyze the",
    "i'll analyze the",
    "i will evaluate",
    "i'll evaluate",
    "i will explore",
    "i'll explore",
    "i will assess",
    "i'll assess",
    "let me assess",
    "let me evaluate",
    "let me research",
    "let me investigate",
    "let me explore",
    "let me analyze",
    # v4: Stalling / idle posturing without real work
    "awaiting further instructions",
    "awaiting your instructions",
    "awaiting your command",
    "ready and awaiting",
    "standing by for",
    "awaiting your next",
    "waiting for your",
    "at your command",
]

# Compile patterns for efficient matching
_FORBIDDEN_PATTERNS: list[re.Pattern] = [
    re.compile(re.escape(phrase), re.IGNORECASE)
    for phrase in FORBIDDEN_PHRASES
]


# =============================================================================
# Structural Fake Work Detection Patterns
# =============================================================================

# Matches time estimates like "(1 hour)", "(2-3 hours)", "(30 minutes)", "(1 day)"
_TIME_ESTIMATE_PATTERN = re.compile(
    r'\(\s*\d+(?:\s*-\s*\d+)?\s*(?:hour|hr|minute|min|day|week|month)s?\s*\)',
    re.IGNORECASE,
)

# Matches "Phase 1:", "Stage 2:" headers in fake work proposals
_PHASE_HEADER_PATTERN = re.compile(
    r'(?:phase|stage)\s+\d+\s*[:\-]\s*\w',
    re.IGNORECASE,
)

# Matches "I recommend starting with" pattern
_RECOMMEND_STARTING_PATTERN = re.compile(
    r'i\s+recommend\s+starting\s+with',
    re.IGNORECASE,
)


def detect_fake_work_proposal(text: str) -> Optional[str]:
    """
    Detect structural fake work proposals — multi-phase plans with time
    estimates that propose work the LLM cannot actually execute.

    Unlike detect_forbidden_async_language() which catches individual
    phrases, this function detects the *pattern* of a fake work proposal
    by scoring multiple heuristic signals.

    Returns:
        A reason string if a fake work proposal is detected, None if clean.
    """
    if not text or len(text) < 50:
        return None

    text_lower = text.lower()
    score = 0
    signals: list[str] = []

    # Signal 1: Time estimates like "(2 hours)", "(1-2 days)"
    time_matches = _TIME_ESTIMATE_PATTERN.findall(text)
    if time_matches:
        score += 3 * len(time_matches)
        signals.append(f"time_estimates({len(time_matches)})")

    # Signal 2: Phase/stage headers like "Phase 1: Research"
    phase_matches = _PHASE_HEADER_PATTERN.findall(text)
    if phase_matches:
        score += 2 * len(phase_matches)
        signals.append(f"phase_headers({len(phase_matches)})")

    # Signal 3: "I recommend starting with" pattern
    if _RECOMMEND_STARTING_PATTERN.search(text):
        score += 3
        signals.append("recommend_starting")

    # Signal 4: Multiple future-tense "I will" actions (>=3)
    i_will_count = len(re.findall(r'\bi\s+will\s+(?!not\b)', text_lower))
    if i_will_count >= 3:
        score += 2 * (i_will_count - 2)
        signals.append(f"i_will_count({i_will_count})")

    # Signal 5: Keywords that indicate proposed-but-unexecutable work
    proposal_keywords = [
        "feasibility", "viability", "proof of concept", "prototype",
        "pilot program", "initial assessment", "preliminary",
        "research phase", "discovery phase", "investigation phase",
        "assessment phase", "evaluation phase",
    ]
    keyword_hits = sum(1 for kw in proposal_keywords if kw in text_lower)
    if keyword_hits >= 1:
        score += 2 * keyword_hits
        signals.append(f"proposal_keywords({keyword_hits})")

    # Signal 6: The response proposes sequential work over time
    timeline_indicators = [
        "after completing", "once complete", "upon completion",
        "following the", "in the next phase", "in the subsequent",
        "will then", "next we will", "next i will",
        "then i will", "afterward",
    ]
    timeline_hits = sum(1 for t in timeline_indicators if t in text_lower)
    if timeline_hits >= 2:
        score += 2 * timeline_hits
        signals.append(f"timeline_indicators({timeline_hits})")

    # Threshold: score >= 5 indicates a fake work proposal
    if score >= 5:
        return (
            f"Fake work proposal detected (score={score}): "
            f"{', '.join(signals)}"
        )

    return None


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


# Fix Pack V6/V14: Phrases allowed ONLY when backed by REAL tool receipts.
# V14: Removed future-tense stalling phrases. Only past-tense (proof of
# completed work) and imperative "let me X" (system will immediately execute)
# are allowed. "I will provide" and "I'll proceed with" were letting stalling
# slip through when no tools were actually called.
_AGENTIC_ALLOWED_PHRASES = {
    # Past-tense ONLY: tool-backed claims about work already done
    "i researched", "i found", "i checked", "i discovered",
    "i fetched", "i retrieved", "i looked up", "i queried",
    "i investigated", "i explored", "i analyzed",
    "based on my research", "after researching",
    "i evaluated", "i assessed",
}


def filter_forbidden_for_agentic_context(violations: List[str], has_tool_receipts: bool = False) -> List[str]:
    """Filter out research-related violations when tool receipts are present.

    Fix Pack V6: When the agentic loop has actually called tools (network_client,
    command_runner, etc.), phrases like "I researched X" or "I will investigate"
    are legitimate — they describe real tool-backed work, not simulated progress.

    Args:
        violations: List of detected forbidden phrases.
        has_tool_receipts: Whether real tool calls were made in this turn.

    Returns:
        Filtered list of violations with research phrases removed if backed by receipts.
    """
    if not has_tool_receipts or not violations:
        return violations

    filtered = []
    for v in violations:
        v_lower = v.lower()
        if any(allowed in v_lower for allowed in _AGENTIC_ALLOWED_PHRASES):
            continue  # Skip — this is legitimate tool-backed language
        filtered.append(v)
    return filtered


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

    # Also check for structural fake work proposals
    fake_work_reason = detect_fake_work_proposal(response_ctx.text)
    if fake_work_reason:
        violations.append(fake_work_reason)

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
