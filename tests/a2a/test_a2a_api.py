# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""Unit tests for the A2A Management API endpoints."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "core"))

import pytest
from unittest.mock import MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import api_auth
from src.core.operator_identity import OperatorIdentity
from src.a2a.types import RemoteAgent, RemoteAgentStatus
from src.a2a import api as a2a_api
from src.a2a.api import router, init_a2a_api


# ── Fixtures ────────────────────────────────────────────────

def _make_agent(agent_id="agent-1", display_name="Agent 1", credentials_ref="", **kwargs):
    return RemoteAgent(
        agent_id=agent_id,
        display_name=display_name,
        credentials_ref=credentials_ref,
        **kwargs,
    )


def _make_live_soul(allow_inbound=True, allow_outbound=True, version="1.0.0"):
    soul = MagicMock()
    soul.version = version

    inbound = MagicMock()
    inbound.allow_inbound = allow_inbound
    inbound.allowed_callers = []
    soul.inbound_a2a_permissions = inbound

    outbound = MagicMock()
    outbound.allow_outbound = allow_outbound
    outbound.max_delegation_depth = 2
    outbound.allowed_targets = []
    soul.outbound_a2a_permissions = outbound
    return soul


@pytest.fixture
def mock_registry():
    registry = MagicMock()
    registry.list_agents.return_value = []
    registry.get.return_value = None
    registry.register.return_value = None
    registry.revoke.return_value = None
    registry.update.return_value = None
    return registry


@pytest.fixture
def mock_receipt_service():
    svc = MagicMock()
    svc.create.return_value = None
    svc.search.return_value = []
    return svc


@pytest.fixture
def mock_soul():
    soul = MagicMock()
    soul.version = "1.0.0"

    inbound = MagicMock()
    inbound.allow_inbound = True
    inbound.allowed_callers = []
    soul.inbound_a2a_permissions = inbound

    outbound = MagicMock()
    outbound.allow_outbound = True
    outbound.max_delegation_depth = 2
    outbound.allowed_targets = []
    soul.outbound_a2a_permissions = outbound

    return soul


@pytest.fixture
def mock_outbound_pipeline():
    pipeline = MagicMock()
    return pipeline


@pytest.fixture
def mock_a2a_client():
    client = MagicMock()
    return client


@pytest.fixture
def app(mock_registry, mock_receipt_service, mock_soul, mock_outbound_pipeline, mock_a2a_client):
    """Create a test FastAPI app with A2A API initialized."""
    test_app = FastAPI()
    test_app.include_router(router)

    init_a2a_api(
        registry=mock_registry,
        receipt_service=mock_receipt_service,
        soul=mock_soul,
        outbound_pipeline=mock_outbound_pipeline,
        a2a_client=mock_a2a_client,
    )

    yield test_app

    # Reset module-level state
    init_a2a_api(
        registry=None,
        receipt_service=None,
        soul=None,
        outbound_pipeline=None,
        a2a_client=None,
    )


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture(autouse=True)
def _auth_and_identity(monkeypatch):
    api_auth.init_api_auth(lambda request: True)
    identity = OperatorIdentity(
        operator_id="op-1",
        display_name="Operator One",
        session_id="sess-1",
        auth_method="api_key",
    )
    monkeypatch.setattr(a2a_api, "resolve_operator_identity", lambda request: None)
    monkeypatch.setattr(a2a_api, "get_api_key_identity", lambda request: identity)
    yield
    api_auth.init_api_auth(None)


# ── GET /status ─────────────────────────────────────────────

