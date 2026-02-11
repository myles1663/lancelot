"""
Lancelot vNext4: Risk Classifier

Classifies actions into risk tiers (T0-T3) based on capability,
scope, target patterns, and Soul escalation overrides.
"""

from __future__ import annotations

import fnmatch
import logging
from typing import Optional

from .config import RiskClassificationConfig
from .models import ActionRiskProfile, RiskTier

logger = logging.getLogger(__name__)


class RiskClassifier:
    """Classifies actions into risk tiers based on capability, scope, and config.

    Classification algorithm:
    1. Look up capability in defaults → base tier
    2. Check scope escalation rules
    3. Check pattern escalation rules (fnmatch)
    4. Check Soul escalation overrides
    5. Check Trust Ledger for graduated tiers (can only LOWER, never raise)
    6. Unknown capabilities default to T3 (unknown = dangerous)
    """

    def __init__(self, config: RiskClassificationConfig, soul=None, trust_ledger=None):
        """
        Args:
            config: Risk classification config from governance.yaml
            soul: Optional Soul instance for escalation overrides
            trust_ledger: Optional TrustLedger for progressive tier relaxation
        """
        self._config = config
        self._defaults: dict[str, RiskTier] = {}
        self._soul = soul
        self._soul_escalations: list[dict] = []
        self._trust_ledger = trust_ledger

        # Build default tier lookup
        for capability, tier_int in config.defaults.items():
            try:
                self._defaults[capability] = RiskTier(tier_int)
            except ValueError:
                logger.warning("Invalid tier %d for capability %s, defaulting to T3", tier_int, capability)
                self._defaults[capability] = RiskTier.T3_IRREVERSIBLE

        # Parse Soul escalation rules
        if soul:
            self._parse_soul_escalations(soul)

    def classify(
        self,
        capability: str,
        scope: str = "workspace",
        target: Optional[str] = None,
    ) -> ActionRiskProfile:
        """Classify an action into a risk tier.

        Args:
            capability: The capability identifier (e.g., "fs.read", "shell.exec")
            scope: The scope context (e.g., "workspace", "outside_workspace")
            target: Optional target path/resource for pattern matching

        Returns:
            ActionRiskProfile with the determined risk tier
        """
        # Step 1: Default tier lookup
        tier = self._defaults.get(capability, RiskTier.T3_IRREVERSIBLE)
        soul_escalation = None

        # Step 2: Scope escalation
        for rule in self._config.scope_escalations:
            if rule.capability != capability:
                continue

            # Scope-based escalation
            if rule.scope and rule.scope == scope:
                escalate_to = RiskTier(rule.escalate_to)
                if escalate_to > tier:
                    tier = escalate_to

            # Pattern-based escalation
            if rule.pattern and target:
                if fnmatch.fnmatch(target, rule.pattern):
                    escalate_to = RiskTier(rule.escalate_to)
                    if escalate_to > tier:
                        tier = escalate_to

        # Step 3: Soul escalation overrides (Soul floor — can only raise)
        soul_result = self._check_soul_escalation(capability, scope, target)
        if soul_result is not None:
            escalated_tier, reason = soul_result
            if escalated_tier > tier:
                tier = escalated_tier
                soul_escalation = reason

        # Step 4: Trust Ledger adjustment (can only LOWER, never raise)
        if self._trust_ledger is not None:
            try:
                from src.core import feature_flags
                if feature_flags.FEATURE_TRUST_LEDGER:
                    effective = self._trust_ledger.get_effective_tier(capability, scope)
                    if effective is not None and effective < tier:
                        tier = effective
            except Exception as e:
                logger.warning("Trust ledger check failed: %s", e)

        return ActionRiskProfile(
            tier=tier,
            capability=capability,
            scope=scope,
            reversible=tier <= RiskTier.T1_REVERSIBLE,
            soul_escalation=soul_escalation,
        )

    def classify_step(self, step: dict) -> ActionRiskProfile:
        """Classify a plan step.

        Extracts capability, scope, and target from the step dict
        and delegates to classify().

        Args:
            step: Dict with keys "capability", "scope" (optional), "target" (optional)
        """
        return self.classify(
            capability=step.get("capability", ""),
            scope=step.get("scope", "workspace"),
            target=step.get("target"),
        )

    @property
    def known_capabilities(self) -> list[str]:
        """Return sorted list of all capabilities with configured default tiers."""
        return sorted(self._defaults.keys())

    def update_soul(self, soul) -> None:
        """Re-parse soul escalation rules after a Soul amendment."""
        self._soul = soul
        self._soul_escalations = []
        if soul:
            self._parse_soul_escalations(soul)

    def _parse_soul_escalations(self, soul) -> None:
        """Extract governance escalation rules from the Soul."""
        try:
            governance = None
            if isinstance(soul, dict):
                governance = soul.get("governance", {})
            elif hasattr(soul, "governance"):
                governance = soul.governance if soul.governance else {}

            if not governance:
                return

            escalations = governance.get("escalations", [])
            if not isinstance(escalations, list):
                return

            self._soul_escalations = escalations
        except Exception as e:
            logger.warning("Failed to parse Soul escalations: %s", e)

    def _check_soul_escalation(
        self,
        capability: str,
        scope: str,
        target: Optional[str],
    ) -> Optional[tuple[RiskTier, str]]:
        """Check Soul escalation rules.

        Returns:
            Tuple of (escalated_tier, reason) if a rule matches, else None.
        """
        for rule in self._soul_escalations:
            if rule.get("capability") != capability:
                continue

            # Scope-based Soul escalation
            rule_scope = rule.get("scope", "")
            if rule_scope and rule_scope == scope:
                try:
                    return RiskTier(rule["escalate_to"]), rule.get("reason", "Soul escalation")
                except (ValueError, KeyError):
                    continue

            # Pattern-based Soul escalation
            rule_pattern = rule.get("pattern", "")
            if rule_pattern and target:
                if fnmatch.fnmatch(target, rule_pattern):
                    try:
                        return RiskTier(rule["escalate_to"]), rule.get("reason", "Soul escalation")
                    except (ValueError, KeyError):
                        continue

        return None
