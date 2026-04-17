# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""Tests for Federation Command Relay — kill switch and pause propagation."""

import pytest

from src.federation.identity import generate_identity
from src.federation.kill_switch import FederatedKillSwitch
from src.federation.topology import TopologyRegistry
from src.federation.command_relay import CommandRelay


class _BroadcastTransport:
    def __init__(self):
        self.calls = []

    async def broadcast(self, peers, **kwargs):
        self.calls.append(
            {
                "peers": peers,
                "path": kwargs.get("path"),
                "body": kwargs.get("body"),
            }
        )
        return {
            peer["instance_id"]: type(
                "Result",
                (),
                {"success": True, "error": "", "latency_ms": 1},
            )()
            for peer in peers
        }


class _SelectiveBroadcastTransport(_BroadcastTransport):
    def __init__(self, failures=None):
        super().__init__()
        self._failures = set(failures or [])

    async def broadcast(self, peers, **kwargs):
        self.calls.append(
            {
                "peers": peers,
                "path": kwargs.get("path"),
                "body": kwargs.get("body"),
            }
        )
        results = {}
        for peer in peers:
            failed = peer["instance_id"] in self._failures
            results[peer["instance_id"]] = type(
                "Result",
                (),
                {
                    "success": not failed,
                    "error": "delivery failed" if failed else "",
                    "latency_ms": 1,
                },
            )()
        return results


@pytest.fixture
def root_identity():
    return generate_identity()


@pytest.fixture
def child_identity():
    return generate_identity()


@pytest.fixture
def topology(root_identity, child_identity):
    topo = TopologyRegistry(self_instance_id=root_identity.instance_id)
    topo.register_peer(
        instance_id=child_identity.instance_id,
        fingerprint=child_identity.fingerprint,
        public_key_hex=child_identity.public_key_hex(),
        address="http://child:8000",
        role="child",
    )
    return topo


@pytest.fixture
def relay(root_identity, topology):
    return CommandRelay(
        identity=root_identity,
        transport=None,
        topology=topology,
    )


class TestHandleKillCommand:
    def test_valid_kill_from_root(self, relay, topology, child_identity, root_identity):
        """Child receives kill from root — should accept."""
        # Make a relay for the child side
        child_topo = TopologyRegistry(self_instance_id=child_identity.instance_id)
        child_topo.register_peer(
            instance_id=root_identity.instance_id,
            fingerprint=root_identity.fingerprint,
            public_key_hex=root_identity.public_key_hex(),
            address="http://root:8000",
            role="root",
        )
        kill_switch = FederatedKillSwitch(
            self_instance_id=child_identity.instance_id,
            local_kill_handler=lambda reason: 2,
        )
        child_relay = CommandRelay(
            identity=child_identity,
            transport=None,
            topology=child_topo,
            kill_switch=kill_switch,
        )

        result = child_relay.handle_kill_command({
            "command": {
                "command_id": "kill-001",
                "command_type": "federation_kill",
                "authority": "L1_federation_root",
                "reason": "Safety violation",
            },
            "issuer_instance_id": root_identity.instance_id,
        })
        assert result["accepted"]
        assert result["command_id"] == "kill-001"
        assert result["agents_killed"] == 2

    def test_kill_without_local_engine_rejected(self, child_identity, root_identity):
        child_topo = TopologyRegistry(self_instance_id=child_identity.instance_id)
        child_topo.register_peer(
            instance_id=root_identity.instance_id,
            fingerprint=root_identity.fingerprint,
            public_key_hex=root_identity.public_key_hex(),
            address="http://root:8000",
            role="root",
        )
        child_relay = CommandRelay(
            identity=child_identity,
            transport=None,
            topology=child_topo,
        )
        result = child_relay.handle_kill_command({
            "command": {
                "command_id": "kill-001",
                "command_type": "federation_kill",
                "authority": "L1_federation_root",
                "reason": "Safety violation",
            },
            "issuer_instance_id": root_identity.instance_id,
        })
        assert result["accepted"] is False
        assert "not configured" in result["error"].lower()

    def test_kill_from_unknown_peer_rejected(self, relay):
        result = relay.handle_kill_command({
            "command": {"command_id": "kill-bad"},
            "issuer_instance_id": "unknown-peer-id",
        })
        assert not result["accepted"]
        assert "Unknown" in result["error"]

    def test_kill_from_leaf_rejected(self, child_identity, root_identity):
        """Leaf peers cannot issue kill commands."""
        topo = TopologyRegistry(self_instance_id=root_identity.instance_id)
        topo.register_peer(instance_id=child_identity.instance_id, role="leaf")
        relay = CommandRelay(
            identity=root_identity, transport=None, topology=topo,
        )

        result = relay.handle_kill_command({
            "command": {"command_id": "kill-leaf"},
            "issuer_instance_id": child_identity.instance_id,
        })
        assert not result["accepted"]
        assert "authority" in result["error"].lower()

    def test_kill_from_peer_role_rejected(self, child_identity, root_identity):
        topo = TopologyRegistry(self_instance_id=root_identity.instance_id)
        topo.register_peer(instance_id=child_identity.instance_id, role="peer")
        relay = CommandRelay(
            identity=root_identity, transport=None, topology=topo,
        )

        result = relay.handle_kill_command({
            "command": {
                "command_id": "kill-peer",
                "command_type": "federation_kill",
                "authority": "L1_federation_root",
                "reason": "test",
            },
            "issuer_instance_id": child_identity.instance_id,
        })

        assert result["accepted"] is False
        assert "authority" in result["error"].lower()


