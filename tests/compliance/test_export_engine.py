# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Unit tests for the Compliance Export Engine.

Tests verify period resolution, receipt fetching, the full export pipeline
for each format, SHA-256 integrity hashing, and export receipt generation.
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "core"))

import hashlib
import json
import zipfile
import pytest
from unittest.mock import MagicMock, patch, call
from pathlib import Path

from src.compliance.export_engine import (
    ExportFormat,
    ExportResult,
    PeriodResolutionError,
    resolve_period,
    fetch_receipts,
    run_export,
    write_export_receipt,
    _export_filename,
    _ensure_export_dir,
)
from src.compliance.chain_integrity import ChainIntegrityResult
from src.shared.receipts import Receipt, ActionType, ReceiptStatus


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


def _mock_receipt_service_for_resolve(receipts_exist=True, count=10):
    """Mock for resolve_period tests."""
    svc = MagicMock()
    svc.list.return_value = [_make_receipt()] if receipts_exist else []
    conn = MagicMock()
    svc._get_connection.return_value = conn
    cursor = MagicMock()
    cursor.fetchone.return_value = {"cnt": count}
    conn.execute.return_value = cursor
    return svc


def _mock_receipt_service_for_fetch(receipts=None):
    """Mock for fetch_receipts tests."""
    if receipts is None:
        receipts = [_make_receipt()]
    svc = MagicMock()
    conn = MagicMock()
    svc._get_connection.return_value = conn
    cursor = MagicMock()
    # _row_to_receipt is called per row
    rows = [MagicMock() for _ in receipts]
    cursor.fetchall.return_value = rows
    conn.execute.return_value = cursor
    svc._row_to_receipt.side_effect = receipts
    return svc


def _mock_receipt_service_for_export(receipts=None, count=5):
    """Mock for full run_export tests."""
    if receipts is None:
        receipts = [_make_receipt()]

    svc = MagicMock()

    # resolve_period needs: svc.list, svc._get_connection
    svc.list.return_value = receipts

    conn = MagicMock()
    svc._get_connection.return_value = conn

    # resolve_period count query
    count_cursor = MagicMock()
    count_cursor.fetchone.return_value = {"cnt": count}

    # fetch_receipts query
    fetch_cursor = MagicMock()
    rows = [MagicMock() for _ in receipts]
    fetch_cursor.fetchall.return_value = rows

    # chain_integrity queries: total count, parent count, orphan query
    chain_count_cursor = MagicMock()
    chain_count_cursor.fetchone.return_value = {"cnt": count}
    chain_parent_cursor = MagicMock()
    chain_parent_cursor.fetchone.return_value = {"cnt": count - 1}
    chain_orphan_cursor = MagicMock()
    chain_orphan_cursor.fetchall.return_value = []

    conn.execute.side_effect = [
        count_cursor,        # resolve_period
        fetch_cursor,        # fetch_receipts
        chain_count_cursor,  # check_chain_integrity total
        chain_parent_cursor, # check_chain_integrity parents
        chain_orphan_cursor, # check_chain_integrity orphans
    ]

    svc._row_to_receipt.side_effect = receipts
    svc.create.side_effect = lambda r: r  # pass-through for export receipt
    return svc


# ---------------------------------------------------------------------------
# ExportFormat tests
# ---------------------------------------------------------------------------

class TestExportFormat:
    def test_all_formats_listed(self):
        assert ExportFormat.PDF in ExportFormat.ALL
        assert ExportFormat.SOC2_JSON in ExportFormat.ALL
        assert ExportFormat.ISO27001_JSON in ExportFormat.ALL
        assert ExportFormat.GDPR_JSON in ExportFormat.ALL
        assert len(ExportFormat.ALL) == 4


# ---------------------------------------------------------------------------
# ExportResult tests
# ---------------------------------------------------------------------------

