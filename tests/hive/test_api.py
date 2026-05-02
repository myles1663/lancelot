"""Tests for HIVE API Router."""

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

from unittest.mock import AsyncMock, patch

from src.core import api_auth, auth_api
from src.core.operator_identity import OperatorIdentity
from src.hive import api as hive_api
from src.hive.api import router, init_hive_api, shutdown_hive_api
from src.hive.types import AgentState, TaskSpec
from src.hive.config import HiveConfig
from src.hive.registry import AgentRegistry
from src.hive.receipt_manager import HiveReceiptManager
from src.hive.scoped_soul import ScopedSoulGenerator
from src.hive.lifecycle import AgentLifecycleManager
from src.core.runtime_pause import init_runtime_pause, pause_runtime, resume_runtime


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


@pytest.fixture(autouse=True)
def reset_runtime_pause_state(tmp_path):
    """Keep HIVE API tests isolated from persisted global runtime pause state."""
    init_runtime_pause(str(tmp_path))
    resume_runtime(operator_id="test-op", operator_name="Test", session_id="test-session")
    yield
    resume_runtime(operator_id="test-op", operator_name="Test", session_id="test-session")


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
    api_auth.init_api_auth(lambda request: True)
    auth_api._sessions.clear()
    auth_api._sessions["hive-test-session"] = {
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
        "capabilities": sorted({"warroom.login", "hive.admin"}),
        "groups": [],
    }
    test_app = FastAPI()
    test_app.include_router(router)
    return test_app


@pytest.fixture
def client(app):
    client = TestClient(app)
    client.cookies.set(auth_api.get_warroom_session_cookie_name(), "hive-test-session")
    return client


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
    with patch("src.hive.api._resolve_operator_ids", return_value=("test-op", "test-sess")):
        client = TestClient(app)
        client.cookies.set(auth_api.get_warroom_session_cookie_name(), "hive-test-session")
        yield client
    shutdown_hive_api()


