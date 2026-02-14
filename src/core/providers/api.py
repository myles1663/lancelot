"""
Provider Stack API — REST endpoints for model stack + provider switching (v8.3.1).

Exposes the current provider configuration, lane assignments, discovered
models, and model profiles to the War Room UI.  New in v8.3.1: endpoints to
hot-swap the active provider, override lane model assignments, and reset
lanes to auto-assignment.

Public API:
    router                                → FastAPI APIRouter
    init_provider_api(discovery, orchestrator) → wire instances
    load_persisted_config()               → read provider_config.json
    save_persisted_config(data)           → write provider_config.json
"""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/providers", tags=["providers"])

# Module-level references — set during gateway startup
_discovery = None
_orchestrator = None

# Persistence path (inside Docker volume)
_DATA_DIR = Path(os.getenv("LANCELOT_DATA_DIR", "lancelot_data"))
_CONFIG_FILE = _DATA_DIR / "provider_config.json"

# Models config (for provider display names)
_MODELS_YAML = "config/models.yaml"


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class SwitchProviderRequest(BaseModel):
    provider: str


class LaneOverrideRequest(BaseModel):
    lane: str
    model_id: str


# ---------------------------------------------------------------------------
# Initialization + persistence
# ---------------------------------------------------------------------------

def init_provider_api(discovery, orchestrator=None) -> None:
    """Wire the ModelDiscovery and Orchestrator instances into the API routes."""
    global _discovery, _orchestrator
    _discovery = discovery
    _orchestrator = orchestrator
    logger.info("Provider API initialized (orchestrator=%s)", "yes" if orchestrator else "no")


def load_persisted_config() -> dict:
    """Load persisted provider config from data/provider_config.json."""
    try:
        if _CONFIG_FILE.exists():
            with open(_CONFIG_FILE, "r") as f:
                data = json.load(f)
            logger.info("Loaded persisted provider config: %s", data)
            return data
    except Exception as e:
        logger.warning("Failed to load provider_config.json: %s", e)
    return {}


def _save_config(data: dict) -> None:
    """Atomic write of provider_config.json (write to tmp, then rename)."""
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(_DATA_DIR), suffix=".tmp", prefix="provider_config_"
        )
        try:
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(data, f, indent=2)
            # Atomic rename (works on POSIX; on Windows, replace is used)
            os.replace(tmp_path, str(_CONFIG_FILE))
            logger.info("Persisted provider config: %s", data)
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        logger.warning("Failed to save provider_config.json: %s", e)


def _read_current_config() -> dict:
    """Read the current persisted config (or empty dict)."""
    return load_persisted_config()


def _get_provider_display_names() -> dict:
    """Load provider display names from models.yaml."""
    try:
        with open(_MODELS_YAML, "r") as f:
            data = yaml.safe_load(f) or {}
        providers = data.get("providers", {})
        return {
            name: info.get("display_name", name.title())
            for name, info in providers.items()
        }
    except Exception:
        return {
            "gemini": "Google Gemini",
            "openai": "OpenAI",
            "anthropic": "Anthropic",
        }


# ---------------------------------------------------------------------------
# Existing endpoints (v8.3.0)
# ---------------------------------------------------------------------------

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

    profiles = {}
    for lane, model_id in _discovery.lane_assignments.items():
        profiles[model_id] = _discovery.get_model_profile(model_id)

    return {"profiles": profiles}


# ---------------------------------------------------------------------------
# New endpoints (v8.3.1) — Provider switching + lane overrides
# ---------------------------------------------------------------------------

@router.get("/available")
def get_available_providers():
    """List all known providers with API key availability and active status."""
    from providers.factory import API_KEY_VARS

    display_names = _get_provider_display_names()
    current_provider = _discovery.provider_name if _discovery else None

    providers = []
    for name, env_var in API_KEY_VARS.items():
        has_key = bool(os.getenv(env_var, "").strip())
        providers.append({
            "name": name,
            "display_name": display_names.get(name, name.title()),
            "has_key": has_key,
            "active": name == current_provider,
        })

    return {"providers": providers}


