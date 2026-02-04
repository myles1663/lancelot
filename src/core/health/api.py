"""
Heartbeat API — /health/live and /health/ready endpoints (Prompt 9 / C1-C2).

Public API:
    router        — FastAPI APIRouter with health endpoints
    set_snapshot_provider(fn)  — register a function that returns HealthSnapshot
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.core.health.types import HealthSnapshot

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])

# Snapshot provider — set by the application at startup
_snapshot_provider: Optional[Callable[[], HealthSnapshot]] = None


def set_snapshot_provider(fn: Callable[[], HealthSnapshot]) -> None:
    """Register a function that computes the current HealthSnapshot."""
    global _snapshot_provider
    _snapshot_provider = fn


def _get_snapshot() -> HealthSnapshot:
    """Get the current health snapshot, using the provider if set."""
    if _snapshot_provider is not None:
        try:
            return _snapshot_provider()
        except Exception as exc:
            logger.error("Health snapshot provider failed: %s", exc)
            return HealthSnapshot(
                ready=False,
                degraded_reasons=["Health snapshot provider error"],
            )
    return HealthSnapshot(
        ready=False,
        degraded_reasons=["No health snapshot provider configured"],
    )


# ---------------------------------------------------------------------------
# GET /health/live
# ---------------------------------------------------------------------------

@router.get("/live")
async def health_live():
    """Liveness probe — always returns 200 if the process is running."""
    return {"status": "alive"}


# ---------------------------------------------------------------------------
# GET /health/ready
# ---------------------------------------------------------------------------

@router.get("/ready")
async def health_ready():
    """Readiness probe — returns health snapshot with ready state.

    Returns 200 with ready=true when all systems operational,
    200 with ready=false and degraded_reasons when degraded.
    Never leaks stack traces.
    """
    try:
        snapshot = _get_snapshot()
        return snapshot.model_dump()
    except Exception:
        logger.exception("Health ready check failed")
        return JSONResponse(
            status_code=200,
            content={
                "ready": False,
                "onboarding_state": "UNKNOWN",
                "local_llm_ready": False,
                "scheduler_running": False,
                "last_health_tick_at": None,
                "last_scheduler_tick_at": None,
                "degraded_reasons": ["Health check failed"],
                "timestamp": None,
            },
        )
