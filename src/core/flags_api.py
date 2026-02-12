"""
Flags API — /api/flags

Exposes current feature flag values and allows runtime toggling
for the War Room Kill Switches page.
"""

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/flags", tags=["flags"])


@router.get("")
async def get_flags():
    """Return all feature flag values and metadata."""
    try:
        import feature_flags as ff
        flags = {}

        for attr in sorted(dir(ff)):
            if attr.startswith("FEATURE_"):
                val = getattr(ff, attr, None)
                if isinstance(val, bool):
                    flags[attr] = {
                        "enabled": val,
                        "restart_required": attr in ff.RESTART_REQUIRED_FLAGS,
                    }

        return {"flags": flags}
    except Exception as exc:
        logger.error("get_flags error: %s", exc)
        return JSONResponse(status_code=500, content={"error": "Failed to read flags"})


@router.post("/{name}/toggle")
async def toggle_flag(name: str):
    """Toggle a feature flag at runtime."""
    try:
        import feature_flags as ff
        new_val = ff.toggle_flag(name)
        restart_needed = name in ff.RESTART_REQUIRED_FLAGS
        return {
            "flag": name,
            "enabled": new_val,
            "restart_required": restart_needed,
            "message": f"{name} set to {new_val}" + (
                " (restart required for full effect)" if restart_needed else ""
            ),
        }
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except Exception as exc:
        logger.error("toggle_flag error: %s", exc)
        return JSONResponse(status_code=500, content={"error": "Failed to toggle flag"})


@router.post("/{name}/set")
async def set_flag(name: str, value: bool = True):
    """Set a feature flag to a specific value."""
    try:
        import feature_flags as ff
        ff.set_flag(name, value)
        restart_needed = name in ff.RESTART_REQUIRED_FLAGS
        return {
            "flag": name,
            "enabled": value,
            "restart_required": restart_needed,
        }
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except Exception as exc:
        logger.error("set_flag error: %s", exc)
        return JSONResponse(status_code=500, content={"error": "Failed to set flag"})
