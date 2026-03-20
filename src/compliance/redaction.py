# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Compliance Redaction — ip_address Stripping and PRE_IDENTITY_MIGRATION Flagging.

Two unconditional transformations applied to every compliance export:

1. **ip_address redaction**: All ip_address fields from OperatorIdentity
   records are removed before any export output.  This is unconditional.
   No export format ever includes raw IP addresses.  No configuration
   flag, no bypass, no exceptions.

2. **PRE_IDENTITY_MIGRATION flagging**: Receipts generated before
   Operator Identity was implemented (operator_id is NULL) are flagged
   with a marker and explanatory note.  They do not cause export failure.
   They are present in all export formats with the flag.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List

from src.shared.receipts import Receipt

logger = logging.getLogger("lancelot.compliance.redaction")

# Keys that must be stripped from all export output
_REDACTED_KEYS = {"ip_address"}

# Marker attached to pre-identity-migration receipts
PRE_IDENTITY_MIGRATION_NOTE = (
    "This receipt was generated before Operator Identity tracking was "
    "implemented.  No operator attribution is available.  The receipt "
    "content is authentic but cannot be attributed to a named operator."
)


def redact_receipt(receipt: Receipt) -> Dict[str, Any]:
    """Convert a Receipt to a dict with ip_address redacted and
    PRE_IDENTITY_MIGRATION flagged.

    Returns a new dict — the original Receipt is not modified.
    """
    data = receipt.to_dict()
    return redact_dict(data)


def redact_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively strip ip_address from a dict and flag pre-migration."""
    result = {}
    for key, value in data.items():
        if key in _REDACTED_KEYS:
            continue  # unconditional removal
        if isinstance(value, dict):
            result[key] = redact_dict(value)
        elif isinstance(value, list):
            result[key] = [
                redact_dict(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            result[key] = value

    # Flag pre-identity-migration receipts
    if "operator_id" in data and data.get("operator_id") is None:
        # Check if this looks like a governance-era receipt (has action_type)
        if "action_type" in data:
            result["pre_identity_migration"] = True
            result["pre_identity_migration_note"] = PRE_IDENTITY_MIGRATION_NOTE

    return result


def redact_receipts(receipts: List[Receipt]) -> List[Dict[str, Any]]:
    """Redact a batch of receipts for compliance export."""
    return [redact_receipt(r) for r in receipts]


def is_pre_identity_migration(receipt: Receipt) -> bool:
    """Check if a receipt was generated before Operator Identity tracking."""
    return receipt.operator_id is None
