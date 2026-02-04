"""
Control-Plane API Endpoints (v4 Upgrade — Prompts 6 & 15)

Provides /system/status, /onboarding/*, and /router/* endpoints for
the War Room and any other control surface.  Mounted as a FastAPI APIRouter.

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


def init_control_plane(data_dir: str) -> None:
    """Initialise the control-plane with a data directory.

    Called once at app startup.
    """
    global _snapshot, _startup_time
    _snapshot = OnboardingSnapshot(data_dir)
    _startup_time = time.time()


def set_model_router(model_router) -> None:
    """Register the ModelRouter instance for War Room endpoints."""
    global _model_router
    _model_router = model_router


def get_model_router():
    """Return the active ModelRouter (or None if not set)."""
    return _model_router


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
        mr = get_model_router()
        if mr is None:
            return {"usage": {}, "message": "Model router not initialised"}
        return {"usage": mr.usage.summary()}
    except Exception as exc:
        logger.error("usage_summary error: %s", exc)
        return _safe_error(500, "Failed to retrieve usage summary")


@router.get("/usage/lanes")
async def usage_lanes():
    """Per-lane usage breakdown."""
    try:
        mr = get_model_router()
        if mr is None:
            return {"lanes": {}, "message": "Model router not initialised"}
        return {"lanes": mr.usage.lane_breakdown()}
    except Exception as exc:
        logger.error("usage_lanes error: %s", exc)
        return _safe_error(500, "Failed to retrieve lane usage")


@router.get("/usage/savings")
async def usage_savings():
    """Estimated savings from local model usage."""
    try:
        mr = get_model_router()
        if mr is None:
            return {"savings": {}, "message": "Model router not initialised"}
        return {"savings": mr.usage.estimated_savings()}
    except Exception as exc:
        logger.error("usage_savings error: %s", exc)
        return _safe_error(500, "Failed to retrieve savings data")


@router.post("/usage/reset")
async def usage_reset():
    """Reset usage counters (starts a new tracking period)."""
    try:
        mr = get_model_router()
        if mr is None:
            return _safe_error(400, "Model router not initialised")
        mr.usage.reset()
        return {"message": "Usage counters reset", "usage": mr.usage.summary()}
    except Exception as exc:
        logger.error("usage_reset error: %s", exc)
        return _safe_error(500, "Failed to reset usage counters")
