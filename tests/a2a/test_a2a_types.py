# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""Unit tests for A2A types — data models and enums."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "core"))

import pytest
from datetime import datetime, timezone, timedelta

from src.a2a.types import (
    A2ATask, A2AMessage, A2AMessagePart, A2AArtifact,
    AgentCard, AgentCardSkill, RemoteAgent,
    A2ATaskStatus, AgentFramework, AgentDirection,
    RemoteAgentStatus, AgentCardStatus,
)


# ── A2AMessagePart ──────────────────────────────────────────

class TestA2AMessagePart:
    def test_text_part_creation(self):
        part = A2AMessagePart(type="text", text="hello")
        assert part.type == "text"
        assert part.text == "hello"

    def test_text_part_to_dict(self):
        part = A2AMessagePart(type="text", text="hello")
        d = part.to_dict()
        assert d == {"type": "text", "text": "hello"}

    def test_file_part_to_dict(self):
        part = A2AMessagePart(type="file", file_uri="s3://bucket/key", mime_type="text/csv")
        d = part.to_dict()
        assert d == {"type": "file", "file_uri": "s3://bucket/key", "mime_type": "text/csv"}

    def test_data_part_to_dict(self):
        part = A2AMessagePart(type="data", data={"key": "value"})
        d = part.to_dict()
        assert d == {"type": "data", "data": {"key": "value"}}

    def test_omits_none_fields(self):
        part = A2AMessagePart(type="text", text="x")
        d = part.to_dict()
        assert "file_uri" not in d
        assert "data" not in d
        assert "mime_type" not in d


# ── A2AMessage ──────────────────────────────────────────────

class TestA2AMessage:
    def test_creation(self):
        msg = A2AMessage(role="user", parts=[A2AMessagePart(text="hi")])
        assert msg.role == "user"
        assert len(msg.parts) == 1

    def test_to_dict(self):
        msg = A2AMessage(role="agent", parts=[A2AMessagePart(text="reply")])
        d = msg.to_dict()
        assert d["role"] == "agent"
        assert len(d["parts"]) == 1
        assert d["parts"][0]["text"] == "reply"

    def test_from_dict_round_trip(self):
        original = A2AMessage(
            role="user",
            parts=[A2AMessagePart(type="text", text="hello")],
        )
        d = original.to_dict()
        restored = A2AMessage.from_dict(d)
        assert restored.role == original.role
        assert len(restored.parts) == 1
        assert restored.parts[0].text == "hello"

    def test_from_dict_defaults(self):
        msg = A2AMessage.from_dict({})
        assert msg.role == "user"
        assert msg.parts == []


# ── A2AArtifact ─────────────────────────────────────────────

class TestA2AArtifact:
    def test_creation(self):
        art = A2AArtifact(
            parts=[A2AMessagePart(text="result")],
            metadata={"format": "text"},
        )
        assert len(art.parts) == 1
        assert art.metadata["format"] == "text"

    def test_to_dict(self):
        art = A2AArtifact(
            parts=[A2AMessagePart(text="data")],
            metadata={"key": "val"},
        )
        d = art.to_dict()
        assert d["parts"][0]["text"] == "data"
        assert d["metadata"]["key"] == "val"


# ── A2ATask ─────────────────────────────────────────────────

class TestA2ATask:
    def test_creation_defaults(self):
        task = A2ATask()
        assert task.status == "submitted"
        assert task.risk_tier == 2
        assert task.id  # UUID generated
        assert task.created_at
        assert task.artifacts == []

    def test_to_dict_without_message(self):
        task = A2ATask(id="t1")
        d = task.to_dict()
        assert d["id"] == "t1"
        assert "message" not in d
        assert "artifacts" not in d

    def test_to_dict_with_message(self):
        msg = A2AMessage(parts=[A2AMessagePart(text="go")])
        task = A2ATask(id="t2", message=msg)
        d = task.to_dict()
        assert "message" in d
        assert d["message"]["parts"][0]["text"] == "go"

    def test_to_dict_with_artifacts(self):
        art = A2AArtifact(parts=[A2AMessagePart(text="done")])
        task = A2ATask(id="t3", artifacts=[art])
        d = task.to_dict()
        assert len(d["artifacts"]) == 1


# ── AgentCardSkill ──────────────────────────────────────────

class TestAgentCardSkill:
    def test_to_dict(self):
        skill = AgentCardSkill(id="chat", name="Chat", description="Talk", tags=["general"])
        d = skill.to_dict()
        assert d["id"] == "chat"
        assert d["name"] == "Chat"
        assert d["tags"] == ["general"]


# ── AgentCard ───────────────────────────────────────────────

