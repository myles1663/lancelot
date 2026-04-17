# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Governance Receipt Emission Helper — Phase A2.

Thin wrapper that resolves OperatorIdentity from a FastAPI request
and emits a governance receipt in one call.  Used by all War Room API
endpoints that perform governance-significant actions (kill switch
toggles, connector enable/disable, credential store/delete, scheduler
CRUD, APL rule lifecycle, HIVE interventions, etc.).

Usage:
    from src.core.governance_receipts import emit_governance_receipt

    emit_governance_receipt(
        request,
        ActionType.KILL_SWITCH_ISSUED,
        action_name="toggle_flag",
        inputs={"flag": name, "new_value": False},
    )
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import Request

from src.core.operator_identity import OperatorIdentity
from src.shared.receipts import (
    ActionType,
    Receipt,
    ReceiptService,
    ReceiptStatus,
)

logger = logging.getLogger("lancelot.governance_receipts")

_receipt_service: Optional[ReceiptService] = None


def init_governance_receipts(receipt_service: ReceiptService) -> None:
    """Wire receipt service reference (called from gateway startup)."""
    global _receipt_service
    _receipt_service = receipt_service
    logger.info("Governance receipt helper initialised")


def _resolve_identity(request: Request):
    """Resolve OperatorIdentity from War Room session or API key."""
    from src.core.auth_api import resolve_operator_identity, get_api_key_identity

    identity = resolve_operator_identity(request)
    if identity is None:
        identity = get_api_key_identity(request)
    return identity


def emit_governance_receipt(
    request: Request,
    action_type: ActionType,
    action_name: str,
    inputs: Optional[Dict[str, Any]] = None,
    outputs: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    quest_id: Optional[str] = None,
) -> Optional[Receipt]:
    """Emit a governance receipt with OperatorIdentity from the request.

    Resolves identity from the War Room session token or falls back to
    API-key identity.  Returns the persisted Receipt, or None if the
    receipt service is unavailable.

    This function is intentionally **non-throwing** for the caller —
    receipt emission failures are logged but do not block the governance
    action itself.  The receipt enforcement layer in ReceiptService will
    still record a GOVERNANCE_WRITE_ERROR if identity is missing on a
    required type.
    """
    if _receipt_service is None:
        logger.warning(
            "Receipt service not initialised — governance receipt skipped: %s",
            action_type.value,
        )
        return None

    try:
        identity = _resolve_identity(request)

        receipt = Receipt(
            action_type=action_type.value,
            action_name=action_name,
            inputs=inputs or {},
            outputs=outputs or {},
            status=ReceiptStatus.SUCCESS.value,
            operator_id=identity.operator_id if identity else None,
            session_id=identity.session_id if identity else None,
            metadata=metadata or {},
            quest_id=quest_id,
        )
        return _receipt_service.create(receipt)
    except Exception as exc:
        logger.error(
            "Failed to emit governance receipt %s/%s: %s",
            action_type.value,
            action_name,
            exc,
        )
        return None


def emit_governance_receipt_for_identity(
    identity: OperatorIdentity,
    action_type: ActionType,
    action_name: str,
    inputs: Optional[Dict[str, Any]] = None,
    outputs: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    quest_id: Optional[str] = None,
) -> Optional[Receipt]:
    """Emit a governance receipt using an already-resolved operator identity."""
    if _receipt_service is None:
        logger.warning(
            "Receipt service not initialised — governance receipt skipped: %s",
            action_type.value,
        )
        return None
    try:
        receipt = Receipt(
            action_type=action_type.value,
            action_name=action_name,
            inputs=inputs or {},
            outputs=outputs or {},
            status=ReceiptStatus.SUCCESS.value,
            operator_id=identity.operator_id,
            session_id=identity.session_id,
            metadata=metadata or {},
            quest_id=quest_id,
        )
        return _receipt_service.create(receipt)
    except Exception as exc:
        logger.error(
            "Failed to emit governance receipt %s/%s: %s",
            action_type.value,
            action_name,
            exc,
        )
        return None
