# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
State Snapshot Reader — Reconstructs governance context at any point in time.

Builds a snapshot of the system's governance state by reading receipts,
kill switches, trust ledger, cost data, and Soul version at a given
timestamp or receipt ID. Used by the Time-Travel Debugging UI (State
Inspector) to show what the system looked like when a receipt was created.

Public API:
    StateSnapshotReader(receipt_service, soul_dir) — constructor
    read_snapshot(receipt_id) → StateSnapshot
    read_snapshot_at(timestamp) → StateSnapshot
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class StateSnapshot:
    """Point-in-time governance context snapshot.

    Attributes:
        timestamp: ISO 8601 timestamp of the snapshot point.
        receipt_id: Receipt ID that anchors this snapshot (if applicable).
        quest_id: Quest this snapshot belongs to.
        soul_version: Active Soul version at snapshot time.
        kill_switches: Kill switch states at snapshot time.
        trust_tier: Effective trust tier at snapshot time.
        trust_records: Recent trust ledger entries up to snapshot time.
        cost_data: Accumulated cost data up to snapshot time.
        active_flags: Feature flag states (reconstructed from receipts).
        receipt_chain: Ordered receipt chain leading to this point.
        metadata: Additional context (soul_constraints_active, apl_rules_active).
    """
    timestamp: str = ""
    receipt_id: Optional[str] = None
    quest_id: Optional[str] = None
    soul_version: Optional[str] = None
    kill_switches: Dict[str, bool] = field(default_factory=dict)
    trust_tier: Optional[int] = None
    trust_records: List[Dict[str, Any]] = field(default_factory=list)
    cost_data: Dict[str, Any] = field(default_factory=dict)
    active_flags: Dict[str, bool] = field(default_factory=dict)
    receipt_chain: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "receipt_id": self.receipt_id,
            "quest_id": self.quest_id,
            "soul_version": self.soul_version,
            "kill_switches": self.kill_switches,
            "trust_tier": self.trust_tier,
            "trust_records": self.trust_records,
            "cost_data": self.cost_data,
            "active_flags": self.active_flags,
            "receipt_chain_length": len(self.receipt_chain),
            "metadata": self.metadata,
        }


