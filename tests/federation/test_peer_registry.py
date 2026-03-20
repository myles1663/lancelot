# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""Tests for Federation Peer Registry — SQLite-backed peer storage."""

import os
import tempfile
import threading

import pytest

from src.federation.peer_registry import PeerRegistryStore


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_peers.sqlite")


@pytest.fixture
def store(db_path):
    return PeerRegistryStore(db_path)


# ═══════════════════════════════════════════════════════════════
# Schema & Initialization
# ═══════════════════════════════════════════════════════════════

class TestInitialization:
    def test_creates_db_file(self, db_path):
        store = PeerRegistryStore(db_path)
        assert os.path.exists(db_path)

    def test_creates_parent_dirs(self, tmp_path):
        nested = str(tmp_path / "a" / "b" / "c" / "peers.sqlite")
        store = PeerRegistryStore(nested)
        assert os.path.exists(nested)

    def test_double_init_safe(self, db_path):
        s1 = PeerRegistryStore(db_path)
        s1.save_peer("peer-1", fingerprint="fp1")
        s2 = PeerRegistryStore(db_path)
        assert s2.get_peer("peer-1") is not None

    def test_empty_store_has_no_peers(self, store):
        assert store.list_peers() == []
        assert store.peer_count() == 0


# ═══════════════════════════════════════════════════════════════
# Peer CRUD
# ═══════════════════════════════════════════════════════════════

class TestPeerCRUD:
    def test_save_and_get(self, store):
        store.save_peer(
            instance_id="peer-1",
            fingerprint="fp-abc",
            public_key_hex="deadbeef",
            address="http://peer1:8000",
            role="child",
            soul_version_hash="hash123",
        )
        peer = store.get_peer("peer-1")
        assert peer is not None
        assert peer["instance_id"] == "peer-1"
        assert peer["fingerprint"] == "fp-abc"
        assert peer["public_key_hex"] == "deadbeef"
        assert peer["address"] == "http://peer1:8000"
        assert peer["role"] == "child"
        assert peer["soul_version_hash"] == "hash123"

    def test_save_upserts(self, store):
        store.save_peer("peer-1", address="http://old:8000")
        store.save_peer("peer-1", address="http://new:8000")
        peer = store.get_peer("peer-1")
        assert peer["address"] == "http://new:8000"
        assert store.peer_count() == 1

    def test_list_peers(self, store):
        store.save_peer("peer-1", role="root")
        store.save_peer("peer-2", role="child")
        store.save_peer("peer-3", role="peer")
        peers = store.list_peers()
        assert len(peers) == 3
        ids = {p["instance_id"] for p in peers}
        assert ids == {"peer-1", "peer-2", "peer-3"}

    def test_remove_peer(self, store):
        store.save_peer("peer-1")
        assert store.remove_peer("peer-1")
        assert store.get_peer("peer-1") is None
        assert store.peer_count() == 0

    def test_remove_nonexistent(self, store):
        assert not store.remove_peer("ghost")

    def test_get_nonexistent(self, store):
        assert store.get_peer("ghost") is None

    def test_metadata_roundtrip(self, store):
        store.save_peer("peer-1", metadata={"region": "east", "version": "0.3.0"})
        peer = store.get_peer("peer-1")
        assert peer["metadata"]["region"] == "east"
        assert peer["metadata"]["version"] == "0.3.0"

    def test_peer_count(self, store):
        assert store.peer_count() == 0
        store.save_peer("p1")
        assert store.peer_count() == 1
        store.save_peer("p2")
        assert store.peer_count() == 2
        store.remove_peer("p1")
        assert store.peer_count() == 1


# ═══════════════════════════════════════════════════════════════
# Heartbeat Update
# ═══════════════════════════════════════════════════════════════

