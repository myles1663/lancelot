# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Federation Receipt Manager — typed methods for all federation receipt types.

Wraps emit_federation_receipt() with specific methods for each event type,
ensuring consistent metadata and receipt chaining.
Follows the HIVE receipt_manager pattern (src/hive/receipt_manager.py).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.federation.receipts import emit_federation_receipt

logger = logging.getLogger(__name__)


class FederationReceiptManager:
    """Typed receipt emission for all federation events.

    Every federation action is receipt-traced through this manager.
    Federation receipts carry instance_id, federation_quest_id,
    and soul_version_hash in metadata for cross-instance traceability.
    """

    def __init__(
        self,
        instance_id: str,
        data_dir: str = "/home/lancelot/data",
    ):
        self._instance_id = instance_id
        self._data_dir = data_dir

    # ── Heartbeat Events ──────────────────────────────────────────

    def record_heartbeat_emitted(
        self,
        soul_version_hash: str = "",
        deployment_mode: str = "standalone",
        peer_count: int = 0,
    ) -> str:
        """Record a heartbeat emission. Returns receipt ID."""
        receipt = emit_federation_receipt(
            event_type="heartbeat",
            action_name="heartbeat_emitted",
            inputs={
                "deployment_mode": deployment_mode,
                "peer_count": peer_count,
            },
            instance_id=self._instance_id,
            soul_version_hash=soul_version_hash,
            data_dir=self._data_dir,
        )
        return receipt.id

    def record_staleness_detected(
        self,
        peer_instance_id: str,
        staleness_level: str,
        age_seconds: float,
    ) -> str:
        """Record that a peer has become stale."""
        receipt = emit_federation_receipt(
            event_type="heartbeat",
            action_name="staleness_detected",
            inputs={
                "peer_instance_id": peer_instance_id,
                "staleness_level": staleness_level,
                "age_seconds": age_seconds,
            },
            instance_id=self._instance_id,
            data_dir=self._data_dir,
        )
        return receipt.id

    # ── Identity Events ───────────────────────────────────────────

    def record_identity_generated(
        self,
        fingerprint: str,
    ) -> str:
        """Record federation identity generation."""
        receipt = emit_federation_receipt(
            event_type="identity",
            action_name="identity_generated",
            inputs={
                "fingerprint": fingerprint,
            },
            instance_id=self._instance_id,
            data_dir=self._data_dir,
        )
        return receipt.id

    def record_identity_loaded(
        self,
        fingerprint: str,
    ) -> str:
        """Record federation identity loaded from disk."""
        receipt = emit_federation_receipt(
            event_type="identity",
            action_name="identity_loaded",
            inputs={
                "fingerprint": fingerprint,
            },
            instance_id=self._instance_id,
            data_dir=self._data_dir,
        )
        return receipt.id

    # ── Topology Events ───────────────────────────────────────────

    def record_peer_registered(
        self,
        peer_instance_id: str,
        peer_fingerprint: str,
        peer_address: str = "",
    ) -> str:
        """Record a new peer registration."""
        receipt = emit_federation_receipt(
            event_type="topology",
            action_name="peer_registered",
            inputs={
                "peer_instance_id": peer_instance_id,
                "peer_fingerprint": peer_fingerprint,
                "peer_address": peer_address,
            },
            instance_id=self._instance_id,
            data_dir=self._data_dir,
        )
        return receipt.id

    def record_peer_removed(
        self,
        peer_instance_id: str,
        reason: str = "",
    ) -> str:
        """Record a peer removal from topology."""
        receipt = emit_federation_receipt(
            event_type="topology",
            action_name="peer_removed",
            inputs={
                "peer_instance_id": peer_instance_id,
                "reason": reason,
            },
            instance_id=self._instance_id,
            data_dir=self._data_dir,
        )
        return receipt.id

    def record_topology_change(
        self,
        old_mode: str,
        new_mode: str,
        peer_count: int,
    ) -> str:
        """Record deployment mode change."""
        receipt = emit_federation_receipt(
            event_type="topology",
            action_name="topology_change",
            inputs={
                "old_mode": old_mode,
                "new_mode": new_mode,
                "peer_count": peer_count,
            },
            instance_id=self._instance_id,
            data_dir=self._data_dir,
        )
        return receipt.id

    # ── Handoff Events ────────────────────────────────────────────

    def record_handoff_initiated(
        self,
        handoff_id: str,
        target_instance_id: str,
        workflow_summary: str = "",
        federation_quest_id: Optional[str] = None,
        parent_receipt_id: Optional[str] = None,
    ) -> str:
        """Record a handoff initiation to a target instance."""
        receipt = emit_federation_receipt(
            event_type="handoff",
            action_name="handoff_initiated",
            inputs={
                "target_instance_id": target_instance_id,
                "workflow_summary": workflow_summary,
            },
            instance_id=self._instance_id,
            federation_quest_id=federation_quest_id,
            handoff_id=handoff_id,
            parent_id=parent_receipt_id,
            data_dir=self._data_dir,
        )
        return receipt.id

    def record_handoff_received(
        self,
        handoff_id: str,
        source_instance_id: str,
        federation_quest_id: Optional[str] = None,
    ) -> str:
        """Record a handoff received from a source instance."""
        receipt = emit_federation_receipt(
            event_type="handoff",
            action_name="handoff_received",
            inputs={
                "source_instance_id": source_instance_id,
            },
            instance_id=self._instance_id,
            federation_quest_id=federation_quest_id,
            handoff_id=handoff_id,
            data_dir=self._data_dir,
        )
        return receipt.id

    def record_handoff_rejected(
        self,
        handoff_id: str,
        source_instance_id: str,
        reason: str,
        federation_quest_id: Optional[str] = None,
    ) -> str:
        """Record a handoff rejection."""
        receipt = emit_federation_receipt(
            event_type="handoff",
            action_name="handoff_rejected",
            inputs={
                "source_instance_id": source_instance_id,
                "reason": reason,
            },
            instance_id=self._instance_id,
            federation_quest_id=federation_quest_id,
            handoff_id=handoff_id,
            data_dir=self._data_dir,
        )
        return receipt.id

    # ── Soul Events ───────────────────────────────────────────────

    def record_soul_version_push(
        self,
        soul_version_hash: str,
        target_instance_ids: List[str],
    ) -> str:
        """Record a Soul version push from root to children."""
        receipt = emit_federation_receipt(
            event_type="soul",
            action_name="soul_version_push",
            inputs={
                "target_instance_ids": target_instance_ids,
                "target_count": len(target_instance_ids),
            },
            instance_id=self._instance_id,
            soul_version_hash=soul_version_hash,
            data_dir=self._data_dir,
        )
        return receipt.id

    def record_soul_handshake_ack(
        self,
        parent_instance_id: str,
        soul_version_hash: str,
        compatible: bool,
    ) -> str:
        """Record acknowledgment of a Soul version handshake."""
        receipt = emit_federation_receipt(
            event_type="soul",
            action_name="soul_handshake_ack",
            inputs={
                "parent_instance_id": parent_instance_id,
                "compatible": compatible,
            },
            instance_id=self._instance_id,
            soul_version_hash=soul_version_hash,
            data_dir=self._data_dir,
        )
        return receipt.id

    def record_divergence(
        self,
        peer_instance_id: str,
        staleness_seconds: float,
        soul_version_hash: str = "",
    ) -> str:
        """Record connectivity loss (divergence) from federation peer."""
        receipt = emit_federation_receipt(
            event_type="soul",
            action_name="divergence_detected",
            inputs={
                "peer_instance_id": peer_instance_id,
                "staleness_seconds": staleness_seconds,
            },
            instance_id=self._instance_id,
            soul_version_hash=soul_version_hash,
            data_dir=self._data_dir,
        )
        return receipt.id

    def record_reconnection(
        self,
        peer_instance_id: str,
        divergence_duration_s: float,
        reconciliation_result: str = "",
    ) -> str:
        """Record reconnection after divergence."""
        receipt = emit_federation_receipt(
            event_type="soul",
            action_name="reconnection",
            inputs={
                "peer_instance_id": peer_instance_id,
                "divergence_duration_s": divergence_duration_s,
                "reconciliation_result": reconciliation_result,
            },
            instance_id=self._instance_id,
            data_dir=self._data_dir,
        )
        return receipt.id

    # ── Budget Events ─────────────────────────────────────────────

    def record_spawn_receipt(
        self,
        agent_id: str,
        model_tier: str,
        estimated_cost: float = 0.0,
        federation_quest_id: Optional[str] = None,
    ) -> str:
        """Record a sub-agent spawn in federation context (before first token)."""
        receipt = emit_federation_receipt(
            event_type="budget",
            action_name="spawn_receipt",
            inputs={
                "agent_id": agent_id,
                "model_tier": model_tier,
                "estimated_cost": estimated_cost,
            },
            instance_id=self._instance_id,
            federation_quest_id=federation_quest_id,
            data_dir=self._data_dir,
        )
        return receipt.id

    def record_budget_threshold(
        self,
        threshold_level: str,
        utilization_pct: float,
        action_taken: str = "",
    ) -> str:
        """Record a budget threshold trigger (T2 warning, T3 escalation)."""
        receipt = emit_federation_receipt(
            event_type="budget",
            action_name="budget_threshold",
            inputs={
                "threshold_level": threshold_level,
                "utilization_pct": utilization_pct,
                "action_taken": action_taken,
            },
            instance_id=self._instance_id,
            data_dir=self._data_dir,
        )
        return receipt.id

    # ── Query Helpers ─────────────────────────────────────────────

    def get_federation_receipts(
        self,
        federation_quest_id: Optional[str] = None,
        action_types: Optional[List[str]] = None,
    ) -> List:
        """Get federation receipts, optionally filtered."""
        try:
            from receipts import get_receipt_service
        except ImportError:
            from src.shared.receipts import get_receipt_service

        if action_types is None:
            action_types = [
                "federation_heartbeat_event",
                "federation_identity_event",
                "federation_topology_event",
                "federation_handoff_event",
                "federation_soul_event",
                "federation_budget_event",
            ]

        service = get_receipt_service(self._data_dir)
        if federation_quest_id:
            return service.search(
                query=federation_quest_id,
                action_types=action_types,
            )
        return service.list(action_type=action_types[0])

    def get_handoff_chain(self, handoff_id: str) -> List:
        """Get all receipts for a specific handoff transaction."""
        try:
            from receipts import get_receipt_service
        except ImportError:
            from src.shared.receipts import get_receipt_service
        service = get_receipt_service(self._data_dir)
        return service.search(
            query=handoff_id,
            action_types=["federation_handoff_event"],
        )
