# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Unit tests for the Compliance Export API endpoints.

Tests verify each endpoint's behavior including format listing, export
generation, download, chain integrity, history, verification, and
error handling when the receipt service is not initialised.
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "core"))

import json
import hashlib
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from pathlib import Path

from src.compliance.api import (
    router,
    init_compliance_api,
    ExportRequest,
    ExportResponse,
    ExportHistoryEntry,
    ChainIntegrityResponse,
    _receipt_service,
    _data_dir,
)


# ---------------------------------------------------------------------------
# Attempt to use FastAPI TestClient; fall back to direct mock testing
# ---------------------------------------------------------------------------

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    _app = FastAPI()
    _app.include_router(router)
    HAS_TESTCLIENT = True
except ImportError:
    HAS_TESTCLIENT = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_client(receipt_service=None, data_dir="/tmp/test_data"):
    """Create a TestClient with initialised compliance API."""
    import src.compliance.api as api_mod
    api_mod._receipt_service = receipt_service
    api_mod._data_dir = data_dir
    return TestClient(_app)


def _make_mock_receipt(
    id="r-001",
    action_type="compliance_export_generated",
    timestamp="2026-01-31T00:00:00Z",
    inputs=None,
    outputs=None,
    operator_id="op-001",
):
    r = MagicMock()
    r.id = id
    r.action_type = action_type
    r.timestamp = timestamp
    r.inputs = inputs or {}
    r.outputs = outputs or {}
    r.operator_id = operator_id
    return r


# ---------------------------------------------------------------------------
# Model tests (always run)
# ---------------------------------------------------------------------------

class TestModels:
    def test_export_request_required_fields(self):
        req = ExportRequest(
            format="SOC2_JSON",
            period_start="2026-01-01",
            period_end="2026-01-31",
        )
        assert req.format == "SOC2_JSON"
        assert req.anomaly_threshold == 5  # default

    def test_export_request_optional_fields(self):
        req = ExportRequest(
            format="PDF",
            period_start="2026-01-01",
            period_end="2026-01-31",
            quest_id="q-1",
            anomaly_threshold=10,
        )
        assert req.quest_id == "q-1"
        assert req.anomaly_threshold == 10

    def test_chain_integrity_response(self):
        resp = ChainIntegrityResponse(
            status="CHAIN_INTACT",
            period_start="2026-01-01",
            period_end="2026-01-31",
            total_receipts=100,
            receipts_with_parents=90,
            orphaned_count=0,
            is_intact=True,
        )
        assert resp.is_intact is True


# ---------------------------------------------------------------------------
# init_compliance_api tests
# ---------------------------------------------------------------------------

class TestInit:
    def test_init_sets_module_globals(self):
        import src.compliance.api as api_mod
        svc = MagicMock()
        init_compliance_api(svc, "/custom/data")
        assert api_mod._receipt_service is svc
        assert api_mod._data_dir == "/custom/data"


# ---------------------------------------------------------------------------
# Endpoint tests (require FastAPI TestClient)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not HAS_TESTCLIENT, reason="FastAPI/TestClient not available")
class TestFormatsEndpoint:
    def test_get_formats_returns_list(self):
        client = _get_client(receipt_service=MagicMock())
        resp = client.get("/api/compliance/formats")
        assert resp.status_code == 200
        data = resp.json()
        assert "formats" in data
        format_ids = [f["id"] for f in data["formats"]]
        assert "PDF" in format_ids
        assert "SOC2_JSON" in format_ids
        assert "ISO27001_JSON" in format_ids
        assert "GDPR_JSON" in format_ids

    def test_each_format_has_required_keys(self):
        client = _get_client(receipt_service=MagicMock())
        resp = client.get("/api/compliance/formats")
        for fmt in resp.json()["formats"]:
            assert "id" in fmt
            assert "name" in fmt
            assert "description" in fmt
            assert "available" in fmt