class TestHandlePause:
    def test_valid_pause(self, relay, topology, child_identity, root_identity):
        child_topo = TopologyRegistry(self_instance_id=child_identity.instance_id)
        child_topo.register_peer(
            instance_id=root_identity.instance_id,
            fingerprint=root_identity.fingerprint,
            public_key_hex=root_identity.public_key_hex(),
            address="http://root:8000",
            role="root",
        )
        child_relay = CommandRelay(
            identity=child_identity,
            transport=None,
            topology=child_topo,
            local_pause_handler=lambda reason: {
                "paused_agents": 2,
                "already_paused_agents": 1,
                "execution_state": "paused",
            },
        )

        result = child_relay.handle_pause({
            "reason": "Soul propagation T2",
            "issuer_instance_id": root_identity.instance_id,
        })
        assert result["accepted"]
        assert result["paused_agents"] == 2
        assert result["already_paused_agents"] == 1
        assert result["execution_state"] == "paused"

    def test_pause_from_unknown_rejected(self, relay):
        result = relay.handle_pause({
            "reason": "test",
            "issuer_instance_id": "unknown",
        })
        assert not result["accepted"]

    def test_pause_rejects_body_issuer_spoof(self, child_identity, root_identity):
        child_topo = TopologyRegistry(self_instance_id=child_identity.instance_id)
        child_topo.register_peer(
            instance_id=root_identity.instance_id,
            fingerprint=root_identity.fingerprint,
            public_key_hex=root_identity.public_key_hex(),
            address="http://root:8000",
            role="root",
        )
        child_relay = CommandRelay(
            identity=child_identity,
            transport=None,
            topology=child_topo,
            local_pause_handler=lambda reason: {"paused_agents": 0},
        )

        result = child_relay.handle_pause(
            {
                "reason": "Soul propagation T2",
                "issuer_instance_id": "spoofed-peer",
            },
            authenticated_instance_id=root_identity.instance_id,
        )

        assert result["accepted"] is False
        assert "does not match authenticated peer" in result["error"]

    def test_pause_without_local_engine_rejected(self, child_identity, root_identity):
        child_topo = TopologyRegistry(self_instance_id=child_identity.instance_id)
        child_topo.register_peer(
            instance_id=root_identity.instance_id,
            fingerprint=root_identity.fingerprint,
            public_key_hex=root_identity.public_key_hex(),
            address="http://root:8000",
            role="root",
        )
        child_relay = CommandRelay(
            identity=child_identity,
            transport=None,
            topology=child_topo,
        )

        result = child_relay.handle_pause({
            "reason": "Soul propagation T2",
            "issuer_instance_id": root_identity.instance_id,
        })

        assert result["accepted"] is False
        assert "not configured" in result["error"].lower()

    def test_pause_from_non_root_peer_rejected(self, child_identity, root_identity):
        child_topo = TopologyRegistry(self_instance_id=child_identity.instance_id)
        child_topo.register_peer(
            instance_id=root_identity.instance_id,
            fingerprint=root_identity.fingerprint,
            public_key_hex=root_identity.public_key_hex(),
            address="http://root:8000",
            role="peer",
        )
        child_relay = CommandRelay(
            identity=child_identity,
            transport=None,
            topology=child_topo,
            local_pause_handler=lambda reason, full_stop=False: {"paused_agents": 1},
        )

        result = child_relay.handle_pause({
            "reason": "Soul propagation T3",
            "issuer_instance_id": root_identity.instance_id,
            "full_stop": True,
        })

        assert result["accepted"] is False
        assert "authority" in result["error"].lower()


