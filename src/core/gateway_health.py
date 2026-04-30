"""Gateway health and readiness snapshot assembly."""

from __future__ import annotations

import time
from typing import Any


def summarize_local_model_role_lane(roles: dict[str, Any]) -> dict[str, Any]:
    """Summarize role-router health as one operator-facing local model lane."""
    enabled_roles = [
        payload for payload in (roles or {}).values()
        if isinstance(payload, dict) and payload.get("enabled", True)
    ]
    if not enabled_roles:
        return {"ready": False, "loaded": False, "status": "unavailable"}

    ready = all(bool(role.get("ready")) for role in enabled_roles)
    loaded = any(bool(role.get("loaded", role.get("ready"))) for role in enabled_roles)
    failed = [
        role for role in enabled_roles
        if not role.get("ready") and role.get("last_error")
    ]
    verified = [
        str(role.get("last_verified_at"))
        for role in enabled_roles
        if role.get("last_verified_at")
    ]
    checked = [
        str(role.get("last_checked_at"))
        for role in enabled_roles
        if role.get("last_checked_at")
    ]
    smoke_times = [
        float(role.get("last_smoke_elapsed_ms"))
        for role in enabled_roles
        if role.get("last_smoke_elapsed_ms") is not None
    ]
    return {
        "ready": ready,
        "loaded": loaded,
        "status": "ok" if ready else ("degraded" if loaded else "unavailable"),
        "last_error": "; ".join(str(role.get("last_error")) for role in failed) or None,
        "last_verified_at": max(verified) if verified else None,
        "last_checked_at": max(checked) if checked else None,
        "last_smoke_elapsed_ms": max(smoke_times) if smoke_times else None,
    }


def summarize_local_model_router_readiness(local_roles) -> dict[str, Any]:
    """Return aggregate readiness for a role-specific local model router."""
    if local_roles is None:
        return {
            "ready": False,
            "loaded": False,
            "status": "unavailable",
            "last_error": None,
        }
    try:
        raw = local_roles.status()
    except Exception as exc:
        return {
            "ready": False,
            "loaded": False,
            "status": "unavailable",
            "last_error": str(exc),
        }

    roles = raw.get("roles", raw) if isinstance(raw, dict) else {}
    summary = summarize_local_model_role_lane(roles)
    summary.setdefault("last_error", None)
    return summary


def _collect_local_model_status(main_orchestrator, logger) -> dict[str, Any]:
    local_llm_ready = False
    local_llm_loaded = False
    local_llm_status = "unavailable"
    local_llm_last_error = None
    local_llm_last_verified_at = None
    local_llm_last_checked_at = None
    local_llm_consecutive_failures = 0
    local_llm_last_smoke_elapsed_ms = None
    local_llm_roles: dict[str, Any] = {}

    if getattr(main_orchestrator, "local_model", None) is not None:
        try:
            local_health = main_orchestrator.local_model.health()
            local_llm_ready = bool(local_health.get("ready"))
            local_llm_loaded = bool(local_health.get("loaded", local_llm_ready))
            local_llm_status = local_health.get(
                "status",
                "ok" if local_llm_ready else "degraded",
            )
            local_llm_last_error = local_health.get("last_error")
            local_llm_last_verified_at = local_health.get("last_verified_at")
            local_llm_last_checked_at = local_health.get("last_checked_at")
            local_llm_consecutive_failures = local_health.get("consecutive_failures", 0)
            local_llm_last_smoke_elapsed_ms = local_health.get("last_smoke_elapsed_ms")
        except Exception as exc:
            local_llm_last_error = str(exc)
            local_llm_status = "unavailable"

    try:
        local_roles_router = getattr(main_orchestrator, "local_model_roles", None)
        if local_roles_router is not None:
            from src.core.model_usage_policy import set_local_model_roles_status

            raw_roles = local_roles_router.status()
            set_local_model_roles_status(raw_roles)
        else:
            from src.core.model_usage_policy import get_model_usage_status

            raw_roles = get_model_usage_status().get("local_model_roles", {}) or {}
        if isinstance(raw_roles, dict) and isinstance(raw_roles.get("roles"), dict):
            local_llm_roles = raw_roles["roles"]
        elif isinstance(raw_roles, dict):
            local_llm_roles = raw_roles
    except Exception as exc:
        logger.debug("Local model role status unavailable during health check: %s", exc)

    role_lane = summarize_local_model_role_lane(local_llm_roles)
    if not local_llm_ready and role_lane.get("ready"):
        local_llm_ready = True
        local_llm_loaded = True
        local_llm_status = "ok"
        local_llm_last_error = None
        local_llm_last_verified_at = role_lane.get("last_verified_at")
        local_llm_last_checked_at = role_lane.get("last_checked_at")
        local_llm_last_smoke_elapsed_ms = role_lane.get("last_smoke_elapsed_ms")
    elif not local_llm_ready and role_lane.get("loaded"):
        local_llm_loaded = True
        local_llm_status = "degraded"
        local_llm_last_error = role_lane.get("last_error") or local_llm_last_error

    return {
        "loaded": local_llm_loaded,
        "ready": local_llm_ready,
        "status": local_llm_status,
        "last_error": local_llm_last_error,
        "last_verified_at": local_llm_last_verified_at,
        "last_checked_at": local_llm_last_checked_at,
        "consecutive_failures": local_llm_consecutive_failures,
        "last_smoke_elapsed_ms": local_llm_last_smoke_elapsed_ms,
        "roles": local_llm_roles,
    }


