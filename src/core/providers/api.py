"""
Provider Stack API — REST endpoints for model stack visibility (v8.3.0).

Exposes the current provider configuration, lane assignments, discovered
models, and model profiles to the War Room UI.

Public API:
    router                      → FastAPI APIRouter
    init_provider_api(discovery) → wire ModelDiscovery instance
"""

import logging
from typing import Optional

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/providers", tags=["providers"])

# Module-level reference — set during gateway startup
_discovery = None


def init_provider_api(discovery) -> None:
    """Wire the ModelDiscovery instance into the API routes."""
    global _discovery
    _discovery = discovery
    logger.info("Provider API initialized")


@router.get("/stack")
def get_provider_stack():
    """Return current model stack: provider, lanes, discovered models."""
    if not _discovery:
        return {
            "provider": "none",
            "provider_display_name": "Not Configured",
            "lanes": {},
            "discovered_models": [],
            "models_count": 0,
            "last_refresh": None,
            "status": "unavailable",
        }

    stack = _discovery.get_stack()
    stack["status"] = "connected"
    return stack


@router.get("/models")
def get_available_models():
    """Return all models discovered from the active provider."""
    if not _discovery:
        return {"models": [], "provider": "none"}

    return {
        "provider": _discovery.provider_name,
        "models": [
            {
                "id": m.id,
                "display_name": m.display_name,
                "context_window": m.context_window,
                "supports_tools": m.supports_tools,
                "capability_tier": m.capability_tier,
                "cost_input_per_1k": m.input_cost_per_1k,
                "cost_output_per_1k": m.output_cost_per_1k,
            }
            for m in _discovery.discovered_models
        ],
    }


@router.post("/refresh")
def refresh_models():
    """Re-run model discovery from the provider API."""
    if not _discovery:
        return {"status": "error", "message": "No provider configured"}

    try:
        _discovery.refresh()
        return {
            "status": "ok",
            "models_found": len(_discovery.discovered_models),
            "lanes": _discovery.lane_assignments,
        }
    except Exception as e:
        logger.error("Model discovery refresh failed: %s", e)
        return {"status": "error", "message": str(e)}


@router.get("/profiles")
def get_model_profiles():
    """Return static model profiles from model_profiles.yaml."""
    if not _discovery:
        return {"profiles": {}}

    # Return profiles for all lane-assigned models
    profiles = {}
    for lane, model_id in _discovery.lane_assignments.items():
        profiles[model_id] = _discovery.get_model_profile(model_id)

    return {"profiles": profiles}
