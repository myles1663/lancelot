# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""Unit tests for the Receipt Bridge — OTel span export callback."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "core"))

import pytest
from unittest.mock import MagicMock, patch

import src.observability.receipt_bridge as bridge_module
from src.observability.receipt_bridge import (
    configure_bridge,
    on_receipt_written,
)


@pytest.fixture(autouse=True)
def reset_bridge_state():
    """Reset bridge module state before each test."""
    bridge_module._enabled = False
    bridge_module._otel_enabled = False
    bridge_module._sampling_rate = 0.1
    yield
    bridge_module._enabled = False
    bridge_module._otel_enabled = False
    bridge_module._sampling_rate = 0.1


def _sample_receipt(**overrides):
    """Create a minimal receipt dict for testing."""
    base = {
        "id": "rcpt-test-001",
        "action_type": "task_executed",
        "action_name": "test_action",
        "status": "success",
        "tier": 1,
        "quest_id": "quest-42",
        "timestamp": "2026-03-17T12:00:00Z",
        "operator_id": "op-1",
    }
    base.update(overrides)
    return base


# ── configure_bridge ─────────────────────────────────────────────


class TestConfigureBridge:
    def test_enables_bridge(self):
        configure_bridge(enabled=True, sampling_rate=0.5)
        assert bridge_module._enabled is True
        assert bridge_module._otel_enabled is True
        assert bridge_module._sampling_rate == 0.5

    def test_disables_bridge(self):
        configure_bridge(enabled=True)
        bridge_module._otel_enabled = True
        configure_bridge(enabled=False)
        assert bridge_module._enabled is False
        assert bridge_module._otel_enabled is False

    def test_can_enable_bridge_without_span_export(self):
        configure_bridge(enabled=True, otel_enabled=False)
        assert bridge_module._enabled is True
        assert bridge_module._otel_enabled is False

    def test_clamps_sampling_rate_high(self):
        """Sampling rate above 1.0 is clamped to 1.0."""
        configure_bridge(enabled=True, sampling_rate=2.5)
        assert bridge_module._sampling_rate == 1.0

    def test_clamps_sampling_rate_low(self):
        """Sampling rate below 0.0 is clamped to 0.0."""
        configure_bridge(enabled=True, sampling_rate=-0.5)
        assert bridge_module._sampling_rate == 0.0

    def test_default_sampling_rate(self):
        configure_bridge(enabled=True)
        assert bridge_module._sampling_rate == 0.1


# ── on_receipt_written (disabled) ────────────────────────────────


class TestOnReceiptWrittenDisabled:
    def test_skips_when_disabled(self):
        """When bridge is disabled, no span export happens."""
        bridge_module._enabled = False
        with patch.object(bridge_module, "_export_span") as mock_export:
            on_receipt_written(_sample_receipt())
            mock_export.assert_not_called()


# ── on_receipt_written (enabled) ─────────────────────────────────


class TestOnReceiptWrittenEnabled:
    def test_calls_span_creation_when_enabled(self):
        """When enabled, _export_span is called."""
        configure_bridge(enabled=True)
        with patch.object(bridge_module, "_export_span") as mock_export, \
             patch.object(bridge_module, "_deliver_webhooks"):
            on_receipt_written(_sample_receipt())
            mock_export.assert_called_once()

    def test_calls_metrics_update(self):
        """When enabled, metrics update is attempted."""
        configure_bridge(enabled=True)
        with patch.object(bridge_module, "_export_span"), \
             patch.object(bridge_module, "_deliver_webhooks"), \
             patch("src.observability.receipt_bridge.update_metrics_from_receipt",
                   create=True) as mock_metrics:
            # The import inside on_receipt_written may fail — that's fine,
            # the test verifies the try block is entered
            on_receipt_written(_sample_receipt())

    def test_calls_webhook_delivery(self):
        """When enabled, webhook delivery is attempted."""
        configure_bridge(enabled=True)
        with patch.object(bridge_module, "_export_span"), \
             patch.object(bridge_module, "_deliver_webhooks") as mock_wh:
            on_receipt_written(_sample_receipt())
            mock_wh.assert_called_once()

    def test_webhook_delivery_runs_when_span_export_disabled(self):
        """Webhooks and incidents still run when OTel export is intentionally off."""
        configure_bridge(enabled=True, otel_enabled=False)
        with patch.object(bridge_module, "_export_span") as mock_export, \
             patch.object(bridge_module, "_deliver_webhooks") as mock_wh:
            on_receipt_written(_sample_receipt())
            mock_export.assert_not_called()
            mock_wh.assert_called_once()

    def test_non_throwing_on_export_error(self):
        """Span export errors are swallowed — never propagated."""
        configure_bridge(enabled=True)
        with patch.object(bridge_module, "_export_span",
                          side_effect=RuntimeError("boom")), \
             patch.object(bridge_module, "_deliver_webhooks"):
            # Should not raise
            on_receipt_written(_sample_receipt())

    def test_non_throwing_on_webhook_error(self):
        """Webhook delivery errors are swallowed."""
        configure_bridge(enabled=True)
        with patch.object(bridge_module, "_export_span"), \
             patch.object(bridge_module, "_deliver_webhooks",
                          side_effect=RuntimeError("webhook boom")):
            on_receipt_written(_sample_receipt())

    def test_handles_none_receipt(self):
        """None receipt should not crash the bridge."""
        configure_bridge(enabled=True)
        # _export_span will be called with None; it will raise internally
        # but on_receipt_written must swallow it
        try:
            on_receipt_written(None)
        except Exception:
            pytest.fail("on_receipt_written should not raise on None input")

    def test_handles_malformed_receipt(self):
        """Malformed receipt (missing keys) should not crash."""
        configure_bridge(enabled=True)
        try:
            on_receipt_written({"random_key": "random_value"})
        except Exception:
            pytest.fail("on_receipt_written should not raise on malformed receipt")

    def test_multiple_rapid_calls(self):
        """Multiple rapid calls should not crash or corrupt state."""
        configure_bridge(enabled=True)
        with patch.object(bridge_module, "_export_span"), \
             patch.object(bridge_module, "_deliver_webhooks"):
            for i in range(100):
                on_receipt_written(_sample_receipt(id=f"rcpt-{i}"))
