# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Incident Receipt Hook — non-blocking callback for the receipt bridge.

Registered into the observability receipt bridge to evaluate every
persisted receipt against trigger rules. When a trigger fires:
1. Dedup check against IncidentStore
2. Create IncidentRecord
3. Emit INCIDENT_OPENED receipt
4. Page via webhook engine (INCIDENT_PAGED receipt)

This module MUST NOT raise or block. All errors are logged and swallowed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("lancelot.incidents.receipt_hook")

# Module-level state (initialized by configure())
_enabled: bool = False
_data_dir: Optional[str] = None
_trigger_engine = None
_store = None


def configure(enabled: bool, data_dir: str) -> None:
    """Initialize the incident receipt hook.

    Called during gateway startup when FEATURE_INCIDENT_RESPONSE is on.
    """
    global _enabled, _data_dir, _trigger_engine, _store

    _enabled = enabled
    _data_dir = data_dir

    if not enabled:
        logger.info("Incident receipt hook disabled")
        return

    try:
        from src.incidents.trigger_engine import TriggerEngine
        from src.incidents.store import get_incident_store

        _trigger_engine = TriggerEngine()
        _store = get_incident_store(data_dir)
        logger.info(
            "Incident receipt hook configured: %d triggers active",
            len(_trigger_engine.triggers),
        )
    except Exception as exc:
        logger.error("Failed to configure incident receipt hook: %s", exc)
        _enabled = False


def on_receipt_for_incidents(receipt_dict: Dict[str, Any]) -> None:
    """Non-blocking callback invoked by the receipt bridge.

    Evaluates the receipt against trigger rules, handles dedup,
    and creates incident records when triggers fire.
    """
    if not _enabled or _trigger_engine is None or _store is None:
        return

    try:
        _evaluate_receipt(receipt_dict)
    except Exception as exc:
        logger.debug("Incident hook failed (non-blocking): %s", exc)


def _evaluate_receipt(receipt_dict: Dict[str, Any]) -> None:
    """Core evaluation logic. Separated for testability."""
    # Don't trigger on our own incident receipts (prevent loops)
    action_type = receipt_dict.get("action_type", "")
    if action_type.startswith("incident_") or action_type == "playbook_updated":
        return

    # Evaluate triggers
    incident = _trigger_engine.evaluate(receipt_dict)
    if incident is None:
        return

    # Dedup check: same (trigger_type, source_id) within window?
    existing_id = _store.find_by_dedup_key(
        incident.dedup_key,
        # Find the trigger to get its specific window
        window_seconds=_get_dedup_window(incident.playbook_name),
    )
    if existing_id is not None:
        logger.debug(
            "Dedup: trigger %s matched existing incident %s",
            incident.dedup_key, existing_id,
        )
        return

    # Also check exact trigger receipt dedup
    exact_match = _store.find_by_trigger_receipt(incident.trigger_receipt_id)
    if exact_match is not None:
        logger.debug(
            "Dedup: trigger receipt %s already opened incident %s",
            incident.trigger_receipt_id, exact_match,
        )
        return

    # Create the incident
    _store.create(incident)

    # Emit INCIDENT_OPENED receipt (system-generated, no operator_id)
    _emit_incident_receipt(
        "incident_opened",
        {
            "incident_id": incident.incident_id,
            "trigger_receipt_id": incident.trigger_receipt_id,
            "category": incident.category,
            "severity": incident.severity,
            "playbook_name": incident.playbook_name,
        },
    )

    # Page responders
    _page_responders(incident)


def _get_dedup_window(playbook_name: str) -> int:
    """Get the dedup window for a trigger by playbook name."""
    if _trigger_engine is None:
        return 300

    for trigger in _trigger_engine.triggers:
        if trigger.playbook == playbook_name:
            return trigger.dedup_window_seconds
    return 300  # Default 5 minutes


def _page_responders(incident) -> None:
    """Send paging notification via the webhook engine."""
    try:
        now = datetime.now(timezone.utc).isoformat()
        incident.paged_at = now

        # Update the incident with paged_at
        _store.update(incident)

        # Emit INCIDENT_PAGED receipt (system-generated)
        _emit_incident_receipt(
            "incident_paged",
            {
                "incident_id": incident.incident_id,
                "paging_channel": "webhook",
                "escalation_number": 1,
                "severity": incident.severity,
            },
        )
        logger.info(
            "Paging sent for incident %s [%s]",
            incident.incident_id, incident.severity,
        )
    except Exception as exc:
        logger.error("Paging failed for incident %s: %s",
                      incident.incident_id, exc)


def _emit_incident_receipt(action_type: str, metadata: Dict[str, Any]) -> None:
    """Emit a receipt for an incident lifecycle event."""
    try:
        from src.shared.receipts import (
            create_receipt, get_receipt_service,
            ActionType, CognitionTier,
        )

        receipt = create_receipt(
            ActionType(action_type),
            f"incident_response:{action_type}",
            metadata,
            tier=CognitionTier.DETERMINISTIC,
        )

        svc = get_receipt_service()
        if svc:
            svc.create(receipt)
    except Exception as exc:
        logger.debug("Failed to emit incident receipt %s: %s", action_type, exc)
