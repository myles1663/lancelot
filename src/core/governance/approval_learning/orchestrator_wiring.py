"""
APL Orchestrator Wiring — builds DecisionContext from plan steps
and records decisions via ApprovalRecorder.

The actual orchestrator modification (P73) calls these helpers.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from src.core.governance.approval_learning.decision_log import DecisionLog
from src.core.governance.approval_learning.models import (
    DecisionContext,
    RuleCheckResult,
    RiskTier,
)

logger = logging.getLogger(__name__)


def build_decision_context(
    step: Any,
    classification: Any,
    target: str = "",
    target_category: str = "",
    scope: str = "",
    content_hash: str = "",
    content_size: int = 0,
    metadata: Optional[dict] = None,
) -> DecisionContext:
    """Build DecisionContext from a plan step and its risk classification.

    Maps step fields to context fields:
    - step.tool / capability → capability, operation_id, connector_id
    - target → target, target_domain
    - classification.tier → risk_tier
    - Current time → timestamp, day_of_week, hour_of_day
    """
    # Extract capability from step
    capability = ""
    if hasattr(step, "capability"):
        capability = step.capability
    elif hasattr(step, "tool"):
        capability = step.tool
    else:
        capability = str(step)

    # Extract target from step params if not provided
    if not target and hasattr(step, "params"):
        params = step.params if isinstance(step.params, dict) else {}
        if isinstance(step.params, list):
            params = {p.key: p.value for p in step.params if hasattr(p, "key")}
        target = params.get("to", params.get("target", params.get("recipient", "")))

    # Extract scope from step
    if not scope and hasattr(step, "params"):
        params = step.params if isinstance(step.params, dict) else {}
        if isinstance(step.params, list):
            params = {p.key: p.value for p in step.params if hasattr(p, "key")}
        channel = params.get("channel", "")
        if channel:
            scope = f"channel:{channel}"

    # Get risk tier from classification
    risk_tier = RiskTier.T3_IRREVERSIBLE
    if hasattr(classification, "tier"):
        risk_tier = classification.tier
    elif isinstance(classification, int):
        risk_tier = RiskTier(classification)

    return DecisionContext.from_action(
        capability=capability,
        target=target,
        risk_tier=risk_tier,
        target_category=target_category,
        scope=scope,
        content_hash=content_hash,
        content_size=content_size,
        metadata=metadata,
    )


class ApprovalRecorder:
    """Records approval decisions to the DecisionLog."""

    def __init__(self, decision_log: DecisionLog):
        self._log = decision_log

    def record_manual_decision(
        self,
        context: DecisionContext,
        approved: bool,
        decision_time_ms: int = 0,
        reason: str = "",
    ) -> None:
        """Record a manual owner decision."""
        self._log.record(
            context,
            "approved" if approved else "denied",
            decision_time_ms=decision_time_ms,
            reason=reason,
        )

    def record_auto_decision(
        self,
        context: DecisionContext,
        rule_check: RuleCheckResult,
    ) -> None:
        """Record an automated decision from a rule."""
        decision = "approved" if rule_check.action == "auto_approve" else "denied"
        self._log.record(
            context,
            decision,
            decision_time_ms=0,
            rule_id=rule_check.rule_id,
        )
