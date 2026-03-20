# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Unit tests for the GDPR Article 30 Processing Record Generator.

Tests verify processing activity detection, PII category extraction,
data subject categories, legal basis, retention, transfer safeguards,
and overall output structure.
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "core"))

import pytest
from unittest.mock import patch

from src.compliance.gdpr_mapper import (
    transform_gdpr,
    _detect_pii_events,
    _extract_pii_categories,
    _extract_recipients,
    _build_processing_record,
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
        action_type=ActionType.SYSTEM.value,
        action_name="system_action",
        inputs={},
        outputs={},
        status="success",
        operator_id="op-001",
        session_id="sess-001",
        metadata={},
        quest_id="quest-1",
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


# ---------------------------------------------------------------------------
# _detect_pii_events tests
# ---------------------------------------------------------------------------

class TestDetectPiiEvents:
    def test_detects_pii_metadata_key(self):
        receipts = [{"metadata": {"pii_detected": True}, "quest_id": "q1"}]
        result = _detect_pii_events(receipts)
        assert "q1" in result
        assert len(result["q1"]) == 1

    def test_detects_pii_scrubbed_key(self):
        receipts = [{"metadata": {"pii_scrubbed": True}, "quest_id": "q1"}]
        result = _detect_pii_events(receipts)
        assert "q1" in result

    def test_detects_pii_in_action_name(self):
        receipts = [{"action_name": "pii_scrub_check", "metadata": {}, "quest_id": "q2"}]
        result = _detect_pii_events(receipts)
        assert "q2" in result

    def test_no_pii_returns_empty(self):
        receipts = [{"action_name": "normal_action", "metadata": {}, "quest_id": "q1"}]
        result = _detect_pii_events(receipts)
        assert result == {}

    def test_no_quest_id_uses_placeholder(self):
        receipts = [{"metadata": {"pii_detected": True}, "quest_id": None}]
        result = _detect_pii_events(receipts)
        assert "_no_quest" in result

    def test_multiple_quests_grouped(self):
        receipts = [
            {"metadata": {"pii_detected": True}, "quest_id": "q1"},
            {"metadata": {"redacted": True}, "quest_id": "q2"},
            {"metadata": {"pii_detected": True}, "quest_id": "q1"},
        ]
        result = _detect_pii_events(receipts)
        assert len(result["q1"]) == 2
        assert len(result["q2"]) == 1


# ---------------------------------------------------------------------------
# _extract_pii_categories tests
# ---------------------------------------------------------------------------

class TestExtractPiiCategories:
    def test_extracts_categories_from_metadata(self):
        receipts = [
            {"metadata": {"pii_categories": ["email", "phone"]}},
            {"metadata": {"pii_categories": ["email", "name"]}},
        ]
        categories = _extract_pii_categories(receipts)
        assert "email" in categories
        assert "phone" in categories
        assert "name" in categories

    def test_no_categories_returns_limitation_note(self):
        receipts = [{"metadata": {"pii_detected": True}}]
        categories = _extract_pii_categories(receipts)
        assert len(categories) == 1
        assert "detected, category not recorded" in categories[0]

    def test_empty_category_list_returns_limitation(self):
        receipts = [{"metadata": {"pii_categories": []}}]
        categories = _extract_pii_categories(receipts)
        assert "detected, category not recorded" in categories[0]

    def test_sorted_output(self):
        receipts = [{"metadata": {"pii_categories": ["zip", "address", "email"]}}]
        categories = _extract_pii_categories(receipts)
        assert categories == sorted(categories)


# ---------------------------------------------------------------------------
# _extract_recipients tests
# ---------------------------------------------------------------------------

class TestExtractRecipients:
    def test_extracts_from_mcp_tool_call(self):
        receipts = [
            {
                "action_type": ActionType.MCP_TOOL_CALL.value,
                "inputs": {"server_id": "ext-api-1"},
            }
        ]
        result = _extract_recipients(receipts)
        assert "ext-api-1" in result

    def test_extracts_connector_id(self):
        receipts = [
            {
                "action_type": ActionType.CONNECTOR_ENABLED.value,
                "inputs": {"connector_id": "slack-connector"},
            }
        ]
        result = _extract_recipients(receipts)
        assert "slack-connector" in result

    def test_non_transmission_type_ignored(self):
        receipts = [
            {
                "action_type": ActionType.SYSTEM.value,
                "inputs": {"server_id": "internal"},
            }
        ]
        result = _extract_recipients(receipts)
        assert result == []

    def test_deduplication(self):
        receipts = [
            {"action_type": ActionType.MCP_TOOL_CALL.value, "inputs": {"server_id": "api-1"}},
            {"action_type": ActionType.MCP_TOOL_CALL.value, "inputs": {"server_id": "api-1"}},
        ]
        result = _extract_recipients(receipts)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _build_processing_record tests
# ---------------------------------------------------------------------------

class TestBuildProcessingRecord:
    def test_record_with_pii(self):
        quest_receipts = [
            {"timestamp": "2026-01-10T00:00:00Z", "action_type": "system", "inputs": {}},
        ]
        pii_receipts = [
            {"metadata": {"pii_categories": ["email"]}},
        ]
        record = _build_processing_record("q1", quest_receipts, pii_receipts, [])
        assert record["personal_data_processed"] is True
        assert record["quest_id"] == "q1"
        assert "email" in record["categories_of_personal_data"]
        assert record["pii_event_count"] == 1

    def test_record_without_pii(self):
        quest_receipts = [
            {"timestamp": "2026-01-10T00:00:00Z", "action_type": "system", "inputs": {}},
        ]
        record = _build_processing_record("q1", quest_receipts, [], [])
        assert record["personal_data_processed"] is False
        assert record["categories_of_personal_data"] == []

    def test_retention_period_present(self):
        record = _build_processing_record("q1", [], [], [])
        assert "retention_period" in record
        assert len(record["retention_period"]) > 0

    def test_security_measures_present(self):
        record = _build_processing_record("q1", [], [], [])
        assert "security_measures" in record
        assert "Soul governance" in record["security_measures"]


# ---------------------------------------------------------------------------
# transform_gdpr tests
# ---------------------------------------------------------------------------

class TestTransformGdpr:
    def test_produces_valid_structure(self):
        result = transform_gdpr(
            [_make_receipt()], _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        assert "export_metadata" in result
        assert "processing_activities" in result
        assert "pii_category_note" in result
        assert result["export_metadata"]["format"] == "GDPR_ARTICLE_30"

    def test_processing_activities_from_receipts(self):
        receipts = [
            _make_receipt(quest_id="q1", id="r1"),
            _make_receipt(quest_id="q2", id="r2"),
        ]
        result = transform_gdpr(
            receipts, _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        pa = result["processing_activities"]
        assert pa["total_quests"] == 2
        assert len(pa["records"]) == 2

    def test_pii_quest_vs_non_pii_counts(self):
        receipts = [
            _make_receipt(
                quest_id="q-pii", id="r1",
                metadata={"pii_detected": True},
            ),
            _make_receipt(quest_id="q-clean", id="r2"),
        ]
        result = transform_gdpr(
            receipts, _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        pa = result["processing_activities"]
        assert pa["quests_with_personal_data"] == 1
        assert pa["quests_without_personal_data"] == 1

    def test_pii_category_extraction(self):
        receipts = [
            _make_receipt(
                quest_id="q1", id="r1",
                metadata={"pii_categories": ["email", "phone"]},
            ),
        ]
        result = transform_gdpr(
            receipts, _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        records = result["processing_activities"]["records"]
        pii_record = [r for r in records if r["personal_data_processed"]]
        assert len(pii_record) == 1
        assert "email" in pii_record[0]["categories_of_personal_data"]

    def test_data_subject_categories_with_pii(self):
        receipts = [
            _make_receipt(quest_id="q1", metadata={"pii_detected": True}),
        ]
        result = transform_gdpr(
            receipts, _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        record = result["processing_activities"]["records"][0]
        assert "Derived from PII" in record["categories_of_data_subjects"]

    def test_data_subject_categories_without_pii(self):
        receipts = [_make_receipt(quest_id="q1")]
        result = transform_gdpr(
            receipts, _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        record = result["processing_activities"]["records"][0]
        assert "No personal data" in record["categories_of_data_subjects"]

    def test_transfer_safeguards_from_recipients(self):
        receipts = [
            _make_receipt(
                quest_id="q1",
                action_type=ActionType.MCP_TOOL_CALL.value,
                inputs={"server_id": "external-api"},
            ),
        ]
        result = transform_gdpr(
            receipts, _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        record = result["processing_activities"]["records"][0]
        assert "external-api" in record["recipients"]

    def test_empty_receipts_handling(self):
        result = transform_gdpr(
            [], _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        pa = result["processing_activities"]
        assert pa["total_quests"] == 0
        assert pa["records"] == []

    def test_chain_integrity_in_metadata(self):
        result = transform_gdpr(
            [], _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        assert result["export_metadata"]["chain_integrity"] == "CHAIN_INTACT"

    def test_no_quest_id_grouped_under_placeholder(self):
        receipts = [_make_receipt(quest_id=None, id="r1")]
        result = transform_gdpr(
            receipts, _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        records = result["processing_activities"]["records"]
        assert len(records) == 1
        assert records[0]["quest_id"] == "_no_quest"

    def test_purpose_field_present(self):
        receipts = [_make_receipt()]
        result = transform_gdpr(
            receipts, _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        record = result["processing_activities"]["records"][0]
        assert "purpose" in record
        assert "Soul governance" in record["purpose"]

    def test_pii_category_note_present(self):
        result = transform_gdpr(
            [], _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        assert "pii_category_note" in result
        assert "limitation" in result["pii_category_note"].lower()

    def test_period_metadata(self):
        result = transform_gdpr(
            [], _intact_chain(),
            "2026-03-01", "2026-03-31", "op-xyz", "2026-03-31T00:00:00Z", "exp-1",
        )
        meta = result["export_metadata"]
        assert meta["period_start"] == "2026-03-01"
        assert meta["period_end"] == "2026-03-31"
        assert meta["generated_by"]["operator_id"] == "op-xyz"

    def test_legal_basis_via_purpose(self):
        """The purpose field serves as the legal basis justification."""
        receipts = [_make_receipt()]
        result = transform_gdpr(
            receipts, _intact_chain(),
            "2026-01-01", "2026-01-31", "op-001", "2026-01-31T00:00:00Z", "e-001",
        )
        record = result["processing_activities"]["records"][0]
        assert record["processing_activity"] is True
        assert len(record["purpose"]) > 0
