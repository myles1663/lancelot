# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""Tests for Federation Auth — signing, verification, and replay protection."""

import time
from datetime import datetime, timezone, timedelta

import pytest

from src.federation.identity import generate_identity, sign_payload, verify_signature
from src.federation.auth import (
    FederationAuth,
    VerifyResult,
    HEADER_INSTANCE_ID,
    HEADER_TIMESTAMP,
    HEADER_NONCE,
    HEADER_SIGNATURE,
    _canonical_string,
    _body_hash,
)


@pytest.fixture
def sender_identity():
    return generate_identity()


@pytest.fixture
def receiver_identity():
    return generate_identity()


@pytest.fixture
def sender_auth(sender_identity):
    return FederationAuth(identity=sender_identity)


@pytest.fixture
def receiver_auth(receiver_identity, sender_identity):
    """Receiver auth that knows the sender's public key."""
    def resolver(instance_id):
        if instance_id == sender_identity.instance_id:
            return sender_identity.public_key_bytes
        return None

    auth = FederationAuth(identity=receiver_identity, peer_key_resolver=resolver)
    return auth


# ── Canonical String ──────────────────────────────────────────

class TestCanonicalString:
    def test_deterministic(self):
        a = _canonical_string("POST", "/api/test", "ts", "nonce", b"body")
        b = _canonical_string("POST", "/api/test", "ts", "nonce", b"body")
        assert a == b

    def test_method_case_normalized(self):
        a = _canonical_string("post", "/api/test", "ts", "nonce", b"body")
        b = _canonical_string("POST", "/api/test", "ts", "nonce", b"body")
        assert a == b

    def test_different_body_different_hash(self):
        a = _canonical_string("POST", "/api/test", "ts", "nonce", b"body1")
        b = _canonical_string("POST", "/api/test", "ts", "nonce", b"body2")
        assert a != b

    def test_different_nonce_different_string(self):
        a = _canonical_string("POST", "/api/test", "ts", "nonce1", b"body")
        b = _canonical_string("POST", "/api/test", "ts", "nonce2", b"body")
        assert a != b

    def test_empty_body(self):
        result = _canonical_string("GET", "/path", "ts", "n", b"")
        assert b"GET\n/path\n" in result


# ── Sign Request ─────────────────────────────────────────────

class TestSignRequest:
    def test_returns_all_headers(self, sender_auth, sender_identity):
        headers = sender_auth.sign_request("POST", "/api/test", b'{"key": "val"}')
        assert HEADER_INSTANCE_ID in headers
        assert HEADER_TIMESTAMP in headers
        assert HEADER_NONCE in headers
        assert HEADER_SIGNATURE in headers
        assert headers[HEADER_INSTANCE_ID] == sender_identity.instance_id

    def test_signature_is_hex(self, sender_auth):
        headers = sender_auth.sign_request("GET", "/api/test")
        sig = headers[HEADER_SIGNATURE]
        bytes.fromhex(sig)  # Should not raise

    def test_nonce_is_unique(self, sender_auth):
        h1 = sender_auth.sign_request("GET", "/api/test")
        h2 = sender_auth.sign_request("GET", "/api/test")
        assert h1[HEADER_NONCE] != h2[HEADER_NONCE]

    def test_timestamp_is_recent(self, sender_auth):
        headers = sender_auth.sign_request("GET", "/api/test")
        ts = datetime.fromisoformat(headers[HEADER_TIMESTAMP])
        now = datetime.now(timezone.utc)
        assert abs((now - ts).total_seconds()) < 2.0


# ── Verify Request ───────────────────────────────────────────

