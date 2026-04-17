# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Soul Propagation Engine — risk-tiered Soul version propagation.

Three severity classifications control how Soul updates propagate:
- T1 (Minor): Pushes via heartbeat, applies from next decision point, no pause
- T2 (Significant): Pause signal to all instances, simultaneous activation, resume
- T3 (Critical): Full stop, cancel approval queues, per-instance confirmation to resume

Soul Version Consistency Monitor tracks federation-wide Soul state:
SYNCHRONIZED → PROPAGATING → STALE → DIVERGED
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class PropagationTier(str, Enum):
    """Risk tier for Soul version changes."""
    T1_MINOR = "T1"          # Tone, cosmetic, non-behavioral
    T2_SIGNIFICANT = "T2"    # Autonomy posture, approval rules
    T3_CRITICAL = "T3"       # Risk rules, scheduling boundaries, spawn budget


class PropagationState(str, Enum):
    """State of a Soul propagation event."""
    INITIATED = "initiated"
    PAUSING = "pausing"          # T2/T3: waiting for instances to pause
    PAUSED = "paused"            # T2/T3: all instances paused
    ACTIVATING = "activating"    # Pushing new Soul version
    CONFIRMING = "confirming"    # T3: waiting for per-instance confirmation
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class ConsistencyState(str, Enum):
    """Federation-wide Soul version consistency."""
    SYNCHRONIZED = "synchronized"    # All instances on same version
    PROPAGATING = "propagating"      # Update in flight
    STALE = "stale"                  # Some instances behind
    DIVERGED = "diverged"            # Instances have incompatible versions


class InstancePropState(str, Enum):
    """Per-instance state during propagation."""
    PENDING = "pending"
    PAUSED = "paused"
    ACTIVATED = "activated"
    CONFIRMED = "confirmed"    # T3 only
    REJECTED = "rejected"
    TIMEOUT = "timeout"


@dataclass
class InstancePropagation:
    """Tracks propagation state for a single instance."""
    instance_id: str
    state: InstancePropState = InstancePropState.PENDING
    previous_version_hash: str = ""
    new_version_hash: str = ""
    updated_at: Optional[str] = None
    reject_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "state": self.state.value,
            "previous_version_hash": self.previous_version_hash,
            "new_version_hash": self.new_version_hash,
            "updated_at": self.updated_at,
            "reject_reason": self.reject_reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InstancePropagation":
        state = data.get("state", InstancePropState.PENDING.value)
        try:
            inst_state = InstancePropState(state)
        except ValueError:
            inst_state = InstancePropState.PENDING
        return cls(
            instance_id=data.get("instance_id", ""),
            state=inst_state,
            previous_version_hash=data.get("previous_version_hash", ""),
            new_version_hash=data.get("new_version_hash", ""),
            updated_at=data.get("updated_at"),
            reject_reason=data.get("reject_reason", ""),
        )


@dataclass
class SoulPropagationEvent:
    """A Soul version propagation event across the federation."""
    event_id: str
    tier: PropagationTier
    state: PropagationState = PropagationState.INITIATED
    issuer_id: str = ""
    reason: str = ""
    source_version: str = ""
    source_version_hash: str = ""
    target_version: str = ""
    target_version_hash: str = ""
    initiated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: Optional[str] = None
    instances: List[InstancePropagation] = field(default_factory=list)
    timeout_seconds: float = 60.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "tier": self.tier.value,
            "state": self.state.value,
            "issuer_id": self.issuer_id,
            "reason": self.reason,
            "source_version": self.source_version,
            "target_version": self.target_version,
            "target_version_hash": self.target_version_hash,
            "initiated_at": self.initiated_at,
            "completed_at": self.completed_at,
            "timeout_seconds": self.timeout_seconds,
            "instances": [
                i.to_dict()
                for i in self.instances
            ],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SoulPropagationEvent":
        tier = data.get("tier", PropagationTier.T1_MINOR.value)
        state = data.get("state", PropagationState.INITIATED.value)
        try:
            propagation_tier = PropagationTier(tier)
        except ValueError:
            propagation_tier = PropagationTier.T1_MINOR
        try:
            propagation_state = PropagationState(state)
        except ValueError:
            propagation_state = PropagationState.INITIATED
        return cls(
            event_id=data.get("event_id", ""),
            tier=propagation_tier,
            state=propagation_state,
            issuer_id=data.get("issuer_id", ""),
            reason=data.get("reason", ""),
            source_version=data.get("source_version", ""),
            source_version_hash=data.get("source_version_hash", ""),
            target_version=data.get("target_version", ""),
            target_version_hash=data.get("target_version_hash", ""),
            initiated_at=data.get("initiated_at", datetime.now(timezone.utc).isoformat()),
            completed_at=data.get("completed_at"),
            instances=[
                InstancePropagation.from_dict(item)
                for item in data.get("instances", []) or []
            ],
            timeout_seconds=float(data.get("timeout_seconds", 60.0) or 60.0),
        )