class TestExportResult:
    def test_success_true_when_no_error(self):
        chain = ChainIntegrityResult(
            status="CHAIN_INTACT", period_start="2026-01-01",
            period_end="2026-01-31", total_receipts=5,
            receipts_with_parents=3, orphaned_count=0,
        )
        result = ExportResult(
            export_id="e1", export_format="SOC2_JSON",
            period_start="2026-01-01", period_end="2026-01-31",
            receipt_count=5, chain_integrity=chain,
            output_path="/tmp/test.json", output_sha256="abc",
            export_duration_ms=100.0, generated_at="2026-01-31T00:00:00Z",
        )
        assert result.success is True

    def test_success_false_when_error_present(self):
        chain = ChainIntegrityResult(
            status="CHAIN_ANOMALY", period_start="2026-01-01",
            period_end="2026-01-31", total_receipts=0,
            receipts_with_parents=0, orphaned_count=0,
        )
        result = ExportResult(
            export_id="e1", export_format="SOC2_JSON",
            period_start="2026-01-01", period_end="2026-01-31",
            receipt_count=0, chain_integrity=chain,
            output_path="", output_sha256="",
            export_duration_ms=5.0, generated_at="2026-01-31T00:00:00Z",
            error="No receipts found",
        )
        assert result.success is False

    def test_to_dict_includes_all_fields(self):
        chain = ChainIntegrityResult(
            status="CHAIN_INTACT", period_start="2026-01-01",
            period_end="2026-01-31", total_receipts=5,
            receipts_with_parents=3, orphaned_count=0,
        )
        result = ExportResult(
            export_id="e1", export_format="SOC2_JSON",
            period_start="2026-01-01", period_end="2026-01-31",
            receipt_count=5, chain_integrity=chain,
            output_path="/tmp/test.json", output_sha256="abc123",
            export_duration_ms=100.0, generated_at="2026-01-31T00:00:00Z",
            quest_id="q-1",
        )
        d = result.to_dict()
        assert d["export_id"] == "e1"
        assert d["success"] is True
        assert d["quest_id"] == "q-1"
        assert d["chain_integrity"]["status"] == "CHAIN_INTACT"


# ---------------------------------------------------------------------------
# resolve_period tests
# ---------------------------------------------------------------------------

class TestResolvePeriod:
    def test_valid_period_returns_count(self):
        svc = _mock_receipt_service_for_resolve(receipts_exist=True, count=10)
        count = resolve_period(svc, "2026-01-01", "2026-01-31")
        assert count == 10

    def test_start_after_end_raises(self):
        svc = MagicMock()
        with pytest.raises(PeriodResolutionError, match="must be before"):
            resolve_period(svc, "2026-02-01", "2026-01-01")

    def test_start_equals_end_raises(self):
        svc = MagicMock()
        with pytest.raises(PeriodResolutionError, match="must be before"):
            resolve_period(svc, "2026-01-01", "2026-01-01")

    def test_no_receipts_raises(self):
        svc = _mock_receipt_service_for_resolve(receipts_exist=False)
        with pytest.raises(PeriodResolutionError, match="No receipts found"):
            resolve_period(svc, "2026-01-01", "2026-01-31")


# ---------------------------------------------------------------------------
# fetch_receipts tests
# ---------------------------------------------------------------------------

class TestFetchReceipts:
    def test_returns_receipts_in_range(self):
        receipts = [_make_receipt(id="r1"), _make_receipt(id="r2")]
        svc = _mock_receipt_service_for_fetch(receipts)
        result = fetch_receipts(svc, "2026-01-01", "2026-01-31")
        assert len(result) == 2

    def test_quest_id_filter_applied(self):
        svc = _mock_receipt_service_for_fetch([_make_receipt()])
        fetch_receipts(svc, "2026-01-01", "2026-01-31", quest_id="q-123")
        conn = svc._get_connection()
        sql_arg = conn.execute.call_args[0][0]
        assert "quest_id" in sql_arg

    def test_empty_result(self):
        svc = _mock_receipt_service_for_fetch([])
        svc._get_connection().execute().fetchall.return_value = []
        svc._row_to_receipt.side_effect = []
        result = fetch_receipts(svc, "2026-01-01", "2026-01-31")
        assert result == []

    def test_no_quest_id_omits_filter(self):
        svc = _mock_receipt_service_for_fetch([_make_receipt()])
        fetch_receipts(svc, "2026-01-01", "2026-01-31", quest_id=None)
        conn = svc._get_connection()
        sql_arg = conn.execute.call_args[0][0]
        assert "quest_id" not in sql_arg


# ---------------------------------------------------------------------------
# _export_filename tests
# ---------------------------------------------------------------------------

class TestExportFilename:
    def test_zip_extension_for_soc2(self):
        name = _export_filename("SOC2_JSON", "2026-01-01T00:00:00Z", "2026-01-31T23:59:59Z", "abc12345-6789")
        assert name.endswith(".zip")
        assert "soc2_json" in name
        assert "2026-01-01" in name
        assert "2026-01-31" in name
        assert "abc12345" in name

    def test_pdf_extension(self):
        name = _export_filename("PDF", "2026-01-01", "2026-01-31", "xxxxxxxx-yyyy")
        assert name.endswith(".pdf")

    def test_short_id_is_first_8_chars(self):
        name = _export_filename("SOC2_JSON", "2026-01-01", "2026-01-31", "abcdefgh-ijkl-mnop")
        assert "abcdefgh" in name


# ---------------------------------------------------------------------------
# _ensure_export_dir tests
# ---------------------------------------------------------------------------

