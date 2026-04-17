# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Federation Divergence — connectivity loss detection and reconciliation.

When an instance detects that it has lost connectivity with federation peers
(staleness exceeds the lost threshold), it enters divergence mode:
- T3 operations are blocked until reconciliation completes
- T0-T2 continue under local Soul
- A Divergence Receipt is emitted

When connectivity restores, a Reconnection Receipt is emitted and
reconciliation determines if the divergence was compatible or incompatible.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from src.federation.heartbeat import StalenessLevel, compute_staleness

logger = logging.getLogger(__name__)


class DivergenceState(str, Enum):
    """Current divergence state of this instance."""
    CONNECTED = "connected"      # Normal federation connectivity
    DIVERGED = "diverged"        # Lost connectivity, T3 blocked
    RECONNECTING = "reconnecting"  # Connectivity restored, reconciling
    RECONCILED = "reconciled"    # Reconciliation complete


class ReconciliationOutcome(str, Enum):
    """Result of post-divergence reconciliation."""
    COMPATIBLE = "compatible"        # No conflicts, merge and continue
    INCOMPATIBLE = "incompatible"    # Conflicts found, needs operator review


@dataclass
class ConflictRecord:
    """A single conflict detected during reconciliation."""
    conflict_type: str    # budget_exceeded, soul_violated, task_state_conflict, handoff_collision
    description: str
    resolution: str       # auto_merge, needs_operator_review
    affected_component: str = ""


@dataclass
class DivergenceSnapshot:
    """State snapshot captured at the moment of detected isolation."""
    last_confirmed_contact_at: Optional[str] = None
    soul_hash_at_divergence: str = ""
    active_task_count: int = 0
    hive_spawn_count: int = 0
    hive_spawn_states: Dict[str, str] = field(default_factory=dict)
    pending_handoffs: List[Dict[str, str]] = field(default_factory=list)
    budget_utilization_pct: float = 0.0
    divergence_detected_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "last_confirmed_contact_at": self.last_confirmed_contact_at,
            "soul_hash_at_divergence": self.soul_hash_at_divergence,
            "active_task_count": self.active_task_count,
            "hive_spawn_count": self.hive_spawn_count,
            "hive_spawn_states": self.hive_spawn_states,
            "pending_handoffs": self.pending_handoffs,
            "budget_utilization_pct": self.budget_utilization_pct,
            "divergence_detected_at": self.divergence_detected_at,
        }


@dataclass
class ReconciliationStatus:
    """Last reconciliation result for the current/most recent divergence cycle."""
    outcome: str = ""
    conflicts: List[Dict[str, str]] = field(default_factory=list)
    reconciled_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "outcome": self.outcome,
            "conflicts": self.conflicts,
            "reconciled_at": self.reconciled_at,
        }


