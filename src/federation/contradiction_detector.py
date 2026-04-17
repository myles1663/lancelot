# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Receipt DAG Contradiction Detector — continuous consistency checking.

Evaluates downstream outputs against upstream handoff contract assumptions.
Three assumption categories:
- FACTUAL: Schema validation (does the data match expected structure?)
- CONSTRAINT: Range checks (are values within expected bounds?)
- TEMPORAL: Timestamp consistency (is ordering preserved?)

Surfaces contradictions in real time with full context and resolution options.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class AssumptionCategory(str, Enum):
    """Category of contract assumption being checked."""
    FACTUAL = "factual"        # Schema/structure validation
    CONSTRAINT = "constraint"  # Range/bound checks
    TEMPORAL = "temporal"      # Timestamp ordering


class ContradictionSeverity(str, Enum):
    """Severity of a detected contradiction."""
    LOW = "low"          # Informational — may self-resolve
    MEDIUM = "medium"    # Requires attention but not blocking
    HIGH = "high"        # Blocking — workflow should pause
    CRITICAL = "critical"  # Requires immediate operator intervention


class ContradictionState(str, Enum):
    """State of a contradiction."""
    ACTIVE = "active"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


@dataclass
class ContractAssumptionCheck:
    """A single assumption to check against a receipt."""
    assumption_id: str
    category: AssumptionCategory
    description: str
    check_expression: str = ""  # JSON path or field reference
    expected_value: Any = None
    actual_value: Any = None
    passed: bool = False


@dataclass
class Contradiction:
    """A detected contradiction in the receipt DAG."""
    contradiction_id: str
    federation_quest_id: str
    source_instance_id: str
    target_instance_id: str
    source_receipt_id: str = ""
    target_receipt_id: str = ""
    edge_id: str = ""
    category: AssumptionCategory = AssumptionCategory.FACTUAL
    severity: ContradictionSeverity = ContradictionSeverity.MEDIUM
    state: ContradictionState = ContradictionState.ACTIVE
    description: str = ""
    assumption_text: str = ""
    expected: str = ""
    actual: str = ""
    detected_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    resolved_at: Optional[str] = None
    resolved_by: str = ""
    resolution_action: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contradiction_id": self.contradiction_id,
            "federation_quest_id": self.federation_quest_id,
            "source_instance_id": self.source_instance_id,
            "target_instance_id": self.target_instance_id,
            "source_receipt_id": self.source_receipt_id,
            "target_receipt_id": self.target_receipt_id,
            "edge_id": self.edge_id,
            "category": self.category.value,
            "severity": self.severity.value,
            "state": self.state.value,
            "description": self.description,
            "assumption_text": self.assumption_text,
            "expected": self.expected,
            "actual": self.actual,
            "detected_at": self.detected_at,
            "resolved_at": self.resolved_at,
            "resolution_action": self.resolution_action,
        }


