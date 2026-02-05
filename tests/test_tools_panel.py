"""
Tests for Tool Fabric Panel (Prompt 10)
=======================================

Tests the War Room Tool Fabric panel including:
- Provider health display
- Routing policy summary
- Receipt filtering
- Safe Mode toggle
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from typing import Dict, Any, List

from src.tools.contracts import (
    Capability,
    ProviderHealth,
    ProviderState,
)
from src.tools.receipts import ToolReceipt, create_tool_receipt
from src.ui.panels.tools_panel import (
    ToolsPanel,
    ToolsPanelConfig,
    get_tools_panel,
    reset_tools_panel,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_fabric():
    """Create a mock ToolFabric for testing."""
    fabric = MagicMock()

    # Mock config
    fabric.config = MagicMock()
    fabric.config.safe_mode = False

    # Mock health data
    fabric.get_health.return_value = {
        "local_sandbox": ProviderHealth(
            provider_id="local_sandbox",
            state=ProviderState.HEALTHY,
            version="1.0.0",
            capabilities=["shell_exec", "repo_ops", "file_ops"],
        ),
        "ui_templates": ProviderHealth(
            provider_id="ui_templates",
            state=ProviderState.HEALTHY,
            version="1.0.0",
            capabilities=["ui_builder"],
        ),
        "ui_antigravity": ProviderHealth(
            provider_id="ui_antigravity",
            state=ProviderState.OFFLINE,
            error_message="Antigravity not available",
            capabilities=["ui_builder"],
        ),
    }

    # Mock probe_health
    fabric.probe_health.return_value = fabric.get_health.return_value

    # Mock routing summary
    fabric.get_routing_summary.return_value = {
        "capability_providers": {
            "shell_exec": ["local_sandbox"],
            "repo_ops": ["local_sandbox"],
            "file_ops": ["local_sandbox"],
            "ui_builder": ["ui_templates", "ui_antigravity"],
        },
        "safe_mode": False,
    }

    # Mock is_available
    def is_available(cap):
        available_caps = {
            Capability.SHELL_EXEC: True,
            Capability.REPO_OPS: True,
            Capability.FILE_OPS: True,
            Capability.UI_BUILDER: True,
            Capability.VISION_CONTROL: False,
        }
        return available_caps.get(cap, False)

    fabric.is_available.side_effect = is_available

    return fabric


@pytest.fixture
def panel(mock_fabric):
    """Create a ToolsPanel with mock fabric."""
    return ToolsPanel(fabric=mock_fabric)


@pytest.fixture
def panel_with_config(mock_fabric):
    """Create a ToolsPanel with custom config."""
    config = ToolsPanelConfig(
        max_receipts_displayed=10,
        show_offline_providers=False,
        show_provider_metadata=True,
    )
    return ToolsPanel(fabric=mock_fabric, config=config)


@pytest.fixture
def sample_receipts():
    """Create sample receipts for testing."""
    receipts = []

    # Successful shell exec receipt
    r1 = create_tool_receipt(
        capability=Capability.SHELL_EXEC,
        action="run",
        provider_id="local_sandbox",
        workspace="/project",
        inputs={"command": "git status"},
    )
    r1.success = True
    r1.duration_ms = 150
    receipts.append(r1)

    # Failed file ops receipt
    r2 = create_tool_receipt(
        capability=Capability.FILE_OPS,
        action="read",
        provider_id="local_sandbox",
        workspace="/project",
        inputs={"path": "/etc/passwd"},
    )
    r2.success = False
    r2.error_message = "Path traversal blocked"
    r2.duration_ms = 5
    receipts.append(r2)

    # UI builder receipt
    r3 = create_tool_receipt(
        capability=Capability.UI_BUILDER,
        action="scaffold",
        provider_id="ui_templates",
        workspace="/project",
        inputs={"template_id": "fastapi_service"},
    )
    r3.success = True
    r3.duration_ms = 500
    receipts.append(r3)

    return receipts


# =============================================================================
# Provider Health Tests
# =============================================================================


class TestProviderHealth:
    """Tests for provider health functionality."""

    def test_get_provider_health(self, panel, mock_fabric):
        """Test getting provider health."""
        health = panel.get_provider_health()

        assert "local_sandbox" in health
        assert health["local_sandbox"].state == ProviderState.HEALTHY

    def test_get_provider_health_list(self, panel):
        """Test getting provider health as list."""
        health_list = panel.get_provider_health_list()

        assert len(health_list) == 3
        assert all("provider_id" in p for p in health_list)
        assert all("state" in p for p in health_list)
        assert all("state_icon" in p for p in health_list)

    def test_provider_health_list_sorted(self, panel):
        """Test that provider list is sorted by ID."""
        health_list = panel.get_provider_health_list()

        provider_ids = [p["provider_id"] for p in health_list]
        assert provider_ids == sorted(provider_ids)

    def test_state_icon_healthy(self, panel):
        """Test healthy state icon."""
        assert panel._state_icon(ProviderState.HEALTHY) == "🟢"

    def test_state_icon_degraded(self, panel):
        """Test degraded state icon."""
        assert panel._state_icon(ProviderState.DEGRADED) == "🟡"

    def test_state_icon_offline(self, panel):
        """Test offline state icon."""
        assert panel._state_icon(ProviderState.OFFLINE) == "🔴"

    def test_hide_offline_providers(self, panel_with_config):
        """Test hiding offline providers when configured."""
        health_list = panel_with_config.get_provider_health_list()

        provider_ids = [p["provider_id"] for p in health_list]
        assert "ui_antigravity" not in provider_ids
        assert "local_sandbox" in provider_ids

    def test_probe_provider(self, panel, mock_fabric):
        """Test probing a specific provider."""
        health = panel.probe_provider("local_sandbox")

        mock_fabric.probe_health.assert_called_once_with("local_sandbox")
        assert health.state == ProviderState.HEALTHY

    def test_probe_all_providers(self, panel, mock_fabric):
        """Test probing all providers."""
        health = panel.probe_all_providers()

        mock_fabric.probe_health.assert_called_once()
        assert len(health) == 3


# =============================================================================
# Health Summary Tests
# =============================================================================


class TestHealthSummary:
    """Tests for health summary functionality."""

    def test_get_health_summary(self, panel):
        """Test getting health summary."""
        summary = panel.get_health_summary()

        assert "counts" in summary
        assert "overall_status" in summary
        assert "overall_icon" in summary
        assert "safe_mode" in summary

    def test_health_counts(self, panel):
        """Test health counts are correct."""
        summary = panel.get_health_summary()

        counts = summary["counts"]
        assert counts["healthy"] == 2
        assert counts["degraded"] == 0
        assert counts["offline"] == 1
        assert counts["total"] == 3

    def test_overall_status_degraded(self, panel):
        """Test overall status with offline provider."""
        summary = panel.get_health_summary()

        # With 2 healthy, 0 degraded, 1 offline → DEGRADED
        assert summary["overall_status"] == "DEGRADED"
        assert summary["overall_icon"] == "🟡"

    def test_overall_status_all_healthy(self, mock_fabric):
        """Test overall status when all healthy."""
        mock_fabric.get_health.return_value = {
            "local_sandbox": ProviderHealth(
                provider_id="local_sandbox",
                state=ProviderState.HEALTHY,
            ),
        }
        panel = ToolsPanel(fabric=mock_fabric)

        summary = panel.get_health_summary()
        assert summary["overall_status"] == "HEALTHY"
        assert summary["overall_icon"] == "🟢"

    def test_overall_status_all_offline(self, mock_fabric):
        """Test overall status when all offline."""
        mock_fabric.get_health.return_value = {
            "provider1": ProviderHealth(
                provider_id="provider1",
                state=ProviderState.OFFLINE,
            ),
        }
        panel = ToolsPanel(fabric=mock_fabric)

        summary = panel.get_health_summary()
        assert summary["overall_status"] == "OFFLINE"
        assert summary["overall_icon"] == "🔴"


# =============================================================================
# Routing Summary Tests
# =============================================================================


class TestRoutingSummary:
    """Tests for routing summary functionality."""

    def test_get_routing_summary(self, panel, mock_fabric):
        """Test getting routing summary."""
        summary = panel.get_routing_summary()

        mock_fabric.get_routing_summary.assert_called_once()
        assert "capability_providers" in summary

    def test_get_capability_providers(self, panel):
        """Test getting capability providers."""
        cap_provs = panel.get_capability_providers()

        assert "shell_exec" in cap_provs
        assert "local_sandbox" in cap_provs["shell_exec"]

    def test_is_capability_available(self, panel):
        """Test checking capability availability."""
        assert panel.is_capability_available("shell_exec") is True
        assert panel.is_capability_available("vision_control") is False

    def test_is_capability_available_invalid(self, panel):
        """Test invalid capability name."""
        assert panel.is_capability_available("invalid_cap") is False


# =============================================================================
# Safe Mode Tests
# =============================================================================


class TestSafeMode:
    """Tests for Safe Mode functionality."""

    def test_is_safe_mode(self, panel, mock_fabric):
        """Test checking Safe Mode status."""
        mock_fabric.config.safe_mode = False
        assert panel.is_safe_mode() is False

        mock_fabric.config.safe_mode = True
        assert panel.is_safe_mode() is True

    def test_enable_safe_mode(self, panel, mock_fabric):
        """Test enabling Safe Mode."""
        panel.enable_safe_mode()

        mock_fabric.enable_safe_mode.assert_called_once()

    def test_disable_safe_mode(self, panel, mock_fabric):
        """Test disabling Safe Mode."""
        panel.disable_safe_mode()

        mock_fabric.disable_safe_mode.assert_called_once()

    def test_toggle_safe_mode_on(self, panel, mock_fabric):
        """Test toggling Safe Mode on."""
        mock_fabric.config.safe_mode = False

        result = panel.toggle_safe_mode()

        assert result is True
        mock_fabric.enable_safe_mode.assert_called_once()

    def test_toggle_safe_mode_off(self, panel, mock_fabric):
        """Test toggling Safe Mode off."""
        mock_fabric.config.safe_mode = True

        result = panel.toggle_safe_mode()

        assert result is False
        mock_fabric.disable_safe_mode.assert_called_once()


# =============================================================================
# Receipt Management Tests
# =============================================================================


class TestReceiptManagement:
    """Tests for receipt management functionality."""

    def test_add_receipt(self, panel, sample_receipts):
        """Test adding a receipt."""
        panel.add_receipt(sample_receipts[0])

        receipts = panel.get_receipts()
        assert len(receipts) == 1

    def test_add_multiple_receipts(self, panel, sample_receipts):
        """Test adding multiple receipts."""
        for r in sample_receipts:
            panel.add_receipt(r)

        receipts = panel.get_receipts()
        assert len(receipts) == 3

    def test_receipts_ordered_newest_first(self, panel, sample_receipts):
        """Test that receipts are ordered newest first."""
        for r in sample_receipts:
            panel.add_receipt(r)

        receipts = panel.get_receipts()
        # Last added should be first
        assert receipts[0]["capability"] == "ui_builder"

    def test_filter_receipts_by_capability(self, panel, sample_receipts):
        """Test filtering receipts by capability."""
        for r in sample_receipts:
            panel.add_receipt(r)

        receipts = panel.get_receipts(capability="shell_exec")
        assert len(receipts) == 1
        assert receipts[0]["capability"] == "shell_exec"

    def test_filter_receipts_by_provider(self, panel, sample_receipts):
        """Test filtering receipts by provider."""
        for r in sample_receipts:
            panel.add_receipt(r)

        receipts = panel.get_receipts(provider="ui_templates")
        assert len(receipts) == 1
        assert receipts[0]["provider_id"] == "ui_templates"

    def test_filter_receipts_combined(self, panel, sample_receipts):
        """Test filtering receipts by both capability and provider."""
        for r in sample_receipts:
            panel.add_receipt(r)

        receipts = panel.get_receipts(capability="shell_exec", provider="local_sandbox")
        assert len(receipts) == 1

    def test_receipt_limit(self, panel, sample_receipts):
        """Test receipt limit."""
        for r in sample_receipts:
            panel.add_receipt(r)

        receipts = panel.get_receipts(limit=2)
        assert len(receipts) == 2

    def test_clear_receipts(self, panel, sample_receipts):
        """Test clearing receipts."""
        for r in sample_receipts:
            panel.add_receipt(r)

        panel.clear_receipts()

        receipts = panel.get_receipts()
        assert len(receipts) == 0

    def test_receipt_callback(self, panel, sample_receipts):
        """Test receipt callback."""
        received = []
        panel.on_receipt(lambda r: received.append(r))

        panel.add_receipt(sample_receipts[0])

        assert len(received) == 1
        assert received[0] == sample_receipts[0]

    def test_receipt_to_dict(self, panel, sample_receipts):
        """Test receipt conversion to dict."""
        panel.add_receipt(sample_receipts[0])

        receipts = panel.get_receipts()
        r = receipts[0]

        assert "receipt_id" in r
        assert "timestamp" in r
        assert "capability" in r
        assert "action" in r
        assert "provider_id" in r
        assert "success" in r
        assert "duration_ms" in r
        assert "status_icon" in r


# =============================================================================
# Render Data Tests
# =============================================================================


class TestRenderData:
    """Tests for render_data functionality."""

    def test_render_data_structure(self, panel):
        """Test render data structure."""
        data = panel.render_data()

        assert data["panel"] == "tool_fabric"
        assert "health_summary" in data
        assert "providers" in data
        assert "routing" in data
        assert "capability_providers" in data
        assert "receipts" in data
        assert "safe_mode" in data
        assert "available_capabilities" in data

    def test_render_data_with_filters(self, panel, sample_receipts):
        """Test render data with filters."""
        for r in sample_receipts:
            panel.add_receipt(r)

        data = panel.render_data(
            capability_filter="shell_exec",
            provider_filter="local_sandbox",
        )

        assert len(data["receipts"]) == 1

    def test_available_capabilities_list(self, panel):
        """Test that available capabilities are listed."""
        data = panel.render_data()

        caps = data["available_capabilities"]
        assert "shell_exec" in caps
        assert "repo_ops" in caps
        assert "ui_builder" in caps


# =============================================================================
# Config Tests
# =============================================================================


class TestPanelConfig:
    """Tests for panel configuration."""

    def test_default_config(self):
        """Test default configuration values."""
        config = ToolsPanelConfig()

        assert config.max_receipts_displayed == 20
        assert config.show_receipt_details is True
        assert config.auto_refresh_interval_s == 30
        assert config.show_offline_providers is True
        assert config.show_provider_metadata is False

    def test_custom_config(self):
        """Test custom configuration."""
        config = ToolsPanelConfig(
            max_receipts_displayed=50,
            show_offline_providers=False,
        )

        assert config.max_receipts_displayed == 50
        assert config.show_offline_providers is False


# =============================================================================
# Global Instance Tests
# =============================================================================


class TestGlobalInstance:
    """Tests for global panel instance."""

    def test_get_tools_panel_creates_instance(self, mock_fabric):
        """Test that get_tools_panel creates an instance."""
        reset_tools_panel()

        panel = get_tools_panel(fabric=mock_fabric)
        assert panel is not None
        assert isinstance(panel, ToolsPanel)

    def test_get_tools_panel_returns_same_instance(self, mock_fabric):
        """Test that get_tools_panel returns the same instance."""
        reset_tools_panel()

        panel1 = get_tools_panel(fabric=mock_fabric)
        panel2 = get_tools_panel()

        assert panel1 is panel2

    def test_reset_tools_panel(self, mock_fabric):
        """Test resetting the global panel."""
        reset_tools_panel()

        panel1 = get_tools_panel(fabric=mock_fabric)
        reset_tools_panel()
        panel2 = get_tools_panel(fabric=mock_fabric)

        assert panel1 is not panel2


# =============================================================================
# Integration Tests
# =============================================================================


class TestIntegration:
    """Integration tests with real components (where possible)."""

    def test_panel_with_no_providers(self):
        """Test panel with empty fabric."""
        mock_fabric = MagicMock()
        mock_fabric.config.safe_mode = False
        mock_fabric.get_health.return_value = {}
        mock_fabric.get_routing_summary.return_value = {"capability_providers": {}}

        panel = ToolsPanel(fabric=mock_fabric)
        data = panel.render_data()

        assert data["health_summary"]["counts"]["total"] == 0
        assert len(data["providers"]) == 0

    def test_panel_preserves_receipts_on_fabric_reset(self, mock_fabric, sample_receipts):
        """Test that panel preserves receipts when fabric is probed."""
        panel = ToolsPanel(fabric=mock_fabric)

        for r in sample_receipts:
            panel.add_receipt(r)

        # Probe all should not affect receipts
        panel.probe_all_providers()

        receipts = panel.get_receipts()
        assert len(receipts) == 3

    def test_render_data_is_json_serializable(self, panel, sample_receipts):
        """Test that render data can be JSON serialized."""
        import json

        for r in sample_receipts:
            panel.add_receipt(r)

        data = panel.render_data()

        # Should not raise
        json_str = json.dumps(data, default=str)
        assert isinstance(json_str, str)


# =============================================================================
# Streamlit Render Tests (Mock)
# =============================================================================


class TestStreamlitRender:
    """Tests for Streamlit rendering (using mocks)."""

    def test_render_tools_panel_calls_streamlit(self, panel):
        """Test that render_tools_panel calls streamlit functions."""
        mock_st = MagicMock()

        # Mock columns to return correct number based on call
        def columns_side_effect(specs):
            if isinstance(specs, int):
                return [MagicMock() for _ in range(specs)]
            return [MagicMock() for _ in range(len(specs))]

        mock_st.columns.side_effect = columns_side_effect
        mock_st.selectbox.return_value = "All"
        mock_st.button.return_value = False
        mock_st.expander.return_value.__enter__ = MagicMock()
        mock_st.expander.return_value.__exit__ = MagicMock()

        from src.ui.panels.tools_panel import render_tools_panel
        render_tools_panel(panel=panel, streamlit_module=mock_st)

        # Verify key streamlit calls
        mock_st.header.assert_called_with("Tool Fabric")
        mock_st.columns.assert_called()
        mock_st.divider.assert_called()


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_receipts(self, panel):
        """Test getting receipts when none exist."""
        receipts = panel.get_receipts()
        assert receipts == []

    def test_filter_with_no_matches(self, panel, sample_receipts):
        """Test filtering with no matches."""
        for r in sample_receipts:
            panel.add_receipt(r)

        receipts = panel.get_receipts(capability="vision_control")
        assert receipts == []

    def test_receipt_callback_error_handled(self, panel, sample_receipts):
        """Test that callback errors are handled."""
        def bad_callback(r):
            raise ValueError("Test error")

        panel.on_receipt(bad_callback)

        # Should not raise
        panel.add_receipt(sample_receipts[0])

        receipts = panel.get_receipts()
        assert len(receipts) == 1

    def test_probe_unknown_provider(self, panel, mock_fabric):
        """Test probing unknown provider."""
        mock_fabric.probe_health.return_value = {}

        health = panel.probe_provider("unknown_provider")

        assert health.state == ProviderState.OFFLINE
        assert "not found" in health.error_message

    def test_max_receipts_trimmed(self, panel, sample_receipts):
        """Test that receipts are trimmed when over max."""
        panel._config.max_receipts_displayed = 2

        # Add more than max
        for _ in range(50):
            panel.add_receipt(sample_receipts[0])

        # Internal store trimmed to 2x max
        assert len(panel._receipt_store) <= 4
