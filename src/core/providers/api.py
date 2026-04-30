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
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from src.core.api_auth import require_authenticated_request
from src.core.auth_api import require_operator_capability

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/providers",
    tags=["providers"],
    dependencies=[Depends(require_authenticated_request)],
)

# Module-level references — set during gateway startup
_discovery = None
_orchestrator = None

# Auth status tracking — updated when provider calls fail with auth errors.
# Maps provider name → error message (None = healthy).
_auth_errors: dict[str, str] = {}


def report_auth_error(provider: str, message: str = "") -> None:
    """Record that a provider's API key failed authentication.

    Called by the orchestrator when a ProviderAuthError is caught.
    The /stack endpoint reads this to show accurate status in the War Room.
    """
    _auth_errors[provider] = message or "Invalid API key"
    logger.warning("Provider auth error recorded for '%s': %s", provider, message)


def clear_auth_error(provider: str) -> None:
    """Clear a previously recorded auth error (e.g. after successful key rotation)."""
    _auth_errors.pop(provider, None)

def _resolve_data_dir() -> Path:
    """Resolve the durable provider-config directory."""
    configured = os.getenv("LANCELOT_DATA_DIR", "").strip()
    if configured:
        return Path(configured)

    docker_data_dir = Path("/home/lancelot/data")
    if docker_data_dir.exists():
        return docker_data_dir

    return Path("lancelot_data")


# Persistence path (prefer durable runtime data volume, fall back to legacy local path)
_DATA_DIR = _resolve_data_dir()
_CONFIG_FILE = _DATA_DIR / "provider_config.json"
_LEGACY_CONFIG_FILE = Path("lancelot_data") / "provider_config.json"

# Models config (for provider display names)
_MODELS_YAML = "config/models.yaml"


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class SwitchProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str


class LaneOverrideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lane: str
    model_id: str


class RotateKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    api_key: str


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

        if _LEGACY_CONFIG_FILE.exists():
            with open(_LEGACY_CONFIG_FILE, "r") as f:
                data = json.load(f)
            logger.info("Loaded legacy provider config from %s: %s", _LEGACY_CONFIG_FILE, data)
            if _CONFIG_FILE != _LEGACY_CONFIG_FILE:
                _save_config(data)
                logger.info("Migrated provider config to durable path: %s", _CONFIG_FILE)
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
            except OSError as exc:
                logger.debug(
                    "Failed to remove temporary provider config '%s' after write error: %s",
                    tmp_path,
                    exc,
                )
            raise
    except Exception as e:
        logger.warning("Failed to save provider_config.json: %s", e)


def _read_current_config() -> dict:
    """Read the current persisted config (or empty dict)."""
    return load_persisted_config()


def ensure_persisted_active_provider(provider_name: str) -> bool:
    """Persist the active provider when runtime state is healthy but config is missing/stale."""
    provider_name = (provider_name or "").strip()
    if not provider_name:
        return False

    config = _read_current_config()
    if config.get("active_provider") == provider_name:
        return False

    config["active_provider"] = provider_name
    _save_config(config)
    return True


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
            "xai": "xAI (Grok)",
        }


def _anthropic_oauth_status() -> Optional[dict]:
    try:
        from oauth_token_manager import get_oauth_manager

        mgr = get_oauth_manager()
        if mgr:
            return mgr.get_token_status()
    except Exception as exc:
        logger.warning("Failed to inspect Anthropic OAuth status: %s", exc)
    return None


def _anthropic_oauth_token() -> str:
    try:
        from oauth_token_manager import get_oauth_manager

        mgr = get_oauth_manager()
        if mgr:
            return mgr.get_valid_token() or ""
    except Exception as exc:
        logger.warning("Failed to retrieve Anthropic OAuth token: %s", exc)
    return ""


