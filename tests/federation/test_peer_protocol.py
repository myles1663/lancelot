# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""Tests for Federation Peer Protocol mutual registration handshake."""

from types import SimpleNamespace

import pytest

import src.federation.peer_protocol as peer_protocol_module
from src.federation.identity import generate_identity, sign_payload
from src.federation.peer_protocol import PeerRegistrationProtocol, PendingRegistration
from src.federation.topology import TopologyRegistry


class LoopbackTransport:
    """Minimal in-memory transport for mutual registration tests."""

    def __init__(self):
        self._routes = {}

    def register(self, address: str, protocol: PeerRegistrationProtocol) -> None:
        self._routes[address.rstrip("/")] = protocol

    async def send(
        self,
        peer_address: str,
        method: str,
        path: str,
        body=None,
        peer_id: str = "",
        timeout_override_s: float | None = None,
    ):
        protocol = self._routes.get(peer_address.rstrip("/"))
        if protocol is None:
            return SimpleNamespace(
                success=False,
                body=None,
                status_code=404,
                error=f"Unknown test route: {peer_address}",
                peer_id=peer_id,
            )

        if path == "/api/federation/peer/register":
            response = await protocol.handle_registration_request(body or {})
        elif path == "/api/federation/peer/confirm":
            response = protocol.handle_registration_confirm(body or {})
        else:
            response = {"accepted": False, "error": f"Unhandled path: {path}"}

        accepted = bool(response.get("accepted"))
        return SimpleNamespace(
            success=accepted,
            body=response,
            status_code=200 if accepted else 400,
            error=response.get("error", ""),
            peer_id=peer_id,
        )


