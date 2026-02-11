"""
Lancelot vNext4: Risk-Tiered Governance Data Models

Core data types for risk classification, verification tracking,
and receipt management. These models have zero external dependencies
beyond stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, IntEnum
from typing import Optional


# ── Risk Tier Enum ───────────────────────────────────────────────

class RiskTier(IntEnum):
    """Risk classification tiers for tool actions.

    T0: Read-only, no side effects, workspace-scoped
    T1: Has side effects, fully reversible
    T2: Has side effects, partially reversible, requires policy confirmation
    T3: Cannot be undone, or affects external systems
    """
    T0_INERT = 0
    T1_REVERSIBLE = 1
    T2_CONTROLLED = 2
    T3_IRREVERSIBLE = 3


# ── Verification Enums ───────────────────────────────────────────

class VerificationStrategy(str, Enum):
    """How an action is verified after execution."""
    NONE = "none"       # T0: no verification needed
    ASYNC = "async"     # T1: verified in background
    SYNC = "sync"       # T2, T3: verified before next step


class VerificationStatus(str, Enum):
    """Lifecycle status of verification for an action."""
    SKIPPED = "skipped"
    ASYNC_PENDING = "async_pending"
    ASYNC_PASSED = "async_passed"
    ASYNC_FAILED = "async_failed"
    SYNC_PASSED = "sync_passed"
    SYNC_FAILED = "sync_failed"


# ── Risk Classification Dataclasses ──────────────────────────────

_TIER_LABELS = {
    RiskTier.T0_INERT: "inert",
    RiskTier.T1_REVERSIBLE: "reversible",
    RiskTier.T2_CONTROLLED: "controlled",
    RiskTier.T3_IRREVERSIBLE: "irreversible",
}


@dataclass(frozen=True)
class RiskClassification:
    """Precomputed governance requirements for a given risk tier."""
    tier: RiskTier
    label: str
    requires_sync_verify: bool
    requires_approval: bool
    batchable_receipt: bool
    reason: str

    @classmethod
    def from_tier(cls, tier: RiskTier, reason: str = "") -> RiskClassification:
        """Auto-derive governance requirements from tier value."""
        return cls(
            tier=tier,
            label=_TIER_LABELS[tier],
            requires_sync_verify=tier >= RiskTier.T2_CONTROLLED,
            requires_approval=tier >= RiskTier.T3_IRREVERSIBLE,
            batchable_receipt=tier <= RiskTier.T1_REVERSIBLE,
            reason=reason,
        )


@dataclass(frozen=True)
class ActionRiskProfile:
    """Runtime risk profile for a specific action invocation."""
    tier: RiskTier
    capability: str
    tool_id: str = ""
    scope: str = "workspace"  # "workspace" | "network" | "system" | "external"
    reversible: bool = True
    soul_escalation: Optional[str] = None  # escalation rule ID if Soul overrode default
    classified_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ── Receipt Type Constants ───────────────────────────────────────

RECEIPT_TYPE_BATCH = "batch_receipt"
RECEIPT_TYPE_VERIFICATION = "verification_receipt"
RECEIPT_TYPE_VERIFICATION_FAILED = "verification_failed"
RECEIPT_TYPE_ROLLBACK = "rollback"
RECEIPT_TYPE_TEMPLATE_MATCH = "template_match"
RECEIPT_TYPE_POLICY_CACHE_HIT = "policy_cache_hit"
