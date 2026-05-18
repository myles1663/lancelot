"""
Receipts API — /api/receipts/*

Exposes the ReceiptService for the War Room Receipt Explorer.
Search, filter, and retrieve execution receipts.
"""

import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from src.core.api_auth import require_authenticated_request

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/receipts",
    tags=["receipts"],
    dependencies=[Depends(require_authenticated_request)],
)

_receipt_service = None


def init_receipts_api(data_dir: str) -> None:
    """Initialise the receipts API with a data directory."""
    global _receipt_service
    try:
        from receipts import get_receipt_service
        _receipt_service = get_receipt_service(data_dir)
        logger.info("Receipts API initialised")
    except Exception as exc:
        logger.warning("Receipts API init failed: %s", exc)


def get_receipt_service_instance():
    """Return the initialized receipt service, or None before API startup."""
    return _receipt_service


def _safe_error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message, "status": status_code})


@router.get("")
async def list_receipts(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    action_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    quest_id: Optional[str] = Query(None),
    risk_tier: Optional[int] = Query(None, ge=0, le=3),
    tier: Optional[str] = Query(None, description="Alias for risk_tier; accepts 0-3 or T0-T3"),
    since: Optional[str] = Query(None),
    until: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Text search across action names and content"),
):
    """List receipts with filters and optional text search."""
    try:
        if _receipt_service is None:
            return {"receipts": [], "total": 0, "message": "Receipt service not initialised"}

        effective_risk_tier = _resolve_risk_tier(risk_tier, tier)
        search_query, inferred_risk_tier = _normalize_text_query(q)
        if effective_risk_tier is None:
            effective_risk_tier = inferred_risk_tier

        if search_query:
            results = _receipt_service.search(
                query=search_query,
                limit=limit,
                offset=offset,
                action_types=[action_type] if action_type else None,
                status=status,
                quest_id=quest_id,
                risk_tier=effective_risk_tier,
                since=since,
                until=until,
            )
            total = _receipt_service.count_search(
                query=search_query,
                action_types=[action_type] if action_type else None,
                status=status,
                quest_id=quest_id,
                risk_tier=effective_risk_tier,
                since=since,
                until=until,
            )
        else:
            results = _receipt_service.list(
                limit=limit,
                offset=offset,
                action_type=action_type,
                status=status,
                quest_id=quest_id,
                risk_tier=effective_risk_tier,
                since=since,
                until=until,
            )
            total = _receipt_service.count(
                action_type=action_type,
                status=status,
                quest_id=quest_id,
                risk_tier=effective_risk_tier,
                since=since,
                until=until,
            )

        return {
            "receipts": [_receipt_to_dict(r) for r in results],
            "total": total,
        }
    except Exception as exc:
        logger.error("list_receipts error: %s", exc)
        return _safe_error(500, "Failed to list receipts")


def _parse_tier_value(value: str) -> Optional[int]:
    cleaned = value.strip().lower()
    if not cleaned:
        return None

    exact = re.fullmatch(r"t([0-3])", cleaned)
    if exact:
        return int(exact.group(1))

    named = re.fullmatch(r"(?:risk[\s_-]*)?tier\s*[:\s-]*([0-3])", cleaned)
    if named:
        return int(named.group(1))

    if cleaned in {"0", "1", "2", "3"}:
        return int(cleaned)

    return None


def _resolve_risk_tier(risk_tier: Optional[int], tier: Optional[str]) -> Optional[int]:
    if risk_tier is not None:
        return risk_tier
    if tier is None:
        return None
    return _parse_tier_value(tier)


def _normalize_text_query(q: Optional[str]) -> tuple[Optional[str], Optional[int]]:
    if q is None:
        return None, None

    stripped = q.strip()
    if not stripped:
        return None, None

    inferred_tier = _parse_tier_value(stripped)
    if inferred_tier is not None and not stripped.isdigit():
        return None, inferred_tier

    return stripped, None


@router.get("/stats")
async def receipt_stats(
    since: Optional[str] = Query(None),
    quest_id: Optional[str] = Query(None),
):
    """Aggregate receipt statistics."""
    try:
        if _receipt_service is None:
            return {"stats": {}, "message": "Receipt service not initialised"}

        stats = _receipt_service.get_stats(since=since, quest_id=quest_id)
        return {"stats": stats}
    except Exception as exc:
        logger.error("receipt_stats error: %s", exc)
        return _safe_error(500, "Failed to get receipt stats")


@router.get("/{receipt_id}/context")
async def get_receipt_context(receipt_id: str):
    """Get a receipt's relational context: children, parent summary, quest sibling count.

    Called by the Receipt Explorer when a row is expanded to provide
    context about what the receipt is connected to.
    """
    try:
        if _receipt_service is None:
            return _safe_error(400, "Receipt service not initialised")

        receipt = _receipt_service.get(receipt_id)
        if receipt is None:
            return _safe_error(404, f"Receipt {receipt_id} not found")

        # Children
        children = _receipt_service.get_children(receipt_id)

        # Parent summary
        parent_summary = None
        if receipt.parent_id:
            parent = _receipt_service.get(receipt.parent_id)
            if parent:
                parent_summary = {
                    "id": parent.id,
                    "action_name": parent.action_name,
                    "action_type": parent.action_type,
                    "status": parent.status,
                }

        # Quest sibling count
        quest_receipts_count = None
        if receipt.quest_id:
            quest_siblings = _receipt_service.get_quest_receipts(receipt.quest_id)
            quest_receipts_count = len(quest_siblings)

        return {
            "children": [_receipt_to_dict(c) for c in children],
            "quest_receipts_count": quest_receipts_count,
            "parent": parent_summary,
        }
    except Exception as exc:
        logger.error("get_receipt_context error: %s", exc)
        return _safe_error(500, "Failed to get receipt context")


@router.get("/{receipt_id}")
async def get_receipt(receipt_id: str):
    """Get a single receipt by ID."""
    try:
        if _receipt_service is None:
            return _safe_error(400, "Receipt service not initialised")

        receipt = _receipt_service.get(receipt_id)
        if receipt is None:
            return _safe_error(404, f"Receipt {receipt_id} not found")

        return {"receipt": _receipt_to_dict(receipt)}
    except Exception as exc:
        logger.error("get_receipt error: %s", exc)
        return _safe_error(500, "Failed to get receipt")


def _receipt_to_dict(r) -> dict:
    """Convert a Receipt dataclass to a JSON-safe dict."""
    return {
        "id": r.id,
        "timestamp": r.timestamp,
        "action_type": r.action_type,
        "action_name": r.action_name,
        "inputs": r.inputs,
        "outputs": r.outputs,
        "status": r.status,
        "duration_ms": r.duration_ms,
        "token_count": r.token_count,
        "tier": r.tier,
        "parent_id": r.parent_id,
        "quest_id": r.quest_id,
        "error_message": r.error_message,
        "metadata": r.metadata,
    }
