# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
MCP Permission Evaluator — Soul-gated access control for MCP servers and tools.

Every MCP tool invocation is checked against the active Soul's mcp_permissions
block. Access is fail-closed: if the Soul doesn't explicitly permit a server
or tool, it is blocked.

Soul mcp_permissions format:

    mcp_permissions:
      - server_id: "github-mcp"
        allowed_tools: ["list_repos", "read_file", "create_issue"]
        risk_tier: T1
      - server_id: "postgres-mcp"
        allowed_tools: ["query"]
        risk_tier: T2
      - server_id: "stripe-mcp"
        allowed_tools: ["*"]
        risk_tier: T3

Wildcard (*) permits all tools the server exposes.
Omitting a server_id blocks all access to that server.

Federation ceiling enforcement:
    When a parent Soul defines mcp_permissions, child instances can only
    have equal or more restrictive permissions. A child cannot grant access
    to a server or tool that the parent does not permit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class MCPRiskTier(str, Enum):
    """Risk tier for MCP operations, maps to governance RiskTier."""
    T0 = "T0"  # Read-only, no side effects
    T1 = "T1"  # Side effects, reversible
    T2 = "T2"  # Side effects, partially reversible
    T3 = "T3"  # Irreversible, affects external systems


@dataclass(frozen=True)
class MCPServerPermission:
    """Permission grant for a single MCP server from the Soul."""
    server_id: str
    allowed_tools: frozenset[str] = field(default_factory=frozenset)
    risk_tier: MCPRiskTier = MCPRiskTier.T2
    wildcard: bool = False  # True if allowed_tools contains "*"

    @classmethod
    def from_dict(cls, data: dict) -> MCPServerPermission:
        server_id = data.get("server_id", "")
        tools = data.get("allowed_tools", [])
        tier_str = data.get("risk_tier", "T2").upper()

        # Normalize tier
        try:
            tier = MCPRiskTier(tier_str)
        except ValueError:
            tier = MCPRiskTier.T2

        wildcard = "*" in tools
        tool_set = frozenset(tools) if not wildcard else frozenset(["*"])

        return cls(
            server_id=server_id,
            allowed_tools=tool_set,
            risk_tier=tier,
            wildcard=wildcard,
        )


@dataclass
class PermissionCheckResult:
    """Result of an MCP permission check."""
    allowed: bool
    server_id: str
    tool_name: str = ""
    risk_tier: MCPRiskTier = MCPRiskTier.T2
    block_reason: str = ""
    soul_version: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "server_id": self.server_id,
            "tool_name": self.tool_name,
            "risk_tier": self.risk_tier.value,
            "block_reason": self.block_reason,
            "soul_version": self.soul_version,
        }


