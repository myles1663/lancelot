# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Receipt Bridge — hooks into the receipt write path to export OTel spans
and update OTel metrics.

This module provides the `on_receipt_written()` callback that is called
by ReceiptService.create() after a receipt is persisted. It:

1. Checks sampling rules (T2/T3 always, T0/T1 sampled, governance always)
2. Creates an OTel span from the receipt data
3. Updates the 12 OTel metric instruments

Design constraint: this callback MUST NOT raise exceptions or block
the receipt write path. All errors are logged and swallowed.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("lancelot.observability.receipt_bridge")

# Module-level config
_sampling_rate: float = 0.1  # T0/T1 sampling rate
_enabled: bool = False


def configure_bridge(enabled: bool, sampling_rate: float = 0.1) -> None:
    """Configure the receipt bridge.

    Args:
        enabled: Whether OTel export is active
        sampling_rate: T0/T1 span sampling rate (0.0-1.0)
    """
    global _enabled, _sampling_rate
    _enabled = enabled
    _sampling_rate = max(0.0, min(1.0, sampling_rate))
    logger.info(
        "Receipt bridge configured: enabled=%s, sampling_rate=%.2f",
        enabled, _sampling_rate,
    )


def on_receipt_written(receipt_dict: Dict[str, Any]) -> None:
    """Callback invoked after a receipt is successfully persisted.

    This is the single integration point between the receipt system
    and the observability layer. It MUST NOT raise or block.

    Args:
        receipt_dict: The receipt as a dictionary (from Receipt.to_dict())
    """
    if not _enabled:
        return

    try:
        _export_span(receipt_dict)
    except Exception as exc:
        logger.debug("Span export failed (non-blocking): %s", exc)

    try:
        from src.observability.metrics import update_metrics_from_receipt
        update_metrics_from_receipt(receipt_dict)
    except Exception as exc:
        logger.debug("Metrics update failed (non-blocking): %s", exc)

    try:
        _deliver_webhooks(receipt_dict)
    except Exception as exc:
        logger.debug("Webhook delivery failed (non-blocking): %s", exc)

    try:
        _evaluate_incident_triggers(receipt_dict)
    except Exception as exc:
        logger.debug("Incident trigger eval failed (non-blocking): %s", exc)


def _deliver_webhooks(receipt_dict: Dict[str, Any]) -> None:
    """Deliver webhooks for a receipt to matching endpoints."""
    from src.observability.webhook_engine import get_webhook_engine
    engine = get_webhook_engine()
    if engine is None:
        return
    engine.on_receipt(receipt_dict)


def _evaluate_incident_triggers(receipt_dict: Dict[str, Any]) -> None:
    """Forward receipt to incident response hook if enabled."""
    try:
        from src.incidents.receipt_hook import on_receipt_for_incidents
        on_receipt_for_incidents(receipt_dict)
    except ImportError:
        pass  # Incidents module not available


def _export_span(receipt_dict: Dict[str, Any]) -> None:
    """Create and export an OTel span from a receipt."""
    from src.observability.otel_provider import get_tracer, is_initialized
    from src.observability.span_mapper import (
        should_export,
        span_name,
        is_error_receipt,
        receipt_to_span_attrs,
        _deterministic_trace_id,
        _deterministic_span_id,
    )

    if not is_initialized():
        return

    tracer = get_tracer()
    if tracer is None:
        return

    action_type = receipt_dict.get("action_type", "")
    tier = receipt_dict.get("tier", 0)

    # Sampling decision
    if not should_export(action_type, tier, _sampling_rate):
        return

    # Build span context
    quest_id = receipt_dict.get("quest_id") or receipt_dict.get("id", "no-quest")
    receipt_id = receipt_dict.get("id", "")
    parent_id = receipt_dict.get("parent_id")

    name = span_name(action_type)
    attrs = receipt_to_span_attrs(receipt_dict)

    # Parse timestamp for span timing
    timestamp_str = receipt_dict.get("timestamp", "")
    duration_ms = receipt_dict.get("duration_ms") or 0

    try:
        from opentelemetry import trace, context
        from opentelemetry.trace import (
            SpanKind,
            StatusCode,
            NonRecordingSpan,
            SpanContext,
            TraceFlags,
        )

        # Create deterministic IDs
        trace_id_bytes = _deterministic_trace_id(quest_id)
        span_id_bytes = _deterministic_span_id(receipt_id)
        trace_id = int.from_bytes(trace_id_bytes, "big")
        span_id = int.from_bytes(span_id_bytes, "big")

        # Build parent context if parent_id exists
        parent_ctx = None
        if parent_id:
            parent_span_id_bytes = _deterministic_span_id(parent_id)
            parent_span_id = int.from_bytes(parent_span_id_bytes, "big")
            parent_span_ctx = SpanContext(
                trace_id=trace_id,
                span_id=parent_span_id,
                is_remote=False,
                trace_flags=TraceFlags(TraceFlags.SAMPLED),
            )
            parent_ctx = trace.set_span_in_context(
                NonRecordingSpan(parent_span_ctx)
            )

        # Create and immediately end the span (receipt is already complete)
        span = tracer.start_span(
            name=name,
            context=parent_ctx,
            kind=SpanKind.INTERNAL,
            attributes=attrs,
        )

        # Set error status for blocked/failed receipts
        if is_error_receipt(action_type, receipt_dict.get("status", "")):
            error_msg = receipt_dict.get("error_message") or f"Blocked: {action_type}"
            span.set_status(StatusCode.ERROR, error_msg)

        span.end()

    except Exception as exc:
        # Never let span creation failure propagate
        logger.debug("Failed to create OTel span: %s", exc)
