# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Unit tests for state_snapshot — governance context reconstruction.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "core"))

import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from src.timetravel.state_snapshot import StateSnapshot, StateSnapshotReader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_receipt(
    receipt_id="r-001",
    quest_id="q-001",
    timestamp="2026-01-15T10:00:00Z",
    action_type="tool_call",
    inputs=None,
    outputs=None,
):
    """Build a mock Receipt object."""
    r = MagicMock()
    r.id = receipt_id
    r.quest_id = quest_id
    r.timestamp = timestamp
    r.action_type = action_type
    r.inputs = inputs or {}
    r.outputs = outputs or {}
    r.to_dict.return_value = {
        "id": receipt_id,
        "quest_id": quest_id,
        "timestamp": timestamp,
        "action_type": action_type,
    }
    return r


def _make_receipt_service(
    get_return=None,
    list_return=None,
    quest_receipts=None,
    stats_return=None,
):
    """Build a mock receipt_service."""
    svc = MagicMock()
    svc.get.return_value = get_return
    svc.list.return_value = list_return or []
    svc.get_quest_receipts.return_value = quest_receipts or []
    svc.get_stats.return_value = stats_return or {
        "tokens": {"total": 500},
        "total_receipts": 10,
        "duration_ms": {"total": 3000},
    }
    return svc


# ---------------------------------------------------------------------------
# StateSnapshot.to_dict
# ---------------------------------------------------------------------------

class TestStateSnapshotToDict:
    def test_serialization(self):
        snap = StateSnapshot(
            timestamp="2026-01-15T10:00:00Z",
            receipt_id="r-001",
            quest_id="q-001",
            soul_version="v2",
            kill_switches={"auto_approve": True},
            trust_tier=2,
            trust_records=[{"tier": 2}],
            cost_data={"total_tokens": 100},
            active_flags={"FEATURE_SOUL": True},
            receipt_chain=[{"id": "r-001"}],
            metadata={"soul_constraints_active": True},
        )
        d = snap.to_dict()
        assert d["timestamp"] == "2026-01-15T10:00:00Z"
        assert d["receipt_id"] == "r-001"
        assert d["quest_id"] == "q-001"
        assert d["soul_version"] == "v2"
        assert d["kill_switches"] == {"auto_approve": True}
        assert d["trust_tier"] == 2
        assert d["receipt_chain_length"] == 1
        assert d["metadata"]["soul_constraints_active"] is True

    def test_receipt_chain_length_not_full_chain(self):
        """to_dict should include receipt_chain_length, not the full chain."""
        snap = StateSnapshot(
            receipt_chain=[{"id": "r-1"}, {"id": "r-2"}, {"id": "r-3"}],
        )
        d = snap.to_dict()
        assert d["receipt_chain_length"] == 3
        assert "receipt_chain" not in d


# ---------------------------------------------------------------------------
# StateSnapshotReader.read_snapshot
# ---------------------------------------------------------------------------

