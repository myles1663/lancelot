# Lancelot - A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""Protocol endpoint tests for the hardened A2A server."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.a2a.inbound_pipeline import InboundPipeline
from src.a2a.registry import A2ARegistry
from src.a2a import server as a2a_server
from src.a2a.server import _active_tasks, a2a_server_router, init_a2a_server
from src.a2a.types import RemoteAgent
from src.core.runtime_pause import init_runtime_pause, pause_runtime, resume_runtime


def _make_soul():
    soul = MagicMock()
    soul.version = "1.0.0"
    inbound = MagicMock()
    inbound.allow_inbound = True
    inbound.default_trust_tier = "T2"
    inbound.allowed_callers = []
    inbound.blocked_callers = []
    inbound.require_preregistration = True
    inbound.require_agent_card = True
    soul.inbound_a2a_permissions = inbound
    return soul


def _make_app(data_dir: str = "/tmp"):
    registry = MagicMock(spec=A2ARegistry)
    agent = RemoteAgent(
        agent_id="peer-1",
        display_name="Peer One",
        auth_type="bearer_token",
        credentials_ref="a2a.peer-1",
        agent_card_url="https://peer.example.com/.well-known/agent.json",
        direction="inbound",
    )
    registry.get.side_effect = lambda agent_id: agent if agent_id == "peer-1" else None
    registry.update_interaction.return_value = None

    vault = MagicMock()
    vault.retrieve.side_effect = lambda key: "secret123"

    client = MagicMock()
    client.fetch_agent_card.return_value = MagicMock()
    client.is_lancelot_instance.return_value = False
    client.assess_agent_card.return_value = {"allowed": True, "card": MagicMock()}

    pipeline = InboundPipeline(
        registry=registry,
        receipt_service=MagicMock(),
        soul=_make_soul(),
        vault=vault,
        a2a_client=client,
    )
    executor = MagicMock(
        return_value={
            "status": "completed",
            "artifacts": [{"parts": [{"type": "text", "text": "done"}], "metadata": {}}],
        }
    )

    app = FastAPI()
    init_a2a_server(_make_soul(), MagicMock(), registry, pipeline, task_executor=executor, data_dir=data_dir)
    app.include_router(a2a_server_router)
    return app, executor


def _headers(agent_id: str, token: str):
    return {
        "X-Agent-ID": agent_id,
        "Authorization": f"Bearer {token}",
    }


def test_task_status_requires_same_authenticated_peer(monkeypatch, tmp_path):
    _active_tasks.clear()
    monkeypatch.setattr(a2a_server, "_check_a2a_kill_switch", lambda: True)
    app, _executor = _make_app(str(tmp_path))
    client = TestClient(app)

    submit = client.post(
        "/a2a/tasks/send",
        json={"message": {"role": "user", "parts": [{"type": "text", "text": "hello"}]}, "metadata": {}},
        headers=_headers("peer-1", "secret123"),
    )
    assert submit.status_code == 200
    task_id = submit.json()["id"]

    other = client.get(f"/a2a/tasks/{task_id}", headers=_headers("peer-2", "secret123"))
    assert other.status_code == 401

    wrong_secret = client.get(f"/a2a/tasks/{task_id}", headers=_headers("peer-1", "wrong"))
    assert wrong_secret.status_code == 401

    owner = client.get(f"/a2a/tasks/{task_id}", headers=_headers("peer-1", "secret123"))
    assert owner.status_code == 200


def test_agent_card_uses_live_soul_provider(monkeypatch, tmp_path):
    _active_tasks.clear()
    monkeypatch.setattr(a2a_server, "_check_a2a_kill_switch", lambda: True)

    registry = MagicMock(spec=A2ARegistry)
    registry.get.return_value = None
    pipeline = MagicMock(spec=InboundPipeline)
    soul_state = {"soul": _make_soul()}

    app = FastAPI()
    init_a2a_server(lambda: soul_state["soul"], MagicMock(), registry, pipeline, task_executor=MagicMock(), data_dir=str(tmp_path))
    monkeypatch.setattr(
        a2a_server,
        "_agent_card_generator",
        lambda soul, base_url: type("Card", (), {"to_dict": lambda self: {"version": soul.version}})(),
    )
    app.include_router(a2a_server_router)
    client = TestClient(app)

    first = client.get("/.well-known/agent.json")
    assert first.status_code == 200

    soul_state["soul"].version = "2.0.0"
    second = client.get("/.well-known/agent.json")
    assert second.status_code == 200
    assert second.json()["version"] == "2.0.0"


def test_send_task_executes_through_injected_executor(monkeypatch, tmp_path):
    _active_tasks.clear()
    monkeypatch.setattr(a2a_server, "_check_a2a_kill_switch", lambda: True)
    app, executor = _make_app(str(tmp_path))
    client = TestClient(app)

    response = client.post(
        "/a2a/tasks/send",
        json={"message": {"role": "user", "parts": [{"type": "text", "text": "hello"}]}, "metadata": {}},
        headers=_headers("peer-1", "secret123"),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    executor.assert_called_once()


def test_send_task_returns_failed_when_executor_raises(monkeypatch, tmp_path):
    _active_tasks.clear()
    monkeypatch.setattr(a2a_server, "_check_a2a_kill_switch", lambda: True)
    app, _executor = _make_app(str(tmp_path))
    a2a_server._task_executor = MagicMock(side_effect=RuntimeError("boom"))
    client = TestClient(app)

    response = client.post(
        "/a2a/tasks/send",
        json={"message": {"role": "user", "parts": [{"type": "text", "text": "hello"}]}, "metadata": {}},
        headers=_headers("peer-1", "secret123"),
    )

    assert response.status_code == 500
    assert response.json()["status"] == "failed"


def test_send_task_rejected_while_runtime_paused(monkeypatch, tmp_path):
    _active_tasks.clear()
    monkeypatch.setattr(a2a_server, "_check_a2a_kill_switch", lambda: True)
    init_runtime_pause(str(tmp_path))
    pause_runtime("Paused for maintenance", operator_id="op-1", operator_name="Arthur", session_id="session-1")
    try:
        app, _executor = _make_app(str(tmp_path))
        client = TestClient(app)

        response = client.post(
            "/a2a/tasks/send",
            json={"message": {"role": "user", "parts": [{"type": "text", "text": "hello"}]}, "metadata": {}},
            headers=_headers("peer-1", "secret123"),
        )

        assert response.status_code == 423
        assert response.json()["status"] == "failed"
    finally:
        resume_runtime(operator_id="op-1", operator_name="Arthur", session_id="session-1")


def test_task_status_survives_server_reinitialization(monkeypatch, tmp_path):
    _active_tasks.clear()
    monkeypatch.setattr(a2a_server, "_check_a2a_kill_switch", lambda: True)
    app, _executor = _make_app(str(tmp_path))
    client = TestClient(app)

    submit = client.post(
        "/a2a/tasks/send",
        json={"message": {"role": "user", "parts": [{"type": "text", "text": "hello"}]}, "metadata": {}},
        headers=_headers("peer-1", "secret123"),
    )
    assert submit.status_code == 200
    task_id = submit.json()["id"]

    _active_tasks.clear()
    reloaded_app, _ = _make_app(str(tmp_path))
    reloaded_client = TestClient(reloaded_app)

    owner = reloaded_client.get(f"/a2a/tasks/{task_id}", headers=_headers("peer-1", "secret123"))
    assert owner.status_code == 200
    assert owner.json()["status"] == "completed"
