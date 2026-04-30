# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Federation Audit Engine — cross-instance audit trail and quest reconstruction.

Provides:
- Federation quest reconstruction (complete timeline across all instances)
- Cross-instance receipt querying by federation_quest_id, instance_id,
  receipt_type, soul_version_hash, risk_tier, time_range
- Forensic Timeline generation for compliance review
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AuditEventType(str, Enum):
    """Types of audit events in the federation."""
    HANDOFF_INITIATED = "handoff_initiated"
    HANDOFF_RECEIVED = "handoff_received"
    HANDOFF_COMPLETED = "handoff_completed"
    HANDOFF_REJECTED = "handoff_rejected"
    SOUL_PUSH = "soul_push"
    SOUL_ACTIVATED = "soul_activated"
    KILL_ISSUED = "kill_issued"
    KILL_ACKNOWLEDGED = "kill_acknowledged"
    DIVERGENCE_DETECTED = "divergence_detected"
    RECONCILIATION_COMPLETED = "reconciliation_completed"
    CONTRADICTION_DETECTED = "contradiction_detected"
    COST_THRESHOLD_CROSSED = "cost_threshold_crossed"
    PEER_REGISTERED = "peer_registered"
    PEER_REMOVED = "peer_removed"


@dataclass
class AuditEntry:
    """A single entry in the federation audit trail."""
    entry_id: str
    event_type: AuditEventType
    instance_id: str
    federation_quest_id: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    soul_version_hash: str = ""
    risk_tier: str = ""       # T0-T3
    details: Dict[str, Any] = field(default_factory=dict)
    related_entry_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "event_type": self.event_type.value,
            "instance_id": self.instance_id,
            "federation_quest_id": self.federation_quest_id,
            "timestamp": self.timestamp,
            "soul_version_hash": self.soul_version_hash,
            "risk_tier": self.risk_tier,
            "details": self.details,
            "related_entry_ids": self.related_entry_ids,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AuditEntry":
        event_type = data.get("event_type", AuditEventType.HANDOFF_INITIATED.value)
        try:
            evt = AuditEventType(event_type)
        except ValueError:
            evt = AuditEventType.HANDOFF_INITIATED
        return cls(
            entry_id=data.get("entry_id", str(uuid.uuid4())),
            event_type=evt,
            instance_id=data.get("instance_id", ""),
            federation_quest_id=data.get("federation_quest_id", ""),
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
            soul_version_hash=data.get("soul_version_hash", ""),
            risk_tier=data.get("risk_tier", ""),
            details=data.get("details", {}) or {},
            related_entry_ids=list(data.get("related_entry_ids", []) or []),
        )


@dataclass
class ForensicTimeline:
    """A reconstructed timeline for a federation quest or time range."""
    quest_id: str = ""
    instance_ids: List[str] = field(default_factory=list)
    entries: List[AuditEntry] = field(default_factory=list)
    start_time: str = ""
    end_time: str = ""
    total_entries: int = 0
    contradictions_found: int = 0
    instances_involved: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "quest_id": self.quest_id,
            "instance_ids": self.instance_ids,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "total_entries": self.total_entries,
            "contradictions_found": self.contradictions_found,
            "instances_involved": self.instances_involved,
            "entries": [e.to_dict() for e in self.entries],
        }