class TestStatusEndpoint:
    def test_status_not_initialized(self, client):
        resp = client.get("/api/hive/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "not_initialized"
        assert data["enabled"] is False
        assert data["runtime_degraded"] is True
        assert data["architect_ready"] is False
        assert any("architect not initialized" in reason.lower() for reason in data["degraded_reasons"])

    def test_status_degrades_when_architect_status_fails(self, app, config, registry, receipt_mgr, lifecycle):
        class _BrokenArchitect:
            def get_status(self):
                raise RuntimeError("architect exploded")

        init_hive_api(
            architect=_BrokenArchitect(),
            lifecycle=lifecycle,
            registry=registry,
            receipt_mgr=receipt_mgr,
            config=config,
        )
        client = TestClient(app)
        client.cookies.set(auth_api.get_warroom_session_cookie_name(), "hive-test-session")

        try:
            resp = client.get("/api/hive/status")
            assert resp.status_code == 200
            data = resp.json()
            assert data["runtime_degraded"] is True
            assert data["status"] == "error"
            assert any("architect status unavailable" in reason.lower() for reason in data["degraded_reasons"])
            assert any("architect exploded" in err.lower() for err in data["runtime_errors"])
        finally:
            shutdown_hive_api()

    def test_status_degrades_when_registry_status_fails(self, app, config, receipt_mgr, lifecycle):
        class _Architect:
            def get_status(self):
                return {"status": "ready"}

        class _BrokenRegistry:
            def active_count(self):
                raise RuntimeError("registry exploded")

        init_hive_api(
            architect=_Architect(),
            lifecycle=lifecycle,
            registry=_BrokenRegistry(),
            receipt_mgr=receipt_mgr,
            config=config,
        )
        client = TestClient(app)
        client.cookies.set(auth_api.get_warroom_session_cookie_name(), "hive-test-session")

        try:
            resp = client.get("/api/hive/status")
            assert resp.status_code == 200
            data = resp.json()
            assert data["runtime_degraded"] is True
            assert any("registry status unavailable" in reason.lower() for reason in data["degraded_reasons"])
            assert any("registry exploded" in err.lower() for err in data["runtime_errors"])
        finally:
            shutdown_hive_api()


class TestTaskSubmission:
    def test_submit_task_not_initialized(self, client):
        response = client.post("/api/hive/tasks", json={"goal": "Investigate issue"})
        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_submit_task_forwards_authenticated_operator_context(self, app):
        architect = type("Architect", (), {})()
        architect.execute_task = AsyncMock(return_value={"quest_id": "quest-1", "success": True})

        init_hive_api(
            architect=architect,
            lifecycle=None,
            registry=None,
            receipt_mgr=None,
            config=HiveConfig(max_concurrent_agents=5),
        )
        client = TestClient(app)
        client.cookies.set(auth_api.get_warroom_session_cookie_name(), "hive-test-session")

        try:
            response = client.post("/api/hive/tasks", json={"goal": "Investigate issue", "context": {"ticket": "INC-1"}})
            assert response.status_code == 200
            architect.execute_task.assert_awaited_once_with(
                "Investigate issue",
                {"ticket": "INC-1"},
                operator_id="op-arthur",
                session_id="session-1",
                operator_name="Arthur",
            )
        finally:
            shutdown_hive_api()

    def test_task_receipt_tree_endpoints(self, initialized_client, receipt_mgr):
        response = initialized_client.get("/api/hive/tasks/quest-1")
        assert response.status_code == 200
        assert response.json()["quest_id"] == "quest-1"

        tree = initialized_client.get("/api/hive/tasks/quest-1/tree")
        assert tree.status_code == 200
        assert tree.json()["quest_id"] == "quest-1"

    def test_task_receipt_tree_not_initialized(self, client):
        assert client.get("/api/hive/tasks/quest-1").status_code == 503
        assert client.get("/api/hive/tasks/quest-1/tree").status_code == 503

    def test_submit_task_blocked_while_runtime_paused(self, app, tmp_path):
        architect = type("Architect", (), {})()
        architect.execute_task = AsyncMock(return_value={"quest_id": "quest-1", "success": True})

        init_runtime_pause(str(tmp_path))
        pause_runtime("Paused for maintenance", operator_id="op-1", operator_name="Arthur", session_id="session-1")
        init_hive_api(
            architect=architect,
            lifecycle=None,
            registry=None,
            receipt_mgr=None,
            config=HiveConfig(max_concurrent_agents=5),
        )
        client = TestClient(app)
        client.cookies.set(auth_api.get_warroom_session_cookie_name(), "hive-test-session")

        try:
            response = client.post("/api/hive/tasks", json={"goal": "Investigate issue"})
            assert response.status_code == 423
            architect.execute_task.assert_not_called()
        finally:
            resume_runtime(operator_id="op-1", operator_name="Arthur", session_id="session-1")
            shutdown_hive_api()

    def test_status_returns_valid_json(self, initialized_client):
        resp = initialized_client.get("/api/hive/status")
        assert resp.status_code == 200
        # Without architect, still returns valid structure
        data = resp.json()
        assert data["status"] == "not_initialized"
        assert data["runtime_degraded"] is True

    def test_submit_task_rejects_unexpected_fields(self, app):
        architect = type("Architect", (), {})()
        architect.execute_task = AsyncMock(return_value={"quest_id": "quest-1", "success": True})

        init_hive_api(
            architect=architect,
            lifecycle=None,
            registry=None,
            receipt_mgr=None,
            config=HiveConfig(max_concurrent_agents=5),
        )
        client = TestClient(app)
        client.cookies.set(auth_api.get_warroom_session_cookie_name(), "hive-test-session")

        try:
            response = client.post(
                "/api/hive/tasks",
                json={
                    "goal": "Investigate issue",
                    "context": {"ticket": "INC-1"},
                    "operator_id": "spoofed-operator",
                },
            )
            assert response.status_code == 422
            architect.execute_task.assert_not_called()
        finally:
            shutdown_hive_api()

    def test_submit_task_rejects_malformed_context_type(self, app):
        architect = type("Architect", (), {})()
        architect.execute_task = AsyncMock(return_value={"quest_id": "quest-1", "success": True})

        init_hive_api(
            architect=architect,
            lifecycle=None,
            registry=None,
            receipt_mgr=None,
            config=HiveConfig(max_concurrent_agents=5),
        )
        client = TestClient(app)
        client.cookies.set(auth_api.get_warroom_session_cookie_name(), "hive-test-session")

        try:
            response = client.post(
                "/api/hive/tasks",
                json={"goal": "Investigate issue", "context": ["not", "a", "dict"]},
            )
            assert response.status_code == 422
            architect.execute_task.assert_not_called()
        finally:
            shutdown_hive_api()


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

    def test_uninitialized_agent_collection_endpoints(self, client):
        assert client.get("/api/hive/agents").status_code == 503
        assert client.get("/api/hive/agents/history").status_code == 503
        assert client.get("/api/hive/agents/agent-1").status_code == 503

    def test_agent_soul_endpoint_variants(self, app, config, registry, receipt_mgr):
        class _SoulModelDump:
            def model_dump(self):
                return {"mission": "model_dump"}

        class _SoulDict:
            def dict(self):
                return {"mission": "dict"}

        class _Lifecycle:
            def __init__(self, runtime):
                self.runtime = runtime

            def get_runtime(self, agent_id):
                return self.runtime

        client = TestClient(app)
        client.cookies.set(auth_api.get_warroom_session_cookie_name(), "hive-test-session")

        init_hive_api(None, None, registry, receipt_mgr, config)
        try:
            assert client.get("/api/hive/agents/agent-1/soul").status_code == 503
        finally:
            shutdown_hive_api()

        init_hive_api(None, _Lifecycle(None), registry, receipt_mgr, config)
        try:
            assert client.get("/api/hive/agents/agent-1/soul").json()["note"] == "No active runtime"
        finally:
            shutdown_hive_api()

        init_hive_api(None, _Lifecycle(object()), registry, receipt_mgr, config)
        try:
            assert client.get("/api/hive/agents/agent-1/soul").json() == {"agent_id": "agent-1", "soul": None}
        finally:
            shutdown_hive_api()

        for soul, expected in [(_SoulModelDump(), {"mission": "model_dump"}), (_SoulDict(), {"mission": "dict"}), ("plain-soul", "plain-soul")]:
            runtime = type("Runtime", (), {"_scoped_soul": soul})()
            init_hive_api(None, _Lifecycle(runtime), registry, receipt_mgr, config)
            try:
                assert client.get("/api/hive/agents/agent-1/soul").json()["soul"] == expected
            finally:
                shutdown_hive_api()


class TestAgentControlEndpoints:
    def test_pause_and_resume_agent(self, initialized_client, lifecycle, monkeypatch):
        monkeypatch.setattr(
            "src.core.governance_receipts.emit_governance_receipt",
            lambda *args, **kwargs: object(),
        )
        record = lifecycle.spawn(TaskSpec())
        pause = initialized_client.post(
            f"/api/hive/agents/{record.agent_id}/pause",
            json={"reason": "Operator pause"},
        )
        assert pause.status_code == 200
        assert pause.json()["status"] == "paused"

        resume = initialized_client.post(f"/api/hive/agents/{record.agent_id}/resume")
        assert resume.status_code == 200
        assert resume.json()["status"] == "resumed"

    def test_pause_and_resume_not_found_or_uninitialized(self, initialized_client, client, monkeypatch):
        monkeypatch.setattr(
            "src.core.governance_receipts.emit_governance_receipt",
            lambda *args, **kwargs: object(),
        )
        assert initialized_client.post("/api/hive/agents/missing/pause", json={"reason": "x"}).status_code == 404
        assert initialized_client.post("/api/hive/agents/missing/resume").status_code == 404
        shutdown_hive_api()
        assert client.post("/api/hive/agents/missing/pause", json={"reason": "x"}).status_code == 503
        assert client.post("/api/hive/agents/missing/resume").status_code == 503
        assert client.post("/api/hive/agents/missing/kill", json={"reason": "x"}).status_code == 503
        assert client.post("/api/hive/kill-all", json={"reason": "x"}).status_code == 503

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

    def test_kill_rejects_unexpected_fields(self, initialized_client, lifecycle):
        record = lifecycle.spawn(TaskSpec())
        resp = initialized_client.post(
            f"/api/hive/agents/{record.agent_id}/kill",
            json={"reason": "Test kill", "operator_id": "spoofed"},
        )
        assert resp.status_code == 422

    def test_kill_all(self, initialized_client, lifecycle):
        for _ in range(3):
            lifecycle.spawn(TaskSpec())
        resp = initialized_client.post(
            "/api/hive/kill-all",
            json={"reason": "Emergency"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "killed_all"

    def test_kill_all_rejects_unexpected_fields(self, initialized_client, lifecycle):
        for _ in range(2):
            lifecycle.spawn(TaskSpec())
        resp = initialized_client.post(
            "/api/hive/kill-all",
            json={"reason": "Emergency", "collapsed": ["spoofed"]},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_modify_agent_forwards_operator_context_and_emits_receipt(self, app, config, monkeypatch):
        architect = type("Architect", (), {})()
        architect.handle_intervention = AsyncMock(return_value={"modified": True})
        receipts = []
        monkeypatch.setattr(
            "src.core.governance_receipts.emit_governance_receipt",
            lambda request, action_type, action_name, inputs=None, **kwargs: receipts.append((action_name, inputs)),
        )
        init_hive_api(architect=architect, lifecycle=None, registry=None, receipt_mgr=None, config=config)
        client = TestClient(app)
        client.cookies.set(auth_api.get_warroom_session_cookie_name(), "hive-test-session")
        try:
            response = client.post(
                "/api/hive/agents/agent-1/modify",
                json={"reason": "Need tighter scope", "feedback": "reduce scope", "constraints": {"max_steps": 2}},
            )
            assert response.status_code == 200
            assert response.json() == {"modified": True}
            call = architect.handle_intervention.await_args
            intervention = call.args[0]
            assert intervention.agent_id == "agent-1"
            assert intervention.reason == "Need tighter scope"
            assert call.kwargs == {"operator_id": "op-arthur", "session_id": "session-1"}
            assert receipts == [("modify_agent", {"agent_id": "agent-1", "reason": "Need tighter scope"})]
        finally:
            shutdown_hive_api()

    def test_modify_agent_not_initialized(self, client):
        assert client.post("/api/hive/agents/agent-1/modify", json={"reason": "x"}).status_code == 503


class TestInterventionEndpoints:
    def test_get_interventions_by_quest_empty(self, initialized_client):
        """Querying interventions for a non-existent quest returns empty."""
        resp = initialized_client.get("/api/hive/interventions/nonexistent-quest")
        assert resp.status_code == 200
        assert resp.json()["interventions"] == []

    def test_get_interventions_after_kill(self, initialized_client, lifecycle):
        record = lifecycle.spawn(TaskSpec())
        lifecycle.kill(record.agent_id, "Test reason", operator_id="test-op", session_id="test-sess")
        resp = initialized_client.get("/api/hive/interventions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["interventions"]) >= 1

    def test_interventions_not_initialized(self, client):
        assert client.get("/api/hive/interventions").status_code == 503
        assert client.get("/api/hive/interventions/quest-1").status_code == 503


def test_operator_context_helpers_and_audit_fallback(monkeypatch):
    identity = OperatorIdentity(
        operator_id="op-1",
        display_name="Arthur",
        session_id="session-1",
        session_started_at="2026-04-10T00:00:00Z",
        auth_method="local",
        ip_address="127.0.0.1",
    )
    monkeypatch.setattr("src.core.governance_receipts._resolve_identity", lambda request: identity)
    assert hive_api._resolve_operator_context(object()) == ("op-1", "session-1", "Arthur")
    assert hive_api._resolve_operator_ids(object()) == ("op-1", "session-1")

    monkeypatch.setattr(
        "src.core.governance_receipts._resolve_identity",
        lambda request: (_ for _ in ()).throw(RuntimeError("identity failed")),
    )
    assert hive_api._resolve_operator_context(object()) == (None, None, None)

    class _BrokenAudit:
        def log_event(self, *args, **kwargs):
            raise RuntimeError("audit failed")

    hive_api.init_hive_api(None, None, None, None, None, audit_logger=_BrokenAudit())
    hive_api._hive_audit("HIVE_TEST", "details", actor="Arthur")
    hive_api.shutdown_hive_api()


def test_receipt_to_dict_fallback():
    receipt = type(
        "Receipt",
        (),
        {
            "id": "r1",
            "action_type": "hive",
            "action_name": "pause",
            "inputs": {"agent_id": "a1"},
            "status": "success",
            "metadata": {"m": 1},
            "created_at": "2026-04-10T00:00:00Z",
        },
    )()
    assert hive_api._receipt_to_dict(receipt)["inputs"] == {"agent_id": "a1"}
    custom = type("ReceiptWithDict", (), {"to_dict": lambda self: {"id": "custom"}})()
    assert hive_api._receipt_to_dict(custom) == {"id": "custom"}
