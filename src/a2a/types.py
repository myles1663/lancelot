# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
A2A Types — Data models for Agent-to-Agent protocol.

Implements the A2A v0.2 protocol primitives: Agent Card, Task, Message,
Artifact, and RemoteAgent registry records.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


# ── A2A Protocol Enums ───────────────────────────────────────

class A2ATaskStatus(str, Enum):
    """A2A task lifecycle states."""
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class AgentFramework(str, Enum):
    """Known agent frameworks."""
    CREWAI = "crewai"
    LANGCHAIN = "langchain"
    GOOGLE_ADK = "google_adk"
    LANCELOT = "lancelot"
    UNKNOWN = "unknown"


class AgentDirection(str, Enum):
    """Remote agent interaction direction."""
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    BOTH = "both"


class AgentCardStatus(str, Enum):
    """Agent Card verification state."""
    VERIFIED = "verified"
    STALE = "stale"
    UNVERIFIED = "unverified"


class RemoteAgentStatus(str, Enum):
    """Registry status for remote agents."""
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


# ── A2A Protocol Models ─────────────────────────────────────

@dataclass
class A2AMessagePart:
    """A2A message part — text, file, or structured data."""
    type: str = "text"  # "text", "file", "data"
    text: Optional[str] = None
    file_uri: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    mime_type: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {"type": self.type}
        if self.text is not None:
            d["text"] = self.text
        if self.file_uri is not None:
            d["file_uri"] = self.file_uri
        if self.data is not None:
            d["data"] = self.data
        if self.mime_type is not None:
            d["mime_type"] = self.mime_type
        return d


@dataclass
class A2AMessage:
    """A2A message — contains one or more parts."""
    role: str = "user"  # "user" or "agent"
    parts: List[A2AMessagePart] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"role": self.role, "parts": [p.to_dict() for p in self.parts]}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> A2AMessage:
        parts = [A2AMessagePart(**p) for p in data.get("parts", [])]
        return cls(role=data.get("role", "user"), parts=parts)


@dataclass
class A2AArtifact:
    """A2A artifact — output of a completed task."""
    parts: List[A2AMessagePart] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "parts": [p.to_dict() for p in self.parts],
            "metadata": self.metadata,
        }


@dataclass
class A2ATask:
    """A2A task — core work unit."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = A2ATaskStatus.SUBMITTED.value
    message: Optional[A2AMessage] = None
    artifacts: List[A2AArtifact] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    # Lancelot governance extensions
    quest_id: Optional[str] = None
    risk_tier: int = 2
    caller_agent_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "id": self.id,
            "status": self.status,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.message:
            d["message"] = self.message.to_dict()
        if self.artifacts:
            d["artifacts"] = [a.to_dict() for a in self.artifacts]
        return d


@dataclass
class AgentCardSkill:
    """Skill advertised in an Agent Card."""
    id: str
    name: str
    description: str = ""
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AgentCard:
    """A2A Agent Card — describes an agent's capabilities."""
    name: str
    description: str
    url: str
    version: str = "0.2"
    a2a_protocol_version: str = "0.2"
    skills: List[AgentCardSkill] = field(default_factory=list)
    authentication: Dict[str, Any] = field(default_factory=dict)
    capabilities: Dict[str, bool] = field(default_factory=lambda: {
        "streaming": True,
        "pushNotifications": False,
    })
    governance_declaration: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "name": self.name,
            "description": self.description,
            "url": self.url,
            "version": self.version,
            "a2a_protocol_version": self.a2a_protocol_version,
            "skills": [s.to_dict() for s in self.skills],
            "authentication": self.authentication,
            "capabilities": self.capabilities,
        }
        if self.governance_declaration is not None:
            d["governance_declaration"] = self.governance_declaration
        if self.metadata:
            d["metadata"] = self.metadata
        return d


# ── Remote Agent Registry Record ────────────────────────────

@dataclass
class RemoteAgent:
    """A2A Remote Agent record in the registry.

    Stores identity, trust, and connection details for external agents.
    Credentials are stored separately in the Credential Vault.
    """
    agent_id: str
    display_name: str
    agent_card_url: str = ""
    agent_framework: str = AgentFramework.UNKNOWN.value
    auth_type: str = "none"  # bearer_token, oauth2, api_key, none
    credentials_ref: str = ""  # Vault reference key
    inbound_trust_tier: int = 2
    outbound_trust_tier: int = 2
    direction: str = AgentDirection.OUTBOUND.value
    network_allowlist_entries: List[str] = field(default_factory=list)
    kill_switch_id: str = ""  # Auto-generated: A2A_[AGENT_ID]
    last_verified: str = ""
    status: str = RemoteAgentStatus.ACTIVE.value
    auto_registered: bool = False
    agent_card_cache: Optional[Dict[str, Any]] = None
    # Stats
    interaction_count: int = 0
    success_count: int = 0
    last_interaction: str = ""
    last_outcome: str = ""
    registered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self):
        if not self.kill_switch_id:
            safe_id = self.agent_id.replace("-", "_").upper()
            self.kill_switch_id = f"A2A_{safe_id}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RemoteAgent:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @property
    def card_status(self) -> str:
        """Determine Agent Card verification status."""
        if not self.last_verified:
            return AgentCardStatus.UNVERIFIED.value
        try:
            verified = datetime.fromisoformat(self.last_verified)
            age_hours = (datetime.now(timezone.utc) - verified).total_seconds() / 3600
            if age_hours > 24:
                return AgentCardStatus.STALE.value
            return AgentCardStatus.VERIFIED.value
        except (ValueError, TypeError):
            return AgentCardStatus.UNVERIFIED.value
