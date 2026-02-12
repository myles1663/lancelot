"""
Trust Ledger API — /api/trust/*

Exposes the TrustLedger for the War Room Trust Ledger page.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/trust", tags=["trust"])

_trust_ledger = None


def init_trust_api(trust_ledger=None) -> None:
    global _trust_ledger
    _trust_ledger = trust_ledger
    logger.info("Trust API initialised (ledger=%s)", _trust_ledger is not None)


def _safe_error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message, "status": status_code})


@router.get("/records")
async def trust_records():
    """All trust records with current tier and success rates."""
    try:
        if _trust_ledger is None:
            return {"records": [], "message": "Trust ledger not initialised"}

        records = _trust_ledger.list_records()
        return {
            "records": [
                {
                    "capability": r.capability,
                    "scope": r.scope,
                    "current_tier": int(r.current_tier),
                    "default_tier": int(r.default_tier),
                    "is_graduated": r.is_graduated,
                    "consecutive_successes": r.consecutive_successes,
                    "total_successes": r.total_successes,
                    "total_failures": r.total_failures,
                    "total_rollbacks": r.total_rollbacks,
                    "success_rate": r.success_rate,
                    "can_graduate": r.can_graduate,
                    "last_success": r.last_success,
                    "last_failure": r.last_failure,
                }
                for r in records
            ],
            "total": len(records),
        }
    except Exception as exc:
        logger.error("trust_records error: %s", exc)
        return _safe_error(500, "Failed to get trust records")


@router.get("/proposals")
async def trust_proposals():
    """Pending graduation proposals."""
    try:
        if _trust_ledger is None:
            return {"proposals": [], "message": "Trust ledger not initialised"}

        proposals = _trust_ledger.pending_proposals()
        return {
            "proposals": [
                {
                    "id": p.id,
                    "capability": p.capability,
                    "scope": p.scope,
                    "current_tier": int(p.current_tier),
                    "proposed_tier": int(p.proposed_tier),
                    "consecutive_successes": p.consecutive_successes,
                    "status": p.status,
                    "created_at": p.created_at,
                }
                for p in proposals
            ],
            "total": len(proposals),
        }
    except Exception as exc:
        logger.error("trust_proposals error: %s", exc)
        return _safe_error(500, "Failed to get trust proposals")


@router.get("/timeline")
async def trust_timeline():
    """Trust progression timeline — graduation events across all records."""
    try:
        if _trust_ledger is None:
            return {"events": [], "message": "Trust ledger not initialised"}

        events = []
        for record in _trust_ledger.list_records():
            for event in record.graduation_history:
                events.append({
                    "capability": record.capability,
                    "scope": record.scope,
                    "timestamp": event.timestamp,
                    "from_tier": int(event.from_tier),
                    "to_tier": int(event.to_tier),
                    "trigger": event.trigger,
                    "owner_approved": event.owner_approved,
                })
        events.sort(key=lambda e: e["timestamp"], reverse=True)
        return {"events": events[:200], "total": len(events)}
    except Exception as exc:
        logger.error("trust_timeline error: %s", exc)
        return _safe_error(500, "Failed to get trust timeline")


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(proposal_id: str, request: Request):
    """Approve a graduation proposal."""
    try:
        if _trust_ledger is None:
            return _safe_error(400, "Trust ledger not initialised")

        data = await request.json() if request.headers.get("content-type") else {}
        reason = data.get("reason", "Approved via War Room")
        _trust_ledger.apply_graduation(proposal_id, approved=True, reason=reason)
        return {"status": "approved", "proposal_id": proposal_id}
    except Exception as exc:
        logger.error("approve_proposal error: %s", exc)
        return _safe_error(500, "Failed to approve proposal")


@router.post("/proposals/{proposal_id}/decline")
async def decline_proposal(proposal_id: str, request: Request):
    """Decline a graduation proposal."""
    try:
        if _trust_ledger is None:
            return _safe_error(400, "Trust ledger not initialised")

        data = await request.json() if request.headers.get("content-type") else {}
        reason = data.get("reason", "Declined via War Room")
        _trust_ledger.apply_graduation(proposal_id, approved=False, reason=reason)
        return {"status": "declined", "proposal_id": proposal_id}
    except Exception as exc:
        logger.error("decline_proposal error: %s", exc)
        return _safe_error(500, "Failed to decline proposal")
