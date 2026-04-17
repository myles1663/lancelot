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

import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from pydantic import ValidationError

from src.core.soul.store import Soul
from src.federation.identity import FederationIdentity
from src.federation.soul_compat import (
    CompatibilityLevel,
    classify_compatibility,
    compute_soul_intersection,
    validate_more_restrictive,
)
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

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HandoffPackage":
        return cls(
            handoff_id=data.get("handoff_id", str(uuid.uuid4())),
            federation_quest_id=data.get("federation_quest_id", ""),
            source_instance_id=data.get("source_instance_id", ""),
            target_instance_id=data.get("target_instance_id", ""),
            task_context=data.get("task_context", {}) or {},
            soul_context=data.get("soul_context", {}) or {},
            contract=data.get("contract", {}) or {},
            receipt_chain=list(data.get("receipt_chain", []) or []),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
        )


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
        current_soul_provider: Optional[Callable[[], Optional[Soul]]] = None,
        persistence_path: Optional[str] = None,
    ):
        self._identity = identity
        self._transport = transport
        self._topology = topology
        self._contradiction_detector = contradiction_detector
        self._receipt_mgr = receipt_mgr
        self._audit = audit
        self._timeout_s = handoff_timeout_s
        self._current_soul_provider = current_soul_provider
        self._persistence_path = persistence_path
        self._lock = threading.Lock()

        # Track active handoffs (in-memory, keyed by handoff_id)
        self._active_handoffs: Dict[str, HandoffPackage] = {}
        self._load_from_disk()

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
            with self._lock:
                self._active_handoffs[package.handoff_id] = package
                self._persist_to_disk_locked()

            if self._receipt_mgr:
                try:
                    self._receipt_mgr.record_handoff_initiated(
                        handoff_id=package.handoff_id,
                        target_instance_id=target_instance_id,
                        federation_quest_id=package.federation_quest_id,
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

    def handle_handoff_initiation(
        self,
        request_data: dict,
        authenticated_instance_id: Optional[str] = None,
    ) -> dict:
        """Handle an incoming handoff request from a source peer.

        Validates the handoff, checks contract assumptions, and
        accepts or rejects.

        Args:
            request_data: The handoff package dict.

        Returns:
            Response dict with accepted/rejected and reason.
        """
        source_id = request_data.get("source_instance_id", "")
        if authenticated_instance_id:
            if source_id and source_id != authenticated_instance_id:
                return {
                    "accepted": False,
                    "reason": (
                        "Source instance does not match authenticated peer: "
                        f"{source_id} != {authenticated_instance_id}"
                    ),
                }
            source_id = authenticated_instance_id
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

        schema_error = self._validate_payload_schema(
            task_context,
            contract.get("data_payload_schema", {}),
        )
        if schema_error:
            return {
                "accepted": False,
                "reason": schema_error,
                "handoff_id": handoff_id,
            }

        effective_soul, soul_error = self._validate_soul_boundary(
            soul_context,
            contract.get("soul_context_constraints", {}),
        )
        if soul_error:
            return {
                "accepted": False,
                "reason": soul_error,
                "handoff_id": handoff_id,
            }

        # Check for contradictions if detector available
        if self._contradiction_detector and receipt_chain:
            try:
                contradictions = self._contradiction_detector.check_receipt_chain(
                    receipt_chain,
                    federation_quest_id=quest_id,
                    source_instance_id=source_id,
                    target_instance_id=self._identity.instance_id,
                    contract=contract,
                    edge_id=handoff_id,
                )
                if contradictions:
                    self._record_contradictions(
                        contradictions,
                        quest_id=quest_id,
                        handoff_id=handoff_id,
                    )
                    return {
                        "accepted": False,
                        "reason": f"Receipt chain has {len(contradictions)} contradictions",
                        "handoff_id": handoff_id,
                        "contradictions": len(contradictions),
                    }
            except Exception as exc:
                logger.warning(
                    "Contradiction detector failed for handoff %s during initiation: %s",
                    handoff_id,
                    exc,
                )
                return {
                    "accepted": False,
                    "reason": f"Contradiction detector unavailable: {exc}",
                    "handoff_id": handoff_id,
                }

        # Accept the handoff
        package = HandoffPackage(
            handoff_id=handoff_id,
            federation_quest_id=quest_id,
            source_instance_id=source_id,
            target_instance_id=self._identity.instance_id,
            task_context=task_context,
            soul_context=effective_soul or soul_context,
            contract=contract,
            receipt_chain=receipt_chain,
        )
        with self._lock:
            self._active_handoffs[handoff_id] = package
            self._persist_to_disk_locked()

        if self._receipt_mgr:
            try:
                self._receipt_mgr.record_handoff_received(
                    handoff_id=handoff_id,
                    source_instance_id=source_id,
                    federation_quest_id=quest_id,
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

    def _validate_payload_schema(
        self,
        task_context: Dict[str, Any],
        schema: Dict[str, Any],
    ) -> Optional[str]:
        """Validate task context against a minimal JSON-schema subset."""
        if not schema:
            return None

        required = schema.get("required", [])
        missing = [field for field in required if field not in task_context]
        if missing:
            return f"Task context missing required fields: {', '.join(sorted(missing))}"

        properties = schema.get("properties", {})
        for field, rules in properties.items():
            if field not in task_context or not isinstance(rules, dict):
                continue
            expected_type = rules.get("type")
            if expected_type and not self._matches_json_type(task_context[field], expected_type):
                return (
                    f"Task context field '{field}' does not match expected type "
                    f"'{expected_type}'"
                )
        return None

    def _validate_soul_boundary(
        self,
        soul_context: Dict[str, Any],
        constraints: Dict[str, Any],
    ) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Validate that incoming Soul context is compatible and monotonic."""
        if not soul_context:
            return None, None

        try:
            received_soul = Soul.model_validate(soul_context)
        except ValidationError as exc:
            return None, f"Invalid soul_context: {exc.errors()[0]['msg']}"

        current_soul = self._current_soul_provider() if self._current_soul_provider else None
        if current_soul is None:
            return soul_context, None

        compatibility, notes = classify_compatibility(received_soul, current_soul)
        if compatibility == CompatibilityLevel.RED:
            reason = "; ".join(notes) if notes else "Soul compatibility check failed"
            return None, f"Incompatible Soul boundary: {reason}"

        effective_soul = compute_soul_intersection(current_soul, received_soul)
        valid_current, current_violations = validate_more_restrictive(effective_soul, current_soul)
        valid_received, received_violations = validate_more_restrictive(effective_soul, received_soul)
        if not valid_current or not valid_received:
            violations = current_violations + received_violations
            return None, f"Soul intersection is not monotonic: {'; '.join(violations)}"

        if constraints and not self._matches_constraint_shape(
            effective_soul.model_dump(),
            constraints,
        ):
            return None, "Effective Soul does not satisfy handoff contract constraints"

        return effective_soul.model_dump(), None

    @staticmethod
    def _matches_json_type(value: Any, expected_type: str) -> bool:
        if expected_type == "string":
            return isinstance(value, str)
        if expected_type == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected_type == "boolean":
            return isinstance(value, bool)
        if expected_type == "object":
            return isinstance(value, dict)
        if expected_type == "array":
            return isinstance(value, list)
        return True

    def _matches_constraint_shape(self, actual: Any, expected: Any) -> bool:
        """Recursively validate a minimal constraint shape."""
        if isinstance(expected, dict):
            if not isinstance(actual, dict):
                return False
            for key, value in expected.items():
                if key not in actual:
                    return False
                if not self._matches_constraint_shape(actual[key], value):
                    return False
            return True
        if isinstance(expected, list):
            if not isinstance(actual, list):
                return False
            return all(item in actual for item in expected)
        return actual == expected

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
            with self._lock:
                self._active_handoffs.pop(handoff_id, None)
                self._persist_to_disk_locked()

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

    def handle_completion_report(
        self,
        request_data: dict,
        authenticated_instance_id: str = "",
    ) -> dict:
        """Handle a completion report from a target peer."""
        handoff_id = request_data.get("handoff_id", "")
        quest_id = request_data.get("federation_quest_id", "")
        reporting_id = request_data.get("reporting_instance_id", "")
        result = request_data.get("result", {})
        receipts = request_data.get("receipts", []) or []

        package = self._active_handoffs.get(handoff_id)
        if package is None:
            return {
                "acknowledged": False,
                "handoff_id": handoff_id,
                "error": f"Handoff {handoff_id} not found",
            }

        expected_target_id = package.target_instance_id
        peer_id = authenticated_instance_id or reporting_id
        if not peer_id:
            return {
                "acknowledged": False,
                "handoff_id": handoff_id,
                "error": "Missing authenticated peer identity",
            }

        if reporting_id and reporting_id != peer_id:
            return {
                "acknowledged": False,
                "handoff_id": handoff_id,
                "error": "Completion report instance mismatch",
            }

        if peer_id != expected_target_id:
            return {
                "acknowledged": False,
                "handoff_id": handoff_id,
                "error": "Completion report received from unexpected peer",
            }

        contradictions: List[dict] = []
        if self._contradiction_detector:
            try:
                contradictions = self._contradiction_detector.check_receipt_chain(
                    receipts,
                    federation_quest_id=quest_id or package.federation_quest_id,
                    source_instance_id=package.source_instance_id,
                    target_instance_id=peer_id,
                    contract=package.contract,
                    result=result,
                    edge_id=handoff_id,
                )
            except Exception as exc:
                logger.warning("Contradiction detector failed for handoff %s: %s", handoff_id, exc)
                return {
                    "acknowledged": False,
                    "handoff_id": handoff_id,
                    "error": f"Contradiction detector unavailable: {exc}",
                }

        if contradictions:
            self._record_contradictions(
                contradictions,
                quest_id=quest_id or package.federation_quest_id,
                handoff_id=handoff_id,
            )
            return {
                "acknowledged": False,
                "handoff_id": handoff_id,
                "error": f"Completion report contradicted upstream contract ({len(contradictions)} contradiction(s))",
                "contradictions": len(contradictions),
            }

        with self._lock:
            self._active_handoffs.pop(handoff_id, None)
            self._persist_to_disk_locked()

        if self._audit:
            try:
                self._audit.record(
                    event_type="handoff_completed",
                    instance_id=peer_id,
                    federation_quest_id=quest_id or package.federation_quest_id,
                    details={
                        "handoff_id": handoff_id,
                        "source": self._identity.instance_id,
                        "result_summary": result,
                    },
                )
            except Exception:
                pass

        logger.info(
            "Handoff completion received: %s from %s",
            handoff_id[:8], peer_id[:8],
        )

        return {
            "acknowledged": True,
            "handoff_id": handoff_id,
        }

    def _record_contradictions(
        self,
        contradictions: List[Any],
        *,
        quest_id: str,
        handoff_id: str,
    ) -> None:
        if self._audit:
            for contradiction in contradictions:
                try:
                    self._audit.record(
                        event_type="contradiction_detected",
                        instance_id=self._identity.instance_id,
                        federation_quest_id=quest_id,
                        details={
                            "handoff_id": handoff_id,
                            "contradiction_id": getattr(contradiction, "contradiction_id", ""),
                            "category": getattr(getattr(contradiction, "category", None), "value", ""),
                            "severity": getattr(getattr(contradiction, "severity", None), "value", ""),
                            "description": getattr(contradiction, "description", ""),
                        },
                    )
                except Exception:
                    pass

    def get_handoff_status(self, handoff_id: str) -> Optional[Dict[str, Any]]:
        """Get the current status of a handoff."""
        with self._lock:
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
        with self._lock:
            handoff_ids = list(self._active_handoffs.keys())
        return [self.get_handoff_status(hid) for hid in handoff_ids]

    def _persist_to_disk_locked(self) -> None:
        """Persist active handoff state to disk. Caller must hold _lock."""
        if not self._persistence_path:
            return
        try:
            path = Path(self._persistence_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "active_handoffs": [package.to_dict() for package in self._active_handoffs.values()],
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as exc:
            logger.warning("Failed to persist active federation handoffs: %s", exc)

    def _load_from_disk(self) -> None:
        """Load persisted active handoffs from disk if present."""
        if not self._persistence_path:
            return
        path = Path(self._persistence_path)
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            for item in payload.get("active_handoffs", []) or []:
                package = HandoffPackage.from_dict(item)
                self._active_handoffs[package.handoff_id] = package
            logger.info(
                "Loaded federation handoff state: %d active handoff(s)",
                len(self._active_handoffs),
            )
        except Exception as exc:
            logger.warning("Failed to load active federation handoffs: %s", exc)
