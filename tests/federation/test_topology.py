# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""Tests for Federation topology registry and deployment mode detection."""

import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
import pytest
from src.federation.topology import (
    DeploymentMode,
    PeerHealth,
    PeerRecord,
    PeerRole,
    TopologyRegistry,
)


class TestPeerRecord:
    def test_default_fields(self):
        peer = PeerRecord(instance_id="test-peer")
        assert peer.instance_id == "test-peer"
        assert peer.role == PeerRole.PEER.value
        assert peer.last_heartbeat_at is None

    def test_to_dict_roundtrip(self):
        peer = PeerRecord(
            instance_id="test-peer",
            fingerprint="abc123",
            address="192.168.1.50:8000",
            role=PeerRole.CHILD.value,
        )
        d = peer.to_dict()
        peer2 = PeerRecord.from_dict(d)
        assert peer2.instance_id == peer.instance_id
        assert peer2.fingerprint == peer.fingerprint
        assert peer2.role == peer.role


class TestTopologyRegistry:
    @pytest.fixture
    def registry(self):
        return TopologyRegistry(self_instance_id="self-001")

    def test_initial_standalone(self, registry):
        assert registry.deployment_mode == DeploymentMode.STANDALONE
        assert registry.peer_count() == 0
        assert registry.list_peers() == []

    def test_register_peer(self, registry):
        peer = registry.register_peer(
            instance_id="peer-a",
            fingerprint="fp-a",
            address="10.0.0.2:8000",
        )
        assert peer.instance_id == "peer-a"
        assert registry.peer_count() == 1

    def test_register_self_raises(self, registry):
        with pytest.raises(ValueError, match="Cannot register self"):
            registry.register_peer(instance_id="self-001")

    def test_max_peers_enforced(self):
        reg = TopologyRegistry(self_instance_id="self-001", max_peers=2)
        reg.register_peer(instance_id="peer-a")
        reg.register_peer(instance_id="peer-b")
        with pytest.raises(ValueError, match="Maximum peer count"):
            reg.register_peer(instance_id="peer-c")

    def test_update_existing_peer(self, registry):
        registry.register_peer(instance_id="peer-a", fingerprint="old-fp")
        registry.register_peer(instance_id="peer-a", fingerprint="new-fp")
        assert registry.peer_count() == 1
        peer = registry.get_peer("peer-a")
        assert peer.fingerprint == "new-fp"

    def test_remove_peer(self, registry):
        registry.register_peer(instance_id="peer-a")
        assert registry.remove_peer("peer-a")
        assert registry.peer_count() == 0
        assert not registry.remove_peer("nonexistent")

    def test_update_heartbeat(self, registry):
        registry.register_peer(instance_id="peer-a")
        now = datetime.now(timezone.utc).isoformat()
        assert registry.update_heartbeat("peer-a", timestamp=now)
        peer = registry.get_peer("peer-a")
        assert peer.last_heartbeat_at == now

    def test_update_heartbeat_unknown_peer(self, registry):
        assert not registry.update_heartbeat("nonexistent")

    def test_get_peer_heartbeats(self, registry):
        registry.register_peer(instance_id="peer-a")
        registry.register_peer(instance_id="peer-b")
        now = datetime.now(timezone.utc).isoformat()
        registry.update_heartbeat("peer-a", timestamp=now)
        hbs = registry.get_peer_heartbeats()
        assert hbs["peer-a"] == now
        assert hbs["peer-b"] is None


class TestDeploymentModeDetection:
    def test_standalone_no_peers(self):
        reg = TopologyRegistry(self_instance_id="self-001")
        assert reg.deployment_mode == DeploymentMode.STANDALONE

    def test_federated_all_peers(self):
        reg = TopologyRegistry(self_instance_id="self-001")
        reg.register_peer(instance_id="peer-a", role=PeerRole.PEER.value)
        reg.register_peer(instance_id="peer-b", role=PeerRole.PEER.value)
        assert reg.deployment_mode == DeploymentMode.FEDERATED

    def test_hierarchical_with_root(self):
        reg = TopologyRegistry(self_instance_id="self-001")
        reg.register_peer(instance_id="peer-a", role=PeerRole.ROOT.value)
        assert reg.deployment_mode == DeploymentMode.HIERARCHICAL

    def test_hierarchical_with_children(self):
        reg = TopologyRegistry(self_instance_id="self-001")
        reg.register_peer(instance_id="peer-a", role=PeerRole.CHILD.value)
        assert reg.deployment_mode == DeploymentMode.HIERARCHICAL

    def test_back_to_standalone_on_remove(self):
        reg = TopologyRegistry(self_instance_id="self-001")
        reg.register_peer(instance_id="peer-a")
        assert reg.deployment_mode == DeploymentMode.FEDERATED
        reg.remove_peer("peer-a")
        assert reg.deployment_mode == DeploymentMode.STANDALONE


