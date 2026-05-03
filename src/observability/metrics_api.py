# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
War Room Metrics API — /api/metrics/*

Read-only API for external dashboard integration. Exposes governance
data over HTTP with cursor pagination and per-operator bearer tokens.

All responses use a consistent envelope with soul_version.
Querying cannot modify agent behavior.
"""

from __future__ import annotations

import base64
import hashlib
import importlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from src.core.api_auth import require_authenticated_request
from src.core.auth_api import require_operator_capability, resolve_authenticated_identity
from src.observability.config import load_config

logger = logging.getLogger("lancelot.observability.metrics_api")

router = APIRouter(
    prefix="/api/metrics",
    tags=["metrics"],
    dependencies=[
        Depends(require_authenticated_request),
        Depends(require_operator_capability("observability.admin")),
    ],
)

# Module-level references
_receipt_service = None
_data_dir = "/home/lancelot/data"


def init_metrics_api(receipt_service, data_dir: str = "/home/lancelot/data") -> None:
    """Initialize the Metrics API with the receipt service."""
    global _receipt_service, _data_dir
    _receipt_service = receipt_service
    _data_dir = data_dir
    logger.info("Metrics API initialized")


def shutdown_metrics_api() -> None:
    """Clear metrics API runtime references for hot-toggle shutdown."""
    global _receipt_service, _data_dir
    _receipt_service = None
    _data_dir = "/home/lancelot/data"
    logger.info("Metrics API shutdown complete")


def _soul_payload(soul: Any) -> Dict[str, Any]:
    """Serialize a Soul model across Pydantic versions."""
    if hasattr(soul, "model_dump"):
        return soul.model_dump()
    if hasattr(soul, "dict"):
        return soul.dict()
    return dict(soul)


# ── Response Envelope ─────────────────────────────────────────────

def _envelope(
    data: Any,
    cursor: Optional[str] = None,
    has_more: bool = False,
    limit: int = 100,
    runtime_degraded: bool = False,
    degraded_reasons: Optional[List[str]] = None,
    runtime_errors: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Wrap response data in the standard Metrics API envelope."""
    soul_version = _get_soul_version()
    return {
        "api_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "deployment_id": _get_deployment_id(),
        "soul_version": soul_version,
        "runtime_degraded": runtime_degraded,
        "degraded_reasons": list(degraded_reasons or []),
        "runtime_errors": list(runtime_errors or []),
        "data": data,
        "pagination": {
            "cursor": cursor,
            "has_more": has_more,
            "limit": limit,
        },
    }


def _get_soul_version() -> str:
    """Get the current Soul version hash."""
    try:
        from src.core.soul.store import load_active_soul
        soul = load_active_soul()
        if soul:
            return hashlib.sha256(
                json.dumps(_soul_payload(soul), sort_keys=True).encode()
            ).hexdigest()[:16]
    except Exception as exc:
        logger.warning("Failed to resolve soul version for metrics API: %s", exc)
    return "unknown"


def _get_deployment_id() -> str:
    """Get the deployment ID."""
    import os
    return os.getenv("LANCELOT_DEPLOYMENT_ID", "local")


# ── Cursor Helpers ────────────────────────────────────────────────

def _encode_cursor(offset: int) -> str:
    """Encode an offset as an opaque cursor string."""
    return base64.urlsafe_b64encode(str(offset).encode()).decode()


def _decode_cursor(cursor: Optional[str]) -> int:
    """Decode a cursor back to an offset. Returns 0 on invalid."""
    if not cursor:
        return 0
    try:
        return int(base64.urlsafe_b64decode(cursor).decode())
    except Exception:
        return 0


# ── Rate Limiting ─────────────────────────────────────────────────

_rate_buckets: Dict[str, List[float]] = {}


def _check_rate_limit(operator_id: str, max_per_minute: int = 60) -> bool:
    """Simple per-operator rate limit. Returns True if allowed."""
    now = time.time()
    if operator_id not in _rate_buckets:
        _rate_buckets[operator_id] = []

    # Prune entries older than 60s
    _rate_buckets[operator_id] = [
        t for t in _rate_buckets[operator_id] if now - t < 60
    ]

    if len(_rate_buckets[operator_id]) >= max_per_minute:
        return False

    _rate_buckets[operator_id].append(now)
    return True


def _authorize_metrics_request(request: Request):
    """Enforce runtime enablement and per-operator rate limiting."""
    config = load_config()
    if not config.metrics_api.enabled:
        raise HTTPException(status_code=503, detail="Metrics API disabled")

    identity = resolve_authenticated_identity(request)
    rate_key = identity.operator_id or identity.display_name or "operator"
    if not _check_rate_limit(rate_key, config.metrics_api.rate_limit_per_minute):
        raise HTTPException(status_code=429, detail="Metrics API rate limit exceeded")

    return identity, config


def _emit_metrics_query_receipt(request: Request, receipt_id: str, config) -> None:
    """Emit a metrics lookup receipt when detailed query receipting is enabled."""
    if not config.metrics_api.receipt_queries or _receipt_service is None:
        return

    try:
        from src.shared.receipts import ActionType, Receipt, ReceiptStatus

        identity = resolve_authenticated_identity(request)
        receipt = Receipt(
            action_type=ActionType.METRICS_API_QUERY.value,
            action_name="metrics_receipt_lookup",
            inputs={"receipt_id": receipt_id},
            outputs={"status": "lookup"},
            status=ReceiptStatus.SUCCESS.value,
            operator_id=identity.operator_id,
            session_id=identity.session_id or "",
            metadata={"query_type": "receipt_detail"},
        )
        _receipt_service.create(receipt)
    except Exception as exc:
        logger.warning("Failed to emit metrics query receipt: %s", exc)


def _append_runtime_issue(
    degraded_reasons: List[str],
    runtime_errors: List[str],
    reason: str,
    exc: Exception,
) -> None:
    degraded_reasons.append(reason)
    runtime_errors.append(str(exc))
    logger.warning("%s: %s", reason, exc)


def _get_pending_t3_approvals_count() -> int:
    """Return pending T3 approvals when governance exports a count helper."""
    governance_api = importlib.import_module("src.core.governance_api")
    count_fn = getattr(governance_api, "_get_pending_approvals_count", None)
    if callable(count_fn):
        return int(count_fn())
    return 0


def _get_active_hive_agents_count() -> int:
    """Return the count of active HIVE agents from the live runtime module path."""
    hive_runtime = importlib.import_module("src.hive.runtime")
    get_runtime = getattr(hive_runtime, "get_runtime", None)
    if callable(get_runtime):
        runtime = get_runtime()
        active_agents = getattr(runtime, "active_agents", None)
        if callable(active_agents):
            return len(list(active_agents()))
        if isinstance(active_agents, (list, tuple, set, dict)):
            return len(active_agents)

    # Backward-compatible fallback for the current lifecycle-backed implementation.
    hive_api = importlib.import_module("src.hive.api")
    lifecycle = getattr(hive_api, "_lifecycle", None)
    if lifecycle is None:
        return 0
    runtimes = getattr(lifecycle, "_runtimes", None)
    if isinstance(runtimes, dict):
        return len(runtimes)
    return 0


# ── Endpoints ─────────────────────────────────────────────────────

@router.get("/summary")
async def metrics_summary(request: Request):
    """Current governance health summary.

    Active kill switches, pending T3 approvals, current spend rate,
    active agents, Soul version.
    """
    _authorize_metrics_request(request)
    if _receipt_service is None:
        return JSONResponse(status_code=503, content={"error": "Not initialized"})

    degraded_reasons: List[str] = []
    runtime_errors: List[str] = []

    # Active kill switches
    active_kills = 0
    try:
        from src.core.feature_flags import get_all_flags
        flags = get_all_flags()
        # Count flags that are OFF (kill switch = feature disabled)
        for name, val in flags.items():
            if name.startswith("FEATURE_") and not val:
                active_kills += 1
    except Exception as exc:
        _append_runtime_issue(
            degraded_reasons,
            runtime_errors,
            "Kill switch status unavailable",
            exc,
        )

    # Pending T3 approvals
    pending_t3 = 0
    try:
        pending_t3 = _get_pending_t3_approvals_count()
    except Exception as exc:
        _append_runtime_issue(
            degraded_reasons,
            runtime_errors,
            "Pending approval status unavailable",
            exc,
        )

    # Active HIVE agents
    active_agents = 0
    try:
        active_agents = _get_active_hive_agents_count()
    except Exception as exc:
        _append_runtime_issue(
            degraded_reasons,
            runtime_errors,
            "HIVE runtime status unavailable",
            exc,
        )

    # Cost rate
    cost_rate = 0.0
    try:
        from src.core.control_plane import get_usage_tracker
        tracker = get_usage_tracker()
        if tracker:
            cost_rate = getattr(tracker, 'current_rate_usd_per_hour', 0.0)
    except Exception as exc:
        _append_runtime_issue(
            degraded_reasons,
            runtime_errors,
            "Cost tracker status unavailable",
            exc,
        )

    return _envelope({
        "active_kill_switches": active_kills,
        "pending_t3_approvals": pending_t3,
        "current_spend_rate_usd_hr": cost_rate,
        "active_hive_agents": active_agents,
        "soul_version": _get_soul_version(),
    }, runtime_degraded=bool(degraded_reasons), degraded_reasons=degraded_reasons, runtime_errors=runtime_errors)


@router.get("/receipts")
async def metrics_receipts(
    request: Request,
    start: Optional[str] = Query(None, description="ISO 8601 start"),
    end: Optional[str] = Query(None, description="ISO 8601 end"),
    receipt_type: Optional[str] = Query(None),
    quest_id: Optional[str] = Query(None),
    operator_id: Optional[str] = Query(None),
    risk_tier: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    cursor: Optional[str] = Query(None),
):
    """Paginated receipt query with filters."""
    _authorize_metrics_request(request)
    if _receipt_service is None:
        return JSONResponse(status_code=503, content={"error": "Not initialized"})

    offset = _decode_cursor(cursor)

    rows = _receipt_service.list(
        limit=limit + 1,
        offset=offset,
        action_type=receipt_type,
        quest_id=quest_id,
        operator_id=operator_id,
        risk_tier=risk_tier,
        since=start,
        until=end,
    )
    has_more = len(rows) > limit
    receipts = [_receipt_summary(receipt.to_dict()) for receipt in rows[:limit]]

    next_cursor = _encode_cursor(offset + limit) if has_more else None
    return _envelope({"receipts": receipts, "total": len(receipts)},
                     cursor=next_cursor, has_more=has_more, limit=limit)


@router.get("/receipts/{receipt_id}")
async def metrics_receipt_detail(receipt_id: str, request: Request):
    """Full receipt payload for a single receipt."""
    _identity, config = _authorize_metrics_request(request)
    if _receipt_service is None:
        return JSONResponse(status_code=503, content={"error": "Not initialized"})

    receipt = _receipt_service.get(receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")

    _emit_metrics_query_receipt(request, receipt_id, config)
    return _envelope({"receipt": receipt.to_dict()})


@router.get("/actions")
async def metrics_actions(
    request: Request,
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    group_by: str = Query("risk_tier", description="risk_tier, receipt_type, operator_id, quest_id"),
    interval: str = Query("1h", description="Aggregation interval: 1h, 6h, 1d"),
):
    """Aggregated action counts for charting."""
    _authorize_metrics_request(request)
    if _receipt_service is None:
        return JSONResponse(status_code=503, content={"error": "Not initialized"})

    valid_groups = {"risk_tier": "tier", "receipt_type": "action_type",
                    "operator_id": "operator_id", "quest_id": "quest_id"}
    col = valid_groups.get(group_by, "tier")

    groups = _receipt_service.aggregate_counts(group_by=col, since=start, until=end)

    return _envelope({"group_by": group_by, "groups": groups, "interval": interval})


@router.get("/cost")
async def metrics_cost(
    request: Request,
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    group_by: str = Query("provider", description="provider, model, quest_id"),
):
    """Cost aggregation for custom dashboards."""
    _authorize_metrics_request(request)
    if _receipt_service is None:
        return JSONResponse(status_code=503, content={"error": "Not initialized"})

    # Cost data is in receipt outputs.cost_usd; aggregate from task_executed receipts.
    output_rows = _receipt_service.list_action_outputs(
        action_type="task_executed",
        since=start,
        until=end,
    )
    totals: Dict[str, float] = {}
    for outputs in output_rows:
        try:
            cost = float(outputs.get("cost_usd", 0))
            key = str(outputs.get(group_by, "unknown"))
            totals[key] = totals.get(key, 0) + cost
        except (ValueError, TypeError, json.JSONDecodeError):
            continue

    cost_groups = [{"key": k, "total_usd": round(v, 4)} for k, v in sorted(totals.items(), key=lambda x: -x[1])]
    return _envelope({"group_by": group_by, "cost_groups": cost_groups})


@router.get("/trust-ledger")
async def metrics_trust_ledger(request: Request):
    """Current Trust Ledger state."""
    _authorize_metrics_request(request)
    if _receipt_service is None:
        return JSONResponse(status_code=503, content={"error": "Not initialized"})

    try:
        from src.core.trust_api import _trust_ledger
        if _trust_ledger:
            entries = _trust_ledger.get_all_entries()
            return _envelope({"entries": [e.to_dict() if hasattr(e, 'to_dict') else str(e) for e in entries]})
    except Exception as exc:
        degraded_reasons = ["Trust ledger status unavailable"]
        runtime_errors = [str(exc)]
        logger.warning("Trust ledger status unavailable: %s", exc)
        return _envelope(
            {"entries": []},
            runtime_degraded=True,
            degraded_reasons=degraded_reasons,
            runtime_errors=runtime_errors,
        )
    return _envelope({"entries": []})


@router.get("/soul")
async def metrics_soul(request: Request):
    """Current Soul document summary (not full text)."""
    _authorize_metrics_request(request)
    soul_data: Dict[str, Any] = {"version": "unknown"}
    degraded_reasons: List[str] = []
    runtime_errors: List[str] = []
    try:
        from src.core.soul.store import load_active_soul
        soul = load_active_soul()
        if soul:
            soul_data = {
                "version": _get_soul_version(),
                "name": getattr(soul, "name", ""),
                "capability_count": len(getattr(soul, "capabilities", [])),
                "constraint_count": len(getattr(soul, "constraints", [])),
            }
        else:
            degraded_reasons.append("Soul status unavailable")
    except Exception as exc:
        _append_runtime_issue(
            degraded_reasons,
            runtime_errors,
            "Soul status unavailable",
            exc,
        )
    return _envelope(
        soul_data,
        runtime_degraded=bool(degraded_reasons),
        degraded_reasons=degraded_reasons,
        runtime_errors=runtime_errors,
    )


@router.get("/kill-switches")
async def metrics_kill_switches(request: Request):
    """Current kill switch state with dependency info."""
    _authorize_metrics_request(request)
    try:
        from src.core.feature_flags import get_all_flags
        flags = get_all_flags()
        switches = []
        for name, val in sorted(flags.items()):
            switches.append({
                "name": name,
                "active": val,
                "disabled": not val,
            })
        return _envelope({"switches": switches, "total": len(switches)})
    except Exception as exc:
        logger.warning("Kill switch status unavailable: %s", exc)
        return _envelope(
            {"switches": [], "total": 0},
            runtime_degraded=True,
            degraded_reasons=["Kill switch status unavailable"],
            runtime_errors=[str(exc)],
        )


@router.get("/hive")
async def metrics_hive(request: Request):
    """Current HIVE state — active agents, quests."""
    _authorize_metrics_request(request)
    hive_data: Dict[str, Any] = {"active_agents": 0, "quests": []}
    degraded_reasons: List[str] = []
    runtime_errors: List[str] = []
    try:
        hive_data["active_agents"] = _get_active_hive_agents_count()
    except Exception as exc:
        _append_runtime_issue(
            degraded_reasons,
            runtime_errors,
            "HIVE runtime status unavailable",
            exc,
        )
    return _envelope(
        hive_data,
        runtime_degraded=bool(degraded_reasons),
        degraded_reasons=degraded_reasons,
        runtime_errors=runtime_errors,
    )


@router.get("/webhooks/status")
async def metrics_webhook_status(request: Request):
    """Webhook delivery health per endpoint."""
    _authorize_metrics_request(request)
    try:
        from src.observability.webhook_engine import get_webhook_engine
        engine = get_webhook_engine()
        if engine:
            return _envelope({"endpoints": engine.get_stats()})
        return _envelope(
            {"endpoints": {}},
            runtime_degraded=True,
            degraded_reasons=["Webhook engine not initialized"],
            runtime_errors=[],
        )
    except Exception as exc:
        logger.warning("Webhook engine status unavailable: %s", exc)
        return _envelope(
            {"endpoints": {}},
            runtime_degraded=True,
            degraded_reasons=["Webhook engine status unavailable"],
            runtime_errors=[str(exc)],
        )


# ── Helpers ───────────────────────────────────────────────────────

def _receipt_summary(row: Dict[str, Any]) -> Dict[str, Any]:
    """Summarize a receipt row for the metrics API."""
    return {
        "id": row.get("id", ""),
        "timestamp": row.get("timestamp", ""),
        "action_type": row.get("action_type", ""),
        "action_name": row.get("action_name", ""),
        "status": row.get("status", ""),
        "tier": row.get("tier", 0),
        "duration_ms": row.get("duration_ms"),
        "quest_id": row.get("quest_id"),
        "operator_id": row.get("operator_id"),
    }