class TestEnsureExportDir:
    def test_creates_directory(self, tmp_path):
        export_dir = _ensure_export_dir(str(tmp_path))
        assert export_dir.exists()
        assert export_dir.name == "compliance_exports"

    def test_idempotent(self, tmp_path):
        _ensure_export_dir(str(tmp_path))
        export_dir = _ensure_export_dir(str(tmp_path))
        assert export_dir.exists()


# ---------------------------------------------------------------------------
# write_export_receipt tests
# ---------------------------------------------------------------------------

class TestWriteExportReceipt:
    def test_creates_receipt_with_correct_fields(self):
        svc = MagicMock()
        svc.create.side_effect = lambda r: r

        chain = ChainIntegrityResult(
            status="CHAIN_INTACT", period_start="2026-01-01",
            period_end="2026-01-31", total_receipts=10,
            receipts_with_parents=8, orphaned_count=0,
        )
        export_result = ExportResult(
            export_id="e-001", export_format="SOC2_JSON",
            period_start="2026-01-01", period_end="2026-01-31",
            receipt_count=10, chain_integrity=chain,
            output_path="/data/exports/test.json",
            output_sha256="sha256hash",
            export_duration_ms=250.0,
            generated_at="2026-01-31T00:00:00Z",
        )

        receipt = write_export_receipt(svc, export_result, "op-001", "sess-001")
        assert receipt.action_type == ActionType.COMPLIANCE_EXPORT_GENERATED.value
        assert receipt.operator_id == "op-001"
        assert receipt.session_id == "sess-001"
        assert receipt.inputs["export_format"] == "SOC2_JSON"
        assert receipt.outputs["export_id"] == "e-001"
        assert receipt.outputs["output_sha256"] == "sha256hash"
        assert receipt.metadata["compliance_export"] is True


# ---------------------------------------------------------------------------
# run_export pipeline tests
# ---------------------------------------------------------------------------