def _codex_oauth_status() -> Optional[dict]:
    manager_status: Optional[dict] = None
    try:
        from openai_codex_oauth_manager import get_openai_codex_manager

        mgr = get_openai_codex_manager()
        if mgr:
            manager_status = mgr.get_token_status()
    except Exception as exc:
        logger.warning("Failed to inspect Codex OAuth status: %s", exc)
    if manager_status and manager_status.get("configured"):
        return manager_status
    if _codex_cli_auth_available():
        return {
            "configured": True,
            "valid": True,
            "status": "cli_auth",
            "provider": "openai-codex",
        }
    return manager_status


def _codex_oauth_token() -> str:
    try:
        from openai_codex_oauth_manager import get_openai_codex_manager

        mgr = get_openai_codex_manager()
        if mgr:
            return mgr.get_valid_token() or ""
    except Exception as exc:
        logger.warning("Failed to retrieve Codex OAuth token: %s", exc)
    return ""


def _codex_cli_auth_available() -> bool:
    try:
        from providers.codex_cli_client import has_codex_cli_auth

        return has_codex_cli_auth()
    except Exception as exc:
        logger.warning("Failed to inspect Codex CLI auth availability: %s", exc)
        return False


def _read_provider_profile_lanes(provider_name: str, label: str) -> dict:
    try:
        from provider_profile import ProfileRegistry

        registry = ProfileRegistry()
        if registry.has_provider(provider_name):
            profile = registry.get_profile(provider_name)
            lane_overrides = {
                "fast": profile.fast.model,
                "deep": profile.deep.model,
            }
            if profile.cache:
                lane_overrides["cache"] = profile.cache.model
            return lane_overrides
    except Exception as exc:
        logger.warning(
            "Failed to seed provider lane %s from profile '%s': %s",
            label,
            provider_name,
            exc,
        )
    return {}


def _provider_profile_lane_defaults(provider_name: str) -> dict:
    return _read_provider_profile_lanes(provider_name, "defaults")


def _provider_profile_lane_overrides(provider_name: str) -> dict:
    """Backward-compatible alias for tests and older call sites."""
    return _read_provider_profile_lanes(provider_name, "overrides")


def _create_model_discovery(provider, *, lane_overrides: dict, fallback_lanes: dict):
    from model_discovery import ModelDiscovery

    try:
        return ModelDiscovery(
            provider,
            lane_overrides=lane_overrides,
            fallback_lanes=fallback_lanes,
        )
    except TypeError:
        return ModelDiscovery(provider, lane_overrides=lane_overrides)


def _replace_discovery_provider(discovery, new_provider, *, lane_overrides: dict, fallback_lanes: dict) -> None:
    try:
        discovery.replace_provider(
            new_provider,
            lane_overrides=lane_overrides,
            fallback_lanes=fallback_lanes,
        )
    except TypeError:
        discovery.replace_provider(new_provider, lane_overrides=lane_overrides)


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

    from providers.factory import API_KEY_VARS

    stack = _discovery.get_stack()

    # Determine status: check auth errors first, then key presence
    provider_name = stack.get("provider", "")
    env_var = API_KEY_VARS.get(provider_name, "")
    has_key = bool(os.getenv(env_var, "").strip()) if env_var else False
    # Anthropic OAuth counts as having credentials.
    if not has_key and provider_name == "anthropic":
        status = _anthropic_oauth_status()
        if status and status.get("configured"):
            has_key = True
    # Codex OAuth counts as having credentials
    if not has_key and provider_name == "openai-codex":
        status = _codex_oauth_status()
        if status and status.get("configured"):
            has_key = True

    if provider_name in _auth_errors:
        stack["status"] = "auth_error"
        stack["status_detail"] = _auth_errors[provider_name]
    elif not has_key:
        stack["status"] = "no_key"
    else:
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


@router.post("/refresh", dependencies=[Depends(require_operator_capability("provider.admin"))])
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
# Provider switching + lane overrides
# ---------------------------------------------------------------------------