class TestHandleResume:
    def test_valid_resume(self, child_identity, root_identity):
        child_topo = TopologyRegistry(self_instance_id=child_identity.instance_id)
        child_topo.register_peer(
            instance_id=root_identity.instance_id,
            fingerprint=root_identity.fingerprint,
            public_key_hex=root_identity.public_key_hex(),
            address="http://root:8000",
            role="root",
        )
        child_relay = CommandRelay(
            identity=child_identity,
            transport=None,
            topology=child_topo,
            local_resume_handler=lambda reason: {
                "resumed_agents": 2,
                "execution_state": "running",
            },
        )

        result = child_relay.handle_resume({
            "reason": "Soul propagation complete",
            "issuer_instance_id": root_identity.instance_id,
        })

        assert result["accepted"]
        assert result["resumed_agents"] == 2
        assert result["execution_state"] == "running"

    def test_resume_without_local_engine_rejected(self, child_identity, root_identity):
        child_topo = TopologyRegistry(self_instance_id=child_identity.instance_id)
        child_topo.register_peer(
            instance_id=root_identity.instance_id,
            fingerprint=root_identity.fingerprint,
            public_key_hex=root_identity.public_key_hex(),
            address="http://root:8000",
            role="root",
        )
        child_relay = CommandRelay(
            identity=child_identity,
            transport=None,
            topology=child_topo,
        )

        result = child_relay.handle_resume({
            "reason": "Soul propagation complete",
            "issuer_instance_id": root_identity.instance_id,
        })

        assert result["accepted"] is False
        assert "not configured" in result["error"].lower()

    def test_resume_from_non_root_peer_rejected(self, child_identity, root_identity):
        child_topo = TopologyRegistry(self_instance_id=child_identity.instance_id)
        child_topo.register_peer(
            instance_id=root_identity.instance_id,
            fingerprint=root_identity.fingerprint,
            public_key_hex=root_identity.public_key_hex(),
            address="http://root:8000",
            role="peer",
        )
        child_relay = CommandRelay(
            identity=child_identity,
            transport=None,
            topology=child_topo,
            local_resume_handler=lambda reason: {"resumed_agents": 1},
        )

        result = child_relay.handle_resume({
            "reason": "Soul propagation complete",
            "issuer_instance_id": root_identity.instance_id,
        })

        assert result["accepted"] is False
        assert "authority" in result["error"].lower()


class TestOperatorKillPath:
    @pytest.mark.asyncio
    async def test_issue_and_propagate_kill_uses_persisted_kill_engine(self, root_identity, topology):
        transport = _SelectiveBroadcastTransport(failures=set())
        engine = FederatedKillSwitch(
            self_instance_id=root_identity.instance_id,
            peer_ids=[p.instance_id for p in topology.list_peers()],
            local_kill_handler=lambda reason: 3,
        )
        relay = CommandRelay(
            identity=root_identity,
            transport=transport,
            topology=topology,
            kill_switch=engine,
        )

        outcome = await relay.issue_and_propagate_kill(
            {
                "command_id": "cmd-operator-1",
                "command_type": "federation_kill",
                "authority": "L1_federation_root",
                "issuer_instance_id": root_identity.instance_id,
                "reason": "operator stop",
            }
        )

        cmd = engine.get_command("cmd-operator-1")
        assert outcome["local_agents_killed"] == 3
        assert outcome["results"] == {topology.list_peers()[0].instance_id: True}
        assert cmd is not None
        assert cmd.state.value == "completed"
        assert transport.calls[0]["path"] == "/api/federation/killswitch"

    @pytest.mark.asyncio
    async def test_issue_and_propagate_kill_records_remote_rejection(self, root_identity, topology):
        peer_id = topology.list_peers()[0].instance_id
        transport = _SelectiveBroadcastTransport(failures={peer_id})
        engine = FederatedKillSwitch(
            self_instance_id=root_identity.instance_id,
            peer_ids=[peer_id],
            local_kill_handler=lambda reason: 1,
        )
        relay = CommandRelay(
            identity=root_identity,
            transport=transport,
            topology=topology,
            kill_switch=engine,
        )

        outcome = await relay.issue_and_propagate_kill(
            {
                "command_id": "cmd-operator-2",
                "command_type": "federation_kill",
                "authority": "L1_federation_root",
                "issuer_instance_id": root_identity.instance_id,
                "reason": "operator stop",
            }
        )

        cmd = engine.get_command("cmd-operator-2")
        target = next(t for t in cmd.targets if t.instance_id == peer_id)
        assert outcome["results"][peer_id] is False
        assert cmd.state.value == "partial"
        assert target.ack_state.value == "rejected"


class TestPropagation:
    @pytest.mark.asyncio
    async def test_propagate_pause_includes_full_stop_flag(self, root_identity, topology):
        transport = _BroadcastTransport()
        relay = CommandRelay(
            identity=root_identity,
            transport=transport,
            topology=topology,
        )

        result = await relay.propagate_pause("T3 rollout", full_stop=True)

        assert len(result) == 1
        assert all(result.values())
        assert transport.calls[0]["path"] == "/api/federation/pause"
        assert transport.calls[0]["body"]["full_stop"] is True

    @pytest.mark.asyncio
    async def test_propagate_resume_payload_has_no_undefined_fields(self, root_identity, topology):
        transport = _BroadcastTransport()
        relay = CommandRelay(
            identity=root_identity,
            transport=transport,
            topology=topology,
        )

        result = await relay.propagate_resume("Resume after rollout")

        assert transport.calls[0]["path"] == "/api/federation/resume"
        assert transport.calls[0]["body"] == {
            "reason": "Resume after rollout",
            "issuer_instance_id": root_identity.instance_id,
        }
        assert all(result.values())
