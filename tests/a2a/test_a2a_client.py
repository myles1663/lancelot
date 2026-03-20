# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""Unit tests for the A2A HTTP Client."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "core"))

import pytest
from unittest.mock import MagicMock, patch
import json

from src.a2a.types import (
    AgentCard, AgentCardSkill, RemoteAgent, A2ATaskStatus,
)
from src.a2a.client import A2AClient


def _make_agent(
    agent_id="remote-1",
    agent_card_url="https://agent.example.com/.well-known/agent.json",
):
    return RemoteAgent(
        agent_id=agent_id,
        display_name="Remote Agent",
        agent_card_url=agent_card_url,
    )


def _make_card_response_data(governance_framework=None):
    data = {
        "name": "Remote Agent",
        "description": "A test agent",
        "url": "https://agent.example.com",
        "version": "1.0",
        "a2a_protocol_version": "0.2",
        "skills": [{"id": "chat", "name": "Chat", "description": "Talk", "tags": []}],
        "authentication": {"type": "bearer_token"},
        "capabilities": {"streaming": True},
    }
    if governance_framework:
        data["governance_declaration"] = {"governance_framework": governance_framework}
    return data


def _mock_httpx_client(response_data=None, raise_on_get=None, raise_on_post=None,
                       raise_on_json=None):
    """Build a mock httpx module with a mock Client context manager."""
    mock_httpx = MagicMock()
    mock_client_instance = MagicMock()

    mock_response = MagicMock()
    if raise_on_json:
        mock_response.json.side_effect = raise_on_json
    elif response_data is not None:
        mock_response.json.return_value = response_data
    mock_response.raise_for_status.return_value = None

    if raise_on_get:
        mock_client_instance.get.side_effect = raise_on_get
    else:
        mock_client_instance.get.return_value = mock_response

    if raise_on_post:
        mock_client_instance.post.side_effect = raise_on_post
    else:
        mock_client_instance.post.return_value = mock_response

    # Context manager support
    mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
    mock_client_instance.__exit__ = MagicMock(return_value=False)
    mock_httpx.Client.return_value = mock_client_instance

    return mock_httpx, mock_client_instance


# ── fetch_agent_card ────────────────────────────────────────

class TestFetchAgentCard:
    def test_returns_parsed_agent_card(self):
        mock_httpx, _ = _mock_httpx_client(response_data=_make_card_response_data())
        with patch.dict("sys.modules", {"httpx": mock_httpx}):
            client = A2AClient()
            card = client.fetch_agent_card("https://agent.example.com/.well-known/agent.json")
        assert card is not None
        assert card.name == "Remote Agent"
        assert len(card.skills) == 1

    def test_timeout_returns_none(self):
        mock_httpx, _ = _mock_httpx_client(raise_on_get=Exception("Connection timeout"))
        with patch.dict("sys.modules", {"httpx": mock_httpx}):
            client = A2AClient()
            card = client.fetch_agent_card("https://unreachable.example.com/agent.json")
        assert card is None

    def test_invalid_json_returns_none(self):
        mock_httpx, _ = _mock_httpx_client(
            raise_on_json=json.JSONDecodeError("bad", "", 0),
        )
        with patch.dict("sys.modules", {"httpx": mock_httpx}):
            client = A2AClient()
            card = client.fetch_agent_card("https://agent.example.com/agent.json")
        assert card is None

    def test_emits_receipt_on_success(self):
        mock_httpx, _ = _mock_httpx_client(response_data=_make_card_response_data())
        svc = MagicMock()
        with patch.dict("sys.modules", {"httpx": mock_httpx}):
            client = A2AClient(receipt_service=svc)
            client.fetch_agent_card("https://agent.example.com/.well-known/agent.json")
        svc.create.assert_called_once()


# ── send_task ───────────────────────────────────────────────

