# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Unit tests for resume_engine — fork, replay, and inspect pipelines.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "core"))

import pytest
from unittest.mock import MagicMock, patch, call

from src.timetravel.resume_engine import (
    ResumeEngine,
    ForkResult,
    ReplayResult,
    InspectionResult,
)
from src.timetravel.state_snapshot import StateSnapshot
from src.core.soul.store import ForkPermissions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_soul(
    allow_fork=True,
    require_approval_tier=0,
    modifiable_fields=None,
    prohibited_modifications=None,
    version="v2",
):
    soul = MagicMock()
    soul.version = version
    if modifiable_fields is None:
        modifiable_fields = ["inputs"]
    if prohibited_modifications is None:
        prohibited_modifications = [
            "operator_id", "session_id", "quest_id", "timestamp", "id",
        ]
    soul.fork_permissions = ForkPermissions(
        allow_fork=allow_fork,
        require_approval_tier=require_approval_tier,
        modifiable_fields=modifiable_fields,
        prohibited_modifications=prohibited_modifications,
    )
    return soul


def _make_receipt(
    receipt_id="r-001",
    quest_id="q-001",
    timestamp="2026-01-15T10:00:00Z",
    tier=0,
):
    r = MagicMock()
    r.id = receipt_id
    r.quest_id = quest_id
    r.timestamp = timestamp
    r.tier = tier
    r.to_dict.return_value = {"id": receipt_id, "quest_id": quest_id}
    return r


def _make_receipt_service(quest_receipts=None):
    svc = MagicMock()
    svc.get_quest_receipts.return_value = quest_receipts or []
    svc.create.return_value = None
    return svc


def _make_snapshot_reader(snapshot=None):
    reader = MagicMock()
    if snapshot is None:
        snapshot = StateSnapshot(
            timestamp="2026-01-15T10:00:00Z",
            receipt_id="r-001",
            quest_id="q-001",
        )
    reader.read_snapshot.return_value = snapshot
    return reader


# ---------------------------------------------------------------------------
# Result type serialization
# ---------------------------------------------------------------------------

class TestResultTypes:
    def test_fork_result_to_dict(self):
        r = ForkResult(
            success=True,
            fork_quest_id="fq-1",
            source_quest_id="sq-1",
            receipt_id="r-1",
            approval_status="approved",
            modifications_applied={"inputs.q": "new"},
        )
        d = r.to_dict()
        assert d["success"] is True
        assert d["fork_quest_id"] == "fq-1"
        assert d["source_quest_id"] == "sq-1"
        assert d["modifications_applied"] == {"inputs.q": "new"}

    def test_replay_result_to_dict(self):
        r = ReplayResult(
            success=True,
            replay_quest_id="rq-1",
            source_quest_id="sq-1",
            receipt_id="r-1",
            approval_status="approved",
        )
        d = r.to_dict()
        assert d["success"] is True
        assert d["replay_quest_id"] == "rq-1"

    def test_inspection_result_to_dict(self):
        r = InspectionResult(
            success=True,
            receipt_id="r-1",
            snapshot={"timestamp": "2026-01-15T10:00:00Z"},
        )
        d = r.to_dict()
        assert d["success"] is True
        assert d["snapshot"]["timestamp"] == "2026-01-15T10:00:00Z"

    def test_fork_result_error_to_dict(self):
        r = ForkResult(success=False, error="Something broke")
        d = r.to_dict()
        assert d["success"] is False
        assert d["error"] == "Something broke"
        assert d["fork_quest_id"] is None


# ---------------------------------------------------------------------------
# create_inspection
# ---------------------------------------------------------------------------

class TestCreateInspection:
    def test_emits_time_travel_inspect_receipt(self):
        soul = _make_soul()
        svc = _make_receipt_service()
        reader = _make_snapshot_reader()
        engine = ResumeEngine(svc, soul, reader)

        result = engine.create_inspection("r-001", operator_id="op-1")
        assert result.success is True
        assert svc.create.called
        created_receipt = svc.create.call_args[0][0]
        assert created_receipt.action_type == "time_travel_inspect"

    def test_returns_inspection_result_with_snapshot(self):
        soul = _make_soul()
        svc = _make_receipt_service()
        snap = StateSnapshot(
            timestamp="2026-01-15T10:00:00Z",
            receipt_id="r-001",
        )
        reader = _make_snapshot_reader(snap)
        engine = ResumeEngine(svc, soul, reader)

        result = engine.create_inspection("r-001")
        assert isinstance(result, InspectionResult)
        assert result.success is True
        assert result.snapshot is not None
        assert result.snapshot["receipt_id"] == "r-001"

    def test_snapshot_reader_not_found_returns_error(self):
        soul = _make_soul()
        svc = _make_receipt_service()
        reader = MagicMock()
        reader.read_snapshot.side_effect = ValueError("Receipt not found: r-bad")
        engine = ResumeEngine(svc, soul, reader)

        result = engine.create_inspection("r-bad")
        assert result.success is False
        assert "not found" in result.error

    def test_no_snapshot_reader(self):
        soul = _make_soul()
        svc = _make_receipt_service()
        engine = ResumeEngine(svc, soul, snapshot_reader=None)

        result = engine.create_inspection("r-001")
        assert result.success is True
        assert result.snapshot is None