@router.get("/available")
def get_available_providers():
    """List all known providers with API key availability and active status."""
    from providers.factory import API_KEY_VARS

    display_names = _get_provider_display_names()
    current_provider = _discovery.provider_name if _discovery else None

    providers = []
    for name, env_var in API_KEY_VARS.items():
        has_key = bool(os.getenv(env_var, "").strip()) if env_var else False
        # Anthropic OAuth counts as having credentials.
        if not has_key and name == "anthropic":
            status = _anthropic_oauth_status()
            if status and status.get("configured"):
                has_key = True
        # Codex OAuth counts as having credentials
        if not has_key and name == "openai-codex":
            status = _codex_oauth_status()
            if status and status.get("configured"):
                has_key = True
        providers.append({
            "name": name,
            "display_name": display_names.get(name, name.title()),
            "has_key": has_key,
            "active": name == current_provider,
        })

    return {"providers": providers}


@router.post("/switch", dependencies=[Depends(require_operator_capability("provider.admin"))])
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

    # Validate API key (or OAuth token) is configured
    env_var = API_KEY_VARS[provider_name]
    api_key = os.getenv(env_var, "").strip() if env_var else ""
    if not api_key:
        _has_oauth = False
        # Check for OAuth token as alternative for Anthropic.
        if provider_name == "anthropic":
            _has_oauth = bool(_anthropic_oauth_token())
        # Codex OAuth: ChatGPT Pro subscription
        elif provider_name == "openai-codex":
            _has_oauth = bool(_codex_oauth_token()) or _codex_cli_auth_available()
        if not _has_oauth:
            return {
                "status": "error",
                "message": f"No API key or OAuth token configured for {provider_name}",
            }

    try:
        # Switch the orchestrator's provider
        if _orchestrator:
            result_msg = _orchestrator.switch_provider(provider_name)
        else:
            result_msg = f"Switched to {provider_name} (no orchestrator)"

        # Create a fresh provider for model discovery
        _auth_token = ""
        # Pass OAuth token for Anthropic when no API key.
        if not api_key and provider_name == "anthropic":
            _auth_token = _anthropic_oauth_token()
        # Codex OAuth token
        elif provider_name == "openai-codex":
            _auth_token = _codex_oauth_token()
        new_provider = create_provider(provider_name, api_key, auth_token=_auth_token)

        # Read existing config to preserve lane overrides if desired
        config = _read_current_config()
        lane_overrides = config.get("lane_overrides", {})
        fallback_lanes = _provider_profile_lane_defaults(provider_name)

        # Replace provider in discovery and re-run
        global _discovery
        if _discovery:
            _replace_discovery_provider(
                _discovery,
                new_provider,
                lane_overrides=lane_overrides,
                fallback_lanes=fallback_lanes,
            )
        else:
            # Discovery wasn't initialized at startup (no provider). Create it now.
            _discovery = _create_model_discovery(
                new_provider,
                lane_overrides=lane_overrides,
                fallback_lanes=fallback_lanes,
            )
            _discovery.refresh()

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


@router.post("/lanes/override", dependencies=[Depends(require_operator_capability("provider.admin"))])
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


@router.post("/lanes/reset", dependencies=[Depends(require_operator_capability("provider.admin"))])
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


# ---------------------------------------------------------------------------
# API Key Management (v0.1.2) — rotate keys from the War Room
# ---------------------------------------------------------------------------

def _mask_key(key: str) -> str:
    """Return a masked preview of an API key (last 4 characters only)."""
    if not key or len(key) < 5:
        return "****"
    return "····" + key[-4:]


def _update_env_file(env_var: str, new_value: str) -> bool:
    """Update or add an env var in the .env file for persistence across restarts."""
    env_path = Path(os.getenv("LANCELOT_ENV_PATH", ".env"))
    try:
        lines = []
        found = False
        if env_path.exists():
            with open(env_path, "r") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith(f"{env_var}=") or stripped.startswith(f"{env_var} ="):
                        lines.append(f"{env_var}={new_value}\n")
                        found = True
                    else:
                        lines.append(line)
        if not found:
            lines.append(f"{env_var}={new_value}\n")
        with open(env_path, "w") as f:
            f.writelines(lines)
        return True
    except Exception as e:
        logger.warning("Failed to update .env file: %s", e)
        return False


