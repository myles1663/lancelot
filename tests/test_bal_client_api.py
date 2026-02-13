"""
Tests for BAL Client REST API (Step 2E).

Uses FastAPI TestClient with real database — no mocks.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.bal.clients.api import router, init_client_api
from src.core.bal.clients.models import ClientStatus, PlanTier
from src.core.bal.clients.repository import ClientRepository
from src.core.bal.database import BALDatabase


# ===================================================================
# Fixtures
# ===================================================================

@pytest.fixture
def bal_db(tmp_path):
    db = BALDatabase(data_dir=str(tmp_path))
    yield db
    db.close()


@pytest.fixture
def app_client(bal_db, monkeypatch):
    """Create a FastAPI TestClient with the client router mounted."""
    # Ensure BAL feature flag passes at module level
    import src.core.feature_flags as ff
    monkeypatch.setattr(ff, "FEATURE_BAL", True)

    repo = ClientRepository(bal_db)
    init_client_api(repo)

    app = FastAPI()
    app.include_router(router)

    return TestClient(app)


# ===================================================================
# Tests
# ===================================================================

class TestCreateClient:
    def test_create_201(self, app_client):
        resp = app_client.post("/api/v1/clients", json={
            "name": "New Corp",
            "email": "new@corp.com",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "New Corp"
        assert data["email"] == "new@corp.com"
        assert data["status"] == "onboarding"
        assert data["plan_tier"] == "starter"
        assert "id" in data

    def test_create_with_tier(self, app_client):
        resp = app_client.post("/api/v1/clients", json={
            "name": "Growth Co",
            "email": "growth@co.com",
            "plan_tier": "growth",
        })
        assert resp.status_code == 201
        assert resp.json()["plan_tier"] == "growth"

    def test_create_duplicate_email_409(self, app_client):
        app_client.post("/api/v1/clients", json={
            "name": "First",
            "email": "dupe@test.com",
        })
        resp = app_client.post("/api/v1/clients", json={
            "name": "Second",
            "email": "dupe@test.com",
        })
        assert resp.status_code == 409

    def test_create_invalid_email_422(self, app_client):
        resp = app_client.post("/api/v1/clients", json={
            "name": "Bad",
            "email": "not-an-email",
        })
        assert resp.status_code == 422


class TestListClients:
    def test_list_empty(self, app_client):
        resp = app_client.get("/api/v1/clients")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["clients"] == []

    def test_list_multiple(self, app_client):
        app_client.post("/api/v1/clients", json={"name": "A", "email": "a@a.com"})
        app_client.post("/api/v1/clients", json={"name": "B", "email": "b@b.com"})
        resp = app_client.get("/api/v1/clients")
        assert resp.json()["total"] == 2

    def test_list_filter_by_status(self, app_client):
        # Create two clients
        r1 = app_client.post("/api/v1/clients", json={"name": "A", "email": "a@a.com"})
        r2 = app_client.post("/api/v1/clients", json={"name": "B", "email": "b@b.com"})
        cid = r1.json()["id"]
        # Activate one
        app_client.post(f"/api/v1/clients/{cid}/activate")

        # Filter active
        resp = app_client.get("/api/v1/clients?status=active")
        assert resp.json()["total"] == 1

        # Filter onboarding
        resp = app_client.get("/api/v1/clients?status=onboarding")
        assert resp.json()["total"] == 1


class TestGetClient:
    def test_get_200(self, app_client):
        r = app_client.post("/api/v1/clients", json={"name": "Get Me", "email": "get@me.com"})
        cid = r.json()["id"]
        resp = app_client.get(f"/api/v1/clients/{cid}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Get Me"

    def test_get_404(self, app_client):
        resp = app_client.get("/api/v1/clients/nonexistent-id")
        assert resp.status_code == 404


class TestUpdateClient:
    def test_update_name(self, app_client):
        r = app_client.post("/api/v1/clients", json={"name": "Orig", "email": "orig@test.com"})
        cid = r.json()["id"]
        resp = app_client.patch(f"/api/v1/clients/{cid}", json={"name": "Updated"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated"

    def test_update_preferences(self, app_client):
        r = app_client.post("/api/v1/clients", json={"name": "Prefs", "email": "prefs@test.com"})
        cid = r.json()["id"]
        resp = app_client.patch(f"/api/v1/clients/{cid}", json={
            "preferences": {"tone": "witty", "brand_voice_notes": "Be funny"},
        })
        assert resp.status_code == 200
        assert resp.json()["preferences"]["tone"] == "witty"

    def test_update_404(self, app_client):
        resp = app_client.patch("/api/v1/clients/fake-id", json={"name": "X"})
        assert resp.status_code == 404


class TestClientLifecycleEndpoints:
    def test_activate(self, app_client):
        r = app_client.post("/api/v1/clients", json={"name": "Activate", "email": "act@test.com"})
        cid = r.json()["id"]
        resp = app_client.post(f"/api/v1/clients/{cid}/activate")
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

    def test_pause(self, app_client):
        r = app_client.post("/api/v1/clients", json={"name": "Pause", "email": "pause@test.com"})
        cid = r.json()["id"]
        # Must activate first
        app_client.post(f"/api/v1/clients/{cid}/activate")
        resp = app_client.post(f"/api/v1/clients/{cid}/pause", json={"reason": "vacation"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "paused"

    def test_resume(self, app_client):
        r = app_client.post("/api/v1/clients", json={"name": "Resume", "email": "resume@test.com"})
        cid = r.json()["id"]
        app_client.post(f"/api/v1/clients/{cid}/activate")
        app_client.post(f"/api/v1/clients/{cid}/pause", json={"reason": "break"})
        resp = app_client.post(f"/api/v1/clients/{cid}/resume")
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

    def test_invalid_transition_422(self, app_client):
        r = app_client.post("/api/v1/clients", json={"name": "Bad", "email": "bad@test.com"})
        cid = r.json()["id"]
        # ONBOARDING -> PAUSED is invalid
        resp = app_client.post(f"/api/v1/clients/{cid}/pause")
        assert resp.status_code == 422

    def test_activate_404(self, app_client):
        resp = app_client.post("/api/v1/clients/fake-id/activate")
        assert resp.status_code == 404

    def test_full_lifecycle(self, app_client):
        """Full lifecycle: create -> activate -> pause -> resume."""
        r = app_client.post("/api/v1/clients", json={
            "name": "Lifecycle Co",
            "email": "life@cycle.com",
            "plan_tier": "growth",
        })
        assert r.status_code == 201
        cid = r.json()["id"]

        # Verify exists
        r = app_client.get(f"/api/v1/clients/{cid}")
        assert r.status_code == 200
        assert r.json()["status"] == "onboarding"

        # Update preferences
        r = app_client.patch(f"/api/v1/clients/{cid}", json={
            "preferences": {"tone": "casual"},
        })
        assert r.status_code == 200

        # Activate
        r = app_client.post(f"/api/v1/clients/{cid}/activate")
        assert r.json()["status"] == "active"

        # Pause
        r = app_client.post(f"/api/v1/clients/{cid}/pause", json={"reason": "holiday"})
        assert r.json()["status"] == "paused"

        # Resume
        r = app_client.post(f"/api/v1/clients/{cid}/resume")
        assert r.json()["status"] == "active"
