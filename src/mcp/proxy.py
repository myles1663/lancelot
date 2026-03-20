# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Governed MCP Proxy — The single entry point for all MCP tool invocations.

Every MCP call routes through the full governance stack in this order:

    Gate 1: Soul Permission       — is this server+tool permitted by the Soul?
    Gate 2: Kill Switch           — is FEATURE_MCP on? Is the server's switch on?
    Gate 3: Server Status         — is the server active (not suspended/error)?
    Gate 4: Network Allowlist     — is the server's endpoint domain allowed?
    Gate 5: Argument Screening    — deep injection detection via MCPArgumentScreener
                                    (SQL, path traversal, command injection, prompt
                                    injection, SSRF, NoSQL, size limits) + platform
                                    InputSanitizer (banned phrases, homoglyphs)
    Gate 6: Credential Resolution — retrieve auth from Vault (scoped access)
    Gate 7: MCP Execution         — send the request to the MCP server
    Gate 7b: Response Guard       — scrub credentials, prompt injection markers,
                                    and oversized payloads from MCP responses
    Gate 8: Receipt Persistence   — MANDATORY. If receipt write fails, the
                                    invocation result is DISCARDED and an error
                                    is returned. A governance system whose audit
                                    trail is broken is not governed.

Fail-closed on every gate. No shortcutting, no fallbacks.

Constructor requirements:
    - MCPPermissionEvaluator (Soul gate)
    - MCPServerRegistry (server config + credential resolution)
    - MCPReceiptManager (MANDATORY — proxy refuses to construct without it)
    - MCPArgumentScreener (optional — if None, Gate 5 uses basic InputSanitizer only)
    - MCPResponseGuard (optional — if None, Gate 7b is skipped)
    - NetworkInterceptor (optional — if None, Gate 4 is skipped)
    - InputSanitizer (optional — if None and no screener, Gate 5 is skipped)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from src.mcp.argument_screen import MCPArgumentScreener
from src.mcp.client import MCPCallResult, MCPClient, MCPToolSpec
from src.mcp.kill_switches import check_mcp_kill_switches
from src.mcp.permissions import MCPPermissionEvaluator, PermissionCheckResult
from src.mcp.receipts import MCPReceiptManager
from src.mcp.registry import (
    MCPAuthType,
    MCPServerConfig,
    MCPServerRegistry,
    MCPServerStatus,
)
from src.mcp.response_guard import MCPResponseGuard

logger = logging.getLogger(__name__)