@router.get("/keys", dependencies=[Depends(require_operator_capability("provider.admin"))])
def get_provider_keys():
    """List all providers with key status (never returns full keys)."""
    from providers.factory import API_KEY_VARS

    display_names = _get_provider_display_names()
    current_provider = _discovery.provider_name if _discovery else None

    keys = []
    for name, env_var in API_KEY_VARS.items():
        # OAuth-only providers (no API key env var) get OAuth status instead of key fields
        if name == "openai-codex":
            entry = {
                "provider": name,
                "display_name": display_names.get(name, "OpenAI (Codex/Pro)"),
                "env_var": "",
                "has_key": False,
                "key_preview": "",
                "active": name == current_provider,
                "oauth_only": True,
                "oauth_configured": False,
                "oauth_status": None,
            }
            status = _codex_oauth_status()
            if status:
                entry["oauth_configured"] = status.get("configured", False)
                entry["oauth_status"] = status.get("status")
                entry["has_key"] = entry["oauth_configured"]  # treat OAuth as "has credentials"
            keys.append(entry)
            continue

        raw = os.getenv(env_var, "").strip() if env_var else ""
        entry = {
            "provider": name,
            "display_name": display_names.get(name, name.title()),
            "env_var": env_var,
            "has_key": bool(raw),
            "key_preview": _mask_key(raw) if raw else "",
            "active": name == current_provider,
            "oauth_configured": False,
            "oauth_status": None,
        }
        # Append OAuth status for Anthropic.
        if name == "anthropic":
            status = _anthropic_oauth_status()
            if status:
                entry["oauth_configured"] = status.get("configured", False)
                entry["oauth_status"] = status.get("status")
        keys.append(entry)

    return {"keys": keys}


@router.post("/keys/rotate", dependencies=[Depends(require_operator_capability("provider.admin"))])
def rotate_provider_key(req: RotateKeyRequest):
    """Validate and rotate an API key for a provider.

    1. Validates provider name
    2. Tests the new key against the provider API
    3. Updates os.environ
    4. If active provider, hot-swaps the provider instance
    5. Persists to .env file
    """
    from providers.factory import API_KEY_VARS, create_provider

    provider_name = req.provider.lower().strip()
    new_key = req.api_key.strip()

    if provider_name not in API_KEY_VARS:
        return {
            "status": "error",
            "message": f"Unknown provider: '{provider_name}'. Available: {', '.join(API_KEY_VARS.keys())}",
        }

    if not new_key or len(new_key) < 10:
        return {"status": "error", "message": "API key is too short"}

    env_var = API_KEY_VARS[provider_name]

    # Step 1: Validate the new key by creating a provider and listing models
    try:
        test_provider = create_provider(provider_name, new_key)
        models = test_provider.list_models()
        logger.info("Key validation for %s: discovered %d models", provider_name, len(models))
    except Exception as e:
        logger.warning("Key validation failed for %s: %s", provider_name, e)
        return {
            "status": "error",
            "message": f"Key validation failed: {e}",
        }

    # Step 2: Update os.environ and clear any auth error
    os.environ[env_var] = new_key
    clear_auth_error(provider_name)

    # Step 3: If this is the active provider, hot-swap
    current_provider = _discovery.provider_name if _discovery else None
    hot_swapped = False

    if provider_name == current_provider:
        try:
            if _orchestrator:
                _orchestrator.switch_provider(provider_name)

            config = _read_current_config()
            lane_overrides = config.get("lane_overrides", {})
            fallback_lanes = _provider_profile_lane_defaults(provider_name)

            if _discovery:
                new_provider = create_provider(provider_name, new_key)
                _replace_discovery_provider(
                    _discovery,
                    new_provider,
                    lane_overrides=lane_overrides,
                    fallback_lanes=fallback_lanes,
                )

            hot_swapped = True
        except Exception as e:
            logger.error("Hot-swap after key rotation failed: %s", e)

    # Step 4: Persist to .env file
    persisted = _update_env_file(env_var, new_key)

    return {
        "status": "ok",
        "provider": provider_name,
        "key_preview": _mask_key(new_key),
        "models_discovered": len(models),
        "hot_swapped": hot_swapped,
        "persisted_to_env": persisted,
        "message": f"API key rotated for {provider_name}" + (
            " and provider hot-swapped" if hot_swapped else ""
        ),
    }