# ---------------------------------------------------------------------------
# create_replay
# ---------------------------------------------------------------------------

class TestCreateReplay:
    def test_quest_not_found(self):
        soul = _make_soul()
        svc = _make_receipt_service(quest_receipts=[])
        engine = ResumeEngine(svc, soul)

        result = engine.create_replay("q-nonexistent")
        assert result.success is False
        assert "not found" in result.error

    def test_soul_rejects_replay(self):
        soul = _make_soul(allow_fork=False)
        svc = _make_receipt_service(quest_receipts=[_make_receipt()])
        engine = ResumeEngine(svc, soul)

        result = engine.create_replay("q-001")
        assert result.success is False
        assert result.approval_status == "rejected"

    @patch("src.timetravel.resume_engine.ResumeEngine._get_current_trust_tier")
    def test_t3_approval_gate_approved(self, mock_tier):
        mock_tier.return_value = 3
        soul = _make_soul(require_approval_tier=3)
        svc = _make_receipt_service(quest_receipts=[_make_receipt()])
        engine = ResumeEngine(
            svc,
            soul,
            quest_executor=lambda **kwargs: {"run_id": "run-approved", "status": "SUCCEEDED"},
        )

        result = engine.create_replay("q-001", operator_id="op-1")
        assert result.success is True
        assert result.approval_status == "approved"

    @patch("src.timetravel.resume_engine.ResumeEngine._get_current_trust_tier")
    def test_t3_approval_gate_rejected_insufficient_tier(self, mock_tier):
        mock_tier.return_value = 1
        soul = _make_soul(require_approval_tier=3)
        svc = _make_receipt_service(quest_receipts=[_make_receipt()])
        engine = ResumeEngine(svc, soul)

        result = engine.create_replay("q-001", operator_id="op-1")
        assert result.success is False
        assert result.approval_status == "rejected"
        assert "Insufficient trust tier" in result.error

    @patch("src.timetravel.resume_engine.ResumeEngine._get_current_trust_tier")
    def test_mints_new_quest_id(self, mock_tier):
        mock_tier.return_value = 3
        soul = _make_soul(require_approval_tier=0)
        svc = _make_receipt_service(quest_receipts=[_make_receipt()])
        engine = ResumeEngine(
            svc,
            soul,
            quest_executor=lambda **kwargs: {"run_id": "run-mint", "status": "SUCCEEDED"},
        )

        result = engine.create_replay("q-001")
        assert result.success is True
        assert result.replay_quest_id is not None
        assert result.replay_quest_id != "q-001"

    @patch("src.timetravel.resume_engine.ResumeEngine._get_current_trust_tier")
    def test_emits_quest_replayed_receipt(self, mock_tier):
        mock_tier.return_value = 3
        soul = _make_soul()
        svc = _make_receipt_service(quest_receipts=[_make_receipt()])
        engine = ResumeEngine(
            svc,
            soul,
            quest_executor=lambda **kwargs: {"run_id": "run-receipt", "status": "SUCCEEDED"},
        )

        result = engine.create_replay("q-001")
        assert result.success is True
        # Find the QUEST_REPLAYED receipt in create calls
        replay_receipts = [
            c[0][0] for c in svc.create.call_args_list
            if c[0][0].action_type == "quest_replayed"
        ]
        assert len(replay_receipts) >= 1

    @patch("src.timetravel.resume_engine.ResumeEngine._get_current_trust_tier")
    def test_replay_executes_via_injected_callback(self, mock_tier):
        mock_tier.return_value = 3
        soul = _make_soul()
        svc = _make_receipt_service(quest_receipts=[_make_receipt()])
        executor = MagicMock(return_value={"run_id": "run-1", "status": "SUCCEEDED"})
        engine = ResumeEngine(svc, soul, quest_executor=executor)

        result = engine.create_replay("q-001", operator_id="op-1", session_id="sess-1")

        assert result.success is True
        executor.assert_called_once_with(
            mode="replay",
            source_quest_id="q-001",
            new_quest_id=result.replay_quest_id,
            modifications={},
            operator_id="op-1",
            session_id="sess-1",
        )

    @patch("src.timetravel.resume_engine.ResumeEngine._get_current_trust_tier")
    def test_replay_fails_closed_without_executor(self, mock_tier):
        mock_tier.return_value = 3
        soul = _make_soul()
        svc = _make_receipt_service(quest_receipts=[_make_receipt()])
        engine = ResumeEngine(svc, soul)

        result = engine.create_replay("q-001")

        assert result.success is False
        assert "executor" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# create_fork