class StaticTransport:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def send(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


@pytest.fixture
def transport():
    return LoopbackTransport()


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
def protocol_a(identity_a, topology_a, transport):
    protocol = PeerRegistrationProtocol(
        identity=identity_a,
        topology=topology_a,
        transport=transport,
        self_address="http://a:8000",
    )
    transport.register("http://a:8000", protocol)
    return protocol


@pytest.fixture
def protocol_b(identity_b, topology_b, transport):
    protocol = PeerRegistrationProtocol(
        identity=identity_b,
        topology=topology_b,
        transport=transport,
        self_address="http://b:8000",
    )
    transport.register("http://b:8000", protocol)
    return protocol


class TestHandleRegistrationRequest:
    @pytest.mark.asyncio
    async def test_valid_registration(self, protocol_a, protocol_b, topology_a, topology_b, identity_a, identity_b):
        result = await protocol_a.initiate_registration("http://b:8000", target_role="peer")

        assert result.success
        assert result.mutual
        assert result.peer_instance_id == identity_b.instance_id

        peer_from_a = topology_a.get_peer(identity_b.instance_id)
        peer_from_b = topology_b.get_peer(identity_a.instance_id)
        assert peer_from_a is not None
        assert peer_from_a.address == "http://b:8000"
        assert peer_from_a.public_key_hex == identity_b.public_key_hex()
        assert peer_from_b is not None
        assert peer_from_b.address == "http://a:8000"
        assert peer_from_b.public_key_hex == identity_a.public_key_hex()

    @pytest.mark.asyncio
    async def test_missing_fields_rejected(self, protocol_b):
        response = await protocol_b.handle_registration_request({})
        assert not response.get("accepted", True)
        assert "Missing" in response.get("error", "")

    @pytest.mark.asyncio
    async def test_bad_signature_rejected(self, protocol_b, identity_a):
        request = {
            "registration_id": "reg-1",
            "instance_id": identity_a.instance_id,
            "public_key_hex": identity_a.public_key_hex(),
            "fingerprint": identity_a.fingerprint,
            "address": "http://a:8000",
            "challenge": "test",
            "challenge_signature": "deadbeef" * 8,
        }
        response = await protocol_b.handle_registration_request(request)
        assert not response.get("accepted", True)

    @pytest.mark.asyncio
    async def test_fingerprint_mismatch_rejected(self, protocol_b, identity_a):
        challenge = "abc123"
        request = {
            "registration_id": "reg-1",
            "instance_id": identity_a.instance_id,
            "public_key_hex": identity_a.public_key_hex(),
            "fingerprint": "mismatch",
            "address": "http://a:8000",
            "challenge": challenge,
            "challenge_signature": sign_payload(identity_a, challenge.encode("utf-8")).hex(),
        }
        response = await protocol_b.handle_registration_request(request)
        assert not response.get("accepted", True)
        assert "Fingerprint" in response.get("error", "")

    @pytest.mark.asyncio
    async def test_no_signature_rejected(self, protocol_b, identity_a):
        request = {
            "registration_id": "reg-1",
            "instance_id": identity_a.instance_id,
            "public_key_hex": identity_a.public_key_hex(),
            "fingerprint": identity_a.fingerprint,
            "address": "http://a:8000",
            "challenge": "test",
        }
        response = await protocol_b.handle_registration_request(request)
        assert not response.get("accepted", True)

    @pytest.mark.asyncio
    async def test_existing_peer_rekey_rejected(self, protocol_b, topology_b):
        trusted = generate_identity()
        attacker = generate_identity()
        topology_b.register_peer(
            instance_id="trusted-peer",
            fingerprint=trusted.fingerprint,
            public_key_hex=trusted.public_key_hex(),
            address="http://trusted:8000",
            role="peer",
        )
        challenge = "takeover"
        request = {
            "registration_id": "reg-1",
            "instance_id": "trusted-peer",
            "public_key_hex": attacker.public_key_hex(),
            "fingerprint": attacker.fingerprint,
            "address": "http://evil.example:8000",
            "challenge": challenge,
            "challenge_signature": sign_payload(attacker, challenge.encode("utf-8")).hex(),
        }

        response = await protocol_b.handle_registration_request(request)

        assert not response.get("accepted", True)
        assert "rekey" in response.get("error", "").lower()
        trusted_peer = topology_b.get_peer("trusted-peer")
        assert trusted_peer is not None
        assert trusted_peer.public_key_hex == trusted.public_key_hex()
        assert trusted_peer.address == "http://trusted:8000"


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
    @pytest.mark.asyncio
    async def test_initiate_registration_requires_self_address(self, identity_a, topology_a, transport):
        protocol = PeerRegistrationProtocol(
            identity=identity_a,
            topology=topology_a,
            transport=transport,
            self_address="",
        )

        result = await protocol.initiate_registration("http://b:8000")

        assert not result.success
        assert "self_address" in result.error

    def test_unknown_confirmation_rejected(self, protocol_a):
        result = protocol_a.handle_registration_confirm(
            {
                "registration_id": "missing",
                "instance_id": "peer-x",
                "public_key_hex": generate_identity().public_key_hex(),
                "challenge_response": "aa",
                "counter_challenge": "bb",
            }
        )
        assert not result["accepted"]
        assert "Unknown or expired" in result["error"]

    @pytest.mark.asyncio
    async def test_registration_notifies_mesh_callbacks(self, identity_a, topology_a, transport):
        events = {"added": [], "removed": []}
        protocol = PeerRegistrationProtocol(
            identity=identity_a,
            topology=topology_a,
            transport=transport,
            self_address="http://a:8000",
            on_peer_registered=lambda instance_id, address: events["added"].append((instance_id, address)),
            on_peer_removed=lambda instance_id: events["removed"].append(instance_id),
        )
        topology_a.register_peer(
            instance_id="peer-1",
            fingerprint="fp",
            public_key_hex=generate_identity().public_key_hex(),
            address="http://peer-1:8000",
            role="peer",
        )
        protocol._notify_peer_registered("peer-1", "http://peer-1:8000")
        protocol.handle_peer_removal("peer-1")

        assert events["added"] == [("peer-1", "http://peer-1:8000")]
        assert events["removed"] == ["peer-1"]

    def test_pending_registration_survives_restart(self, identity_a, topology_a, transport, tmp_path):
        path = tmp_path / "pending_registrations.json"
        protocol = PeerRegistrationProtocol(
            identity=identity_a,
            topology=topology_a,
            transport=transport,
            self_address="http://a:8000",
            persistence_path=str(path),
        )
        protocol._pending["r1"] = PendingRegistration(
            registration_id="r1",
            instance_id="peer-1",
            public_key_hex="aa",
            fingerprint="fp",
            address="http://peer-1:8000",
            role="peer",
            soul_version_hash="",
            challenge="c1",
        )
        protocol._persist_pending()

        reloaded = PeerRegistrationProtocol(
            identity=identity_a,
            topology=topology_a,
            transport=transport,
            self_address="http://a:8000",
            persistence_path=str(path),
        )
        assert "r1" in reloaded._pending

    def test_expired_pending_registration_not_reloaded(self, identity_a, topology_a, transport, tmp_path):
        path = tmp_path / "pending_registrations.json"
        protocol = PeerRegistrationProtocol(
            identity=identity_a,
            topology=topology_a,
            transport=transport,
            self_address="http://a:8000",
            persistence_path=str(path),
            pending_ttl_s=1.0,
        )
        protocol._pending["r1"] = PendingRegistration(
            registration_id="r1",
            instance_id="peer-1",
            public_key_hex="aa",
            fingerprint="fp",
            address="http://peer-1:8000",
            role="peer",
            soul_version_hash="",
            challenge="c1",
            created_at="2000-01-01T00:00:00+00:00",
        )
        protocol._persist_pending()

        reloaded = PeerRegistrationProtocol(
            identity=identity_a,
            topology=topology_a,
            transport=transport,
            self_address="http://a:8000",
            persistence_path=str(path),
            pending_ttl_s=1.0,
        )
        assert reloaded._pending == {}

    @pytest.mark.asyncio
    async def test_initiate_registration_rejects_bad_target_pending_limit_and_transport_failure(self, identity_a, topology_a):
        protocol = PeerRegistrationProtocol(
            identity=identity_a,
            topology=topology_a,
            transport=StaticTransport(SimpleNamespace(success=True, body={}, status_code=200, error="")),
            self_address="http://a:8000",
            max_pending=1,
        )

        assert "Target address" in (await protocol.initiate_registration("not-a-url")).error
        protocol._pending["busy"] = PendingRegistration("busy", "", "", "", "http://a:8000", "peer", "", "c")
        assert "Too many pending" in (await protocol.initiate_registration("http://b:8000")).error

        failing = PeerRegistrationProtocol(
            identity=identity_a,
            topology=topology_a,
            transport=StaticTransport(SimpleNamespace(success=False, body=None, status_code=503, error="down")),
            self_address="http://a:8000",
        )
        assert (await failing.initiate_registration("http://b:8000")).error == "down"
        assert failing._pending == {}

    @pytest.mark.asyncio
    async def test_initiate_registration_rejects_incomplete_invalid_and_unconfirmed_responses(self, identity_a, topology_a, monkeypatch):
        incomplete = PeerRegistrationProtocol(
            identity=identity_a,
            topology=topology_a,
            transport=StaticTransport(SimpleNamespace(success=True, body={"instance_id": "b"}, status_code=200, error="")),
            self_address="http://a:8000",
        )
        assert "Incomplete" in (await incomplete.initiate_registration("http://b:8000")).error

        invalid = PeerRegistrationProtocol(
            identity=identity_a,
            topology=topology_a,
            transport=StaticTransport(SimpleNamespace(
                success=True,
                body={"instance_id": "b", "public_key_hex": "not-hex", "challenge_response": "bad"},
                status_code=200,
                error="",
            )),
            self_address="http://a:8000",
        )
        assert "Invalid key" in (await invalid.initiate_registration("http://b:8000")).error

        target = generate_identity()
        monkeypatch.setattr(peer_protocol_module, "verify_signature", lambda *_: False)
        bad_sig = PeerRegistrationProtocol(
            identity=identity_a,
            topology=topology_a,
            transport=StaticTransport(SimpleNamespace(
                success=True,
                body={
                    "instance_id": target.instance_id,
                    "public_key_hex": target.public_key_hex(),
                    "challenge_response": "aa",
                    "fingerprint": target.fingerprint,
                },
                status_code=200,
                error="",
            )),
            self_address="http://a:8000",
        )
        assert "verification failed" in (await bad_sig.initiate_registration("http://b:8000")).error

    @pytest.mark.asyncio
    async def test_handle_registration_request_validation_and_confirmation_failures(self, protocol_b, identity_a):
        request = {
            "registration_id": "reg-1",
            "instance_id": identity_a.instance_id,
            "public_key_hex": identity_a.public_key_hex(),
            "fingerprint": identity_a.fingerprint,
            "address": "not-a-url",
            "challenge": "c",
            "challenge_signature": sign_payload(identity_a, b"c").hex(),
        }
        response = await protocol_b.handle_registration_request(request)
        assert "valid initiator address" in response["error"]

        protocol_b._pending = {
            str(i): PendingRegistration(str(i), "", "", "", "http://x", "peer", "", "c")
            for i in range(protocol_b._max_pending)
        }
        request["address"] = "http://a:8000"
        response = await protocol_b.handle_registration_request(request)
        assert "Too many pending" in response["error"]

    @pytest.mark.asyncio
    async def test_handle_registration_request_rejects_confirm_errors_and_bad_counter_signature(
        self,
        identity_b,
        topology_b,
        identity_a,
        monkeypatch,
    ):
        def request_for(challenge="c"):
            return {
                "registration_id": "reg-1",
                "instance_id": identity_a.instance_id,
                "public_key_hex": identity_a.public_key_hex(),
                "fingerprint": identity_a.fingerprint,
                "address": "http://a:8000",
                "challenge": challenge,
                "challenge_signature": sign_payload(identity_a, challenge.encode()).hex(),
            }

        confirm_down = PeerRegistrationProtocol(
            identity=identity_b,
            topology=topology_b,
            transport=StaticTransport(SimpleNamespace(success=False, body=None, status_code=500, error="confirm down")),
            self_address="http://b:8000",
        )
        assert (await confirm_down.handle_registration_request(request_for()))["error"] == "confirm down"

        confirm_reject = PeerRegistrationProtocol(
            identity=identity_b,
            topology=topology_b,
            transport=StaticTransport(SimpleNamespace(success=True, body={"accepted": False, "error": "rejected"}, status_code=400, error="")),
            self_address="http://b:8000",
        )
        assert (await confirm_reject.handle_registration_request(request_for()))["error"] == "rejected"

        invalid_counter = PeerRegistrationProtocol(
            identity=identity_b,
            topology=topology_b,
            transport=StaticTransport(SimpleNamespace(success=True, body={"accepted": True, "counter_challenge_response": "not-hex"}, status_code=200, error="")),
            self_address="http://b:8000",
        )
        assert "Invalid confirmation" in (await invalid_counter.handle_registration_request(request_for()))["error"]

        monkeypatch.setattr(peer_protocol_module, "verify_signature", lambda *args: True)
        topology_b.register_peer = lambda **kwargs: (_ for _ in ()).throw(ValueError("policy rejected"))
        register_reject = PeerRegistrationProtocol(
            identity=identity_b,
            topology=topology_b,
            transport=StaticTransport(SimpleNamespace(success=True, body={"accepted": True, "counter_challenge_response": "aa"}, status_code=200, error="")),
            self_address="http://b:8000",
        )
        assert "policy rejected" in (await register_reject.handle_registration_request(request_for()))["error"]

    def test_confirm_validation_register_failure_callbacks_and_persistence_edges(self, identity_a, topology_a, tmp_path):
        callbacks = {"added": 0, "removed": 0}
        protocol = PeerRegistrationProtocol(
            identity=identity_a,
            topology=topology_a,
            transport=StaticTransport(SimpleNamespace(success=True, body={}, status_code=200, error="")),
            self_address="http://a:8000",
            on_peer_registered=lambda *_: (_ for _ in ()).throw(RuntimeError("callback failed")),
            on_peer_removed=lambda *_: (_ for _ in ()).throw(RuntimeError("callback failed")),
            persistence_path=str(tmp_path / "pending.json"),
        )
        assert not protocol.handle_registration_confirm({})["accepted"]

        peer = generate_identity()
        protocol._pending["reg-1"] = PendingRegistration(
            "reg-1", "", "", "", "http://a:8000", "peer", "", "challenge", expected_target_address="http://b:8000"
        )
        mismatch = protocol.handle_registration_confirm({
            "registration_id": "reg-1",
            "instance_id": peer.instance_id,
            "public_key_hex": peer.public_key_hex(),
            "fingerprint": "wrong",
            "challenge_response": "aa",
            "counter_challenge": "bb",
        })
        assert "Fingerprint" in mismatch["error"]

        invalid = protocol.handle_registration_confirm({
            "registration_id": "reg-1",
            "instance_id": peer.instance_id,
            "public_key_hex": peer.public_key_hex(),
            "challenge_response": "bad",
            "counter_challenge": "bb",
        })
        assert "Invalid key" in invalid["error"]

        corrupt_path = tmp_path / "corrupt.json"
        corrupt_path.write_text("{bad json", encoding="utf-8")
        reloaded = PeerRegistrationProtocol(
            identity=identity_a,
            topology=topology_a,
            transport=StaticTransport(SimpleNamespace(success=True, body={}, status_code=200, error="")),
            self_address="http://a:8000",
            persistence_path=str(corrupt_path),
        )
        assert reloaded._pending == {}

        assert protocol._normalize_address("ftp://peer") == ""
        assert protocol._is_pending_expired(PendingRegistration("x", "", "", "", "", "", "", "", created_at="not-date")) is True
        protocol._pending_ttl_s = 0
        assert protocol._is_pending_expired(PendingRegistration("x", "", "", "", "", "", "", "", created_at="not-date")) is False
