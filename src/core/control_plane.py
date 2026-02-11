"""
Control-Plane API Endpoints (v4 Upgrade — Prompts 6 & 15 + Fix Pack V1)

Provides /system/status, /onboarding/*, /router/*, /warroom/*, and /tokens/*
endpoints for the War Room and any other control surface.
Mounted as a FastAPI APIRouter.

All responses use safe error handling — no stack traces leak to clients.
"""
import time
import logging
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.core.onboarding_snapshot import OnboardingSnapshot, OnboardingState
from src.core import recovery_commands

logger = logging.getLogger(__name__)

router = APIRouter()

# Snapshot instance — set by init_control_plane() at startup
_snapshot: Optional[OnboardingSnapshot] = None
_startup_time: Optional[float] = None
_model_router = None  # Set by set_model_router()
_usage_tracker = None  # Set by set_usage_tracker() — standalone tracker
_usage_persistence = None  # Set by set_usage_persistence()

_token_store = None  # Set by init_control_plane if available
_war_room_artifacts = []  # In-memory store for War Room artifacts


def init_control_plane(data_dir: str) -> None:
    """Initialise the control-plane with a data directory.

    Called once at app startup.
    """
    global _snapshot, _startup_time, _token_store
    _snapshot = OnboardingSnapshot(data_dir)
    _startup_time = time.time()

    # Fix Pack V1: Try to initialize token store for War Room endpoints
    try:
        from pathlib import Path
        from src.core.execution_authority.store import ExecutionTokenStore
        _token_store = ExecutionTokenStore(Path(data_dir) / "tokens.db")
        logger.info("Control-plane: ExecutionTokenStore initialised for /tokens/* endpoints")
    except Exception as exc:
        logger.warning("Control-plane: ExecutionTokenStore not available: %s", exc)
        _token_store = None


def store_war_room_artifact(artifact_data: dict) -> None:
    """Store a War Room artifact (called from orchestrator/assembler)."""
    _war_room_artifacts.append(artifact_data)


def set_model_router(model_router) -> None:
    """Register the ModelRouter instance for War Room endpoints."""
    global _model_router
    _model_router = model_router


def get_model_router():
    """Return the active ModelRouter (or None if not set)."""
    return _model_router


def set_usage_tracker(tracker) -> None:
    """Register a standalone UsageTracker for War Room endpoints."""
    global _usage_tracker
    _usage_tracker = tracker


def get_usage_tracker():
    """Return the active UsageTracker (standalone or from model router)."""
    if _usage_tracker is not None:
        return _usage_tracker
    if _model_router is not None:
        return getattr(_model_router, "usage", None)
    return None


def set_usage_persistence(persistence) -> None:
    """Register the UsagePersistence for monthly endpoints."""
    global _usage_persistence
    _usage_persistence = persistence


def get_snapshot() -> OnboardingSnapshot:
    """Return the active snapshot (raises if not initialised)."""
    if _snapshot is None:
        raise RuntimeError("Control-plane not initialised — call init_control_plane()")
    return _snapshot


def _safe_error(status_code: int, message: str) -> JSONResponse:
    """Return a structured error response with no internal details."""
    return JSONResponse(
        status_code=status_code,
        content={"error": message, "status": status_code},
    )


# ------------------------------------------------------------------
# /system/status
# ------------------------------------------------------------------

@router.get("/system/status")
async def system_status():
    """Full system provisioning status for the War Room."""
    try:
        snap = get_snapshot()
        uptime = round(time.time() - _startup_time, 1) if _startup_time else 0

        return {
            "onboarding": {
                "state": snap.state.value,
                "flagship_provider": snap.flagship_provider,
                "credential_status": snap.credential_status,
                "local_model_status": snap.local_model_status,
                "is_ready": snap.is_ready,
            },
            "cooldown": {
                "active": snap.is_in_cooldown(),
                "remaining_seconds": round(snap.cooldown_remaining(), 1),
                "reason": snap.last_error if snap.state == OnboardingState.COOLDOWN else None,
            },
            "uptime_seconds": uptime,
        }
    except Exception as exc:
        logger.error("system_status error: %s", exc)
        return _safe_error(500, "Failed to retrieve system status")


# ------------------------------------------------------------------
# /onboarding/*
# ------------------------------------------------------------------

@router.get("/onboarding/status")
async def onboarding_status():
    """Detailed onboarding snapshot for the War Room recovery panel."""
    try:
        snap = get_snapshot()
        return {
            "state": snap.state.value,
            "flagship_provider": snap.flagship_provider,
            "credential_status": snap.credential_status,
            "local_model_status": snap.local_model_status,
            "is_ready": snap.is_ready,
            "cooldown_active": snap.is_in_cooldown(),
            "cooldown_remaining": round(snap.cooldown_remaining(), 1),
            "last_error": snap.last_error,
            "resend_count": snap.resend_count,
            "updated_at": snap.updated_at,
        }
    except Exception as exc:
        logger.error("onboarding_status error: %s", exc)
        return _safe_error(500, "Failed to retrieve onboarding status")


