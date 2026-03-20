# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Fork Permission Evaluator — Soul-governed validation for time-travel operations.

Evaluates whether a fork/replay request is permitted under the current Soul's
fork_permissions block. Produces FORK_SOUL_REJECTED receipts on denial.

Public API:
    evaluate_fork_request(soul, mode, modifications) → ForkDecision
    evaluate_replay_request(soul) → ForkDecision
    evaluate_inspect_request(soul) → ForkDecision
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TimeTravelMode(str, Enum):
    """Time-travel operation modes."""
    INSPECT = "inspect"   # Read-only state viewing
    REPLAY = "replay"     # Re-execute unchanged, new quest_id, current Soul
    FORK = "fork"         # Modify inputs, Soul-validated, T3 approval gate


@dataclass(frozen=True)
class ForkDecision:
    """Result of a fork permission evaluation.

    Attributes:
        allowed: Whether the operation is permitted.
        mode: The requested time-travel mode.
        reason: Human-readable explanation of the decision.
        rejected_fields: Fields that failed validation (fork mode only).
        required_approval_tier: Minimum tier needed (if allowed).
    """
    allowed: bool
    mode: TimeTravelMode
    reason: str
    rejected_fields: List[str] = field(default_factory=list)
    required_approval_tier: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "mode": self.mode.value,
            "reason": self.reason,
            "rejected_fields": self.rejected_fields,
            "required_approval_tier": self.required_approval_tier,
        }


def evaluate_inspect_request(soul: Any) -> ForkDecision:
    """Evaluate whether an INSPECT (read-only) operation is allowed.

    INSPECT is always allowed — it's read-only and produces no side effects.
    The Soul's fork_permissions.allow_fork does NOT gate inspection.
    """
    return ForkDecision(
        allowed=True,
        mode=TimeTravelMode.INSPECT,
        reason="INSPECT mode is read-only and always permitted.",
        required_approval_tier=0,
    )


def evaluate_replay_request(soul: Any) -> ForkDecision:
    """Evaluate whether a REPLAY operation is allowed under the current Soul.

    REPLAY re-executes an unchanged quest with the current Soul. It requires
    fork_permissions.allow_fork to be true (since it creates new receipts)
    but does NOT check modifiable_fields (no modifications are made).
    """
    fork_perms = getattr(soul, "fork_permissions", None)

    if fork_perms is None:
        return ForkDecision(
            allowed=False,
            mode=TimeTravelMode.REPLAY,
            reason="Soul has no fork_permissions block — replay denied by default.",
        )

    if not fork_perms.allow_fork:
        return ForkDecision(
            allowed=False,
            mode=TimeTravelMode.REPLAY,
            reason="Soul fork_permissions.allow_fork is false — replay denied.",
        )

    return ForkDecision(
        allowed=True,
        mode=TimeTravelMode.REPLAY,
        reason="Replay permitted under current Soul fork_permissions.",
        required_approval_tier=fork_perms.require_approval_tier,
    )


def evaluate_fork_request(
    soul: Any,
    modifications: Dict[str, Any],
) -> ForkDecision:
    """Evaluate whether a FORK operation is allowed under the current Soul.

    Fork validation pipeline:
    1. Check allow_fork master switch
    2. Check each modified field against prohibited_modifications
    3. Check each modified field against modifiable_fields allowlist
    4. Return decision with required approval tier

    Args:
        soul: The active Soul instance.
        modifications: Dict of field paths → new values that the fork
                       intends to change (e.g., {"inputs.query": "new prompt"}).

    Returns:
        ForkDecision with allowed=True/False and details.
    """
    fork_perms = getattr(soul, "fork_permissions", None)

    if fork_perms is None:
        return ForkDecision(
            allowed=False,
            mode=TimeTravelMode.FORK,
            reason="Soul has no fork_permissions block — fork denied by default.",
        )

    if not fork_perms.allow_fork:
        return ForkDecision(
            allowed=False,
            mode=TimeTravelMode.FORK,
            reason="Soul fork_permissions.allow_fork is false — fork denied.",
        )

    if not modifications:
        # No modifications = effectively a replay, but requested as fork
        return ForkDecision(
            allowed=True,
            mode=TimeTravelMode.FORK,
            reason="Fork with no modifications — treated as replay.",
            required_approval_tier=fork_perms.require_approval_tier,
        )

    # Check prohibited modifications (architectural enforcement)
    prohibited = set(fork_perms.prohibited_modifications)
    rejected_prohibited = []
    for field_path in modifications:
        # Check both the full path and the root field name
        root_field = field_path.split(".")[0]
        if field_path in prohibited or root_field in prohibited:
            rejected_prohibited.append(field_path)

    if rejected_prohibited:
        return ForkDecision(
            allowed=False,
            mode=TimeTravelMode.FORK,
            reason=(
                f"Fork rejected — attempted to modify prohibited fields: "
                f"{', '.join(rejected_prohibited)}. These fields are "
                f"architecturally protected and cannot be changed."
            ),
            rejected_fields=rejected_prohibited,
        )

    # Check modifiable_fields allowlist
    allowed_fields = set(fork_perms.modifiable_fields)
    if not allowed_fields:
        return ForkDecision(
            allowed=False,
            mode=TimeTravelMode.FORK,
            reason=(
                "Fork rejected — modifiable_fields is empty. "
                "No field modifications are permitted by the Soul."
            ),
            rejected_fields=list(modifications.keys()),
        )

    rejected_not_allowed = []
    for field_path in modifications:
        # Support prefix matching: if "inputs" is in modifiable_fields,
        # "inputs.query" is allowed. Also exact match.
        field_allowed = False
        if field_path in allowed_fields:
            field_allowed = True
        else:
            for allowed in allowed_fields:
                if field_path.startswith(allowed + "."):
                    field_allowed = True
                    break
        if not field_allowed:
            rejected_not_allowed.append(field_path)

    if rejected_not_allowed:
        return ForkDecision(
            allowed=False,
            mode=TimeTravelMode.FORK,
            reason=(
                f"Fork rejected — fields not in modifiable_fields allowlist: "
                f"{', '.join(rejected_not_allowed)}. "
                f"Allowed: {', '.join(sorted(allowed_fields))}."
            ),
            rejected_fields=rejected_not_allowed,
        )

    return ForkDecision(
        allowed=True,
        mode=TimeTravelMode.FORK,
        reason="Fork permitted — all modified fields are in the Soul allowlist.",
        required_approval_tier=fork_perms.require_approval_tier,
    )


def create_rejection_receipt_data(
    decision: ForkDecision,
    quest_id: str,
    operator_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build receipt data dict for a FORK_SOUL_REJECTED receipt.

    This does NOT create the receipt — the caller is responsible for
    persisting it via ReceiptService. This function just structures
    the data correctly.
    """
    return {
        "action_type": "fork_soul_rejected",
        "action_name": f"soul_fork_validation_{decision.mode.value}",
        "inputs": {
            "requested_mode": decision.mode.value,
            "quest_id": quest_id,
        },
        "outputs": decision.to_dict(),
        "status": "failure",
        "tier": 0,
        "quest_id": quest_id,
        "operator_id": operator_id,
        "metadata": {
            "subsystem": "time_travel",
            "rejection_reason": decision.reason,
            "rejected_fields": decision.rejected_fields,
        },
    }
