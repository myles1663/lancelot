# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Federation Receipts — helper functions for emitting federation-specific receipts.

Wraps the shared receipt system with federation-specific defaults.
Follows the HIVE receipts pattern (src/hive/receipts.py).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

try:
    from receipts import (
        ActionType,
        CognitionTier,
        Receipt,
        create_finalized_receipt,
        get_receipt_service,
    )
except ImportError:
    from src.shared.receipts import (
        ActionType,
        CognitionTier,
        Receipt,
        create_finalized_receipt,
        get_receipt_service,
    )

logger = logging.getLogger(__name__)

# Map event categories to their ActionType values
_FEDERATION_ACTION_TYPES = {
    "heartbeat": ActionType.FEDERATION_HEARTBEAT_EVENT,
    "identity": ActionType.FEDERATION_IDENTITY_EVENT,
    "topology": ActionType.FEDERATION_TOPOLOGY_EVENT,
    "handoff": ActionType.FEDERATION_HANDOFF_EVENT,
    "soul": ActionType.FEDERATION_SOUL_EVENT,
    "budget": ActionType.FEDERATION_BUDGET_EVENT,
}


def emit_federation_receipt(
    event_type: str,
    action_name: str,
    inputs: Dict[str, Any],
    instance_id: Optional[str] = None,
    federation_quest_id: Optional[str] = None,
    handoff_id: Optional[str] = None,
    soul_version_hash: Optional[str] = None,
    parent_id: Optional[str] = None,
    quest_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    data_dir: str = "/home/lancelot/data",
) -> Receipt:
    """Create and persist a federation receipt.

    Args:
        event_type: One of "heartbeat", "identity", "topology", "handoff", "soul", "budget".
        action_name: Specific action name (e.g., "heartbeat_emitted", "peer_registered").
        inputs: Input data for the action.
        instance_id: UUID of the emitting instance.
        federation_quest_id: Immutable workflow ID spanning instances.
        handoff_id: Unique handoff transaction ID (for handoff receipts).
        soul_version_hash: SHA hash of the Soul document in effect.
        parent_id: Optional parent receipt ID for hierarchy.
        quest_id: Optional quest ID for grouping.
        metadata: Optional additional metadata.
        data_dir: Root data directory for receipt storage.

    Returns:
        The persisted Receipt instance.
    """
    action_type = _FEDERATION_ACTION_TYPES.get(event_type)
    if action_type is None:
        raise ValueError(
            f"Unknown federation event type '{event_type}'. "
            f"Must be one of: {list(_FEDERATION_ACTION_TYPES.keys())}"
        )

    # Build federation-specific metadata
    fed_metadata = {"federation_subsystem": event_type}
    if instance_id:
        fed_metadata["instance_id"] = instance_id
    if federation_quest_id:
        fed_metadata["federation_quest_id"] = federation_quest_id
    if handoff_id:
        fed_metadata["handoff_id"] = handoff_id
    if soul_version_hash:
        fed_metadata["soul_version_hash"] = soul_version_hash
    if metadata:
        fed_metadata.update(metadata)

    receipt = create_finalized_receipt(
        action_type=action_type,
        action_name=action_name,
        inputs=inputs,
        tier=CognitionTier.DETERMINISTIC,
        parent_id=parent_id,
        quest_id=quest_id,
        metadata=fed_metadata,
    )

    service = get_receipt_service(data_dir)
    service.create(receipt)

    logger.debug(
        "Federation receipt emitted: type=%s, action=%s, id=%s, instance=%s",
        event_type, action_name, receipt.id, instance_id,
    )
    return receipt
