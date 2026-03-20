# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""Tests for Federation identity generation, persistence, signing, and verification."""

import json
import os
import tempfile
import pytest
from src.federation.identity import (
    FederationIdentity,
    generate_identity,
    load_or_generate_identity,
    sign_payload,
    verify_signature,
)


class TestGenerateIdentity:
    """Test Ed25519 keypair generation."""

    def test_generates_valid_identity(self):
        identity = generate_identity()
        assert identity.instance_id  # UUID string
        assert len(identity.public_key_bytes) == 32  # Ed25519 public key
        assert len(identity.private_key_bytes) == 32  # Ed25519 seed
        assert len(identity.fingerprint) == 16  # SHA-256 prefix
        assert identity.created_at  # ISO timestamp

    def test_unique_identities(self):
        id1 = generate_identity()
        id2 = generate_identity()
        assert id1.instance_id != id2.instance_id
        assert id1.fingerprint != id2.fingerprint
        assert id1.public_key_bytes != id2.public_key_bytes

    def test_public_key_hex(self):
        identity = generate_identity()
        hex_str = identity.public_key_hex()
        assert len(hex_str) == 64  # 32 bytes = 64 hex chars
        # Round-trip
        assert bytes.fromhex(hex_str) == identity.public_key_bytes

    def test_to_public_dict_excludes_private_key(self):
        identity = generate_identity()
        pub = identity.to_public_dict()
        assert "instance_id" in pub
        assert "public_key" in pub
        assert "fingerprint" in pub
        assert "created_at" in pub
        assert "private_key" not in pub
        assert "private_key_bytes" not in pub


class TestLoadOrGenerateIdentity:
    """Test identity persistence."""

    def test_generates_and_persists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = load_or_generate_identity(data_dir=tmpdir)
            assert identity.instance_id

            # Check file was written
            path = os.path.join(tmpdir, "federation_identity.json")
            assert os.path.exists(path)

            with open(path) as f:
                data = json.load(f)
            assert data["instance_id"] == identity.instance_id
            assert data["fingerprint"] == identity.fingerprint

    def test_loads_existing_identity(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Generate first
            id1 = load_or_generate_identity(data_dir=tmpdir)
            # Load again — should get same identity
            id2 = load_or_generate_identity(data_dir=tmpdir)
            assert id1.instance_id == id2.instance_id
            assert id1.fingerprint == id2.fingerprint
            assert id1.public_key_bytes == id2.public_key_bytes

    def test_regenerates_on_corrupt_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "federation_identity.json")
            with open(path, "w") as f:
                f.write("not valid json")
            identity = load_or_generate_identity(data_dir=tmpdir)
            assert identity.instance_id  # Should still work


class TestSignAndVerify:
    """Test Ed25519 signing and verification."""

    def test_sign_and_verify_roundtrip(self):
        identity = generate_identity()
        payload = b"hello federation"
        signature = sign_payload(identity, payload)
        assert isinstance(signature, bytes)
        assert len(signature) == 64  # Ed25519 signature
        assert verify_signature(identity.public_key_bytes, payload, signature)

    def test_verify_wrong_payload_fails(self):
        identity = generate_identity()
        signature = sign_payload(identity, b"original")
        assert not verify_signature(identity.public_key_bytes, b"tampered", signature)

    def test_verify_wrong_key_fails(self):
        id1 = generate_identity()
        id2 = generate_identity()
        signature = sign_payload(id1, b"payload")
        assert not verify_signature(id2.public_key_bytes, b"payload", signature)

    def test_verify_corrupt_signature_fails(self):
        identity = generate_identity()
        assert not verify_signature(identity.public_key_bytes, b"payload", b"bad_sig")
