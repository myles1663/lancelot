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

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from src.core.api_auth import require_authenticated_request
from src.core.auth_api import get_api_key_identity, require_operator_capability, resolve_operator_identity

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/timetravel",
    tags=["time-travel"],
    dependencies=[
        Depends(require_authenticated_request),
        Depends(require_operator_capability("timetravel.admin")),
    ],
)

# Module-level dependencies (injected at startup)
_receipt_service: Any = None
_soul: Any = None
_resume_engine: Any = None
_snapshot_reader: Any = None


def init_timetravel_api(
    receipt_service: Any,
    soul: Any,
    soul_dir: Optional[str] = None,
    quest_executor: Any = None,
    trust_ledger: Any = None,
    data_dir: Optional[str] = None,
) -> None:
    """Initialize the Time-Travel API with required dependencies."""
    global _receipt_service, _soul, _resume_engine, _snapshot_reader

    from src.timetravel.state_snapshot import StateSnapshotReader
    from src.timetravel.resume_engine import ResumeEngine

    _receipt_service = receipt_service
    _soul = soul
    _snapshot_reader = StateSnapshotReader(
        receipt_service,
        soul_dir,
        trust_ledger=trust_ledger,
        data_dir=data_dir,
    )
    _resume_engine = ResumeEngine(
        receipt_service,
        soul,
        _snapshot_reader,
        quest_executor=quest_executor,
        trust_ledger=trust_ledger,
        data_dir=data_dir,
    )

    logger.info("Time-Travel Debugging API initialized")


def shutdown_timetravel_api() -> None:
    """Clear Time-Travel API runtime references for hot-toggle shutdown."""
    global _receipt_service, _soul, _resume_engine, _snapshot_reader
    _receipt_service = None
    _soul = None
    _resume_engine = None
    _snapshot_reader = None
    logger.info("Time-Travel API shutdown complete")


def _get_soul() -> Any:
    """Resolve the live Time-Travel Soul."""
    soul = _soul
    if soul is None:
        return None
    if hasattr(soul, "fork_permissions") or hasattr(soul, "version"):
        return soul
    return soul() if callable(soul) else soul


def update_timetravel_soul(soul: Any) -> None:
    """Refresh the live Soul used by Time-Travel."""
    global _soul
    _soul = soul
    if _resume_engine is not None and hasattr(_resume_engine, "update_soul"):
        _resume_engine.update_soul(soul)


def update_timetravel_executor(quest_executor: Any) -> None:
    """Refresh the execution callback used for replay and fork."""
    if _resume_engine is not None and hasattr(_resume_engine, "update_quest_executor"):
        _resume_engine.update_quest_executor(quest_executor)


# ── Request Models ───────────────────────────────────────────────

class InspectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    receipt_id: str = Field(..., description="Receipt ID to inspect")


class ReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_quest_id: str = Field(..., description="Quest ID to replay")


class ForkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_quest_id: str = Field(..., description="Quest ID to fork")
    modifications: Dict[str, Any] = Field(
        default_factory=dict,
        description="Field path → new value modifications",
    )


# ── Helper ───────────────────────────────────────────────────────

def _extract_identity(request: Request) -> tuple[str, str]:
    """Extract operator_id and session_id from authenticated request identity."""
    identity = resolve_operator_identity(request)
    if identity is None:
        identity = get_api_key_identity(request)
    return identity.operator_id, identity.session_id


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
    degraded_reasons = []
    runtime_errors = []
    soul = None

    if _resume_engine is None:
        degraded_reasons.append("Time-Travel engine not initialized")
    if _snapshot_reader is None:
        degraded_reasons.append("Time-Travel snapshot reader not initialized")
    if _receipt_service is None:
        degraded_reasons.append("Time-Travel receipt service not initialized")
    quest_executor_ready = bool(
        _resume_engine is not None
        and getattr(_resume_engine, "_quest_executor", None) is not None
    )
    if _resume_engine is not None and not quest_executor_ready:
        degraded_reasons.append("Time-Travel quest executor not initialized")

    try:
        soul = _get_soul()
    except Exception as exc:
        logger.error("Failed to resolve Time-Travel Soul: %s", exc)
        degraded_reasons.append("Time-Travel Soul status unavailable")
        runtime_errors.append(f"soul_error: {exc}")

    if soul is None:
        degraded_reasons.append("Time-Travel Soul not loaded")

    return {
        "enabled": _resume_engine is not None,
        "engine_ready": _resume_engine is not None,
        "quest_executor_ready": quest_executor_ready,
        "snapshot_reader_ready": _snapshot_reader is not None,
        "receipt_service_ready": _receipt_service is not None,
        "soul_version": getattr(soul, "version", None) if soul else None,
        "fork_allowed": (
            getattr(soul, "fork_permissions", None) is not None
            and getattr(soul.fork_permissions, "allow_fork", False)
        ) if soul else False,
        "require_approval_tier": (
            getattr(soul.fork_permissions, "require_approval_tier", 3)
        ) if soul and getattr(soul, "fork_permissions", None) else 3,
        "runtime_degraded": bool(degraded_reasons or runtime_errors),
        "degraded_reasons": degraded_reasons,
        "runtime_errors": runtime_errors,
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
            detail="Replay requires authenticated operator identity",
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
            detail="Fork requires authenticated operator identity",
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
