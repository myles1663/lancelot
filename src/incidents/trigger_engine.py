# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Trigger Detection Engine — evaluates receipts against trigger rules.

Monitors the receipt stream for trigger conditions and creates incident
records when a trigger fires. Handles deduplication so the same event
does not open multiple incidents.

Window implementation: fixed-window counters (window_type: fixed).
Upgrade path to sliding window documented per trigger if needed.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from src.incidents.models import (
    IncidentCategory,
    IncidentRecord,
    IncidentSeverity,
)

logger = logging.getLogger("lancelot.incidents.trigger_engine")


@dataclass
class TriggerCondition:
    """A field-level condition that must be true for a trigger to fire."""
    field: str
    value: Any
    operator: str = "eq"  # "eq", "contains", "gt", "lt"


@dataclass
class TriggerRule:
    """Defines when a receipt should open an incident.

    Attributes:
        name: Unique trigger identifier.
        receipt_types: Receipt action_type(s) that this trigger watches.
        conditions: Optional field-level filters on receipt metadata.
        category: Incident category to assign.
        severity: Default severity for incidents opened by this trigger.
        playbook: Playbook name to execute.
        dedup_source_field: Metadata field used as the source identifier
            for deduplication. E.g., "switch_name" for kill switches,
            "caller_agent_id" for injection events.
        dedup_window_seconds: Per-trigger dedup window (default 300s / 5 min).
        window_type: Counter window type. Currently only "fixed" is implemented.
        burst_threshold: If set, trigger only fires after this many receipts
            within burst_window_seconds (fixed-window counter).
        burst_window_seconds: Window for burst counting (default 900s / 15 min).
    """
    name: str
    receipt_types: List[str]
    category: IncidentCategory
    severity: IncidentSeverity
    playbook: str
    conditions: List[TriggerCondition] = field(default_factory=list)
    dedup_source_field: Optional[str] = None
    dedup_window_seconds: int = 300
    window_type: str = "fixed"
    burst_threshold: Optional[int] = None
    burst_window_seconds: int = 900


# ── Default trigger rules (from spec Section 5) ───────────────────

