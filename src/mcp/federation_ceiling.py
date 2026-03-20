# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
MCP Federation Ceiling Enforcement — Monotonic permission narrowing.

Implements the federation constraint for MCP permissions: child/peer
instances can ONLY have equal or more restrictive MCP permissions
than the root Soul. This follows the exact same narrowing contract
used by the HIVE Agent Mesh for Scoped Souls:

    HIVE pattern (src/hive/scoped_soul.py):
        - Constraints are ADDITIVE (never remove parent rules)
        - allowed_autonomous ⊆ parent.allowed_autonomous
        - risk_rules ⊇ parent.risk_rules
        - Scheduling boundaries only tighten

    MCP ceiling pattern (this module):
        - permitted_servers ⊆ root.permitted_servers
        - allowed_tools ⊆ root.allowed_tools (per server)
        - risk_tier ≥ root.risk_tier (per server, higher = more restrictive)
        - Wildcard only if root has wildcard
        - Child can never ESCALATE permissions

The ceiling is enforced at three points:

    1. Soul propagation — when a Soul is pushed to a peer, the peer's
       MCP permissions are automatically intersected with the root's.

    2. Peer registration — when a new peer joins the federation, its
       mcp_permissions are validated against the root's ceiling.

    3. Runtime audit — periodic check that running instances haven't
       drifted above their ceiling (defense in depth).

This module is independent of the proxy — it operates on Soul
documents and permission sets, not on individual invocations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from src.mcp.permissions import (
    MCPPermissionEvaluator,
    MCPRiskTier,
    MCPServerPermission,
    _tier_severity,
)

logger = logging.getLogger(__name__)


@dataclass
class CeilingViolation:
    """A single violation found during ceiling enforcement."""
    server_id: str
    violation_type: str  # "server_removed", "tools_narrowed", "wildcard_downgraded", "tier_elevated"
    detail: str


@dataclass
class CeilingEnforcementResult:
    """Result of applying a federation ceiling to MCP permissions."""
    enforced: bool  # True if any changes were made
    violations: List[CeilingViolation] = field(default_factory=list)
    original_server_count: int = 0
    resulting_server_count: int = 0
    resulting_permissions: List[MCPServerPermission] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enforced": self.enforced,
            "violation_count": len(self.violations),
            "violations": [
                {"server_id": v.server_id, "type": v.violation_type, "detail": v.detail}
                for v in self.violations
            ],
            "original_servers": self.original_server_count,
            "resulting_servers": self.resulting_server_count,
        }


def enforce_mcp_ceiling(
    child_permissions: List[MCPServerPermission],
    root_permissions: List[MCPServerPermission],
) -> CeilingEnforcementResult:
    """Enforce the federation ceiling: child ⊆ root for MCP permissions.

    This is the same monotonic narrowing contract used by HIVE Scoped Souls.
    The child can only have equal or more restrictive permissions.

    Contract (mirrors HIVE ScopedSoulGenerator.validate_more_restrictive):
        1. Child servers ⊆ root servers (can't access servers root doesn't permit)
        2. Child tools ⊆ root tools per server (can't access tools root doesn't permit)
        3. Child risk tier ≥ root risk tier per server (can't be less restrictive)
        4. Child wildcard → root must have wildcard (can't escalate to wildcard)

    Args:
        child_permissions: The child/peer instance's MCP permissions.
        root_permissions: The root Soul's MCP permissions (the ceiling).

    Returns:
        CeilingEnforcementResult with the narrowed permissions and any
        violations that were corrected.
    """
    root_map: Dict[str, MCPServerPermission] = {
        p.server_id: p for p in root_permissions
    }
    violations: List[CeilingViolation] = []
    resulting: List[MCPServerPermission] = []

    for child_perm in child_permissions:
        root_perm = root_map.get(child_perm.server_id)

        # Rule 1: Server must be in root's permission set
        if root_perm is None:
            violations.append(CeilingViolation(
                server_id=child_perm.server_id,
                violation_type="server_removed",
                detail=f"Server '{child_perm.server_id}' not permitted by root Soul — removed",
            ))
            continue

        # Rule 4: Wildcard check
        if child_perm.wildcard and not root_perm.wildcard:
            # Downgrade wildcard to root's explicit tool set
            violations.append(CeilingViolation(
                server_id=child_perm.server_id,
                violation_type="wildcard_downgraded",
                detail=(
                    f"Wildcard downgraded to root's tool set: "
                    f"{sorted(root_perm.allowed_tools)}"
                ),
            ))
            narrowed_tools = root_perm.allowed_tools
            narrowed_wildcard = False
        elif child_perm.wildcard and root_perm.wildcard:
            # Both have wildcard — keep wildcard
            narrowed_tools = frozenset(["*"])
            narrowed_wildcard = True
        elif root_perm.wildcard:
            # Root has wildcard, child has explicit — keep child's set
            narrowed_tools = child_perm.allowed_tools
            narrowed_wildcard = False
        else:
            # Rule 2: Intersect tool sets
            narrowed_tools = child_perm.allowed_tools & root_perm.allowed_tools
            removed = child_perm.allowed_tools - root_perm.allowed_tools
            if removed:
                violations.append(CeilingViolation(
                    server_id=child_perm.server_id,
                    violation_type="tools_narrowed",
                    detail=f"Tools removed by ceiling: {sorted(removed)}",
                ))
            narrowed_wildcard = False

        # If no tools remain after intersection, remove the server
        if not narrowed_tools:
            violations.append(CeilingViolation(
                server_id=child_perm.server_id,
                violation_type="server_removed",
                detail="No tools remaining after ceiling — server removed",
            ))
            continue

        # Rule 3: Risk tier ceiling (child must be ≥ root)
        child_severity = _tier_severity(child_perm.risk_tier)
        root_severity = _tier_severity(root_perm.risk_tier)

        if child_severity < root_severity:
            # Child is less restrictive — elevate to root's tier
            violations.append(CeilingViolation(
                server_id=child_perm.server_id,
                violation_type="tier_elevated",
                detail=(
                    f"Risk tier elevated from {child_perm.risk_tier.value} "
                    f"to {root_perm.risk_tier.value}"
                ),
            ))
            final_tier = root_perm.risk_tier
        else:
            final_tier = child_perm.risk_tier

        resulting.append(MCPServerPermission(
            server_id=child_perm.server_id,
            allowed_tools=narrowed_tools,
            risk_tier=final_tier,
            wildcard=narrowed_wildcard,
        ))

    if violations:
        logger.warning(
            "Federation MCP ceiling enforced: %d violation(s) corrected, "
            "%d → %d servers",
            len(violations), len(child_permissions), len(resulting),
        )
        for v in violations:
            logger.warning("  Ceiling: [%s] %s — %s", v.server_id, v.violation_type, v.detail)

    return CeilingEnforcementResult(
        enforced=len(violations) > 0,
        violations=violations,
        original_server_count=len(child_permissions),
        resulting_server_count=len(resulting),
        resulting_permissions=resulting,
    )


