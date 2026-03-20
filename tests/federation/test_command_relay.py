# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""Tests for Federation Command Relay — kill switch and pause propagation."""

import pytest

from src.federation.identity import generate_identity
from src.federation.topology import TopologyRegistry
from src.federation.command_relay import CommandRelay


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
                "command_type": "emergency_stop",
                "authority": "L1",
                "reason": "Safety violation",
            },
            "issuer_instance_id": root_identity.instance_id,
        })
        assert result["accepted"]
        assert result["command_id"] == "kill-001"

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


class TestHandlePause:
    def test_valid_pause(self, relay, topology, child_identity, root_identity):
        child_topo = TopologyRegistry(self_instance_id=child_identity.instance_id)
        child_topo.register_peer(instance_id=root_identity.instance_id, role="root")
        child_relay = CommandRelay(
            identity=child_identity, transport=None, topology=child_topo,
        )

        result = child_relay.handle_pause({
            "reason": "Soul propagation T2",
            "issuer_instance_id": root_identity.instance_id,
        })
        assert result["accepted"]

    def test_pause_from_unknown_rejected(self, relay):
        result = relay.handle_pause({
            "reason": "test",
            "issuer_instance_id": "unknown",
        })
        assert not result["accepted"]
