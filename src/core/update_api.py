"""
Lancelot Update API — Endpoints for the War Room update banner.

GET  /api/updates/status  — Cached update status (cheap poll)
POST /api/updates/check   — Force an immediate version check
POST /api/updates/dismiss  — Dismiss the update banner
"""

import logging
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from update_checker import UpdateChecker

logger = logging.getLogger("lancelot.update_api")

router = APIRouter(prefix="/api/updates", tags=["updates"])

# Module-level reference; set by init_update_api()
_checker: UpdateChecker | None = None


def init_update_api(checker: UpdateChecker) -> None:
    """Wire the UpdateChecker instance into the router."""
    global _checker
    _checker = checker
    logger.info("Update API initialized.")


def _safe_error(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": msg, "status": status})


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.get("/status")
async def update_status():
    """Return current update status (reads cached state — no network call)."""
    if not _checker:
        return _safe_error(503, "Update checker not initialized")
    return _checker.get_update_status()


@router.post("/check")
async def update_check():
    """Force an immediate version check."""
    if not _checker:
        return _safe_error(503, "Update checker not initialized")
    result = _checker.force_check()
    return {"status": "ok", **result}


@router.post("/dismiss")
async def update_dismiss():
    """Dismiss the update banner (info/recommended only)."""
    if not _checker:
        return _safe_error(503, "Update checker not initialized")
    success = _checker.dismiss()
    if not success:
        return _safe_error(400, "Cannot dismiss important/critical updates")
    return {"status": "dismissed"}
