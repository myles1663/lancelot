"""Tests for HIVE API Router."""

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

from src.hive.api import router, init_hive_api, shutdown_hive_api
from src.hive.types import AgentState, TaskSpec
from src.hive.config import HiveConfig
from src.hive.registry import AgentRegistry
from src.hive.receipt_manager import HiveReceiptManager
from src.hive.scoped_soul import ScopedSoulGenerator
from src.hive.lifecycle import AgentLifecycleManager


@pytest.fixture(autouse=True)
def reset_receipt_service():
    """Reset receipt service singleton in ALL module references."""
    import sys
    modules_to_reset = []
    for mod_name in ("src.shared.receipts", "receipts"):
        mod = sys.modules.get(mod_name)
        if mod and hasattr(mod, "_service_instance"):
            modules_to_reset.append((mod, mod._service_instance))
            mod._service_instance = None
    yield
    for mod, old_val in modules_to_reset:
        mod._service_instance = old_val


@pytest.fixture
def config():
    return HiveConfig(max_concurrent_agents=5)


@pytest.fixture
def registry():
    return AgentRegistry(max_concurrent_agents=5)


@pytest.fixture
def receipt_mgr(tmp_path):
    return HiveReceiptManager(data_dir=str(tmp_path))


@pytest.fixture
def lifecycle(config, registry, receipt_mgr):
    def executor(action):
        return {"result": "ok"}
    mgr = AgentLifecycleManager(
        config=config,
        registry=registry,
        receipt_manager=receipt_mgr,
        soul_generator=ScopedSoulGenerator(),
        action_executor=executor,
    )
    yield mgr
    mgr.shutdown()


@pytest.fixture
def app():
    """Create a test FastAPI app with the HIVE router."""
    test_app = FastAPI()
    test_app.include_router(router)
    return test_app


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def initialized_client(app, config, registry, receipt_mgr, lifecycle):
    """Client with HIVE API initialized."""
    init_hive_api(
        architect=None,  # Most tests don't need architect
        lifecycle=lifecycle,
        registry=registry,
        receipt_mgr=receipt_mgr,
        config=config,
    )
    yield TestClient(app)
    shutdown_hive_api()


class TestStatusEndpoint:
    def test_status_not_initialized(self, client):
        resp = client.get("/api/hive/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "not_initialized"
        assert data["enabled"] is False

    def test_status_returns_valid_json(self, initialized_client):
        resp = initialized_client.get("/api/hive/status")
        assert resp.status_code == 200
        # Without architect, still returns valid structure
        data = resp.json()
        assert data["status"] == "not_initialized"


class TestRosterEndpoint:
    def test_roster_empty(self, initialized_client):
        resp = initialized_client.get("/api/hive/roster")
        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] == []
        assert data["archived"] == []

    def test_roster_with_agents(self, initialized_client, lifecycle, registry):
        lifecycle.spawn(TaskSpec(description="Test task"))
        resp = initialized_client.get("/api/hive/roster")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["active"]) == 1
        assert data["active"][0]["state"] == "ready"

    def test_roster_not_initialized(self, client):
        resp = client.get("/api/hive/roster")
        assert resp.status_code == 503


class TestAgentsEndpoint:
    def test_list_active_agents(self, initialized_client, lifecycle):
        lifecycle.spawn(TaskSpec(description="Agent 1"))
        lifecycle.spawn(TaskSpec(description="Agent 2"))
        resp = initialized_client.get("/api/hive/agents")
        assert resp.status_code == 200
        assert len(resp.json()["agents"]) == 2

    def test_get_agent_by_id(self, initialized_client, lifecycle):
        record = lifecycle.spawn(TaskSpec(description="Test"))
        resp = initialized_client.get(f"/api/hive/agents/{record.agent_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_id"] == record.agent_id
        assert data["state"] == "ready"

    def test_get_agent_not_found(self, initialized_client):
        resp = initialized_client.get("/api/hive/agents/nonexistent")
        assert resp.status_code == 404

    def test_agent_history(self, initialized_client, lifecycle):
        record = lifecycle.spawn(TaskSpec())
        future = lifecycle.execute(record.agent_id, [{"action": "done"}])
        future.result(timeout=5)
        resp = initialized_client.get("/api/hive/agents/history")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["agents"]) >= 1


class TestAgentControlEndpoints:
    def test_kill_agent(self, initialized_client, lifecycle):
        record = lifecycle.spawn(TaskSpec())
        resp = initialized_client.post(
            f"/api/hive/agents/{record.agent_id}/kill",
            json={"reason": "Test kill"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "killed"

    def test_kill_requires_reason(self, initialized_client, lifecycle):
        record = lifecycle.spawn(TaskSpec())
        resp = initialized_client.post(
            f"/api/hive/agents/{record.agent_id}/kill",
            json={"reason": ""},
        )
        assert resp.status_code == 422  # Validation error (min_length=1)

    def test_kill_not_found(self, initialized_client):
        resp = initialized_client.post(
            "/api/hive/agents/nonexistent/kill",
            json={"reason": "Test"},
        )
        assert resp.status_code == 404

    def test_kill_all(self, initialized_client, lifecycle):
        for _ in range(3):
            lifecycle.spawn(TaskSpec())
        resp = initialized_client.post(
            "/api/hive/kill-all",
            json={"reason": "Emergency"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "killed_all"


class TestInterventionEndpoints:
    def test_get_interventions_by_quest_empty(self, initialized_client):
        """Querying interventions for a non-existent quest returns empty."""
        resp = initialized_client.get("/api/hive/interventions/nonexistent-quest")
        assert resp.status_code == 200
        assert resp.json()["interventions"] == []

    def test_get_interventions_after_kill(self, initialized_client, lifecycle):
        record = lifecycle.spawn(TaskSpec())
        lifecycle.kill(record.agent_id, "Test reason")
        resp = initialized_client.get("/api/hive/interventions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["interventions"]) >= 1
