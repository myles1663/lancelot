# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Incident Response Models — data structures for incident records.

An incident record is the authoritative tracking object for the response
lifecycle. It is stored in data/incidents/ and is separate from the receipt
system. The receipt system records what the agent did. The incident record
documents how humans responded.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class IncidentCategory(str, Enum):
    """Categories based on the governance event that triggers them."""
    GOVERNANCE_BREACH = "GOVERNANCE_BREACH"
    SECURITY_EVENT = "SECURITY_EVENT"
    COST_ANOMALY = "COST_ANOMALY"
    AVAILABILITY_INCIDENT = "AVAILABILITY_INCIDENT"
    COMPLIANCE_EVENT = "COMPLIANCE_EVENT"


class IncidentStatus(str, Enum):
    """Lifecycle status of an incident."""
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    CONTAINED = "CONTAINED"
    REMEDIATING = "REMEDIATING"
    CLOSED = "CLOSED"
    FALSE_POSITIVE = "FALSE_POSITIVE"


class IncidentSeverity(str, Enum):
    """Severity classification. Set by trigger, can be escalated."""
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

    def __lt__(self, other: IncidentSeverity) -> bool:
        order = [IncidentSeverity.LOW, IncidentSeverity.MEDIUM,
                 IncidentSeverity.HIGH, IncidentSeverity.CRITICAL]
        return order.index(self) < order.index(other)

    def __le__(self, other: IncidentSeverity) -> bool:
        return self == other or self < other

    def __gt__(self, other: IncidentSeverity) -> bool:
        return not self <= other

    def __ge__(self, other: IncidentSeverity) -> bool:
        return not self < other


@dataclass
class TimelineEntry:
    """A single entry in an incident's response timeline."""
    timestamp: str
    entry_type: str  # e.g., "status_change", "note", "escalation", "remediation_linked"
    actor: str  # operator_id or "SYSTEM"
    detail: str
    receipt_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TimelineEntry:
        return cls(**{k: v for k, v in data.items()
                      if k in cls.__dataclass_fields__})


@dataclass
class IncidentRecord:
    """The authoritative tracking object for an incident response lifecycle."""
    incident_id: str
    trigger_receipt_id: str
    category: str  # IncidentCategory value
    severity: str  # IncidentSeverity value
    playbook_name: str
    status: str = IncidentStatus.OPEN.value
    opened_at: str = ""
    paged_at: Optional[str] = None
    responder_id: Optional[str] = None
    acknowledged_at: Optional[str] = None
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    remediation_receipts: List[str] = field(default_factory=list)
    closed_at: Optional[str] = None
    closed_by: Optional[str] = None
    root_cause: Optional[str] = None
    board_report_generated: bool = False
    # Dedup tracking
    dedup_key: Optional[str] = None

    def __post_init__(self):
        if not self.opened_at:
            self.opened_at = datetime.now(timezone.utc).isoformat()

    def add_timeline_entry(self, entry: TimelineEntry) -> None:
        self.timeline.append(entry.to_dict())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> IncidentRecord:
        valid_fields = cls.__dataclass_fields__
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)

    @classmethod
    def create(
        cls,
        trigger_receipt_id: str,
        category: IncidentCategory,
        severity: IncidentSeverity,
        playbook_name: str,
        dedup_key: Optional[str] = None,
    ) -> IncidentRecord:
        """Factory method to create a new incident record."""
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            incident_id=str(uuid.uuid4()),
            trigger_receipt_id=trigger_receipt_id,
            category=category.value,
            severity=severity.value,
            playbook_name=playbook_name,
            status=IncidentStatus.OPEN.value,
            opened_at=now,
            dedup_key=dedup_key,
        )