def classify_change_tier(
    changed_fields: List[str],
) -> PropagationTier:
    """Classify a Soul change into a propagation tier.

    Args:
        changed_fields: List of Soul field names that changed.

    Returns:
        The highest applicable tier.
    """
    t3_fields = {
        "risk_rules", "scheduling_boundaries", "spawn_budget",
        "mission", "allegiance",
    }
    t2_fields = {
        "autonomy_posture", "approval_rules",
    }
    # Everything else is T1 (tone_invariants, memory_ethics, etc.)

    fields_set = set(changed_fields)

    if fields_set & t3_fields:
        return PropagationTier.T3_CRITICAL
    if fields_set & t2_fields:
        return PropagationTier.T2_SIGNIFICANT
    return PropagationTier.T1_MINOR


class SoulPropagationEngine:
    """Manages Soul version propagation across the federation.

    Handles the full lifecycle: classify change → create event → track
    per-instance state → complete or rollback.
    """

    def __init__(
        self,
        self_instance_id: str,
        peer_ids: Optional[List[str]] = None,
        persistence_path: Optional[str] = None,
    ):
        self._self_id = self_instance_id
        self._peer_ids = list(peer_ids or [])
        self._events: Dict[str, SoulPropagationEvent] = {}
        self._consistency_state = ConsistencyState.SYNCHRONIZED
        self._persistence_path = Path(persistence_path) if persistence_path else None
        self._lock = threading.Lock()
        self._load_from_disk()

    def update_peers(self, peer_ids: List[str]) -> None:
        with self._lock:
            self._peer_ids = list(peer_ids)
            self._persist_to_disk_locked()

    @property
    def consistency_state(self) -> ConsistencyState:
        with self._lock:
            return self._consistency_state

    def initiate_propagation(
        self,
        event_id: str,
        tier: PropagationTier,
        issuer_id: str,
        reason: str,
        source_version: str,
        source_version_hash: str,
        target_version: str,
        target_version_hash: str,
        timeout_seconds: float = 60.0,
    ) -> SoulPropagationEvent:
        """Initiate a Soul propagation event.

        Creates the event and sets initial per-instance states based on tier.
        """
        with self._lock:
            instances = [
                InstancePropagation(
                    instance_id=self._self_id,
                    previous_version_hash=source_version_hash,
                    new_version_hash=target_version_hash,
                )
            ]
            for pid in self._peer_ids:
                instances.append(InstancePropagation(
                    instance_id=pid,
                    previous_version_hash=source_version_hash,
                    new_version_hash=target_version_hash,
                ))

            event = SoulPropagationEvent(
                event_id=event_id,
                tier=tier,
                issuer_id=issuer_id,
                reason=reason,
                source_version=source_version,
                source_version_hash=source_version_hash,
                target_version=target_version,
                target_version_hash=target_version_hash,
                timeout_seconds=timeout_seconds,
                instances=instances,
            )

            # Set initial state based on tier
            if tier == PropagationTier.T1_MINOR:
                # T1: Skip pause, go directly to activating
                event.state = PropagationState.ACTIVATING
            else:
                # T2/T3: Need to pause first
                event.state = PropagationState.PAUSING

            self._events[event_id] = event
            self._consistency_state = ConsistencyState.PROPAGATING
            self._persist_to_disk_locked()
            return event

    def record_pause_ack(self, event_id: str, instance_id: str) -> bool:
        """Record that an instance has paused for T2/T3 propagation."""
        with self._lock:
            event = self._events.get(event_id)
            if not event or event.state != PropagationState.PAUSING:
                return False

            inst = self._find_instance(event, instance_id)
            if not inst or inst.state != InstancePropState.PENDING:
                return False

            inst.state = InstancePropState.PAUSED
            inst.updated_at = datetime.now(timezone.utc).isoformat()

            # Check if all paused
            all_paused = all(
                i.state == InstancePropState.PAUSED for i in event.instances
            )
            if all_paused:
                event.state = PropagationState.PAUSED

            self._persist_to_disk_locked()
            return True

    def advance_to_activation(self, event_id: str) -> bool:
        """Advance a paused event to activation phase.

        For T2: called after all instances paused.
        For T3: called by operator after review.
        """
        with self._lock:
            event = self._events.get(event_id)
            if not event or event.state != PropagationState.PAUSED:
                return False
            event.state = PropagationState.ACTIVATING
            self._persist_to_disk_locked()
            return True

    def record_activation(self, event_id: str, instance_id: str) -> bool:
        """Record that an instance activated the new Soul version."""
        with self._lock:
            event = self._events.get(event_id)
            if not event or event.state != PropagationState.ACTIVATING:
                return False

            inst = self._find_instance(event, instance_id)
            if not inst:
                return False

            inst.state = InstancePropState.ACTIVATED
            inst.updated_at = datetime.now(timezone.utc).isoformat()

            # For T3, move to confirmation only after every instance has
            # reached activation (or already confirmed itself locally).
            if event.tier == PropagationTier.T3_CRITICAL:
                all_activated = all(
                    i.state in {
                        InstancePropState.ACTIVATED,
                        InstancePropState.CONFIRMED,
                    }
                    for i in event.instances
                )
                if all_activated:
                    event.state = PropagationState.CONFIRMING
                self._persist_to_disk_locked()
            else:
                # T1/T2: check if all activated
                all_activated = all(
                    i.state == InstancePropState.ACTIVATED for i in event.instances
                )
                if all_activated:
                    self._complete_event(event)
                else:
                    self._persist_to_disk_locked()

            return True

    def record_confirmation(self, event_id: str, instance_id: str) -> bool:
        """Record T3 confirmation from an instance (post-activation check)."""
        with self._lock:
            event = self._events.get(event_id)
            if not event or event.tier != PropagationTier.T3_CRITICAL:
                return False
            if event.state not in (
                PropagationState.CONFIRMING, PropagationState.ACTIVATING
            ):
                return False

            inst = self._find_instance(event, instance_id)
            if not inst:
                return False

            inst.state = InstancePropState.CONFIRMED
            inst.updated_at = datetime.now(timezone.utc).isoformat()

            self._persist_to_disk_locked()
            return True

    def complete_confirmed_event(self, event_id: str) -> bool:
        """Complete a T3 event once resume is allowed after confirmations."""
        with self._lock:
            event = self._events.get(event_id)
            if not event or event.tier != PropagationTier.T3_CRITICAL:
                return False
            if event.state == PropagationState.FAILED:
                return False
            all_confirmed = all(
                i.state == InstancePropState.CONFIRMED for i in event.instances
            )
            if not all_confirmed:
                return False
            self._complete_event(event)
            return True

    def record_rejection(
        self,
        event_id: str,
        instance_id: str,
        reason: str = "",
    ) -> bool:
        """Record that an instance rejected the propagation."""
        with self._lock:
            event = self._events.get(event_id)
            if not event:
                return False

            inst = self._find_instance(event, instance_id)
            if not inst:
                return False

            inst.state = InstancePropState.REJECTED
            inst.updated_at = datetime.now(timezone.utc).isoformat()
            inst.reject_reason = reason

            # Any rejection fails the event
            event.state = PropagationState.FAILED
            event.completed_at = datetime.now(timezone.utc).isoformat()
            self._consistency_state = ConsistencyState.DIVERGED
            self._persist_to_disk_locked()
            return True

    def rollback(self, event_id: str) -> bool:
        """Roll back a failed or in-progress propagation."""
        with self._lock:
            event = self._events.get(event_id)
            if not event:
                return False
            if event.state in (PropagationState.COMPLETED, PropagationState.ROLLED_BACK):
                return False

            event.state = PropagationState.ROLLED_BACK
            event.completed_at = datetime.now(timezone.utc).isoformat()
            self._consistency_state = ConsistencyState.STALE
            self._persist_to_disk_locked()
            return True

    def get_event(self, event_id: str) -> Optional[SoulPropagationEvent]:
        with self._lock:
            return self._events.get(event_id)

    def get_active_events(self) -> List[SoulPropagationEvent]:
        with self._lock:
            return [
                e for e in self._events.values()
                if e.state not in (
                    PropagationState.COMPLETED,
                    PropagationState.FAILED,
                    PropagationState.ROLLED_BACK,
                )
            ]

    def _find_instance(
        self, event: SoulPropagationEvent, instance_id: str
    ) -> Optional[InstancePropagation]:
        """Find instance in event. Caller must hold lock."""
        return next(
            (i for i in event.instances if i.instance_id == instance_id),
            None,
        )

    def _complete_event(self, event: SoulPropagationEvent) -> None:
        """Mark event as completed. Caller must hold lock."""
        event.state = PropagationState.COMPLETED
        event.completed_at = datetime.now(timezone.utc).isoformat()
        self._consistency_state = ConsistencyState.SYNCHRONIZED
        self._persist_to_disk_locked()

    def _persist_to_disk_locked(self) -> None:
        if self._persistence_path is None:
            return
        payload = {
            "self_instance_id": self._self_id,
            "peer_ids": self._peer_ids,
            "consistency_state": self._consistency_state.value,
            "events": [event.to_dict() for event in self._events.values()],
        }
        self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
        self._persistence_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _load_from_disk(self) -> None:
        if self._persistence_path is None or not self._persistence_path.exists():
            return
        try:
            payload = json.loads(self._persistence_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to load soul propagation state: %s", exc)
            return

        try:
            consistency = ConsistencyState(
                payload.get("consistency_state", ConsistencyState.SYNCHRONIZED.value)
            )
        except ValueError:
            consistency = ConsistencyState.SYNCHRONIZED

        events = {}
        for item in payload.get("events", []) or []:
            try:
                event = SoulPropagationEvent.from_dict(item)
            except Exception as exc:
                logger.warning("Skipping invalid soul propagation event during load: %s", exc)
                continue
            if event.event_id:
                events[event.event_id] = event

        self._peer_ids = list(payload.get("peer_ids", self._peer_ids) or [])
        self._consistency_state = consistency
        self._events = events
