# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""Tests for Federation API endpoints."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

from src.core import api_auth, auth_api
from src.core.operator_identity import OperatorIdentity
import src.federation.api as federation_api
from src.federation.api import (
    router,
    init_federation_api,
    init_federation_transport,
    shutdown_federation_api,
)
from src.federation.heartbeat import HeartbeatEmitter
from src.federation.identity import generate_identity


@pytest.fixture
def app():
    """Create a test FastAPI app with federation router."""
    api_auth.init_api_auth(lambda request: True)
    auth_api._sessions.clear()
    auth_api._sessions["federation-test-session"] = {
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
        "capabilities": sorted({"warroom.login", "federation.admin", "governance.admin"}),
        "groups": [],
    }
    test_app = FastAPI()
    test_app.include_router(router)
    return test_app


@pytest.fixture
def client(app):
    client = TestClient(app)
    client.cookies.set(auth_api.get_warroom_session_cookie_name(), "federation-test-session")
    return client


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

    def test_resume_503(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)
        resp = client.post("/api/federation/resume", json={})
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

    def test_peer_register_not_blocked_by_warroom_auth(self, app, identity, emitter, config):
        api_auth.init_api_auth(lambda request: False)
        client = TestClient(app)
        init_federation_api(identity, emitter, config)
        async def handle_registration_request(body):
            return {"accepted": False, "error": "Missing"}

        init_federation_transport(
            peer_protocol=SimpleNamespace(
                handle_registration_request=handle_registration_request,
                handle_registration_confirm=lambda body: {"accepted": False, "error": "Missing"},
            )
        )

        resp = client.post("/api/federation/peer/register", json={})

        assert resp.status_code == 400
        assert "Missing" in resp.json()["error"]

    def test_peer_confirm_not_blocked_by_warroom_auth(self, app, identity, emitter, config):
        api_auth.init_api_auth(lambda request: False)
        client = TestClient(app)
        init_federation_api(identity, emitter, config)

        async def handle_registration_request(body):
            return {"accepted": False, "error": "Missing"}

        init_federation_transport(
            peer_protocol=SimpleNamespace(
                handle_registration_request=handle_registration_request,
                handle_registration_confirm=lambda body: {"accepted": False, "error": "Missing"},
            )
        )

        resp = client.post("/api/federation/peer/confirm", json={})

        assert resp.status_code == 400
        assert "Missing" in resp.json()["error"]

    def test_command_requires_federation_signature_not_warroom_auth(self, app, identity, emitter, config):
        api_auth.init_api_auth(lambda request: False)
        client = TestClient(app)
        init_federation_api(identity, emitter, config)

        class FakeAuth:
            def verify_request(self, method, path, body, headers):
                return SimpleNamespace(valid=False, reason="Missing headers", instance_id="")

        init_federation_transport(
            command_relay=SimpleNamespace(handle_kill_command=lambda body: {"accepted": True}),
            auth=FakeAuth(),
        )

        resp = client.post("/api/federation/command", json={})

        assert resp.status_code == 401
        assert "Federation authentication failed" in resp.json()["detail"]


