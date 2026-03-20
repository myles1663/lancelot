# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Federation Handoff Protocol — Task context packaging, delivery, and receipt exchange.

Implements the full handoff lifecycle between federation peers:
    1. Source packages task context + soul context + receipt chain
    2. Source POSTs handoff to target via /api/federation/handoff/initiate
    3. Target validates contract, checks assumptions, accepts/rejects
    4. Target executes task and reports completion via callback
    5. Source receives completion report with receipts

Handoff states:
    INITIATED → ACCEPTED → IN_PROGRESS → COMPLETED
    INITIATED → REJECTED
    ACCEPTED → FAILED
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.federation.identity import FederationIdentity
from src.federation.topology import TopologyRegistry
from src.federation.transport import FederationTransport

logger = logging.getLogger(__name__)


@dataclass
class HandoffPackage:
    """Complete handoff context sent to a target peer."""
    handoff_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    federation_quest_id: str = ""
    source_instance_id: str = ""
    target_instance_id: str = ""
    task_context: Dict[str, Any] = field(default_factory=dict)
    soul_context: Dict[str, Any] = field(default_factory=dict)
    contract: Dict[str, Any] = field(default_factory=dict)
    receipt_chain: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "federation_quest_id": self.federation_quest_id,
            "source_instance_id": self.source_instance_id,
            "target_instance_id": self.target_instance_id,
            "task_context": self.task_context,
            "soul_context": self.soul_context,
            "contract": self.contract,
            "receipt_chain": self.receipt_chain,
            "created_at": self.created_at,
        }


@dataclass
class HandoffResult:
    """Result of a handoff initiation."""
    success: bool
    handoff_id: str = ""
    state: str = ""
    error: str = ""
    target_instance_id: str = ""


