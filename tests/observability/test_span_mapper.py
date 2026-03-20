# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""Unit tests for the Receipt-to-Span Mapper."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "core"))

import pytest
from unittest.mock import patch

from src.observability.span_mapper import (
    _deterministic_trace_id,
    _deterministic_span_id,
    span_name,
    is_error_receipt,
    receipt_to_span_attrs,
    should_sample,
    should_export,
    ALWAYS_EXPORT_TYPES,
)


# ── _deterministic_trace_id ──────────────────────────────────────


class TestDeterministicTraceId:
    def test_same_input_same_output(self):
        """Same quest_id always produces the same trace_id."""
        result1 = _deterministic_trace_id("quest-abc-123")
        result2 = _deterministic_trace_id("quest-abc-123")
        assert result1 == result2

    def test_different_inputs_different_outputs(self):
        """Different quest_ids produce different trace_ids."""
        result1 = _deterministic_trace_id("quest-abc-123")
        result2 = _deterministic_trace_id("quest-xyz-456")
        assert result1 != result2

    def test_returns_16_bytes(self):
        """Trace ID must be exactly 16 bytes per OTel spec."""
        result = _deterministic_trace_id("any-quest-id")
        assert isinstance(result, bytes)
        assert len(result) == 16

    def test_empty_string_input(self):
        """Empty quest_id should still produce a valid 16-byte ID."""
        result = _deterministic_trace_id("")
        assert isinstance(result, bytes)
        assert len(result) == 16

    def test_unicode_input(self):
        """Unicode quest_id should hash correctly."""
        result = _deterministic_trace_id("quest-日本語-テスト")
        assert isinstance(result, bytes)
        assert len(result) == 16


# ── _deterministic_span_id ───────────────────────────────────────


class TestDeterministicSpanId:
    def test_same_input_same_output(self):
        """Same receipt_id always produces the same span_id."""
        result1 = _deterministic_span_id("receipt-001")
        result2 = _deterministic_span_id("receipt-001")
        assert result1 == result2

    def test_different_inputs_different_outputs(self):
        """Different receipt_ids produce different span_ids."""
        result1 = _deterministic_span_id("receipt-001")
        result2 = _deterministic_span_id("receipt-002")
        assert result1 != result2

    def test_returns_8_bytes(self):
        """Span ID must be exactly 8 bytes per OTel spec."""
        result = _deterministic_span_id("any-receipt-id")
        assert isinstance(result, bytes)
        assert len(result) == 8

    def test_trace_and_span_ids_differ_for_same_input(self):
        """trace_id and span_id for the same string differ (different lengths)."""
        trace_id = _deterministic_trace_id("shared-id")
        span_id = _deterministic_span_id("shared-id")
        # They're different lengths so can't be equal, but the first 8 bytes
        # of the trace_id should equal the span_id (both from SHA-256)
        assert trace_id[:8] == span_id


# ── span_name ────────────────────────────────────────────────────


class TestSpanName:
    def test_formats_correctly(self):
        """Produces 'lancelot.{type}' format."""
        assert span_name("task_executed") == "lancelot.task_executed"

    def test_lowercases_action_type(self):
        """Action type is lowercased in the span name."""
        assert span_name("KILL_SWITCH_ISSUED") == "lancelot.kill_switch_issued"

    def test_mixed_case(self):
        assert span_name("T3_Approved") == "lancelot.t3_approved"

    def test_empty_action_type(self):
        assert span_name("") == "lancelot."


# ── is_error_receipt ─────────────────────────────────────────────


class TestIsErrorReceipt:
    def test_blocked_prefix_detected(self):
        """BLOCKED_ prefix produces an error."""
        assert is_error_receipt("blocked_by_soul", "success") is True

    def test_blocked_prefix_case_insensitive(self):
        assert is_error_receipt("BLOCKED_MCP_TOOL", "success") is True

    def test_governance_write_error_detected(self):
        assert is_error_receipt("governance_write_error", "success") is True

    def test_failure_status_detected(self):
        """'failure' status produces an error regardless of action type."""
        assert is_error_receipt("task_executed", "failure") is True

    def test_normal_receipt_not_error(self):
        """Normal receipts are not errors."""
        assert is_error_receipt("task_executed", "success") is False

    def test_approved_not_error(self):
        assert is_error_receipt("t3_approved", "approved") is False


# ── receipt_to_span_attrs ────────────────────────────────────────