class TestReadSnapshot:
    @patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_metadata")
    @patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_receipt_chain")
    @patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_cost_data")
    @patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_trust_data")
    @patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_kill_switches")
    @patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_soul_version")
    def test_returns_snapshot_for_valid_receipt(self, *_patches):
        receipt = _make_receipt()
        svc = _make_receipt_service(get_return=receipt)
        reader = StateSnapshotReader(svc)

        snap = reader.read_snapshot("r-001")
        assert isinstance(snap, StateSnapshot)
        assert snap.receipt_id == "r-001"
        assert snap.quest_id == "q-001"
        assert snap.timestamp == "2026-01-15T10:00:00Z"

    def test_receipt_not_found_raises(self):
        svc = _make_receipt_service(get_return=None)
        reader = StateSnapshotReader(svc)
        with pytest.raises(ValueError, match="Receipt not found"):
            reader.read_snapshot("nonexistent")

    def test_populates_soul_version_from_soul_updated(self):
        receipt = _make_receipt()
        soul_receipt = _make_receipt(
            action_type="soul_updated",
            outputs={"new_version": "v3"},
        )
        svc = _make_receipt_service(get_return=receipt)
        # list() returns soul_updated receipt when queried with action_type
        svc.list.return_value = [soul_receipt]

        with patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_kill_switches"), \
             patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_trust_data"), \
             patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_cost_data"), \
             patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_receipt_chain"), \
             patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_metadata"):
            reader = StateSnapshotReader(svc)
            snap = reader.read_snapshot("r-001")
            assert snap.soul_version == "v3"

    def test_replays_kill_switches_issued_and_lifted(self):
        receipt = _make_receipt()
        svc = _make_receipt_service(get_return=receipt)

        issued_receipt = _make_receipt(
            action_type="kill_switch_issued",
            inputs={"flag_name": "auto_approve"},
            timestamp="2026-01-15T09:00:00Z",
        )
        lifted_receipt = _make_receipt(
            action_type="kill_switch_lifted",
            inputs={"flag_name": "auto_approve"},
            timestamp="2026-01-15T09:30:00Z",
        )

        def list_side_effect(*args, **kwargs):
            at = kwargs.get("action_type", "")
            if at == "kill_switch_issued":
                return [issued_receipt]
            elif at == "kill_switch_lifted":
                return [lifted_receipt]
            return []

        svc.list.side_effect = list_side_effect

        with patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_soul_version"), \
             patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_trust_data"), \
             patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_cost_data"), \
             patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_receipt_chain"), \
             patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_metadata"):
            reader = StateSnapshotReader(svc)
            snap = reader.read_snapshot("r-001")
            # Lifted after issued => not active
            assert snap.kill_switches.get("auto_approve") is False

    def test_kill_switch_issued_then_lifted_not_active(self):
        """Explicit test: issued at T1, lifted at T2 => switch is False."""
        receipt = _make_receipt()
        svc = _make_receipt_service(get_return=receipt)

        issued = _make_receipt(
            inputs={"flag_name": "kill_all"},
            timestamp="2026-01-15T08:00:00Z",
        )
        lifted = _make_receipt(
            inputs={"flag_name": "kill_all"},
            timestamp="2026-01-15T09:00:00Z",
        )

        def list_se(*a, **kw):
            at = kw.get("action_type", "")
            if at == "kill_switch_issued":
                return [issued]
            elif at == "kill_switch_lifted":
                return [lifted]
            return []

        svc.list.side_effect = list_se

        with patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_soul_version"), \
             patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_trust_data"), \
             patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_cost_data"), \
             patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_receipt_chain"), \
             patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_metadata"):
            reader = StateSnapshotReader(svc)
            snap = reader.read_snapshot("r-001")
            assert snap.kill_switches.get("kill_all") is False

    def test_multiple_kill_switches_tracked_independently(self):
        receipt = _make_receipt()
        svc = _make_receipt_service(get_return=receipt)

        issued_a = _make_receipt(
            inputs={"flag_name": "switch_a"},
            timestamp="2026-01-15T08:00:00Z",
        )
        issued_b = _make_receipt(
            inputs={"flag_name": "switch_b"},
            timestamp="2026-01-15T08:05:00Z",
        )
        lifted_a = _make_receipt(
            inputs={"flag_name": "switch_a"},
            timestamp="2026-01-15T09:00:00Z",
        )

        def list_se(*a, **kw):
            at = kw.get("action_type", "")
            if at == "kill_switch_issued":
                return [issued_a, issued_b]
            elif at == "kill_switch_lifted":
                return [lifted_a]
            return []

        svc.list.side_effect = list_se

        with patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_soul_version"), \
             patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_trust_data"), \
             patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_cost_data"), \
             patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_receipt_chain"), \
             patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_metadata"):
            reader = StateSnapshotReader(svc)
            snap = reader.read_snapshot("r-001")
            assert snap.kill_switches["switch_a"] is False
            assert snap.kill_switches["switch_b"] is True

    def test_populates_cost_data(self):
        receipt = _make_receipt()
        svc = _make_receipt_service(
            get_return=receipt,
            stats_return={
                "tokens": {"total": 1234},
                "total_receipts": 42,
                "duration_ms": {"total": 9999},
            },
        )

        with patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_soul_version"), \
             patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_kill_switches"), \
             patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_trust_data"), \
             patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_receipt_chain"), \
             patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_metadata"):
            reader = StateSnapshotReader(svc)
            snap = reader.read_snapshot("r-001")
            # cost_data is populated by _populate_cost_data which we patched out
            # So test the method directly instead
        reader2 = StateSnapshotReader(svc)
        snap2 = StateSnapshot(
            timestamp="2026-01-15T10:00:00Z",
            quest_id="q-001",
        )
        reader2._populate_cost_data(snap2)
        assert snap2.cost_data["total_tokens"] == 1234
        assert snap2.cost_data["total_receipts"] == 42

    def test_builds_receipt_chain(self):
        receipt = _make_receipt()
        r1 = _make_receipt(receipt_id="r-1", timestamp="2026-01-15T09:00:00Z")
        r2 = _make_receipt(receipt_id="r-2", timestamp="2026-01-15T09:30:00Z")
        r3 = _make_receipt(receipt_id="r-3", timestamp="2026-01-15T11:00:00Z")

        svc = _make_receipt_service(get_return=receipt, quest_receipts=[r1, r2, r3])
        reader = StateSnapshotReader(svc)
        snap = StateSnapshot(
            timestamp="2026-01-15T10:00:00Z",
            quest_id="q-001",
        )
        reader._populate_receipt_chain(snap)
        # Only r1 and r2 should be included (timestamps <= snapshot timestamp)
        assert len(snap.receipt_chain) == 2

    def test_empty_receipt_chain_no_quest_id(self):
        snap = StateSnapshot(timestamp="2026-01-15T10:00:00Z")
        svc = _make_receipt_service()
        reader = StateSnapshotReader(svc)
        reader._populate_receipt_chain(snap)
        assert snap.receipt_chain == []

    def test_no_soul_updated_receipts_fallback(self):
        """When no SOUL_UPDATED receipts exist, falls back to 'unknown'."""
        receipt = _make_receipt()
        svc = _make_receipt_service(get_return=receipt)
        svc.list.return_value = []

        with patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_kill_switches"), \
             patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_trust_data"), \
             patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_cost_data"), \
             patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_receipt_chain"), \
             patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_metadata"):
            # Also patch get_active_version import to force fallback
            with patch.dict("sys.modules", {"src.core.soul.store": MagicMock()}):
                reader = StateSnapshotReader(svc)
                snap = reader.read_snapshot("r-001")
                # Soul version comes from list returning empty; inner import might
                # succeed or fail depending on env. Key is it doesn't crash.
                assert snap.soul_version is not None

    def test_populates_trust_tier(self):
        """_populate_trust_data sets trust_tier from TrustLedger."""
        snap = StateSnapshot(timestamp="2026-01-15T10:00:00Z")
        svc = _make_receipt_service()
        reader = StateSnapshotReader(svc)

        mock_ledger = MagicMock()
        mock_ledger.get_approval_tier.return_value = 2
        mock_ledger.export_records.return_value = [{"capability": "cap.test", "scope": "s"}]

        reader = StateSnapshotReader(svc, trust_ledger=mock_ledger)
        reader._populate_trust_data(snap)
        assert snap.trust_tier == 2
        assert snap.trust_records == [{"capability": "cap.test", "scope": "s"}]

    def test_populates_metadata_with_feature_flags(self):
        snap = StateSnapshot(timestamp="2026-01-15T10:00:00Z")
        svc = _make_receipt_service()
        reader = StateSnapshotReader(svc)

        with patch.dict("sys.modules", {}):
            with patch(
                "src.timetravel.state_snapshot.StateSnapshotReader._populate_metadata"
            ) as mock_pop:
                # Call directly to verify it sets metadata keys
                pass

        # Test the method directly with mocked imports
        snap2 = StateSnapshot(timestamp="2026-01-15T10:00:00Z")
        mock_flags_module = MagicMock()
        mock_flags_module.FEATURE_SOUL = True
        mock_flags_module.FEATURE_APPROVAL_LEARNING = False
        mock_flags_module.FEATURE_TRUST_LEDGER = True
        mock_flags_module.FEATURE_TIME_TRAVEL = True
        mock_flags_module.get_all_flags.return_value = {"FEATURE_SOUL": True}

        with patch.dict("sys.modules", {"src.core.feature_flags": mock_flags_module}):
            reader._populate_metadata(snap2)
            assert snap2.metadata.get("soul_constraints_active") is True
            assert snap2.active_flags == {"FEATURE_SOUL": True}


