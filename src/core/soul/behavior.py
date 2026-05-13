# Lancelot - A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""Runtime behavior checks for active Soul documents.

This module evaluates a requested capability against the merged Soul,
structured governance controls, and the risk classifier. It is intentionally
read-only: it explains the decision that execution layers should enforce.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from src.core.governance.config import RiskClassificationConfig, load_governance_config
from src.core.governance.models import RiskClassification, RiskTier
from src.core.governance.risk_classifier import RiskClassifier
from src.core.soul.store import Soul


Decision = str


@dataclass(frozen=True)
class SoulBehaviorDecision:
    """Decision summary for a capability under a specific Soul."""

    capability: str
    scope: str
    target: Optional[str]
    decision: Decision
    risk_tier: str
    requires_approval: bool
    blocked: bool
    requires_sync_verify: bool
    reasons: list[str] = field(default_factory=list)
    matched_controls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "scope": self.scope,
            "target": self.target,
            "decision": self.decision,
            "risk_tier": self.risk_tier,
            "requires_approval": self.requires_approval,
            "blocked": self.blocked,
            "requires_sync_verify": self.requires_sync_verify,
            "reasons": list(self.reasons),
            "matched_controls": list(self.matched_controls),
        }


def evaluate_soul_behavior(
    soul: Soul,
    capability: str,
    *,
    scope: str = "workspace",
    target: Optional[str] = None,
    risk_config: Optional[RiskClassificationConfig] = None,
) -> SoulBehaviorDecision:
    """Evaluate a capability against the supplied Soul.

    The decision order is conservative and mirrors the intended runtime
    hierarchy:
    1. enforced kill switches stop execution
    2. data-boundary prohibitions block execution
    3. explicit Soul approval lists and external transmission rules require approval
    4. risk classifier tiers apply, including structured risk overrides
    5. allowed autonomous domain capabilities are allowed
    6. anything unknown falls back to risk-classifier behavior
    """

    capability = str(capability or "").strip()
    scope = str(scope or "workspace").strip() or "workspace"
    target = str(target).strip() if target is not None and str(target).strip() else None

    config = risk_config or load_governance_config().risk_classification
    classifier = RiskClassifier(config, soul=soul)
    profile = classifier.classify(capability, scope=scope, target=target)
    classification = RiskClassification.from_tier(profile.tier, profile.soul_escalation or "")

    reasons: list[str] = []
    matched: list[str] = []

    for rule in soul.kill_switch_rules:
        if not rule.enforced:
            continue
        if _matches_any(rule.trigger, capability, target) or _matches_any(rule.name, capability, target):
            matched.append(f"kill_switch:{rule.name}")
            if rule.reason:
                reasons.append(rule.reason)
            return _decision(
                capability,
                scope,
                target,
                "blocked",
                RiskTier.T3_IRREVERSIBLE,
                True,
                True,
                True,
                reasons or [f"Kill switch '{rule.name}' requires {rule.action}."],
                matched,
            )

    for boundary in soul.data_boundaries:
        if _matches_entries(boundary.prohibited_access, capability):
            matched.append(f"data_boundary:{boundary.name}:prohibited")
            if boundary.reason:
                reasons.append(boundary.reason)
            return _decision(
                capability,
                scope,
                target,
                "blocked",
                RiskTier.T3_IRREVERSIBLE,
                True,
                True,
                True,
                reasons or [f"Capability is prohibited by data boundary '{boundary.name}'."],
                matched,
            )

    if _matches_entries(soul.autonomy_posture.requires_approval, capability):
        matched.append("autonomy_posture:requires_approval")
        reasons.append("Capability is listed in autonomy_posture.requires_approval.")
        return _decision(
            capability,
            scope,
            target,
            "requires_approval",
            max(profile.tier, RiskTier.T3_IRREVERSIBLE),
            True,
            False,
            True,
            reasons,
            matched,
        )

    for rule in soul.external_transmission_rules:
        if _matches_entries(rule.applies_to, capability):
            matched.append(f"external_transmission:{rule.name}")
            if rule.reason:
                reasons.append(rule.reason)
            return _decision(
                capability,
                scope,
                target,
                "requires_approval",
                _parse_tier(rule.requires_approval_tier) or RiskTier.T3_IRREVERSIBLE,
                True,
                False,
                True,
                reasons or [f"External transmission rule '{rule.name}' requires approval."],
                matched,
            )

    matched_risk_override_reason = _matched_risk_override_reason(soul, capability)
    if matched_risk_override_reason and "risk_override" not in matched:
        matched.append("risk_override")
        reasons.append(matched_risk_override_reason)

    if profile.soul_escalation and "risk_override" not in matched:
        matched.append("risk_override")
        reasons.append(profile.soul_escalation)

    if _classifier_has_governance_for(classifier, capability) or profile.soul_escalation or matched_risk_override_reason:
        if classification.requires_approval:
            return _decision(
                capability,
                scope,
                target,
                "requires_approval",
                profile.tier,
                True,
                False,
                classification.requires_sync_verify,
                reasons or [f"Risk tier T{int(profile.tier)} requires approval."],
                matched,
            )
        if classification.requires_sync_verify:
            return _decision(
                capability,
                scope,
                target,
                "allowed",
                profile.tier,
                False,
                False,
                True,
                reasons or [f"Risk tier T{int(profile.tier)} requires synchronous verification."],
                matched,
            )

    for boundary in soul.data_boundaries:
        if _matches_entries(boundary.allowed_access, capability):
            matched.append(f"data_boundary:{boundary.name}:allowed")

    if _matches_entries(soul.autonomy_posture.allowed_autonomous, capability):
        matched.append("autonomy_posture:allowed_autonomous")
        return _decision(
            capability,
            scope,
            target,
            "allowed",
            profile.tier if profile.soul_escalation else RiskTier.T0_INERT,
            False,
            False,
            classification.requires_sync_verify if profile.soul_escalation else False,
            reasons or ["Capability is allowed autonomously by the active Soul."],
            matched,
        )

    if classification.requires_approval:
        return _decision(
            capability,
            scope,
            target,
            "requires_approval",
            profile.tier,
            True,
            False,
            classification.requires_sync_verify,
            reasons or ["Capability is not explicitly allowed and classifies as high risk."],
            matched,
        )

    return _decision(
        capability,
        scope,
        target,
        "allowed",
        profile.tier,
        False,
        False,
        classification.requires_sync_verify,
        reasons or [f"Risk tier T{int(profile.tier)} is allowed under current governance."],
        matched,
    )


