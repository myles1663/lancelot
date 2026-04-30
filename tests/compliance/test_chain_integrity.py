# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Unit tests for the Chain Integrity Checker.

Tests verify receipt DAG verification logic including gap detection,
orphan identification, out-of-period link handling, and serialization.
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "core"))

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from dataclasses import asdict

from src.compliance.chain_integrity import (
    ChainGap,
    ChainIntegrityResult,
    check_chain_integrity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_receipt_service(
    total_count=0,
    parent_count=0,
    orphan_rows=None,
):
    """Build a mock ReceiptService exposing the public audit summary API."""
    if orphan_rows is None:
        orphan_rows = []

    svc = MagicMock()
    svc.summarize_parent_chain.return_value = {
        "total_receipts": total_count,
        "receipts_with_parents": parent_count,
        "missing_parent_gaps": [
            {
                "receipt_id": row["id"],
                "orphaned_parent_id": row["parent_id"],
                "receipt_timestamp": row["timestamp"],
            }
            for row in orphan_rows
        ],
    }
    return svc


# ---------------------------------------------------------------------------
# ChainGap dataclass tests
# ---------------------------------------------------------------------------

class TestChainGap:
    def test_to_dict_contains_all_fields(self):
        gap = ChainGap(
            receipt_id="r1",
            orphaned_parent_id="p1",
            gap_type="missing_parent",
            receipt_timestamp="2026-01-01T00:00:00Z",
        )
        d = gap.to_dict()
        assert d["receipt_id"] == "r1"
        assert d["orphaned_parent_id"] == "p1"
        assert d["gap_type"] == "missing_parent"
        assert d["receipt_timestamp"] == "2026-01-01T00:00:00Z"

    def test_to_dict_default_timestamp(self):
        gap = ChainGap(receipt_id="r1", orphaned_parent_id="p1", gap_type="missing_parent")
        assert gap.to_dict()["receipt_timestamp"] == ""


# ---------------------------------------------------------------------------
# ChainIntegrityResult dataclass tests
# ---------------------------------------------------------------------------

class TestChainIntegrityResult:
    def test_is_intact_true_when_chain_intact(self):
        result = ChainIntegrityResult(
            status="CHAIN_INTACT",
            period_start="2026-01-01",
            period_end="2026-01-31",
            total_receipts=10,
            receipts_with_parents=8,
            orphaned_count=0,
        )
        assert result.is_intact is True

    def test_is_intact_false_when_chain_anomaly(self):
        result = ChainIntegrityResult(
            status="CHAIN_ANOMALY",
            period_start="2026-01-01",
            period_end="2026-01-31",
            total_receipts=10,
            receipts_with_parents=8,
            orphaned_count=1,
            gaps=[ChainGap("r1", "p1", "missing_parent")],
        )
        assert result.is_intact is False

    def test_to_dict_includes_is_intact(self):
        result = ChainIntegrityResult(
            status="CHAIN_INTACT",
            period_start="2026-01-01",
            period_end="2026-01-31",
            total_receipts=5,
            receipts_with_parents=3,
            orphaned_count=0,
        )
        d = result.to_dict()
        assert "is_intact" in d
        assert d["is_intact"] is True

    def test_to_dict_serializes_gaps(self):
        gap = ChainGap("r1", "p1", "missing_parent", "2026-01-15T00:00:00Z")
        result = ChainIntegrityResult(
            status="CHAIN_ANOMALY",
            period_start="2026-01-01",
            period_end="2026-01-31",
            total_receipts=5,
            receipts_with_parents=3,
            orphaned_count=1,
            gaps=[gap],
        )
        d = result.to_dict()
        assert len(d["gaps"]) == 1
        assert d["gaps"][0]["receipt_id"] == "r1"

    def test_to_dict_all_scalar_fields(self):
        result = ChainIntegrityResult(
            status="CHAIN_INTACT",
            period_start="2026-01-01",
            period_end="2026-06-30",
            total_receipts=100,
            receipts_with_parents=90,
            orphaned_count=0,
        )
        d = result.to_dict()
        assert d["status"] == "CHAIN_INTACT"
        assert d["period_start"] == "2026-01-01"
        assert d["period_end"] == "2026-06-30"
        assert d["total_receipts"] == 100
        assert d["receipts_with_parents"] == 90
        assert d["orphaned_count"] == 0


# ---------------------------------------------------------------------------
# check_chain_integrity function tests
# ---------------------------------------------------------------------------

class TestCheckChainIntegrity:
    def test_perfect_chain_returns_intact(self):
        svc = _mock_receipt_service(total_count=10, parent_count=8, orphan_rows=[])
        result = check_chain_integrity(svc, "2026-01-01", "2026-01-31")
        assert result.is_intact is True
        assert result.status == "CHAIN_INTACT"
        assert result.total_receipts == 10
        assert result.receipts_with_parents == 8
        assert result.orphaned_count == 0
        assert result.gaps == []

    def test_single_orphaned_parent_detected(self):
        orphan = {"id": "r1", "parent_id": "missing_p1", "timestamp": "2026-01-10T00:00:00Z"}
        svc = _mock_receipt_service(total_count=5, parent_count=3, orphan_rows=[orphan])
        result = check_chain_integrity(svc, "2026-01-01", "2026-01-31")
        assert result.is_intact is False
        assert result.status == "CHAIN_ANOMALY"
        assert result.orphaned_count == 1
        assert len(result.gaps) == 1
        assert result.gaps[0].orphaned_parent_id == "missing_p1"
        assert result.gaps[0].gap_type == "missing_parent"

    def test_multiple_gaps_detected(self):
        orphans = [
            {"id": "r1", "parent_id": "mp1", "timestamp": "2026-01-05T00:00:00Z"},
            {"id": "r2", "parent_id": "mp2", "timestamp": "2026-01-10T00:00:00Z"},
            {"id": "r3", "parent_id": "mp3", "timestamp": "2026-01-15T00:00:00Z"},
        ]
        svc = _mock_receipt_service(total_count=20, parent_count=15, orphan_rows=orphans)
        result = check_chain_integrity(svc, "2026-01-01", "2026-01-31")
        assert result.orphaned_count == 3
        assert len(result.gaps) == 3
        assert result.is_intact is False

    def test_empty_receipt_list_returns_intact(self):
        svc = _mock_receipt_service(total_count=0, parent_count=0, orphan_rows=[])
        result = check_chain_integrity(svc, "2026-01-01", "2026-01-31")
        assert result.is_intact is True
        assert result.total_receipts == 0
        assert result.orphaned_count == 0

    def test_single_receipt_no_parent(self):
        svc = _mock_receipt_service(total_count=1, parent_count=0, orphan_rows=[])
        result = check_chain_integrity(svc, "2026-01-01", "2026-01-31")
        assert result.is_intact is True
        assert result.total_receipts == 1
        assert result.receipts_with_parents == 0

    def test_quest_id_filter_passed_to_queries(self):
        svc = _mock_receipt_service(total_count=3, parent_count=2, orphan_rows=[])
        result = check_chain_integrity(svc, "2026-01-01", "2026-01-31", quest_id="q-123")
        assert result.is_intact is True
        svc.summarize_parent_chain.assert_called_once_with(
            since="2026-01-01",
            until="2026-01-31",
            quest_id="q-123",
        )

    def test_period_metadata_in_result(self):
        svc = _mock_receipt_service(total_count=5, parent_count=3, orphan_rows=[])
        result = check_chain_integrity(svc, "2026-03-01", "2026-03-31")
        assert result.period_start == "2026-03-01"
        assert result.period_end == "2026-03-31"

    def test_gap_receipt_timestamp_preserved(self):
        orphan = {"id": "r1", "parent_id": "p1", "timestamp": "2026-01-15T12:30:00Z"}
        svc = _mock_receipt_service(total_count=5, parent_count=3, orphan_rows=[orphan])
        result = check_chain_integrity(svc, "2026-01-01", "2026-01-31")
        assert result.gaps[0].receipt_timestamp == "2026-01-15T12:30:00Z"
        assert result.gaps[0].receipt_id == "r1"

    def test_mixed_valid_and_orphaned_links(self):
        # Only orphaned ones appear in orphan_rows; valid links are in parent_count
        orphans = [
            {"id": "r5", "parent_id": "mp1", "timestamp": "2026-01-20T00:00:00Z"},
        ]
        svc = _mock_receipt_service(total_count=10, parent_count=8, orphan_rows=orphans)
        result = check_chain_integrity(svc, "2026-01-01", "2026-01-31")
        assert result.is_intact is False
        assert result.total_receipts == 10
        assert result.receipts_with_parents == 8
        assert result.orphaned_count == 1

    def test_no_quest_id_omits_quest_filter(self):
        svc = _mock_receipt_service(total_count=5, parent_count=3, orphan_rows=[])
        check_chain_integrity(svc, "2026-01-01", "2026-01-31", quest_id=None)
        svc.summarize_parent_chain.assert_called_once_with(
            since="2026-01-01",
            until="2026-01-31",
            quest_id=None,
        )

    def test_result_to_dict_roundtrip(self):
        gap = ChainGap("r1", "p1", "missing_parent", "2026-01-10T00:00:00Z")
        result = ChainIntegrityResult(
            status="CHAIN_ANOMALY",
            period_start="2026-01-01",
            period_end="2026-01-31",
            total_receipts=10,
            receipts_with_parents=8,
            orphaned_count=1,
            gaps=[gap],
        )
        d = result.to_dict()
        assert d["status"] == "CHAIN_ANOMALY"
        assert d["is_intact"] is False
        assert d["orphaned_count"] == 1
        assert len(d["gaps"]) == 1
        assert d["gaps"][0]["receipt_id"] == "r1"

    def test_default_gaps_is_empty_list(self):
        result = ChainIntegrityResult(
            status="CHAIN_INTACT",
            period_start="2026-01-01",
            period_end="2026-01-31",
            total_receipts=0,
            receipts_with_parents=0,
            orphaned_count=0,
        )
        assert result.gaps == []
