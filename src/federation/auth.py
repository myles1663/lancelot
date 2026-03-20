# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Federation Auth — Ed25519 request signing, verification, and replay protection.

Every inter-instance HTTP request is signed by the sender's private key.
The receiver verifies the signature using the sender's public key from
the topology registry. Replay attacks are prevented via nonce + timestamp
window enforcement.

Header protocol:
    X-Federation-Instance-Id: <sender instance UUID>
    X-Federation-Timestamp:   <ISO 8601 UTC>
    X-Federation-Nonce:       <random 32 bytes hex>
    X-Federation-Signature:   <Ed25519 signature hex of canonical string>

Canonical string:
    "{method}\n{path}\n{timestamp}\n{nonce}\n{sha256(body)}"
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Deque, Optional, Set

from src.federation.identity import FederationIdentity, sign_payload, verify_signature

logger = logging.getLogger(__name__)

# Header names
HEADER_INSTANCE_ID = "X-Federation-Instance-Id"
HEADER_TIMESTAMP = "X-Federation-Timestamp"
HEADER_NONCE = "X-Federation-Nonce"
HEADER_SIGNATURE = "X-Federation-Signature"

# All auth headers
AUTH_HEADERS = (HEADER_INSTANCE_ID, HEADER_TIMESTAMP, HEADER_NONCE, HEADER_SIGNATURE)


@dataclass
class VerifyResult:
    """Result of verifying an incoming federation request."""
    valid: bool
    instance_id: str = ""
    reason: str = ""


def _body_hash(body: bytes) -> str:
    """SHA-256 hex digest of the request body."""
    return hashlib.sha256(body).hexdigest()


def _canonical_string(
    method: str, path: str, timestamp: str, nonce: str, body: bytes,
) -> bytes:
    """Build the canonical string for signing/verification."""
    bh = _body_hash(body)
    canonical = f"{method.upper()}\n{path}\n{timestamp}\n{nonce}\n{bh}"
    return canonical.encode("utf-8")


class FederationAuth:
    """Request signing and verification for federation transport.

    Uses Ed25519 signatures over a canonical request string.
    Provides replay protection via nonce deduplication and timestamp
    window enforcement.
    """

    def __init__(
        self,
        identity: FederationIdentity,
        peer_key_resolver=None,
        timestamp_window_s: float = 30.0,
        nonce_cache_size: int = 10_000,
    ):
        """
        Args:
            identity: This instance's federation identity (has private key).
            peer_key_resolver: Callable(instance_id) -> Optional[bytes]
                Returns the public key bytes for a given peer instance ID.
                Typically wraps TopologyRegistry.get_peer().
            timestamp_window_s: Maximum age of a valid request timestamp.
            nonce_cache_size: Maximum nonces to track for replay protection.
        """
        self._identity = identity
        self._peer_key_resolver = peer_key_resolver
        self._timestamp_window_s = timestamp_window_s
        self._nonce_cache: Deque[str] = deque(maxlen=nonce_cache_size)
        self._nonce_set: Set[str] = set()
        self._lock = threading.Lock()

    def set_peer_key_resolver(self, resolver) -> None:
        """Set or update the peer public key resolver."""
        self._peer_key_resolver = resolver

    # ── Outbound: Sign Requests ──────────────────────────────

    def sign_request(
        self,
        method: str,
        path: str,
        body: bytes = b"",
    ) -> dict[str, str]:
        """Sign an outbound federation request.

        Returns a dict of headers to attach to the HTTP request.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        nonce = os.urandom(32).hex()

        canonical = _canonical_string(method, path, timestamp, nonce, body)
        signature = sign_payload(self._identity, canonical)

        return {
            HEADER_INSTANCE_ID: self._identity.instance_id,
            HEADER_TIMESTAMP: timestamp,
            HEADER_NONCE: nonce,
            HEADER_SIGNATURE: signature.hex(),
        }

    # ── Inbound: Verify Requests ─────────────────────────────

    def verify_request(
        self,
        method: str,
        path: str,
        body: bytes,
        headers: dict[str, str],
    ) -> VerifyResult:
        """Verify an incoming federation request.

        Checks:
        1. All required headers present
        2. Sender is a known peer with a public key
        3. Timestamp within window
        4. Nonce not reused
        5. Ed25519 signature valid

        Returns VerifyResult with valid=True/False and reason.
        """
        # 1. Extract headers (normalize to lowercase for case-insensitive lookup)
        lc_headers = {k.lower(): v for k, v in headers.items()}
        instance_id = lc_headers.get(HEADER_INSTANCE_ID.lower(), "")
        timestamp = lc_headers.get(HEADER_TIMESTAMP.lower(), "")
        nonce = lc_headers.get(HEADER_NONCE.lower(), "")
        signature_hex = lc_headers.get(HEADER_SIGNATURE.lower(), "")

        if not all([instance_id, timestamp, nonce, signature_hex]):
            missing = [h for h in AUTH_HEADERS if not lc_headers.get(h.lower())]
            return VerifyResult(
                valid=False,
                reason=f"Missing headers: {', '.join(missing)}",
            )

        # 2. Resolve sender's public key
        if not self._peer_key_resolver:
            return VerifyResult(
                valid=False,
                reason="No peer key resolver configured",
            )

        public_key_bytes = self._peer_key_resolver(instance_id)
        if public_key_bytes is None:
            return VerifyResult(
                valid=False,
                instance_id=instance_id,
                reason=f"Unknown peer: {instance_id}",
            )

        # 3. Timestamp window
        if not self._check_timestamp(timestamp):
            return VerifyResult(
                valid=False,
                instance_id=instance_id,
                reason=f"Timestamp outside {self._timestamp_window_s}s window",
            )

        # 4. Nonce replay check
        if not self._check_nonce(nonce):
            return VerifyResult(
                valid=False,
                instance_id=instance_id,
                reason="Nonce already used (replay detected)",
            )

        # 5. Signature verification
        canonical = _canonical_string(method, path, timestamp, nonce, body)
        try:
            signature_bytes = bytes.fromhex(signature_hex)
        except ValueError:
            return VerifyResult(
                valid=False,
                instance_id=instance_id,
                reason="Invalid signature format (not hex)",
            )

        if not verify_signature(public_key_bytes, canonical, signature_bytes):
            return VerifyResult(
                valid=False,
                instance_id=instance_id,
                reason="Signature verification failed",
            )

        return VerifyResult(valid=True, instance_id=instance_id)

    def _check_timestamp(self, timestamp_iso: str) -> bool:
        """Check if timestamp is within the allowed window."""
        try:
            ts = datetime.fromisoformat(timestamp_iso)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            age = abs((now - ts).total_seconds())
            return age <= self._timestamp_window_s
        except (ValueError, TypeError):
            return False

    def _check_nonce(self, nonce: str) -> bool:
        """Check if nonce is new (not replayed). Thread-safe.

        Returns True if the nonce is fresh and records it.
        Returns False if it has been seen before.
        """
        with self._lock:
            if nonce in self._nonce_set:
                return False

            # Evict oldest if at capacity
            if len(self._nonce_cache) >= self._nonce_cache.maxlen:
                evicted = self._nonce_cache[0]
                self._nonce_set.discard(evicted)

            self._nonce_cache.append(nonce)
            self._nonce_set.add(nonce)
            return True

    def prune_nonces(self) -> int:
        """Manually prune the nonce cache. Returns number remaining."""
        with self._lock:
            return len(self._nonce_set)