class TestCommandEndpoints:
    def test_pause_rejects_when_local_pause_fails(self, app, identity, emitter, config):
        api_auth.init_api_auth(lambda request: False)
        client = TestClient(app)
        init_federation_api(identity, emitter, config)

        class FakeAuth:
            def verify_request(self, method, path, body, headers):
                return SimpleNamespace(valid=True, reason="", instance_id="peer-1")

        init_federation_transport(
            command_relay=SimpleNamespace(
                handle_pause=lambda body, authenticated_instance_id="": {
                    "accepted": False,
                    "error": "Local pause engine not configured",
                }
            ),
            auth=FakeAuth(),
        )

        resp = client.post("/api/federation/pause", json={"reason": "Soul propagation T2"})

        assert resp.status_code == 403
        assert "pause engine" in resp.json()["error"].lower()

    def test_resume_rejects_when_local_resume_fails(self, app, identity, emitter, config):
        api_auth.init_api_auth(lambda request: False)
        client = TestClient(app)
        init_federation_api(identity, emitter, config)

        class FakeAuth:
            def verify_request(self, method, path, body, headers):
                return SimpleNamespace(valid=True, reason="", instance_id="peer-1")

        init_federation_transport(
            command_relay=SimpleNamespace(
                handle_resume=lambda body, authenticated_instance_id="": {
                    "accepted": False,
                    "error": "Local resume engine not configured",
                }
            ),
            auth=FakeAuth(),
        )

        resp = client.post("/api/federation/resume", json={"reason": "Soul propagation complete"})

        assert resp.status_code == 403
        assert "resume engine" in resp.json()["error"].lower()

    def test_budget_report_binds_authenticated_peer_identity(self, app, identity, emitter, config):
        api_auth.init_api_auth(lambda request: False)
        client = TestClient(app)
        init_federation_api(identity, emitter, config)

        class FakeAuth:
            def verify_request(self, method, path, body, headers):
                return SimpleNamespace(valid=True, reason="", instance_id="peer-1")

        init_federation_transport(
            cost_reporter=SimpleNamespace(
                handle_cost_report=lambda body, authenticated_instance_id="": {
                    "accepted": True,
                    "instance_id": authenticated_instance_id,
                    "body_instance_id": body.get("instance_id"),
                }
            ),
            auth=FakeAuth(),
        )

        resp = client.post("/api/federation/budget/report", json={"instance_id": "spoofed-peer"})

        assert resp.status_code == 200
        assert resp.json()["instance_id"] == "peer-1"
        assert resp.json()["body_instance_id"] == "spoofed-peer"

    def test_manage_kill_uses_issue_and_propagate_path(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)

        class SpyRelay:
            async def issue_and_propagate_kill(self, command_data):
                return {
                    "command_id": command_data["command_id"],
                    "local_agents_killed": 2,
                    "results": {"peer-1": True},
                    "total": 1,
                    "timed_out": [],
                    "command": {"command_id": command_data["command_id"], "state": "completed"},
                }

        init_federation_transport(command_relay=SpyRelay())

        resp = client.post(
            "/api/federation/manage/kill",
            json={
                "command": {
                    "command_id": "cmd-1",
                    "command_type": "federation_kill",
                    "authority": "L1_federation_root",
                    "reason": "operator test",
                }
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["command_id"] == "cmd-1"
        assert data["local_agents_killed"] == 2
        assert data["command"]["state"] == "completed"

    def test_command_rejects_unexpected_fields(self, app, identity, emitter, config):
        api_auth.init_api_auth(lambda request: False)
        client = TestClient(app)
        init_federation_api(identity, emitter, config)
        called = []

        class FakeAuth:
            def verify_request(self, method, path, body, headers):
                return SimpleNamespace(valid=True, reason="", instance_id="peer-1")

        init_federation_transport(
            command_relay=SimpleNamespace(
                handle_kill_command=lambda body, authenticated_instance_id="": called.append(body) or {"accepted": True}
            ),
            auth=FakeAuth(),
        )

        resp = client.post(
            "/api/federation/command",
            json={
                "command": {
                    "command_id": "cmd-1",
                    "command_type": "federation_kill",
                    "authority": "L1_federation_root",
                    "reason": "operator test",
                },
                "issuer_instance_id": "peer-1",
                "unexpected": True,
            },
        )

        assert resp.status_code == 422
        assert called == []

    def test_pause_rejects_unexpected_fields(self, app, identity, emitter, config):
        api_auth.init_api_auth(lambda request: False)
        client = TestClient(app)
        init_federation_api(identity, emitter, config)
        called = []

        class FakeAuth:
            def verify_request(self, method, path, body, headers):
                return SimpleNamespace(valid=True, reason="", instance_id="peer-1")

        init_federation_transport(
            command_relay=SimpleNamespace(
                handle_pause=lambda body, authenticated_instance_id="": called.append(body) or {"accepted": True}
            ),
            auth=FakeAuth(),
        )

        resp = client.post(
            "/api/federation/pause",
            json={"reason": "Soul propagation", "issuer_instance_id": "peer-1", "operator_id": "spoofed"},
        )

        assert resp.status_code == 422
        assert called == []

    def test_peer_register_rejects_unexpected_fields(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)
        called = []

        async def handle_registration_request(body):
            called.append(body)
            return {"accepted": True}

        init_federation_transport(
            peer_protocol=SimpleNamespace(
                handle_registration_request=handle_registration_request,
                handle_registration_confirm=lambda body: {"accepted": True},
            )
        )

        resp = client.post(
            "/api/federation/peer/register",
            json={
                "registration_id": "reg-1",
                "instance_id": "peer-1",
                "public_key_hex": "abcd",
                "address": "https://peer-1.example",
                "challenge": "challenge",
                "challenge_signature": "deadbeef",
                "operator_id": "spoofed",
            },
        )

        assert resp.status_code == 422
        assert called == []

    def test_manage_register_peer_rejects_unexpected_fields(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)
        called = []

        async def initiate_registration(**kwargs):
            called.append(kwargs)
            return SimpleNamespace(success=True, peer_instance_id="peer-1", peer_fingerprint="fp", mutual=True, error="")

        init_federation_transport(peer_protocol=SimpleNamespace(initiate_registration=initiate_registration))

        resp = client.post(
            "/api/federation/manage/register-peer",
            json={"target_address": "https://peer-1.example", "role": "peer", "operator_id": "spoofed"},
        )

        assert resp.status_code == 422
        assert called == []

    def test_manage_handoff_rejects_unexpected_fields(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)
        called = []

        async def initiate_handoff(**kwargs):
            called.append(kwargs)
            return SimpleNamespace(success=True, handoff_id="h-1", state="accepted", target_instance_id="peer-1", error="")

        init_federation_transport(handoff_protocol=SimpleNamespace(initiate_handoff=initiate_handoff))

        resp = client.post(
            "/api/federation/manage/handoff",
            json={"target_instance_id": "peer-1", "task_context": {}, "operator_id": "spoofed"},
        )

        assert resp.status_code == 422
        assert called == []

    def test_manage_kill_rejects_unexpected_fields(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)
        called = []

        class SpyRelay:
            async def issue_and_propagate_kill(self, command_data):
                called.append(command_data)
                return {"command_id": "cmd-1", "results": {}, "total": 0, "timed_out": [], "command": None}

        init_federation_transport(command_relay=SpyRelay())

        resp = client.post(
            "/api/federation/manage/kill",
            json={
                "command": {
                    "command_id": "cmd-1",
                    "command_type": "federation_kill",
                    "authority": "L1_federation_root",
                    "reason": "operator test",
                    "operator_id": "spoofed",
                }
            },
        )

        assert resp.status_code == 422
        assert called == []


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
        init_federation_transport(
            soul_transport=SimpleNamespace(
                get_local_soul_hash=lambda: "live-soul-hash",
            )
        )
        resp = client.get("/api/federation/soul/hash")
        assert resp.status_code == 200
        data = resp.json()
        assert data["instance_id"] == identity.instance_id
        assert data["soul_version_hash"] == "live-soul-hash"

    def test_soul_hash_returns_503_when_runtime_soul_unavailable(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)
        init_federation_transport(
            soul_transport=SimpleNamespace(
                get_local_soul_hash=lambda: "",
            )
        )

        resp = client.get("/api/federation/soul/hash")

        assert resp.status_code == 503
        data = resp.json()
        assert data["soul_version_hash"] == ""
        assert "runtime soul hash" in data["error"].lower()

    def test_soul_handshake_uses_transport_logic(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)
        init_federation_transport(
            soul_transport=SimpleNamespace(
                handle_handshake=lambda body: {
                    "instance_id": identity.instance_id,
                    "local_hash": "abc",
                    "remote_hash": "def",
                    "compatible": False,
                    "compatibility_level": "red",
                    "notes": ["Mission statements differ"],
                }
            )
        )

        resp = client.post("/api/federation/soul/handshake", json={"remote_soul_hash": "def"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["compatible"] is False
        assert data["compatibility_level"] == "red"

    def test_soul_confirm_uses_transport_logic(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)

        async def _handle_confirmation(body, authenticated_instance_id=""):
            return {
                "accepted": True,
                "event_id": body["event_id"],
                "instance_id": authenticated_instance_id or body.get("instance_id", ""),
                "all_confirmed": False,
            }

        init_federation_transport(
            soul_transport=SimpleNamespace(
                handle_soul_confirmation=_handle_confirmation,
            )
        )

        resp = client.post(
            "/api/federation/soul/confirm",
            json={"event_id": "ev-1", "instance_id": "peer-1"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["accepted"] is True
        assert data["event_id"] == "ev-1"

    def test_status_includes_local_identity_fields(self, client, identity, emitter, config):
        config.self_address = "https://mesh.example.internal:8000"
        init_federation_api(identity, emitter, config)
        resp = client.get("/api/federation/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["self_address"] == "https://mesh.example.internal:8000"
        assert data["public_key"] == identity.public_key_hex()

    def test_status_reflects_live_cost_and_propagation_state(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)
        federation_api._divergence_detector = SimpleNamespace(
            state=SimpleNamespace(value="connected"),
            get_divergence_duration_s=lambda: 0.0,
            last_reconciliation=None,
        )
        init_federation_transport(
            soul_transport=SimpleNamespace(
                get_consistency_state=lambda: "propagating",
                get_active_propagations=lambda: [{"event_id": "ev-1", "state": "activating"}],
                get_local_soul_hash=lambda: "live-soul-hash",
            ),
            cost_reporter=SimpleNamespace(
                running=True,
                get_aggregate_status=lambda: {"threshold": "warning", "stale_instance_ids": []},
            ),
            transport=SimpleNamespace(
                started=True,
                get_circuit_breaker_states=lambda: {"peer-1": {"state": "closed"}},
            ),
            heartbeat_mesh=SimpleNamespace(
                running=True,
                get_subscription_status=lambda: {"peer-1": "active"},
                get_stream_outcome_status=lambda: {"peer-1": "ok"},
                get_stream_errors=lambda: {},
            ),
        )
        resp = client.get("/api/federation/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["soul_consistency"] == "propagating"
        assert data["cost_threshold"] == "warning"
        assert data["active_propagations"][0]["event_id"] == "ev-1"
        assert data["transport_ready"] is True
        assert data["runtime_degraded"] is False
        assert data["circuit_breaker_summary"]["closed"] == 1
        assert data["subscription_status"]["peer-1"] == "active"
        assert data["subscription_stream_outcome"]["peer-1"] == "ok"
        assert data["local_soul_hash"] == "live-soul-hash"

    def test_status_surfaces_runtime_degradation_instead_of_healthy_defaults(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)

        class BrokenSoul:
            def get_consistency_state(self):
                raise RuntimeError("soul failure")

            def get_active_propagations(self):
                raise RuntimeError("soul failure")

            def get_local_soul_hash(self):
                raise RuntimeError("soul failure")

        class BrokenCost:
            running = True

            def get_aggregate_status(self):
                raise RuntimeError("cost failure")

        class BrokenDivergence:
            @property
            def state(self):
                raise RuntimeError("divergence failure")

            def get_divergence_duration_s(self):
                raise RuntimeError("divergence failure")

            @property
            def last_reconciliation(self):
                raise RuntimeError("divergence failure")

        federation_api._divergence_detector = BrokenDivergence()
        init_federation_transport(
            soul_transport=BrokenSoul(),
            cost_reporter=BrokenCost(),
            transport=SimpleNamespace(
                started=False,
                get_circuit_breaker_states=lambda: {"peer-1": {"state": "open"}},
            ),
            heartbeat_mesh=SimpleNamespace(
                running=False,
                get_subscription_status=lambda: {"peer-1": "disconnected"},
                get_stream_outcome_status=lambda: {"peer-1": "failed"},
                get_stream_errors=lambda: {"peer-1": "HTTP 503"},
                divergence_evaluation_failed=True,
                divergence_status_error="detector exploded",
            ),
        )

        resp = client.get("/api/federation/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["runtime_degraded"] is True
        assert data["transport_ready"] is False
        assert data["soul_consistency"] == "degraded"
        assert data["cost_threshold"] == "unknown"
        assert data["divergence_state"] == "unknown"
        assert data["divergence_evaluation_failed"] is True
        assert data["circuit_breaker_summary"]["open"] == 1
        assert any("transport not started" in reason.lower() for reason in data["degraded_reasons"])
        assert any("soul transport status unavailable" in reason.lower() for reason in data["degraded_reasons"])
        assert any("heartbeat stream failed" in reason.lower() for reason in data["degraded_reasons"])

    def test_status_surfaces_heartbeat_mesh_divergence_failures(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)
        federation_api._divergence_detector = SimpleNamespace(
            state=SimpleNamespace(value="connected"),
            get_divergence_duration_s=lambda: 0.0,
            last_reconciliation=None,
        )
        init_federation_transport(
            soul_transport=SimpleNamespace(
                get_consistency_state=lambda: "synchronized",
                get_active_propagations=lambda: [],
                get_local_soul_hash=lambda: "",
            ),
            cost_reporter=SimpleNamespace(
                running=True,
                get_aggregate_status=lambda: {"threshold": "normal", "stale_instance_ids": []},
            ),
            transport=SimpleNamespace(
                started=True,
                get_circuit_breaker_states=lambda: {"peer-1": {"state": "closed"}},
            ),
            heartbeat_mesh=SimpleNamespace(
                running=True,
                get_subscription_status=lambda: {"peer-1": "active"},
                get_stream_outcome_status=lambda: {"peer-1": "failed"},
                get_stream_errors=lambda: {"peer-1": "connect failed"},
                divergence_evaluation_failed=True,
                divergence_status_error="detector exploded",
            ),
        )

        resp = client.get("/api/federation/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["runtime_degraded"] is True
        assert data["divergence_evaluation_failed"] is True
        assert data["divergence_status_error"] == "detector exploded"
        assert any("divergence evaluation failed" in reason.lower() for reason in data["degraded_reasons"])
        assert any("heartbeat stream failed for peer(s): peer-1" in reason for reason in data["degraded_reasons"])

    def test_status_degrades_when_runtime_soul_hash_is_unavailable(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)
        federation_api._divergence_detector = SimpleNamespace(
            state=SimpleNamespace(value="connected"),
            get_divergence_duration_s=lambda: 0.0,
            last_reconciliation=None,
        )
        init_federation_transport(
            soul_transport=SimpleNamespace(
                get_consistency_state=lambda: "synchronized",
                get_active_propagations=lambda: [],
                get_local_soul_hash=lambda: "",
            ),
            cost_reporter=SimpleNamespace(
                running=True,
                get_aggregate_status=lambda: {"threshold": "normal", "stale_instance_ids": []},
            ),
            transport=SimpleNamespace(
                started=True,
                get_circuit_breaker_states=lambda: {},
            ),
            heartbeat_mesh=SimpleNamespace(
                running=True,
                get_subscription_status=lambda: {},
                get_stream_outcome_status=lambda: {},
                get_stream_errors=lambda: {},
                divergence_evaluation_failed=False,
                divergence_status_error=None,
            ),
        )

        resp = client.get("/api/federation/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["runtime_degraded"] is True
        assert data["local_soul_hash"] == ""
        assert any("runtime soul hash unavailable" in reason.lower() for reason in data["degraded_reasons"])

    def test_status_surfaces_cost_reporter_error_payload_as_degraded(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)
        federation_api._divergence_detector = SimpleNamespace(
            state=SimpleNamespace(value="connected"),
            get_divergence_duration_s=lambda: 0.0,
            last_reconciliation=None,
        )
        init_federation_transport(
            soul_transport=SimpleNamespace(
                get_consistency_state=lambda: "synchronized",
                get_active_propagations=lambda: [],
            ),
            cost_reporter=SimpleNamespace(
                running=True,
                get_aggregate_status=lambda: {"error": "aggregate unavailable"},
            ),
            transport=SimpleNamespace(
                started=True,
                get_circuit_breaker_states=lambda: {},
            ),
            heartbeat_mesh=SimpleNamespace(
                running=True,
                get_subscription_status=lambda: {},
                divergence_evaluation_failed=False,
                divergence_status_error=None,
            ),
        )

        resp = client.get("/api/federation/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["runtime_degraded"] is True
        assert data["cost_threshold"] == "unknown"
        assert any("cost status unavailable" in reason.lower() for reason in data["degraded_reasons"])
        assert any("aggregate unavailable" in err.lower() for err in data["runtime_errors"])

    def test_health_includes_runtime_degradation_details(self, client, identity, emitter, config):
        topology = SimpleNamespace(
            list_peers=lambda: [SimpleNamespace(instance_id="peer-1")],
            get_health_summary=lambda: {
                "total_peers": 1,
                "healthy": 1,
                "warning": 0,
                "critical": 0,
                "lost": 0,
                "deployment_mode": "federated",
            },
        )
        init_federation_api(identity, emitter, config, topology_registry=topology)
        init_federation_transport(
            soul_transport=SimpleNamespace(
                get_consistency_state=lambda: "synchronized",
                get_active_propagations=lambda: [],
            ),
            cost_reporter=SimpleNamespace(
                running=False,
                get_aggregate_status=lambda: {"threshold": "normal", "stale_instance_ids": ["peer-1"]},
            ),
            transport=SimpleNamespace(
                started=True,
                get_circuit_breaker_states=lambda: {"peer-1": {"state": "half_open"}},
            ),
            heartbeat_mesh=SimpleNamespace(
                running=False,
                get_subscription_status=lambda: {"peer-1": "disconnected"},
                get_stream_outcome_status=lambda: {"peer-1": "failed"},
                get_stream_errors=lambda: {"peer-1": "connect failed"},
            ),
        )

        resp = client.get("/api/federation/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["runtime_degraded"] is True
        assert data["heartbeat_mesh_running"] is False
        assert data["circuit_breaker_summary"]["half_open"] == 1
        assert data["stale_instance_ids"] == ["peer-1"]
        assert data["subscription_stream_outcome"]["peer-1"] == "failed"

    def test_budget_threshold_surfaces_aggregate_failure(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)
        init_federation_transport(
            cost_reporter=SimpleNamespace(
                get_aggregate_status=lambda: {"error": "aggregate unavailable"},
            ),
        )

        resp = client.get("/api/federation/budget/threshold")
        assert resp.status_code == 503
        data = resp.json()
        assert data["threshold"] == "unknown"
        assert "aggregate unavailable" in data["error"]

    def test_budget_snapshot_surfaces_aggregate_failure(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)
        init_federation_transport(
            cost_reporter=SimpleNamespace(
                get_aggregate_status=lambda: {"error": "aggregate unavailable"},
            ),
        )

        resp = client.get("/api/federation/budget")
        assert resp.status_code == 503
        data = resp.json()
        assert "aggregate unavailable" in data["error"]

    def test_get_settings_returns_local_instance_settings(self, client, identity, emitter, config):
        config.self_address = "https://mesh.example.internal:8000"
        init_federation_api(identity, emitter, config)
        resp = client.get("/api/federation/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["self_address"] == "https://mesh.example.internal:8000"
        assert data["instance_id"] == identity.instance_id
        assert data["public_key"] == identity.public_key_hex()
        assert data["restart_required"] is True

    def test_update_settings_persists_self_address(self, client, identity, emitter, config, monkeypatch):
        init_federation_api(identity, emitter, config)
        saved = {}

        def fake_save(updated):
            saved["config"] = updated
            return None

        monkeypatch.setattr(federation_api, "save_federation_config", fake_save)

        resp = client.put(
            "/api/federation/settings",
            json={"self_address": "https://mesh.example.internal:8000/"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["saved"] is True
        assert data["self_address"] == "https://mesh.example.internal:8000"
        assert saved["config"].self_address == "https://mesh.example.internal:8000"

    def test_update_settings_rejects_invalid_address(self, client, identity, emitter, config):
        init_federation_api(identity, emitter, config)
        resp = client.put("/api/federation/settings", json={"self_address": "notaurl"})
        assert resp.status_code == 400

    def test_dashboard_returns_self_command_center_entry(self, client, identity, emitter, config, monkeypatch):
        import feature_flags as ff

        monkeypatch.setattr(ff, "FEATURE_FEDERATION", True)
        monkeypatch.setattr(ff, "FEATURE_FEDERATION_DASHBOARD", True)
        config.self_address = "https://root.example"
        init_federation_api(identity, emitter, config)
        emitter.emit_once()

        resp = client.get("/api/federation/dashboard")

        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["fleet"]["total_instances"] == 1
        assert data["instances"][0]["is_self"] is True
        assert data["instances"][0]["command_center_url"] == "/war-room/command"

    def test_dashboard_activity_feed_uses_receipt_service_for_agent_events(self, client, identity, emitter, config, monkeypatch):
        import feature_flags as ff
        from src.core import receipts_api
        from src.shared.receipts import ActionType, Receipt, ReceiptStatus

        monkeypatch.setattr(ff, "FEATURE_FEDERATION", True)
        monkeypatch.setattr(ff, "FEATURE_FEDERATION_DASHBOARD", True)
        receipt = Receipt(
            id="receipt-agent-1",
            timestamp="2026-04-30T12:00:00+00:00",
            action_type=ActionType.HIVE_AGENT_EVENT.value,
            action_name="",
            status=ReceiptStatus.SUCCESS.value,
            metadata={"event": "hive_agent_spawned", "operator_id": "op-arthur"},
        )

        class FakeReceiptService:
            def list(self, limit=100, **kwargs):
                return [receipt]

        monkeypatch.setattr(receipts_api, "_receipt_service", FakeReceiptService())
        init_federation_api(identity, emitter, config)
        emitter.emit_once()

        resp = client.get("/api/federation/dashboard")

        assert resp.status_code == 200
        data = resp.json()
        assert data["activity"][0]["id"] == "receipt-agent-1"
        assert data["activity"][0]["event_type"] == ActionType.HIVE_AGENT_EVENT.value
        assert data["activity"][0]["description"] == "hive_agent_spawned"
        assert data["activity"][0]["operator"] == "op-arthur"
        assert data["instances"][0]["recent_activity"] == "hive_agent_spawned"

    def test_dashboard_approval_queue_includes_pending_sentry_request(self, client, identity, emitter, config, monkeypatch, tmp_path):
        import feature_flags as ff
        from src.core import governance_api
        from src.integrations.mcp_sentry import MCPSentry

        monkeypatch.setattr(ff, "FEATURE_FEDERATION", True)
        monkeypatch.setattr(ff, "FEATURE_FEDERATION_DASHBOARD", True)
        sentry = MCPSentry(data_dir=str(tmp_path))
        pending = sentry.check_permission(
            "connector.email.send_message",
            {"recipient": "customer@example.com", "subject": "Fleet approval test"},
        )
        assert pending["status"] == "PENDING"
        monkeypatch.setattr(governance_api, "_mcp_sentry", sentry)
        init_federation_api(identity, emitter, config)
        emitter.emit_once()

        resp = client.get("/api/federation/dashboard")

        assert resp.status_code == 200
        data = resp.json()
        approval = next(item for item in data["approvals"] if item["id"] == pending["request_id"])
        assert approval["instance_id"] == identity.instance_id
        assert approval["capability"] == "connector.email.send_message"
        assert approval["risk_tier"] == "T3"
        assert data["fleet"]["pending_approvals"] == 1
        assert data["instances"][0]["pending_approvals"] == 1

    def test_dashboard_ignores_stale_cost_for_unregistered_peers(self, client, identity, emitter, config, monkeypatch):
        import feature_flags as ff

        monkeypatch.setattr(ff, "FEATURE_FEDERATION", True)
        monkeypatch.setattr(ff, "FEATURE_FEDERATION_DASHBOARD", True)
        init_federation_api(identity, emitter, config)
        federation_api._divergence_detector = SimpleNamespace(
            state=SimpleNamespace(value="connected"),
            get_divergence_duration_s=lambda: 0.0,
            last_reconciliation=None,
        )
        init_federation_transport(
            soul_transport=SimpleNamespace(
                get_consistency_state=lambda: "synchronized",
                get_active_propagations=lambda: [],
                get_local_soul_hash=lambda: "root-soul",
            ),
            cost_reporter=SimpleNamespace(
                running=True,
                get_aggregate_status=lambda: {
                    "threshold": "normal",
                    "stale_instance_ids": ["retired-peer"],
                },
            ),
            transport=SimpleNamespace(
                started=True,
                get_circuit_breaker_states=lambda: {},
            ),
            heartbeat_mesh=SimpleNamespace(
                running=True,
                get_subscription_status=lambda: {},
                get_stream_outcome_status=lambda: {},
                get_stream_errors=lambda: {},
                divergence_evaluation_failed=False,
                divergence_status_error=None,
            ),
        )
        emitter.emit_once()

        resp = client.get("/api/federation/dashboard")

        assert resp.status_code == 200
        data = resp.json()
        local = data["instances"][0]
        assert local["health"] == "healthy"
        assert local["state"] == "healthy"
        assert data["fleet"]["instances_needing_attention"] == 0
        assert all("cost data stale" not in reason.lower() for reason in local["attention_reasons"])

    def test_dashboard_formats_current_peer_cost_staleness_as_attention_notice(self, client, identity, emitter, config, monkeypatch):
        import feature_flags as ff
        from src.federation.topology import TopologyRegistry

        monkeypatch.setattr(ff, "FEATURE_FEDERATION", True)
        monkeypatch.setattr(ff, "FEATURE_FEDERATION_DASHBOARD", True)
        topology = TopologyRegistry(identity.instance_id)
        topology.register_peer(
            "peer-1",
            fingerprint="fp-1",
            public_key_hex="abcd",
            address="https://peer.example",
            role="child",
            soul_version_hash="root-soul",
        )
        init_federation_api(identity, emitter, config, topology_registry=topology)
        federation_api._divergence_detector = SimpleNamespace(
            state=SimpleNamespace(value="connected"),
            get_divergence_duration_s=lambda: 0.0,
            last_reconciliation=None,
        )
        init_federation_transport(
            soul_transport=SimpleNamespace(
                get_consistency_state=lambda: "synchronized",
                get_active_propagations=lambda: [],
                get_local_soul_hash=lambda: "root-soul",
            ),
            cost_reporter=SimpleNamespace(
                running=True,
                get_aggregate_status=lambda: {
                    "threshold": "normal",
                    "stale_instance_ids": ["peer-1"],
                },
            ),
            transport=SimpleNamespace(
                started=True,
                get_circuit_breaker_states=lambda: {},
            ),
            heartbeat_mesh=SimpleNamespace(
                running=True,
                get_subscription_status=lambda: {},
                get_stream_outcome_status=lambda: {},
                get_stream_errors=lambda: {},
                divergence_evaluation_failed=False,
                divergence_status_error=None,
            ),
        )
        emitter.emit_once()

        resp = client.get("/api/federation/dashboard")

        assert resp.status_code == 200
        data = resp.json()
        local = next(item for item in data["instances"] if item["is_self"])
        assert local["health"] == "healthy"
        assert "Cost telemetry stale for peer.example" in local["attention_reasons"]
        assert all("Federation cost data stale" not in reason for reason in local["attention_reasons"])

    def test_dashboard_surfaces_peer_command_center_fallback(self, client, identity, emitter, config, monkeypatch):
        import feature_flags as ff
        from src.federation.topology import TopologyRegistry

        monkeypatch.setattr(ff, "FEATURE_FEDERATION", True)
        monkeypatch.setattr(ff, "FEATURE_FEDERATION_DASHBOARD", True)
        topology = TopologyRegistry(identity.instance_id)
        topology.register_peer(
            "peer-1",
            fingerprint="fp-1",
            public_key_hex="abcd",
            address="https://peer.example",
            role="child",
            soul_version_hash="peer-soul",
        )
        topology.update_heartbeat("peer-1", soul_version_hash="peer-soul")
        init_federation_api(identity, emitter, config, topology_registry=topology)
        emitter.emit_once()

        resp = client.get("/api/federation/dashboard")

        assert resp.status_code == 200
        data = resp.json()
        peer = next(item for item in data["instances"] if item["instance_id"] == "peer-1")
        assert peer["command_center_url"] == "https://peer.example/war-room/command"
        assert peer["detail_status"] == "unavailable"
        assert any("Remote detail unavailable" in reason for reason in peer["attention_reasons"])

    def test_dashboard_local_approval_uses_operator_identity(self, client, identity, emitter, config, monkeypatch):
        import feature_flags as ff
        from src.core import governance_api

        monkeypatch.setattr(ff, "FEATURE_FEDERATION", True)
        monkeypatch.setattr(ff, "FEATURE_FEDERATION_DASHBOARD", True)
        init_federation_api(identity, emitter, config)
        recorded = {}

        def fake_approve(approval_id, *, reason="", identity=None):
            recorded["approval_id"] = approval_id
            recorded["reason"] = reason
            recorded["operator_id"] = identity.operator_id if identity else ""
            return {"status": "approved", "id": approval_id, "type": "sentry"}

        monkeypatch.setattr(governance_api, "_approve_item_direct", fake_approve)

        resp = client.post(
            f"/api/federation/dashboard/instances/{identity.instance_id}/approvals/ap-1/approve",
            json={"reason": "operator reviewed the action"},
        )

        assert resp.status_code == 200
        assert resp.json()["result"]["status"] == "approved"
        assert recorded == {
            "approval_id": "ap-1",
            "reason": "operator reviewed the action",
            "operator_id": "op-arthur",
        }

    def test_dashboard_remote_approval_proxies_operator_identity(self, client, identity, emitter, config, monkeypatch):
        import feature_flags as ff
        from src.federation.topology import TopologyRegistry

        monkeypatch.setattr(ff, "FEATURE_FEDERATION", True)
        monkeypatch.setattr(ff, "FEATURE_FEDERATION_DASHBOARD", True)
        topology = TopologyRegistry(identity.instance_id)
        topology.register_peer(
            "peer-1",
            fingerprint="fp-1",
            public_key_hex="abcd",
            address="https://peer.example",
            role="child",
            soul_version_hash="peer-soul",
        )
        init_federation_api(identity, emitter, config, topology_registry=topology)
        recorded = {}

        class FakeTransport:
            async def send(self, **kwargs):
                recorded.update(kwargs)
                return SimpleNamespace(
                    success=True,
                    status_code=200,
                    body={
                        "success": True,
                        "result": {"status": "approved", "id": "ap-1", "type": "sentry"},
                    },
                    error="",
                )

        init_federation_transport(transport=FakeTransport())

        resp = client.post(
            "/api/federation/dashboard/instances/peer-1/approvals/ap-1/approve",
            json={"reason": "operator reviewed the remote action"},
        )

        assert resp.status_code == 200
        assert recorded["method"] == "POST"
        assert recorded["path"] == "/api/federation/dashboard/local/approvals/ap-1/approve"
        assert recorded["body"]["reason"] == "operator reviewed the remote action"
        assert recorded["body"]["operator_identity"]["operator_id"] == "op-arthur"
        assert recorded["body"]["source_instance_id"] == identity.instance_id

    def test_dashboard_local_detail_rejects_non_root_peer(self, app, identity, emitter, config, monkeypatch):
        import feature_flags as ff
        from src.federation.topology import TopologyRegistry

        monkeypatch.setattr(ff, "FEATURE_FEDERATION", True)
        monkeypatch.setattr(ff, "FEATURE_FEDERATION_DASHBOARD", True)
        api_auth.init_api_auth(lambda request: False)
        client = TestClient(app)
        topology = TopologyRegistry(identity.instance_id)
        topology.register_peer(
            "peer-1",
            fingerprint="fp-1",
            public_key_hex="abcd",
            address="https://peer.example",
            role="peer",
            soul_version_hash="peer-soul",
        )
        init_federation_api(identity, emitter, config, topology_registry=topology)

        class FakeAuth:
            def verify_request(self, method, path, body, headers):
                return SimpleNamespace(valid=True, reason="", instance_id="peer-1")

        init_federation_transport(auth=FakeAuth())

        resp = client.get("/api/federation/dashboard/local")

        assert resp.status_code == 403
        assert "ROOT" in resp.json()["detail"]

    def test_dashboard_local_approval_accepts_root_peer_identity_payload(self, app, identity, emitter, config, monkeypatch):
        import feature_flags as ff
        from src.core import governance_api
        from src.federation.topology import TopologyRegistry

        monkeypatch.setattr(ff, "FEATURE_FEDERATION", True)
        monkeypatch.setattr(ff, "FEATURE_FEDERATION_DASHBOARD", True)
        api_auth.init_api_auth(lambda request: False)
        client = TestClient(app)
        topology = TopologyRegistry(identity.instance_id)
        topology.register_peer(
            "root-1",
            fingerprint="fp-root",
            public_key_hex="abcd",
            address="https://root.example",
            role="root",
            soul_version_hash="root-soul",
        )
        init_federation_api(identity, emitter, config, topology_registry=topology)
        recorded = {}

        class FakeAuth:
            def verify_request(self, method, path, body, headers):
                return SimpleNamespace(valid=True, reason="", instance_id="root-1")

        def fake_approve(approval_id, *, reason="", identity=None):
            recorded["approval_id"] = approval_id
            recorded["reason"] = reason
            recorded["operator_id"] = identity.operator_id if identity else ""
            return {"status": "approved", "id": approval_id, "type": "sentry"}

        init_federation_transport(auth=FakeAuth())
        monkeypatch.setattr(governance_api, "_approve_item_direct", fake_approve)

        resp = client.post(
            "/api/federation/dashboard/local/approvals/ap-1/approve",
            json={
                "reason": "root operator approved",
                "operator_identity": {
                    "operator_id": "op-root",
                    "display_name": "Root Operator",
                    "session_id": "session-root",
                    "session_started_at": "2026-04-10T00:00:00Z",
                    "auth_method": "local",
                    "ip_address": "127.0.0.1",
                },
                "source_instance_id": "root-1",
            },
        )

        assert resp.status_code == 200
        assert resp.json()["result"]["status"] == "approved"
        assert recorded == {
            "approval_id": "ap-1",
            "reason": "root operator approved",
            "operator_id": "op-root",
        }


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
