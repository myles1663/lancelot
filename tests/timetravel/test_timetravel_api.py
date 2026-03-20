# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Unit tests for the Time-Travel Debugging REST API.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "core"))

import pytest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.timetravel import api as tt_api
from src.timetravel.api import router
from src.timetravel.resume_engine import (
    ForkResult,
    ReplayResult,
    InspectionResult,
)
from src.timetravel.state_snapshot import StateSnapshot


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_api_globals():
    """Reset module-level globals before and after each test."""
    original = (
        tt_api._receipt_service,
        tt_api._soul,
        tt_api._resume_engine,
        tt_api._snapshot_reader,
    )
    yield
    (
        tt_api._receipt_service,
        tt_api._soul,
        tt_api._resume_engine,
        tt_api._snapshot_reader,
    ) = original


@pytest.fixture
def mock_soul():
    soul = MagicMock()
    soul.version = "v2"
    soul.fork_permissions = MagicMock()
    soul.fork_permissions.allow_fork = True
    soul.fork_permissions.require_approval_tier = 3
    return soul


@pytest.fixture
def mock_receipt_service():
    svc = MagicMock()
    return svc


@pytest.fixture
def mock_resume_engine():
    return MagicMock()


@pytest.fixture
def mock_snapshot_reader():
    return MagicMock()


@pytest.fixture
def client(mock_soul, mock_receipt_service, mock_resume_engine, mock_snapshot_reader):
    """Create a FastAPI test client with injected mocks."""
    tt_api._receipt_service = mock_receipt_service
    tt_api._soul = mock_soul
    tt_api._resume_engine = mock_resume_engine
    tt_api._snapshot_reader = mock_snapshot_reader

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def uninitialized_client():
    """Client with no engine initialized (feature disabled)."""
    tt_api._receipt_service = None
    tt_api._soul = None
    tt_api._resume_engine = None
    tt_api._snapshot_reader = None

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_receipt(receipt_id="r-001", quest_id="q-001"):
    r = MagicMock()
    r.id = receipt_id
    r.quest_id = quest_id
    r.to_dict.return_value = {"id": receipt_id, "quest_id": quest_id}
    return r


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------