# ---------------------------------------------------------------------------
# OAuth Management — Anthropic OAuth from the War Room
# ---------------------------------------------------------------------------

@router.post("/oauth/initiate", dependencies=[Depends(require_operator_capability("provider.admin"))])
def oauth_initiate():
    """Generate Anthropic OAuth authorization URL with PKCE for browser flow."""
    try:
        from oauth_token_manager import get_oauth_manager
        manager = get_oauth_manager()
        if manager is None:
            return {"status": "error", "message": "OAuth manager not initialized"}
        auth_url, state = manager.generate_auth_url()
        return {"status": "ok", "auth_url": auth_url, "state": state}
    except Exception as e:
        logger.error("OAuth initiate failed: %s", e)
        return {"status": "error", "message": str(e)}


@router.get("/oauth/status", dependencies=[Depends(require_operator_capability("provider.admin"))])
def oauth_status():
    """Get current Anthropic OAuth token status for the War Room."""
    try:
        from oauth_token_manager import get_oauth_manager
        manager = get_oauth_manager()
        if manager is None:
            return {"configured": False, "status": "not_available"}
        return manager.get_token_status()
    except Exception as e:
        return {"configured": False, "status": "error", "error": str(e)}


@router.post("/oauth/revoke", dependencies=[Depends(require_operator_capability("provider.admin"))])
def oauth_revoke():
    """Revoke/clear stored Anthropic OAuth tokens."""
    try:
        from oauth_token_manager import get_oauth_manager
        manager = get_oauth_manager()
        if manager:
            manager.revoke()
        return {"status": "ok", "message": "OAuth tokens revoked"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# OpenAI Codex OAuth Management — ChatGPT Pro subscription access
# ---------------------------------------------------------------------------

@router.post("/oauth/openai-codex/initiate", dependencies=[Depends(require_operator_capability("provider.admin"))])
def oauth_codex_initiate():
    """Generate OpenAI Codex OAuth authorization URL with PKCE for browser flow."""
    try:
        if _codex_cli_auth_available():
            return {
                "status": "ok",
                "message": "Codex CLI auth is already available via mounted ~/.codex/auth.json. No browser OAuth flow is required.",
                "provider": "openai-codex",
            }
        from openai_codex_oauth_manager import get_openai_codex_manager
        manager = get_openai_codex_manager()
        if manager is None:
            return {"status": "error", "message": "Codex OAuth manager not initialized"}
        auth_url, state = manager.generate_auth_url()
        return {"status": "ok", "auth_url": auth_url, "state": state, "provider": "openai-codex"}
    except Exception as e:
        logger.error("Codex OAuth initiate failed: %s", e)
        return {"status": "error", "message": str(e)}


@router.get("/oauth/openai-codex/status", dependencies=[Depends(require_operator_capability("provider.admin"))])
def oauth_codex_status():
    """Get current OpenAI Codex OAuth token status for the War Room."""
    try:
        status = _codex_oauth_status()
        if status is None:
            return {"configured": False, "status": "not_available", "provider": "openai-codex"}
        return status
    except Exception as e:
        return {"configured": False, "status": "error", "provider": "openai-codex", "error": str(e)}


@router.post("/oauth/openai-codex/revoke", dependencies=[Depends(require_operator_capability("provider.admin"))])
def oauth_codex_revoke():
    """Revoke/clear stored OpenAI Codex OAuth tokens."""
    try:
        from openai_codex_oauth_manager import get_openai_codex_manager
        manager = get_openai_codex_manager()
        if manager:
            manager.revoke()
            return {"status": "ok", "message": "Codex OAuth tokens revoked"}
        if _codex_cli_auth_available():
            return {
                "status": "ok",
                "message": "Codex CLI auth is sourced from mounted ~/.codex/auth.json. Sign out on the host to revoke it.",
            }
        return {"status": "ok", "message": "Codex OAuth tokens revoked"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