@router.post("/onboarding/command")
async def onboarding_command(request: Request):
    """Execute a recovery command (STATUS, BACK, RESTART STEP, RESEND CODE, RESET ONBOARDING).

    Payload: ``{"command": "STATUS"}``
    """
    try:
        data = await request.json()
        command = data.get("command", "").strip()

        if not command:
            return _safe_error(400, "Missing 'command' field")

        snap = get_snapshot()
        result = recovery_commands.try_handle(command, snap)

        if result is None:
            return _safe_error(400, f"Unknown command: {command}")

        return {
            "command": command,
            "response": result,
            "state": snap.state.value,
        }
    except Exception as exc:
        logger.error("onboarding_command error: %s", exc)
        return _safe_error(500, "Failed to execute command")


@router.post("/onboarding/back")
async def onboarding_back():
    """Shortcut: execute BACK command."""
    try:
        snap = get_snapshot()
        result = recovery_commands.try_handle("back", snap)
        return {"response": result, "state": snap.state.value}
    except Exception as exc:
        logger.error("onboarding_back error: %s", exc)
        return _safe_error(500, "Failed to execute BACK")


@router.post("/onboarding/restart-step")
async def onboarding_restart_step():
    """Shortcut: execute RESTART STEP command."""
    try:
        snap = get_snapshot()
        result = recovery_commands.try_handle("restart step", snap)
        return {"response": result, "state": snap.state.value}
    except Exception as exc:
        logger.error("onboarding_restart_step error: %s", exc)
        return _safe_error(500, "Failed to execute RESTART STEP")


@router.post("/onboarding/resend-code")
async def onboarding_resend_code():
    """Shortcut: execute RESEND CODE command."""
    try:
        snap = get_snapshot()
        result = recovery_commands.try_handle("resend code", snap)
        return {"response": result, "state": snap.state.value}
    except Exception as exc:
        logger.error("onboarding_resend_code error: %s", exc)
        return _safe_error(500, "Failed to execute RESEND CODE")


@router.post("/onboarding/reset")
async def onboarding_reset():
    """Shortcut: execute RESET ONBOARDING command."""
    try:
        snap = get_snapshot()
        result = recovery_commands.try_handle("reset onboarding", snap)
        return {"response": result, "state": snap.state.value}
    except Exception as exc:
        logger.error("onboarding_reset error: %s", exc)
        return _safe_error(500, "Failed to execute RESET ONBOARDING")


# ------------------------------------------------------------------
# /router/* — Model Router War Room panel (Prompt 15)
# ------------------------------------------------------------------

@router.get("/router/decisions")
async def router_decisions():
    """Recent routing decisions for the War Room Model Router panel."""
    try:
        mr = get_model_router()
        if mr is None:
            return {"decisions": [], "message": "Model router not initialised"}
        decisions = mr.recent_decisions
        return {
            "decisions": [d.to_dict() for d in decisions[:50]],
            "total": len(decisions),
        }
    except Exception as exc:
        logger.error("router_decisions error: %s", exc)
        return _safe_error(500, "Failed to retrieve router decisions")


@router.get("/router/stats")
async def router_stats():
    """Routing statistics for the War Room."""
    try:
        mr = get_model_router()
        if mr is None:
            return {"stats": {}, "message": "Model router not initialised"}
        return {"stats": mr.stats}
    except Exception as exc:
        logger.error("router_stats error: %s", exc)
        return _safe_error(500, "Failed to retrieve router stats")


# ------------------------------------------------------------------
# /usage/* — Usage & Cost Telemetry panel (Prompt 17)
# ------------------------------------------------------------------

@router.get("/usage/summary")
async def usage_summary():
    """Full usage and cost summary for the War Room cost panel."""
    try:
        tracker = get_usage_tracker()
        if tracker is None:
            return {"usage": {}, "message": "Usage tracker not initialised"}
        return {"usage": tracker.summary()}
    except Exception as exc:
        logger.error("usage_summary error: %s", exc)
        return _safe_error(500, "Failed to retrieve usage summary")


@router.get("/usage/lanes")
async def usage_lanes():
    """Per-lane usage breakdown."""
    try:
        tracker = get_usage_tracker()
        if tracker is None:
            return {"lanes": {}, "message": "Usage tracker not initialised"}
        return {"lanes": tracker.lane_breakdown()}
    except Exception as exc:
        logger.error("usage_lanes error: %s", exc)
        return _safe_error(500, "Failed to retrieve lane usage")


@router.get("/usage/models")
async def usage_models():
    """Per-model usage breakdown."""
    try:
        tracker = get_usage_tracker()
        if tracker is None:
            return {"models": {}, "message": "Usage tracker not initialised"}
        return {"models": tracker.model_breakdown()}
    except Exception as exc:
        logger.error("usage_models error: %s", exc)
        return _safe_error(500, "Failed to retrieve model usage")


