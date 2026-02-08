"""
Execution Authority Schema — ExecutionToken and related types.

An ExecutionToken represents a scoped, time-limited, auditable authority
to perform actions. Minted when a user approves a permission request.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import List, Optional


class TaskType(str, Enum):
    """Type of task the token authorizes."""
    CODE_CHANGE = "CODE_CHANGE"
    DEPLOY = "DEPLOY"
    RESEARCH = "RESEARCH"
    FILE_OP = "FILE_OP"
    INTEGRATION = "INTEGRATION"
    OTHER = "OTHER"


class NetworkPolicy(str, Enum):
    """Network access policy for the token."""
    OFF = "OFF"
    ALLOWLIST = "ALLOWLIST"
    FULL = "FULL"


class SecretPolicy(str, Enum):
    """Secret access policy."""
    NO_SECRETS = "NO_SECRETS"
    ENV_ONLY = "ENV_ONLY"
    VAULT_ONLY = "VAULT_ONLY"


class TokenStatus(str, Enum):
    """Lifecycle status of a token."""
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


@dataclass
class ExecutionToken:
    """Scoped authority to perform actions within defined boundaries."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    created_by: str = "Commander"
    scope: str = ""
    task_type: str = TaskType.OTHER.value
    allowed_tools: List[str] = field(default_factory=list)
    allowed_skills: List[str] = field(default_factory=list)
    allowed_paths: List[str] = field(default_factory=list)
    network_policy: str = NetworkPolicy.OFF.value
    network_allowlist: List[str] = field(default_factory=list)
    secret_policy: str = SecretPolicy.NO_SECRETS.value
    max_duration_sec: int = 300
    max_actions: int = 50
    risk_tier: str = "LOW"
    requires_verifier: bool = False
    status: str = TokenStatus.ACTIVE.value
    parent_receipt_id: Optional[str] = None
    actions_used: int = 0
    expires_at: Optional[str] = None
    session_id: str = ""

    def __post_init__(self):
        """Set expires_at from max_duration_sec if not already set."""
        if self.expires_at is None and self.max_duration_sec > 0:
            try:
                created = datetime.fromisoformat(self.created_at)
            except (ValueError, TypeError):
                created = datetime.now(timezone.utc)
            self.expires_at = (created + timedelta(seconds=self.max_duration_sec)).isoformat()

    def is_expired(self) -> bool:
        """Check if the token has expired by time or action count."""
        if self.status != TokenStatus.ACTIVE.value:
            return True
        if self.actions_used >= self.max_actions:
            return True
        if self.expires_at:
            try:
                exp = datetime.fromisoformat(self.expires_at)
                if datetime.now(timezone.utc) > exp:
                    return True
            except (ValueError, TypeError):
                pass
        return False

    def allows_tool(self, tool_name: str) -> bool:
        """Check if the token allows the given tool."""
        if not self.allowed_tools:
            return True  # Empty means all allowed
        return tool_name in self.allowed_tools

    def allows_skill(self, skill_name: str) -> bool:
        """Check if the token allows the given skill."""
        if not self.allowed_skills:
            return True
        return skill_name in self.allowed_skills

    def allows_path(self, path: str) -> bool:
        """Check if the token allows the given file path."""
        if not self.allowed_paths:
            return True
        import fnmatch
        return any(fnmatch.fnmatch(path, pattern) for pattern in self.allowed_paths)

    def allows_network(self, host: str) -> bool:
        """Check if the token allows network access to the given host."""
        if self.network_policy == NetworkPolicy.OFF.value:
            return False
        if self.network_policy == NetworkPolicy.FULL.value:
            return True
        # ALLOWLIST
        return host in self.network_allowlist

    def to_dict(self) -> dict:
        """Serialize to dict."""
        return {
            "id": self.id,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "scope": self.scope,
            "task_type": self.task_type,
            "allowed_tools": self.allowed_tools,
            "allowed_skills": self.allowed_skills,
            "allowed_paths": self.allowed_paths,
            "network_policy": self.network_policy,
            "network_allowlist": self.network_allowlist,
            "secret_policy": self.secret_policy,
            "max_duration_sec": self.max_duration_sec,
            "max_actions": self.max_actions,
            "risk_tier": self.risk_tier,
            "requires_verifier": self.requires_verifier,
            "status": self.status,
            "parent_receipt_id": self.parent_receipt_id,
            "actions_used": self.actions_used,
            "expires_at": self.expires_at,
            "session_id": self.session_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ExecutionToken":
        """Deserialize from dict."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class AuthResult:
    """Result of an authority check."""
    allowed: bool = True
    reason: str = ""