class TestVerifyRequest:
    def test_valid_roundtrip(self, sender_auth, receiver_auth):
        body = b'{"command": "test"}'
        headers = sender_auth.sign_request("POST", "/api/test", body)
        result = receiver_auth.verify_request("POST", "/api/test", body, headers)
        assert result.valid
        assert result.instance_id == sender_auth._identity.instance_id

    def test_get_request_roundtrip(self, sender_auth, receiver_auth):
        headers = sender_auth.sign_request("GET", "/api/status")
        result = receiver_auth.verify_request("GET", "/api/status", b"", headers)
        assert result.valid

    def test_missing_header_fails(self, receiver_auth):
        result = receiver_auth.verify_request("POST", "/api/test", b"", {})
        assert not result.valid
        assert "Missing headers" in result.reason

    def test_unknown_peer_fails(self, receiver_auth):
        headers = {
            HEADER_INSTANCE_ID: "unknown-peer-id",
            HEADER_TIMESTAMP: datetime.now(timezone.utc).isoformat(),
            HEADER_NONCE: "abc123",
            HEADER_SIGNATURE: "deadbeef" * 8,
        }
        result = receiver_auth.verify_request("POST", "/api/test", b"", headers)
        assert not result.valid
        assert "Unknown peer" in result.reason

    def test_tampered_body_fails(self, sender_auth, receiver_auth):
        body = b'{"command": "test"}'
        headers = sender_auth.sign_request("POST", "/api/test", body)
        # Tamper with body
        result = receiver_auth.verify_request("POST", "/api/test", b'{"command": "evil"}', headers)
        assert not result.valid
        assert "Signature verification failed" in result.reason

    def test_tampered_path_fails(self, sender_auth, receiver_auth):
        body = b'{"command": "test"}'
        headers = sender_auth.sign_request("POST", "/api/test", body)
        # Verify against different path
        result = receiver_auth.verify_request("POST", "/api/other", body, headers)
        assert not result.valid

    def test_wrong_key_fails(self, sender_auth, receiver_identity):
        """Receiver doesn't know sender's key — uses a different one."""
        def wrong_resolver(instance_id):
            return receiver_identity.public_key_bytes  # Wrong key

        auth = FederationAuth(identity=receiver_identity, peer_key_resolver=wrong_resolver)
        body = b'{"test": true}'
        headers = sender_auth.sign_request("POST", "/api/test", body)
        result = auth.verify_request("POST", "/api/test", body, headers)
        assert not result.valid

    def test_invalid_signature_hex_fails(self, receiver_auth, sender_identity):
        headers = {
            HEADER_INSTANCE_ID: sender_identity.instance_id,
            HEADER_TIMESTAMP: datetime.now(timezone.utc).isoformat(),
            HEADER_NONCE: "abc123",
            HEADER_SIGNATURE: "not-hex!",
        }
        result = receiver_auth.verify_request("POST", "/api/test", b"", headers)
        assert not result.valid
        assert "not hex" in result.reason

    def test_no_resolver_fails(self, sender_auth, receiver_identity):
        auth = FederationAuth(identity=receiver_identity)  # No resolver
        headers = sender_auth.sign_request("POST", "/api/test", b"")
        result = auth.verify_request("POST", "/api/test", b"", headers)
        assert not result.valid
        assert "resolver" in result.reason.lower()


# ── Timestamp Window ─────────────────────────────────────────

class TestTimestampWindow:
    def test_fresh_timestamp_valid(self, sender_auth, receiver_auth):
        body = b""
        headers = sender_auth.sign_request("GET", "/api/test", body)
        result = receiver_auth.verify_request("GET", "/api/test", body, headers)
        assert result.valid

    def test_expired_timestamp_rejected(self, sender_auth, receiver_auth):
        body = b""
        headers = sender_auth.sign_request("POST", "/api/test", body)
        # Forge an old timestamp
        old_ts = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        headers[HEADER_TIMESTAMP] = old_ts
        # Re-sign won't match, but test timestamp check directly
        result = receiver_auth.verify_request("POST", "/api/test", body, headers)
        assert not result.valid

    def test_future_timestamp_rejected(self, receiver_auth, sender_identity):
        future_ts = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
        headers = {
            HEADER_INSTANCE_ID: sender_identity.instance_id,
            HEADER_TIMESTAMP: future_ts,
            HEADER_NONCE: "test-nonce",
            HEADER_SIGNATURE: "aa" * 64,
        }
        result = receiver_auth.verify_request("POST", "/api/test", b"", headers)
        assert not result.valid
        assert "window" in result.reason.lower()


# ── Nonce Replay Protection ──────────────────────────────────

class TestNonceReplay:
    def test_first_use_allowed(self, sender_auth, receiver_auth):
        body = b'{"test": true}'
        headers = sender_auth.sign_request("POST", "/api/test", body)
        result = receiver_auth.verify_request("POST", "/api/test", body, headers)
        assert result.valid

    def test_replay_rejected(self, sender_auth, receiver_auth):
        body = b'{"test": true}'
        headers = sender_auth.sign_request("POST", "/api/test", body)

        # First use succeeds
        result1 = receiver_auth.verify_request("POST", "/api/test", body, headers)
        assert result1.valid

        # Replay fails
        result2 = receiver_auth.verify_request("POST", "/api/test", body, headers)
        assert not result2.valid
        assert "replay" in result2.reason.lower()

    def test_nonce_cache_eviction(self):
        """Nonce cache evicts oldest when full."""
        identity = generate_identity()
        auth = FederationAuth(
            identity=identity,
            nonce_cache_size=5,
        )
        # Fill cache
        for i in range(5):
            assert auth._check_nonce(f"nonce-{i}")

        # All should be seen
        assert not auth._check_nonce("nonce-0")

        # Add one more — evicts nonce-0
        # But nonce-0 was already marked as seen, so adding nonce-5 evicts nonce-0
        # After eviction, nonce-0 should be fresh again
        # First, let's verify nonce-5 is fresh
        assert auth._check_nonce("nonce-5")

        # Now nonce-0 was evicted, so it should be fresh
        assert auth._check_nonce("nonce-0")

    def test_concurrent_nonce_safety(self, receiver_auth):
        """Multiple threads can't both claim the same nonce."""
        import threading
        results = []

        def check_nonce():
            r = receiver_auth._check_nonce("shared-nonce")
            results.append(r)

        threads = [threading.Thread(target=check_nonce) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly one thread should succeed
        assert results.count(True) == 1
        assert results.count(False) == 9