@router.get("/usage/savings")
async def usage_savings():
    """Estimated savings from local model usage."""
    try:
        tracker = get_usage_tracker()
        if tracker is None:
            return {"savings": {}, "message": "Usage tracker not initialised"}
        return {"savings": tracker.estimated_savings()}
    except Exception as exc:
        logger.error("usage_savings error: %s", exc)
        return _safe_error(500, "Failed to retrieve savings data")


@router.get("/usage/monthly")
async def usage_monthly(month: str = ""):
    """Monthly usage data from persistence (survives restarts).

    Query params:
        month: Optional month key (e.g. ``2026-02``). Defaults to current.
    """
    try:
        if _usage_persistence is None:
            return {"monthly": {}, "message": "Usage persistence not initialised"}
        if month:
            data = _usage_persistence.get_month(month)
        else:
            data = _usage_persistence.get_current_month()
        return {
            "monthly": data,
            "available_months": _usage_persistence.get_available_months(),
        }
    except Exception as exc:
        logger.error("usage_monthly error: %s", exc)
        return _safe_error(500, "Failed to retrieve monthly usage")


@router.post("/usage/reset")
async def usage_reset():
    """Reset in-memory usage counters (starts a new tracking period)."""
    try:
        tracker = get_usage_tracker()
        if tracker is None:
            return _safe_error(400, "Usage tracker not initialised")
        tracker.reset()
        return {"message": "Usage counters reset", "usage": tracker.summary()}
    except Exception as exc:
        logger.error("usage_reset error: %s", exc)
        return _safe_error(500, "Failed to reset usage counters")


# ------------------------------------------------------------------
# /warroom/* — War Room Artifact endpoints (Fix Pack V1 PR1)
# ------------------------------------------------------------------

@router.post("/warroom/artifacts")
async def warroom_store_artifact(request: Request):
    """Persist a War Room artifact."""
    try:
        data = await request.json()
        if not data:
            return _safe_error(400, "Missing artifact data")
        store_war_room_artifact(data)
        return {"status": "stored", "artifact_count": len(_war_room_artifacts)}
    except Exception as exc:
        logger.error("warroom_store_artifact error: %s", exc)
        return _safe_error(500, "Failed to store artifact")


@router.get("/warroom/artifacts")
async def warroom_list_artifacts(session_id: str = ""):
    """List War Room artifacts, optionally filtered by session."""
    try:
        if session_id:
            filtered = [a for a in _war_room_artifacts
                        if a.get("session_id") == session_id]
        else:
            filtered = _war_room_artifacts
        return {"artifacts": filtered[-50:], "total": len(filtered)}
    except Exception as exc:
        logger.error("warroom_list_artifacts error: %s", exc)
        return _safe_error(500, "Failed to list artifacts")


@router.get("/warroom/artifacts/{artifact_id}")
async def warroom_get_artifact(artifact_id: str):
    """Get a single War Room artifact by ID."""
    try:
        for art in _war_room_artifacts:
            if art.get("id") == artifact_id:
                return {"artifact": art}
        return _safe_error(404, f"Artifact {artifact_id} not found")
    except Exception as exc:
        logger.error("warroom_get_artifact error: %s", exc)
        return _safe_error(500, "Failed to retrieve artifact")


# ------------------------------------------------------------------
# /tokens/* — ExecutionToken endpoints (Fix Pack V1 PR3)
# ------------------------------------------------------------------

@router.get("/tokens")
async def tokens_list(status: str = "", limit: int = 50):
    """List ExecutionTokens."""
    try:
        if _token_store is None:
            return {"tokens": [], "message": "Token store not initialised"}
        tokens = _token_store.list_tokens(limit=limit, status=status or None)
        return {"tokens": [t.to_dict() for t in tokens], "total": len(tokens)}
    except Exception as exc:
        logger.error("tokens_list error: %s", exc)
        return _safe_error(500, "Failed to list tokens")


@router.get("/tokens/{token_id}")
async def tokens_get(token_id: str):
    """Get a single ExecutionToken by ID."""
    try:
        if _token_store is None:
            return _safe_error(400, "Token store not initialised")
        token = _token_store.get(token_id)
        if token is None:
            return _safe_error(404, f"Token {token_id} not found")
        return {"token": token.to_dict()}
    except Exception as exc:
        logger.error("tokens_get error: %s", exc)
        return _safe_error(500, "Failed to retrieve token")


@router.post("/tokens/{token_id}/revoke")
async def tokens_revoke(token_id: str, request: Request):
    """Revoke an ExecutionToken."""
    try:
        if _token_store is None:
            return _safe_error(400, "Token store not initialised")
        data = await request.json()
        reason = data.get("reason", "Manual revocation via control plane")
        success = _token_store.revoke(token_id, reason)
        if success:
            return {"status": "revoked", "token_id": token_id, "reason": reason}
        return _safe_error(400, f"Token {token_id} could not be revoked (already revoked or not found)")
    except Exception as exc:
        logger.error("tokens_revoke error: %s", exc)
        return _safe_error(500, "Failed to revoke token")
