"""
Soul Linter — validates Soul invariants beyond schema (Prompt 2 / A2).

The linter enforces constitutional invariants that the Pydantic schema
cannot express (e.g. "destructive actions *must* appear in requires_approval").

Public API:
    LintIssue         — dataclass describing one issue
    LintSeverity      — "critical" | "warning"
    lint(soul) -> list[LintIssue]
    lint_or_raise(soul)  — raises SoulStoreError on critical issues
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import List

from src.core.soul.store import Soul, SoulStoreError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class LintSeverity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"


@dataclass(frozen=True)
class LintIssue:
    """A single lint finding."""
    rule: str
    severity: LintSeverity
    message: str


# ---------------------------------------------------------------------------
# Invariant checkers  (each returns a list of issues)
# ---------------------------------------------------------------------------

def _check_destructive_actions_require_approval(soul: Soul) -> List[LintIssue]:
    """Destructive action categories must appear in requires_approval."""
    required_keywords = {"delete", "deploy", "destroy", "drop"}
    approval_set = {a.lower() for a in soul.autonomy_posture.requires_approval}

    # Check that at least one destructive keyword appears
    has_destructive_coverage = any(
        any(kw in entry for kw in required_keywords)
        for entry in approval_set
    )

    if not has_destructive_coverage:
        return [
            LintIssue(
                rule="destructive_actions_require_approval",
                severity=LintSeverity.CRITICAL,
                message=(
                    "autonomy_posture.requires_approval must include at least "
                    "one destructive action keyword (delete, deploy, destroy, drop)."
                ),
            )
        ]

    # Also check risk_rules for a matching rule
    risk_names = {r.name.lower() for r in soul.risk_rules}
    has_risk_rule = any(
        "destruct" in name or "approval" in name for name in risk_names
    )
    if not has_risk_rule:
        return [
            LintIssue(
                rule="destructive_actions_require_approval",
                severity=LintSeverity.WARNING,
                message=(
                    "risk_rules should include an enforced rule covering "
                    "destructive actions."
                ),
            )
        ]

    return []


def _check_no_silent_degradation(soul: Soul) -> List[LintIssue]:
    """Tone invariants must prohibit silent degradation / suppressed errors."""
    combined = " ".join(soul.tone_invariants).lower()

    silence_keywords = {"suppress", "silent", "degrade", "error", "failure"}
    has_coverage = sum(1 for kw in silence_keywords if kw in combined) >= 2

    if not has_coverage:
        return [
            LintIssue(
                rule="no_silent_degradation",
                severity=LintSeverity.CRITICAL,
                message=(
                    "tone_invariants must prohibit silent degradation — "
                    "include statements about reporting errors / not suppressing failures."
                ),
            )
        ]
    return []


def _check_scheduling_no_autonomous_irreversible(soul: Soul) -> List[LintIssue]:
    """Scheduling boundaries must prevent autonomous irreversible actions."""
    sb = soul.scheduling_boundaries

    if not sb.no_autonomous_irreversible:
        return [
            LintIssue(
                rule="scheduling_no_autonomous_irreversible",
                severity=LintSeverity.CRITICAL,
                message=(
                    "scheduling_boundaries.no_autonomous_irreversible must be true."
                ),
            )
        ]
    return []


def _check_approval_channels_exist(soul: Soul) -> List[LintIssue]:
    """Approval rules must define at least one channel."""
    if not soul.approval_rules.channels:
        return [
            LintIssue(
                rule="approval_channels_required",
                severity=LintSeverity.CRITICAL,
                message="approval_rules.channels must contain at least one channel.",
            )
        ]
    return []


def _check_memory_ethics_present(soul: Soul) -> List[LintIssue]:
    """Memory ethics must have at least one rule."""
    if not soul.memory_ethics:
        return [
            LintIssue(
                rule="memory_ethics_required",
                severity=LintSeverity.WARNING,
                message="memory_ethics should contain at least one rule.",
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Registry of all checks
# ---------------------------------------------------------------------------

_CHECKS = [
    _check_destructive_actions_require_approval,
    _check_no_silent_degradation,
    _check_scheduling_no_autonomous_irreversible,
    _check_approval_channels_exist,
    _check_memory_ethics_present,
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def lint(soul: Soul) -> List[LintIssue]:
    """Run all invariant checks against a Soul, returning issues found."""
    issues: List[LintIssue] = []
    for check in _CHECKS:
        issues.extend(check(soul))
    return issues


def lint_or_raise(soul: Soul) -> List[LintIssue]:
    """Run lint and raise SoulStoreError if any critical issues exist.

    Returns the full list of issues (including warnings) on success.
    """
    issues = lint(soul)
    critical = [i for i in issues if i.severity == LintSeverity.CRITICAL]

    if critical:
        details = "; ".join(f"[{i.rule}] {i.message}" for i in critical)
        raise SoulStoreError(f"Soul lint failed — {len(critical)} critical issue(s): {details}")

    for issue in issues:
        logger.warning("Soul lint warning: [%s] %s", issue.rule, issue.message)

    return issues