class TestGetStatus:
    def test_returns_enabled_state(self, client):
        resp = client.get("/api/timetravel/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["soul_version"] == "v2"

    def test_returns_disabled_when_uninitialized(self, uninitialized_client):
        resp = uninitialized_client.get("/api/timetravel/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False


# ---------------------------------------------------------------------------
# GET /quest/{quest_id}/receipts
# ---------------------------------------------------------------------------

class TestGetQuestReceipts:
    def test_returns_receipt_list(self, client, mock_receipt_service):
        r1 = _make_mock_receipt("r-1")
        r2 = _make_mock_receipt("r-2")
        mock_receipt_service.get_quest_receipts.return_value = [r1, r2]

        resp = client.get("/api/timetravel/quest/q-001/receipts")
        assert resp.status_code == 200
        data = resp.json()
        assert data["quest_id"] == "q-001"
        assert data["receipt_count"] == 2
        assert len(data["receipts"]) == 2

    def test_no_receipts_returns_404(self, client, mock_receipt_service):
        mock_receipt_service.get_quest_receipts.return_value = []
        resp = client.get("/api/timetravel/quest/q-missing/receipts")
        assert resp.status_code == 404

    def test_feature_disabled_returns_503(self, uninitialized_client):
        resp = uninitialized_client.get("/api/timetravel/quest/q-001/receipts")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /receipt/{receipt_id}/snapshot
# ---------------------------------------------------------------------------

class TestGetReceiptSnapshot:
    def test_returns_state_snapshot(self, client, mock_snapshot_reader):
        snap = StateSnapshot(
            timestamp="2026-01-15T10:00:00Z",
            receipt_id="r-001",
            quest_id="q-001",
            soul_version="v2",
        )
        mock_snapshot_reader.read_snapshot.return_value = snap

        resp = client.get("/api/timetravel/receipt/r-001/snapshot")
        assert resp.status_code == 200
        data = resp.json()
        assert data["receipt_id"] == "r-001"
        assert data["soul_version"] == "v2"

    def test_not_found_returns_404(self, client, mock_snapshot_reader):
        mock_snapshot_reader.read_snapshot.side_effect = ValueError(
            "Receipt not found: r-bad"
        )
        resp = client.get("/api/timetravel/receipt/r-bad/snapshot")
        assert resp.status_code == 404

    def test_feature_disabled_returns_503(self, uninitialized_client):
        resp = uninitialized_client.get("/api/timetravel/receipt/r-001/snapshot")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# POST /inspect
# ---------------------------------------------------------------------------

class TestPostInspect:
    def test_returns_inspection_result(self, client, mock_resume_engine):
        mock_resume_engine.create_inspection.return_value = InspectionResult(
            success=True,
            receipt_id="r-inspect",
            snapshot={"timestamp": "2026-01-15T10:00:00Z"},
        )
        resp = client.post(
            "/api/timetravel/inspect",
            json={"receipt_id": "r-001"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["receipt_id"] == "r-inspect"

    def test_feature_disabled_returns_503(self, uninitialized_client):
        resp = uninitialized_client.post(
            "/api/timetravel/inspect",
            json={"receipt_id": "r-001"},
        )
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# POST /replay
# ---------------------------------------------------------------------------

class TestPostReplay:
    def test_valid_quest_returns_replay_result(self, client, mock_resume_engine):
        mock_resume_engine.create_replay.return_value = ReplayResult(
            success=True,
            replay_quest_id="rq-new",
            source_quest_id="q-001",
            receipt_id="r-replay",
            approval_status="approved",
        )
        resp = client.post(
            "/api/timetravel/replay",
            json={"source_quest_id": "q-001"},
            headers={"X-Operator-ID": "op-1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["replay_quest_id"] == "rq-new"

    def test_missing_operator_returns_401(self, client):
        resp = client.post(
            "/api/timetravel/replay",
            json={"source_quest_id": "q-001"},
        )
        assert resp.status_code == 401

    def test_invalid_quest_returns_error(self, client, mock_resume_engine):
        mock_resume_engine.create_replay.return_value = ReplayResult(
            success=False,
            source_quest_id="q-bad",
            error="Source quest not found: q-bad",
        )
        resp = client.post(
            "/api/timetravel/replay",
            json={"source_quest_id": "q-bad"},
            headers={"X-Operator-ID": "op-1"},
        )
        assert resp.status_code == 400

    def test_soul_rejects_returns_403(self, client, mock_resume_engine):
        mock_resume_engine.create_replay.return_value = ReplayResult(
            success=False,
            source_quest_id="q-001",
            error="Soul fork_permissions.allow_fork is false",
            approval_status="rejected",
        )
        resp = client.post(
            "/api/timetravel/replay",
            json={"source_quest_id": "q-001"},
            headers={"X-Operator-ID": "op-1"},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /fork
# ---------------------------------------------------------------------------

class TestPostFork:
    def test_valid_request_returns_fork_result(self, client, mock_resume_engine):
        mock_resume_engine.create_fork.return_value = ForkResult(
            success=True,
            fork_quest_id="fq-new",
            source_quest_id="q-001",
            receipt_id="r-fork",
            approval_status="approved",
            modifications_applied={"inputs.query": "new"},
        )
        resp = client.post(
            "/api/timetravel/fork",
            json={
                "source_quest_id": "q-001",
                "modifications": {"inputs.query": "new"},
            },
            headers={"X-Operator-ID": "op-1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["fork_quest_id"] == "fq-new"

    def test_missing_operator_returns_401(self, client):
        resp = client.post(
            "/api/timetravel/fork",
            json={"source_quest_id": "q-001", "modifications": {}},
        )
        assert resp.status_code == 401

    def test_soul_rejects_returns_403(self, client, mock_resume_engine):
        mock_resume_engine.create_fork.return_value = ForkResult(
            success=False,
            source_quest_id="q-001",
            error="Prohibited field: operator_id",
            approval_status="rejected",
        )
        resp = client.post(
            "/api/timetravel/fork",
            json={
                "source_quest_id": "q-001",
                "modifications": {"operator_id": "evil"},
            },
            headers={"X-Operator-ID": "op-1"},
        )
        assert resp.status_code == 403

    def test_t3_rejected_returns_403(self, client, mock_resume_engine):
        mock_resume_engine.create_fork.return_value = ForkResult(
            success=False,
            source_quest_id="q-001",
            error="Insufficient trust tier",
            approval_status="rejected",
        )
        resp = client.post(
            "/api/timetravel/fork",
            json={
                "source_quest_id": "q-001",
                "modifications": {"inputs.q": "new"},
            },
            headers={"X-Operator-ID": "op-1"},
        )
        assert resp.status_code == 403

    def test_feature_disabled_returns_503(self, uninitialized_client):
        resp = uninitialized_client.post(
            "/api/timetravel/fork",
            json={"source_quest_id": "q-001", "modifications": {}},
            headers={"X-Operator-ID": "op-1"},
        )
        assert resp.status_code == 503
