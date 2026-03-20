# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
MCP Kill Switches — Feature-flag and per-server kill switch integration.

Two levels of kill switch for MCP:

    1. MCP_ALL (master switch) — disables ALL MCP tool invocations globally.
       Implemented as a feature flag: FEATURE_MCP.
       When OFF, the proxy refuses every request regardless of server or Soul.

    2. Per-server switch — each registered MCP server has a kill_switch_id
       (e.g., "MCP_SERVER_GITHUB"). When that flag is OFF, only that
       server is blocked.

Both are hot-toggleable through the War Room Kill Switches panel
without container restart.

Kill switch checks happen AFTER Soul permission (fail-closed) and
BEFORE any credential resolution or network call. This ensures that
a killed server never leaks credentials or generates outbound traffic.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ── Kill Switch Result ────────────────────────────────────────────

class MCPKillSwitchResult:
    """Result of a kill switch check."""

    __slots__ = ("allowed", "switch_id", "reason")

    def __init__(
        self, allowed: bool, switch_id: str = "", reason: str = ""
    ):
        self.allowed = allowed
        self.switch_id = switch_id
        self.reason = reason

    def to_dict(self):
        return {
            "allowed": self.allowed,
            "switch_id": self.switch_id,
            "reason": self.reason,
        }


# ── Kill Switch Gate ──────────────────────────────────────────────

# Master flag name — checked via feature_flags module
MCP_MASTER_FLAG = "FEATURE_MCP"


def check_mcp_master() -> MCPKillSwitchResult:
    """Check the global MCP master kill switch.

    Returns blocked result if FEATURE_MCP is disabled.
    """
    try:
        import feature_flags
        enabled = getattr(feature_flags, MCP_MASTER_FLAG, False)
    except ImportError:
        # feature_flags not available — fail closed
        enabled = False

    if not enabled:
        return MCPKillSwitchResult(
            allowed=False,
            switch_id=MCP_MASTER_FLAG,
            reason="MCP master kill switch is OFF — all MCP invocations blocked",
        )
    return MCPKillSwitchResult(allowed=True, switch_id=MCP_MASTER_FLAG)


def check_server_switch(kill_switch_id: str) -> MCPKillSwitchResult:
    """Check a per-server kill switch.

    The kill_switch_id is typically "MCP_SERVER_<SERVER_ID>" and is
    registered as a feature flag. Returns blocked if flag is OFF.

    If the flag doesn't exist in feature_flags, we fail-open on
    per-server switches (the master switch and Soul permissions
    already gate access). This allows new servers to be registered
    without requiring a code change to add their flag.
    """
    if not kill_switch_id:
        return MCPKillSwitchResult(allowed=True)

    try:
        import feature_flags
        # Per-server flags may not exist as module globals — check
        # persisted state directly for dynamic flags
        flag_name = f"FEATURE_{kill_switch_id}"
        enabled = getattr(feature_flags, flag_name, None)
        if enabled is None:
            # Dynamic flag — check persisted state
            enabled = feature_flags._persisted_state.get(flag_name, True)
    except ImportError:
        enabled = True  # Can't check — fail open (master switch covers)

    if not enabled:
        return MCPKillSwitchResult(
            allowed=False,
            switch_id=kill_switch_id,
            reason=f"MCP server kill switch '{kill_switch_id}' is OFF",
        )
    return MCPKillSwitchResult(allowed=True, switch_id=kill_switch_id)


def check_mcp_kill_switches(
    server_kill_switch_id: str = "",
) -> MCPKillSwitchResult:
    """Combined kill switch check: master + per-server.

    This is the single entry point called by the MCP proxy before
    every invocation. Checks master first, then per-server.
    """
    # Gate 1: Master switch
    master = check_mcp_master()
    if not master.allowed:
        return master

    # Gate 2: Per-server switch
    if server_kill_switch_id:
        server = check_server_switch(server_kill_switch_id)
        if not server.allowed:
            return server

    return MCPKillSwitchResult(allowed=True)