class TestAgentCard:
    def test_creation(self):
        card = AgentCard(name="Test", description="Desc", url="http://localhost")
        assert card.a2a_protocol_version == "0.2"
        assert card.version == "0.2"

    def test_to_dict_includes_governance(self):
        card = AgentCard(
            name="Test", description="Desc", url="http://x",
            governance_declaration={"governance_framework": "lancelot"},
        )
        d = card.to_dict()
        assert d["governance_declaration"]["governance_framework"] == "lancelot"

    def test_to_dict_omits_none_governance(self):
        card = AgentCard(name="T", description="D", url="http://x")
        d = card.to_dict()
        assert "governance_declaration" not in d


# ── RemoteAgent ─────────────────────────────────────────────

class TestRemoteAgent:
    def test_creation(self):
        agent = RemoteAgent(agent_id="agent-1", display_name="Agent One")
        assert agent.agent_id == "agent-1"
        assert agent.status == "active"
        assert agent.direction == "outbound"

    def test_kill_switch_id_auto_generation(self):
        agent = RemoteAgent(agent_id="test-agent-1", display_name="Test")
        assert agent.kill_switch_id == "A2A_TEST_AGENT_1"

    def test_kill_switch_id_format_no_hyphens(self):
        agent = RemoteAgent(agent_id="my-cool-agent", display_name="Cool")
        assert "-" not in agent.kill_switch_id
        assert agent.kill_switch_id == "A2A_MY_COOL_AGENT"

    def test_kill_switch_id_explicit_not_overwritten(self):
        agent = RemoteAgent(agent_id="x", display_name="X", kill_switch_id="CUSTOM_KS")
        assert agent.kill_switch_id == "CUSTOM_KS"

    def test_to_dict(self):
        agent = RemoteAgent(agent_id="a1", display_name="A1")
        d = agent.to_dict()
        assert d["agent_id"] == "a1"
        assert "kill_switch_id" in d

    def test_from_dict_round_trip(self):
        original = RemoteAgent(agent_id="rt", display_name="RT Agent")
        d = original.to_dict()
        restored = RemoteAgent.from_dict(d)
        assert restored.agent_id == original.agent_id
        assert restored.display_name == original.display_name
        assert restored.kill_switch_id == original.kill_switch_id

    def test_from_dict_ignores_extra_keys(self):
        d = {"agent_id": "x", "display_name": "X", "unknown_field": "ignored"}
        agent = RemoteAgent.from_dict(d)
        assert agent.agent_id == "x"

    def test_card_status_verified(self):
        recent = datetime.now(timezone.utc).isoformat()
        agent = RemoteAgent(agent_id="v", display_name="V", last_verified=recent)
        assert agent.card_status == AgentCardStatus.VERIFIED.value

    def test_card_status_stale(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        agent = RemoteAgent(agent_id="s", display_name="S", last_verified=old)
        assert agent.card_status == AgentCardStatus.STALE.value

    def test_card_status_unverified_empty(self):
        agent = RemoteAgent(agent_id="u", display_name="U", last_verified="")
        assert agent.card_status == AgentCardStatus.UNVERIFIED.value

    def test_card_status_unverified_none(self):
        agent = RemoteAgent(agent_id="n", display_name="N")
        # last_verified defaults to ""
        assert agent.card_status == AgentCardStatus.UNVERIFIED.value

    def test_card_status_invalid_date(self):
        agent = RemoteAgent(agent_id="bad", display_name="Bad", last_verified="not-a-date")
        assert agent.card_status == AgentCardStatus.UNVERIFIED.value


# ── Enums ───────────────────────────────────────────────────

class TestEnums:
    def test_a2a_task_status_values(self):
        assert A2ATaskStatus.SUBMITTED.value == "submitted"
        assert A2ATaskStatus.WORKING.value == "working"
        assert A2ATaskStatus.INPUT_REQUIRED.value == "input-required"
        assert A2ATaskStatus.COMPLETED.value == "completed"
        assert A2ATaskStatus.FAILED.value == "failed"
        assert A2ATaskStatus.CANCELED.value == "canceled"

    def test_agent_framework_values(self):
        assert AgentFramework.CREWAI.value == "crewai"
        assert AgentFramework.LANGCHAIN.value == "langchain"
        assert AgentFramework.GOOGLE_ADK.value == "google_adk"
        assert AgentFramework.LANCELOT.value == "lancelot"
        assert AgentFramework.UNKNOWN.value == "unknown"

    def test_agent_direction_values(self):
        assert AgentDirection.INBOUND.value == "inbound"
        assert AgentDirection.OUTBOUND.value == "outbound"
        assert AgentDirection.BOTH.value == "both"

    def test_remote_agent_status_values(self):
        assert RemoteAgentStatus.ACTIVE.value == "active"
        assert RemoteAgentStatus.SUSPENDED.value == "suspended"
        assert RemoteAgentStatus.REVOKED.value == "revoked"

    def test_agent_card_status_values(self):
        assert AgentCardStatus.VERIFIED.value == "verified"
        assert AgentCardStatus.STALE.value == "stale"
        assert AgentCardStatus.UNVERIFIED.value == "unverified"
