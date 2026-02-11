"""
APL Data Models — DecisionContext, DecisionRecord, ApprovalPattern,
AutomationRule, RuleCheckResult.
"""

from __future__ import annotations

import fnmatch
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from src.core.governance.models import RiskTier


# ── Decision Context ────────────────────────────────────────────


@dataclass(frozen=True)
class DecisionContext:
    """Full context of an action that required approval."""

    # What action
    capability: str               # "connector.email.send_message"
    operation_id: str             # "send_message"
    connector_id: str             # "email"
    risk_tier: RiskTier

    # Target
    target: str                   # "bob@client.com"
    target_domain: str            # "client.com"
    target_category: str          # "verified_recipient" | "new_recipient" | ""
    scope: str                    # "channel:#general" | ""

    # Temporal
    timestamp: str                # ISO 8601
    day_of_week: int              # 0=Mon, 6=Sun
    hour_of_day: int              # 0-23

    # Payload metadata (NOT the payload itself)
    content_hash: str = ""
    content_size: int = 0

    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_action(
        cls,
        capability: str,
        target: str = "",
        risk_tier: RiskTier = RiskTier.T3_IRREVERSIBLE,
        target_category: str = "",
        scope: str = "",
        content_hash: str = "",
        content_size: int = 0,
        metadata: Optional[dict] = None,
        timestamp: Optional[datetime] = None,
    ) -> DecisionContext:
        """Create context from action parameters, auto-filling temporal fields."""
        now = timestamp or datetime.now(timezone.utc)

        # Extract operation_id and connector_id from capability
        parts = capability.split(".")
        connector_id = parts[1] if len(parts) >= 2 else ""
        operation_id = parts[-1] if len(parts) >= 2 else capability

        # Extract target_domain from target
        target_domain = ""
        if "@" in target:
            target_domain = target.split("@", 1)[1]
        elif target.startswith("channel:"):
            target_domain = ""
        elif "." in target:
            target_domain = target

        return cls(
            capability=capability,
            operation_id=operation_id,
            connector_id=connector_id,
            risk_tier=risk_tier,
            target=target,
            target_domain=target_domain,
            target_category=target_category,
            scope=scope,
            timestamp=now.isoformat(),
            day_of_week=now.weekday(),
            hour_of_day=now.hour,
            content_hash=content_hash,
            content_size=content_size,
            metadata=metadata or {},
        )


# ── Decision Record ─────────────────────────────────────────────


@dataclass(frozen=True)
class DecisionRecord:
    """A single approve/deny decision in the log."""

    id: str
    context: DecisionContext
    decision: str                 # "approved" | "denied"
    decision_time_ms: int = 0     # How long owner took
    reason: str = ""              # Optional owner reason
    rule_id: str = ""             # If auto-approved, which rule
    recorded_at: str = ""         # ISO 8601

    @property
    def is_auto(self) -> bool:
        """True if this decision was made by an automation rule."""
        return self.rule_id != ""

    @property
    def is_approval(self) -> bool:
        """True if the decision was 'approved'."""
        return self.decision == "approved"


# ── Approval Pattern ────────────────────────────────────────────


@dataclass
class ApprovalPattern:
    """A detected pattern in owner decisions."""

    id: str
    pattern_type: str             # "approval" | "denial"

    # Conditions (None = not constrained on this dimension)
    capability: Optional[str] = None
    target_domain: Optional[str] = None
    target_category: Optional[str] = None
    scope: Optional[str] = None
    time_range: Optional[Tuple[int, int]] = None    # (start_hour, end_hour)
    day_range: Optional[Tuple[int, int]] = None      # (start_day, end_day)

    # Stats
    total_observations: int = 0
    consistent_decisions: int = 0
    first_observed: str = ""
    last_observed: str = ""
    avg_decision_time_ms: float = 0.0

    @property
    def confidence(self) -> float:
        """Pattern confidence: consistency rate x observation factor."""
        if self.total_observations == 0:
            return 0.0
        consistency = self.consistent_decisions / self.total_observations
        observation_factor = min(1.0, self.total_observations / 30)
        return consistency * observation_factor

    @property
    def specificity(self) -> int:
        """How many dimensions this pattern constrains."""
        count = 0
        if self.capability is not None:
            count += 1
        if self.target_domain is not None:
            count += 1
        if self.target_category is not None:
            count += 1
        if self.scope is not None:
            count += 1
        if self.time_range is not None:
            count += 1
        if self.day_range is not None:
            count += 1
        return count

    def matches(self, context: DecisionContext) -> bool:
        """Check if a decision context matches this pattern.

        Every non-None condition must match. Supports wildcards
        in capability via fnmatch.
        """
        if self.capability is not None:
            if not fnmatch.fnmatch(context.capability, self.capability):
                return False

        if self.target_domain is not None:
            if context.target_domain != self.target_domain:
                return False

        if self.target_category is not None:
            if context.target_category != self.target_category:
                return False

        if self.scope is not None:
            if context.scope != self.scope:
                return False

        if self.time_range is not None:
            start, end = self.time_range
            if start <= end:
                # Normal range, e.g., (9, 17)
                if not (start <= context.hour_of_day < end):
                    return False
            else:
                # Wrap-around range, e.g., (22, 6)
                if not (context.hour_of_day >= start or context.hour_of_day < end):
                    return False

        if self.day_range is not None:
            start, end = self.day_range
            if start <= end:
                if not (start <= context.day_of_week <= end):
                    return False
            else:
                # Wrap-around, e.g., (5, 1) = Sat-Mon
                if not (context.day_of_week >= start or context.day_of_week <= end):
                    return False

        return True


