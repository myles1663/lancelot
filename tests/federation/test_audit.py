# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""Tests for Federation Audit Engine."""

import os
import tempfile

import pytest
from src.federation.audit import (
    AuditEntry,
    AuditEventType,
    FederationAuditEngine,
    ForensicTimeline,
)


@pytest.fixture
def engine():
    return FederationAuditEngine()


def _entry(entry_id, event_type=AuditEventType.HANDOFF_INITIATED,
           instance_id="inst-1", quest_id="quest-1", timestamp=None,
           risk_tier="", soul_hash=""):
    return AuditEntry(
        entry_id=entry_id,
        event_type=event_type,
        instance_id=instance_id,
        federation_quest_id=quest_id,
        timestamp=timestamp or "2026-03-16T10:00:00+00:00",
        risk_tier=risk_tier,
        soul_version_hash=soul_hash,
    )


class TestRecordAndGet:
    def test_record_and_get(self, engine):
        entry = _entry("e1")
        engine.record(entry)
        assert engine.get_entry("e1") is not None

    def test_get_nonexistent(self, engine):
        assert engine.get_entry("nope") is None

    def test_eviction(self):
        engine = FederationAuditEngine(max_entries=3)
        engine.record(_entry("e1", timestamp="2026-03-16T10:00:00+00:00"))
        engine.record(_entry("e2", timestamp="2026-03-16T10:01:00+00:00"))
        engine.record(_entry("e3", timestamp="2026-03-16T10:02:00+00:00"))
        engine.record(_entry("e4", timestamp="2026-03-16T10:03:00+00:00"))
        # e1 should be evicted (oldest)
        assert engine.get_entry("e1") is None
        assert engine.get_entry("e4") is not None


class TestQuery:
    def test_query_all(self, engine):
        engine.record(_entry("e1"))
        engine.record(_entry("e2"))
        assert len(engine.query()) == 2

    def test_filter_by_quest(self, engine):
        engine.record(_entry("e1", quest_id="q1"))
        engine.record(_entry("e2", quest_id="q2"))
        results = engine.query(federation_quest_id="q1")
        assert len(results) == 1
        assert results[0].entry_id == "e1"

    def test_filter_by_instance(self, engine):
        engine.record(_entry("e1", instance_id="i1"))
        engine.record(_entry("e2", instance_id="i2"))
        results = engine.query(instance_id="i1")
        assert len(results) == 1

    def test_filter_by_event_type(self, engine):
        engine.record(_entry("e1", event_type=AuditEventType.HANDOFF_INITIATED))
        engine.record(_entry("e2", event_type=AuditEventType.KILL_ISSUED))
        results = engine.query(event_type=AuditEventType.KILL_ISSUED)
        assert len(results) == 1
        assert results[0].entry_id == "e2"

    def test_filter_by_risk_tier(self, engine):
        engine.record(_entry("e1", risk_tier="T1"))
        engine.record(_entry("e2", risk_tier="T3"))
        results = engine.query(risk_tier="T3")
        assert len(results) == 1

    def test_filter_by_soul_hash(self, engine):
        engine.record(_entry("e1", soul_hash="abc"))
        engine.record(_entry("e2", soul_hash="def"))
        results = engine.query(soul_version_hash="abc")
        assert len(results) == 1

    def test_filter_by_time_range(self, engine):
        engine.record(_entry("e1", timestamp="2026-03-16T10:00:00+00:00"))
        engine.record(_entry("e2", timestamp="2026-03-16T12:00:00+00:00"))
        engine.record(_entry("e3", timestamp="2026-03-16T14:00:00+00:00"))
        results = engine.query(
            start_time="2026-03-16T11:00:00+00:00",
            end_time="2026-03-16T13:00:00+00:00",
        )
        assert len(results) == 1
        assert results[0].entry_id == "e2"

    def test_limit(self, engine):
        for i in range(10):
            engine.record(_entry(f"e{i}"))
        results = engine.query(limit=3)
        assert len(results) == 3

    def test_sorted_by_timestamp(self, engine):
        engine.record(_entry("e2", timestamp="2026-03-16T12:00:00+00:00"))
        engine.record(_entry("e1", timestamp="2026-03-16T10:00:00+00:00"))
        results = engine.query()
        assert results[0].entry_id == "e1"
        assert results[1].entry_id == "e2"

    def test_combined_filters(self, engine):
        engine.record(_entry("e1", instance_id="i1", quest_id="q1"))
        engine.record(_entry("e2", instance_id="i1", quest_id="q2"))
        engine.record(_entry("e3", instance_id="i2", quest_id="q1"))
        results = engine.query(instance_id="i1", federation_quest_id="q1")
        assert len(results) == 1
        assert results[0].entry_id == "e1"