class TestGetStatus:
    def test_returns_status(self, client, mock_registry):
        mock_registry.list_agents.return_value = [_make_agent()]
        resp = client.get("/api/a2a/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["inbound_enabled"] is True
        assert data["outbound_enabled"] is True
        assert data["registered_agents"] == 1
        assert data["runtime_degraded"] is False
        assert data["registry_ready"] is True
        assert data["outbound_pipeline_ready"] is True
        assert data["client_ready"] is True

    def test_status_uses_live_soul_provider(self, mock_registry, mock_receipt_service, mock_outbound_pipeline, mock_a2a_client):
        soul_state = {"soul": _make_live_soul(True, True, "1.0.0")}
        app = FastAPI()
        app.include_router(router)
        init_a2a_api(
            registry=mock_registry,
            receipt_service=mock_receipt_service,
            soul=lambda: soul_state["soul"],
            outbound_pipeline=mock_outbound_pipeline,
            a2a_client=mock_a2a_client,
        )
        client = TestClient(app)

        first = client.get("/api/a2a/status")
        assert first.status_code == 200
        assert first.json()["outbound_enabled"] is True
        assert first.json()["soul_version"] == "1.0.0"

        soul_state["soul"] = _make_live_soul(False, False, "2.0.0")
        second = client.get("/api/a2a/status")
        assert second.status_code == 200
        assert second.json()["inbound_enabled"] is False
        assert second.json()["outbound_enabled"] is False
        assert second.json()["soul_version"] == "2.0.0"

    def test_status_surfaces_registry_failure_as_degraded(self, mock_registry, client):
        mock_registry.list_agents.side_effect = RuntimeError("registry exploded")

        resp = client.get("/api/a2a/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["runtime_degraded"] is True
        assert data["registered_agents"] == 0
        assert any("registry status unavailable" in reason.lower() for reason in data["degraded_reasons"])
        assert any("registry exploded" in err.lower() for err in data["runtime_errors"])

    def test_status_surfaces_live_soul_failure_as_degraded(self, mock_registry, mock_receipt_service, mock_outbound_pipeline, mock_a2a_client):
        app = FastAPI()
        app.include_router(router)
        init_a2a_api(
            registry=mock_registry,
            receipt_service=mock_receipt_service,
            soul=lambda: (_ for _ in ()).throw(RuntimeError("soul exploded")),
            outbound_pipeline=mock_outbound_pipeline,
            a2a_client=mock_a2a_client,
        )
        client = TestClient(app)

        resp = client.get("/api/a2a/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["runtime_degraded"] is True
        assert data["soul_version"] is None
        assert data["inbound_enabled"] is False
        assert data["outbound_enabled"] is False
        assert any("soul status unavailable" in reason.lower() for reason in data["degraded_reasons"])
        assert any("soul not loaded" in reason.lower() for reason in data["degraded_reasons"])
        assert any("soul exploded" in err.lower() for err in data["runtime_errors"])


# ── GET /agents ─────────────────────────────────────────────

class TestListAgents:
    def test_returns_agent_list(self, client, mock_registry):
        mock_registry.list_agents.return_value = [
            _make_agent("a1", "Agent 1"),
            _make_agent("a2", "Agent 2"),
        ]
        resp = client.get("/api/a2a/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["agents"]) == 2

    def test_credentials_redacted(self, client, mock_registry):
        mock_registry.list_agents.return_value = [
            _make_agent("a1", "Agent 1", credentials_ref="vault://secret"),
        ]
        resp = client.get("/api/a2a/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agents"][0]["credentials_ref"] == "[REDACTED]"

    def test_empty_credentials_not_redacted(self, client, mock_registry):
        mock_registry.list_agents.return_value = [
            _make_agent("a1", "Agent 1", credentials_ref=""),
        ]
        resp = client.get("/api/a2a/agents")
        data = resp.json()
        assert data["agents"][0]["credentials_ref"] == ""


# ── GET /agents/{id} ────────────────────────────────────────

class TestGetAgent:
    def test_returns_agent_detail(self, client, mock_registry, mock_receipt_service, mock_soul):
        agent = _make_agent()
        mock_registry.get.return_value = agent
        mock_receipt_service.search.return_value = []
        mock_soul.inbound_a2a_permissions.allowed_callers = []
        mock_soul.outbound_a2a_permissions.allowed_targets = []

        resp = client.get("/api/a2a/agents/agent-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_id"] == "agent-1"
        assert "card_status" in data

    def test_not_found_returns_404(self, client, mock_registry):
        mock_registry.get.return_value = None
        resp = client.get("/api/a2a/agents/nonexistent")
        assert resp.status_code == 404

    def test_credentials_redacted_in_detail(self, client, mock_registry, mock_receipt_service, mock_soul):
        agent = _make_agent(credentials_ref="vault://secret")
        mock_registry.get.return_value = agent
        mock_receipt_service.search.return_value = []
        mock_soul.inbound_a2a_permissions.allowed_callers = []
        mock_soul.outbound_a2a_permissions.allowed_targets = []

        resp = client.get("/api/a2a/agents/agent-1")
        data = resp.json()
        assert data["credentials_ref"] == "[REDACTED]"


# ── POST /agents ────────────────────────────────────────────

class TestRegisterAgent:
    def test_registers_new_agent(self, client, mock_registry, mock_receipt_service):
        mock_registry.get.return_value = None
        resp = client.post("/api/a2a/agents", json={
            "agent_id": "new-agent",
            "display_name": "New Agent",
            "agent_card_url": "https://new.example.com/.well-known/agent.json",
        })
        assert resp.status_code == 200

    def test_register_rejects_unexpected_fields(self, client):
        resp = client.post("/api/a2a/agents", json={
            "agent_id": "new-agent",
            "display_name": "New Agent",
            "unexpected": "deny-me",
        })
        assert resp.status_code == 422


class TestDelegateTask:
    def test_delegate_forwards_authenticated_identity(self, client, mock_outbound_pipeline):
        mock_outbound_pipeline.delegate.return_value = type(
            "Result",
            (),
            {"success": True, "to_dict": lambda self: {"success": True, "task_id": "task-1"}},
        )()

        resp = client.post(
            "/api/a2a/delegate",
            json={"target_agent_id": "agent-1", "content": "Investigate", "task_type": "general"},
        )

        assert resp.status_code == 200
        mock_outbound_pipeline.delegate.assert_called_once_with(
            target_agent_id="agent-1",
            task_content="Investigate",
            task_type="general",
            operator_id="op-1",
            session_id="sess-1",
        )
        data = resp.json()
        assert data["agent_id"] == "new-agent"
        assert data["status"] == "registered"
        mock_registry.register.assert_called_once()
        receipt = mock_receipt_service.create.call_args.args[0]
        assert receipt.operator_id == "op-1"
        assert receipt.session_id == "sess-1"

    def test_duplicate_returns_409(self, client, mock_registry):
        mock_registry.get.return_value = _make_agent("dup-agent")
        resp = client.post("/api/a2a/agents", json={
            "agent_id": "dup-agent",
            "display_name": "Duplicate",
        })
        assert resp.status_code == 409


# ── DELETE /agents/{id} ────────────────────────────────────

class TestRevokeAgent:
    def test_revokes_agent(self, client, mock_registry):
        mock_registry.get.return_value = _make_agent()
        resp = client.delete("/api/a2a/agents/agent-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "revoked"
        mock_registry.revoke.assert_called_with("agent-1")

    def test_revoke_not_found_returns_404(self, client, mock_registry):
        mock_registry.get.return_value = None
        resp = client.delete("/api/a2a/agents/nonexistent")
        assert resp.status_code == 404


# ── POST /agents/{id}/verify ──────────────────────────────

class TestVerifyAgentCard:
    def test_triggers_verification(self, client, mock_registry, mock_a2a_client):
        agent = _make_agent()
        mock_registry.get.return_value = agent
        mock_a2a_client.verify_agent_card.return_value = True

        resp = client.post("/api/a2a/agents/agent-1/verify")
        assert resp.status_code == 200
        data = resp.json()
        assert data["verified"] is True
        mock_registry.update.assert_called_once()

    def test_verify_not_found_returns_404(self, client, mock_registry):
        mock_registry.get.return_value = None
        resp = client.post("/api/a2a/agents/nonexistent/verify")
        assert resp.status_code == 404


# ── GET /card ───────────────────────────────────────────────

class TestGetOwnCard:
    @patch("src.a2a.agent_card.generate_agent_card")
    def test_returns_own_agent_card(self, mock_gen, client, mock_soul):
        from src.a2a.types import AgentCard
        mock_gen.return_value = AgentCard(
            name="Lancelot", description="Test", url="http://localhost",
        )
        resp = client.get("/api/a2a/card")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Lancelot"


# ── POST /card/regenerate ──────────────────────────────────

class TestRegenerateCard:
    @patch("src.a2a.agent_card.generate_agent_card")
    @patch("src.a2a.agent_card.invalidate_card")
    def test_regenerates_card(self, mock_invalidate, mock_gen, client, mock_soul, mock_receipt_service):
        from src.a2a.types import AgentCard, AgentCardSkill
        mock_gen.return_value = AgentCard(
            name="Lancelot", description="Test", url="http://localhost",
            skills=[AgentCardSkill(id="chat", name="Chat")],
        )
        resp = client.post("/api/a2a/card/regenerate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "regenerated"
        assert data["skills_count"] == 1
        mock_invalidate.assert_called_once()
        receipt = mock_receipt_service.create.call_args.args[0]
        assert receipt.operator_id == "op-1"
        assert receipt.session_id == "sess-1"


# ── POST /delegate ──────────────────────────────────────────

class TestDelegateTask:
    def test_successful_delegation(self, client, mock_outbound_pipeline):
        from src.a2a.outbound_pipeline import DelegationResult
        mock_outbound_pipeline.delegate.return_value = DelegationResult(
            success=True,
            task_id="t1",
            target_agent_id="target",
            status="completed",
        )
        resp = client.post("/api/a2a/delegate", json={
            "target_agent_id": "target",
            "content": "Do something",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_failed_delegation_returns_error(self, client, mock_outbound_pipeline):
        from src.a2a.outbound_pipeline import DelegationResult
        mock_outbound_pipeline.delegate.return_value = DelegationResult(
            success=False,
            error="Target not registered",
            block_reason="AGENT_NOT_REGISTERED",
        )
        resp = client.post("/api/a2a/delegate", json={
            "target_agent_id": "unknown",
            "content": "Do something",
        })
        assert resp.status_code == 403

    def test_delegate_rejects_unexpected_fields(self, client):
        resp = client.post("/api/a2a/delegate", json={
            "target_agent_id": "target",
            "content": "Do something",
            "unexpected": "deny-me",
        })
        assert resp.status_code == 422


# ── GET /receipts ───────────────────────────────────────────

class TestListReceipts:
    def test_returns_receipts(self, client, mock_receipt_service):
        mock_receipt = MagicMock()
        mock_receipt.to_dict.return_value = {"id": "r1", "action_type": "a2a_task_received"}
        mock_receipt_service.search.return_value = [mock_receipt]

        resp = client.get("/api/a2a/receipts")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["receipts"]) == 1


# ── Feature flag disabled ──────────────────────────────────

class TestRegistryNotInitialized:
    def test_agents_503_without_registry(self, mock_receipt_service, mock_soul):
        test_app = FastAPI()
        test_app.include_router(router)
        init_a2a_api(
            registry=None,
            receipt_service=mock_receipt_service,
            soul=mock_soul,
        )
        tc = TestClient(test_app)
        resp = tc.get("/api/a2a/agents")
        assert resp.status_code == 503

        # Cleanup
        init_a2a_api(registry=None, receipt_service=None, soul=None)
