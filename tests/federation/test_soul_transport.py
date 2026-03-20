# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""Tests for Federation Soul Transport — soul push/pull handlers."""

import pytest

from src.federation.identity import generate_identity
from src.federation.topology import TopologyRegistry
from src.federation.soul_transport import SoulTransport


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
        address="http://child:8000",
        role="child",
    )
    return topo


@pytest.fixture
def transport_obj(root_identity, topology):
    return SoulTransport(
        identity=root_identity,
        transport=None,  # HTTP transport not needed for handler tests
        topology=topology,
    )


class TestHandleSoulPush:
    def test_valid_push_accepted(self, transport_obj, child_identity, topology):
        """Child receives soul push — should accept."""
        child_topo = TopologyRegistry(self_instance_id=child_identity.instance_id)
        child_topo.register_peer(
            instance_id=transport_obj._identity.instance_id, role="root",
        )
        child_transport = SoulTransport(
            identity=child_identity, transport=None, topology=child_topo,
        )

        result = child_transport.handle_soul_push({
            "source_instance_id": transport_obj._identity.instance_id,
            "soul_document": {"identity": {"name": "lancelot"}},
            "soul_hash": "abc123",
            "tier": "T1",
        })
        assert result["accepted"]
        assert result["soul_hash"] == "abc123"

    def test_push_from_unknown_rejected(self, transport_obj):
        result = transport_obj.handle_soul_push({
            "source_instance_id": "unknown-peer",
            "soul_document": {},
            "soul_hash": "hash",
            "tier": "T1",
        })
        assert not result["accepted"]
        assert "Unknown" in result["error"]


class TestHandleSoulFetch:
    def test_returns_instance_info(self, transport_obj, root_identity):
        result = transport_obj.handle_soul_fetch()
        assert result["instance_id"] == root_identity.instance_id


class TestResolveTargets:
    def test_all_peers(self, transport_obj, child_identity):
        targets = transport_obj._resolve_targets()
        assert len(targets) == 1
        assert targets[0].instance_id == child_identity.instance_id

    def test_specific_targets(self, transport_obj, child_identity):
        targets = transport_obj._resolve_targets([child_identity.instance_id])
        assert len(targets) == 1

    def test_unknown_target_filtered(self, transport_obj):
        targets = transport_obj._resolve_targets(["nonexistent"])
        assert len(targets) == 0