class TestQuestReconstruction:
    def test_reconstruct(self, engine):
        engine.record(_entry("e1", quest_id="q1", instance_id="i1",
                             timestamp="2026-03-16T10:00:00+00:00"))
        engine.record(_entry("e2", quest_id="q1", instance_id="i2",
                             timestamp="2026-03-16T10:05:00+00:00"))
        engine.record(_entry("e3", quest_id="q1", instance_id="i1",
                             event_type=AuditEventType.CONTRADICTION_DETECTED,
                             timestamp="2026-03-16T10:10:00+00:00"))

        timeline = engine.reconstruct_quest("q1")
        assert timeline.total_entries == 3
        assert timeline.instances_involved == 2
        assert timeline.contradictions_found == 1
        assert timeline.start_time == "2026-03-16T10:00:00+00:00"
        assert timeline.end_time == "2026-03-16T10:10:00+00:00"

    def test_reconstruct_empty(self, engine):
        timeline = engine.reconstruct_quest("nonexistent")
        assert timeline.total_entries == 0


class TestInstanceTimeline:
    def test_instance_timeline(self, engine):
        engine.record(_entry("e1", instance_id="i1"))
        engine.record(_entry("e2", instance_id="i2"))
        results = engine.get_instance_timeline("i1")
        assert len(results) == 1


class TestSummary:
    def test_summary_empty(self, engine):
        s = engine.get_summary()
        assert s["total_entries"] == 0

    def test_summary_populated(self, engine):
        engine.record(_entry("e1", quest_id="q1",
                             event_type=AuditEventType.HANDOFF_INITIATED))
        engine.record(_entry("e2", quest_id="q1",
                             event_type=AuditEventType.KILL_ISSUED))
        engine.record(_entry("e3", quest_id="q2",
                             event_type=AuditEventType.HANDOFF_INITIATED))
        s = engine.get_summary()
        assert s["total_entries"] == 3
        assert s["unique_quests"] == 2
        assert s["event_type_counts"]["handoff_initiated"] == 2
        assert s["event_type_counts"]["kill_issued"] == 1


class TestPersistence:
    def test_entries_survive_reopen(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "audit_log.json")

            engine1 = FederationAuditEngine(persistence_path=path)
            engine1.record(_entry("e1", quest_id="q1"))

            engine2 = FederationAuditEngine(persistence_path=path)
            results = engine2.query(federation_quest_id="q1")
            assert len(results) == 1
            assert results[0].entry_id == "e1"


class TestSerialization:
    def test_entry_to_dict(self):
        entry = _entry("e1", risk_tier="T2")
        d = entry.to_dict()
        assert d["entry_id"] == "e1"
        assert d["event_type"] == "handoff_initiated"
        assert d["risk_tier"] == "T2"

    def test_timeline_to_dict(self):
        t = ForensicTimeline(
            quest_id="q1",
            total_entries=5,
            instances_involved=2,
        )
        d = t.to_dict()
        assert d["quest_id"] == "q1"
        assert d["total_entries"] == 5
