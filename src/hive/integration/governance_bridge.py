"""
HIVE Governance Bridge — connects to the running governance pipeline.

Delegates to the production RiskClassifier, TrustLedger, DecisionLog,
and MCPSentry instances. All are production-active when their feature
flags are enabled.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class GovernanceResult:
    """Result of a governance validation check."""
    approved: bool
    tier: Optional[str] = None
    reason: str = ""
    requires_operator_approval: bool = False
    approval_request_id: Optional[str] = None


class GovernanceBridge:
    """Bridge to the running Lancelot governance pipeline.

    Gets instances of RiskClassifier, TrustLedger, DecisionLog, and
    MCPSentry at init time from the orchestrator.
    """

    def __init__(
        self,
        risk_classifier=None,
        trust_ledger=None,
        decision_log=None,
        mcp_sentry=None,
        enforce_kill_switches: bool = False,
    ):
        self._risk_classifier = risk_classifier
        self._trust_ledger = trust_ledger
        self._decision_log = decision_log
        self._mcp_sentry = mcp_sentry
        self._enforce_kill_switches = enforce_kill_switches

    def _sentry_context(
        self,
        capability: str,
        scope: str,
        target: Optional[str],
        agent_id: Optional[str],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = {
            "capability": capability,
            "scope": scope,
            "target": target or "",
            "agent_id": agent_id or "",
            "source": "hive",
        }
        if context:
            payload["context"] = context
        return payload

    def _check_sentry_permission(
        self,
        capability: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not self._mcp_sentry:
            return {
                "status": "NOT_CONFIGURED",
                "message": "No MCP Sentry configured",
                "request_id": None,
            }
        try:
            try:
                result = self._mcp_sentry.check_permission(capability, payload)
            except TypeError:
                result = self._mcp_sentry.check_permission(capability)
        except Exception as exc:
            logger.warning("MCP Sentry check failed: %s", exc)
            return {
                "status": "DENIED",
                "message": f"MCP Sentry check failed: {exc}",
                "request_id": None,
            }

        if isinstance(result, bool):
            return {
                "status": "APPROVED" if result else "DENIED",
                "message": "MCP Sentry approved" if result else f"MCP Sentry denied: {capability}",
                "request_id": None,
            }

        if not isinstance(result, dict):
            approved = bool(result)
            return {
                "status": "APPROVED" if approved else "DENIED",
                "message": str(result),
                "request_id": None,
            }

        status = str(result.get("status", "APPROVED")).upper()
        return {
            "status": status,
            "message": result.get("message", ""),
            "request_id": result.get("request_id"),
        }

    def validate_action(
        self,
        capability: str,
        scope: str = "workspace",
        target: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> GovernanceResult:
        """Validate an agent action through the governance pipeline.

        Steps:
        1. RiskClassifier.classify() to get tier
        2. Check if tier requires approval
        3. TrustLedger for tier adjustment
        4. MCPSentry permission check

        Returns GovernanceResult with approval decision.
        """
        tier_str = "T0"
        requires_approval = False
        sentry_payload = self._sentry_context(capability, scope, target, agent_id)

        if self._enforce_kill_switches and not self.check_kill_switches():
            return GovernanceResult(
                approved=False,
                tier="T3",
                reason="HIVE kill switch is active",
                requires_operator_approval=False,
            )

        # Step 1: Risk classification
        if self._risk_classifier:
            try:
                profile = self._risk_classifier.classify(
                    capability=capability,
                    scope=scope,
                    target=target,
                )
                tier_str = f"T{profile.tier.value}"
                # T3 always requires approval for HIVE agents
                requires_approval = profile.tier.value >= 3
                # T2 requires supervision
                if profile.tier.value >= 2:
                    requires_approval = True
            except Exception as exc:
                logger.warning(
                    "Risk classification failed for %s: %s — defaulting to T3",
                    capability, exc,
                )
                tier_str = "T3"
                requires_approval = True
        else:
            # No classifier available — conservative default
            tier_str = "T2"
            requires_approval = True

        # Step 2: Trust ledger adjustment
        if self._trust_ledger and not requires_approval:
            try:
                effective = self._trust_ledger.get_effective_tier(capability, scope)
                if effective is not None and effective.value < int(tier_str[1]):
                    tier_str = f"T{effective.value}"
                    requires_approval = effective.value >= 2
            except Exception as exc:
                logger.warning("Trust ledger check failed: %s", exc)

        # Step 3: MCP Sentry permission check / approval queue
        sentry_result = self._check_sentry_permission(capability, sentry_payload)
        if sentry_result["status"] == "DENIED":
            return GovernanceResult(
                approved=False,
                tier=tier_str,
                reason=sentry_result["message"] or f"MCP Sentry denied: {capability}",
                requires_operator_approval=False,
                approval_request_id=sentry_result["request_id"],
            )

        if requires_approval:
            if sentry_result["status"] == "APPROVED":
                return GovernanceResult(
                    approved=True,
                    tier=tier_str,
                    reason=sentry_result["message"] or "Previously approved by governance",
                    approval_request_id=sentry_result["request_id"],
                )
            return GovernanceResult(
                approved=False,
                tier=tier_str,
                reason=sentry_result["message"] or f"Tier {tier_str} requires operator approval",
                requires_operator_approval=True,
                approval_request_id=sentry_result["request_id"],
            )

        if sentry_result["status"] == "PENDING":
            return GovernanceResult(
                approved=False,
                tier=tier_str,
                reason=sentry_result["message"] or f"MCP Sentry queued approval for {capability}",
                requires_operator_approval=True,
                approval_request_id=sentry_result["request_id"],
            )

        return GovernanceResult(
            approved=True,
            tier=tier_str,
            reason=sentry_result["message"] or "Governance check passed",
        )

    def check_kill_switches(self) -> bool:
        """Check if HIVE kill switches are active.

        Returns True if HIVE should continue operating.
        """
        try:
            import feature_flags as ff
            return getattr(ff, "FEATURE_HIVE", False)
        except ImportError:
            try:
                from src.core.feature_flags import FEATURE_HIVE
                return FEATURE_HIVE
            except ImportError:
                return False

    def update_trust(
        self,
        capability: str,
        scope: str,
        success: bool,
    ) -> None:
        """Record success/failure in the trust ledger."""
        if not self._trust_ledger:
            return
        try:
            if success:
                self._trust_ledger.record_success(capability, scope)
            else:
                self._trust_ledger.record_failure(capability, scope)
        except Exception as exc:
            logger.warning("Trust ledger update failed: %s", exc)

    def request_approval(
        self,
        capability: str,
        agent_id: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Request operator approval for a high-tier action.

        Returns True only when an already-approved matching sentry request exists.
        Otherwise this queues or preserves a pending approval request.
        """
        payload = self._sentry_context(
            capability=capability,
            scope="workspace",
            target=None,
            agent_id=agent_id,
            context=context,
        )
        sentry_result = self._check_sentry_permission(capability, payload)
        logger.info(
            "HIVE approval requested: capability=%s, agent=%s, status=%s, request_id=%s",
            capability,
            agent_id,
            sentry_result["status"],
            sentry_result["request_id"],
        )
        return sentry_result["status"] == "APPROVED"
