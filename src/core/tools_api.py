"""
Tools API — /api/tools/*

Exposes Tool Fabric status, provider health, and capability routing
for the War Room Tool Fabric page.
"""

import logging
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tools", tags=["tools"])

_fabric = None


def init_tools_api() -> None:
    """Initialise the tools API by getting the global ToolFabric instance."""
    global _fabric
    try:
        from feature_flags import FEATURE_TOOLS_FABRIC
        if not FEATURE_TOOLS_FABRIC:
            logger.info("Tools API: Tool Fabric disabled by feature flag")
            return
        from tools.fabric import get_tool_fabric
        _fabric = get_tool_fabric()
        logger.info("Tools API initialised")
    except Exception as exc:
        logger.warning("Tools API init failed: %s", exc)


def _safe_error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


@router.get("/health")
async def tools_health():
    """Provider health status for all registered tool providers."""
    if _fabric is None:
        return {"providers": {}, "summary": {"total_providers": 0}, "enabled": False}
    try:
        health_data = _fabric.get_health()
        summary = {
            "total_providers": len(health_data),
            "healthy": sum(1 for h in health_data.values() if h.state.value == "healthy"),
            "degraded": sum(1 for h in health_data.values() if h.state.value == "degraded"),
            "offline": sum(1 for h in health_data.values() if h.state.value == "offline"),
        }
        providers = {}
        for pid, h in health_data.items():
            providers[pid] = {
                "state": h.state.value,
                "error": h.error_message,
            }
        return {"providers": providers, "summary": summary, "enabled": True}
    except Exception as exc:
        logger.error("tools_health error: %s", exc)
        return _safe_error(500, "Failed to retrieve tool health")


@router.get("/routing")
async def tools_routing():
    """Capability routing summary — which providers handle which capabilities."""
    if _fabric is None:
        return {"capabilities": {}, "enabled": False}
    try:
        routing = _fabric.get_routing_summary()
        return {"routing": routing, "enabled": True}
    except Exception as exc:
        logger.error("tools_routing error: %s", exc)
        return _safe_error(500, "Failed to retrieve routing summary")


@router.get("/config")
async def tools_config():
    """Tool Fabric configuration status."""
    if _fabric is None:
        return {"enabled": False, "safe_mode": False, "receipts": False}
    try:
        return {
            "enabled": _fabric.config.enabled,
            "safe_mode": _fabric.config.safe_mode,
            "receipts": _fabric.config.emit_receipts,
        }
    except Exception as exc:
        logger.error("tools_config error: %s", exc)
        return _safe_error(500, "Failed to retrieve tool config")
