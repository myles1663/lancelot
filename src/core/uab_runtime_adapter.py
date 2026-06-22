"""Narrow runtime adapter between Lancelot Core and the embedded UAB package."""

from __future__ import annotations

import inspect
from typing import Any, Optional


def create_uab_provider(receipt_service: Optional[Any] = None):
    """Create the embedded UAB provider behind the approved adapter boundary."""
    from src.shared.receipts_service import get_receipt_service
    from src.tools.providers.uab_bridge import UABProvider

    signature = inspect.signature(UABProvider)
    accepts_receipts = (
        "receipt_service" in signature.parameters
        or any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values())
    )
    if accepts_receipts:
        return UABProvider(receipt_service=receipt_service or get_receipt_service())
    return UABProvider()


def init_uab_provider(logger: Optional[Any] = None) -> dict[str, Any]:
    """Register the UAB provider with Tool Fabric."""
    from src.tools.fabric import get_tool_fabric

    fabric = get_tool_fabric()
    provider = create_uab_provider()
    fabric.register_provider(provider)
    fabric.update_router_preferences()
    if logger is not None:
        logger.warning(
            "UAB BRIDGE hot-started; desktop app control via daemon at %s",
            provider.config.daemon_url,
        )
    return {"provider": provider}


def shutdown_uab_provider(logger: Optional[Any] = None) -> None:
    """Unregister the UAB provider from Tool Fabric."""
    from src.tools.fabric import get_tool_fabric

    fabric = get_tool_fabric()
    fabric.unregister_provider("uab_bridge")
    fabric.update_router_preferences()
    if logger is not None:
        logger.info("UAB Bridge provider unregistered.")


def summarize_uab_provider_health(provider: Any, health: Any) -> dict[str, Any]:
    """Return UAB startup status through the provider's public contract."""
    if hasattr(provider, "summarize_health"):
        return provider.summarize_health(health)

    metadata = getattr(health, "metadata", {}) or {}
    state = getattr(getattr(health, "state", None), "value", "unknown")
    daemon_url = metadata.get("daemon_url")
    config = getattr(provider, "config", None)
    if daemon_url is None and config is not None:
        daemon_url = getattr(config, "daemon_url", None)
    return {
        "state": state,
        "daemon_url": daemon_url,
        "error": getattr(health, "error_message", None),
    }


def get_uab_provider(logger: Any) -> tuple[Any, dict[str, Any]]:
    """Return a UAB provider when the daemon can be reached or may recover."""
    try:
        provider = create_uab_provider()
        health = provider.health_check()
        status = summarize_uab_provider_health(provider, health)
        state = status["state"]
        if state == "healthy":
            return provider, status
        logger.warning(
            "HIVE UAB provider offline at startup; desktop-control workflows will remain unavailable "
            "until the host daemon recovers (health_state=%s, daemon_url=%s, error=%s)",
            state,
            status["daemon_url"],
            status["error"],
        )
        return provider, status
    except Exception as exc:
        logger.warning(
            "HIVE UAB provider failed to initialize; desktop-control workflows will run without UAB: %s",
            exc,
        )
        return None, {"state": "unavailable", "daemon_url": None, "error": str(exc)}
