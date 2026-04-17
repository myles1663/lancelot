# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""Tests for Soul Propagation Engine."""
import pytest
from src.federation.soul_propagation import (
    ConsistencyState,
    PropagationState,
    PropagationTier,
    SoulPropagationEngine,
    classify_change_tier,
    InstancePropState,
)


class TestClassifyChangeTier:
    def test_t1_tone(self):
        assert classify_change_tier(["tone_invariants"]) == PropagationTier.T1_MINOR

    def test_t1_memory_ethics(self):
        assert classify_change_tier(["memory_ethics"]) == PropagationTier.T1_MINOR

    def test_t2_autonomy(self):
        assert classify_change_tier(["autonomy_posture"]) == PropagationTier.T2_SIGNIFICANT

    def test_t2_approval(self):
        assert classify_change_tier(["approval_rules"]) == PropagationTier.T2_SIGNIFICANT

    def test_t3_risk_rules(self):
        assert classify_change_tier(["risk_rules"]) == PropagationTier.T3_CRITICAL

    def test_t3_mission(self):
        assert classify_change_tier(["mission"]) == PropagationTier.T3_CRITICAL

    def test_highest_tier_wins(self):
        # T1 + T3 = T3
        assert classify_change_tier(
            ["tone_invariants", "risk_rules"]
        ) == PropagationTier.T3_CRITICAL

    def test_t2_plus_t1(self):
        assert classify_change_tier(
            ["tone_invariants", "autonomy_posture"]
        ) == PropagationTier.T2_SIGNIFICANT


@pytest.fixture
def engine():
    return SoulPropagationEngine(
        self_instance_id="self-001",
        peer_ids=["peer-a", "peer-b"],
    )


class TestT1Propagation:
    def test_t1_skips_pause(self, engine):
        event = engine.initiate_propagation(
            "ev-1", PropagationTier.T1_MINOR,
            "self-001", "tone update",
            "v1", "hash1", "v2", "hash2",
        )
        assert event.state == PropagationState.ACTIVATING

    def test_t1_complete_on_all_activated(self, engine):
        engine.initiate_propagation(
            "ev-1", PropagationTier.T1_MINOR,
            "self-001", "tone update",
            "v1", "hash1", "v2", "hash2",
        )
        engine.record_activation("ev-1", "self-001")
        engine.record_activation("ev-1", "peer-a")
        engine.record_activation("ev-1", "peer-b")
        event = engine.get_event("ev-1")
        assert event.state == PropagationState.COMPLETED
        assert engine.consistency_state == ConsistencyState.SYNCHRONIZED


class TestT2Propagation:
    def test_t2_starts_pausing(self, engine):
        event = engine.initiate_propagation(
            "ev-1", PropagationTier.T2_SIGNIFICANT,
            "self-001", "autonomy change",
            "v1", "hash1", "v2", "hash2",
        )
        assert event.state == PropagationState.PAUSING

    def test_t2_pause_then_activate(self, engine):
        engine.initiate_propagation(
            "ev-1", PropagationTier.T2_SIGNIFICANT,
            "self-001", "autonomy change",
            "v1", "hash1", "v2", "hash2",
        )
        engine.record_pause_ack("ev-1", "self-001")
        engine.record_pause_ack("ev-1", "peer-a")
        engine.record_pause_ack("ev-1", "peer-b")
        event = engine.get_event("ev-1")
        assert event.state == PropagationState.PAUSED

        engine.advance_to_activation("ev-1")
        event = engine.get_event("ev-1")
        assert event.state == PropagationState.ACTIVATING


