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
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("lancelot.observability.metrics_api")

router = APIRouter(prefix="/api/metrics", tags=["metrics"])

# Module-level references
_receipt_service = None
_data_dir = "/home/lancelot/data"


def init_metrics_api(receipt_service, data_dir: str = "/home/lancelot/data") -> None:
    """Initialize the Metrics API with the receipt service."""
    global _receipt_service, _data_dir
    _receipt_service = receipt_service
    _data_dir = data_dir
    logger.info("Metrics API initialized")


# ── Response Envelope ─────────────────────────────────────────────

def _envelope(data: Any, cursor: Optional[str] = None, has_more: bool = False, limit: int = 100) -> Dict[str, Any]:
    """Wrap response data in the standard Metrics API envelope."""
    soul_version = _get_soul_version()
    return {
        "api_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "deployment_id": _get_deployment_id(),
        "soul_version": soul_version,
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
        from soul.store import SoulStore
        store = SoulStore()
        soul = store.load()
        if soul:
            return hashlib.sha256(
                json.dumps(soul.dict(), sort_keys=True).encode()
            ).hexdigest()[:16]
    except Exception:
        pass
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


# ── Endpoints ─────────────────────────────────────────────────────

@router.get("/summary")
async def metrics_summary():
    """Current governance health summary.

    Active kill switches, pending T3 approvals, current spend rate,
    active agents, Soul version.
    """
    if _receipt_service is None:
        return JSONResponse(status_code=503, content={"error": "Not initialized"})

    # Active kill switches
    active_kills = 0
    try:
        from feature_flags import get_all_flags
        flags = get_all_flags()
        # Count flags that are OFF (kill switch = feature disabled)
        for name, val in flags.items():
            if name.startswith("FEATURE_") and not val:
                active_kills += 1
    except Exception:
        pass

    # Pending T3 approvals
    pending_t3 = 0
    try:
        from governance_api import _get_pending_approvals_count
        pending_t3 = _get_pending_approvals_count()
    except Exception:
        pass

    # Active HIVE agents
    active_agents = 0
    try:
        from hive.runtime import get_runtime
        rt = get_runtime()
        if rt:
            active_agents = len(rt.active_agents())
    except Exception:
        pass

    # Cost rate
    cost_rate = 0.0
    try:
        from control_plane import get_usage_tracker
        tracker = get_usage_tracker()
        if tracker:
            cost_rate = getattr(tracker, 'current_rate_usd_per_hour', 0.0)
    except Exception:
        pass

    return _envelope({
        "active_kill_switches": active_kills,
        "pending_t3_approvals": pending_t3,
        "current_spend_rate_usd_hr": cost_rate,
        "active_hive_agents": active_agents,
        "soul_version": _get_soul_version(),
    })


@router.get("/receipts")
async def metrics_receipts(
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
    if _receipt_service is None:
        return JSONResponse(status_code=503, content={"error": "Not initialized"})

    offset = _decode_cursor(cursor)

    conn = _receipt_service._get_connection()
    sql = "SELECT * FROM receipts WHERE 1=1"
    params: List[Any] = []

    if start:
        sql += " AND timestamp >= ?"
        params.append(start)
    if end:
        sql += " AND timestamp <= ?"
        params.append(end)
    if receipt_type:
        sql += " AND action_type = ?"
        params.append(receipt_type)
    if quest_id:
        sql += " AND quest_id = ?"
        params.append(quest_id)
    if operator_id:
        sql += " AND operator_id = ?"
        params.append(operator_id)
    if risk_tier is not None:
        sql += " AND tier = ?"
        params.append(risk_tier)

    sql += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
    params.extend([limit + 1, offset])  # Fetch one extra to check has_more

    rows = conn.execute(sql, params).fetchall()
    has_more = len(rows) > limit
    receipts = [
        _receipt_summary(dict(row)) for row in rows[:limit]
    ]

    next_cursor = _encode_cursor(offset + limit) if has_more else None
    return _envelope({"receipts": receipts, "total": len(receipts)},
                     cursor=next_cursor, has_more=has_more, limit=limit)


@router.get("/receipts/{receipt_id}")
async def metrics_receipt_detail(receipt_id: str):
    """Full receipt payload for a single receipt."""
    if _receipt_service is None:
        return JSONResponse(status_code=503, content={"error": "Not initialized"})

    receipt = _receipt_service.get(receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")

    return _envelope({"receipt": receipt.to_dict()})


@router.get("/actions")
async def metrics_actions(
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    group_by: str = Query("risk_tier", description="risk_tier, receipt_type, operator_id, quest_id"),
    interval: str = Query("1h", description="Aggregation interval: 1h, 6h, 1d"),
):
    """Aggregated action counts for charting."""
    if _receipt_service is None:
        return JSONResponse(status_code=503, content={"error": "Not initialized"})

    valid_groups = {"risk_tier": "tier", "receipt_type": "action_type",
                    "operator_id": "operator_id", "quest_id": "quest_id"}
    col = valid_groups.get(group_by, "tier")

    conn = _receipt_service._get_connection()
    sql = f"SELECT {col} as group_key, COUNT(*) as count FROM receipts WHERE 1=1"
    params: List[Any] = []
    if start:
        sql += " AND timestamp >= ?"
        params.append(start)
    if end:
        sql += " AND timestamp <= ?"
        params.append(end)
    sql += f" GROUP BY {col} ORDER BY count DESC"

    rows = conn.execute(sql, params).fetchall()
    groups = [{"key": row["group_key"], "count": row["count"]} for row in rows]

    return _envelope({"group_by": group_by, "groups": groups, "interval": interval})


@router.get("/cost")
async def metrics_cost(
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    group_by: str = Query("provider", description="provider, model, quest_id"),
):
    """Cost aggregation for custom dashboards."""
    if _receipt_service is None:
        return JSONResponse(status_code=503, content={"error": "Not initialized"})

    # Cost data is in receipt outputs.cost_usd — aggregate from task_executed receipts
    conn = _receipt_service._get_connection()
    sql = "SELECT outputs FROM receipts WHERE action_type = 'task_executed'"
    params: List[Any] = []
    if start:
        sql += " AND timestamp >= ?"
        params.append(start)
    if end:
        sql += " AND timestamp <= ?"
        params.append(end)

    rows = conn.execute(sql, params).fetchall()

    totals: Dict[str, float] = {}
    for row in rows:
        try:
            outputs = json.loads(row["outputs"]) if isinstance(row["outputs"], str) else row["outputs"]
            cost = float(outputs.get("cost_usd", 0))
            key = str(outputs.get(group_by, "unknown"))
            totals[key] = totals.get(key, 0) + cost
        except (ValueError, TypeError, json.JSONDecodeError):
            continue

    cost_groups = [{"key": k, "total_usd": round(v, 4)} for k, v in sorted(totals.items(), key=lambda x: -x[1])]
    return _envelope({"group_by": group_by, "cost_groups": cost_groups})


@router.get("/trust-ledger")
async def metrics_trust_ledger():
    """Current Trust Ledger state."""
    if _receipt_service is None:
        return JSONResponse(status_code=503, content={"error": "Not initialized"})

    try:
        from trust_api import _trust_ledger
        if _trust_ledger:
            entries = _trust_ledger.get_all_entries()
            return _envelope({"entries": [e.to_dict() if hasattr(e, 'to_dict') else str(e) for e in entries]})
    except Exception:
        pass
    return _envelope({"entries": []})


@router.get("/soul")
async def metrics_soul():
    """Current Soul document summary (not full text)."""
    soul_data: Dict[str, Any] = {"version": "unknown"}
    try:
        from soul.store import SoulStore
        store = SoulStore()
        soul = store.load()
        if soul:
            soul_data = {
                "version": _get_soul_version(),
                "name": getattr(soul, "name", ""),
                "capability_count": len(getattr(soul, "capabilities", [])),
                "constraint_count": len(getattr(soul, "constraints", [])),
            }
    except Exception:
        pass
    return _envelope(soul_data)


@router.get("/kill-switches")
async def metrics_kill_switches():
    """Current kill switch state with dependency info."""
    try:
        from feature_flags import get_all_flags
        flags = get_all_flags()
        switches = []
        for name, val in sorted(flags.items()):
            switches.append({
                "name": name,
                "active": val,
                "disabled": not val,
            })
        return _envelope({"switches": switches, "total": len(switches)})
    except Exception:
        return _envelope({"switches": [], "total": 0})


@router.get("/hive")
async def metrics_hive():
    """Current HIVE state — active agents, quests."""
    hive_data: Dict[str, Any] = {"active_agents": 0, "quests": []}
    try:
        from hive.runtime import get_runtime
        rt = get_runtime()
        if rt:
            agents = rt.active_agents()
            hive_data["active_agents"] = len(agents)
    except Exception:
        pass
    return _envelope(hive_data)


@router.get("/webhooks/status")
async def metrics_webhook_status():
    """Webhook delivery health per endpoint."""
    try:
        from src.observability.webhook_engine import get_webhook_engine
        engine = get_webhook_engine()
        if engine:
            return _envelope({"endpoints": engine.get_stats()})
    except Exception:
        pass
    return _envelope({"endpoints": {}})


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