DEFAULT_TRIGGERS: List[TriggerRule] = [
    # --- GOVERNANCE_BREACH ---
    TriggerRule(
        name="kill_switch_activated",
        receipt_types=["kill_switch_issued"],
        category=IncidentCategory.GOVERNANCE_BREACH,
        severity=IncidentSeverity.HIGH,
        playbook="governance-breach-kill-switch",
        dedup_source_field="switch_name",
    ),
    TriggerRule(
        name="soul_violation",
        receipt_types=["fork_soul_rejected"],
        category=IncidentCategory.GOVERNANCE_BREACH,
        severity=IncidentSeverity.HIGH,
        playbook="governance-breach-soul-violation",
        dedup_source_field="quest_id",
        burst_threshold=2,
        burst_window_seconds=900,
    ),
    TriggerRule(
        name="t3_pattern",
        receipt_types=["t3_rejected"],
        category=IncidentCategory.GOVERNANCE_BREACH,
        severity=IncidentSeverity.HIGH,
        playbook="governance-breach-t3-pattern",
        dedup_source_field="quest_id",
        burst_threshold=5,
        burst_window_seconds=900,
    ),

    # --- SECURITY_EVENT ---
    TriggerRule(
        name="injection_detected",
        receipt_types=["mcp_tool_blocked", "a2a_inbound_blocked"],
        conditions=[TriggerCondition(field="block_reason", value="INJECTION_DETECTED")],
        category=IncidentCategory.SECURITY_EVENT,
        severity=IncidentSeverity.CRITICAL,
        playbook="security-event-injection",
        dedup_source_field="source_id",
    ),
    TriggerRule(
        name="credential_anomaly",
        receipt_types=["credential_revoked"],
        category=IncidentCategory.SECURITY_EVENT,
        severity=IncidentSeverity.HIGH,
        playbook="security-event-credential-anomaly",
        dedup_source_field="credential_name",
    ),
    TriggerRule(
        name="allowlist_violation",
        receipt_types=["a2a_outbound_blocked", "mcp_tool_blocked"],
        conditions=[TriggerCondition(field="block_reason", value="BLOCKED_ALLOWLIST")],
        category=IncidentCategory.SECURITY_EVENT,
        severity=IncidentSeverity.HIGH,
        playbook="security-event-allowlist-violation",
        dedup_source_field="endpoint",
        burst_threshold=3,
        burst_window_seconds=900,
    ),

    # --- COST_ANOMALY ---
    TriggerRule(
        name="budget_ceiling_breached",
        receipt_types=["cost_threshold_crossed"],
        conditions=[TriggerCondition(field="threshold_level", value=100, operator="eq")],
        category=IncidentCategory.COST_ANOMALY,
        severity=IncidentSeverity.MEDIUM,
        playbook="cost-anomaly-ceiling-breached",
        dedup_source_field="quest_id",
    ),
    TriggerRule(
        name="spend_rate_anomaly",
        receipt_types=["cost_threshold_crossed"],
        conditions=[TriggerCondition(field="rate_anomaly", value=True, operator="eq")],
        category=IncidentCategory.COST_ANOMALY,
        severity=IncidentSeverity.MEDIUM,
        playbook="cost-anomaly-spend-rate",
        dedup_source_field="quest_id",
        burst_threshold=3,
        burst_window_seconds=900,
    ),

    # --- AVAILABILITY_INCIDENT ---
    TriggerRule(
        name="receipt_write_failure",
        receipt_types=["governance_write_error"],
        category=IncidentCategory.AVAILABILITY_INCIDENT,
        severity=IncidentSeverity.HIGH,
        playbook="availability-receipt-write-failure",
        dedup_source_field="error_type",
    ),
    TriggerRule(
        name="agent_crash",
        receipt_types=["agent_stopped"],
        conditions=[TriggerCondition(field="expected", value=False, operator="eq")],
        category=IncidentCategory.AVAILABILITY_INCIDENT,
        severity=IncidentSeverity.HIGH,
        playbook="availability-agent-crash",
        dedup_source_field="agent_id",
    ),

    # --- COMPLIANCE_EVENT ---
    TriggerRule(
        name="chain_anomaly",
        receipt_types=["compliance_export_generated"],
        conditions=[TriggerCondition(field="chain_result", value="CHAIN_ANOMALY")],
        category=IncidentCategory.COMPLIANCE_EVENT,
        severity=IncidentSeverity.HIGH,
        playbook="compliance-chain-anomaly",
        dedup_source_field="export_id",
    ),
    TriggerRule(
        name="deprovisioned_session",
        receipt_types=["session_invalidated"],
        conditions=[TriggerCondition(field="reason", value="REFRESH_FAILED")],
        category=IncidentCategory.COMPLIANCE_EVENT,
        severity=IncidentSeverity.HIGH,
        playbook="compliance-deprovisioned-session",
        dedup_source_field="operator_id",
    ),
]


class _FixedWindowCounter:
    """Fixed-window counter for burst detection.

    Counts events per (trigger_name, source_id) within fixed time windows.
    Window boundaries are aligned to wall-clock intervals.
    """

    def __init__(self):
        self._lock = threading.Lock()
        # Key: (trigger_name, source_id) -> {"window_start": float, "count": int}
        self._counters: Dict[tuple, Dict[str, Any]] = {}

    def increment(
        self,
        trigger_name: str,
        source_id: str,
        window_seconds: int,
    ) -> int:
        """Increment counter and return the current count within the window."""
        key = (trigger_name, source_id)
        now = time.time()

        with self._lock:
            entry = self._counters.get(key)
            if entry is None or (now - entry["window_start"]) >= window_seconds:
                # Start new window
                self._counters[key] = {"window_start": now, "count": 1}
                return 1
            else:
                entry["count"] += 1
                return entry["count"]

    def reset(self, trigger_name: str, source_id: str) -> None:
        """Reset a counter (e.g., after incident is opened)."""
        key = (trigger_name, source_id)
        with self._lock:
            self._counters.pop(key, None)

    def cleanup(self, max_age_seconds: int = 3600) -> None:
        """Remove stale counters older than max_age_seconds."""
        now = time.time()
        with self._lock:
            stale = [
                k for k, v in self._counters.items()
                if (now - v["window_start"]) > max_age_seconds
            ]
            for k in stale:
                del self._counters[k]


