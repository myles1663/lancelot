# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""Tests for Federation API endpoints."""

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

from src.federation.api import (
    router,
    init_federation_api,
    shutdown_federation_api,
)
from src.federation.heartbeat import HeartbeatEmitter
from src.federation.identity import generate_identity


@pytest.fixture
def app():
    """Create a test FastAPI app with federation router."""
    test_app = FastAPI()
    test_app.include_router(router)
    return test_app


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def identity():
    return generate_identity()


@pytest.fixture
def emitter(identity):
    return HeartbeatEmitter(instance_id=identity.instance_id)


@pytest.fixture
def config():
    from src.federation.config import FederationConfig
    return FederationConfig()


@pytest.fixture(autouse=True)
def cleanup():
    """Ensure federation API is shut down after each test."""
    yield
    shutdown_federation_api()


class TestNotInitialized:
    """All endpoints return 503 when federation is not initialized."""

    def test_heartbeat_503(self, client):
        resp = client.get("/api/federation/heartbeat")
        assert resp.status_code == 503

    def test_identity_503(self, client):
        resp = client.get("/api/federation/identity")
        assert resp.status_code == 503

    def test_status_503(self, client):
        resp = client.get("/api/federation/status")
        assert resp.status_code == 503

    def test_topology_503(self, client):
        resp = client.get("/api/federation/topology")
        assert resp.status_code == 503

    def test_soul_hash_503(self, client):
        resp = client.get("/api/federation/soul/hash")
        assert resp.status_code == 503


class TestTransportNotWired:
    """Endpoints return 503 when API is initialized but transport layer is not wired."""

    def test_command_503(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)
        resp = client.post("/api/federation/command", json={})
        assert resp.status_code == 503
        assert "transport" in resp.json()["error"].lower()

    def test_killswitch_503(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)
        resp = client.post("/api/federation/killswitch", json={})
        assert resp.status_code == 503

    def test_pause_503(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)
        resp = client.post("/api/federation/pause", json={})
        assert resp.status_code == 503

    def test_handoff_initiate_503(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)
        resp = client.post("/api/federation/handoff/initiate", json={})
        assert resp.status_code == 503

    def test_peer_register_503(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)
        resp = client.post("/api/federation/peer/register", json={})
        assert resp.status_code == 503

    def test_budget_503(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)
        resp = client.get("/api/federation/budget")
        assert resp.status_code == 503


class TestDiscoveryEndpoints:
    """Live discovery endpoints after initialization."""

    def test_identity_returns_public_info(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)
        resp = client.get("/api/federation/identity")
        assert resp.status_code == 200
        data = resp.json()
        assert data["instance_id"] == identity.instance_id
        assert data["fingerprint"] == identity.fingerprint
        assert "public_key" in data

    def test_status_returns_summary(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)
        resp = client.get("/api/federation/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["instance_id"] == identity.instance_id
        assert data["peer_count"] == 0
        assert data["heartbeat_interval_s"] == 2.0

    def test_topology_standalone(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)
        resp = client.get("/api/federation/topology")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deployment_mode"] == "standalone"
        assert data["peer_count"] == 0
        assert data["peers"] == []

    def test_soul_hash(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)
        # Emit a heartbeat so there's data
        emitter.emit_once()
        resp = client.get("/api/federation/soul/hash")
        assert resp.status_code == 200
        data = resp.json()
        assert data["instance_id"] == identity.instance_id


class TestHeartbeatEndpoint:
    """Test the heartbeat snapshot endpoint."""

    def test_no_heartbeats_yet(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)
        resp = client.get("/api/federation/heartbeat")
        assert resp.status_code == 200
        data = resp.json()
        assert data["heartbeat"] is None

    def test_after_emit(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)
        emitter.emit_once()
        resp = client.get("/api/federation/heartbeat")
        assert resp.status_code == 200
        data = resp.json()
        assert data["heartbeat"]["instance_id"] == identity.instance_id


class TestShutdown:
    """Test that shutdown resets API state."""

    def test_shutdown_returns_503(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)
        # Verify it works
        resp = client.get("/api/federation/identity")
        assert resp.status_code == 200
        # Shut down
        shutdown_federation_api()
        # Should be 503 again
        resp = client.get("/api/federation/identity")
        assert resp.status_code == 503
