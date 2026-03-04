"""
HIVE Receipts — helper functions for emitting HIVE-specific receipts.

Wraps the shared receipt system with HIVE-specific defaults.
Follows the BAL receipts pattern (src/core/bal/receipts.py).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

try:
    from receipts import (
        ActionType,
        CognitionTier,
        Receipt,
        create_receipt,
        get_receipt_service,
    )
except ImportError:
    from src.shared.receipts import (
        ActionType,
        CognitionTier,
        Receipt,
        create_receipt,
        get_receipt_service,
    )

logger = logging.getLogger(__name__)

# Map event types to their ActionType values
_HIVE_ACTION_TYPES = {
    "task": ActionType.HIVE_TASK_EVENT,
    "agent": ActionType.HIVE_AGENT_EVENT,
    "intervention": ActionType.HIVE_INTERVENTION_EVENT,
}


def emit_hive_receipt(
    event_type: str,
    action_name: str,
    inputs: Dict[str, Any],
    parent_id: Optional[str] = None,
    quest_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    data_dir: str = "/home/lancelot/data",
) -> Receipt:
    """Create and persist a HIVE receipt.

    Args:
        event_type: One of "task", "agent", "intervention".
        action_name: Specific action name (e.g., "agent_spawned", "task_decomposed").
        inputs: Input data for the action.
        parent_id: Optional parent receipt ID for hierarchy.
        quest_id: Optional quest ID for grouping.
        metadata: Optional additional metadata.
        data_dir: Root data directory for receipt storage.

    Returns:
        The persisted Receipt instance.
    """
    action_type = _HIVE_ACTION_TYPES.get(event_type)
    if action_type is None:
        raise ValueError(
            f"Unknown HIVE event type '{event_type}'. "
            f"Must be one of: {list(_HIVE_ACTION_TYPES.keys())}"
        )

    receipt = create_receipt(
        action_type=action_type,
        action_name=action_name,
        inputs=inputs,
        tier=CognitionTier.DETERMINISTIC,
        parent_id=parent_id,
        quest_id=quest_id,
        metadata={"hive_subsystem": event_type, **(metadata or {})},
    )

    service = get_receipt_service(data_dir)
    service.create(receipt)

    logger.debug(
        "HIVE receipt emitted: type=%s, action=%s, id=%s",
        event_type, action_name, receipt.id,
    )
    return receipt