def _decision(
    capability: str,
    scope: str,
    target: Optional[str],
    decision: Decision,
    tier: RiskTier,
    requires_approval: bool,
    blocked: bool,
    requires_sync_verify: bool,
    reasons: list[str],
    matched_controls: list[str],
) -> SoulBehaviorDecision:
    return SoulBehaviorDecision(
        capability=capability,
        scope=scope,
        target=target,
        decision=decision,
        risk_tier=f"T{int(tier)}",
        requires_approval=requires_approval,
        blocked=blocked,
        requires_sync_verify=requires_sync_verify,
        reasons=reasons,
        matched_controls=matched_controls,
    )


def _matches_entries(entries: Iterable[str], capability: str) -> bool:
    return any(_matches_pattern(entry, capability) for entry in entries)


def _matches_any(pattern: str, capability: str, target: Optional[str]) -> bool:
    return _matches_pattern(pattern, capability) or bool(target and _matches_pattern(pattern, target))


def _matches_pattern(pattern: str, value: str) -> bool:
    pattern = str(pattern or "").strip()
    value = str(value or "").strip()
    return bool(pattern and value and (pattern == value or fnmatch.fnmatch(value, pattern)))


def _parse_tier(value: Any) -> Optional[RiskTier]:
    try:
        if isinstance(value, str):
            text = value.strip().upper()
            if text.startswith("T"):
                text = text[1:]
            return RiskTier(int(text))
        return RiskTier(value)
    except (TypeError, ValueError):
        return None


def _classifier_has_governance_for(classifier: RiskClassifier, capability: str) -> bool:
    return any(_matches_pattern(pattern, capability) for pattern in classifier.known_capabilities)


def _matched_risk_override_reason(soul: Soul, capability: str) -> Optional[str]:
    for rule in soul.risk_overrides:
        if _matches_pattern(rule.capability, capability):
            return rule.reason
    return None
