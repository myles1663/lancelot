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

from src.core.kill_switches import (
    KillSwitchDecision,
    KillSwitchScope,
    evaluate_feature_flag_kill_switch,
)

logger = logging.getLogger(__name__)


# ── Kill Switch Result ────────────────────────────────────────────

MCPKillSwitchResult = KillSwitchDecision


# ── Kill Switch Gate ──────────────────────────────────────────────

# Master flag name — checked via feature_flags module
MCP_MASTER_FLAG = "FEATURE_MCP"


def check_mcp_master() -> MCPKillSwitchResult:
    """Check the global MCP master kill switch.

    Returns blocked result if FEATURE_MCP is disabled.
    """
    return evaluate_feature_flag_kill_switch(
        flag_name=MCP_MASTER_FLAG,
        switch_id=MCP_MASTER_FLAG,
        scope=KillSwitchScope.MCP_MASTER,
        missing_default=False,
        blocked_reason="MCP master kill switch is OFF — all MCP invocations blocked",
    )


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
        return MCPKillSwitchResult(
            allowed=True,
            scope=KillSwitchScope.MCP_SERVER,
        )

    return evaluate_feature_flag_kill_switch(
        flag_name=f"FEATURE_{kill_switch_id}",
        switch_id=kill_switch_id,
        scope=KillSwitchScope.MCP_SERVER,
        missing_default=True,
        blocked_reason=f"MCP server kill switch '{kill_switch_id}' is OFF",
    )


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

    return MCPKillSwitchResult(allowed=True, scope=KillSwitchScope.MCP_SERVER)