class HandoffProtocol:
    """Manages federation task handoffs between peers."""

    def __init__(
        self,
        identity: FederationIdentity,
        transport: FederationTransport,
        topology: TopologyRegistry,
        contradiction_detector=None,
        receipt_mgr=None,
        audit=None,
        handoff_timeout_s: float = 30.0,
    ):
        self._identity = identity
        self._transport = transport
        self._topology = topology
        self._contradiction_detector = contradiction_detector
        self._receipt_mgr = receipt_mgr
        self._audit = audit
        self._timeout_s = handoff_timeout_s

        # Track active handoffs (in-memory, keyed by handoff_id)
        self._active_handoffs: Dict[str, HandoffPackage] = {}

    async def initiate_handoff(
        self,
        target_instance_id: str,
        task_context: dict,
        soul_context: dict,
        contract: dict,
        receipt_chain: Optional[List[dict]] = None,
        federation_quest_id: str = "",
    ) -> HandoffResult:
        """Initiate a task handoff to a target peer.

        Args:
            target_instance_id: Target peer's instance ID.
            task_context: Goal, constraints, partial results.
            soul_context: Serialized operating Soul for this task.
            contract: HandoffContract fields (success_criteria, etc.).
            receipt_chain: Receipts from prior work on this task.
            federation_quest_id: Cross-instance quest tracking ID.

        Returns:
            HandoffResult with success/failure.
        """
        peer = self._topology.get_peer(target_instance_id)
        if not peer:
            return HandoffResult(
                success=False,
                error=f"Unknown target peer: {target_instance_id}",
            )

        package = HandoffPackage(
            federation_quest_id=federation_quest_id or str(uuid.uuid4()),
            source_instance_id=self._identity.instance_id,
            target_instance_id=target_instance_id,
            task_context=task_context,
            soul_context=soul_context,
            contract=contract,
            receipt_chain=receipt_chain or [],
        )

        # Send handoff to target
        result = await self._transport.send(
            peer_address=peer.address,
            method="POST",
            path="/api/federation/handoff/initiate",
            body=package.to_dict(),
            peer_id=target_instance_id,
            timeout_override_s=self._timeout_s,
        )

        if not result.success:
            return HandoffResult(
                success=False,
                handoff_id=package.handoff_id,
                error=result.error or f"Handoff failed: HTTP {result.status_code}",
                target_instance_id=target_instance_id,
            )

        response = result.body or {}

        if response.get("accepted"):
            # Track active handoff
            self._active_handoffs[package.handoff_id] = package

            if self._receipt_mgr:
                try:
                    self._receipt_mgr.record_handoff_initiated(
                        handoff_id=package.handoff_id,
                        target_id=target_instance_id,
                        quest_id=package.federation_quest_id,
                    )
                except Exception:
                    pass

            if self._audit:
                try:
                    self._audit.record(
                        event_type="handoff_initiated",
                        instance_id=self._identity.instance_id,
                        federation_quest_id=package.federation_quest_id,
                        details={
                            "handoff_id": package.handoff_id,
                            "target": target_instance_id,
                            "latency_ms": result.latency_ms,
                        },
                    )
                except Exception:
                    pass

            logger.info(
                "Handoff initiated: %s → %s (quest=%s)",
                self._identity.instance_id[:8],
                target_instance_id[:8],
                package.federation_quest_id[:8],
            )

            return HandoffResult(
                success=True,
                handoff_id=package.handoff_id,
                state="accepted",
                target_instance_id=target_instance_id,
            )

        return HandoffResult(
            success=False,
            handoff_id=package.handoff_id,
            state="rejected",
            error=response.get("reason", "Target rejected handoff"),
            target_instance_id=target_instance_id,
        )

    def handle_handoff_initiation(self, request_data: dict) -> dict:
        """Handle an incoming handoff request from a source peer.

        Validates the handoff, checks contract assumptions, and
        accepts or rejects.

        Args:
            request_data: The handoff package dict.

        Returns:
            Response dict with accepted/rejected and reason.
        """
        source_id = request_data.get("source_instance_id", "")
        handoff_id = request_data.get("handoff_id", "")
        quest_id = request_data.get("federation_quest_id", "")
        contract = request_data.get("contract", {})
        task_context = request_data.get("task_context", {})
        soul_context = request_data.get("soul_context", {})
        receipt_chain = request_data.get("receipt_chain", [])

        # Validate source is a known peer
        peer = self._topology.get_peer(source_id)
        if not peer:
            return {
                "accepted": False,
                "reason": f"Unknown source peer: {source_id}",
                "handoff_id": handoff_id,
            }

        # Validate contract has success criteria
        success_criteria = contract.get("success_criteria", [])
        if not success_criteria:
            logger.warning("Handoff %s has no success criteria", handoff_id)

        # Check for contradictions if detector available
        if self._contradiction_detector and receipt_chain:
            try:
                contradictions = self._contradiction_detector.check_receipt_chain(
                    receipt_chain
                )
                if contradictions:
                    return {
                        "accepted": False,
                        "reason": f"Receipt chain has {len(contradictions)} contradictions",
                        "handoff_id": handoff_id,
                        "contradictions": len(contradictions),
                    }
            except Exception:
                pass

        # Accept the handoff
        package = HandoffPackage(
            handoff_id=handoff_id,
            federation_quest_id=quest_id,
            source_instance_id=source_id,
            target_instance_id=self._identity.instance_id,
            task_context=task_context,
            soul_context=soul_context,
            contract=contract,
            receipt_chain=receipt_chain,
        )
        self._active_handoffs[handoff_id] = package

        if self._receipt_mgr:
            try:
                self._receipt_mgr.record_handoff_received(
                    handoff_id=handoff_id,
                    source_id=source_id,
                    quest_id=quest_id,
                )
            except Exception:
                pass

        if self._audit:
            try:
                self._audit.record(
                    event_type="handoff_received",
                    instance_id=self._identity.instance_id,
                    federation_quest_id=quest_id,
                    details={
                        "handoff_id": handoff_id,
                        "source": source_id,
                    },
                )
            except Exception:
                pass

        logger.info(
            "Handoff accepted: %s from %s (quest=%s)",
            handoff_id[:8], source_id[:8], quest_id[:8],
        )

        return {
            "accepted": True,
            "handoff_id": handoff_id,
            "instance_id": self._identity.instance_id,
        }

    async def report_completion(
        self,
        handoff_id: str,
        result: dict,
        receipts: Optional[List[dict]] = None,
    ) -> bool:
        """Report handoff completion back to the source peer.

        Args:
            handoff_id: The handoff being completed.
            result: Task execution result.
            receipts: Receipts generated during execution.

        Returns:
            True if completion report was delivered.
        """
        package = self._active_handoffs.get(handoff_id)
        if not package:
            logger.warning("Completion report for unknown handoff: %s", handoff_id)
            return False

        source_peer = self._topology.get_peer(package.source_instance_id)
        if not source_peer:
            logger.warning("Source peer not found: %s", package.source_instance_id)
            return False

        payload = {
            "handoff_id": handoff_id,
            "federation_quest_id": package.federation_quest_id,
            "reporting_instance_id": self._identity.instance_id,
            "result": result,
            "receipts": receipts or [],
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

        send_result = await self._transport.send(
            peer_address=source_peer.address,
            method="POST",
            path="/api/federation/handoff/complete",
            body=payload,
            peer_id=package.source_instance_id,
        )

        if send_result.success:
            # Clean up active handoff
            self._active_handoffs.pop(handoff_id, None)

            if self._audit:
                try:
                    self._audit.record(
                        event_type="handoff_completed",
                        instance_id=self._identity.instance_id,
                        federation_quest_id=package.federation_quest_id,
                        details={"handoff_id": handoff_id},
                    )
                except Exception:
                    pass

        return send_result.success

    def handle_completion_report(self, request_data: dict) -> dict:
        """Handle a completion report from a target peer."""
        handoff_id = request_data.get("handoff_id", "")
        quest_id = request_data.get("federation_quest_id", "")
        reporting_id = request_data.get("reporting_instance_id", "")
        result = request_data.get("result", {})

        package = self._active_handoffs.pop(handoff_id, None)

        if self._audit:
            try:
                self._audit.record(
                    event_type="handoff_completed",
                    instance_id=reporting_id,
                    federation_quest_id=quest_id,
                    details={
                        "handoff_id": handoff_id,
                        "source": self._identity.instance_id,
                    },
                )
            except Exception:
                pass

        logger.info(
            "Handoff completion received: %s from %s",
            handoff_id[:8], reporting_id[:8] if reporting_id else "unknown",
        )

        return {
            "acknowledged": True,
            "handoff_id": handoff_id,
        }

    def get_handoff_status(self, handoff_id: str) -> Optional[Dict[str, Any]]:
        """Get the current status of a handoff."""
        package = self._active_handoffs.get(handoff_id)
        if not package:
            return None
        return {
            "handoff_id": package.handoff_id,
            "federation_quest_id": package.federation_quest_id,
            "source_instance_id": package.source_instance_id,
            "target_instance_id": package.target_instance_id,
            "state": "active",
            "created_at": package.created_at,
        }

    def list_active_handoffs(self) -> List[Dict[str, Any]]:
        """List all active handoffs."""
        return [
            self.get_handoff_status(hid) for hid in self._active_handoffs
        ]