class TestT3Propagation:
    def test_t3_requires_confirmation(self, engine):
        engine.initiate_propagation(
            "ev-1", PropagationTier.T3_CRITICAL,
            "self-001", "risk rules change",
            "v1", "hash1", "v2", "hash2",
        )
        # Pause all
        for iid in ["self-001", "peer-a", "peer-b"]:
            engine.record_pause_ack("ev-1", iid)
        engine.advance_to_activation("ev-1")

        # Activate one — should remain ACTIVATING until all instances activate
        engine.record_activation("ev-1", "self-001")
        event = engine.get_event("ev-1")
        assert event.state == PropagationState.ACTIVATING

        for iid in ["peer-a", "peer-b"]:
            engine.record_activation("ev-1", iid)
        event = engine.get_event("ev-1")
        assert event.state == PropagationState.CONFIRMING

    def test_t3_all_confirmed_completes(self, engine):
        engine.initiate_propagation(
            "ev-1", PropagationTier.T3_CRITICAL,
            "self-001", "risk rules change",
            "v1", "hash1", "v2", "hash2",
        )
        for iid in ["self-001", "peer-a", "peer-b"]:
            engine.record_pause_ack("ev-1", iid)
        engine.advance_to_activation("ev-1")
        for iid in ["self-001", "peer-a", "peer-b"]:
            engine.record_activation("ev-1", iid)

        for iid in ["self-001", "peer-a", "peer-b"]:
            engine.record_confirmation("ev-1", iid)

        event = engine.get_event("ev-1")
        assert event.state == PropagationState.CONFIRMING

        assert engine.complete_confirmed_event("ev-1") is True
        event = engine.get_event("ev-1")
        assert event.state == PropagationState.COMPLETED


class TestRejectionAndRollback:
    def test_rejection_fails_event(self, engine):
        engine.initiate_propagation(
            "ev-1", PropagationTier.T2_SIGNIFICANT,
            "self-001", "change",
            "v1", "hash1", "v2", "hash2",
        )
        engine.record_rejection("ev-1", "peer-a", "incompatible")
        event = engine.get_event("ev-1")
        assert event.state == PropagationState.FAILED
        assert engine.consistency_state == ConsistencyState.DIVERGED

    def test_rollback(self, engine):
        engine.initiate_propagation(
            "ev-1", PropagationTier.T1_MINOR,
            "self-001", "change",
            "v1", "hash1", "v2", "hash2",
        )
        assert engine.rollback("ev-1")
        event = engine.get_event("ev-1")
        assert event.state == PropagationState.ROLLED_BACK
        assert engine.consistency_state == ConsistencyState.STALE

    def test_rollback_completed_fails(self, engine):
        engine.initiate_propagation(
            "ev-1", PropagationTier.T1_MINOR,
            "self-001", "change",
            "v1", "hash1", "v2", "hash2",
        )
        for iid in ["self-001", "peer-a", "peer-b"]:
            engine.record_activation("ev-1", iid)
        assert not engine.rollback("ev-1")


class TestEventToDict:
    def test_to_dict(self, engine):
        event = engine.initiate_propagation(
            "ev-1", PropagationTier.T1_MINOR,
            "self-001", "change",
            "v1", "hash1", "v2", "hash2",
        )
        d = event.to_dict()
        assert d["event_id"] == "ev-1"
        assert d["tier"] == "T1"
        assert len(d["instances"]) == 3


class TestPersistence:
    def test_active_event_survives_restart(self, tmp_path):
        path = tmp_path / "soul_propagation.json"
        engine = SoulPropagationEngine(
            self_instance_id="self-001",
            peer_ids=["peer-a"],
            persistence_path=str(path),
        )
        engine.initiate_propagation(
            "ev-1", PropagationTier.T2_SIGNIFICANT,
            "self-001", "autonomy change",
            "v1", "hash1", "v2", "hash2",
        )

        reloaded = SoulPropagationEngine(
            self_instance_id="self-001",
            peer_ids=[],
            persistence_path=str(path),
        )
        event = reloaded.get_event("ev-1")
        assert event is not None
        assert event.state == PropagationState.PAUSING
        assert reloaded.consistency_state == ConsistencyState.PROPAGATING

    def test_completed_event_persists_consistency_state(self, tmp_path):
        path = tmp_path / "soul_propagation.json"
        engine = SoulPropagationEngine(
            self_instance_id="self-001",
            peer_ids=["peer-a"],
            persistence_path=str(path),
        )
        engine.initiate_propagation(
            "ev-1", PropagationTier.T1_MINOR,
            "self-001", "tone update",
            "v1", "hash1", "v2", "hash2",
        )
        engine.record_activation("ev-1", "self-001")
        engine.record_activation("ev-1", "peer-a")

        reloaded = SoulPropagationEngine(
            self_instance_id="self-001",
            peer_ids=[],
            persistence_path=str(path),
        )
        event = reloaded.get_event("ev-1")
        assert event is not None
        assert event.state == PropagationState.COMPLETED
        assert reloaded.consistency_state == ConsistencyState.SYNCHRONIZED
