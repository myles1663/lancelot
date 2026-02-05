"""
War Room — Tool Fabric Panel (Prompt 10)
=========================================

Displays Tool Fabric status in the War Room:
- Provider toggles and health display
- Routing policy summary
- Recent tool receipts viewer
- Safe Mode toggle

This panel integrates with ToolFabric to show real-time provider
health and allows operators to control tool execution policies.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Callable

from src.tools.contracts import (
    Capability,
    ProviderHealth,
    ProviderState,
)
from src.tools.fabric import (
    ToolFabric,
    ToolFabricConfig,
    get_tool_fabric,
)
from src.tools.receipts import ToolReceipt

logger = logging.getLogger(__name__)


# =============================================================================
# Panel Configuration
# =============================================================================


@dataclass
class ToolsPanelConfig:
    """Configuration for the Tool Fabric panel."""

    # Display settings
    max_receipts_displayed: int = 20
    show_receipt_details: bool = True
    auto_refresh_interval_s: int = 30

    # Health display
    show_offline_providers: bool = True
    show_provider_metadata: bool = False

    # Receipt filtering
    default_capability_filter: Optional[str] = None
    default_provider_filter: Optional[str] = None


# =============================================================================
# Panel Data Provider
# =============================================================================


class ToolsPanel:
    """
    Tool Fabric panel data provider for the War Room.

    Provides:
    - Provider health status
    - Routing summary
    - Receipt history
    - Safe Mode control
    """

    def __init__(
        self,
        fabric: Optional[ToolFabric] = None,
        config: Optional[ToolsPanelConfig] = None,
    ):
        """
        Initialize the Tools Panel.

        Args:
            fabric: Optional ToolFabric instance (uses global if not provided)
            config: Optional panel configuration
        """
        self._fabric = fabric
        self._config = config or ToolsPanelConfig()
        self._receipt_store: List[ToolReceipt] = []
        self._receipt_callbacks: List[Callable[[ToolReceipt], None]] = []

    @property
    def fabric(self) -> ToolFabric:
        """Get the Tool Fabric instance."""
        if self._fabric is None:
            self._fabric = get_tool_fabric()
        return self._fabric

    # =========================================================================
    # Provider Health
    # =========================================================================

    def get_provider_health(self) -> Dict[str, ProviderHealth]:
        """
        Get health status for all providers.

        Returns:
            Dict mapping provider_id to ProviderHealth
        """
        return self.fabric.get_health()

    def get_provider_health_list(self) -> List[Dict[str, Any]]:
        """
        Get provider health as a list of dicts for display.

        Returns:
            List of provider health dicts with display fields
        """
        health_map = self.get_provider_health()
        result = []

        for provider_id, health in health_map.items():
            # Skip offline providers if configured
            if (not self._config.show_offline_providers
                and health.state == ProviderState.OFFLINE):
                continue

            entry = {
                "provider_id": provider_id,
                "state": health.state.value,
                "state_icon": self._state_icon(health.state),
                "version": health.version or "N/A",
                "capabilities": health.capabilities,
                "error_message": health.error_message,
                "last_check": health.last_check,
            }

            if self._config.show_provider_metadata and health.metadata:
                entry["metadata"] = health.metadata

            result.append(entry)

        return sorted(result, key=lambda x: x["provider_id"])

    def probe_provider(self, provider_id: str) -> ProviderHealth:
        """
        Probe a specific provider's health.

        Args:
            provider_id: Provider to probe

        Returns:
            Updated ProviderHealth
        """
        health_map = self.fabric.probe_health(provider_id)
        return health_map.get(provider_id, ProviderHealth(
            provider_id=provider_id,
            state=ProviderState.OFFLINE,
            error_message="Provider not found",
        ))

    def probe_all_providers(self) -> Dict[str, ProviderHealth]:
        """
        Probe all providers (force refresh).

        Returns:
            Dict mapping provider_id to ProviderHealth
        """
        return self.fabric.probe_health()

    def _state_icon(self, state: ProviderState) -> str:
        """Get icon for provider state."""
        icons = {
            ProviderState.HEALTHY: "🟢",
            ProviderState.DEGRADED: "🟡",
            ProviderState.OFFLINE: "🔴",
        }
        return icons.get(state, "⚪")

    # =========================================================================
    # Health Summary
    # =========================================================================

    def get_health_summary(self) -> Dict[str, Any]:
        """
        Get health summary for the status bar.

        Returns:
            Summary dict with counts and overall status
        """
        health_map = self.get_provider_health()

        counts = {
            "healthy": 0,
            "degraded": 0,
            "offline": 0,
            "total": len(health_map),
        }

        for health in health_map.values():
            if health.state == ProviderState.HEALTHY:
                counts["healthy"] += 1
            elif health.state == ProviderState.DEGRADED:
                counts["degraded"] += 1
            else:
                counts["offline"] += 1

        # Determine overall status
        if counts["offline"] == counts["total"]:
            overall = "OFFLINE"
            overall_icon = "🔴"
        elif counts["healthy"] == counts["total"]:
            overall = "HEALTHY"
            overall_icon = "🟢"
        elif counts["healthy"] > 0:
            overall = "DEGRADED"
            overall_icon = "🟡"
        else:
            overall = "OFFLINE"
            overall_icon = "🔴"

        return {
            "counts": counts,
            "overall_status": overall,
            "overall_icon": overall_icon,
            "safe_mode": self.fabric.config.safe_mode,
        }

    # =========================================================================
    # Routing Summary
    # =========================================================================

    def get_routing_summary(self) -> Dict[str, Any]:
        """
        Get routing configuration summary.

        Returns:
            Routing summary dict
        """
        return self.fabric.get_routing_summary()

    def get_capability_providers(self) -> Dict[str, List[str]]:
        """
        Get mapping of capabilities to available providers.

        Returns:
            Dict mapping capability to list of provider IDs
        """
        routing = self.get_routing_summary()
        return routing.get("capabilities", {})

    def is_capability_available(self, capability: str) -> bool:
        """
        Check if a capability is available.

        Args:
            capability: Capability name

        Returns:
            True if capability has at least one healthy provider
        """
        try:
            cap = Capability(capability)
            return self.fabric.is_available(cap)
        except ValueError:
            return False

    # =========================================================================
    # Safe Mode
    # =========================================================================

    def is_safe_mode(self) -> bool:
        """Check if Safe Mode is enabled."""
        return self.fabric.config.safe_mode

    def enable_safe_mode(self) -> None:
        """
        Enable Safe Mode.

        In Safe Mode, only local_sandbox and ui_templates are used.
        All optional CLI providers and Antigravity are disabled.
        """
        self.fabric.enable_safe_mode()
        logger.info("Safe Mode enabled via Tools Panel")

    def disable_safe_mode(self) -> None:
        """Disable Safe Mode."""
        self.fabric.disable_safe_mode()
        logger.info("Safe Mode disabled via Tools Panel")

    def toggle_safe_mode(self) -> bool:
        """
        Toggle Safe Mode.

        Returns:
            New Safe Mode state
        """
        if self.is_safe_mode():
            self.disable_safe_mode()
            return False
        else:
            self.enable_safe_mode()
            return True

    # =========================================================================
    # Receipt Management
    # =========================================================================

    def add_receipt(self, receipt: ToolReceipt) -> None:
        """
        Add a receipt to the panel's store.

        Args:
            receipt: ToolReceipt to add
        """
        self._receipt_store.insert(0, receipt)

        # Trim to max size
        max_size = self._config.max_receipts_displayed * 2
        if len(self._receipt_store) > max_size:
            self._receipt_store = self._receipt_store[:max_size]

        # Notify callbacks
        for callback in self._receipt_callbacks:
            try:
                callback(receipt)
            except Exception as e:
                logger.warning("Receipt callback error: %s", e)

    def on_receipt(self, callback: Callable[[ToolReceipt], None]) -> None:
        """
        Register a callback for new receipts.

        Args:
            callback: Function to call with new receipts
        """
        self._receipt_callbacks.append(callback)

    def get_receipts(
        self,
        capability: Optional[str] = None,
        provider: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get tool receipts with optional filtering.

        Args:
            capability: Filter by capability
            provider: Filter by provider
            limit: Maximum receipts to return

        Returns:
            List of receipt dicts
        """
        limit = limit or self._config.max_receipts_displayed
        results = []

        for receipt in self._receipt_store:
            # Apply filters
            if capability and receipt.capability != capability:
                continue
            if provider and receipt.provider_id != provider:
                continue

            results.append(self._receipt_to_dict(receipt))

            if len(results) >= limit:
                break

        return results

    def _receipt_to_dict(self, receipt: ToolReceipt) -> Dict[str, Any]:
        """Convert receipt to display dict."""
        return {
            "receipt_id": receipt.receipt_id,
            "timestamp": receipt.timestamp,
            "capability": receipt.capability,
            "action": receipt.action,
            "provider_id": receipt.provider_id,
            "success": receipt.success,
            "duration_ms": receipt.duration_ms,
            "status_icon": "✅" if receipt.success else "❌",
            "error_message": receipt.error_message if not receipt.success else None,
        }

    def clear_receipts(self) -> None:
        """Clear all stored receipts."""
        self._receipt_store.clear()

    # =========================================================================
    # Panel Render Data
    # =========================================================================

    def render_data(
        self,
        capability_filter: Optional[str] = None,
        provider_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get all panel data for rendering.

        Args:
            capability_filter: Optional capability filter for receipts
            provider_filter: Optional provider filter for receipts

        Returns:
            Complete panel data dict
        """
        # Use config defaults if filters not provided
        cap_filter = capability_filter or self._config.default_capability_filter
        prov_filter = provider_filter or self._config.default_provider_filter

        return {
            "panel": "tool_fabric",
            "health_summary": self.get_health_summary(),
            "providers": self.get_provider_health_list(),
            "routing": self.get_routing_summary(),
            "capability_providers": self.get_capability_providers(),
            "receipts": self.get_receipts(
                capability=cap_filter,
                provider=prov_filter,
            ),
            "safe_mode": self.is_safe_mode(),
            "available_capabilities": [c.value for c in Capability],
        }


# =============================================================================
# Streamlit Render Function
# =============================================================================


def render_tools_panel(
    panel: Optional[ToolsPanel] = None,
    streamlit_module: Any = None,
) -> None:
    """
    Render the Tool Fabric panel in Streamlit.

    Args:
        panel: Optional ToolsPanel instance (creates one if not provided)
        streamlit_module: Streamlit module (import streamlit as st)
    """
    # Import streamlit if not provided
    if streamlit_module is None:
        import streamlit as streamlit_module
    st = streamlit_module

    # Create panel if not provided
    if panel is None:
        panel = ToolsPanel()

    # Get panel data
    data = panel.render_data()

    # Header
    st.header("Tool Fabric")

    # Health Summary Row
    summary = data["health_summary"]
    c1, c2, c3, c4 = st.columns(4)

    with c1:
        st.metric(
            label="Overall Status",
            value=f"{summary['overall_icon']} {summary['overall_status']}",
        )
    with c2:
        st.metric(
            label="Healthy",
            value=summary["counts"]["healthy"],
            delta=None,
        )
    with c3:
        st.metric(
            label="Degraded",
            value=summary["counts"]["degraded"],
            delta=None if summary["counts"]["degraded"] == 0 else "!",
            delta_color="off" if summary["counts"]["degraded"] == 0 else "inverse",
        )
    with c4:
        st.metric(
            label="Offline",
            value=summary["counts"]["offline"],
            delta=None if summary["counts"]["offline"] == 0 else "!",
            delta_color="off" if summary["counts"]["offline"] == 0 else "inverse",
        )

    st.divider()

    # Safe Mode Toggle
    col1, col2 = st.columns([3, 1])
    with col1:
        st.write("### Safe Mode")
        st.caption(
            "When enabled, only local_sandbox and ui_templates are used. "
            "All optional CLI providers and Antigravity are disabled."
        )
    with col2:
        safe_mode = data["safe_mode"]
        if st.button(
            "Disable Safe Mode" if safe_mode else "Enable Safe Mode",
            use_container_width=True,
            type="secondary" if safe_mode else "primary",
            key="safe_mode_toggle",
        ):
            panel.toggle_safe_mode()
            st.rerun()

        if safe_mode:
            st.warning("Safe Mode Active")

    st.divider()

    # Provider Health Section
    st.write("### Provider Health")

    # Refresh button
    if st.button("Refresh Health", key="refresh_health"):
        panel.probe_all_providers()
        st.rerun()

    # Provider list
    providers = data["providers"]
    if not providers:
        st.info("No providers registered")
    else:
        for prov in providers:
            with st.expander(
                f"{prov['state_icon']} {prov['provider_id']} ({prov['state']})",
                expanded=prov["state"] != "healthy",
            ):
                st.write(f"**Version:** {prov['version']}")
                if prov["capabilities"]:
                    st.write(f"**Capabilities:** {', '.join(prov['capabilities'])}")
                if prov["error_message"]:
                    st.error(f"Error: {prov['error_message']}")
                if prov.get("metadata"):
                    st.json(prov["metadata"])

                # Individual probe button
                if st.button(f"Probe {prov['provider_id']}", key=f"probe_{prov['provider_id']}"):
                    panel.probe_provider(prov["provider_id"])
                    st.rerun()

    st.divider()

    # Routing Summary
    st.write("### Routing Policy")
    routing = data["routing"]

    cap_provs = data["capability_providers"]
    if cap_provs:
        for cap, provs in cap_provs.items():
            available = panel.is_capability_available(cap)
            icon = "✅" if available else "❌"
            st.write(f"**{cap}:** {icon} → {', '.join(provs) if provs else 'None'}")
    else:
        st.info("No routing data available")

    st.divider()

    # Recent Receipts
    st.write("### Recent Tool Operations")

    # Filters
    filter_col1, filter_col2 = st.columns(2)
    with filter_col1:
        cap_options = ["All"] + data["available_capabilities"]
        selected_cap = st.selectbox("Filter by Capability", cap_options, key="cap_filter")
    with filter_col2:
        prov_options = ["All"] + [p["provider_id"] for p in providers]
        selected_prov = st.selectbox("Filter by Provider", prov_options, key="prov_filter")

    # Get filtered receipts
    receipts = panel.get_receipts(
        capability=None if selected_cap == "All" else selected_cap,
        provider=None if selected_prov == "All" else selected_prov,
    )

    if not receipts:
        st.info("No tool operations recorded")
    else:
        for r in receipts:
            with st.expander(
                f"{r['status_icon']} [{r['timestamp'][:19]}] {r['capability']}/{r['action']}",
                expanded=False,
            ):
                st.write(f"**Provider:** {r['provider_id']}")
                st.write(f"**Duration:** {r['duration_ms']}ms")
                if r.get("error_message"):
                    st.error(r["error_message"])


# =============================================================================
# Global Panel Instance
# =============================================================================


_tools_panel: Optional[ToolsPanel] = None


def get_tools_panel(
    fabric: Optional[ToolFabric] = None,
    config: Optional[ToolsPanelConfig] = None,
) -> ToolsPanel:
    """
    Get the global ToolsPanel instance.

    Args:
        fabric: Optional ToolFabric instance
        config: Optional panel configuration

    Returns:
        Global ToolsPanel instance
    """
    global _tools_panel
    if _tools_panel is None:
        _tools_panel = ToolsPanel(fabric=fabric, config=config)
    return _tools_panel


def reset_tools_panel() -> None:
    """Reset the global tools panel (for testing)."""
    global _tools_panel
    _tools_panel = None