class TestHeartbeat:
    def test_update_heartbeat(self, store):
        store.save_peer("peer-1")
        assert store.update_heartbeat("peer-1", "2026-03-16T12:00:00+00:00")
        peer = store.get_peer("peer-1")
        assert peer["last_heartbeat_at"] == "2026-03-16T12:00:00+00:00"

    def test_update_heartbeat_with_soul_hash(self, store):
        store.save_peer("peer-1")
        store.update_heartbeat("peer-1", "2026-03-16T12:00:00+00:00", "newhash")
        peer = store.get_peer("peer-1")
        assert peer["soul_version_hash"] == "newhash"

    def test_update_unknown_peer_returns_false(self, store):
        assert not store.update_heartbeat("ghost", "2026-03-16T12:00:00+00:00")


# ═══════════════════════════════════════════════════════════════
# Public Key Lookup
# ═══════════════════════════════════════════════════════════════

class TestPublicKeyLookup:
    def test_get_peer_public_key(self, store):
        store.save_peer("peer-1", public_key_hex="abcdef01")
        key = store.get_peer_public_key("peer-1")
        assert key == bytes.fromhex("abcdef01")

    def test_unknown_peer_returns_none(self, store):
        assert store.get_peer_public_key("ghost") is None

    def test_empty_key_returns_none(self, store):
        store.save_peer("peer-1", public_key_hex="")
        assert store.get_peer_public_key("peer-1") is None


# ═══════════════════════════════════════════════════════════════
# Nonce Replay Protection
# ═══════════════════════════════════════════════════════════════

class TestNonces:
    def test_first_nonce_accepted(self, store):
        assert store.check_and_store_nonce("nonce-1")

    def test_duplicate_nonce_rejected(self, store):
        assert store.check_and_store_nonce("nonce-1")
        assert not store.check_and_store_nonce("nonce-1")

    def test_different_nonces_accepted(self, store):
        assert store.check_and_store_nonce("nonce-1")
        assert store.check_and_store_nonce("nonce-2")
        assert store.check_and_store_nonce("nonce-3")

    def test_nonce_count(self, store):
        assert store.nonce_count() == 0
        store.check_and_store_nonce("n1")
        store.check_and_store_nonce("n2")
        assert store.nonce_count() == 2

    def test_prune_old_nonces(self, store):
        store.check_and_store_nonce("n1")
        store.check_and_store_nonce("n2")
        # Prune with 0 max_age removes everything
        pruned = store.prune_old_nonces(max_age_s=0.0)
        assert pruned == 2
        assert store.nonce_count() == 0

    def test_prune_preserves_recent(self, store):
        store.check_and_store_nonce("n1")
        # Prune with very large max_age keeps everything
        pruned = store.prune_old_nonces(max_age_s=3600.0)
        assert pruned == 0
        assert store.nonce_count() == 1


# ═══════════════════════════════════════════════════════════════
# Thread Safety
# ═══════════════════════════════════════════════════════════════

class TestThreadSafety:
    def test_concurrent_peer_writes(self, store):
        errors = []

        def write_peers(start):
            try:
                for i in range(10):
                    store.save_peer(f"peer-{start + i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_peers, args=(i * 10,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert store.peer_count() == 50

    def test_concurrent_nonce_checks(self, store):
        """Same nonce from multiple threads — only one should succeed."""
        results = []

        def check():
            r = store.check_and_store_nonce("shared-nonce")
            results.append(r)

        threads = [threading.Thread(target=check) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results.count(True) == 1
        assert results.count(False) == 9


# ═══════════════════════════════════════════════════════════════
# Persistence
# ═══════════════════════════════════════════════════════════════

class TestPersistence:
    def test_data_survives_reopen(self, db_path):
        s1 = PeerRegistryStore(db_path)
        s1.save_peer("peer-1", fingerprint="fp1", address="http://p1:8000")

        s2 = PeerRegistryStore(db_path)
        peer = s2.get_peer("peer-1")
        assert peer is not None
        assert peer["fingerprint"] == "fp1"

    def test_nonces_survive_reopen(self, db_path):
        s1 = PeerRegistryStore(db_path)
        s1.check_and_store_nonce("nonce-persistent")

        s2 = PeerRegistryStore(db_path)
        assert not s2.check_and_store_nonce("nonce-persistent")
