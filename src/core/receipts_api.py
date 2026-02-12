"""
Receipts API — /api/receipts/*

Exposes the ReceiptService for the War Room Receipt Explorer.
Search, filter, and retrieve execution receipts.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/receipts", tags=["receipts"])

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


def _safe_error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message, "status": status_code})


@router.get("")
async def list_receipts(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    action_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    quest_id: Optional[str] = Query(None),
    since: Optional[str] = Query(None),
    until: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Text search across action names and content"),
):
    """List receipts with filters and optional text search."""
    try:
        if _receipt_service is None:
            return {"receipts": [], "total": 0, "message": "Receipt service not initialised"}

        if q:
            results = _receipt_service.search(
                query=q,
                limit=limit,
                action_types=[action_type] if action_type else None,
            )
        else:
            results = _receipt_service.list(
                limit=limit,
                offset=offset,
                action_type=action_type,
                status=status,
                quest_id=quest_id,
                since=since,
                until=until,
            )

        return {
            "receipts": [_receipt_to_dict(r) for r in results],
            "total": len(results),
        }
    except Exception as exc:
        logger.error("list_receipts error: %s", exc)
        return _safe_error(500, "Failed to list receipts")


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