class TestPeerHealth:
    def test_healthy_peer(self):
        reg = TopologyRegistry(self_instance_id="self-001")
        reg.register_peer(instance_id="peer-a")
        now = datetime.now(timezone.utc).isoformat()
        reg.update_heartbeat("peer-a", timestamp=now)
        assert reg.get_peer_health("peer-a") == PeerHealth.HEALTHY

    def test_warning_peer(self):
        reg = TopologyRegistry(
            self_instance_id="self-001",
            staleness_warning_s=5.0,
            staleness_critical_s=15.0,
            staleness_lost_s=25.0,
        )
        reg.register_peer(instance_id="peer-a")
        old = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        reg.update_heartbeat("peer-a", timestamp=old)
        assert reg.get_peer_health("peer-a") == PeerHealth.WARNING

    def test_lost_peer(self):
        reg = TopologyRegistry(
            self_instance_id="self-001",
            staleness_lost_s=5.0,
        )
        reg.register_peer(instance_id="peer-a")
        old = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        reg.update_heartbeat("peer-a", timestamp=old)
        assert reg.get_peer_health("peer-a") == PeerHealth.LOST

    def test_unknown_peer(self):
        reg = TopologyRegistry(self_instance_id="self-001")
        assert reg.get_peer_health("nonexistent") == PeerHealth.UNKNOWN

    def test_no_heartbeat_is_lost(self):
        reg = TopologyRegistry(self_instance_id="self-001")
        reg.register_peer(instance_id="peer-a")
        # No heartbeat ever received
        assert reg.get_peer_health("peer-a") == PeerHealth.LOST

    def test_health_summary(self):
        reg = TopologyRegistry(self_instance_id="self-001")
        reg.register_peer(instance_id="peer-a")
        now = datetime.now(timezone.utc).isoformat()
        reg.update_heartbeat("peer-a", timestamp=now)
        summary = reg.get_health_summary()
        assert summary["total_peers"] == 1
        assert summary["healthy"] == 1
        assert summary["deployment_mode"] == "federated"


class TestTopologyPersistence:
    def test_persist_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "topology.json")

            reg1 = TopologyRegistry(
                self_instance_id="self-001",
                persistence_path=path,
            )
            reg1.register_peer(
                instance_id="peer-a",
                fingerprint="fp-a",
                role=PeerRole.CHILD.value,
            )
            assert os.path.exists(path)

            # Load into new registry
            reg2 = TopologyRegistry(
                self_instance_id="self-001",
                persistence_path=path,
            )
            assert reg2.peer_count() == 1
            peer = reg2.get_peer("peer-a")
            assert peer.fingerprint == "fp-a"
            assert reg2.deployment_mode == DeploymentMode.HIERARCHICAL

    def test_no_persistence_path(self):
        reg = TopologyRegistry(self_instance_id="self-001")
        reg.register_peer(instance_id="peer-a")
        # Should not raise
        assert reg.peer_count() == 1

    def test_legacy_list_payload_is_ignored_without_warning(self, caplog):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "topology.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump([], f)

            caplog.set_level("INFO")
            reg = TopologyRegistry(
                self_instance_id="self-001",
                persistence_path=path,
            )

            assert reg.peer_count() == 0
            assert "Ignoring legacy topology payload" in caplog.text
            assert "Failed to load topology from disk" not in caplog.text

    def test_non_object_peers_payload_is_ignored_without_warning(self, caplog):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "topology.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"peers": []}, f)

            caplog.set_level("INFO")
            reg = TopologyRegistry(
                self_instance_id="self-001",
                persistence_path=path,
            )

            assert reg.peer_count() == 0
            assert "Ignoring topology peers payload" in caplog.text
            assert "Failed to load topology from disk" not in caplog.text

    def test_heartbeat_updates_survive_reopen(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "topology.json")
            reg1 = TopologyRegistry(
                self_instance_id="self-001",
                persistence_path=path,
            )
            reg1.register_peer(instance_id="peer-a", soul_version_hash="oldhash")
            now = datetime.now(timezone.utc).isoformat()
            reg1.update_heartbeat("peer-a", timestamp=now, soul_version_hash="newhash")

            reg2 = TopologyRegistry(
                self_instance_id="self-001",
                persistence_path=path,
            )
            peer = reg2.get_peer("peer-a")
            assert peer is not None
            assert peer.last_heartbeat_at == now
            assert peer.soul_version_hash == "newhash"
