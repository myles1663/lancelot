# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""Tests for Graph Builder API endpoints."""

import tempfile
import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

from src.core import api_auth, auth_api
from src.core.operator_identity import OperatorIdentity
from src.federation.graph_api import graph_router, init_graph_api


@pytest.fixture
def client():
    tmpdir = tempfile.mkdtemp()
    api_auth.init_api_auth(lambda request: True)
    auth_api._sessions.clear()
    auth_api._sessions["graph-test-session"] = {
        "expires_at": 9999999999,
        "username": "Arthur",
        "operator_identity": OperatorIdentity(
            operator_id="op-arthur",
            display_name="Arthur",
            session_id="session-1",
            session_started_at="2026-04-10T00:00:00Z",
            auth_method="local",
            ip_address="127.0.0.1",
        ),
        "capabilities": sorted({"warroom.login", "federation.admin"}),
        "groups": [],
    }
    app = FastAPI()
    app.include_router(graph_router)
    init_graph_api(tmpdir)
    client = TestClient(app)
    client.cookies.set(auth_api.get_warroom_session_cookie_name(), "graph-test-session")
    return client


@pytest.fixture
def client_with_topology(client):
    """Client with an active topology containing 2 nodes."""
    client.post("/api/federation/graph/topologies", json={
        "topology_name": "Test Topo",
        "created_by": "test",
    })
    client.post("/api/federation/graph/nodes", json={
        "node_id": "local-node",
        "instance_name": "Local",
        "is_local": True,
        "federation_identity_public_key": "pk-local",
    })
    client.post("/api/federation/graph/nodes", json={
        "node_id": "remote-node",
        "instance_name": "Remote",
        "endpoint": "https://remote.example.com",
        "federation_identity_public_key": "pk-remote",
    })
    return client


class TestTopologyCRUD:
    def test_create_topology(self, client):
        resp = client.post("/api/federation/graph/topologies", json={
            "topology_name": "My Topo",
        })
        assert resp.status_code == 200
        assert resp.json()["version"] == 1

    def test_get_active(self, client):
        client.post("/api/federation/graph/topologies", json={
            "topology_name": "My Topo",
        })
        resp = client.get("/api/federation/graph/topologies/active")
        assert resp.status_code == 200
        assert resp.json()["topology_name"] == "My Topo"

    def test_get_active_404(self, client):
        resp = client.get("/api/federation/graph/topologies/active")
        assert resp.status_code == 404

    def test_delete_active(self, client):
        client.post("/api/federation/graph/topologies", json={})
        resp = client.delete("/api/federation/graph/topologies/active")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_delete_active_404(self, client):
        resp = client.delete("/api/federation/graph/topologies/active")
        assert resp.status_code == 404


class TestNodeOperations:
    def test_add_node(self, client):
        client.post("/api/federation/graph/topologies", json={})
        resp = client.post("/api/federation/graph/nodes", json={
            "node_id": "node-a",
            "instance_name": "Node A",
        })
        assert resp.status_code == 200
        assert resp.json()["node_count"] == 1

    def test_add_duplicate_node(self, client):
        client.post("/api/federation/graph/topologies", json={})
        client.post("/api/federation/graph/nodes", json={"node_id": "node-a"})
        resp = client.post("/api/federation/graph/nodes", json={"node_id": "node-a"})
        assert resp.status_code == 409

    def test_remove_node(self, client_with_topology):
        resp = client_with_topology.delete("/api/federation/graph/nodes/local-node")
        assert resp.status_code == 200
        assert resp.json()["removed_node"] == "local-node"

    def test_remove_node_404(self, client_with_topology):
        resp = client_with_topology.delete("/api/federation/graph/nodes/nonexistent")
        assert resp.status_code == 404

    def test_remove_node_cascades_edges(self, client_with_topology):
        client_with_topology.post("/api/federation/graph/edges", json={
            "source_node_id": "local-node",
            "target_node_id": "remote-node",
        })
        resp = client_with_topology.delete("/api/federation/graph/nodes/local-node")
        assert resp.status_code == 200
        assert len(resp.json()["removed_edges"]) == 1

    def test_update_node(self, client_with_topology):
        resp = client_with_topology.put(
            "/api/federation/graph/nodes/local-node",
            json={"instance_name": "Updated Name"},
        )
        assert resp.status_code == 200