class MCPPermissionEvaluator:
    """Evaluates MCP access against the active Soul's mcp_permissions.

    Fail-closed: if no permission is found, access is denied.
    Thread-safe: permission set can be updated atomically.
    """

    def __init__(self, soul_version: str = ""):
        self._permissions: Dict[str, MCPServerPermission] = {}
        self._soul_version = soul_version

    def load_from_soul(self, soul_data: dict) -> None:
        """Load MCP permissions from a parsed Soul document.

        Args:
            soul_data: The full Soul document dict.
        """
        mcp_perms = soul_data.get("mcp_permissions", [])
        new_permissions: Dict[str, MCPServerPermission] = {}

        for entry in mcp_perms:
            perm = MCPServerPermission.from_dict(entry)
            if perm.server_id:
                new_permissions[perm.server_id] = perm

        self._permissions = new_permissions
        self._soul_version = soul_data.get("version", "")

        logger.info(
            "MCP permissions loaded: %d server(s) permitted (soul=%s)",
            len(new_permissions), self._soul_version,
        )

    def load_permissions(
        self,
        permissions: List[MCPServerPermission],
        soul_version: str = "",
    ) -> None:
        """Load MCP permissions directly (for testing or programmatic use)."""
        self._permissions = {p.server_id: p for p in permissions}
        self._soul_version = soul_version

    @property
    def soul_version(self) -> str:
        return self._soul_version

    @property
    def permitted_servers(self) -> List[str]:
        """List of server IDs permitted by the current Soul."""
        return list(self._permissions.keys())

    def get_server_permission(self, server_id: str) -> Optional[MCPServerPermission]:
        """Get the permission grant for a specific server, if any."""
        return self._permissions.get(server_id)

    def check_server_access(self, server_id: str) -> PermissionCheckResult:
        """Check if a server is permitted by the Soul.

        Does not check individual tool access — use check_tool_access for that.
        """
        perm = self._permissions.get(server_id)
        if perm is None:
            return PermissionCheckResult(
                allowed=False,
                server_id=server_id,
                risk_tier=MCPRiskTier.T2,
                block_reason=f"Server '{server_id}' not permitted by active Soul",
                soul_version=self._soul_version,
            )

        return PermissionCheckResult(
            allowed=True,
            server_id=server_id,
            risk_tier=perm.risk_tier,
            soul_version=self._soul_version,
        )

    def check_tool_access(
        self,
        server_id: str,
        tool_name: str,
    ) -> PermissionCheckResult:
        """Check if a specific tool on a specific server is permitted.

        This is the primary check called before every MCP tool invocation.
        Fail-closed: both server and tool must be explicitly permitted.
        """
        # Gate 1: Server must be permitted
        perm = self._permissions.get(server_id)
        if perm is None:
            return PermissionCheckResult(
                allowed=False,
                server_id=server_id,
                tool_name=tool_name,
                block_reason=f"Server '{server_id}' not permitted by active Soul",
                soul_version=self._soul_version,
            )

        # Gate 2: Tool must be in allowed list (or wildcard)
        if not perm.wildcard and tool_name not in perm.allowed_tools:
            return PermissionCheckResult(
                allowed=False,
                server_id=server_id,
                tool_name=tool_name,
                risk_tier=perm.risk_tier,
                block_reason=(
                    f"Tool '{tool_name}' not in allowed_tools for server '{server_id}'. "
                    f"Permitted: {sorted(perm.allowed_tools)}"
                ),
                soul_version=self._soul_version,
            )

        return PermissionCheckResult(
            allowed=True,
            server_id=server_id,
            tool_name=tool_name,
            risk_tier=perm.risk_tier,
            soul_version=self._soul_version,
        )

    def get_allowed_tools(self, server_id: str) -> Set[str]:
        """Get the set of allowed tools for a server.

        Returns empty set if server is not permitted.
        Returns {"*"} for wildcard access.
        """
        perm = self._permissions.get(server_id)
        if perm is None:
            return set()
        return set(perm.allowed_tools)

    def enforce_federation_ceiling(
        self,
        parent_permissions: List[MCPServerPermission],
    ) -> List[str]:
        """Enforce that current permissions don't exceed the parent's ceiling.

        Any server or tool in our permissions that the parent doesn't permit
        is removed. Returns list of violations that were enforced.

        This implements the federation constraint: child instances can only
        have equal or more restrictive MCP permissions than their parent.
        """
        parent_map = {p.server_id: p for p in parent_permissions}
        violations: List[str] = []
        enforced: Dict[str, MCPServerPermission] = {}

        for server_id, our_perm in self._permissions.items():
            parent_perm = parent_map.get(server_id)

            # Parent doesn't permit this server at all — remove
            if parent_perm is None:
                violations.append(
                    f"Server '{server_id}' removed: not permitted by parent Soul"
                )
                continue

            # Parent permits server — check tool-level ceiling
            if parent_perm.wildcard:
                # Parent allows everything on this server — keep ours as-is
                enforced[server_id] = our_perm
            elif our_perm.wildcard:
                # We have wildcard but parent doesn't — restrict to parent's set
                violations.append(
                    f"Server '{server_id}': wildcard downgraded to parent's tool set"
                )
                enforced[server_id] = MCPServerPermission(
                    server_id=server_id,
                    allowed_tools=parent_perm.allowed_tools,
                    risk_tier=max(our_perm.risk_tier, parent_perm.risk_tier, key=lambda t: _tier_severity(t)),
                    wildcard=False,
                )
            else:
                # Both have explicit sets — intersect
                allowed = our_perm.allowed_tools & parent_perm.allowed_tools
                removed = our_perm.allowed_tools - parent_perm.allowed_tools
                if removed:
                    violations.append(
                        f"Server '{server_id}': tools removed by ceiling: {sorted(removed)}"
                    )
                if allowed:
                    enforced[server_id] = MCPServerPermission(
                        server_id=server_id,
                        allowed_tools=allowed,
                        risk_tier=max(our_perm.risk_tier, parent_perm.risk_tier, key=lambda t: _tier_severity(t)),
                        wildcard=False,
                    )
                else:
                    violations.append(
                        f"Server '{server_id}' removed: no tools remaining after ceiling"
                    )

            # Enforce risk tier ceiling (child can't be LESS restrictive)
            if server_id in enforced:
                child_tier = enforced[server_id].risk_tier
                parent_tier = parent_perm.risk_tier
                if _tier_severity(child_tier) < _tier_severity(parent_tier):
                    violations.append(
                        f"Server '{server_id}': risk tier elevated from {child_tier.value} to {parent_tier.value}"
                    )
                    enforced[server_id] = MCPServerPermission(
                        server_id=server_id,
                        allowed_tools=enforced[server_id].allowed_tools,
                        risk_tier=parent_tier,
                        wildcard=enforced[server_id].wildcard,
                    )

        self._permissions = enforced

        if violations:
            logger.warning(
                "Federation ceiling enforced: %d violation(s) corrected",
                len(violations),
            )
            for v in violations:
                logger.warning("  Ceiling: %s", v)

        return violations


def _tier_severity(tier: MCPRiskTier) -> int:
    """Map tier to numeric severity for comparison."""
    return {"T0": 0, "T1": 1, "T2": 2, "T3": 3}.get(tier.value, 2)