class ContradictionDetector:
    """Detects and tracks contradictions in federation receipt DAGs.

    Continuously checks downstream outputs against upstream handoff contract
    assumptions. Manages contradiction lifecycle: detect → acknowledge →
    resolve/escalate.
    """

    def __init__(
        self,
        on_contradiction: Optional[Callable[[Contradiction], None]] = None,
    ):
        """
        Args:
            on_contradiction: Callback when a new contradiction is detected.
        """
        self._on_contradiction = on_contradiction
        self._contradictions: Dict[str, Contradiction] = {}
        self._lock = threading.Lock()

    def check_receipt_chain(
        self,
        receipt_chain: List[Dict[str, Any]],
        *,
        federation_quest_id: str = "",
        source_instance_id: str = "",
        target_instance_id: str = "",
        contract: Optional[Dict[str, Any]] = None,
        result: Optional[Dict[str, Any]] = None,
        edge_id: str = "",
    ) -> List[Contradiction]:
        """Run the live contradiction checks used by federation handoffs."""
        contradictions: List[Contradiction] = []
        contract = contract or {}
        result = result or {}

        previous_timestamp: Optional[str] = None
        previous_receipt_id: str = ""
        for index, receipt in enumerate(receipt_chain or []):
            current_timestamp = self._extract_timestamp(receipt)
            if previous_timestamp and current_timestamp:
                contradiction = self.check_temporal(
                    contradiction_id=f"{edge_id or 'handoff'}:temporal:{index}",
                    federation_quest_id=federation_quest_id,
                    source_instance_id=source_instance_id,
                    target_instance_id=target_instance_id,
                    assumption_text="Federation receipt chain must remain temporally ordered",
                    upstream_timestamp=previous_timestamp,
                    downstream_timestamp=current_timestamp,
                    source_receipt_id=previous_receipt_id,
                    target_receipt_id=str(receipt.get("id", "")),
                    edge_id=edge_id,
                )
                if contradiction:
                    contradictions.append(contradiction)
            if current_timestamp:
                previous_timestamp = current_timestamp
                previous_receipt_id = str(receipt.get("id", ""))

        expected_schema = (
            contract.get("result_schema")
            or contract.get("result_payload_schema")
            or contract.get("data_payload_schema")
            or {}
        )
        if expected_schema and isinstance(result, dict):
            contradiction = self.check_factual(
                contradiction_id=f"{edge_id or 'handoff'}:factual:result",
                federation_quest_id=federation_quest_id,
                source_instance_id=source_instance_id,
                target_instance_id=target_instance_id,
                assumption_text="Federation completion result must satisfy the declared payload schema",
                expected_schema=self._normalize_schema(expected_schema),
                actual_data=result,
                source_receipt_id=previous_receipt_id,
                edge_id=edge_id,
            )
            if contradiction:
                contradictions.append(contradiction)

        for field_name, bounds in (contract.get("constraint_bounds") or {}).items():
            if not isinstance(bounds, dict):
                continue
            value = self._lookup_field(result, field_name)
            if value is None or not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            contradiction = self.check_constraint(
                contradiction_id=f"{edge_id or 'handoff'}:constraint:{field_name}",
                federation_quest_id=federation_quest_id,
                source_instance_id=source_instance_id,
                target_instance_id=target_instance_id,
                assumption_text=f"Federation completion field '{field_name}' must remain within declared bounds",
                field_name=field_name,
                value=float(value),
                min_value=bounds.get("min"),
                max_value=bounds.get("max"),
                source_receipt_id=previous_receipt_id,
                edge_id=edge_id,
            )
            if contradiction:
                contradictions.append(contradiction)

        if contract.get("success_criteria") and self._result_failed(result):
            contradiction = Contradiction(
                contradiction_id=f"{edge_id or 'handoff'}:success_criteria",
                federation_quest_id=federation_quest_id,
                source_instance_id=source_instance_id,
                target_instance_id=target_instance_id,
                source_receipt_id=previous_receipt_id,
                edge_id=edge_id,
                category=AssumptionCategory.CONSTRAINT,
                severity=ContradictionSeverity.HIGH,
                description="Completion result did not satisfy declared success criteria",
                assumption_text="Federation handoff completion must satisfy its declared success criteria",
                expected=str(contract.get("success_criteria", [])),
                actual=str(result),
            )
            self._record(contradiction)
            contradictions.append(contradiction)

        return contradictions

    def check_factual(
        self,
        contradiction_id: str,
        federation_quest_id: str,
        source_instance_id: str,
        target_instance_id: str,
        assumption_text: str,
        expected_schema: Dict[str, Any],
        actual_data: Dict[str, Any],
        source_receipt_id: str = "",
        target_receipt_id: str = "",
        edge_id: str = "",
    ) -> Optional[Contradiction]:
        """Check a factual (schema) assumption.

        Returns a Contradiction if the actual data doesn't match the expected schema,
        or None if it passes.
        """
        missing_keys = set(expected_schema.keys()) - set(actual_data.keys())
        if not missing_keys:
            return None

        c = Contradiction(
            contradiction_id=contradiction_id,
            federation_quest_id=federation_quest_id,
            source_instance_id=source_instance_id,
            target_instance_id=target_instance_id,
            source_receipt_id=source_receipt_id,
            target_receipt_id=target_receipt_id,
            edge_id=edge_id,
            category=AssumptionCategory.FACTUAL,
            severity=ContradictionSeverity.HIGH,
            description=f"Missing expected keys in payload: {missing_keys}",
            assumption_text=assumption_text,
            expected=str(list(expected_schema.keys())),
            actual=str(list(actual_data.keys())),
        )

        self._record(c)
        return c

    def check_constraint(
        self,
        contradiction_id: str,
        federation_quest_id: str,
        source_instance_id: str,
        target_instance_id: str,
        assumption_text: str,
        field_name: str,
        value: float,
        min_value: Optional[float] = None,
        max_value: Optional[float] = None,
        source_receipt_id: str = "",
        target_receipt_id: str = "",
        edge_id: str = "",
    ) -> Optional[Contradiction]:
        """Check a constraint (range) assumption.

        Returns a Contradiction if the value is out of bounds.
        """
        violations = []
        if min_value is not None and value < min_value:
            violations.append(f"{field_name}={value} < min={min_value}")
        if max_value is not None and value > max_value:
            violations.append(f"{field_name}={value} > max={max_value}")

        if not violations:
            return None

        c = Contradiction(
            contradiction_id=contradiction_id,
            federation_quest_id=federation_quest_id,
            source_instance_id=source_instance_id,
            target_instance_id=target_instance_id,
            source_receipt_id=source_receipt_id,
            target_receipt_id=target_receipt_id,
            edge_id=edge_id,
            category=AssumptionCategory.CONSTRAINT,
            severity=ContradictionSeverity.MEDIUM,
            description=f"Constraint violation: {'; '.join(violations)}",
            assumption_text=assumption_text,
            expected=f"min={min_value}, max={max_value}",
            actual=str(value),
        )

        self._record(c)
        return c

    def check_temporal(
        self,
        contradiction_id: str,
        federation_quest_id: str,
        source_instance_id: str,
        target_instance_id: str,
        assumption_text: str,
        upstream_timestamp: str,
        downstream_timestamp: str,
        source_receipt_id: str = "",
        target_receipt_id: str = "",
        edge_id: str = "",
    ) -> Optional[Contradiction]:
        """Check a temporal (ordering) assumption.

        Returns a Contradiction if downstream timestamp precedes upstream.
        """
        try:
            upstream_dt = datetime.fromisoformat(upstream_timestamp)
            downstream_dt = datetime.fromisoformat(downstream_timestamp)
        except ValueError:
            # Can't parse — flag as contradiction
            c = Contradiction(
                contradiction_id=contradiction_id,
                federation_quest_id=federation_quest_id,
                source_instance_id=source_instance_id,
                target_instance_id=target_instance_id,
                source_receipt_id=source_receipt_id,
                target_receipt_id=target_receipt_id,
                edge_id=edge_id,
                category=AssumptionCategory.TEMPORAL,
                severity=ContradictionSeverity.LOW,
                description="Cannot parse timestamps for temporal check",
                assumption_text=assumption_text,
                expected=upstream_timestamp,
                actual=downstream_timestamp,
            )
            self._record(c)
            return c

        if downstream_dt < upstream_dt:
            c = Contradiction(
                contradiction_id=contradiction_id,
                federation_quest_id=federation_quest_id,
                source_instance_id=source_instance_id,
                target_instance_id=target_instance_id,
                source_receipt_id=source_receipt_id,
                target_receipt_id=target_receipt_id,
                edge_id=edge_id,
                category=AssumptionCategory.TEMPORAL,
                severity=ContradictionSeverity.HIGH,
                description=(
                    f"Temporal ordering violated: downstream ({downstream_timestamp}) "
                    f"precedes upstream ({upstream_timestamp})"
                ),
                assumption_text=assumption_text,
                expected=upstream_timestamp,
                actual=downstream_timestamp,
            )
            self._record(c)
            return c

        return None

    def acknowledge(self, contradiction_id: str, operator: str) -> bool:
        """Acknowledge a contradiction (operator has seen it)."""
        with self._lock:
            c = self._contradictions.get(contradiction_id)
            if not c or c.state != ContradictionState.ACTIVE:
                return False
            c.state = ContradictionState.ACKNOWLEDGED
            return True

    def resolve(
        self,
        contradiction_id: str,
        resolved_by: str,
        action: str,
    ) -> bool:
        """Resolve a contradiction with a specific action."""
        with self._lock:
            c = self._contradictions.get(contradiction_id)
            if not c or c.state in (
                ContradictionState.RESOLVED, ContradictionState.ESCALATED
            ):
                return False
            c.state = ContradictionState.RESOLVED
            c.resolved_at = datetime.now(timezone.utc).isoformat()
            c.resolved_by = resolved_by
            c.resolution_action = action
            return True

    def escalate(self, contradiction_id: str) -> bool:
        """Escalate a contradiction to T3."""
        with self._lock:
            c = self._contradictions.get(contradiction_id)
            if not c or c.state in (
                ContradictionState.RESOLVED, ContradictionState.ESCALATED
            ):
                return False
            c.state = ContradictionState.ESCALATED
            c.severity = ContradictionSeverity.CRITICAL
            return True

    def get_contradiction(self, contradiction_id: str) -> Optional[Contradiction]:
        with self._lock:
            return self._contradictions.get(contradiction_id)

    def get_active(self) -> List[Contradiction]:
        with self._lock:
            return [
                c for c in self._contradictions.values()
                if c.state in (ContradictionState.ACTIVE, ContradictionState.ACKNOWLEDGED)
            ]

    def get_by_quest(self, federation_quest_id: str) -> List[Contradiction]:
        with self._lock:
            return [
                c for c in self._contradictions.values()
                if c.federation_quest_id == federation_quest_id
            ]

    def get_all(self) -> List[Contradiction]:
        with self._lock:
            return list(self._contradictions.values())

    def _record(self, c: Contradiction) -> None:
        """Record a new contradiction. Thread-safe."""
        with self._lock:
            self._contradictions[c.contradiction_id] = c

        if self._on_contradiction:
            try:
                self._on_contradiction(c)
            except Exception as e:
                logger.error("Contradiction callback failed: %s", e)

    @staticmethod
    def _extract_timestamp(receipt: Dict[str, Any]) -> Optional[str]:
        for key in ("timestamp", "created_at", "completed_at", "detected_at"):
            value = receipt.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _normalize_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
        required = list(schema.get("required", []) or [])
        properties = schema.get("properties", {}) or {}
        normalized = {key: {} for key in required}
        for key in required:
            if isinstance(properties.get(key), dict):
                normalized[key] = properties[key]
        return normalized

    @staticmethod
    def _lookup_field(payload: Dict[str, Any], field_name: str) -> Any:
        current: Any = payload
        for part in field_name.split("."):
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current

    @staticmethod
    def _result_failed(result: Dict[str, Any]) -> bool:
        if result.get("success") is False:
            return True
        status = str(result.get("status", "")).strip().lower()
        return status in {"failed", "failure", "error", "rejected", "denied"}
