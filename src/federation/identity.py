# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Federation Identity — Ed25519 keypair generation, signing, and verification.

Each Lancelot instance generates a unique federation identity on first
activation. The keypair is stored in the Credential Vault and used to
authenticate all Governance API communications.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy-import cryptography to avoid hard dependency at module level
_Ed25519PrivateKey = None
_Ed25519PublicKey = None


def _ensure_crypto():
    """Lazy-import Ed25519 types from cryptography library."""
    global _Ed25519PrivateKey, _Ed25519PublicKey
    if _Ed25519PrivateKey is None:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
        _Ed25519PrivateKey = Ed25519PrivateKey
        _Ed25519PublicKey = Ed25519PublicKey


@dataclass
class FederationIdentity:
    """Represents this instance's federation identity."""
    instance_id: str
    public_key_bytes: bytes
    private_key_bytes: bytes
    fingerprint: str
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def public_key_hex(self) -> str:
        """Return public key as hex string for API responses."""
        return self.public_key_bytes.hex()

    def to_public_dict(self) -> dict:
        """Return public-safe identity info (no private key)."""
        return {
            "instance_id": self.instance_id,
            "public_key": self.public_key_hex(),
            "fingerprint": self.fingerprint,
            "created_at": self.created_at,
        }


def generate_identity() -> FederationIdentity:
    """Generate a new Ed25519 federation identity."""
    _ensure_crypto()
    from cryptography.hazmat.primitives import serialization

    private_key = _Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    fingerprint = hashlib.sha256(public_bytes).hexdigest()[:16]
    instance_id = str(uuid.uuid4())

    logger.info(
        "Generated federation identity: instance_id=%s, fingerprint=%s",
        instance_id, fingerprint,
    )

    return FederationIdentity(
        instance_id=instance_id,
        public_key_bytes=public_bytes,
        private_key_bytes=private_bytes,
        fingerprint=fingerprint,
    )


def load_or_generate_identity(
    data_dir: Optional[str] = None,
) -> FederationIdentity:
    """Load federation identity from disk, or generate and persist a new one.

    Identity is stored as JSON in the data directory (not in the vault,
    since the vault may not be bootstrapped when federation initializes).
    """
    import json
    from pathlib import Path

    if data_dir is None:
        data_dir = "/home/lancelot/data"

    identity_path = Path(data_dir) / "federation_identity.json"

    # Try loading existing identity
    if identity_path.exists():
        try:
            with open(identity_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            identity = FederationIdentity(
                instance_id=data["instance_id"],
                public_key_bytes=bytes.fromhex(data["public_key"]),
                private_key_bytes=bytes.fromhex(data["private_key"]),
                fingerprint=data["fingerprint"],
                created_at=data.get("created_at", ""),
            )
            logger.info(
                "Loaded federation identity: instance_id=%s, fingerprint=%s",
                identity.instance_id, identity.fingerprint,
            )
            return identity
        except Exception as exc:
            logger.warning("Failed to load federation identity: %s — generating new", exc)

    # Generate new identity
    identity = generate_identity()

    # Persist to disk
    try:
        identity_path.parent.mkdir(parents=True, exist_ok=True)
        with open(identity_path, "w", encoding="utf-8") as f:
            json.dump({
                "instance_id": identity.instance_id,
                "public_key": identity.public_key_bytes.hex(),
                "private_key": identity.private_key_bytes.hex(),
                "fingerprint": identity.fingerprint,
                "created_at": identity.created_at,
            }, f, indent=2)
        logger.info("Persisted federation identity to %s", identity_path)
    except Exception as exc:
        logger.warning("Failed to persist federation identity: %s", exc)

    return identity


def sign_payload(identity: FederationIdentity, payload: bytes) -> bytes:
    """Sign a payload with this instance's private key."""
    _ensure_crypto()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = Ed25519PrivateKey.from_private_bytes(identity.private_key_bytes)
    return private_key.sign(payload)


def verify_signature(
    public_key_bytes: bytes, payload: bytes, signature: bytes,
) -> bool:
    """Verify a signature against a public key. Returns False on any failure."""
    _ensure_crypto()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        public_key.verify(signature, payload)
        return True
    except Exception:
        return False
