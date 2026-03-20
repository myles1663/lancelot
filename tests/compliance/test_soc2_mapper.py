# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Unit tests for the SOC 2 Type II Control Mapper.

Tests verify receipt-to-control mapping, evidence aggregation,
export metadata, and structure conformance.
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "core"))

import pytest
from unittest.mock import MagicMock

from src.compliance.soc2_mapper import (
    SOC2_CONTROL_MAP,
    transform_soc2,
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
        action_type=ActionType.KILL_SWITCH_ISSUED.value,
        action_name="kill_switch",
        inputs={"target": "agent-1"},
        outputs={"result": "ok"},
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
        orphaned_count=1,
    )


# ---------------------------------------------------------------------------
# _receipt_to_evidence tests
# ---------------------------------------------------------------------------

class TestReceiptToEvidence:
    def test_extracts_core_fields(self):
        rd = {
            "id": "r1",
            "action_type": "kill_switch_issued",
            "timestamp": "2026-01-15T00:00:00Z",
            "operator_id": "op-001",
            "action_name": "kill_switch",
            "status": "success",
            "metadata": {"operator_display_name": "Alice"},
        }
        ev = _receipt_to_evidence(rd)
        assert ev["receipt_id"] == "r1"
        assert ev["receipt_type"] == "kill_switch_issued"
        assert ev["operator_id"] == "op-001"
        assert ev["display_name"] == "Alice"
        assert ev["pre_identity_migration"] is False

    def test_missing_fields_default_to_empty(self):
        ev = _receipt_to_evidence({})
        assert ev["receipt_id"] == ""
        assert ev["receipt_type"] == ""
        assert ev["display_name"] == ""

    def test_pre_identity_migration_flag(self):
        rd = {"pre_identity_migration": True, "metadata": {}}
        ev = _receipt_to_evidence(rd)
        assert ev["pre_identity_migration"] is True


# ---------------------------------------------------------------------------
# transform_soc2 tests
# ---------------------------------------------------------------------------

class TestTransformSoc2:
    def test_produces_valid_structure(self):
        receipts = [_make_receipt()]
        result = transform_soc2(
            receipts, _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        assert "export_metadata" in result
        assert "controls" in result
        assert result["export_metadata"]["format"] == "SOC2_TYPE_II"
        assert result["export_metadata"]["format_version"] == "1.0"

    def test_kill_switch_maps_to_cc2_2(self):
        receipts = [_make_receipt(action_type=ActionType.KILL_SWITCH_ISSUED.value)]
        result = transform_soc2(
            receipts, _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        cc2_2 = result["controls"]["CC2_2"]
        assert cc2_2["evidence_count"] >= 1

    def test_governance_receipt_maps_to_cc6_1(self):
        receipts = [
            _make_receipt(action_type=ActionType.KILL_SWITCH_ISSUED.value),
            _make_receipt(action_type=ActionType.T3_APPROVED.value, id="r-002"),
            _make_receipt(action_type=ActionType.SOUL_UPDATED.value, id="r-003"),
        ]
        result = transform_soc2(
            receipts, _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        cc6_1 = result["controls"]["CC6_1"]
        # All three types are in CC6_1
        assert cc6_1["evidence_count"] >= 3

    def test_credential_maps_to_cc6_2(self):
        receipts = [_make_receipt(
            action_type=ActionType.CREDENTIAL_REGISTERED.value, id="r-002"
        )]
        result = transform_soc2(
            receipts, _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        assert result["controls"]["CC6_2"]["evidence_count"] >= 1

    def test_revocation_maps_to_cc6_3(self):
        receipts = [_make_receipt(
            action_type=ActionType.CREDENTIAL_REVOKED.value, id="r-002"
        )]
        result = transform_soc2(
            receipts, _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        assert result["controls"]["CC6_3"]["evidence_count"] >= 1

    def test_evidence_aggregation_groups_by_control(self):
        # Same receipt type can map to multiple controls
        receipts = [
            _make_receipt(action_type=ActionType.KILL_SWITCH_ISSUED.value, id="r1"),
            _make_receipt(action_type=ActionType.KILL_SWITCH_ISSUED.value, id="r2"),
        ]
        result = transform_soc2(
            receipts, _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        # KILL_SWITCH_ISSUED is in CC2_2, CC6_1, CC7_3
        assert result["controls"]["CC2_2"]["evidence_count"] == 2
        assert result["controls"]["CC7_3"]["evidence_count"] >= 2

    def test_period_metadata_included(self):
        result = transform_soc2(
            [], _intact_chain(),
            "2026-03-01", "2026-03-31", "op-001", "2026-03-31T00:00:00Z", "e-001",
        )
        meta = result["export_metadata"]
        assert meta["period_start"] == "2026-03-01"
        assert meta["period_end"] == "2026-03-31"

    def test_chain_integrity_included_when_intact(self):
        result = transform_soc2(
            [], _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        assert result["export_metadata"]["chain_integrity"] == "CHAIN_INTACT"
        assert result["export_metadata"]["chain_anomaly_detail"] is None

    def test_chain_anomaly_detail_included(self):
        result = transform_soc2(
            [], _anomaly_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        assert result["export_metadata"]["chain_integrity"] == "CHAIN_ANOMALY"
        assert result["export_metadata"]["chain_anomaly_detail"] is not None

    def test_empty_receipts_produces_valid_structure(self):
        result = transform_soc2(
            [], _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        assert result["export_metadata"]["receipt_count"] == 0
        for control in result["controls"].values():
            assert control["evidence_count"] == 0
            assert control["evidence"] == []

    def test_multiple_receipt_types_to_same_control(self):
        receipts = [
            _make_receipt(action_type=ActionType.SOUL_UPDATED.value, id="r1"),
            _make_receipt(action_type=ActionType.AGENT_DEPLOYED.value, id="r2"),
            _make_receipt(action_type=ActionType.TOOL_ENABLED.value, id="r3"),
        ]
        result = transform_soc2(
            receipts, _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        # CC8_1 includes SOUL_UPDATED, AGENT_DEPLOYED, TOOL_ENABLED
        assert result["controls"]["CC8_1"]["evidence_count"] == 3

    def test_export_metadata_operator_and_id(self):
        result = transform_soc2(
            [], _intact_chain(),
            "2026-01-01", "2026-01-31", "op-xyz", "2026-01-31T12:00:00Z", "exp-abc",
        )
        meta = result["export_metadata"]
        assert meta["generated_by"]["operator_id"] == "op-xyz"
        assert meta["generated_at"] == "2026-01-31T12:00:00Z"
        assert meta["export_id"] == "exp-abc"

    def test_ip_address_redacted_from_evidence(self):
        receipt = _make_receipt(metadata={"ip_address": "10.0.0.1", "tag": "test"})
        result = transform_soc2(
            [receipt], _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        # Check no ip_address in any evidence entry across all controls
        for control in result["controls"].values():
            for ev in control["evidence"]:
                assert "ip_address" not in ev

    def test_soul_versions_collected(self):
        receipt = _make_receipt(
            action_type=ActionType.SOUL_UPDATED.value,
            inputs={"soul_version_hash": "abc123"},
        )
        result = transform_soc2(
            [receipt], _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        assert "abc123" in result["export_metadata"]["soul_versions_active"]

    def test_all_control_ids_present(self):
        result = transform_soc2(
            [], _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        for control_id in SOC2_CONTROL_MAP:
            assert control_id in result["controls"]

    def test_control_description_populated(self):
        result = transform_soc2(
            [], _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        for control_id, control in result["controls"].items():
            assert "description" in control
            assert len(control["description"]) > 0
