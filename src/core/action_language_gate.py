"""
Action Language Gate — prevents phantom execution claims.

No "Action:" or execution-tense language unless a real TaskRun exists
with at least one receipt. This is a hard invariant (AC1 from Fix Pack V1).
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class GateResult:
    """Result of an action language gate check."""
    passed: bool = True
    violations: List[str] = field(default_factory=list)
    corrected_text: str = ""


# Patterns that imply execution is happening right now
_EXECUTION_PATTERNS = [
    re.compile(r"^Action:\s", re.MULTILINE),
    re.compile(r"(?:executing|executed)\s+step\b", re.IGNORECASE),
    re.compile(r"running\s+command\b", re.IGNORECASE),
    re.compile(r"deploying\s+to\b", re.IGNORECASE),
    re.compile(r"writing\s+(?:to\s+)?file\b", re.IGNORECASE),
    re.compile(r"modifying\s+(?:the\s+)?\w+", re.IGNORECASE),
    re.compile(
        r"i(?:'m| am)\s+(?:now\s+)?(?:executing|deploying|running|writing|modifying|creating|deleting)",
        re.IGNORECASE,
    ),
    re.compile(r"i\s+(?:have|just)\s+(?:executed|deployed|created|modified|deleted|written)", re.IGNORECASE),
    re.compile(r"successfully\s+(?:executed|deployed|created|modified|deleted|written)", re.IGNORECASE),
    re.compile(r"completed\s+step\s+\d", re.IGNORECASE),
]

# Map of patterns to plan-only replacements
_PLAN_ALTERNATIVES = {
    r"^Action:\s": "Planned action: ",
    r"executing step": "planned step",
    r"running command": "planned command",
    r"deploying to": "will deploy to (pending approval)",
    r"writing file": "will write file (pending approval)",
    r"writing to file": "will write to file (pending approval)",
    r"i'm now executing": "I plan to execute",
    r"i am now executing": "I plan to execute",
    r"i'm executing": "I plan to execute",
    r"i am executing": "I plan to execute",
    r"i have executed": "I plan to execute",
    r"i just executed": "I plan to execute",
    r"successfully executed": "will execute (pending approval)",
    r"successfully deployed": "will deploy (pending approval)",
    r"successfully created": "will create (pending approval)",
    r"completed step": "planned step",
}


def check_action_language(text: str, task_run=None) -> GateResult:
    """Check text for execution-tense claims.

    If a TaskRun exists with status QUEUED/RUNNING/BLOCKED and at least
    one receipt, execution language is allowed.

    Args:
        text: The response text to check.
        task_run: Optional TaskRun object with .status and .receipts_index.

    Returns:
        GateResult with passed=True if OK, or violations + corrected text.
    """
    # If backed by a real task run with receipts, allow
    if task_run is not None:
        status = task_run.status
        if hasattr(status, 'value'):
            status = status.value
        if status in ("QUEUED", "RUNNING", "BLOCKED"):
            receipts = getattr(task_run, 'receipts_index', [])
            if len(receipts) > 0:
                return GateResult(passed=True, corrected_text=text)

    # Check for execution language
    violations = []
    for pattern in _EXECUTION_PATTERNS:
        matches = pattern.findall(text)
        for match in matches:
            violations.append(match.strip() if isinstance(match, str) else pattern.pattern)

    if not violations:
        return GateResult(passed=True, corrected_text=text)

    # Rewrite execution claims to plan-only language
    corrected = text
    for pattern_str, replacement in _PLAN_ALTERNATIVES.items():
        corrected = re.sub(pattern_str, replacement, corrected, flags=re.IGNORECASE | re.MULTILINE)

    logger.info("Action Language Gate: %d violations rewritten", len(violations))
    return GateResult(
        passed=False,
        violations=violations,
        corrected_text=corrected,
    )
