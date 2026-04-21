"""
Tests for Control-Plane API Endpoints (Prompt 6).

Uses FastAPI TestClient — no Docker or live services required.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import api_auth
from src.core import auth_api
from src.core.onboarding_snapshot import OnboardingSnapshot, OnboardingState
from src.core import control_plane
from src.core.operator_identity import OperatorIdentity
from src.core.runtime_pause import get_runtime_pause_status
from src.core.model_usage_policy import set_local_model_availability


@pytest.fixture
def app(tmp_data_dir):
    """Create a fresh FastAPI app with the control-plane router for each test."""
    api_auth.init_api_auth(lambda request: True)
    auth_api._sessions.clear()
    identity = OperatorIdentity(
        operator_id="op-1",
        display_name="Arthur",
        session_id="session-1",
        session_started_at="2026-04-14T00:00:00Z",
        auth_method="local",
        ip_address="127.0.0.1",
    )
    auth_api._sessions["test-session"] = {
        "expires_at": 9999999999,
        "username": "Arthur",
        "operator_identity": identity,
        "capabilities": sorted({"warroom.login", "platform.admin", "onboarding.admin"}),
        "groups": [],
    }
    test_app = FastAPI()
    control_plane.init_control_plane(str(tmp_data_dir))
    set_local_model_availability(False, "Local model not initialized", loaded=False, ready=False)
    control_plane.set_runtime_control_hooks(
        emergency_stop_handler=lambda **kwargs: {
            "stopped_hive_agents": 2,
            "stopped_agent_ids": ["agent-1", "agent-2"],
            "execution_state": "emergency_stopped",
        }
    )
    test_app.include_router(control_plane.router)
    return test_app


@pytest.fixture
def client(app):
    client = TestClient(app)
    client.cookies.set(auth_api.get_warroom_session_cookie_name(), "test-session")
    return client


@pytest.fixture
def snap():
    """Return the active snapshot after init."""
    return control_plane.get_snapshot()


# ==================================================================
# GET /system/status
# ==================================================================

class TestSystemStatus:

    def test_returns_200(self, client):
        resp = client.get("/system/status")
        assert resp.status_code == 200

    def test_contains_onboarding_section(self, client):
        data = client.get("/system/status").json()
        assert "onboarding" in data
        ob = data["onboarding"]
        assert "state" in ob
        assert "flagship_provider" in ob
        assert "credential_status" in ob
        assert "local_model_status" in ob
        assert "local_model_runtime_status" in ob
        assert "local_model_runtime_ready" in ob
        assert "local_model_runtime_loaded" in ob
        assert "local_model_last_verified_at" in ob
        assert "local_model_last_error" in ob
        assert "is_ready" in ob

    def test_contains_cooldown_section(self, client):
        data = client.get("/system/status").json()
        assert "cooldown" in data
        cd = data["cooldown"]
        assert "active" in cd
        assert "remaining_seconds" in cd

    def test_contains_uptime(self, client):
        data = client.get("/system/status").json()
        assert "uptime_seconds" in data
        assert data["uptime_seconds"] >= 0

    def test_contains_runtime_pause(self, client):
        data = client.get("/system/status").json()
        assert "runtime_pause" in data
        assert data["runtime_pause"]["paused"] is False

    def test_contains_model_usage_policy(self, client):
        data = client.get("/system/status").json()
        assert "model_usage_policy" in data
        assert data["model_usage_policy"]["local_execution_mode"] == "low_risk_only"
        assert data["model_usage_policy"]["local_model_ready"] is False
        assert data["model_usage_policy"]["local_model_loaded"] is False
        assert data["model_usage_policy"]["local_model_status"] == "unavailable"

    def test_reflects_state_changes(self, client, snap):
        snap.transition(OnboardingState.CREDENTIALS_CAPTURE,
                        flagship_provider="openai")
        data = client.get("/system/status").json()
        assert data["onboarding"]["state"] == "CREDENTIALS_CAPTURE"
        assert data["onboarding"]["flagship_provider"] == "openai"

    def test_reflects_ready(self, client, snap):
        snap.transition(OnboardingState.READY)
        data = client.get("/system/status").json()
        assert data["onboarding"]["is_ready"] is True

    def test_reflects_cooldown(self, client, snap):
        snap.enter_cooldown(300, "test reason")
        data = client.get("/system/status").json()
        assert data["cooldown"]["active"] is True
        assert data["cooldown"]["remaining_seconds"] > 0
        assert data["cooldown"]["reason"] == "test reason"


# ==================================================================
# GET /onboarding/status
# ==================================================================

class TestOnboardingStatus:

    def test_returns_200(self, client):
        resp = client.get("/onboarding/status")
        assert resp.status_code == 200

    def test_all_fields_present(self, client):
        data = client.get("/onboarding/status").json()
        expected = {
            "state", "flagship_provider", "credential_status",
            "local_model_status", "is_ready", "cooldown_active",
            "cooldown_remaining", "last_error", "resend_count", "updated_at",
            "local_model_runtime_status", "local_model_runtime_ready",
            "local_model_runtime_loaded", "local_model_last_verified_at",
            "local_model_last_checked_at", "local_model_consecutive_failures",
            "local_model_last_smoke_elapsed_ms",
        }
        assert expected.issubset(set(data.keys()))

    def test_reflects_snapshot(self, client, snap):
        snap.transition(OnboardingState.COMMS_VERIFY,
                        flagship_provider="anthropic",
                        credential_status="verified")
        data = client.get("/onboarding/status").json()
        assert data["state"] == "COMMS_VERIFY"
        assert data["flagship_provider"] == "anthropic"
        assert data["credential_status"] == "verified"


# ==================================================================
# POST /onboarding/command
# ==================================================================

class TestOnboardingCommand:

    def test_status_command(self, client):
        resp = client.post("/onboarding/command", json={"command": "STATUS"})
        assert resp.status_code == 200
        data = resp.json()
        assert "response" in data
        assert "System Status" in data["response"]
        assert "state" in data

    def test_unknown_command_returns_400(self, client):
        resp = client.post("/onboarding/command", json={"command": "EXPLODE"})
        assert resp.status_code == 400
        assert "Unknown command" in resp.json()["error"]

    def test_missing_command_returns_400(self, client):
        resp = client.post("/onboarding/command", json={})
        assert resp.status_code == 400
        assert "Missing" in resp.json()["error"]

    def test_back_via_command(self, client, snap):
        snap.transition(OnboardingState.FLAGSHIP_SELECTION)
        resp = client.post("/onboarding/command", json={"command": "BACK"})
        data = resp.json()
        assert data["state"] == "WELCOME"

    def test_reset_via_command(self, client, snap):
        snap.transition(OnboardingState.READY, flagship_provider="gemini")
        resp = client.post("/onboarding/command", json={"command": "RESET ONBOARDING"})
        data = resp.json()
        assert data["state"] == "WELCOME"

    def test_command_rejects_undeclared_fields(self, client):
        resp = client.post(
            "/onboarding/command",
            json={"command": "STATUS", "operator_id": "Mallory"},
        )
        assert resp.status_code == 422


# ==================================================================
# POST /onboarding/back
# ==================================================================

class TestOnboardingBack:

    def test_back_from_flagship(self, client, snap):
        snap.transition(OnboardingState.FLAGSHIP_SELECTION)
        resp = client.post("/onboarding/back")
        assert resp.status_code == 200
        assert resp.json()["state"] == "WELCOME"

    def test_back_from_welcome_fails_gracefully(self, client):
        resp = client.post("/onboarding/back")
        assert resp.status_code == 200
        assert "Cannot" in resp.json()["response"]


# ==================================================================
# POST /onboarding/restart-step
# ==================================================================

class TestOnboardingRestartStep:

    def test_restart_clears_state(self, client, snap):
        snap.transition(OnboardingState.CREDENTIALS_CAPTURE,
                        temp_data={"key": "val"})
        resp = client.post("/onboarding/restart-step")
        assert resp.status_code == 200
        assert "restarted" in resp.json()["response"]
        assert snap.temp_data == {}

    def test_restart_at_welcome(self, client):
        resp = client.post("/onboarding/restart-step")
        assert "first step" in resp.json()["response"]


# ==================================================================
# POST /onboarding/resend-code
# ==================================================================

class TestOnboardingResendCode:

    def test_resend_at_verify_state(self, client, snap):
        snap.transition(OnboardingState.COMMS_VERIFY)
        resp = client.post("/onboarding/resend-code")
        assert resp.status_code == 200
        assert "generated" in resp.json()["response"]

    def test_resend_outside_verify_fails(self, client):
        resp = client.post("/onboarding/resend-code")
        assert "only available" in resp.json()["response"]


# ==================================================================
# POST /onboarding/reset
# ==================================================================

class TestOnboardingReset:

    def test_reset_from_ready(self, client, snap):
        snap.transition(OnboardingState.READY, flagship_provider="openai")
        resp = client.post("/onboarding/reset")
        assert resp.status_code == 200
        assert resp.json()["state"] == "WELCOME"

    def test_reset_from_cooldown(self, client, snap):
        snap.enter_cooldown(9999, "stuck")
        resp = client.post("/onboarding/reset")
        assert resp.json()["state"] == "WELCOME"

    def test_reset_at_welcome_noop(self, client):
        resp = client.post("/onboarding/reset")
        assert "Already" in resp.json()["response"]


class TestRuntimePause:
    def test_runtime_pause_status(self, client):
        resp = client.get("/system/pause")
        assert resp.status_code == 200
        assert resp.json()["paused"] is False

    def test_pause_and_resume_runtime(self, client):
        paused = client.post("/system/pause", json={"reason": "Maintenance window"})
        assert paused.status_code == 200
        assert paused.json()["paused"] is True
        assert paused.json()["reason"] == "Maintenance window"

        resumed = client.post("/system/resume")
        assert resumed.status_code == 200
        assert resumed.json()["paused"] is False
        assert get_runtime_pause_status()["paused"] is False

    def test_pause_without_body_fails_cleanly(self, client):
        paused = client.post("/system/pause")
        assert paused.status_code == 400
        assert paused.json()["error"] == "Missing pause reason"

    def test_pause_rejects_undeclared_fields(self, client):
        paused = client.post(
            "/system/pause",
            json={"reason": "Maintenance window", "scope": "global"},
        )
        assert paused.status_code == 422

    def test_emergency_stop_runtime(self, client):
        stopped = client.post("/system/emergency-stop", json={"reason": "Emergency maintenance"})
        assert stopped.status_code == 200
        body = stopped.json()
        assert body["paused"] is True
        assert body["stopped_hive_agents"] == 2
        assert body["execution_state"] == "emergency_stopped"

    def test_emergency_stop_without_body_fails_cleanly(self, client):
        stopped = client.post("/system/emergency-stop")
        assert stopped.status_code == 400
        assert stopped.json()["error"] == "Missing emergency stop reason"

    def test_emergency_stop_rejects_undeclared_fields(self, client):
        stopped = client.post(
            "/system/emergency-stop",
            json={"reason": "Emergency maintenance", "force": True},
        )
        assert stopped.status_code == 422


class TestModelUsagePolicy:
    def test_get_model_policy(self, client):
        resp = client.get("/system/model-policy")
        assert resp.status_code == 200
        data = resp.json()
        assert data["local_execution_mode"] == "low_risk_only"
        assert data["frontier_scrub_mode"] == "required"

    def test_update_model_policy(self, client):
        resp = client.put(
            "/system/model-policy",
            json={
                "local_execution_mode": "disabled",
                "frontier_scrub_mode": "preferred",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["local_execution_mode"] == "disabled"
        assert data["frontier_scrub_mode"] == "preferred"

    def test_update_model_policy_requires_changes(self, client):
        resp = client.put("/system/model-policy", json={})
        assert resp.status_code == 400
        assert resp.json()["error"] == "No model usage policy changes supplied"

    def test_update_model_policy_rejects_undeclared_fields(self, client):
        resp = client.put(
            "/system/model-policy",
            json={"local_execution_mode": "disabled", "operator_id": "Mallory"},
        )
        assert resp.status_code == 422


# ==================================================================
# Error handling — no stack traces
# ==================================================================

class TestSafeErrors:

    def test_invalid_json_returns_error(self, client):
        resp = client.post(
            "/onboarding/command",
            content="not json",
            headers={"content-type": "application/json"},
        )
        # Should return an error status (422 or 500) with no stack trace
        assert resp.status_code in (422, 500)
        data = resp.json()
        assert "error" in data or "detail" in data

    def test_error_responses_have_structure(self, client):
        resp = client.post("/onboarding/command", json={})
        data = resp.json()
        assert "error" in data
        assert "status" in data


class TestWarRoomArtifacts:

    def test_store_artifact_normalizes_server_side_fields(self, client):
        resp = client.post(
            "/warroom/artifacts",
            json={
                "id": "client-id",
                "type": "TOOL_TRACE",
                "content": {"message": "hello"},
                "session_id": "foreign-session",
            },
        )
        assert resp.status_code == 200

        listing = client.get("/warroom/artifacts").json()
        artifact = listing["artifacts"][0]
        assert artifact["id"] != "client-id"
        assert artifact["session_id"] == "session-1"
        assert artifact["operator_id"] == "op-1"
        assert artifact["source"] == "api"

    def test_store_artifact_rejects_unknown_type(self, client):
        resp = client.post(
            "/warroom/artifacts",
            json={"type": "ARBITRARY_FAKE", "content": {"message": "x"}},
        )
        assert resp.status_code == 400

    def test_store_artifact_rejects_undeclared_fields(self, client):
        resp = client.post(
            "/warroom/artifacts",
            json={
                "type": "TOOL_TRACE",
                "content": {"message": "hello"},
                "debug": True,
            },
        )
        assert resp.status_code == 422

    def test_artifact_store_is_bounded(self, client):
        for idx in range(205):
            resp = client.post(
                "/warroom/artifacts",
                json={"type": "TOOL_TRACE", "content": {"idx": idx}},
            )
            assert resp.status_code == 200

        listing = client.get("/warroom/artifacts").json()
        assert listing["total"] == 200


class TestTokens:

    def test_revoke_rejects_undeclared_fields(self, client):
        resp = client.post(
            "/tokens/token-1/revoke",
            json={"reason": "Manual revocation", "operator_id": "Mallory"},
        )
        assert resp.status_code == 422