class TestEdgeOperations:
    def test_add_edge(self, client_with_topology):
        resp = client_with_topology.post("/api/federation/graph/edges", json={
            "source_node_id": "local-node",
            "target_node_id": "remote-node",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "edge_id" in data
        assert data["compatibility_state"] in ["green", "yellow", "red", "unknown"]

    def test_add_edge_missing_source(self, client_with_topology):
        resp = client_with_topology.post("/api/federation/graph/edges", json={
            "source_node_id": "nonexistent",
            "target_node_id": "remote-node",
        })
        assert resp.status_code == 404

    def test_add_edge_self_loop(self, client_with_topology):
        resp = client_with_topology.post("/api/federation/graph/edges", json={
            "source_node_id": "local-node",
            "target_node_id": "local-node",
        })
        assert resp.status_code == 400

    def test_remove_edge(self, client_with_topology):
        resp = client_with_topology.post("/api/federation/graph/edges", json={
            "source_node_id": "local-node",
            "target_node_id": "remote-node",
        })
        edge_id = resp.json()["edge_id"]
        resp = client_with_topology.delete(f"/api/federation/graph/edges/{edge_id}")
        assert resp.status_code == 200

    def test_remove_edge_404(self, client_with_topology):
        resp = client_with_topology.delete("/api/federation/graph/edges/nonexistent")
        assert resp.status_code == 404


class TestValidation:
    def test_validate_topology(self, client_with_topology):
        client_with_topology.post("/api/federation/graph/edges", json={
            "source_node_id": "local-node",
            "target_node_id": "remote-node",
        })
        resp = client_with_topology.post("/api/federation/graph/validate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["edge_count"] == 1

    def test_validate_single_edge(self, client_with_topology):
        resp = client_with_topology.post("/api/federation/graph/edges", json={
            "source_node_id": "local-node",
            "target_node_id": "remote-node",
        })
        edge_id = resp.json()["edge_id"]
        resp = client_with_topology.post(
            f"/api/federation/graph/validate/edge/{edge_id}"
        )
        assert resp.status_code == 200
        assert len(resp.json()["dimensions"]) == 4

    def test_validate_edge_404(self, client_with_topology):
        resp = client_with_topology.post(
            "/api/federation/graph/validate/edge/nonexistent"
        )
        assert resp.status_code == 404

    def test_acknowledge_yellow(self, client_with_topology):
        resp = client_with_topology.post("/api/federation/graph/edges", json={
            "source_node_id": "local-node",
            "target_node_id": "remote-node",
        })
        edge_id = resp.json()["edge_id"]
        resp = client_with_topology.post(
            f"/api/federation/graph/edges/{edge_id}/acknowledge",
            json={"operator": "admin", "note": "reviewed"},
        )
        assert resp.status_code == 200
        assert resp.json()["acknowledged"] is True


class TestDeploymentGate:
    def test_deployment_gate_standalone(self, client):
        client.post("/api/federation/graph/topologies", json={})
        resp = client.post("/api/federation/graph/deployment-gate")
        assert resp.status_code == 200
        assert resp.json()["deployable"] is True

    def test_deployment_gate_green(self, client_with_topology):
        resp = client_with_topology.post("/api/federation/graph/edges", json={
            "source_node_id": "local-node",
            "target_node_id": "remote-node",
        })
        edge_id = resp.json()["edge_id"]
        # If edge is YELLOW, acknowledge it
        state = resp.json()["compatibility_state"]
        if state == "yellow":
            client_with_topology.post(
                f"/api/federation/graph/edges/{edge_id}/acknowledge",
                json={"operator": "admin", "note": "approved"},
            )
        resp = client_with_topology.post("/api/federation/graph/deployment-gate")
        assert resp.status_code == 200
        assert resp.json()["deployable"] is True

    def test_deploy_success(self, client_with_topology):
        resp = client_with_topology.post("/api/federation/graph/edges", json={
            "source_node_id": "local-node",
            "target_node_id": "remote-node",
        })
        edge_id = resp.json()["edge_id"]
        state = resp.json()["compatibility_state"]
        if state == "yellow":
            client_with_topology.post(
                f"/api/federation/graph/edges/{edge_id}/acknowledge",
                json={"operator": "admin", "note": "approved"},
            )
        resp = client_with_topology.post("/api/federation/graph/deploy")
        assert resp.status_code == 200
        assert resp.json()["deployed"] is True
        assert resp.json()["version"] == 2


class TestVersioning:
    def test_save_version(self, client_with_topology):
        resp = client_with_topology.post(
            "/api/federation/graph/topologies/active/save-version"
        )
        assert resp.status_code == 200
        assert resp.json()["version"] == 1

    def test_list_versions(self, client_with_topology):
        client_with_topology.post(
            "/api/federation/graph/topologies/active/save-version"
        )
        resp = client_with_topology.get("/api/federation/graph/topologies/versions")
        assert resp.status_code == 200
        assert len(resp.json()["versions"]) == 1

    def test_get_version(self, client_with_topology):
        client_with_topology.post(
            "/api/federation/graph/topologies/active/save-version"
        )
        resp = client_with_topology.get("/api/federation/graph/topologies/versions/1")
        assert resp.status_code == 200

    def test_get_version_404(self, client_with_topology):
        resp = client_with_topology.get("/api/federation/graph/topologies/versions/999")
        assert resp.status_code == 404


class TestHandoffContract:
    def test_update_contract(self, client_with_topology):
        resp = client_with_topology.post("/api/federation/graph/edges", json={
            "source_node_id": "local-node",
            "target_node_id": "remote-node",
        })
        edge_id = resp.json()["edge_id"]
        resp = client_with_topology.put(
            f"/api/federation/graph/edges/{edge_id}/contract",
            json={
                "success_criteria": ["task completed", "data validated"],
                "data_payload_schema": {"type": "object"},
            },
        )
        assert resp.status_code == 200
        assert resp.json()["updated"] is True
