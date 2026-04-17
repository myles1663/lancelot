# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Observability API — /api/observability/*

War Room endpoints for configuring OTel export, webhooks, and metrics API.
Read/write configuration, check OTel status, test webhook delivery.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from src.core.api_auth import require_authenticated_request
from src.core.auth_api import require_operator_capability

from src.observability.config import (
    ObservabilityConfig,
    OTelConfig,
    WebhookConfig,
    MetricsApiConfig,
    load_config,
    save_config,
)

logger = logging.getLogger("lancelot.observability.api")

router = APIRouter(
    prefix="/api/observability",
    tags=["observability"],
    dependencies=[
        Depends(require_authenticated_request),
        Depends(require_operator_capability("observability.admin")),
    ],
)


# ── Request / Response Models ─────────────────────────────────────

class OTelConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    endpoint: Optional[str] = None
    auth_header: Optional[str] = None
    export_interval_s: Optional[int] = None
    sampling_rate_t0_t1: Optional[float] = None
    resource_attributes: Optional[Dict[str, str]] = None


class WebhookConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    delivery_timeout_s: Optional[int] = None
    max_retries: Optional[int] = None


class MetricsApiConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    rate_limit_per_minute: Optional[int] = None
    receipt_queries: Optional[bool] = None


# ── Endpoints ─────────────────────────────────────────────────────

@router.get("/config")
async def get_config():
    """Get current observability configuration."""
    config = load_config()
    result = config.to_dict()
    # Redact auth header for display
    if result.get("otel", {}).get("auth_header"):
        result["otel"]["auth_header"] = "***configured***"
    return {"config": result}


@router.patch("/config/otel")
async def update_otel_config(body: OTelConfigUpdate, request: Request):
    """Update OTel exporter configuration."""
    config = load_config()

    if body.enabled is not None:
        config.otel.enabled = body.enabled
    if body.endpoint is not None:
        config.otel.endpoint = body.endpoint
    if body.auth_header is not None:
        config.otel.auth_header = body.auth_header
    if body.export_interval_s is not None:
        config.otel.export_interval_s = max(1, min(60, body.export_interval_s))
    if body.sampling_rate_t0_t1 is not None:
        config.otel.sampling_rate_t0_t1 = max(0.0, min(1.0, body.sampling_rate_t0_t1))
    if body.resource_attributes is not None:
        config.otel.resource_attributes = body.resource_attributes

    save_config(config)

    # Emit governance receipt for config change
    try:
        from src.core.governance_receipts import emit_governance_receipt
        from src.shared.receipts import ActionType
        emit_governance_receipt(
            request,
            ActionType.SYSTEM,
            action_name="observability_otel_config_updated",
            inputs={"endpoint": config.otel.endpoint, "enabled": config.otel.enabled},
        )
    except Exception:
        pass

    # Apply changes to the live bridge if OTel is initialized
    try:
        from src.observability.receipt_bridge import configure_bridge
        from src.observability.otel_provider import is_initialized
        configure_bridge(
            enabled=config.otel.enabled and is_initialized(),
            sampling_rate=config.otel.sampling_rate_t0_t1,
        )
    except Exception:
        pass

    return {"status": "updated", "otel": config.otel.__dict__}


@router.patch("/config/webhooks")
async def update_webhook_config(body: WebhookConfigUpdate, request: Request):
    """Update webhook subsystem configuration."""
    config = load_config()

    if body.enabled is not None:
        config.webhooks.enabled = body.enabled
    if body.delivery_timeout_s is not None:
        config.webhooks.delivery_timeout_s = max(1, min(30, body.delivery_timeout_s))
    if body.max_retries is not None:
        config.webhooks.max_retries = max(1, min(10, body.max_retries))

    save_config(config)
    return {"status": "updated"}


@router.patch("/config/metrics-api")
async def update_metrics_api_config(body: MetricsApiConfigUpdate, request: Request):
    """Update Metrics API configuration."""
    config = load_config()

    if body.enabled is not None:
        config.metrics_api.enabled = body.enabled
    if body.rate_limit_per_minute is not None:
        config.metrics_api.rate_limit_per_minute = max(1, min(600, body.rate_limit_per_minute))
    if body.receipt_queries is not None:
        config.metrics_api.receipt_queries = body.receipt_queries

    save_config(config)
    return {"status": "updated"}


