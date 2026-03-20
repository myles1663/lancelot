# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
OTel Provider — TracerProvider + MeterProvider initialization.

Sets up the OpenTelemetry SDK with OTLP/HTTP exporter. The provider
is initialized once at gateway startup and used for the lifetime of
the process.

Design principles:
- OTel failure never blocks receipt writes
- T2/T3 spans always exported at 100%
- T0/T1 sampling configurable (default 10%)
- All spans use 'lancelot.' namespace prefix
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional, Sequence

logger = logging.getLogger("lancelot.observability.otel_provider")

# Module-level references
_tracer = None
_meter = None
_span_processor = None
_initialized = False


def init_otel(
    endpoint: str,
    auth_header: str = "",
    export_interval_ms: int = 5000,
    resource_attributes: Optional[Dict[str, str]] = None,
) -> bool:
    """Initialize OpenTelemetry TracerProvider and MeterProvider.

    Args:
        endpoint: OTLP/HTTP endpoint URL (e.g., https://otel-collector:4318)
        auth_header: Optional auth header value for the exporter
        export_interval_ms: Batch export interval in milliseconds
        resource_attributes: Additional resource attributes (deployment_id, etc.)

    Returns:
        True if initialization succeeded, False otherwise.
    """
    global _tracer, _meter, _span_processor, _initialized

    if _initialized:
        logger.warning("OTel already initialized, skipping re-init")
        return True

    try:
        from opentelemetry import trace, metrics
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    except ImportError as exc:
        logger.error(
            "OpenTelemetry packages not installed. "
            "Install: pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http. "
            "Error: %s", exc
        )
        return False

    try:
        # Build resource with Lancelot-specific attributes
        res_attrs = {
            "service.name": "lancelot",
            "service.namespace": "governance",
        }
        if resource_attributes:
            res_attrs.update(resource_attributes)

        resource = Resource.create(res_attrs)

        # Exporter headers
        headers = {}
        if auth_header:
            # auth_header format: "HeaderName: value" or just a bearer token
            if ":" in auth_header:
                key, _, val = auth_header.partition(":")
                headers[key.strip()] = val.strip()
            else:
                headers["Authorization"] = f"Bearer {auth_header}"

        # Trace exporter (OTLP/HTTP)
        traces_endpoint = endpoint.rstrip("/") + "/v1/traces"
        span_exporter = OTLPSpanExporter(
            endpoint=traces_endpoint,
            headers=headers,
            timeout=10,
        )
        _span_processor = BatchSpanProcessor(
            span_exporter,
            schedule_delay_millis=export_interval_ms,
            max_export_batch_size=512,
            max_queue_size=2048,
        )

        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(_span_processor)
        trace.set_tracer_provider(tracer_provider)
        _tracer = trace.get_tracer("lancelot.governance", "1.0.0")

        # Metric exporter (OTLP/HTTP)
        metrics_endpoint = endpoint.rstrip("/") + "/v1/metrics"
        metric_exporter = OTLPMetricExporter(
            endpoint=metrics_endpoint,
            headers=headers,
            timeout=10,
        )
        metric_reader = PeriodicExportingMetricReader(
            metric_exporter,
            export_interval_millis=export_interval_ms,
        )
        meter_provider = MeterProvider(
            resource=resource,
            metric_readers=[metric_reader],
        )
        metrics.set_meter_provider(meter_provider)
        _meter = metrics.get_meter("lancelot.governance", "1.0.0")

        # Initialize the 12 metric instruments
        from src.observability.metrics import init_metrics
        init_metrics(_meter)

        _initialized = True
        logger.info(
            "OTel initialized: endpoint=%s, interval=%dms",
            endpoint, export_interval_ms,
        )
        return True

    except Exception as exc:
        logger.error("OTel initialization failed: %s", exc)
        return False


def shutdown_otel() -> None:
    """Gracefully shutdown OTel exporters. Flush pending spans/metrics."""
    global _initialized
    if not _initialized:
        return

    try:
        from opentelemetry import trace, metrics

        tp = trace.get_tracer_provider()
        if hasattr(tp, "shutdown"):
            tp.shutdown()

        mp = metrics.get_meter_provider()
        if hasattr(mp, "shutdown"):
            mp.shutdown()

        _initialized = False
        logger.info("OTel shutdown complete")
    except Exception as exc:
        logger.warning("OTel shutdown error: %s", exc)


def get_tracer():
    """Return the Lancelot OTel tracer, or None if not initialized."""
    return _tracer


def get_meter():
    """Return the Lancelot OTel meter, or None if not initialized."""
    return _meter


def is_initialized() -> bool:
    """Check if OTel is initialized and active."""
    return _initialized
