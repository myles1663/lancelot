# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""Tests for Graph Builder topology persistence."""

import os
import tempfile
import pytest
from src.federation.graph_models import (
    GraphEdge,
    GraphNode,
    InstanceRole,
    TopologyDocument,
)
from src.federation.graph_persistence import TopologyStore


@pytest.fixture
def store():
    tmpdir = tempfile.mkdtemp()
    return TopologyStore(data_dir=tmpdir)


def _topology(name="Test Topo", nodes=None, edges=None):
    return TopologyDocument(
        topology_id="topo-1",
        topology_name=name,
        nodes=nodes or [],
        edges=edges or [],
    )


def _node(node_id="node-a"):
    return GraphNode(
        node_id=node_id,
        instance_name=f"Instance {node_id}",
        federation_identity_public_key=f"pk-{node_id}",
        endpoint=f"https://{node_id}.example.com",
    )


class TestSaveLoad:
    def test_save_and_load(self, store):
        topo = _topology(nodes=[_node()])
        store.save(topo)
        loaded = store.load()
        assert loaded is not None
        assert loaded.topology_id == "topo-1"
        assert len(loaded.nodes) == 1

    def test_load_empty(self, store):
        assert store.load() is None

    def test_save_updates_hash(self, store):
        topo = _topology()
        saved = store.save(topo)
        assert saved.version_hash != ""

    def test_save_updates_timestamp(self, store):
        topo = _topology()
        original = topo.updated_at
        saved = store.save(topo)
        assert saved.updated_at >= original

    def test_overwrite_active(self, store):
        store.save(_topology(name="First"))
        store.save(_topology(name="Second"))
        loaded = store.load()
        assert loaded.topology_name == "Second"


class TestVersioning:
    def test_save_version(self, store):
        topo = _topology()
        version = store.save_version(topo)
        assert version == 1

    def test_load_version(self, store):
        topo = _topology(nodes=[_node()])
        store.save_version(topo)
        loaded = store.load_version(1)
        assert loaded is not None
        assert len(loaded.nodes) == 1

    def test_load_nonexistent_version(self, store):
        assert store.load_version(999) is None

    def test_list_versions(self, store):
        topo1 = _topology(name="alpha")
        store.save_version(topo1)

        topo2 = _topology(name="beta")
        topo2.version = 2
        store.save_version(topo2)

        versions = store.list_versions()
        assert len(versions) == 2
        assert versions[0]["version"] == 1
        assert versions[1]["version"] == 2

    def test_list_versions_empty(self, store):
        assert store.list_versions() == []


class TestDeployment:
    def test_save_deployed(self, store):
        topo = _topology(nodes=[_node()])
        deployed = store.save_deployed(topo)
        assert deployed.deployed_at is not None

    def test_load_deployed(self, store):
        store.save_deployed(_topology())
        loaded = store.load_deployed()
        assert loaded is not None
        assert loaded.deployed_at is not None

    def test_load_deployed_empty(self, store):
        assert store.load_deployed() is None

    def test_deploy_also_saves_version(self, store):
        topo = _topology()
        store.save_deployed(topo)
        loaded_version = store.load_version(1)
        assert loaded_version is not None


class TestDelete:
    def test_delete_active(self, store):
        store.save(_topology())
        assert store.delete_active()
        assert store.load() is None

    def test_delete_nonexistent(self, store):
        assert not store.delete_active()
