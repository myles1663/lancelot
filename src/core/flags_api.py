"""
Flags API — /api/flags

Exposes current feature flag values for the War Room Kill Switches page.
"""

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/flags", tags=["flags"])


@router.get("")
async def get_flags():
    """Return all feature flag values."""
    try:
        import feature_flags as ff
        flags = {
            # Core subsystems
            "FEATURE_SOUL": ff.FEATURE_SOUL,
            "FEATURE_SKILLS": ff.FEATURE_SKILLS,
            "FEATURE_HEALTH_MONITOR": ff.FEATURE_HEALTH_MONITOR,
            "FEATURE_SCHEDULER": ff.FEATURE_SCHEDULER,
            "FEATURE_MEMORY_VNEXT": ff.FEATURE_MEMORY_VNEXT,
            # Tool Fabric
            "FEATURE_TOOLS_FABRIC": ff.FEATURE_TOOLS_FABRIC,
            "FEATURE_TOOLS_CLI_PROVIDERS": ff.FEATURE_TOOLS_CLI_PROVIDERS,
            "FEATURE_TOOLS_ANTIGRAVITY": ff.FEATURE_TOOLS_ANTIGRAVITY,
            "FEATURE_TOOLS_NETWORK": ff.FEATURE_TOOLS_NETWORK,
            "FEATURE_TOOLS_HOST_EXECUTION": ff.FEATURE_TOOLS_HOST_EXECUTION,
        }

        # Add any additional flags that exist
        for attr in dir(ff):
            if attr.startswith("FEATURE_") and attr not in flags:
                val = getattr(ff, attr, None)
                if isinstance(val, bool):
                    flags[attr] = val

        return {"flags": flags}
    except Exception as exc:
        logger.error("get_flags error: %s", exc)
        return JSONResponse(status_code=500, content={"error": "Failed to read flags"})