class TestRunExport:
    def test_soc2_format_produces_zip_bundle(self, tmp_path):
        svc = _mock_receipt_service_for_export()
        result = run_export(
            receipt_service=svc,
            export_format=ExportFormat.SOC2_JSON,
            period_start="2026-01-01",
            period_end="2026-01-31",
            data_dir=str(tmp_path),
            operator_id="op-001",
        )
        assert result.success is True
        assert result.export_format == ExportFormat.SOC2_JSON
        assert Path(result.output_path).exists()
        assert result.output_path.endswith(".zip")
        with zipfile.ZipFile(result.output_path) as zf:
            names = set(zf.namelist())
            assert "manifest.json" in names
            assert "README.txt" in names
            json_name = next(name for name in names if name.endswith(".json") and name != "manifest.json")
            pdf_name = next(name for name in names if name.endswith("_summary.pdf"))
            csv_name = next(name for name in names if name.endswith("_index.csv"))
            data = json.loads(zf.read(json_name))
            manifest = json.loads(zf.read("manifest.json"))
        assert "controls" in data
        assert "export_metadata" in data
        assert "system_context" in data
        assert "integrity" in data
        manifest_names = {entry["name"] for entry in manifest["files"]}
        assert json_name in manifest_names
        assert pdf_name in manifest_names
        assert csv_name in manifest_names

    def test_iso27001_format(self, tmp_path):
        svc = _mock_receipt_service_for_export()
        result = run_export(
            receipt_service=svc,
            export_format=ExportFormat.ISO27001_JSON,
            period_start="2026-01-01",
            period_end="2026-01-31",
            data_dir=str(tmp_path),
            operator_id="op-001",
        )
        assert result.success is True
        with zipfile.ZipFile(result.output_path) as zf:
            json_name = next(
                name for name in zf.namelist()
                if name.endswith(".json") and name != "manifest.json"
            )
            data = json.loads(zf.read(json_name))
        assert "controls" in data
        assert "excluded_controls" in data
        assert "statement_of_applicability" in data

    def test_gdpr_format(self, tmp_path):
        svc = _mock_receipt_service_for_export()
        result = run_export(
            receipt_service=svc,
            export_format=ExportFormat.GDPR_JSON,
            period_start="2026-01-01",
            period_end="2026-01-31",
            data_dir=str(tmp_path),
            operator_id="op-001",
        )
        assert result.success is True
        with zipfile.ZipFile(result.output_path) as zf:
            json_name = next(
                name for name in zf.namelist()
                if name.endswith(".json") and name != "manifest.json"
            )
            data = json.loads(zf.read(json_name))
        assert "processing_activities" in data
        assert "processing_summary" in data

    def test_sha256_integrity_hash(self, tmp_path):
        svc = _mock_receipt_service_for_export()
        result = run_export(
            receipt_service=svc,
            export_format=ExportFormat.SOC2_JSON,
            period_start="2026-01-01",
            period_end="2026-01-31",
            data_dir=str(tmp_path),
            operator_id="op-001",
        )
        # Verify hash matches file contents
        with open(result.output_path, "rb") as f:
            actual_hash = hashlib.sha256(f.read()).hexdigest()
        assert result.output_sha256 == actual_hash

    def test_export_receipt_emitted(self, tmp_path):
        svc = _mock_receipt_service_for_export()
        run_export(
            receipt_service=svc,
            export_format=ExportFormat.SOC2_JSON,
            period_start="2026-01-01",
            period_end="2026-01-31",
            data_dir=str(tmp_path),
            operator_id="op-001",
        )
        # svc.create should have been called with the export receipt
        svc.create.assert_called_once()
        receipt_arg = svc.create.call_args[0][0]
        assert receipt_arg.action_type == ActionType.COMPLIANCE_EXPORT_GENERATED.value

    def test_period_error_returns_error_result(self, tmp_path):
        svc = MagicMock()
        svc.list.return_value = []
        conn = MagicMock()
        svc._get_connection.return_value = conn

        result = run_export(
            receipt_service=svc,
            export_format=ExportFormat.SOC2_JSON,
            period_start="2026-01-01",
            period_end="2026-01-31",
            data_dir=str(tmp_path),
            operator_id="op-001",
        )
        assert result.success is False
        assert "No receipts found" in result.error

    def test_invalid_period_start_after_end(self, tmp_path):
        svc = MagicMock()
        result = run_export(
            receipt_service=svc,
            export_format=ExportFormat.SOC2_JSON,
            period_start="2026-02-01",
            period_end="2026-01-01",
            data_dir=str(tmp_path),
            operator_id="op-001",
        )
        assert result.success is False
        assert "must be before" in result.error

    def test_export_duration_recorded(self, tmp_path):
        svc = _mock_receipt_service_for_export()
        result = run_export(
            receipt_service=svc,
            export_format=ExportFormat.SOC2_JSON,
            period_start="2026-01-01",
            period_end="2026-01-31",
            data_dir=str(tmp_path),
            operator_id="op-001",
        )
        assert result.export_duration_ms > 0

    def test_quest_id_passed_through(self, tmp_path):
        svc = _mock_receipt_service_for_export()
        result = run_export(
            receipt_service=svc,
            export_format=ExportFormat.SOC2_JSON,
            period_start="2026-01-01",
            period_end="2026-01-31",
            data_dir=str(tmp_path),
            operator_id="op-001",
            quest_id="q-42",
        )
        assert result.quest_id == "q-42"

    def test_unknown_format_produces_error_output(self, tmp_path):
        svc = _mock_receipt_service_for_export()
        result = run_export(
            receipt_service=svc,
            export_format="UNKNOWN_FORMAT",
            period_start="2026-01-01",
            period_end="2026-01-31",
            data_dir=str(tmp_path),
            operator_id="op-001",
        )
        assert result.success is True  # file is written, just with error content
        with zipfile.ZipFile(result.output_path) as zf:
            json_name = next(
                name for name in zf.namelist()
                if name.endswith(".json") and name != "manifest.json"
            )
            data = json.loads(zf.read(json_name))
        assert "error" in data

    def test_pdf_format_still_produces_pdf(self, tmp_path):
        svc = _mock_receipt_service_for_export()
        result = run_export(
            receipt_service=svc,
            export_format=ExportFormat.PDF,
            period_start="2026-01-01",
            period_end="2026-01-31",
            data_dir=str(tmp_path),
            operator_id="op-001",
        )
        assert result.success is True
        assert result.output_path.endswith(".pdf")

    def test_export_receipt_failure_does_not_fail_export(self, tmp_path):
        svc = _mock_receipt_service_for_export()
        svc.create.side_effect = RuntimeError("DB error")
        result = run_export(
            receipt_service=svc,
            export_format=ExportFormat.SOC2_JSON,
            period_start="2026-01-01",
            period_end="2026-01-31",
            data_dir=str(tmp_path),
            operator_id="op-001",
        )
        # Export itself should still succeed
        assert result.success is True
        assert Path(result.output_path).exists()

    def test_export_creates_subdirectory(self, tmp_path):
        svc = _mock_receipt_service_for_export()
        run_export(
            receipt_service=svc,
            export_format=ExportFormat.SOC2_JSON,
            period_start="2026-01-01",
            period_end="2026-01-31",
            data_dir=str(tmp_path),
            operator_id="op-001",
        )
        assert (tmp_path / "compliance_exports").exists()

    def test_chain_integrity_included_in_result(self, tmp_path):
        svc = _mock_receipt_service_for_export()
        result = run_export(
            receipt_service=svc,
            export_format=ExportFormat.SOC2_JSON,
            period_start="2026-01-01",
            period_end="2026-01-31",
            data_dir=str(tmp_path),
            operator_id="op-001",
        )
        assert result.chain_integrity.status == "CHAIN_INTACT"