def _provider_component_status(state: str) -> str:
    if state == "healthy":
        return "ok"
    if state == "degraded":
        return "degraded"
    if state == "offline":
        return "offline"
    return "unknown"


def _collect_tool_provider_status(logger) -> tuple[dict[str, str], dict[str, Any]]:
    """Surface enabled Tool Fabric provider health in the public health snapshot."""
    try:
        import feature_flags as ff
        from src.tools.fabric import get_tool_fabric

        enabled_providers = {
            "host_execution": bool(getattr(ff, "FEATURE_TOOLS_HOST_EXECUTION", False)),
            "host_bridge": bool(getattr(ff, "FEATURE_TOOLS_HOST_BRIDGE", False)),
            "uab_bridge": bool(getattr(ff, "FEATURE_TOOLS_UAB", False)),
        }
        active_provider_ids = [
            provider_id for provider_id, enabled in enabled_providers.items()
            if enabled
        ]
        if not active_provider_ids:
            return {}, {}

        fabric = get_tool_fabric()
        try:
            health_by_provider = fabric.get_health()
        except Exception:
            health_by_provider = fabric.probe_health()

        components: dict[str, str] = {}
        details: dict[str, Any] = {}
        for provider_id in active_provider_ids:
            health = health_by_provider.get(provider_id)
            if health is None:
                components[provider_id] = "unavailable"
                details[provider_id] = {
                    "state": "unavailable",
                    "degraded_reasons": ["provider is enabled but not registered"],
                    "error_message": "provider is enabled but not registered",
                }
                continue

            state = getattr(getattr(health, "state", None), "value", str(getattr(health, "state", "unknown")))
            components[provider_id] = _provider_component_status(state)
            details[provider_id] = {
                "state": state,
                "degraded_reasons": list(getattr(health, "degraded_reasons", []) or []),
                "error_message": getattr(health, "error_message", None),
                "metadata": dict(getattr(health, "metadata", {}) or {}),
            }
        return components, details
    except Exception as exc:
        logger.debug("Tool Fabric provider health unavailable during health check: %s", exc)
        return {}, {}


def build_health_snapshot(
    *,
    main_orchestrator,
    crusader_mode,
    app_version: str,
    startup_time: float | None,
    error_count: int,
    total_requests: int,
    logger,
) -> dict[str, Any]:
    """Build the public /health response without owning FastAPI concerns."""
    local_llm = _collect_local_model_status(main_orchestrator, logger)
    local_ready = bool(local_llm["ready"])
    local_loaded = bool(local_llm["loaded"])
    components = {
        "gateway": "ok",
        "orchestrator": "ok" if main_orchestrator.provider else "degraded",
        "local_llm": "ok" if local_ready else ("degraded" if local_loaded else "unavailable"),
        "sentry": "ok",
        "vault": "ok",
        "memory": "ok" if main_orchestrator.is_memory_enabled() else "disabled",
    }
    tool_provider_components, tool_provider_health = _collect_tool_provider_status(logger)
    components.update(tool_provider_components)
    uptime = round(time.time() - startup_time, 1) if startup_time else 0
    snapshot = {
        "status": "online",
        "version": app_version,
        "components": components,
        "local_llm": local_llm,
        "crusader_mode": crusader_mode.is_active,
        "uptime_seconds": uptime,
        "error_count": error_count,
        "total_requests": total_requests,
        "error_rate": round(error_count / max(total_requests, 1) * 100, 2),
    }
    if tool_provider_health:
        snapshot["tool_fabric"] = {"providers": tool_provider_health}
    return snapshot


def build_readiness_snapshot(
    *,
    main_orchestrator,
    startup_time: float | None,
) -> tuple[int, dict[str, Any]]:
    """Build the /ready status code and response body."""
    ready = startup_time is not None
    components = {
        "gateway": "ok",
        "orchestrator": "ok" if main_orchestrator.provider else "degraded",
        "sentry": "ok",
        "memory": "ok" if main_orchestrator.is_memory_enabled() else "disabled",
    }
    all_ok = all(value in ("ok", "disabled") for value in components.values())
    status_code = 200 if (ready and all_ok) else 503
    return status_code, {"ready": ready and all_ok, "components": components}