@router.post("/switch")
def switch_provider(req: SwitchProviderRequest):
    """Hot-swap the active LLM provider (no restart required)."""
    from providers.factory import API_KEY_VARS, create_provider

    provider_name = req.provider.lower().strip()

    # Validate provider name
    if provider_name not in API_KEY_VARS:
        return {
            "status": "error",
            "message": f"Unknown provider: '{provider_name}'. Available: {', '.join(API_KEY_VARS.keys())}",
        }

    # Validate API key is configured
    env_var = API_KEY_VARS[provider_name]
    api_key = os.getenv(env_var, "").strip()
    if not api_key:
        return {
            "status": "error",
            "message": f"No API key configured for {provider_name} (set {env_var})",
        }

    try:
        # Switch the orchestrator's provider
        if _orchestrator:
            result_msg = _orchestrator.switch_provider(provider_name)
        else:
            result_msg = f"Switched to {provider_name} (no orchestrator)"

        # Create a fresh provider for model discovery
        new_provider = create_provider(provider_name, api_key)

        # Read existing config to preserve lane overrides if desired
        config = _read_current_config()
        lane_overrides = config.get("lane_overrides", {})

        # Replace provider in discovery and re-run
        if _discovery:
            _discovery.replace_provider(new_provider, lane_overrides=lane_overrides)

        # Persist the switch
        config["active_provider"] = provider_name
        _save_config(config)

        # Return updated stack
        stack = _discovery.get_stack() if _discovery else {}
        stack["status"] = "connected"
        return {
            "status": "ok",
            "message": result_msg,
            "stack": stack,
        }

    except Exception as e:
        logger.error("Provider switch failed: %s", e, exc_info=True)
        return {"status": "error", "message": str(e)}


@router.post("/lanes/override")
def override_lane(req: LaneOverrideRequest):
    """Override a single lane's model assignment at runtime."""
    lane = req.lane.lower().strip()
    model_id = req.model_id.strip()

    if lane not in ("fast", "deep", "cache"):
        return {
            "status": "error",
            "message": f"Unknown lane: '{lane}'. Available: fast, deep, cache",
        }

    if not model_id:
        return {"status": "error", "message": "model_id is required"}

    # Validate model exists in discovered models (if discovery is available)
    if _discovery:
        discovered_ids = [m.id for m in _discovery.discovered_models]
        if discovered_ids and model_id not in discovered_ids:
            return {
                "status": "error",
                "message": f"Model '{model_id}' not found in discovered models. Available: {', '.join(discovered_ids[:10])}",
            }

    try:
        # Update discovery lane assignment
        if _discovery:
            _discovery.set_lane_override(lane, model_id)

        # Update orchestrator model references
        if _orchestrator:
            _orchestrator.set_lane_model(lane, model_id)

        # Persist the override
        config = _read_current_config()
        if "lane_overrides" not in config:
            config["lane_overrides"] = {}
        config["lane_overrides"][lane] = model_id
        _save_config(config)

        # Return updated stack
        stack = _discovery.get_stack() if _discovery else {}
        stack["status"] = "connected"
        return {
            "status": "ok",
            "message": f"Lane '{lane}' overridden to {model_id}",
            "stack": stack,
        }

    except Exception as e:
        logger.error("Lane override failed: %s", e, exc_info=True)
        return {"status": "error", "message": str(e)}


@router.post("/lanes/reset")
def reset_lanes():
    """Clear all lane overrides and re-run auto-assignment."""
    try:
        if _discovery:
            _discovery.reset_overrides()

        # Also reset orchestrator to profile defaults
        if _orchestrator and _discovery:
            for lane, model_id in _discovery.lane_assignments.items():
                try:
                    _orchestrator.set_lane_model(lane, model_id)
                except Exception as e:
                    logger.warning("Failed to reset orchestrator lane '%s': %s", lane, e)

        # Persist cleared state
        config = _read_current_config()
        config.pop("lane_overrides", None)
        _save_config(config)

        # Return updated stack
        stack = _discovery.get_stack() if _discovery else {}
        stack["status"] = "connected"
        return {
            "status": "ok",
            "message": "Lane overrides cleared — auto-assignment active",
            "stack": stack,
        }

    except Exception as e:
        logger.error("Lane reset failed: %s", e, exc_info=True)
        return {"status": "error", "message": str(e)}
