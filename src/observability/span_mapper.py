# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Receipt-to-Span Mapper — translates Lancelot receipts into OTel spans.

Mapping rules (from spec Section 2.1):
  quest_id     → trace_id
  receipt_id   → span_id
  parent_id    → parent_span_id
  receipt_type → span.name (lancelot.{type_lowercase})
  timestamp    → span start_time
  duration_ms  → span duration (0 for instantaneous events)
  risk_tier    → span attribute: lancelot.risk_tier
  soul_version → span attribute: lancelot.soul_version
  operator_id  → span attribute: lancelot.operator_id
  status       → span status (BLOCKED_* → ERROR)
"""

from __future__ import annotations

import hashlib
import logging
import struct
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence

logger = logging.getLogger("lancelot.observability.span_mapper")

# Blocked receipt types produce ERROR spans
_BLOCKED_PREFIXES = ("blocked_", "governance_write_error")

# Tier labels for span attributes
_TIER_LABELS = {0: "T0", 1: "T1", 2: "T2", 3: "T3"}


def _deterministic_trace_id(quest_id: str) -> bytes:
    """Derive a deterministic 16-byte OTel trace_id from quest_id.

    Uses SHA-256 truncated to 16 bytes.  Deterministic so that all
    spans in the same quest map to the same trace.
    """
    digest = hashlib.sha256(quest_id.encode("utf-8")).digest()
    return digest[:16]


def _deterministic_span_id(receipt_id: str) -> bytes:
    """Derive a deterministic 8-byte OTel span_id from receipt_id."""
    digest = hashlib.sha256(receipt_id.encode("utf-8")).digest()
    return digest[:8]


def span_name(action_type: str) -> str:
    """Generate OTel span name from receipt action_type.

    Convention: lancelot.{action_type_lowercase}
    """
    return f"lancelot.{action_type.lower()}"


def is_error_receipt(action_type: str, status: str) -> bool:
    """Check if a receipt should produce an ERROR span."""
    at_lower = action_type.lower()
    for prefix in _BLOCKED_PREFIXES:
        if at_lower.startswith(prefix):
            return True
    if status == "failure":
        return True
    return False


def receipt_to_span_attrs(receipt_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Extract OTel span attributes from a receipt dictionary.

    Returns a flat dict of attributes suitable for setting on an OTel span.
    """
    attrs: Dict[str, Any] = {}

    # Core governance attributes
    attrs["lancelot.receipt_id"] = receipt_dict.get("id", "")
    attrs["lancelot.action_type"] = receipt_dict.get("action_type", "")
    attrs["lancelot.action_name"] = receipt_dict.get("action_name", "")
    attrs["lancelot.status"] = receipt_dict.get("status", "")

    # Risk tier
    tier = receipt_dict.get("tier", 0)
    attrs["lancelot.risk_tier"] = _TIER_LABELS.get(tier, f"T{tier}")

    # Operator identity
    operator_id = receipt_dict.get("operator_id")
    if operator_id:
        attrs["lancelot.operator_id"] = operator_id

    session_id = receipt_dict.get("session_id")
    if session_id:
        attrs["lancelot.session_id"] = session_id

    # Quest
    quest_id = receipt_dict.get("quest_id")
    if quest_id:
        attrs["lancelot.quest_id"] = quest_id

    # Duration
    duration_ms = receipt_dict.get("duration_ms")
    if duration_ms is not None:
        attrs["lancelot.duration_ms"] = duration_ms

    # Token count
    token_count = receipt_dict.get("token_count")
    if token_count is not None:
        attrs["lancelot.token_count"] = token_count

    # Error message
    error_message = receipt_dict.get("error_message")
    if error_message:
        attrs["lancelot.error_message"] = error_message

    # Soul version from metadata
    metadata = receipt_dict.get("metadata") or {}
    if isinstance(metadata, dict):
        soul_version = metadata.get("soul_version")
        if soul_version:
            attrs["lancelot.soul_version"] = soul_version

    return attrs


def should_sample(tier: int, sampling_rate: float) -> bool:
    """Determine if a span should be exported based on tier and sampling rate.

    T2 and T3 are always exported (100%).
    T0 and T1 are sampled at the configured rate.
    Governance events (kill switches, T3 approvals, Soul changes) always export.
    """
    if tier >= 2:
        return True
    # Random sampling for T0/T1
    import random
    return random.random() < sampling_rate


# Governance event types that are never sampled out regardless of tier
ALWAYS_EXPORT_TYPES = frozenset({
    "kill_switch_issued",
    "kill_switch_lifted",
    "t3_approved",
    "t3_rejected",
    "soul_updated",
    "soul_version_pinned",
    "crusader_mode_activated",
    "crusader_mode_deactivated",
    "agent_stopped",
    "governance_write_error",
    "compliance_export_generated",
    "mcp_tool_blocked",
})


def should_export(action_type: str, tier: int, sampling_rate: float) -> bool:
    """Final export decision combining tier sampling and governance overrides."""
    if action_type.lower() in ALWAYS_EXPORT_TYPES:
        return True
    return should_sample(tier, sampling_rate)
