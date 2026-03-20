# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Time-Travel Debugging API — REST endpoints for inspect, replay, and fork.

Endpoints:
    GET  /api/timetravel/quest/{quest_id}/receipts — quest receipt chain
    GET  /api/timetravel/receipt/{receipt_id}/snapshot — state snapshot
    POST /api/timetravel/inspect — create inspection
    POST /api/timetravel/replay — create replay
    POST /api/timetravel/fork — create fork
    GET  /api/timetravel/status — subsystem status

All endpoints require FEATURE_TIME_TRAVEL to be enabled.
Fork and replay require operator identity.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/timetravel", tags=["time-travel"])

# Module-level dependencies (injected at startup)
_receipt_service: Any = None
_soul: Any = None
_resume_engine: Any = None
_snapshot_reader: Any = None


def init_timetravel_api(
    receipt_service: Any,
    soul: Any,
    soul_dir: Optional[str] = None,
) -> None:
    """Initialize the Time-Travel API with required dependencies."""
    global _receipt_service, _soul, _resume_engine, _snapshot_reader

    from src.timetravel.state_snapshot import StateSnapshotReader
    from src.timetravel.resume_engine import ResumeEngine

    _receipt_service = receipt_service
    _soul = soul
    _snapshot_reader = StateSnapshotReader(receipt_service, soul_dir)
    _resume_engine = ResumeEngine(receipt_service, soul, _snapshot_reader)

    logger.info("Time-Travel Debugging API initialized")


# ── Request Models ───────────────────────────────────────────────

class InspectRequest(BaseModel):
    receipt_id: str = Field(..., description="Receipt ID to inspect")


class ReplayRequest(BaseModel):
    source_quest_id: str = Field(..., description="Quest ID to replay")


class ForkRequest(BaseModel):
    source_quest_id: str = Field(..., description="Quest ID to fork")
    modifications: Dict[str, Any] = Field(
        default_factory=dict,
        description="Field path → new value modifications",
    )


# ── Helper ───────────────────────────────────────────────────────

def _extract_identity(request: Request) -> tuple:
    """Extract operator_id and session_id from request headers."""
    operator_id = request.headers.get("X-Operator-ID")
    session_id = request.headers.get("X-Session-ID")
    return operator_id, session_id


def _require_engine() -> None:
    """Raise 503 if the engine isn't initialized."""
    if _resume_engine is None:
        raise HTTPException(
            status_code=503,
            detail="Time-Travel subsystem not initialized",
        )


# ── Endpoints ────────────────────────────────────────────────────

@router.get("/status")
async def get_status():
    """Get Time-Travel subsystem status."""
    return {
        "enabled": _resume_engine is not None,
        "soul_version": getattr(_soul, "version", None) if _soul else None,
        "fork_allowed": (
            getattr(_soul, "fork_permissions", None) is not None
            and getattr(_soul.fork_permissions, "allow_fork", False)
        ) if _soul else False,
        "require_approval_tier": (
            getattr(_soul.fork_permissions, "require_approval_tier", 3)
        ) if _soul and getattr(_soul, "fork_permissions", None) else 3,
    }


@router.get("/quest/{quest_id}/receipts")
async def get_quest_receipts(quest_id: str):
    """Get the full receipt chain for a quest."""
    _require_engine()

    receipts = _receipt_service.get_quest_receipts(quest_id)
    if not receipts:
        raise HTTPException(status_code=404, detail=f"Quest not found: {quest_id}")

    return {
        "quest_id": quest_id,
        "receipt_count": len(receipts),
        "receipts": [r.to_dict() for r in receipts],
    }


@router.get("/receipt/{receipt_id}/snapshot")
async def get_receipt_snapshot(receipt_id: str):
    """Get the governance state snapshot at a specific receipt."""
    _require_engine()

    if _snapshot_reader is None:
        raise HTTPException(status_code=503, detail="Snapshot reader not available")

    try:
        snapshot = _snapshot_reader.read_snapshot(receipt_id)
        return snapshot.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Snapshot read failed: %s", e)
        raise HTTPException(status_code=500, detail="Snapshot generation failed")


@router.post("/inspect")
async def create_inspection(body: InspectRequest, request: Request):
    """Create a read-only inspection of a receipt's governance state."""
    _require_engine()

    operator_id, session_id = _extract_identity(request)
    result = _resume_engine.create_inspection(
        receipt_id=body.receipt_id,
        operator_id=operator_id,
        session_id=session_id,
    )

    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)

    return result.to_dict()


@router.post("/replay")
async def create_replay(body: ReplayRequest, request: Request):
    """Create a replay of an existing quest under the current Soul."""
    _require_engine()

    operator_id, session_id = _extract_identity(request)
    if not operator_id:
        raise HTTPException(
            status_code=401,
            detail="Replay requires operator identity (X-Operator-ID header)",
        )

    result = _resume_engine.create_replay(
        source_quest_id=body.source_quest_id,
        operator_id=operator_id,
        session_id=session_id,
    )

    if not result.success:
        status = 403 if result.approval_status == "rejected" else 400
        raise HTTPException(status_code=status, detail=result.error)

    return result.to_dict()


@router.post("/fork")
async def create_fork(body: ForkRequest, request: Request):
    """Create a fork of an existing quest with modifications."""
    _require_engine()

    operator_id, session_id = _extract_identity(request)
    if not operator_id:
        raise HTTPException(
            status_code=401,
            detail="Fork requires operator identity (X-Operator-ID header)",
        )

    result = _resume_engine.create_fork(
        source_quest_id=body.source_quest_id,
        modifications=body.modifications,
        operator_id=operator_id,
        session_id=session_id,
    )

    if not result.success:
        status = 403 if result.approval_status == "rejected" else 400
        raise HTTPException(status_code=status, detail=result.error)

    return result.to_dict()