class TestReceiptToSpanAttrs:
    def _make_receipt(self, **overrides):
        base = {
            "id": "rcpt-001",
            "action_type": "task_executed",
            "action_name": "run_query",
            "status": "success",
            "tier": 1,
            "operator_id": "op-42",
            "session_id": "sess-99",
            "quest_id": "quest-7",
            "duration_ms": 150,
            "token_count": 500,
            "error_message": None,
            "metadata": {"soul_version": "v1.2.3"},
        }
        base.update(overrides)
        return base

    def test_extracts_core_attributes(self):
        attrs = receipt_to_span_attrs(self._make_receipt())
        assert attrs["lancelot.receipt_id"] == "rcpt-001"
        assert attrs["lancelot.action_type"] == "task_executed"
        assert attrs["lancelot.action_name"] == "run_query"
        assert attrs["lancelot.status"] == "success"

    def test_risk_tier_label(self):
        attrs = receipt_to_span_attrs(self._make_receipt(tier=2))
        assert attrs["lancelot.risk_tier"] == "T2"

    def test_risk_tier_unknown(self):
        """Tiers outside 0-3 get 'T{n}' format."""
        attrs = receipt_to_span_attrs(self._make_receipt(tier=5))
        assert attrs["lancelot.risk_tier"] == "T5"

    def test_operator_id_included(self):
        attrs = receipt_to_span_attrs(self._make_receipt())
        assert attrs["lancelot.operator_id"] == "op-42"

    def test_session_id_included(self):
        attrs = receipt_to_span_attrs(self._make_receipt())
        assert attrs["lancelot.session_id"] == "sess-99"

    def test_quest_id_included(self):
        attrs = receipt_to_span_attrs(self._make_receipt())
        assert attrs["lancelot.quest_id"] == "quest-7"

    def test_duration_ms_included(self):
        attrs = receipt_to_span_attrs(self._make_receipt())
        assert attrs["lancelot.duration_ms"] == 150

    def test_token_count_included(self):
        attrs = receipt_to_span_attrs(self._make_receipt())
        assert attrs["lancelot.token_count"] == 500

    def test_soul_version_from_metadata(self):
        attrs = receipt_to_span_attrs(self._make_receipt())
        assert attrs["lancelot.soul_version"] == "v1.2.3"

    def test_missing_optional_fields(self):
        """Missing optional fields are simply omitted from attrs."""
        receipt = {
            "id": "rcpt-minimal",
            "action_type": "ping",
            "action_name": "ping",
            "status": "success",
        }
        attrs = receipt_to_span_attrs(receipt)
        assert attrs["lancelot.receipt_id"] == "rcpt-minimal"
        assert "lancelot.operator_id" not in attrs
        assert "lancelot.session_id" not in attrs
        assert "lancelot.quest_id" not in attrs
        assert "lancelot.duration_ms" not in attrs
        assert "lancelot.token_count" not in attrs
        assert "lancelot.soul_version" not in attrs

    def test_empty_dict(self):
        """Empty receipt dict should not raise."""
        attrs = receipt_to_span_attrs({})
        assert attrs["lancelot.receipt_id"] == ""
        assert attrs["lancelot.risk_tier"] == "T0"

    def test_metadata_none(self):
        """None metadata should not raise."""
        attrs = receipt_to_span_attrs(self._make_receipt(metadata=None))
        assert "lancelot.soul_version" not in attrs

    def test_metadata_not_dict(self):
        """Non-dict metadata should not raise."""
        attrs = receipt_to_span_attrs(self._make_receipt(metadata="invalid"))
        assert "lancelot.soul_version" not in attrs

    def test_error_message_included_when_present(self):
        attrs = receipt_to_span_attrs(self._make_receipt(error_message="boom"))
        assert attrs["lancelot.error_message"] == "boom"

    def test_error_message_omitted_when_none(self):
        attrs = receipt_to_span_attrs(self._make_receipt(error_message=None))
        assert "lancelot.error_message" not in attrs


# ── should_sample ────────────────────────────────────────────────


class TestShouldSample:
    def test_t2_always_true(self):
        """T2 always sampled regardless of rate."""
        assert should_sample(2, 0.0) is True

    def test_t3_always_true(self):
        """T3 always sampled regardless of rate."""
        assert should_sample(3, 0.0) is True

    def test_t0_rate_zero_never_sampled(self):
        """T0 with 0.0 rate should never be sampled."""
        with patch("random.random", return_value=0.5):
            assert should_sample(0, 0.0) is False

    def test_t1_rate_zero_never_sampled(self):
        """T1 with 0.0 rate should never be sampled."""
        with patch("random.random", return_value=0.5):
            assert should_sample(1, 0.0) is False

    def test_t0_rate_one_always_sampled(self):
        """T0 with 1.0 rate should always be sampled."""
        with patch("random.random", return_value=0.5):
            assert should_sample(0, 1.0) is True

    def test_t1_respects_sampling_rate(self):
        """T1 with 0.5 rate: random=0.3 → True, random=0.7 → False."""
        with patch("random.random", return_value=0.3):
            assert should_sample(1, 0.5) is True

        with patch("random.random", return_value=0.7):
            assert should_sample(1, 0.5) is False


# ── should_export ────────────────────────────────────────────────


class TestShouldExport:
    def test_governance_event_always_exported(self):
        """All ALWAYS_EXPORT_TYPES bypass sampling."""
        for action_type in ALWAYS_EXPORT_TYPES:
            assert should_export(action_type, 0, 0.0) is True

    def test_governance_event_case_insensitive(self):
        assert should_export("KILL_SWITCH_ISSUED", 0, 0.0) is True

    def test_normal_event_respects_sampling(self):
        """Non-governance events delegate to should_sample."""
        with patch("random.random", return_value=0.5):
            assert should_export("task_executed", 0, 0.3) is False

    def test_normal_t2_always_exported(self):
        """T2 non-governance events still always export (via should_sample)."""
        assert should_export("task_executed", 2, 0.0) is True


# ── Edge: None quest_id fallback ─────────────────────────────────


class TestEdgeCases:
    def test_none_quest_id_fallback_in_attrs(self):
        """receipt_to_span_attrs with no quest_id omits it."""
        receipt = {"id": "rcpt-1", "action_type": "ping", "status": "ok"}
        attrs = receipt_to_span_attrs(receipt)
        assert "lancelot.quest_id" not in attrs