class StateSnapshotReader:
    """Reconstructs governance context from receipt data + system state.

    Uses the receipt history and current system APIs to build a snapshot
    of what the governance environment looked like at a given point.
    """

    def __init__(
        self,
        receipt_service: Any,
        soul_dir: Optional[str] = None,
    ):
        self._receipt_service = receipt_service
        self._soul_dir = soul_dir

    def read_snapshot(self, receipt_id: str) -> StateSnapshot:
        """Build a state snapshot anchored at a specific receipt.

        Reads the receipt, then reconstructs the governance context
        at the time that receipt was created.
        """
        receipt = self._receipt_service.get(receipt_id)
        if receipt is None:
            raise ValueError(f"Receipt not found: {receipt_id}")

        snapshot = StateSnapshot(
            timestamp=receipt.timestamp,
            receipt_id=receipt.id,
            quest_id=receipt.quest_id,
        )

        # Reconstruct each dimension of governance context
        self._populate_soul_version(snapshot)
        self._populate_kill_switches(snapshot)
        self._populate_trust_data(snapshot)
        self._populate_cost_data(snapshot)
        self._populate_receipt_chain(snapshot)
        self._populate_metadata(snapshot)

        return snapshot

    def read_snapshot_at(self, timestamp: str) -> StateSnapshot:
        """Build a state snapshot at an arbitrary timestamp.

        Finds the most recent receipt at or before the given timestamp
        and uses it as the anchor point.
        """
        receipts = self._receipt_service.list(
            limit=1,
            until=timestamp,
        )

        snapshot = StateSnapshot(timestamp=timestamp)

        if receipts:
            snapshot.receipt_id = receipts[0].id
            snapshot.quest_id = receipts[0].quest_id

        self._populate_soul_version(snapshot)
        self._populate_kill_switches(snapshot)
        self._populate_trust_data(snapshot)
        self._populate_cost_data(snapshot)
        self._populate_receipt_chain(snapshot)
        self._populate_metadata(snapshot)

        return snapshot

    def _populate_soul_version(self, snapshot: StateSnapshot) -> None:
        """Determine the Soul version active at snapshot time.

        Looks for the most recent SOUL_UPDATED or SOUL_VERSION_PINNED receipt
        before the snapshot timestamp. Falls back to current active version.
        """
        try:
            soul_receipts = self._receipt_service.list(
                limit=1,
                action_type="soul_updated",
                until=snapshot.timestamp,
            )
            if soul_receipts:
                outputs = soul_receipts[0].outputs or {}
                snapshot.soul_version = outputs.get(
                    "new_version",
                    outputs.get("version"),
                )
            else:
                # Fall back to current active version
                try:
                    from src.core.soul.store import get_active_version
                    snapshot.soul_version = get_active_version(self._soul_dir)
                except Exception:
                    snapshot.soul_version = "unknown"
        except Exception as e:
            logger.warning("Failed to determine Soul version for snapshot: %s", e)
            snapshot.soul_version = "unknown"

    def _populate_kill_switches(self, snapshot: StateSnapshot) -> None:
        """Reconstruct kill switch state at snapshot time.

        Replays KILL_SWITCH_ISSUED and KILL_SWITCH_LIFTED receipts
        up to the snapshot timestamp to determine which switches were active.
        """
        try:
            # Get all kill switch receipts up to snapshot time
            issued = self._receipt_service.list(
                limit=500,
                action_type="kill_switch_issued",
                until=snapshot.timestamp,
            )
            lifted = self._receipt_service.list(
                limit=500,
                action_type="kill_switch_lifted",
                until=snapshot.timestamp,
            )

            # Build state by replaying in chronological order
            switches: Dict[str, bool] = {}
            all_events = sorted(
                [(r, True) for r in issued] + [(r, False) for r in lifted],
                key=lambda x: x[0].timestamp,
            )
            for receipt, is_issued in all_events:
                flag_name = (receipt.inputs or {}).get(
                    "flag_name",
                    (receipt.outputs or {}).get("flag_name", ""),
                )
                if flag_name:
                    switches[flag_name] = is_issued

            snapshot.kill_switches = switches
        except Exception as e:
            logger.warning("Failed to reconstruct kill switches: %s", e)

    def _populate_trust_data(self, snapshot: StateSnapshot) -> None:
        """Get trust ledger state at snapshot time.

        Queries the trust ledger for the effective tier and recent records.
        Note: trust ledger doesn't support point-in-time queries natively,
        so we use current state as best approximation.
        """
        try:
            from src.core.governance.trust_ledger import TrustLedger
            ledger = TrustLedger()
            snapshot.trust_tier = ledger.get_effective_tier()
            records = ledger.list_records(limit=20)
            snapshot.trust_records = [
                r.to_dict() if hasattr(r, "to_dict") else vars(r)
                for r in records
                if hasattr(r, "timestamp")
                and r.timestamp <= snapshot.timestamp
            ]
        except Exception as e:
            logger.debug("Trust ledger unavailable for snapshot: %s", e)

    def _populate_cost_data(self, snapshot: StateSnapshot) -> None:
        """Aggregate cost data up to snapshot time.

        Sums token_count from all receipts up to the snapshot timestamp.
        """
        try:
            stats = self._receipt_service.get_stats(
                until=snapshot.timestamp if hasattr(
                    self._receipt_service.get_stats, "__code__"
                ) and "until" in self._receipt_service.get_stats.__code__.co_varnames
                else None,
                quest_id=snapshot.quest_id,
            )
            snapshot.cost_data = {
                "total_tokens": stats.get("tokens", {}).get("total", 0),
                "total_receipts": stats.get("total_receipts", 0),
                "total_duration_ms": stats.get("duration_ms", {}).get("total", 0),
            }
        except Exception as e:
            logger.debug("Cost data unavailable for snapshot: %s", e)

    def _populate_receipt_chain(self, snapshot: StateSnapshot) -> None:
        """Build the receipt chain for the quest up to snapshot time.

        If quest_id is available, gets all receipts in the quest ordered
        chronologically, truncated at the snapshot timestamp.
        """
        if not snapshot.quest_id:
            return

        try:
            all_receipts = self._receipt_service.get_quest_receipts(
                snapshot.quest_id,
            )
            snapshot.receipt_chain = [
                r.to_dict()
                for r in all_receipts
                if r.timestamp <= snapshot.timestamp
            ]
        except Exception as e:
            logger.debug("Failed to build receipt chain: %s", e)

    def _populate_metadata(self, snapshot: StateSnapshot) -> None:
        """Add governance metadata to the snapshot.

        Includes soul_constraints_active and apl_rules_active flags
        based on current feature flag state.
        """
        try:
            from src.core.feature_flags import (
                FEATURE_SOUL,
                FEATURE_APPROVAL_LEARNING,
                FEATURE_TRUST_LEDGER,
                FEATURE_TIME_TRAVEL,
                get_all_flags,
            )
            snapshot.metadata["soul_constraints_active"] = FEATURE_SOUL
            snapshot.metadata["apl_rules_active"] = FEATURE_APPROVAL_LEARNING
            snapshot.metadata["trust_ledger_active"] = FEATURE_TRUST_LEDGER
            snapshot.metadata["time_travel_active"] = FEATURE_TIME_TRAVEL
            snapshot.active_flags = get_all_flags()
        except Exception as e:
            logger.debug("Feature flags unavailable for snapshot: %s", e)
