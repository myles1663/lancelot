# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Operator Identity — Named Human Attribution for the Governance Receipt Chain.

Every human-initiated governance action must carry the identity of the
human who initiated it. Automated actions carry the SYSTEM identity.
A receipt with null operator_id is automated. A receipt with a populated
operator_id is a human decision. Auditors can tell the difference at a glance.

This module defines:
    - OperatorIdentity: the identity structure attached to governance receipts
    - SYSTEM_IDENTITY: reserved identity for automated actions
    - IDENTITY_REQUIRED_TYPES: receipt types that must carry human identity
    - IdentityRequiredError: raised when a required-identity receipt is
      written without a valid OperatorIdentity
    - resolve_operator_id: deterministic UUID derivation from username
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional, Set, Dict, Any

from src.shared.receipts import ActionType


# ── Operator Identity Model ───────────────────────────────────────

# Namespace UUID for deterministic operator_id derivation.
# Generated once, never changes. UUID5(namespace, username) → stable operator_id.
_OPERATOR_NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


def resolve_operator_id(username: str) -> str:
    """Derive a stable, deterministic operator_id from a username.

    Uses UUID5 (SHA-1 namespace hashing) so the same username always
    produces the same operator_id across sessions and restarts.
    """
    return str(uuid.uuid5(_OPERATOR_NAMESPACE, username))


@dataclass(frozen=True)
class OperatorIdentity:
    """Named human identity attached to governance receipts.

    Fields:
        operator_id:  Stable UUID, same across sessions for the same person.
        display_name: Human-readable name (e.g., "Myles Hamilton").
        session_id:   Ephemeral UUID, unique per War Room session.
        session_started_at: ISO 8601 timestamp of session creation.
        auth_method:  How the session was authenticated.
                      Phase 1: "local" or "api_key".
                      Phase 2: "sso".
        ip_address:   Client IP. Redacted in compliance exports. Never shown in UI.
    """
    operator_id: str
    display_name: str
    session_id: str = ""
    session_started_at: str = ""
    auth_method: str = "local"
    ip_address: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for receipt metadata injection."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> OperatorIdentity:
        """Deserialize from receipt metadata."""
        return cls(
            operator_id=data.get("operator_id", ""),
            display_name=data.get("display_name", ""),
            session_id=data.get("session_id", ""),
            session_started_at=data.get("session_started_at", ""),
            auth_method=data.get("auth_method", "local"),
            ip_address=data.get("ip_address", ""),
        )

    @property
    def is_system(self) -> bool:
        """True if this is the reserved SYSTEM identity."""
        return self.operator_id == "SYSTEM"

    @property
    def is_valid(self) -> bool:
        """True if operator_id and display_name are present."""
        return bool(self.operator_id) and bool(self.display_name)


# ── Reserved SYSTEM Identity ──────────────────────────────────────

SYSTEM_IDENTITY = OperatorIdentity(
    operator_id="SYSTEM",
    display_name="Lancelot Automation",
    session_id="",
    session_started_at="",
    auth_method="system",
    ip_address="",
)


# ── Identity-Required Receipt Types ──────────────────────────────
# Every receipt type in this set MUST carry a valid OperatorIdentity.
# The receipt writer enforces this — callers cannot bypass it.
# A receipt type NOT in this set may optionally carry identity but
# will not be blocked if identity is absent.

