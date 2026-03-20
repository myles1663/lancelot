# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""Tests for Federation Peer Protocol — registration handshake."""

import pytest

from src.federation.identity import generate_identity, sign_payload, verify_signature
from src.federation.topology import TopologyRegistry
from src.federation.peer_protocol import PeerRegistrationProtocol, RegistrationResult


@pytest.fixture
def identity_a():
    return generate_identity()


@pytest.fixture
def identity_b():
    return generate_identity()


@pytest.fixture
def topology_a(identity_a):
    return TopologyRegistry(self_instance_id=identity_a.instance_id)


@pytest.fixture
def topology_b(identity_b):
    return TopologyRegistry(self_instance_id=identity_b.instance_id)


@pytest.fixture
def protocol_a(identity_a, topology_a):
    return PeerRegistrationProtocol(
        identity=identity_a,
        topology=topology_a,
        transport=None,  # Not needed for handle_* tests
    )


@pytest.fixture
def protocol_b(identity_b, topology_b):
    return PeerRegistrationProtocol(
        identity=identity_b,
        topology=topology_b,
        transport=None,
    )


class TestHandleRegistrationRequest:
    def test_valid_registration(self, protocol_b, identity_a):
        challenge = "abc123"
        challenge_sig = sign_payload(identity_a, challenge.encode("utf-8")).hex()

        request = {
            "instance_id": identity_a.instance_id,
            "public_key_hex": identity_a.public_key_hex(),
            "fingerprint": identity_a.fingerprint,
            "address": "http://peer-a:8000",
            "role": "peer",
            "soul_version_hash": "hash1",
            "challenge": challenge,
            "challenge_signature": challenge_sig,
        }

        response = protocol_b.handle_registration_request(request)
        assert response["accepted"]
        assert response["instance_id"]  # Returns own identity
        assert response["public_key_hex"]
        assert response["challenge_response"]

        # Verify the challenge_response is valid
        assert verify_signature(
            protocol_b._identity.public_key_bytes,
            challenge.encode("utf-8"),
            bytes.fromhex(response["challenge_response"]),
        )

    def test_missing_fields_rejected(self, protocol_b):
        response = protocol_b.handle_registration_request({})
        assert not response.get("accepted", True)
        assert "Missing" in response.get("error", "")

    def test_bad_signature_rejected(self, protocol_b, identity_a):
        request = {
            "instance_id": identity_a.instance_id,
            "public_key_hex": identity_a.public_key_hex(),
            "fingerprint": identity_a.fingerprint,
            "challenge": "test",
            "challenge_signature": "deadbeef" * 8,
        }
        response = protocol_b.handle_registration_request(request)
        assert not response.get("accepted", True)

    def test_peer_registered_in_topology(self, protocol_b, identity_a, topology_b):
        challenge = "test123"
        challenge_sig = sign_payload(identity_a, challenge.encode("utf-8")).hex()

        request = {
            "instance_id": identity_a.instance_id,
            "public_key_hex": identity_a.public_key_hex(),
            "fingerprint": identity_a.fingerprint,
            "address": "http://peer-a:8000",
            "role": "child",
            "soul_version_hash": "hash1",
            "challenge": challenge,
            "challenge_signature": challenge_sig,
        }

        protocol_b.handle_registration_request(request)

        peer = topology_b.get_peer(identity_a.instance_id)
        assert peer is not None
        assert peer.role == "child"
        assert peer.address == "http://peer-a:8000"

    def test_max_peers_exceeded(self, identity_b, identity_a):
        topology = TopologyRegistry(
            self_instance_id=identity_b.instance_id,
            max_peers=1,
        )
        # Register one peer to fill the limit
        topology.register_peer("existing-peer", role="peer")

        protocol = PeerRegistrationProtocol(
            identity=identity_b, topology=topology, transport=None,
        )

        challenge = "test"
        request = {
            "instance_id": identity_a.instance_id,
            "public_key_hex": identity_a.public_key_hex(),
            "fingerprint": identity_a.fingerprint,
            "challenge": challenge,
            "challenge_signature": sign_payload(
                identity_a, challenge.encode("utf-8")
            ).hex(),
        }

        response = protocol.handle_registration_request(request)
        assert not response.get("accepted", True)
        assert "rejected" in response.get("error", "").lower() or "Maximum" in response.get("error", "")

    def test_no_signature_still_registers(self, protocol_b, identity_a):
        """Without challenge_signature, registration proceeds (no proof check)."""
        request = {
            "instance_id": identity_a.instance_id,
            "public_key_hex": identity_a.public_key_hex(),
            "fingerprint": identity_a.fingerprint,
            "challenge": "test",
        }
        response = protocol_b.handle_registration_request(request)
        assert response["accepted"]


class TestHandlePeerRemoval:
    def test_remove_registered_peer(self, protocol_b, identity_a, topology_b):
        topology_b.register_peer(identity_a.instance_id, role="peer")
        result = protocol_b.handle_peer_removal(identity_a.instance_id)
        assert result["removed"]
        assert topology_b.get_peer(identity_a.instance_id) is None

    def test_remove_unknown_peer(self, protocol_b):
        result = protocol_b.handle_peer_removal("ghost")
        assert not result["removed"]


class TestMutualRegistration:
    def test_challenge_response_roundtrip(self, protocol_a, protocol_b, identity_a, identity_b):
        """Simulate the full handshake without transport."""
        challenge = "mutual-test-challenge"

        # Step 1: A sends registration to B
        request = {
            "instance_id": identity_a.instance_id,
            "public_key_hex": identity_a.public_key_hex(),
            "fingerprint": identity_a.fingerprint,
            "address": "http://a:8000",
            "role": "peer",
            "challenge": challenge,
            "challenge_signature": sign_payload(
                identity_a, challenge.encode("utf-8")
            ).hex(),
        }

        # Step 2: B processes and returns response
        response = protocol_b.handle_registration_request(request)
        assert response["accepted"]

        # Step 3: A verifies B's challenge response
        verified = verify_signature(
            bytes.fromhex(response["public_key_hex"]),
            challenge.encode("utf-8"),
            bytes.fromhex(response["challenge_response"]),
        )
        assert verified

        # Both sides should now know each other
        # B knows A
        assert protocol_b._topology.get_peer(identity_a.instance_id) is not None
