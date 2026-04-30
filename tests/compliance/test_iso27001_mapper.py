# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Unit tests for the ISO 27001:2022 Annex A Control Mapper.

Tests verify control mapping, statement of applicability (excluded controls),
evidence structure, and export metadata.
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "core"))

import pytest

from src.compliance.iso27001_mapper import (
    ISO27001_CONTROL_MAP,
    ISO27001_EXCLUDED_CONTROLS,
    transform_iso27001,
    _receipt_to_evidence,
)
from src.compliance.chain_integrity import ChainIntegrityResult
from src.shared.receipts import Receipt, ActionType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_receipt(**overrides):
    defaults = dict(
        id="r-001",
        timestamp="2026-01-15T00:00:00Z",
        action_type=ActionType.SOUL_UPDATED.value,
        action_name="soul_update",
        inputs={},
        outputs={},
        status="success",
        operator_id="op-001",
        session_id="sess-001",
        metadata={},
    )
    defaults.update(overrides)
    return Receipt(**defaults)


def _intact_chain():
    return ChainIntegrityResult(
        status="CHAIN_INTACT",
        period_start="2026-01-01",
        period_end="2026-01-31",
        total_receipts=10,
        receipts_with_parents=8,
        orphaned_count=0,
    )


def _anomaly_chain():
    return ChainIntegrityResult(
        status="CHAIN_ANOMALY",
        period_start="2026-01-01",
        period_end="2026-01-31",
        total_receipts=10,
        receipts_with_parents=8,
        orphaned_count=2,
    )


# ---------------------------------------------------------------------------
# _receipt_to_evidence tests
# ---------------------------------------------------------------------------

class TestReceiptToEvidence:
    def test_extracts_core_fields(self):
        rd = {
            "id": "r1",
            "action_type": "soul_updated",
            "timestamp": "2026-01-15T00:00:00Z",
            "operator_id": "op-001",
            "action_name": "soul_update",
            "status": "success",
        }
        ev = _receipt_to_evidence(rd)
        assert ev["receipt_id"] == "r1"
        assert ev["receipt_type"] == "soul_updated"
        assert ev["operator_id"] == "op-001"
        assert ev["operator_attribution"] == "human"

    def test_pre_identity_migration_defaults_false(self):
        ev = _receipt_to_evidence({"metadata": {}})
        assert ev["pre_identity_migration"] is False


# ---------------------------------------------------------------------------
# transform_iso27001 tests
# ---------------------------------------------------------------------------

class TestTransformIso27001:
    def test_produces_valid_structure(self):
        result = transform_iso27001(
            [_make_receipt()], _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        assert "export_metadata" in result
        assert "controls" in result
        assert "excluded_controls" in result
        assert result["export_metadata"]["format"] == "ISO27001_2022"
        assert result["export_metadata"]["format_version"] == "2.0"
        assert "statement_of_applicability" in result
        assert "integrity" in result
        assert "legacy_attribution_summary" in result

    def test_annex_a_control_mapping(self):
        receipts = [
            _make_receipt(action_type=ActionType.SOUL_UPDATED.value, id="r1"),
            _make_receipt(action_type=ActionType.SOUL_VERSION_PINNED.value, id="r2"),
        ]
        result = transform_iso27001(
            receipts, _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        a5_1 = result["controls"]["A.5.1"]
        assert a5_1["evidence_count"] == 2
        assert "Information Security Policies" in a5_1["description"]
        assert a5_1["control_status"] == "observed"

    def test_access_control_mapping(self):
        receipts = [
            _make_receipt(action_type=ActionType.KILL_SWITCH_ISSUED.value, id="r1"),
            _make_receipt(action_type=ActionType.CREDENTIAL_REGISTERED.value, id="r2"),
        ]
        result = transform_iso27001(
            receipts, _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        a5_15 = result["controls"]["A.5.15"]
        assert a5_15["evidence_count"] == 2

    def test_excluded_controls_present(self):
        result = transform_iso27001(
            [], _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        excluded = result["excluded_controls"]
        assert "A.7" in excluded
        assert "A.5.19" in excluded

    def test_notes_field_on_each_control(self):
        result = transform_iso27001(
            [], _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        for control_id, control in result["controls"].items():
            assert "notes" in control
            assert isinstance(control["notes"], str)
            assert "evidence_summary" in control

    def test_empty_receipts_handling(self):
        result = transform_iso27001(
            [], _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        assert result["export_metadata"]["receipt_count"] == 0
        for control in result["controls"].values():
            assert control["evidence_count"] == 0
            assert control["control_status"] == "not_observed_in_period"

    def test_chain_integrity_metadata(self):
        result = transform_iso27001(
            [], _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        assert result["export_metadata"]["chain_integrity"] == "CHAIN_INTACT"
        assert result["export_metadata"]["chain_anomaly_detail"] is None
        assert result["integrity"]["chain_intact"] is True

    def test_chain_anomaly_detail(self):
        result = transform_iso27001(
            [], _anomaly_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        assert result["export_metadata"]["chain_anomaly_detail"] is not None
        assert result["export_metadata"]["chain_anomaly_detail"]["orphaned_count"] == 2

    def test_period_and_operator_metadata(self):
        result = transform_iso27001(
            [], _intact_chain(),
            "2026-03-01", "2026-03-31", "op-xyz", "2026-03-31T12:00:00Z", "exp-abc",
        )
        meta = result["export_metadata"]
        assert meta["period_start"] == "2026-03-01"
        assert meta["period_end"] == "2026-03-31"
        assert meta["generated_by"]["operator_id"] == "op-xyz"
        assert meta["generated_by"]["display_name"] == "op-xyz"
        assert meta["export_id"] == "exp-abc"

    def test_all_control_ids_present(self):
        result = transform_iso27001(
            [], _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        for control_id in ISO27001_CONTROL_MAP:
            assert control_id in result["controls"]

    def test_ip_address_redacted(self):
        receipt = _make_receipt(metadata={"ip_address": "192.168.1.1"})
        result = transform_iso27001(
            [receipt], _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        for control in result["controls"].values():
            for ev in control["evidence"]:
                assert "ip_address" not in ev

    def test_t3_maps_to_a8_2(self):
        receipts = [
            _make_receipt(action_type=ActionType.T3_APPROVED.value, id="r1"),
            _make_receipt(
                action_type=ActionType.MCP_T3_REJECTED.value,
                id="r2",
                status="failure",
            ),
        ]
        result = transform_iso27001(
            receipts, _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        assert result["controls"]["A.8.2"]["evidence_count"] == 2
        assert result["controls"]["A.8.2"]["exception_count"] == 1

    def test_exception_summary_and_scope_present(self):
        receipts = [
            _make_receipt(
                action_type=ActionType.KILL_SWITCH_ISSUED.value,
                id="r1",
                status="failure",
                operator_id=None,
            ),
        ]
        result = transform_iso27001(
            receipts, _anomaly_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
            operator_display_name="Arthur",
        )
        assert result["exception_summary"]["total_exception_receipts"] >= 1
        assert result["export_scope"]["receipt_count"] == 1
        assert result["system_context"]["generated_by"]["display_name"] == "Arthur"