@router.get("/status")
async def otel_status():
    """Check OTel exporter status."""
    config = load_config()

    otel_initialized = False
    try:
        from src.observability.otel_provider import is_initialized
        otel_initialized = is_initialized()
    except Exception:
        pass

    bridge_enabled = False
    try:
        from src.observability.receipt_bridge import _enabled
        bridge_enabled = _enabled
    except Exception:
        pass

    return {
        "feature_flag": _get_feature_flag(),
        "otel": {
            "configured": bool(config.otel.endpoint),
            "enabled": config.otel.enabled,
            "initialized": otel_initialized,
            "bridge_active": bridge_enabled,
            "endpoint": config.otel.endpoint or "(not configured)",
            "sampling_rate_t0_t1": config.otel.sampling_rate_t0_t1,
            "export_interval_s": config.otel.export_interval_s,
        },
        "webhooks": {
            "enabled": config.webhooks.enabled,
            "endpoint_count": len(config.webhooks.endpoints),
            "active_endpoints": sum(1 for ep in config.webhooks.endpoints if ep.enabled),
        },
        "metrics_api": {
            "enabled": config.metrics_api.enabled,
            "rate_limit_per_minute": config.metrics_api.rate_limit_per_minute,
        },
    }


@router.get("/webhooks/endpoints")
async def list_webhook_endpoints():
    """List registered webhook endpoints (secrets redacted)."""
    config = load_config()
    endpoints = []
    for ep in config.webhooks.endpoints:
        endpoints.append({
            "id": ep.id,
            "url": ep.url,
            "categories": ep.categories,
            "cost_thresholds": ep.cost_thresholds,
            "enabled": ep.enabled,
            "has_secret": bool(ep.secret_vault_key),
        })
    return {"endpoints": endpoints, "total": len(endpoints)}


class WebhookEndpointCreate(BaseModel):
    url: str = Field(..., description="HTTPS webhook target URL")
    categories: list = Field(default_factory=list, description="Subscribed event categories")
    secret: str = Field("", description="HMAC shared secret (stored in vault)")
    cost_thresholds: list = Field(default_factory=lambda: [0.75, 0.90, 1.0])
    enabled: bool = True


@router.post("/webhooks/endpoints")
async def register_webhook_endpoint(body: WebhookEndpointCreate, request: Request):
    """Register a new webhook endpoint."""
    # Validate HTTPS
    if not body.url.startswith("https://"):
        raise HTTPException(status_code=400, detail="Webhook endpoints must use HTTPS")

    # Validate categories
    from src.observability.webhook_categories import ALL_CATEGORIES
    for cat in body.categories:
        if cat not in ALL_CATEGORIES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid category '{cat}'. Options: {ALL_CATEGORIES}",
            )

    config = load_config()

    import uuid as _uuid
    ep_id = str(_uuid.uuid4())[:8]
    vault_key = ""

    # Store secret in vault if provided
    if body.secret:
        vault_key = f"webhook_secret_{ep_id}"
        try:
            import secret_cache
            if secret_cache.is_bootstrapped():
                # Store in environment for secret_cache access
                import os
                os.environ[vault_key] = body.secret
        except Exception:
            pass

    from src.observability.config import WebhookEndpoint as WEP
    new_ep = WEP(
        id=ep_id,
        url=body.url,
        categories=body.categories,
        secret_vault_key=vault_key,
        cost_thresholds=body.cost_thresholds,
        enabled=body.enabled,
    )
    config.webhooks.endpoints.append(new_ep)
    save_config(config)

    # Hot-update the engine if running
    try:
        from src.observability.webhook_engine import get_webhook_engine
        engine = get_webhook_engine()
        if engine:
            engine.update_endpoints(config.webhooks.endpoints)
    except Exception:
        pass

    return {"status": "registered", "endpoint_id": ep_id}


@router.delete("/webhooks/endpoints/{endpoint_id}")
async def remove_webhook_endpoint(endpoint_id: str):
    """Remove a webhook endpoint."""
    config = load_config()
    original_len = len(config.webhooks.endpoints)
    config.webhooks.endpoints = [
        ep for ep in config.webhooks.endpoints if ep.id != endpoint_id
    ]
    if len(config.webhooks.endpoints) == original_len:
        raise HTTPException(status_code=404, detail=f"Endpoint {endpoint_id} not found")

    save_config(config)

    # Hot-update
    try:
        from src.observability.webhook_engine import get_webhook_engine
        engine = get_webhook_engine()
        if engine:
            engine.update_endpoints(config.webhooks.endpoints)
    except Exception:
        pass

    return {"status": "removed", "endpoint_id": endpoint_id}


@router.get("/webhooks/stats")
async def webhook_delivery_stats():
    """Get webhook delivery statistics per endpoint."""
    try:
        from src.observability.webhook_engine import get_webhook_engine
        engine = get_webhook_engine()
        if engine:
            return {"stats": engine.get_stats()}
    except Exception:
        pass
    return {"stats": {}}


def _get_feature_flag() -> bool:
    """Check FEATURE_OBSERVABILITY flag."""
    try:
        from src.core.feature_flags import FEATURE_OBSERVABILITY
        return FEATURE_OBSERVABILITY
    except (ImportError, AttributeError):
        return False