class TriggerEngine:
    """Evaluates receipts against trigger rules and creates incidents.

    The engine is a pure evaluator. It does not subscribe to the receipt
    stream itself — the receipt bridge calls evaluate() for each receipt.
    """

    def __init__(self, triggers: Optional[List[TriggerRule]] = None):
        self._triggers = triggers or list(DEFAULT_TRIGGERS)
        self._counter = _FixedWindowCounter()

    @property
    def triggers(self) -> List[TriggerRule]:
        return list(self._triggers)

    def evaluate(self, receipt_dict: Dict[str, Any]) -> Optional[IncidentRecord]:
        """Evaluate a receipt against all trigger rules.

        Returns an IncidentRecord if a trigger fires, None otherwise.
        Does NOT handle deduplication — caller must check the store.
        """
        action_type = receipt_dict.get("action_type", "")

        for trigger in self._triggers:
            if action_type not in trigger.receipt_types:
                continue

            if not self._check_conditions(trigger, receipt_dict):
                continue

            # Extract source identifier for dedup
            source_id = self._extract_source_id(trigger, receipt_dict)

            # Burst detection: check if threshold is met
            if trigger.burst_threshold is not None:
                count = self._counter.increment(
                    trigger.name,
                    source_id,
                    trigger.burst_window_seconds,
                )
                if count < trigger.burst_threshold:
                    logger.debug(
                        "Trigger %s: burst count %d/%d for source %s",
                        trigger.name, count, trigger.burst_threshold, source_id,
                    )
                    continue
                # Threshold met — reset counter and fire
                self._counter.reset(trigger.name, source_id)

            # Build dedup key: (trigger_name, source_id)
            dedup_key = f"{trigger.name}:{source_id}"

            receipt_id = receipt_dict.get("id", receipt_dict.get("receipt_id", ""))
            incident = IncidentRecord.create(
                trigger_receipt_id=receipt_id,
                category=trigger.category,
                severity=trigger.severity,
                playbook_name=trigger.playbook,
                dedup_key=dedup_key,
            )

            logger.info(
                "Trigger fired: %s → incident %s [%s/%s]",
                trigger.name, incident.incident_id,
                trigger.category.value, trigger.severity.value,
            )
            return incident

        return None

    def _check_conditions(
        self,
        trigger: TriggerRule,
        receipt_dict: Dict[str, Any],
    ) -> bool:
        """Check all field-level conditions on a receipt."""
        if not trigger.conditions:
            return True

        metadata = receipt_dict.get("metadata", {})
        if isinstance(metadata, str):
            try:
                import json
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}

        for cond in trigger.conditions:
            actual = metadata.get(cond.field)
            if actual is None:
                # Also check top-level fields
                actual = receipt_dict.get(cond.field)
            if actual is None:
                return False

            if cond.operator == "eq" and actual != cond.value:
                return False
            elif cond.operator == "contains" and cond.value not in str(actual):
                return False
            elif cond.operator == "gt" and not (actual > cond.value):
                return False
            elif cond.operator == "lt" and not (actual < cond.value):
                return False

        return True

    def _extract_source_id(
        self,
        trigger: TriggerRule,
        receipt_dict: Dict[str, Any],
    ) -> str:
        """Extract the source identifier for deduplication."""
        if trigger.dedup_source_field is None:
            return receipt_dict.get("id", "unknown")

        # Check metadata first, then top-level
        metadata = receipt_dict.get("metadata", {})
        if isinstance(metadata, str):
            try:
                import json
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}

        value = metadata.get(trigger.dedup_source_field)
        if value is None:
            value = receipt_dict.get(trigger.dedup_source_field)
        return str(value) if value is not None else "unknown"

    def cleanup_counters(self) -> None:
        """Remove stale burst counters."""
        self._counter.cleanup()