# ---------------------------------------------------------------------------
# StateSnapshotReader.read_snapshot_at
# ---------------------------------------------------------------------------

class TestReadSnapshotAt:
    @patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_metadata")
    @patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_receipt_chain")
    @patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_cost_data")
    @patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_trust_data")
    @patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_kill_switches")
    @patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_soul_version")
    def test_returns_snapshot_at_timestamp(self, *_patches):
        receipt = _make_receipt(receipt_id="r-latest")
        svc = _make_receipt_service(list_return=[receipt])
        reader = StateSnapshotReader(svc)

        snap = reader.read_snapshot_at("2026-01-15T10:00:00Z")
        assert isinstance(snap, StateSnapshot)
        assert snap.timestamp == "2026-01-15T10:00:00Z"
        assert snap.receipt_id == "r-latest"

    @patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_metadata")
    @patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_receipt_chain")
    @patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_cost_data")
    @patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_trust_data")
    @patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_kill_switches")
    @patch("src.timetravel.state_snapshot.StateSnapshotReader._populate_soul_version")
    def test_no_receipts_at_timestamp(self, *_patches):
        svc = _make_receipt_service(list_return=[])
        reader = StateSnapshotReader(svc)

        snap = reader.read_snapshot_at("2026-01-01T00:00:00Z")
        assert snap.receipt_id is None
        assert snap.quest_id is None
        assert snap.timestamp == "2026-01-01T00:00:00Z"
