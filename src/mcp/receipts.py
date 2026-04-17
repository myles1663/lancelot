# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
MCP Receipt System — Immutable audit trail for every MCP tool invocation.

Every MCP operation generates a receipt, whether it succeeds or is blocked.
This is a HARD REQUIREMENT — the proxy MUST NOT complete an invocation
without generating a receipt. The proxy constructor enforces this by
requiring a receipt manager at init time.

Receipt types:
    MCP_TOOL_CALL     — successful tool invocation (inputs, outputs, duration)
    MCP_TOOL_BLOCKED  — invocation blocked by governance (Soul, kill switch,
                        network, argument screening, or receipt write failure)

Fourth fail-closed gate:
    If receipt persistence fails (disk error, DB corruption), the tool
    invocation itself MUST fail. A governance system that silently succeeds
    when its audit trail is broken is not governed. The proxy enforces this
    by catching receipt write errors and surfacing a RECEIPT_WRITE_FAILURE
    block receipt (best-effort) before returning an error to the caller.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Receipt action type values (must match ActionType enum in shared/receipts.py)
MCP_TOOL_CALL = "mcp_tool_call"
MCP_TOOL_BLOCKED = "mcp_tool_blocked"


class MCPReceiptManager:
    """Creates and persists MCP-specific receipts.

    Wraps the core ReceiptService to provide typed receipt creation
    for MCP tool calls and blocks.

    The proxy MUST hold a reference to this manager. It is a constructor
    requirement, not an optional integration. No receipt manager → no proxy.
    """

    def __init__(self, receipt_service):
        """
        Args:
            receipt_service: The core ReceiptService (from src.shared.receipts).
                            Must not be None — enforced at proxy construction.
        """
        if receipt_service is None:
            raise ValueError(
                "MCPReceiptManager requires a ReceiptService. "
                "MCP cannot operate without an audit trail."
            )
        self._service = receipt_service

    def record_tool_call(
        self,
        server_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        result: Any,
        duration_ms: int,
        risk_tier: str = "T2",
        soul_version: str = "",
        quest_id: str = "",
        parent_id: str = "",
        operator_id: str = "",
        session_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Record a successful MCP tool invocation.

        Returns the receipt ID.
        Raises on persistence failure — caller MUST handle this as
        a governance failure (the invocation result should be discarded).
        """
        receipt_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        # Sanitize arguments for storage — strip any credential-looking values
        safe_args = _redact_sensitive_keys(arguments)

        self._service.create(
            id=receipt_id,
            timestamp=now,
            action_type=MCP_TOOL_CALL,
            action_name=f"mcp:{server_id}:{tool_name}",
            inputs={
                "server_id": server_id,
                "tool_name": tool_name,
                "arguments": safe_args,
                "risk_tier": risk_tier,
            },
            outputs={"result_summary": _truncate(str(result), 2000)},
            status="success",
            duration_ms=duration_ms,
            tier=0,  # DETERMINISTIC — MCP proxy is pure routing
            quest_id=quest_id or None,
            parent_id=parent_id or None,
            operator_id=operator_id or None,
            session_id=session_id or None,
            metadata={
                "soul_version": soul_version,
                "mcp_server_id": server_id,
                "mcp_tool_name": tool_name,
                **(metadata or {}),
            },
        )

        logger.debug(
            "MCP receipt: tool_call %s:%s (receipt=%s, tier=%s, %dms)",
            server_id, tool_name, receipt_id[:8], risk_tier, duration_ms,
        )
        return receipt_id

    def record_tool_blocked(
        self,
        server_id: str,
        tool_name: str,
        block_reason: str,
        block_gate: str,
        arguments: Optional[Dict[str, Any]] = None,
        risk_tier: str = "T2",
        soul_version: str = "",
        quest_id: str = "",
        operator_id: str = "",
        session_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Record a blocked MCP tool invocation.

        block_gate identifies which governance gate blocked the call:
            "soul_permission" — Soul didn't permit server or tool
            "kill_switch"     — master or per-server kill switch off
            "network"         — endpoint domain not in allowlist
            "argument_screen" — argument content flagged by InputSanitizer
            "receipt_failure" — receipt persistence failed (fourth gate)
            "server_status"   — server suspended or in error state

        Returns the receipt ID.
        This method is best-effort — if IT fails, we log and move on
        since we're already in a failure path.
        """
        receipt_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        safe_args = _redact_sensitive_keys(arguments or {})

        try:
            self._service.create(
                id=receipt_id,
                timestamp=now,
                action_type=MCP_TOOL_BLOCKED,
                action_name=f"mcp:blocked:{server_id}:{tool_name}",
                inputs={
                    "server_id": server_id,
                    "tool_name": tool_name,
                    "arguments": safe_args,
                    "risk_tier": risk_tier,
                },
                outputs={
                    "blocked": True,
                    "block_reason": block_reason,
                    "block_gate": block_gate,
                },
                status="failure",
                duration_ms=0,
                tier=0,  # DETERMINISTIC
                quest_id=quest_id or None,
                operator_id=operator_id or None,
                session_id=session_id or None,
                metadata={
                    "soul_version": soul_version,
                    "mcp_server_id": server_id,
                    "mcp_tool_name": tool_name,
                    "block_gate": block_gate,
                    **(metadata or {}),
                },
            )
        except Exception as e:
            # Best-effort — we're already on the failure path
            logger.error(
                "Failed to persist MCP block receipt for %s:%s: %s",
                server_id, tool_name, e,
            )
            return ""

        logger.info(
            "MCP receipt: BLOCKED %s:%s gate=%s reason=%s (receipt=%s)",
            server_id, tool_name, block_gate, block_reason[:80], receipt_id[:8],
        )
        return receipt_id

    def record_receipt_write_failure(
        self,
        server_id: str,
        tool_name: str,
        original_error: str,
        soul_version: str = "",
        operator_id: str = "",
        session_id: str = "",
    ) -> str:
        """Record that a receipt write failed — the fourth fail-closed gate.

        This is called when record_tool_call() raises. The proxy catches
        the error, calls this method (best-effort), and then returns an
        error to the caller instead of the tool result.

        A governance system whose audit trail is broken is not governed.
        """
        return self.record_tool_blocked(
            server_id=server_id,
            tool_name=tool_name,
            block_reason=f"Receipt persistence failed: {original_error}",
            block_gate="receipt_failure",
            soul_version=soul_version,
            operator_id=operator_id,
            session_id=session_id,
        )


# ── Helpers ──────────────────────────────────────────────────────

# Keys whose values should be redacted in receipt storage
_SENSITIVE_KEY_PATTERNS = frozenset({
    "token", "key", "secret", "password", "credential",
    "auth", "api_key", "apikey", "access_token", "refresh_token",
})


def _redact_sensitive_keys(data: Dict[str, Any]) -> Dict[str, Any]:
    """Shallow redaction of keys that look like credentials."""
    if not data:
        return {}
    result = {}
    for k, v in data.items():
        key_lower = k.lower()
        if any(pat in key_lower for pat in _SENSITIVE_KEY_PATTERNS):
            result[k] = "[REDACTED]"
        elif isinstance(v, dict):
            result[k] = _redact_sensitive_keys(v)
        else:
            result[k] = v
    return result


def _truncate(text: str, max_len: int) -> str:
    """Truncate a string with ellipsis indicator."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."