def validate_child_within_ceiling(
    child_permissions: List[MCPServerPermission],
    root_permissions: List[MCPServerPermission],
) -> List[CeilingViolation]:
    """Validate that child permissions don't exceed root's ceiling.

    Pure validation — does not modify anything. Returns a list of
    violations. Empty list means the child is within bounds.

    This mirrors HIVE's ScopedSoulGenerator.validate_more_restrictive()
    but for MCP permissions specifically.
    """
    root_map = {p.server_id: p for p in root_permissions}
    violations: List[CeilingViolation] = []

    for child_perm in child_permissions:
        root_perm = root_map.get(child_perm.server_id)

        if root_perm is None:
            violations.append(CeilingViolation(
                server_id=child_perm.server_id,
                violation_type="server_not_permitted",
                detail=f"Server '{child_perm.server_id}' not in root's permitted set",
            ))
            continue

        # Wildcard escalation
        if child_perm.wildcard and not root_perm.wildcard:
            violations.append(CeilingViolation(
                server_id=child_perm.server_id,
                violation_type="wildcard_escalation",
                detail="Child has wildcard but root does not",
            ))

        # Tool set escalation (only check if neither has wildcard)
        if not child_perm.wildcard and not root_perm.wildcard:
            excess = child_perm.allowed_tools - root_perm.allowed_tools
            if excess:
                violations.append(CeilingViolation(
                    server_id=child_perm.server_id,
                    violation_type="tool_escalation",
                    detail=f"Tools exceed root's set: {sorted(excess)}",
                ))

        # Risk tier check (lower severity = less restrictive = violation)
        if _tier_severity(child_perm.risk_tier) < _tier_severity(root_perm.risk_tier):
            violations.append(CeilingViolation(
                server_id=child_perm.server_id,
                violation_type="tier_escalation",
                detail=(
                    f"Child tier {child_perm.risk_tier.value} is less restrictive "
                    f"than root tier {root_perm.risk_tier.value}"
                ),
            ))

    return violations


def apply_ceiling_to_evaluator(
    evaluator: MCPPermissionEvaluator,
    root_permissions: List[MCPServerPermission],
) -> CeilingEnforcementResult:
    """Apply the federation ceiling directly to an MCPPermissionEvaluator.

    Convenience function that extracts the evaluator's current permissions,
    enforces the ceiling, and loads the narrowed result back.

    This is the function called during Soul propagation to a peer.
    """
    # Extract current permissions from the evaluator
    child_permissions = []
    for server_id in evaluator.permitted_servers:
        perm = evaluator.get_server_permission(server_id)
        if perm:
            child_permissions.append(perm)

    result = enforce_mcp_ceiling(child_permissions, root_permissions)

    # Load narrowed permissions back into the evaluator
    if result.enforced:
        evaluator.load_permissions(
            result.resulting_permissions,
            soul_version=evaluator.soul_version,
        )

    return result


def narrow_soul_mcp_permissions(
    child_soul_data: dict,
    root_soul_data: dict,
) -> Dict[str, Any]:
    """Narrow a child Soul document's mcp_permissions against root.

    Operates on raw Soul dicts (as used in federation Soul push).
    Returns the narrowed mcp_permissions list and violation details.

    This is the function called in handle_soul_push() when a peer
    receives a Soul update from the root.
    """
    child_perms_raw = child_soul_data.get("mcp_permissions", [])
    root_perms_raw = root_soul_data.get("mcp_permissions", [])

    child_perms = [MCPServerPermission.from_dict(p) for p in child_perms_raw]
    root_perms = [MCPServerPermission.from_dict(p) for p in root_perms_raw]

    result = enforce_mcp_ceiling(child_perms, root_perms)

    # Convert back to dict format for Soul document
    narrowed_raw = []
    for perm in result.resulting_permissions:
        entry = {
            "server_id": perm.server_id,
            "allowed_tools": list(perm.allowed_tools),
            "risk_tier": perm.risk_tier.value,
        }
        narrowed_raw.append(entry)

    return {
        "mcp_permissions": narrowed_raw,
        "ceiling_enforced": result.enforced,
        "violations": [
            {"server_id": v.server_id, "type": v.violation_type, "detail": v.detail}
            for v in result.violations
        ],
    }