class TestSendTask:
    def test_posts_to_correct_url(self):
        mock_httpx, mock_client = _mock_httpx_client(
            response_data={"id": "t1", "status": "completed"},
        )
        with patch.dict("sys.modules", {"httpx": mock_httpx}):
            agent = _make_agent()
            client = A2AClient()
            client.send_task(agent, "Do this", "task-1")

        call_args = mock_client.post.call_args
        assert "/a2a/tasks/send" in call_args[0][0]

    def test_includes_bearer_auth_headers(self):
        mock_httpx, mock_client = _mock_httpx_client(
            response_data={"id": "t1", "status": "completed"},
        )
        with patch.dict("sys.modules", {"httpx": mock_httpx}):
            agent = _make_agent()
            client = A2AClient()
            client.send_task(agent, "Do this", "task-1",
                             credentials={"type": "bearer_token", "token": "secret123"})

        call_args = mock_client.post.call_args
        headers = call_args[1].get("headers", {})
        assert headers.get("Authorization") == "Bearer secret123"

    def test_includes_api_key_headers(self):
        mock_httpx, mock_client = _mock_httpx_client(
            response_data={"id": "t1", "status": "completed"},
        )
        with patch.dict("sys.modules", {"httpx": mock_httpx}):
            agent = _make_agent()
            client = A2AClient()
            client.send_task(agent, "Do this", "task-1",
                             credentials={"type": "api_key", "key": "mykey"})

        call_args = mock_client.post.call_args
        headers = call_args[1].get("headers", {})
        assert headers.get("X-API-Key") == "mykey"

    def test_connection_error_returns_failed(self):
        mock_httpx, _ = _mock_httpx_client(
            raise_on_post=Exception("Connection refused"),
        )
        with patch.dict("sys.modules", {"httpx": mock_httpx}):
            agent = _make_agent()
            client = A2AClient()
            result = client.send_task(agent, "Do this", "task-1")
        assert result["status"] == A2ATaskStatus.FAILED.value
        assert "error" in result


# ── poll_task_status ────────────────────────────────────────

class TestPollTaskStatus:
    def test_returns_task_status(self):
        mock_httpx, _ = _mock_httpx_client(
            response_data={"id": "t1", "status": "completed"},
        )
        with patch.dict("sys.modules", {"httpx": mock_httpx}):
            agent = _make_agent()
            client = A2AClient()
            result = client.poll_task_status(agent, "t1")
        assert result["status"] == "completed"

    def test_not_found_returns_failed(self):
        mock_httpx, _ = _mock_httpx_client(
            raise_on_get=Exception("404 Not Found"),
        )
        with patch.dict("sys.modules", {"httpx": mock_httpx}):
            agent = _make_agent()
            client = A2AClient()
            result = client.poll_task_status(agent, "nonexistent")
        assert result["status"] == A2ATaskStatus.FAILED.value


# ── is_lancelot_instance ───────────────────────────────────

class TestIsLancelotInstance:
    def test_detects_lancelot_framework(self):
        client = A2AClient()
        card = AgentCard(
            name="Lancelot", description="", url="",
            governance_declaration={"governance_framework": "lancelot"},
        )
        assert client.is_lancelot_instance(card) is True

    def test_returns_false_for_non_lancelot(self):
        client = A2AClient()
        card = AgentCard(
            name="Other", description="", url="",
            governance_declaration={"governance_framework": "crewai"},
        )
        assert client.is_lancelot_instance(card) is False

    def test_returns_false_without_governance(self):
        client = A2AClient()
        card = AgentCard(name="Basic", description="", url="")
        assert client.is_lancelot_instance(card) is False


# ── verify_agent_card ──────────────────────────────────────

class TestVerifyAgentCard:
    def test_no_card_url_returns_false(self):
        client = A2AClient()
        agent = RemoteAgent(agent_id="a", display_name="A", agent_card_url="")
        assert client.verify_agent_card(agent) is False

    @patch.object(A2AClient, "fetch_agent_card")
    def test_fetch_failure_returns_false(self, mock_fetch):
        mock_fetch.return_value = None
        client = A2AClient()
        agent = _make_agent()
        assert client.verify_agent_card(agent) is False

    @patch.object(A2AClient, "fetch_agent_card")
    def test_successful_verification(self, mock_fetch):
        mock_fetch.return_value = AgentCard(name="Agent", description="", url="")
        client = A2AClient()
        agent = _make_agent()
        assert client.verify_agent_card(agent) is True
