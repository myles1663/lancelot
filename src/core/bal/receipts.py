"""
BAL Receipts — helper functions for emitting BAL-specific receipts.

Wraps the shared receipt system with BAL-specific defaults.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from receipts import (
    ActionType,
    CognitionTier,
    Receipt,
    create_receipt,
    get_receipt_service,
)

logger = logging.getLogger(__name__)

# Map subsystem names to their ActionType values
_BAL_ACTION_TYPES = {
    "client": ActionType.BAL_CLIENT_EVENT,
    "intake": ActionType.BAL_INTAKE_EVENT,
    "repurpose": ActionType.BAL_REPURPOSE_EVENT,
    "delivery": ActionType.BAL_DELIVERY_EVENT,
    "billing": ActionType.BAL_BILLING_EVENT,
}


def emit_bal_receipt(
    event_type: str,
    action_name: str,
    inputs: Dict[str, Any],
    parent_id: Optional[str] = None,
    quest_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    data_dir: str = "/home/lancelot/data",
) -> Receipt:
    """Create and persist a BAL receipt.

    Args:
        event_type: One of "client", "intake", "repurpose", "delivery", "billing".
        action_name: Specific action name (e.g., "client_created", "intake_parsed").
        inputs: Input data for the action.
        parent_id: Optional parent receipt ID for hierarchy.
        quest_id: Optional quest ID for grouping.
        metadata: Optional additional metadata.
        data_dir: Root data directory for receipt storage.

    Returns:
        The persisted Receipt instance.
    """
    action_type = _BAL_ACTION_TYPES.get(event_type)
    if action_type is None:
        raise ValueError(
            f"Unknown BAL event type '{event_type}'. "
            f"Must be one of: {list(_BAL_ACTION_TYPES.keys())}"
        )

    receipt = create_receipt(
        action_type=action_type,
        action_name=action_name,
        inputs=inputs,
        tier=CognitionTier.DETERMINISTIC,
        parent_id=parent_id,
        quest_id=quest_id,
        metadata={"bal_subsystem": event_type, **(metadata or {})},
    )

    service = get_receipt_service(data_dir)
    service.create(receipt)

    logger.debug("BAL receipt emitted: type=%s, action=%s, id=%s",
                 event_type, action_name, receipt.id)
    return receipt