class FederationAuditEngine:
    """Cross-instance audit trail for federation governance.

    Stores audit entries and provides query/reconstruction capabilities
    for compliance review and forensic analysis.
    """

    def __init__(self, max_entries: int = 10000, persistence_path: Optional[str] = None):
        self._entries: Dict[str, AuditEntry] = {}
        self._max_entries = max_entries
        self._persistence_path = persistence_path
        self._lock = threading.Lock()
        self._load_from_disk()

    def record(
        self,
        entry: Optional[AuditEntry] = None,
        *,
        event_type: str = "",
        instance_id: str = "",
        federation_quest_id: str = "",
        details: Optional[Dict[str, Any]] = None,
        soul_version_hash: str = "",
        risk_tier: str = "",
    ) -> AuditEntry:
        """Record an audit entry.

        Can be called with a pre-built AuditEntry, or with keyword args
        to construct one automatically:
            audit.record(entry=my_entry)
            audit.record(event_type="handoff_initiated", instance_id="...")
        """
        if entry is None:
            # Build entry from kwargs
            try:
                evt = AuditEventType(event_type)
            except ValueError:
                evt = AuditEventType.HANDOFF_INITIATED  # fallback
                if details is None:
                    details = {}
                details["raw_event_type"] = event_type

            entry = AuditEntry(
                entry_id=str(uuid.uuid4()),
                event_type=evt,
                instance_id=instance_id,
                federation_quest_id=federation_quest_id,
                soul_version_hash=soul_version_hash,
                risk_tier=risk_tier,
                details=details or {},
            )

        with self._lock:
            self._entries[entry.entry_id] = entry

            # Evict oldest if over limit
            if len(self._entries) > self._max_entries:
                oldest_id = min(
                    self._entries,
                    key=lambda k: self._entries[k].timestamp,
                )
                del self._entries[oldest_id]
            self._persist_to_disk_locked()

        return entry

    def get_entry(self, entry_id: str) -> Optional[AuditEntry]:
        with self._lock:
            return self._entries.get(entry_id)

    def query(
        self,
        federation_quest_id: Optional[str] = None,
        instance_id: Optional[str] = None,
        event_type: Optional[AuditEventType] = None,
        soul_version_hash: Optional[str] = None,
        risk_tier: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        limit: int = 100,
    ) -> List[AuditEntry]:
        """Query audit entries with optional filters.

        All filters are AND-combined. Results sorted by timestamp ascending.
        """
        with self._lock:
            results = list(self._entries.values())

        # Apply filters
        if federation_quest_id:
            results = [e for e in results if e.federation_quest_id == federation_quest_id]
        if instance_id:
            results = [e for e in results if e.instance_id == instance_id]
        if event_type:
            results = [e for e in results if e.event_type == event_type]
        if soul_version_hash:
            results = [e for e in results if e.soul_version_hash == soul_version_hash]
        if risk_tier:
            results = [e for e in results if e.risk_tier == risk_tier]
        if start_time:
            results = [e for e in results if e.timestamp >= start_time]
        if end_time:
            results = [e for e in results if e.timestamp <= end_time]

        # Sort by timestamp
        results.sort(key=lambda e: e.timestamp)

        return results[:limit]

    def reconstruct_quest(self, federation_quest_id: str) -> ForensicTimeline:
        """Reconstruct the complete timeline for a federation quest.

        Gathers all entries related to the quest, orders them chronologically,
        and computes summary statistics.
        """
        entries = self.query(federation_quest_id=federation_quest_id, limit=10000)

        if not entries:
            return ForensicTimeline(quest_id=federation_quest_id)

        instance_ids = sorted(set(e.instance_id for e in entries))
        contradictions = sum(
            1 for e in entries
            if e.event_type == AuditEventType.CONTRADICTION_DETECTED
        )

        return ForensicTimeline(
            quest_id=federation_quest_id,
            instance_ids=instance_ids,
            entries=entries,
            start_time=entries[0].timestamp,
            end_time=entries[-1].timestamp,
            total_entries=len(entries),
            contradictions_found=contradictions,
            instances_involved=len(instance_ids),
        )

    def get_instance_timeline(
        self,
        instance_id: str,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        limit: int = 100,
    ) -> List[AuditEntry]:
        """Get chronological audit trail for a specific instance."""
        return self.query(
            instance_id=instance_id,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )

    def get_summary(self) -> Dict[str, Any]:
        """Get audit engine summary statistics."""
        with self._lock:
            entries = list(self._entries.values())

        if not entries:
            return {
                "total_entries": 0,
                "unique_quests": 0,
                "unique_instances": 0,
                "event_type_counts": {},
            }

        quests = set(e.federation_quest_id for e in entries if e.federation_quest_id)
        instances = set(e.instance_id for e in entries)
        type_counts: Dict[str, int] = {}
        for e in entries:
            key = e.event_type.value
            type_counts[key] = type_counts.get(key, 0) + 1

        return {
            "total_entries": len(entries),
            "unique_quests": len(quests),
            "unique_instances": len(instances),
            "event_type_counts": type_counts,
        }

    def _persist_to_disk_locked(self) -> None:
        """Persist audit entries to disk. Caller must hold _lock."""
        if not self._persistence_path:
            return
        try:
            path = Path(self._persistence_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "entries": [entry.to_dict() for entry in self._entries.values()],
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as exc:
            logger.warning("Failed to persist federation audit log: %s", exc)

    def _load_from_disk(self) -> None:
        """Load persisted audit entries from disk if present."""
        if not self._persistence_path:
            return
        path = Path(self._persistence_path)
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            entries = payload.get("entries", [])
            self._entries = {}
            for item in entries:
                entry = AuditEntry.from_dict(item)
                self._entries[entry.entry_id] = entry
            if len(self._entries) > self._max_entries:
                kept = sorted(self._entries.values(), key=lambda e: e.timestamp)[-self._max_entries :]
                self._entries = {entry.entry_id: entry for entry in kept}
            logger.info("Loaded federation audit log: %d entries", len(self._entries))
        except Exception as exc:
            logger.warning("Failed to load federation audit log: %s", exc)