# ── Automation Rule ─────────────────────────────────────────────


@dataclass
class AutomationRule:
    """An automation rule proposed or activated from a detected pattern."""

    id: str
    name: str                     # Human-readable
    description: str

    # Source pattern
    pattern_id: str
    pattern_type: str             # "auto_approve" | "auto_deny"

    # Conditions (serialized from pattern)
    conditions: Dict = field(default_factory=dict)

    # Status
    status: str = "proposed"      # "proposed"|"active"|"paused"|"revoked"
    created_at: str = ""
    activated_at: str = ""
    revoked_at: str = ""

    # Guardrails
    max_auto_decisions_per_day: int = 50
    max_auto_decisions_total: int = 500
    expires_at: str = ""

    # Usage tracking
    auto_decisions_today: int = 0
    auto_decisions_total: int = 0
    last_auto_decision: str = ""
    last_reset_date: str = ""     # For resetting daily counter

    # Safety
    owner_confirmed: bool = False
    soul_compatible: bool = True

    @property
    def is_active(self) -> bool:
        """True if rule can currently be applied."""
        return (
            self.status == "active"
            and self.owner_confirmed
            and self.auto_decisions_today < self.max_auto_decisions_per_day
            and self.auto_decisions_total < self.max_auto_decisions_total
        )

    def increment_usage(self) -> None:
        """Bump usage counters. Reset daily count if new day."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.last_reset_date != today:
            self.auto_decisions_today = 0
            self.last_reset_date = today

        self.auto_decisions_today += 1
        self.auto_decisions_total += 1
        self.last_auto_decision = datetime.now(timezone.utc).isoformat()

    def matches_context(self, context: DecisionContext) -> bool:
        """Check if a context matches this rule's conditions."""
        # Reconstruct an ApprovalPattern from conditions for matching
        conds = self.conditions

        if "capability" in conds:
            if not fnmatch.fnmatch(context.capability, conds["capability"]):
                return False

        if "target_domain" in conds:
            if context.target_domain != conds["target_domain"]:
                return False

        if "target_category" in conds:
            if context.target_category != conds["target_category"]:
                return False

        if "scope" in conds:
            if context.scope != conds["scope"]:
                return False

        if "time_range" in conds:
            start, end = conds["time_range"]
            if start <= end:
                if not (start <= context.hour_of_day < end):
                    return False
            else:
                if not (context.hour_of_day >= start or context.hour_of_day < end):
                    return False

        if "day_range" in conds:
            start, end = conds["day_range"]
            if start <= end:
                if not (start <= context.day_of_week <= end):
                    return False
            else:
                if not (context.day_of_week >= start or context.day_of_week <= end):
                    return False

        return True

    @property
    def specificity(self) -> int:
        """Number of conditions in this rule."""
        return len(self.conditions)


# ── Rule Check Result ───────────────────────────────────────────


@dataclass(frozen=True)
class RuleCheckResult:
    """Result of checking automation rules against a context."""

    action: str                   # "auto_approve"|"auto_deny"|"ask_owner"
    rule_id: str = ""
    rule_name: str = ""
    reason: str = ""
    confidence: float = 0.0
