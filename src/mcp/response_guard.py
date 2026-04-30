# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
MCP Response Guard — Scrubs MCP server responses before returning to agent.

Prevents credential leakage and data exfiltration via MCP tool responses.
A malicious or misconfigured MCP server could:

    1. Echo back credentials passed in request headers
    2. Include API keys or tokens in response payloads
    3. Return excessively large responses (resource exhaustion)
    4. Include prompt injection payloads in tool results
    5. Encode sensitive data in non-obvious formats

The guard inspects responses AFTER the MCP call returns but BEFORE
the result is passed to the agent or stored in a receipt.

This module does NOT modify receipts — it modifies the result that
flows back to the proxy/agent. Receipts get the sanitized version.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class GuardResult:
    """Result of response guard inspection."""
    clean: bool
    original_size: int = 0
    sanitized_size: int = 0
    warnings: List[str] = field(default_factory=list)
    redaction_count: int = 0


# Maximum response sizes
_MAX_RESPONSE_SIZE = 500_000      # 500KB — larger responses are truncated
_MAX_RESPONSE_ITEMS = 1000        # Max items in list responses

# Patterns that look like leaked credentials in responses
_CREDENTIAL_PATTERNS = [
    # API keys / tokens (generic patterns)
    re.compile(r"\b(sk-[a-zA-Z0-9]{20,})\b"),           # OpenAI-style
    re.compile(r"\b(ghp_[a-zA-Z0-9]{36,})\b"),           # GitHub PAT
    re.compile(r"\b(gho_[a-zA-Z0-9]{36,})\b"),           # GitHub OAuth
    re.compile(r"\b(xoxb-[a-zA-Z0-9\-]+)\b"),            # Slack bot token
    re.compile(r"\b(xoxp-[a-zA-Z0-9\-]+)\b"),            # Slack user token
    re.compile(r"\b(AKIA[A-Z0-9]{16})\b"),               # AWS access key
    re.compile(r"\b(eyJ[a-zA-Z0-9_-]{20,}\.eyJ[a-zA-Z0-9_-]{20,})\b"),  # JWT
    re.compile(r"\bBearer\s+[a-zA-Z0-9\-._~+/]+=*\b"),  # Bearer token
    re.compile(r"\bBasic\s+[A-Za-z0-9+/]+=*\b"),        # Basic auth
    # Connection strings
    re.compile(r"(postgres|mysql|mongodb)://[^\s]+@[^\s]+"),
    re.compile(r"(redis|amqp)://[^\s]*:[^\s]*@"),
    # Private keys
    re.compile(r"-----BEGIN\s+(RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"-----BEGIN\s+CERTIFICATE-----"),
]

# Patterns that look like prompt injection in responses
_RESPONSE_INJECTION_PATTERNS = [
    re.compile(r"<\|?system\|?>", re.IGNORECASE),
    re.compile(r"\[SYSTEM\]", re.IGNORECASE),
    re.compile(r"<<\s*SYS\s*>>", re.IGNORECASE),
    re.compile(r"<\|im_start\|>", re.IGNORECASE),
    re.compile(r"IMPORTANT:\s*(ignore|forget|override|disregard)", re.IGNORECASE),
    re.compile(r"NEW\s+INSTRUCTIONS?:", re.IGNORECASE),
]


class MCPResponseGuard:
    """Inspects and sanitizes MCP server responses.

    Applied to every MCP call result before it reaches the agent
    or is stored in a receipt.
    """

    def __init__(
        self,
        max_response_size: int = _MAX_RESPONSE_SIZE,
        scrub_credentials: bool = True,
        scrub_injections: bool = True,
        truncate_oversized: bool = True,
    ):
        self._max_size = max_response_size
        self._scrub_creds = scrub_credentials
        self._scrub_injections = scrub_injections
        self._truncate = truncate_oversized

    def inspect(
        self,
        response: Any,
        server_id: str = "",
        tool_name: str = "",
    ) -> tuple:
        """Inspect and sanitize an MCP response.

        Args:
            response: The raw MCP tool result (string, dict, list, etc.)
            server_id: For logging attribution
            tool_name: For logging attribution

        Returns:
            Tuple of (sanitized_response, GuardResult)
        """
        warnings: List[str] = []
        redaction_count = 0

        # Convert to string for inspection
        if isinstance(response, str):
            text = response
        elif isinstance(response, (dict, list)):
            import json
            text = json.dumps(response)
        else:
            text = str(response)

        original_size = len(text)

        # Size check / truncation
        if self._truncate and original_size > self._max_size:
            text = text[:self._max_size]
            warnings.append(
                f"Response truncated: {original_size:,} → {self._max_size:,} chars"
            )
            logger.warning(
                "MCP response truncated for %s:%s (%d → %d chars)",
                server_id, tool_name, original_size, self._max_size,
            )

        # Credential scrubbing
        if self._scrub_creds:
            for pattern in _CREDENTIAL_PATTERNS:
                matches = pattern.findall(text)
                if matches:
                    text = pattern.sub("[CREDENTIAL_REDACTED]", text)
                    redaction_count += len(matches)
                    warnings.append(
                        f"Credential pattern redacted: {len(matches)} match(es)"
                    )

        # Prompt injection detection in responses
        if self._scrub_injections:
            for pattern in _RESPONSE_INJECTION_PATTERNS:
                if pattern.search(text):
                    text = pattern.sub("[INJECTION_MARKER_REMOVED]", text)
                    redaction_count += 1
                    warnings.append("Prompt injection marker removed from response")

        if redaction_count > 0:
            logger.warning(
                "MCP response guard: %d redaction(s) for %s:%s — %s",
                redaction_count, server_id, tool_name,
                "; ".join(warnings),
            )

        # Reconstruct original type if possible
        sanitized: Any
        if isinstance(response, str):
            sanitized = text
        elif isinstance(response, (dict, list)):
            try:
                import json
                sanitized = json.loads(text)
            except (ValueError, TypeError):
                sanitized = text
        else:
            sanitized = text

        guard_result = GuardResult(
            clean=redaction_count == 0 and len(warnings) == 0,
            original_size=original_size,
            sanitized_size=len(text),
            warnings=warnings,
            redaction_count=redaction_count,
        )

        return sanitized, guard_result