class DivergenceDetector:
    """Monitors federation connectivity and manages divergence state.

    Tracks the staleness of all known peers and triggers divergence
    when all peers are in LOST state.
    """

    def __init__(
        self,
        instance_id: str,
        staleness_lost_s: float = 30.0,
    ):
        self._instance_id = instance_id
        self._staleness_lost_s = staleness_lost_s
        self._state = DivergenceState.CONNECTED
        self._divergence_snapshot: Optional[DivergenceSnapshot] = None
        self._diverged_at: Optional[str] = None
        self._last_reconciliation: Optional[ReconciliationStatus] = None

    @property
    def state(self) -> DivergenceState:
        return self._state

    @property
    def is_diverged(self) -> bool:
        return self._state == DivergenceState.DIVERGED

    @property
    def divergence_snapshot(self) -> Optional[DivergenceSnapshot]:
        return self._divergence_snapshot

    @property
    def last_reconciliation(self) -> Optional[ReconciliationStatus]:
        return self._last_reconciliation

    def check_connectivity(
        self,
        peer_last_heartbeats: Dict[str, Optional[str]],
        current_soul_hash: str = "",
        active_task_count: int = 0,
        hive_spawn_count: int = 0,
        hive_spawn_states: Optional[Dict[str, str]] = None,
        pending_handoffs: Optional[List[Dict[str, str]]] = None,
        budget_utilization_pct: float = 0.0,
    ) -> Tuple[DivergenceState, Optional[DivergenceSnapshot]]:
        """Check federation connectivity based on peer heartbeat freshness.

        Args:
            peer_last_heartbeats: Map of peer_instance_id → last heartbeat ISO timestamp.
            current_soul_hash: Current Soul version hash.
            active_task_count: Number of active tasks.
            hive_spawn_count: Number of HIVE spawns.
            hive_spawn_states: Map of agent_id → state.
            pending_handoffs: List of pending handoff dicts.
            budget_utilization_pct: Current budget utilization.

        Returns:
            (new_state, snapshot_if_newly_diverged)
        """
        if not peer_last_heartbeats:
            # No peers — standalone mode, always connected
            return self._state, None

        # Check if ALL peers are lost
        all_lost = True
        any_fresh = False
        for peer_id, last_hb in peer_last_heartbeats.items():
            level, _ = compute_staleness(
                last_hb, lost_s=self._staleness_lost_s,
            )
            if level != StalenessLevel.LOST:
                all_lost = False
            if level == StalenessLevel.FRESH:
                any_fresh = True

        # State transitions
        if self._state == DivergenceState.CONNECTED and all_lost:
            # Transition to DIVERGED
            self._state = DivergenceState.DIVERGED
            self._diverged_at = datetime.now(timezone.utc).isoformat()
            self._last_reconciliation = None

            # Find the most recent heartbeat for last_confirmed_contact_at
            last_contact = None
            for last_hb in peer_last_heartbeats.values():
                if last_hb and (last_contact is None or last_hb > last_contact):
                    last_contact = last_hb

            self._divergence_snapshot = DivergenceSnapshot(
                last_confirmed_contact_at=last_contact,
                soul_hash_at_divergence=current_soul_hash,
                active_task_count=active_task_count,
                hive_spawn_count=hive_spawn_count,
                hive_spawn_states=hive_spawn_states or {},
                pending_handoffs=pending_handoffs or [],
                budget_utilization_pct=budget_utilization_pct,
            )

            logger.warning(
                "Federation divergence detected: instance=%s, last_contact=%s",
                self._instance_id, last_contact,
            )
            return self._state, self._divergence_snapshot

        elif self._state == DivergenceState.DIVERGED and any_fresh:
            # Transition to RECONNECTING
            self._state = DivergenceState.RECONNECTING
            logger.info(
                "Federation reconnection detected: instance=%s",
                self._instance_id,
            )
            return self._state, None

        return self._state, None

    def mark_reconciled(
        self,
        outcome: Optional[ReconciliationOutcome] = None,
        conflicts: Optional[List[ConflictRecord]] = None,
    ) -> None:
        """Mark this instance as reconciled and capture the reconciliation result."""
        self._state = DivergenceState.RECONCILED
        if outcome is not None:
            self._last_reconciliation = ReconciliationStatus(
                outcome=outcome.value if hasattr(outcome, "value") else str(outcome),
                conflicts=[
                    {
                        "conflict_type": c.conflict_type,
                        "description": c.description,
                        "resolution": c.resolution,
                        "affected_component": c.affected_component,
                    }
                    for c in (conflicts or [])
                ],
            )
        logger.info("Federation reconciled: instance=%s", self._instance_id)

    def reset_to_connected(self) -> None:
        """Reset to connected state (after reconciliation or operator action)."""
        self._state = DivergenceState.CONNECTED
        self._divergence_snapshot = None
        self._diverged_at = None

    def get_divergence_duration_s(self) -> float:
        """Get duration of current divergence in seconds. Returns 0 if not diverged."""
        if not self._diverged_at:
            return 0.0
        try:
            diverged_time = datetime.fromisoformat(self._diverged_at)
            if diverged_time.tzinfo is None:
                diverged_time = diverged_time.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return max(0.0, (now - diverged_time).total_seconds())
        except (ValueError, TypeError):
            return 0.0


def reconcile_divergence(
    divergence_snapshot: DivergenceSnapshot,
    reconnection_soul_hash: str,
    reconnection_budget_pct: float,
    budget_ceiling_pct: float = 100.0,
) -> Tuple[ReconciliationOutcome, List[ConflictRecord]]:
    """Reconcile a divergence event after reconnection.

    Checks for conflicts that occurred during isolation:
    1. Budget overflow: Did utilization exceed ceiling during divergence?
    2. Soul mutation: Did the Soul hash change during divergence?

    Args:
        divergence_snapshot: State at time of divergence.
        reconnection_soul_hash: Soul hash at reconnection time.
        reconnection_budget_pct: Budget utilization at reconnection time.
        budget_ceiling_pct: Maximum allowed budget percentage.

    Returns:
        (outcome, list of conflicts)
    """
    conflicts = []

    # 1. Budget overflow check
    if reconnection_budget_pct > budget_ceiling_pct:
        conflicts.append(ConflictRecord(
            conflict_type="budget_exceeded",
            description=(
                f"Budget utilization at reconnection ({reconnection_budget_pct:.1f}%) "
                f"exceeds ceiling ({budget_ceiling_pct:.1f}%)"
            ),
            resolution="needs_operator_review",
            affected_component="budget",
        ))

    # 2. Soul mutation during divergence
    if (divergence_snapshot.soul_hash_at_divergence
            and reconnection_soul_hash
            and divergence_snapshot.soul_hash_at_divergence != reconnection_soul_hash):
        conflicts.append(ConflictRecord(
            conflict_type="soul_violated",
            description=(
                f"Soul hash changed during divergence: "
                f"{divergence_snapshot.soul_hash_at_divergence[:8]} → "
                f"{reconnection_soul_hash[:8]}"
            ),
            resolution="needs_operator_review",
            affected_component="soul",
        ))

    if conflicts:
        return ReconciliationOutcome.INCOMPATIBLE, conflicts
    return ReconciliationOutcome.COMPATIBLE, []
