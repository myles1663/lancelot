# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""Tests for Federation heartbeat emission and staleness computation."""

import time
from datetime import datetime, timezone, timedelta
import pytest
from src.federation.heartbeat import (
    Heartbeat,
    HeartbeatEmitter,
    StalenessLevel,
    compute_staleness,
)


class TestHeartbeat:
    """Test Heartbeat dataclass."""

    def test_default_fields(self):
        hb = Heartbeat(instance_id="test-123")
        assert hb.instance_id == "test-123"
        assert hb.timestamp  # Auto-generated
        assert hb.soul_version_hash == ""
        assert hb.deployment_mode == "standalone"
        assert hb.active_task_count == 0
        assert hb.budget_utilization_pct == 0.0
        assert hb.peer_count == 0
        assert hb.signature is None

    def test_to_dict(self):
        hb = Heartbeat(instance_id="test-123", deployment_mode="federated")
        d = hb.to_dict()
        assert d["instance_id"] == "test-123"
        assert d["deployment_mode"] == "federated"
        assert "timestamp" in d


class TestStaleness:
    """Test staleness computation."""

    def test_fresh(self):
        now = datetime.now(timezone.utc).isoformat()
        level, age = compute_staleness(now)
        assert level == StalenessLevel.FRESH
        assert age < 2.0  # Should be near-zero

    def test_warning(self):
        past = (datetime.now(timezone.utc) - timedelta(seconds=15)).isoformat()
        level, age = compute_staleness(past)
        assert level == StalenessLevel.WARNING
        assert 14.0 < age < 17.0

    def test_critical(self):
        past = (datetime.now(timezone.utc) - timedelta(seconds=25)).isoformat()
        level, age = compute_staleness(past)
        assert level == StalenessLevel.CRITICAL
        assert 24.0 < age < 27.0

    def test_lost(self):
        past = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        level, age = compute_staleness(past)
        assert level == StalenessLevel.LOST
        assert age > 55.0

    def test_none_is_lost(self):
        level, age = compute_staleness(None)
        assert level == StalenessLevel.LOST
        assert age == -1.0

    def test_invalid_timestamp_is_lost(self):
        level, age = compute_staleness("not-a-timestamp")
        assert level == StalenessLevel.LOST
        assert age == -1.0

    def test_custom_thresholds(self):
        past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        level, _ = compute_staleness(past, warning_s=3.0, critical_s=8.0, lost_s=15.0)
        assert level == StalenessLevel.WARNING


class TestHeartbeatEmitter:
    """Test HeartbeatEmitter lifecycle and subscriber pattern."""

    def test_emit_once(self):
        emitter = HeartbeatEmitter(instance_id="test-emit")
        hb = emitter.emit_once()
        assert hb.instance_id == "test-emit"
        assert emitter.get_latest() is hb

    def test_subscriber_receives_heartbeat(self):
        emitter = HeartbeatEmitter(instance_id="test-sub")
        received = []
        emitter.subscribe(lambda hb: received.append(hb))
        emitter.emit_once()
        assert len(received) == 1
        assert received[0].instance_id == "test-sub"

    def test_unsubscribe(self):
        emitter = HeartbeatEmitter(instance_id="test-unsub")
        received = []
        cb = lambda hb: received.append(hb)
        emitter.subscribe(cb)
        emitter.emit_once()
        emitter.unsubscribe(cb)
        emitter.emit_once()
        assert len(received) == 1  # Only the first one

    def test_history(self):
        emitter = HeartbeatEmitter(instance_id="test-hist")
        for _ in range(5):
            emitter.emit_once()
        history = emitter.get_history(count=3)
        assert len(history) == 3

    def test_providers(self):
        emitter = HeartbeatEmitter(instance_id="test-prov")
        emitter.set_providers(
            soul_hash=lambda: "abc123",
            mode=lambda: "federated",
            task_count=lambda: 3,
            budget=lambda: 42.5,
            peer_count=lambda: 2,
        )
        hb = emitter.emit_once()
        assert hb.soul_version_hash == "abc123"
        assert hb.deployment_mode == "federated"
        assert hb.active_task_count == 3
        assert hb.budget_utilization_pct == 42.5
        assert hb.peer_count == 2

    def test_start_stop(self):
        emitter = HeartbeatEmitter(instance_id="test-bg", interval_s=0.1)
        emitter.start()
        assert emitter.running
        time.sleep(0.35)
        emitter.stop()
        assert not emitter.running
        # Should have emitted at least 2 heartbeats in 0.35s at 0.1s interval
        assert len(emitter.get_history(count=100)) >= 2

    def test_buffer_limit(self):
        emitter = HeartbeatEmitter(instance_id="test-buf", buffer_size=5)
        for _ in range(10):
            emitter.emit_once()
        # Buffer should cap at 5
        assert len(emitter.get_history(count=100)) == 5

    def test_subscriber_error_does_not_crash(self):
        emitter = HeartbeatEmitter(instance_id="test-err")

        def bad_sub(hb):
            raise RuntimeError("subscriber crash")

        emitter.subscribe(bad_sub)
        # Should not raise
        hb = emitter.emit_once()
        assert hb is not None