IDENTITY_REQUIRED_TYPES: Set[str] = {
    # Kill switches
    ActionType.KILL_SWITCH_ISSUED.value,
    ActionType.KILL_SWITCH_LIFTED.value,
    # T3 approvals
    ActionType.T3_APPROVED.value,
    ActionType.T3_REJECTED.value,
    # Soul governance
    ActionType.SOUL_UPDATED.value,
    ActionType.SOUL_VERSION_PINNED.value,
    # Agent lifecycle
    ActionType.AGENT_DEPLOYED.value,
    ActionType.AGENT_STOPPED.value,
    # Credentials
    ActionType.CREDENTIAL_REGISTERED.value,
    ActionType.CREDENTIAL_REVOKED.value,
    # MCP server management
    ActionType.MCP_SERVER_REGISTERED.value,
    ActionType.MCP_SERVER_REVOKED.value,
    ActionType.MCP_T3_APPROVED.value,
    ActionType.MCP_T3_REJECTED.value,
    # Connectors
    ActionType.CONNECTOR_ENABLED.value,
    ActionType.CONNECTOR_DISABLED.value,
    # Network allowlist
    ActionType.ALLOWLIST_MODIFIED.value,
    # Scheduler CRUD
    ActionType.SCHEDULER_TASK_CREATED.value,
    ActionType.SCHEDULER_TASK_DELETED.value,
    # Tool store
    ActionType.TOOL_ENABLED.value,
    ActionType.TOOL_DISABLED.value,
    # APL rule decisions
    ActionType.APL_RULE_APPROVED.value,
    ActionType.APL_RULE_REJECTED.value,
    # HIVE interventions (operator decisions)
    ActionType.HIVE_INTERVENTION_EVENT.value,
    # Compliance export generation
    ActionType.COMPLIANCE_EXPORT_GENERATED.value,
    # Observability — specific receipt detail queries
    ActionType.METRICS_API_QUERY.value,
    # A2A Protocol — operator-initiated actions
    ActionType.T3_A2A_INBOUND_APPROVED.value,
    ActionType.T3_A2A_INBOUND_REJECTED.value,
    ActionType.T3_A2A_OUTBOUND_APPROVED.value,
    ActionType.T3_A2A_OUTBOUND_REJECTED.value,
    ActionType.A2A_AGENT_CARD_UPDATED.value,
    # Time-Travel Debugging — human-initiated actions
    ActionType.QUEST_FORKED.value,
    ActionType.QUEST_REPLAYED.value,
    ActionType.TIME_TRAVEL_INSPECT.value,
    ActionType.T3_FORK_APPROVED.value,
    ActionType.T3_FORK_REJECTED.value,
    # Soul Template Library
    ActionType.SOUL_TEMPLATE_APPLIED.value,
    # Incident Response — human-initiated actions (OPENED and PAGED are system-generated)
    ActionType.INCIDENT_ACKNOWLEDGED.value,
    ActionType.INCIDENT_STATUS_UPDATED.value,
    ActionType.INCIDENT_TIMELINE_ENTRY.value,
    ActionType.INCIDENT_REMEDIATION_LINKED.value,
    ActionType.INCIDENT_ESCALATED.value,
    ActionType.INCIDENT_CLOSED.value,
    ActionType.INCIDENT_FALSE_POSITIVE.value,
    ActionType.PLAYBOOK_UPDATED.value,
}


# ── Exceptions ────────────────────────────────────────────────────

class IdentityRequiredError(Exception):
    """Raised when a receipt type requires OperatorIdentity but none was supplied."""

    def __init__(self, receipt_type: str):
        self.receipt_type = receipt_type
        super().__init__(
            f"Receipt type '{receipt_type}' requires OperatorIdentity but none was supplied. "
            f"Governance action blocked — ungoverned attribution is not allowed."
        )


class InvalidIdentityError(Exception):
    """Raised when a supplied OperatorIdentity is malformed."""

    def __init__(self, receipt_type: str, identity: OperatorIdentity):
        self.receipt_type = receipt_type
        self.identity = identity
        super().__init__(
            f"Receipt type '{receipt_type}' received invalid OperatorIdentity "
            f"(operator_id='{identity.operator_id}', display_name='{identity.display_name}'). "
            f"Both operator_id and display_name are required."
        )


# ── Identity Injection Helper ────────────────────────────────────

def inject_identity_into_metadata(
    metadata: Dict[str, Any],
    identity: Optional[OperatorIdentity],
) -> Dict[str, Any]:
    """Inject operator identity fields into receipt metadata dict.

    If identity is None, operator fields are set to None (for automated actions).
    If identity is SYSTEM, operator_id is "SYSTEM".
    """
    updated = dict(metadata)
    if identity is not None:
        updated["operator_id"] = identity.operator_id
        updated["operator_display_name"] = identity.display_name
        updated["session_id"] = identity.session_id
        updated["auth_method"] = identity.auth_method
    else:
        updated["operator_id"] = None
        updated["operator_display_name"] = None
        updated["session_id"] = None
        updated["auth_method"] = None
    return updated