@pytest.mark.skipif(not HAS_TESTCLIENT, reason="FastAPI/TestClient not available")
class TestExportEndpoint:
    @patch("src.core.auth_api.resolve_operator_identity")
    @patch("src.core.auth_api.get_api_key_identity")
    @patch("src.compliance.export_engine.run_export")
    def test_post_export_triggers_pipeline(self, mock_run, mock_api_key, mock_resolve):
        identity = MagicMock()
        identity.operator_id = "op-001"
        identity.session_id = "sess-001"
        mock_resolve.return_value = identity

        from src.compliance.chain_integrity import ChainIntegrityResult
        chain = ChainIntegrityResult(
            status="CHAIN_INTACT", period_start="2026-01-01",
            period_end="2026-01-31", total_receipts=5,
            receipts_with_parents=3, orphaned_count=0,
        )
        from src.compliance.export_engine import ExportResult
        mock_run.return_value = ExportResult(
            export_id="e-001", export_format="SOC2_JSON",
            period_start="2026-01-01", period_end="2026-01-31",
            receipt_count=5, chain_integrity=chain,
            output_path="/tmp/test.json", output_sha256="abc123",
            export_duration_ms=150.0, generated_at="2026-01-31T00:00:00Z",
        )

        client = _get_client(receipt_service=MagicMock())
        resp = client.post("/api/compliance/export", json={
            "format": "SOC2_JSON",
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["export_id"] == "e-001"
        assert data["success"] is True
        assert data["download_url"] == "/api/compliance/download/e-001"

    @patch("src.core.auth_api.resolve_operator_identity")
    @patch("src.core.auth_api.get_api_key_identity")
    def test_invalid_format_returns_400(self, mock_api_key, mock_resolve):
        mock_resolve.return_value = MagicMock(operator_id="op-001", session_id="s1")
        client = _get_client(receipt_service=MagicMock())
        resp = client.post("/api/compliance/export", json={
            "format": "INVALID_FORMAT",
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
        })
        assert resp.status_code == 400


@pytest.mark.skipif(not HAS_TESTCLIENT, reason="FastAPI/TestClient not available")
class TestDownloadEndpoint:
    def test_download_nonexistent_export_returns_404(self, tmp_path):
        client = _get_client(receipt_service=MagicMock(), data_dir=str(tmp_path))
        resp = client.get("/api/compliance/download/nonexistent-id")
        assert resp.status_code == 404

    def test_download_existing_export(self, tmp_path):
        # Create a fake export file
        export_dir = tmp_path / "compliance_exports"
        export_dir.mkdir()
        fake_file = export_dir / "soc2_json_2026-01-01_2026-01-31_abcdefgh.json"
        fake_file.write_text('{"test": true}')

        client = _get_client(receipt_service=MagicMock(), data_dir=str(tmp_path))
        resp = client.get("/api/compliance/download/abcdefgh-1234-5678")
        assert resp.status_code == 200


@pytest.mark.skipif(not HAS_TESTCLIENT, reason="FastAPI/TestClient not available")
class TestChainIntegrityEndpoint:
    @patch("src.compliance.chain_integrity.check_chain_integrity")
    def test_returns_chain_status(self, mock_check):
        from src.compliance.chain_integrity import ChainIntegrityResult
        mock_check.return_value = ChainIntegrityResult(
            status="CHAIN_INTACT", period_start="2026-01-01",
            period_end="2026-01-31", total_receipts=50,
            receipts_with_parents=45, orphaned_count=0,
        )
        client = _get_client(receipt_service=MagicMock())
        resp = client.get(
            "/api/compliance/chain-integrity",
            params={"period_start": "2026-01-01", "period_end": "2026-01-31"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "CHAIN_INTACT"
        assert data["is_intact"] is True


@pytest.mark.skipif(not HAS_TESTCLIENT, reason="FastAPI/TestClient not available")
class TestHistoryEndpoint:
    def test_returns_export_history(self):
        svc = MagicMock()
        svc.list.return_value = [
            _make_mock_receipt(
                inputs={"export_format": "SOC2_JSON", "period_start": "2026-01-01", "period_end": "2026-01-31"},
                outputs={
                    "export_id": "e-001", "receipt_count_exported": 10,
                    "chain_integrity": "CHAIN_INTACT", "output_sha256": "abc",
                    "export_duration_ms": 100, "output_path": "/data/test.json",
                },
            ),
        ]
        client = _get_client(receipt_service=svc)
        resp = client.get("/api/compliance/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["exports"]) == 1
        assert data["exports"][0]["export_id"] == "e-001"

    def test_empty_history(self):
        svc = MagicMock()
        svc.list.return_value = []
        client = _get_client(receipt_service=svc)
        resp = client.get("/api/compliance/history")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


@pytest.mark.skipif(not HAS_TESTCLIENT, reason="FastAPI/TestClient not available")
class TestVerifyEndpoint:
    def test_verify_nonexistent_returns_404(self, tmp_path):
        client = _get_client(receipt_service=MagicMock(), data_dir=str(tmp_path))
        # Need the directory to exist but empty
        (tmp_path / "compliance_exports").mkdir()
        resp = client.post("/api/compliance/verify/nonexistent-id")
        assert resp.status_code == 404

    def test_verify_matching_hash(self, tmp_path):
        export_dir = tmp_path / "compliance_exports"
        export_dir.mkdir()
        content = b'{"test": true}'
        fake_file = export_dir / "soc2_json_2026-01-01_2026-01-31_abcdefgh.json"
        fake_file.write_bytes(content)
        expected_hash = hashlib.sha256(content).hexdigest()

        svc = MagicMock()
        svc.list.return_value = [
            _make_mock_receipt(
                outputs={"export_id": "abcdefgh-1234", "output_sha256": expected_hash}
            ),
        ]
        client = _get_client(receipt_service=svc, data_dir=str(tmp_path))
        resp = client.post("/api/compliance/verify/abcdefgh-1234-5678")
        assert resp.status_code == 200
        data = resp.json()
        assert data["verified"] is True
        assert data["mismatch"] is False

    def test_verify_mismatched_hash(self, tmp_path):
        export_dir = tmp_path / "compliance_exports"
        export_dir.mkdir()
        fake_file = export_dir / "soc2_json_2026-01-01_2026-01-31_abcdefgh.json"
        fake_file.write_bytes(b'{"test": true}')

        svc = MagicMock()
        svc.list.return_value = [
            _make_mock_receipt(
                outputs={"export_id": "abcdefgh-1234", "output_sha256": "wrong_hash"}
            ),
        ]
        client = _get_client(receipt_service=svc, data_dir=str(tmp_path))
        resp = client.post("/api/compliance/verify/abcdefgh-1234-5678")
        data = resp.json()
        assert data["verified"] is False
        assert data["mismatch"] is True


# ---------------------------------------------------------------------------
# Receipt service not initialised (503) tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not HAS_TESTCLIENT, reason="FastAPI/TestClient not available")
class TestServiceNotInitialised:
    def test_export_returns_503(self):
        client = _get_client(receipt_service=None)
        resp = client.post("/api/compliance/export", json={
            "format": "SOC2_JSON",
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
        })
        assert resp.status_code == 503

    def test_chain_integrity_returns_503(self):
        client = _get_client(receipt_service=None)
        resp = client.get(
            "/api/compliance/chain-integrity",
            params={"period_start": "2026-01-01", "period_end": "2026-01-31"},
        )
        assert resp.status_code == 503

    def test_history_returns_503(self):
        client = _get_client(receipt_service=None)
        resp = client.get("/api/compliance/history")
        assert resp.status_code == 503

    def test_verify_returns_503(self):
        client = _get_client(receipt_service=None)
        resp = client.post("/api/compliance/verify/some-id")
        assert resp.status_code == 503
