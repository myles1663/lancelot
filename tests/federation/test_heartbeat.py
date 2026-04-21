# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""Tests for Federation heartbeat emission and staleness computation."""

import asyncio
import time
from datetime import datetime, timezone, timedelta
import pytest
from src.core.outbound_http import OutboundNetworkError
from src.federation.heartbeat import (
    Heartbeat,
    HeartbeatEmitter,
    StalenessLevel,
    compute_staleness,
)
from src.federation.heartbeat_mesh import HeartbeatMesh
from src.federation.topology import TopologyRegistry


@pytest.fixture(autouse=True)
def allow_outbound_requests(monkeypatch):
    monkeypatch.setattr("src.federation.heartbeat_mesh.assert_url_allowed", lambda url, **kwargs: url)


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


class TestHeartbeatMesh:
    def test_divergence_snapshot_uses_runtime_providers(self):
        topo = TopologyRegistry(self_instance_id="self-1")
        topo.register_peer(
            instance_id="peer-1",
            fingerprint="fp",
            public_key_hex="pk",
            address="http://peer-1:8000",
            role="peer",
        )
        captured = {}

        class _FakeDivergence:
            def check_connectivity(self, peer_last_heartbeats, **kwargs):
                captured["peer_last_heartbeats"] = peer_last_heartbeats
                captured.update(kwargs)

        mesh = HeartbeatMesh(
            topology=topo,
            divergence_detector=_FakeDivergence(),
            current_soul_hash_provider=lambda: "soul-123",
            active_task_count_provider=lambda: 4,
            hive_spawn_count_provider=lambda: 2,
            hive_spawn_states_provider=lambda: {"agent-1": "executing"},
            pending_handoffs_provider=lambda: [{"handoff_id": "h1", "state": "accepted"}],
            budget_utilization_provider=lambda: 87.5,
        )

        mesh._process_sse_event("peer-1", 'event: heartbeat\ndata: {"timestamp": "2026-04-15T00:00:00+00:00", "soul_version_hash":"peer-soul"}')

        assert captured["current_soul_hash"] == "soul-123"
        assert captured["active_task_count"] == 4
        assert captured["hive_spawn_count"] == 2
        assert captured["hive_spawn_states"] == {"agent-1": "executing"}
        assert captured["pending_handoffs"] == [{"handoff_id": "h1", "state": "accepted"}]
        assert captured["budget_utilization_pct"] == 87.5

    def test_divergence_callbacks_fire_on_state_transitions(self):
        topo = TopologyRegistry(self_instance_id="self-1", staleness_lost_s=1.0)
        topo.register_peer(
            instance_id="peer-1",
            fingerprint="fp",
            public_key_hex="pk",
            address="http://peer-1:8000",
            role="peer",
        )

        class _FakeDivergence:
            def __init__(self):
                self.state = type("State", (), {"value": "connected"})()
                self.divergence_snapshot = None

            def check_connectivity(self, peer_last_heartbeats, **kwargs):
                from src.federation.divergence import DivergenceSnapshot
                if kwargs["current_soul_hash"] == "diverge-now":
                    self.state = type("State", (), {"value": "diverged"})()
                    self.divergence_snapshot = DivergenceSnapshot(
                        soul_hash_at_divergence="diverge-now"
                    )
                    return self.state, self.divergence_snapshot
                self.state = type("State", (), {"value": "reconnecting"})()
                return self.state, None

        events = []
        fake = _FakeDivergence()
        current_soul = {"value": "diverge-now"}
        mesh = HeartbeatMesh(
            topology=topo,
            divergence_detector=fake,
            current_soul_hash_provider=lambda: current_soul["value"],
            on_diverged=lambda peer_id, snapshot: events.append(("diverged", peer_id, snapshot.soul_hash_at_divergence)),
            on_reconnecting=lambda peer_id, detector: events.append(("reconnecting", peer_id, detector.state.value)),
        )

        mesh._process_sse_event("peer-1", 'event: heartbeat\ndata: {"timestamp": "2026-04-15T00:00:00+00:00", "soul_version_hash":"peer-soul"}')
        current_soul["value"] = "reconnect-now"
        mesh._process_sse_event("peer-1", 'event: heartbeat\ndata: {"timestamp": "2026-04-15T00:00:01+00:00", "soul_version_hash":"peer-soul"}')

        assert events[0] == ("diverged", "peer-1", "diverge-now")
        assert events[1] == ("reconnecting", "peer-1", "reconnecting")

    @pytest.mark.asyncio
    async def test_start_evaluates_persisted_lost_peer_state(self):
        topo = TopologyRegistry(self_instance_id="self-1", staleness_lost_s=1.0)
        topo.register_peer(
            instance_id="peer-1",
            fingerprint="fp",
            public_key_hex="pk",
            address="http://peer-1:8000",
            role="peer",
        )
        old = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        topo.update_heartbeat("peer-1", timestamp=old)
        events = []

        from src.federation.divergence import DivergenceDetector

        detector = DivergenceDetector(instance_id="self-1", staleness_lost_s=1.0)
        mesh = HeartbeatMesh(
            topology=topo,
            divergence_detector=detector,
            on_diverged=lambda peer_id, snapshot: events.append((peer_id, snapshot.soul_hash_at_divergence)),
        )

        await mesh.start()
        try:
            assert detector.state.value == "diverged"
            assert events == [("", "")]
        finally:
            await mesh.stop()

    def test_divergence_evaluation_failure_is_surfaced(self):
        topo = TopologyRegistry(self_instance_id="self-1")

        class BrokenDivergence:
            def check_connectivity(self, peer_last_heartbeats, **kwargs):
                raise RuntimeError("detector exploded")

        mesh = HeartbeatMesh(
            topology=topo,
            divergence_detector=BrokenDivergence(),
        )

        mesh._evaluate_divergence("peer-1")

        assert mesh.divergence_evaluation_failed is True
        assert mesh.divergence_status_error == "detector exploded"

    @pytest.mark.asyncio
    async def test_subscription_status_reports_reconnecting_on_failure(self):
        topo = TopologyRegistry(self_instance_id="self-1")
        topo.register_peer(
            instance_id="peer-1",
            fingerprint="fp",
            public_key_hex="pk",
            address="http://peer-1:8000",
            role="peer",
        )

        class FlakyMesh(HeartbeatMesh):
            async def _consume_stream(self, instance_id: str, url: str) -> None:
                raise RuntimeError("connect failed")

        mesh = FlakyMesh(topology=topo)
        await mesh.start()
        try:
            await asyncio.sleep(0.05)
            assert mesh.get_subscription_status()["peer-1"] == "reconnecting"
            assert mesh.get_stream_outcome_status()["peer-1"] == "failed"
            assert mesh.get_stream_errors()["peer-1"] == "connect failed"
        finally:
            await mesh.stop()

    @pytest.mark.asyncio
    async def test_subscription_status_preserves_http_failure_outcome(self):
        topo = TopologyRegistry(self_instance_id="self-1")
        topo.register_peer(
            instance_id="peer-1",
            fingerprint="fp",
            public_key_hex="pk",
            address="http://peer-1:8000",
            role="peer",
        )

        class HttpFailMesh(HeartbeatMesh):
            async def _consume_stream(self, instance_id: str, url: str) -> None:
                self._subscription_status[instance_id] = "failed"
                self._stream_outcome_status[instance_id] = "failed"
                self._stream_errors[instance_id] = "HTTP 503"
                return

        mesh = HttpFailMesh(topology=topo)
        await mesh.start()
        try:
            await asyncio.sleep(0.05)
            assert mesh.get_subscription_status()["peer-1"] == "reconnecting"
            assert mesh.get_stream_outcome_status()["peer-1"] == "failed"
            assert mesh.get_stream_errors()["peer-1"] == "HTTP 503"
        finally:
            await mesh.stop()

    @pytest.mark.asyncio
    async def test_subscription_status_reports_allowlist_block(self, monkeypatch):
        topo = TopologyRegistry(self_instance_id="self-1")
        topo.register_peer(
            instance_id="peer-1",
            fingerprint="fp",
            public_key_hex="pk",
            address="https://blocked.example.com",
            role="peer",
        )
        monkeypatch.setattr(
            "src.federation.heartbeat_mesh.assert_url_allowed",
            lambda url, **kwargs: (_ for _ in ()).throw(
                OutboundNetworkError("Federation heartbeat stream blocked by network allowlist")
            ),
        )

        mesh = HeartbeatMesh(topology=topo)
        await mesh.start()
        try:
            await asyncio.sleep(0.05)
            assert mesh.get_subscription_status()["peer-1"] == "reconnecting"
            assert mesh.get_stream_outcome_status()["peer-1"] == "failed"
            assert "network allowlist" in mesh.get_stream_errors()["peer-1"]
        finally:
            await mesh.stop()
