# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Webhook Event Categories — maps receipt types to webhook categories.

From spec Section 3.1. Operators subscribe endpoints to categories,
not individual event types. GOVERNANCE_CRITICAL events bypass sampling
and are always delivered.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, List, Set

# Category definitions
GOVERNANCE_CRITICAL = "GOVERNANCE_CRITICAL"
GOVERNANCE_APPROVAL = "GOVERNANCE_APPROVAL"
SECURITY = "SECURITY"
COST_THRESHOLD = "COST_THRESHOLD"
SOUL_CHANGES = "SOUL_CHANGES"
TASK_LIFECYCLE = "TASK_LIFECYCLE"
INCIDENT_RESPONSE = "INCIDENT_RESPONSE"
ALL = "ALL"

ALL_CATEGORIES = [
    GOVERNANCE_CRITICAL,
    GOVERNANCE_APPROVAL,
    SECURITY,
    COST_THRESHOLD,
    SOUL_CHANGES,
    TASK_LIFECYCLE,
    INCIDENT_RESPONSE,
    ALL,
]

# Map receipt action_types to categories
_TYPE_TO_CATEGORIES: Dict[str, List[str]] = {
    # GOVERNANCE_CRITICAL — requires immediate human attention
    "kill_switch_issued": [GOVERNANCE_CRITICAL],
    "kill_switch_lifted": [GOVERNANCE_CRITICAL],
    "t3_approved": [GOVERNANCE_CRITICAL, GOVERNANCE_APPROVAL],
    "t3_rejected": [GOVERNANCE_CRITICAL, GOVERNANCE_APPROVAL],
    "soul_updated": [GOVERNANCE_CRITICAL, SOUL_CHANGES],
    "soul_version_pinned": [GOVERNANCE_CRITICAL, SOUL_CHANGES],
    "crusader_mode_activated": [GOVERNANCE_CRITICAL],
    "crusader_mode_deactivated": [GOVERNANCE_CRITICAL],
    "agent_stopped": [GOVERNANCE_CRITICAL, TASK_LIFECYCLE],

    # GOVERNANCE_APPROVAL
    "t3_approval_request": [GOVERNANCE_APPROVAL],
    "apl_rule_approved": [GOVERNANCE_APPROVAL],
    "apl_rule_rejected": [GOVERNANCE_APPROVAL],

    # SECURITY
    "mcp_tool_blocked": [SECURITY],
    "credential_revoked": [SECURITY],
    "allowlist_modified": [SECURITY],
    "governance_write_error": [SECURITY],
    "credential_registered": [SECURITY],

    # SOUL_CHANGES (also in GOVERNANCE_CRITICAL for soul_updated)

    # TASK_LIFECYCLE
    "agent_deployed": [TASK_LIFECYCLE],
    "hive_intervention_event": [TASK_LIFECYCLE],

    # COST_THRESHOLD — generated dynamically based on cost receipts

    # INCIDENT_RESPONSE — all incident lifecycle events
    "incident_opened": [INCIDENT_RESPONSE, GOVERNANCE_CRITICAL],
    "incident_paged": [INCIDENT_RESPONSE],
    "incident_acknowledged": [INCIDENT_RESPONSE],
    "incident_status_updated": [INCIDENT_RESPONSE],
    "incident_timeline_entry": [INCIDENT_RESPONSE],
    "incident_remediation_linked": [INCIDENT_RESPONSE],
    "incident_escalated": [INCIDENT_RESPONSE, GOVERNANCE_CRITICAL],
    "incident_closed": [INCIDENT_RESPONSE],
    "incident_false_positive": [INCIDENT_RESPONSE],
    "playbook_updated": [INCIDENT_RESPONSE],
}


def get_categories_for_type(action_type: str) -> List[str]:
    """Return the webhook categories for a receipt action_type.

    ALL category always includes every event type.
    """
    categories = _TYPE_TO_CATEGORIES.get(action_type.lower(), [])
    return categories


def should_deliver(action_type: str, subscribed_categories: Set[str]) -> bool:
    """Check if a receipt type should be delivered to an endpoint.

    Args:
        action_type: The receipt action_type
        subscribed_categories: The endpoint's subscribed categories

    Returns:
        True if at least one category matches.
    """
    if ALL in subscribed_categories:
        return True
    event_categories = get_categories_for_type(action_type)
    return bool(set(event_categories) & subscribed_categories)


def is_governance_critical(action_type: str) -> bool:
    """Check if a receipt type is GOVERNANCE_CRITICAL.

    GOVERNANCE_CRITICAL events are always retried to exhaustion and
    never sampled out.
    """
    return GOVERNANCE_CRITICAL in get_categories_for_type(action_type)