# ---------------------------------------------------------------------------

class TestCreateFork:
    @patch("src.timetravel.resume_engine.ResumeEngine._get_current_trust_tier")
    def test_full_pipeline_success(self, mock_tier):
        mock_tier.return_value = 3
        soul = _make_soul(modifiable_fields=["inputs"])
        svc = _make_receipt_service(quest_receipts=[_make_receipt()])
        engine = ResumeEngine(
            svc,
            soul,
            quest_executor=lambda **kwargs: {"run_id": "fork-success", "status": "SUCCEEDED"},
        )

        result = engine.create_fork(
            "q-001", {"inputs.query": "new prompt"}, operator_id="op-1"
        )
        assert result.success is True
        assert result.fork_quest_id is not None
        assert result.source_quest_id == "q-001"
        assert result.modifications_applied == {"inputs.query": "new prompt"}
        assert result.approval_status == "approved"

    def test_quest_not_found(self):
        soul = _make_soul()
        svc = _make_receipt_service(quest_receipts=[])
        engine = ResumeEngine(svc, soul)

        result = engine.create_fork("q-missing", {"inputs.q": "x"})
        assert result.success is False
        assert "not found" in result.error

    def test_soul_validation_rejects_prohibited_field(self):
        soul = _make_soul()
        svc = _make_receipt_service(quest_receipts=[_make_receipt()])
        engine = ResumeEngine(svc, soul)

        result = engine.create_fork("q-001", {"operator_id": "evil"})
        assert result.success is False
        assert result.approval_status == "rejected"

    @patch("src.timetravel.resume_engine.ResumeEngine._get_current_trust_tier")
    def test_t3_approval_gate_blocks(self, mock_tier):
        mock_tier.return_value = 1
        soul = _make_soul(require_approval_tier=3, modifiable_fields=["inputs"])
        svc = _make_receipt_service(quest_receipts=[_make_receipt()])
        engine = ResumeEngine(svc, soul)

        result = engine.create_fork("q-001", {"inputs.q": "new"})
        assert result.success is False
        assert result.approval_status == "rejected"

    @patch("src.timetravel.resume_engine.ResumeEngine._get_current_trust_tier")
    def test_emits_quest_forked_receipt(self, mock_tier):
        mock_tier.return_value = 3
        soul = _make_soul(modifiable_fields=["inputs"])
        svc = _make_receipt_service(quest_receipts=[_make_receipt()])
        engine = ResumeEngine(
            svc,
            soul,
            quest_executor=lambda **kwargs: {"run_id": "fork-receipt", "status": "SUCCEEDED"},
        )

        result = engine.create_fork("q-001", {"inputs.q": "new"})
        assert result.success is True
        forked_receipts = [
            c[0][0] for c in svc.create.call_args_list
            if c[0][0].action_type == "quest_forked"
        ]
        assert len(forked_receipts) >= 1

    @patch("src.timetravel.resume_engine.ResumeEngine._get_current_trust_tier")
    def test_risk_reclassification_uses_max_tier(self, mock_tier):
        mock_tier.return_value = 3
        soul = _make_soul(modifiable_fields=["inputs"])
        r1 = _make_receipt(tier=1)
        r2 = _make_receipt(tier=2)
        r3 = _make_receipt(tier=0)
        svc = _make_receipt_service(quest_receipts=[r1, r2, r3])
        engine = ResumeEngine(
            svc,
            soul,
            quest_executor=lambda **kwargs: {"run_id": "fork-tier", "status": "SUCCEEDED"},
        )

        result = engine.create_fork("q-001", {"inputs.q": "new"})
        assert result.success is True
        # Check that the forked receipt metadata includes max tier
        forked_receipt = [
            c[0][0] for c in svc.create.call_args_list
            if c[0][0].action_type == "quest_forked"
        ][0]
        assert forked_receipt.metadata["risk_reclassification_tier"] == 2

    @patch("src.timetravel.resume_engine.ResumeEngine._get_current_trust_tier")
    def test_fork_executes_via_injected_callback(self, mock_tier):
        mock_tier.return_value = 3
        soul = _make_soul(modifiable_fields=["inputs"])
        svc = _make_receipt_service(quest_receipts=[_make_receipt()])
        executor = MagicMock(return_value={"run_id": "run-2", "status": "SUCCEEDED"})
        engine = ResumeEngine(svc, soul, quest_executor=executor)

        result = engine.create_fork(
            "q-001",
            {"inputs.query": "new"},
            operator_id="op-1",
            session_id="sess-1",
        )

        assert result.success is True
        executor.assert_called_once_with(
            mode="fork",
            source_quest_id="q-001",
            new_quest_id=result.fork_quest_id,
            modifications={"inputs.query": "new"},
            operator_id="op-1",
            session_id="sess-1",
        )

    @patch("src.timetravel.resume_engine.ResumeEngine._get_current_trust_tier")
    def test_fork_fails_closed_without_executor(self, mock_tier):
        mock_tier.return_value = 3
        soul = _make_soul(modifiable_fields=["inputs"])
        svc = _make_receipt_service(quest_receipts=[_make_receipt()])
        engine = ResumeEngine(svc, soul)

        result = engine.create_fork("q-001", {"inputs.query": "new"})

        assert result.success is False
        assert "executor" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# _request_approval
