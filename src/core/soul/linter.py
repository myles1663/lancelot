# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Soul linter for invariants that extend beyond schema validation.

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


DESTRUCTIVE_CAPABILITIES = frozenset({
    "data.delete",
    "deploy.release",
    "storage.drop",
    "config.reset",
    "agent.terminate",
})
DESTRUCTIVE_CAPABILITY_ALIASES = frozenset({
    "delete",
    "deploy",
    "destroy",
    "drop",
})
REQUIRED_GOVERNANCE_INVARIANTS = frozenset({
    "error_handling.explicit",
    "failure_reporting.required",
    "audit_trail.mandatory",
})
GOVERNANCE_INVARIANT_ALIASES = {
    "error_handling.explicit": frozenset({"error", "errors"}),
    "failure_reporting.required": frozenset({
        "failure",
        "failures",
        "report",
        "reporting",
        "suppress",
        "silent",
        "degrade",
    }),
    "audit_trail.mandatory": frozenset({"audit", "receipt", "trace", "log"}),
}


def _normalize_capability_id(value: str) -> str:
    """Normalize free-form entries toward capability-id style matching.

    Soul documents still carry some legacy free-text entries, so the linter
    matches exact capability ids first and falls back to legacy token checks.
    """
    normalized = value.strip().lower()
    for old, new in (("_", "."), (" ", "."), ("-", "."), ("/", ".")):
        normalized = normalized.replace(old, new)
    return normalized


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
    approval_entries = {
        _normalize_capability_id(entry)
        for entry in soul.autonomy_posture.requires_approval
    }

    has_destructive_coverage = any(
        entry in DESTRUCTIVE_CAPABILITIES
        or any(alias in entry for alias in DESTRUCTIVE_CAPABILITY_ALIASES)
        for entry in approval_entries
    )

    if not has_destructive_coverage:
        return [
            LintIssue(
                rule="destructive_actions_require_approval",
                severity=LintSeverity.CRITICAL,
                message=(
                    "autonomy_posture.requires_approval must include at least "
                    "one destructive capability entry such as data.delete, "
                    "deploy.release, storage.drop, config.reset, or agent.terminate."
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
    normalized = _normalize_capability_id(combined)

    has_structured_coverage = any(
        invariant in normalized for invariant in REQUIRED_GOVERNANCE_INVARIANTS
    )
    legacy_signal_count = sum(
        1
        for tokens in GOVERNANCE_INVARIANT_ALIASES.values()
        if any(token in combined for token in tokens)
    )
    has_coverage = has_structured_coverage or legacy_signal_count >= 1

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
