"""
Tests for src.core.health — Heartbeat endpoints (Prompt 9 / C1-C2).
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.health.types import HealthSnapshot
from src.core.health.api import router, set_snapshot_provider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    """Create a test client with health endpoints."""
    app = FastAPI()
    app.include_router(router)
    set_snapshot_provider(None)
    return TestClient(app)


# ===================================================================
# GET /health/live
# ===================================================================

class TestHealthLive:

    def test_live_returns_200(self, client):
        resp = client.get("/health/live")
        assert resp.status_code == 200

    def test_live_returns_alive(self, client):
        resp = client.get("/health/live")
        assert resp.json()["status"] == "alive"

    def test_live_always_succeeds(self, client):
        # Even with no snapshot provider, live works
        set_snapshot_provider(None)
        resp = client.get("/health/live")
        assert resp.status_code == 200


# ===================================================================
# GET /health/ready — required keys
# ===================================================================

REQUIRED_KEYS = [
    "ready",
    "onboarding_state",
    "local_llm_ready",
    "scheduler_running",
    "last_health_tick_at",
    "last_scheduler_tick_at",
    "degraded_reasons",
]


class TestHealthReadyKeys:

    def test_ready_returns_required_keys(self, client):
        """Blueprint requirement: endpoints return required keys."""
        resp = client.get("/health/ready")
        assert resp.status_code == 200
        data = resp.json()
        for key in REQUIRED_KEYS:
            assert key in data, f"Missing required key: {key}"

    def test_ready_returns_ready_false_without_provider(self, client):
        set_snapshot_provider(None)
        resp = client.get("/health/ready")
        data = resp.json()
        assert data["ready"] is False
        assert len(data["degraded_reasons"]) > 0


# ===================================================================
# No stack traces leaked
# ===================================================================

class TestNoStackTraces:

    def test_no_stack_trace_on_error(self, client):
        """Blueprint requirement: no stack traces leaked."""
        def bad_provider():
            raise RuntimeError("internal error details")

        set_snapshot_provider(bad_provider)
        resp = client.get("/health/ready")
        assert resp.status_code == 200
        body = resp.text
        assert "RuntimeError" not in body
        assert "internal error details" not in body
        assert "Traceback" not in body

    def test_error_returns_degraded_reasons(self, client):
        def bad_provider():
            raise RuntimeError("boom")

        set_snapshot_provider(bad_provider)
        resp = client.get("/health/ready")
        data = resp.json()
        assert data["ready"] is False
        assert "error" in data["degraded_reasons"][0].lower()


# ===================================================================
# With snapshot provider
# ===================================================================

class TestWithProvider:

    def test_ready_true_when_healthy(self, client):
        def healthy():
            return HealthSnapshot(
                ready=True,
                onboarding_state="READY",
                local_llm_ready=True,
                scheduler_running=True,
                last_health_tick_at="2025-01-01T00:00:00Z",
                last_scheduler_tick_at="2025-01-01T00:00:00Z",
            )

        set_snapshot_provider(healthy)
        resp = client.get("/health/ready")
        data = resp.json()
        assert data["ready"] is True
        assert data["onboarding_state"] == "READY"
        assert data["local_llm_ready"] is True
        assert data["scheduler_running"] is True

    def test_ready_false_with_degraded(self, client):
        def degraded():
            return HealthSnapshot(
                ready=False,
                onboarding_state="READY",
                local_llm_ready=False,
                degraded_reasons=["Local LLM not responding"],
            )

        set_snapshot_provider(degraded)
        resp = client.get("/health/ready")
        data = resp.json()
        assert data["ready"] is False
        assert "Local LLM" in data["degraded_reasons"][0]


# ===================================================================
# HealthSnapshot model
# ===================================================================

class TestHealthSnapshotModel:

    def test_default_values(self):
        s = HealthSnapshot()
        assert s.ready is False
        assert s.onboarding_state == "UNKNOWN"
        assert s.local_llm_ready is False
        assert s.scheduler_running is False
        assert s.degraded_reasons == []

    def test_timestamp_auto_set(self):
        s = HealthSnapshot()
        assert s.timestamp is not None

    def test_custom_values(self):
        s = HealthSnapshot(
            ready=True,
            onboarding_state="READY",
            local_llm_ready=True,
            scheduler_running=True,
            degraded_reasons=[],
        )
        assert s.ready is True
        assert s.onboarding_state == "READY"