# ---------------------------------------------------------------------------

class TestRequestApproval:
    @patch("src.timetravel.resume_engine.ResumeEngine._get_current_trust_tier")
    def test_emits_t3_fork_approval_request(self, mock_tier):
        mock_tier.return_value = 3
        soul = _make_soul()
        svc = _make_receipt_service()
        engine = ResumeEngine(svc, soul)

        engine._request_approval(
            mode="fork",
            source_quest_id="q-001",
            required_tier=2,
            operator_id="op-1",
            session_id="s-1",
        )
        # First create call should be the approval request
        request_receipt = svc.create.call_args_list[0][0][0]
        assert request_receipt.action_type == "t3_fork_approval_request"

    @patch("src.timetravel.resume_engine.ResumeEngine._get_current_trust_tier")
    def test_emits_t3_fork_approved_when_tier_sufficient(self, mock_tier):
        mock_tier.return_value = 3
        soul = _make_soul()
        svc = _make_receipt_service()
        engine = ResumeEngine(svc, soul)

        result = engine._request_approval(
            mode="fork",
            source_quest_id="q-001",
            required_tier=2,
            operator_id="op-1",
            session_id="s-1",
        )
        assert result["approved"] is True
        # Second create call should be the approval decision
        decision_receipt = svc.create.call_args_list[1][0][0]
        assert decision_receipt.action_type == "t3_fork_approved"

    @patch("src.timetravel.resume_engine.ResumeEngine._get_current_trust_tier")
    def test_emits_t3_fork_rejected_when_tier_insufficient(self, mock_tier):
        mock_tier.return_value = 1
        soul = _make_soul()
        svc = _make_receipt_service()
        engine = ResumeEngine(svc, soul)

        result = engine._request_approval(
            mode="fork",
            source_quest_id="q-001",
            required_tier=3,
            operator_id="op-1",
            session_id="s-1",
        )
        assert result["approved"] is False
        decision_receipt = svc.create.call_args_list[1][0][0]
        assert decision_receipt.action_type == "t3_fork_rejected"


# ---------------------------------------------------------------------------
# _get_current_trust_tier
# ---------------------------------------------------------------------------

class TestGetCurrentTrustTier:
    def test_returns_tier_from_trust_ledger(self):
        soul = _make_soul()
        svc = _make_receipt_service()
        engine = ResumeEngine(svc, soul)

        mock_ledger = MagicMock()
        mock_ledger.get_effective_tier.return_value = 2

        mock_module = MagicMock()
        mock_module.TrustLedger.return_value = mock_ledger

        with patch.dict(
            "sys.modules",
            {"src.core.governance.trust_ledger": mock_module},
        ):
            tier = engine._get_current_trust_tier()
            assert tier == 2

    def test_defaults_to_t0_when_no_ledger(self):
        soul = _make_soul()
        svc = _make_receipt_service()
        engine = ResumeEngine(svc, soul)

        mock_module = MagicMock()
        mock_module.TrustLedger.side_effect = Exception("unavailable")

        with patch.dict(
            "sys.modules",
            {"src.core.governance.trust_ledger": mock_module},
        ):
            tier = engine._get_current_trust_tier()
            assert tier == 0