class GovernedMCPProxy:
    """Governed proxy for MCP tool invocations.

    All MCP traffic flows through this proxy. No MCP server is reachable
    without passing every governance gate. The proxy is the enforcement
    boundary — the client layer below it is a pure transport.
    """

    def __init__(
        self,
        permission_evaluator: MCPPermissionEvaluator,
        registry: MCPServerRegistry,
        receipt_manager: MCPReceiptManager,
        argument_screener: Optional[MCPArgumentScreener] = None,
        response_guard: Optional[MCPResponseGuard] = None,
        network_interceptor=None,
        input_sanitizer=None,
        default_timeout_s: float = 30.0,
    ):
        """
        Args:
            permission_evaluator: Soul-based MCP permission checker.
            registry: MCP server configuration registry.
            receipt_manager: MANDATORY receipt manager. Proxy will not
                            construct without one.
            argument_screener: Deep argument screening (SQL, path traversal,
                              command injection, prompt injection, SSRF).
                              If None, falls back to basic InputSanitizer.
            response_guard: Response scrubbing (credential leak prevention,
                           prompt injection removal, size limits).
                           If None, Gate 7b is skipped.
            network_interceptor: NetworkInterceptor for domain allowlist
                                checks. If None, Gate 4 is skipped.
            input_sanitizer: InputSanitizer for basic argument screening.
                            Used as fallback if no argument_screener.
            default_timeout_s: Default request timeout for MCP calls.
        """
        if receipt_manager is None:
            raise ValueError(
                "GovernedMCPProxy requires a receipt_manager. "
                "MCP cannot operate without an audit trail. "
                "Build receipts.py before proxy.py."
            )
        self._permissions = permission_evaluator
        self._registry = registry
        self._receipts = receipt_manager
        self._screener = argument_screener
        self._response_guard = response_guard
        self._network = network_interceptor
        self._sanitizer = input_sanitizer
        self._default_timeout_s = default_timeout_s

    async def invoke_tool(
        self,
        server_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        quest_id: str = "",
        parent_receipt_id: str = "",
    ) -> MCPCallResult:
        """Invoke an MCP tool through the full governance pipeline.

        This is the ONLY entry point for MCP tool execution. Every call
        passes through all 8 gates in order.

        Args:
            server_id: Registered MCP server ID.
            tool_name: Tool to invoke on the server.
            arguments: Tool arguments.
            quest_id: Optional quest/task ID for receipt grouping.
            parent_receipt_id: Optional parent receipt for hierarchy.

        Returns:
            MCPCallResult. On governance block, success=False with
            block reason in error field.
        """
        soul_version = self._permissions.soul_version

        # ── Gate 1: Soul Permission ──────────────────────────────
        perm_result = self._permissions.check_tool_access(server_id, tool_name)
        if not perm_result.allowed:
            self._receipts.record_tool_blocked(
                server_id=server_id,
                tool_name=tool_name,
                block_reason=perm_result.block_reason,
                block_gate="soul_permission",
                arguments=arguments,
                risk_tier=perm_result.risk_tier.value,
                soul_version=soul_version,
                quest_id=quest_id,
            )
            return MCPCallResult(
                success=False,
                error=f"Soul permission denied: {perm_result.block_reason}",
                server_id=server_id,
                tool_name=tool_name,
            )

        risk_tier = perm_result.risk_tier.value

        # ── Gate 2: Kill Switch ──────────────────────────────────
        server_config = self._registry.get(server_id)
        kill_switch_id = server_config.kill_switch_id if server_config else ""

        kill_result = check_mcp_kill_switches(kill_switch_id)
        if not kill_result.allowed:
            self._receipts.record_tool_blocked(
                server_id=server_id,
                tool_name=tool_name,
                block_reason=kill_result.reason,
                block_gate="kill_switch",
                arguments=arguments,
                risk_tier=risk_tier,
                soul_version=soul_version,
                quest_id=quest_id,
            )
            return MCPCallResult(
                success=False,
                error=f"Kill switch: {kill_result.reason}",
                server_id=server_id,
                tool_name=tool_name,
            )

        # ── Gate 3: Server Status ────────────────────────────────
        if server_config is None:
            self._receipts.record_tool_blocked(
                server_id=server_id,
                tool_name=tool_name,
                block_reason=f"Server '{server_id}' not registered",
                block_gate="server_status",
                arguments=arguments,
                risk_tier=risk_tier,
                soul_version=soul_version,
                quest_id=quest_id,
            )
            return MCPCallResult(
                success=False,
                error=f"Server '{server_id}' not registered in MCP registry",
                server_id=server_id,
                tool_name=tool_name,
            )

        if server_config.status in (
            MCPServerStatus.SUSPENDED, MCPServerStatus.ERROR
        ):
            self._receipts.record_tool_blocked(
                server_id=server_id,
                tool_name=tool_name,
                block_reason=f"Server status: {server_config.status.value}",
                block_gate="server_status",
                arguments=arguments,
                risk_tier=risk_tier,
                soul_version=soul_version,
                quest_id=quest_id,
            )
            return MCPCallResult(
                success=False,
                error=f"Server '{server_id}' is {server_config.status.value}",
                server_id=server_id,
                tool_name=tool_name,
            )

        # Use server's default risk tier if Soul doesn't override
        if risk_tier == "T2" and server_config.default_risk_tier.value != "T2":
            risk_tier = server_config.default_risk_tier.value

        # ── Gate 4: Network Allowlist ────────────────────────────
        if self._network and server_config.endpoint:
            if not self._network.check_url(server_config.endpoint):
                self._receipts.record_tool_blocked(
                    server_id=server_id,
                    tool_name=tool_name,
                    block_reason=f"Endpoint domain not in network allowlist: {server_config.endpoint}",
                    block_gate="network",
                    arguments=arguments,
                    risk_tier=risk_tier,
                    soul_version=soul_version,
                    quest_id=quest_id,
                )
                return MCPCallResult(
                    success=False,
                    error=f"Network allowlist blocked: {server_config.endpoint}",
                    server_id=server_id,
                    tool_name=tool_name,
                )

        # ── Gate 5: Argument Screening ───────────────────────────
        if arguments:
            screen_error = self._run_argument_screening(
                server_id, tool_name, arguments, risk_tier,
                soul_version, quest_id,
            )
            if screen_error:
                return screen_error

        # ── Gate 6: Credential Resolution ────────────────────────
        auth_headers = {}
        if server_config.vault_key:
            try:
                credential = self._registry.resolve_credential(server_id)
                if credential:
                    auth_headers = self._build_auth_headers(
                        server_config, credential
                    )
            except (KeyError, PermissionError) as e:
                self._receipts.record_tool_blocked(
                    server_id=server_id,
                    tool_name=tool_name,
                    block_reason=f"Credential resolution failed: {e}",
                    block_gate="credential",
                    arguments=arguments,
                    risk_tier=risk_tier,
                    soul_version=soul_version,
                    quest_id=quest_id,
                )
                return MCPCallResult(
                    success=False,
                    error="Credential resolution failed",
                    server_id=server_id,
                    tool_name=tool_name,
                )

        # ── Gate 7: MCP Execution ────────────────────────────────
        client = MCPClient(
            endpoint=server_config.endpoint,
            auth_headers=auth_headers,
            timeout_s=self._default_timeout_s,
        )

        call_result = await client.call_tool(
            tool_name=tool_name,
            arguments=arguments,
            server_id=server_id,
        )

        # ── Gate 7b: Response Guard ──────────────────────────────
        if call_result.success and self._response_guard:
            sanitized_result, guard_result = self._response_guard.inspect(
                call_result.result,
                server_id=server_id,
                tool_name=tool_name,
            )
            call_result.result = sanitized_result
            if not guard_result.clean:
                logger.info(
                    "Response guard applied for %s:%s — %d redaction(s): %s",
                    server_id, tool_name,
                    guard_result.redaction_count,
                    "; ".join(guard_result.warnings),
                )

        # ── Gate 8: Receipt Persistence (MANDATORY) ──────────────
        if call_result.success:
            try:
                self._receipts.record_tool_call(
                    server_id=server_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    result=call_result.result,
                    duration_ms=call_result.duration_ms,
                    risk_tier=risk_tier,
                    soul_version=soul_version,
                    quest_id=quest_id,
                    parent_id=parent_receipt_id,
                )
            except Exception as e:
                # FOURTH FAIL-CLOSED GATE: Receipt write failed.
                # The invocation result is DISCARDED. A governance system
                # whose audit trail is broken is not governed.
                logger.critical(
                    "RECEIPT WRITE FAILURE for %s:%s — discarding result: %s",
                    server_id, tool_name, e,
                )
                self._receipts.record_receipt_write_failure(
                    server_id=server_id,
                    tool_name=tool_name,
                    original_error=str(e),
                    soul_version=soul_version,
                )
                return MCPCallResult(
                    success=False,
                    error="Governance failure: receipt persistence failed. Result discarded.",
                    duration_ms=call_result.duration_ms,
                    server_id=server_id,
                    tool_name=tool_name,
                )
        else:
            # Record the failed call attempt
            self._receipts.record_tool_blocked(
                server_id=server_id,
                tool_name=tool_name,
                block_reason=f"MCP call failed: {call_result.error}",
                block_gate="mcp_execution",
                arguments=arguments,
                risk_tier=risk_tier,
                soul_version=soul_version,
                quest_id=quest_id,
            )

        return call_result

    async def list_server_tools(
        self, server_id: str
    ) -> List[MCPToolSpec]:
        """Discover tools on a registered MCP server.

        Subject to Soul permission and kill switch checks at the
        server level (not individual tool level).
        """
        # Check server-level Soul permission
        perm = self._permissions.check_server_access(server_id)
        if not perm.allowed:
            logger.warning(
                "Tool discovery blocked for %s: %s",
                server_id, perm.block_reason,
            )
            return []

        # Check kill switches
        config = self._registry.get(server_id)
        kill_id = config.kill_switch_id if config else ""
        kill = check_mcp_kill_switches(kill_id)
        if not kill.allowed:
            return []

        if config is None or not config.endpoint:
            return []

        # Resolve credentials for discovery request
        auth_headers = {}
        if config.vault_key:
            try:
                credential = self._registry.resolve_credential(server_id)
                if credential:
                    auth_headers = self._build_auth_headers(config, credential)
            except Exception:
                return []

        client = MCPClient(
            endpoint=config.endpoint,
            auth_headers=auth_headers,
            timeout_s=self._default_timeout_s,
        )
        return await client.list_tools()

    def _run_argument_screening(
        self,
        server_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        risk_tier: str,
        soul_version: str,
        quest_id: str,
    ) -> Optional[MCPCallResult]:
        """Run argument screening through deep screener or basic sanitizer.

        Returns an MCPCallResult error if blocked, or None if clean.
        """
        # Prefer deep screener if available
        if self._screener:
            screen_result = self._screener.screen(
                arguments, server_id=server_id, tool_name=tool_name,
            )
            if not screen_result.passed:
                reason = "; ".join(screen_result.violations[:3])
                self._receipts.record_tool_blocked(
                    server_id=server_id,
                    tool_name=tool_name,
                    block_reason=f"Argument screening ({screen_result.severity}): {reason}",
                    block_gate="argument_screen",
                    arguments=arguments,
                    risk_tier=risk_tier,
                    soul_version=soul_version,
                    quest_id=quest_id,
                    metadata={
                        "severity": screen_result.severity,
                        "violations": screen_result.violations,
                    },
                )
                return MCPCallResult(
                    success=False,
                    error=f"Argument screening blocked ({screen_result.severity}): {reason}",
                    server_id=server_id,
                    tool_name=tool_name,
                )
            return None

        # Fallback: basic InputSanitizer
        if self._sanitizer:
            for key, value in arguments.items():
                if isinstance(value, str):
                    sanitized = self._sanitizer.sanitize(value)
                    if sanitized.startswith("[SUSPICIOUS INPUT DETECTED]"):
                        reason = f"Suspicious content in argument '{key}'"
                        self._receipts.record_tool_blocked(
                            server_id=server_id,
                            tool_name=tool_name,
                            block_reason=reason,
                            block_gate="argument_screen",
                            arguments=arguments,
                            risk_tier=risk_tier,
                            soul_version=soul_version,
                            quest_id=quest_id,
                        )
                        return MCPCallResult(
                            success=False,
                            error=f"Argument screening: {reason}",
                            server_id=server_id,
                            tool_name=tool_name,
                        )
                    if "[REDACTED]" in sanitized:
                        reason = f"Banned phrase detected in argument '{key}'"
                        self._receipts.record_tool_blocked(
                            server_id=server_id,
                            tool_name=tool_name,
                            block_reason=reason,
                            block_gate="argument_screen",
                            arguments=arguments,
                            risk_tier=risk_tier,
                            soul_version=soul_version,
                            quest_id=quest_id,
                        )
                        return MCPCallResult(
                            success=False,
                            error=f"Argument screening: {reason}",
                            server_id=server_id,
                            tool_name=tool_name,
                        )
        return None

    @staticmethod
    def _build_auth_headers(
        config: MCPServerConfig, credential: str
    ) -> Dict[str, str]:
        """Build HTTP auth headers from config + resolved credential."""
        if config.auth_type == MCPAuthType.API_KEY:
            return {"Authorization": f"Bearer {credential}"}
        elif config.auth_type == MCPAuthType.BASIC:
            import base64
            encoded = base64.b64encode(credential.encode()).decode()
            return {"Authorization": f"Basic {encoded}"}
        elif config.auth_type == MCPAuthType.CUSTOM_HEADER:
            header_name = config.auth_header or "X-API-Key"
            return {header_name: credential}
        elif config.auth_type == MCPAuthType.OAUTH2:
            return {"Authorization": f"Bearer {credential}"}
        return {}
