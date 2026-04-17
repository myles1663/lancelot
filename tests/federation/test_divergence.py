# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""Tests for federation divergence detection and reconciliation."""

from datetime import datetime, timezone, timedelta
import pytest
from src.federation.divergence import (
    ConflictRecord,
    DivergenceDetector,
    DivergenceSnapshot,
    DivergenceState,
    ReconciliationOutcome,
    reconcile_divergence,
)


class TestDivergenceDetector:
    def test_initial_state_connected(self):
        dd = DivergenceDetector(instance_id="test-1")
        assert dd.state == DivergenceState.CONNECTED
        assert not dd.is_diverged

    def test_no_peers_stays_connected(self):
        dd = DivergenceDetector(instance_id="test-1")
        state, snapshot = dd.check_connectivity({})
        assert state == DivergenceState.CONNECTED
        assert snapshot is None

    def test_fresh_peers_stays_connected(self):
        dd = DivergenceDetector(instance_id="test-1")
        now = datetime.now(timezone.utc).isoformat()
        state, snapshot = dd.check_connectivity({
            "peer-a": now,
            "peer-b": now,
        })
        assert state == DivergenceState.CONNECTED

    def test_all_lost_triggers_divergence(self):
        dd = DivergenceDetector(instance_id="test-1", staleness_lost_s=5.0)
        old = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        state, snapshot = dd.check_connectivity(
            peer_last_heartbeats={"peer-a": old, "peer-b": old},
            current_soul_hash="abc123",
            active_task_count=3,
            budget_utilization_pct=42.0,
        )
        assert state == DivergenceState.DIVERGED
        assert dd.is_diverged
        assert snapshot is not None
        assert snapshot.soul_hash_at_divergence == "abc123"
        assert snapshot.active_task_count == 3
        assert snapshot.budget_utilization_pct == 42.0

    def test_partial_loss_stays_connected(self):
        dd = DivergenceDetector(instance_id="test-1", staleness_lost_s=5.0)
        now = datetime.now(timezone.utc).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        state, _ = dd.check_connectivity({
            "peer-a": now,   # Fresh
            "peer-b": old,   # Lost
        })
        assert state == DivergenceState.CONNECTED

    def test_reconnection_on_fresh_heartbeat(self):
        dd = DivergenceDetector(instance_id="test-1", staleness_lost_s=5.0)
        old = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        # First: diverge
        dd.check_connectivity({"peer-a": old})
        assert dd.is_diverged
        # Then: reconnect
        now = datetime.now(timezone.utc).isoformat()
        state, _ = dd.check_connectivity({"peer-a": now})
        assert state == DivergenceState.RECONNECTING

    def test_mark_reconciled(self):
        dd = DivergenceDetector(instance_id="test-1", staleness_lost_s=5.0)
        old = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        dd.check_connectivity({"peer-a": old})
        dd.mark_reconciled()
        assert dd.state == DivergenceState.RECONCILED

    def test_mark_reconciled_records_outcome_and_conflicts(self):
        dd = DivergenceDetector(instance_id="test-1", staleness_lost_s=5.0)
        old = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        dd.check_connectivity({"peer-a": old})
        dd.mark_reconciled(
            ReconciliationOutcome.INCOMPATIBLE,
            [
                ConflictRecord(
                    conflict_type="budget_exceeded",
                    description="Budget exceeded",
                    resolution="needs_operator_review",
                    affected_component="budget",
                )
            ],
        )
        assert dd.last_reconciliation is not None
        assert dd.last_reconciliation.outcome == "incompatible"
        assert dd.last_reconciliation.conflicts[0]["conflict_type"] == "budget_exceeded"

    def test_reset_to_connected(self):
        dd = DivergenceDetector(instance_id="test-1", staleness_lost_s=5.0)
        old = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        dd.check_connectivity({"peer-a": old})
        dd.reset_to_connected()
        assert dd.state == DivergenceState.CONNECTED
        assert dd.divergence_snapshot is None

    def test_divergence_duration(self):
        dd = DivergenceDetector(instance_id="test-1", staleness_lost_s=5.0)
        old = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        dd.check_connectivity({"peer-a": old})
        duration = dd.get_divergence_duration_s()
        assert duration >= 0.0  # Should be very small, just created

    def test_divergence_duration_when_connected(self):
        dd = DivergenceDetector(instance_id="test-1")
        assert dd.get_divergence_duration_s() == 0.0


class TestDivergenceSnapshot:
    def test_to_dict(self):
        snap = DivergenceSnapshot(
            soul_hash_at_divergence="abc123",
            active_task_count=5,
            hive_spawn_states={"agent-1": "EXECUTING"},
        )
        d = snap.to_dict()
        assert d["soul_hash_at_divergence"] == "abc123"
        assert d["active_task_count"] == 5
        assert d["hive_spawn_states"]["agent-1"] == "EXECUTING"


class TestReconcileDivergence:
    def test_compatible_no_conflicts(self):
        snap = DivergenceSnapshot(
            soul_hash_at_divergence="hash123",
            budget_utilization_pct=50.0,
        )
        outcome, conflicts = reconcile_divergence(
            divergence_snapshot=snap,
            reconnection_soul_hash="hash123",  # Same hash
            reconnection_budget_pct=55.0,      # Under ceiling
        )
        assert outcome == ReconciliationOutcome.COMPATIBLE
        assert conflicts == []

    def test_budget_exceeded_incompatible(self):
        snap = DivergenceSnapshot(
            soul_hash_at_divergence="hash123",
            budget_utilization_pct=80.0,
        )
        outcome, conflicts = reconcile_divergence(
            divergence_snapshot=snap,
            reconnection_soul_hash="hash123",
            reconnection_budget_pct=110.0,  # Over ceiling
            budget_ceiling_pct=100.0,
        )
        assert outcome == ReconciliationOutcome.INCOMPATIBLE
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == "budget_exceeded"

    def test_soul_mutation_incompatible(self):
        snap = DivergenceSnapshot(
            soul_hash_at_divergence="hash_old",
        )
        outcome, conflicts = reconcile_divergence(
            divergence_snapshot=snap,
            reconnection_soul_hash="hash_new",  # Different!
            reconnection_budget_pct=50.0,
        )
        assert outcome == ReconciliationOutcome.INCOMPATIBLE
        assert any(c.conflict_type == "soul_violated" for c in conflicts)

    def test_multiple_conflicts(self):
        snap = DivergenceSnapshot(
            soul_hash_at_divergence="hash_old",
            budget_utilization_pct=80.0,
        )
        outcome, conflicts = reconcile_divergence(
            divergence_snapshot=snap,
            reconnection_soul_hash="hash_new",
            reconnection_budget_pct=110.0,
            budget_ceiling_pct=100.0,
        )
        assert outcome == ReconciliationOutcome.INCOMPATIBLE
        assert len(conflicts) == 2
