"""
Business Soul Configuration — governance rules for content repurposing.

Defines risk overrides, trust graduation ceilings, and connector policies
specific to the content repurposing business use case.
"""

from __future__ import annotations

import copy
from typing import Dict, List, Optional, Tuple


BUSINESS_SOUL_CONFIG = {
    "identity": "Lancelot Content Repurposing Agent",
    "governance": {
        "risk_overrides": [
            {
                "capability": "connector.stripe.*",
                "scope": "write|delete",
                "minimum_tier": 3,
                "reason": "Financial ops always require approval",
            },
            {
                "capability": "connector.email.send_message",
                "scope": "to=non_verified",
                "minimum_tier": 3,
                "reason": "New recipients require approval",
            },
        ],
        "trust_graduation": {
            "enabled": True,
            "per_capability_ceilings": {
                "connector.email.send_message:verified": 1,  # Can go to T1, not T0
                "connector.stripe.*": 3,  # Stripe always T3
            },
        },
        "connector_policies": {
            "email": {"verified_recipients": []},
            "slack": {"allowed_channels": [], "denied_channels": []},
        },
    },
}


def create_business_soul(
    verified_recipients: Optional[List[str]] = None,
    slack_channels: Optional[List[str]] = None,
) -> dict:
    """Create a configured business Soul with specific recipients/channels."""
    soul = copy.deepcopy(BUSINESS_SOUL_CONFIG)

    if verified_recipients:
        soul["governance"]["connector_policies"]["email"]["verified_recipients"] = (
            verified_recipients
        )
    if slack_channels:
        soul["governance"]["connector_policies"]["slack"]["allowed_channels"] = (
            slack_channels
        )

    return soul


def validate_business_soul(soul_config: dict) -> Tuple[bool, List[str]]:
    """Validate business soul has required sections."""
    issues = []

    if "identity" not in soul_config:
        issues.append("Missing 'identity' section")
    if "governance" not in soul_config:
        issues.append("Missing 'governance' section")
    else:
        gov = soul_config["governance"]
        if "risk_overrides" not in gov:
            issues.append("Missing 'risk_overrides' in governance")
        if "trust_graduation" not in gov:
            issues.append("Missing 'trust_graduation' in governance")
        if "connector_policies" not in gov:
            issues.append("Missing 'connector_policies' in governance")

    return (len(issues) == 0, issues)
