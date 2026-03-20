# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
MCP Argument Screener — Deep inspection of MCP tool arguments.

Extends the platform's InputSanitizer with MCP-specific threat patterns:

    1. Prompt injection via tool arguments (tool args → LLM context)
    2. SQL/NoSQL injection in query-type arguments
    3. Path traversal in file/path arguments
    4. SSRF via URL arguments (redirect to internal services)
    5. Command injection via shell-like arguments
    6. Excessive argument size (resource exhaustion)

The screener operates on the ARGUMENT VALUES, not the tool spec.
It runs after Soul permission and kill switch checks (Gate 5 in proxy).

Screening is fail-open by default for individual patterns (flag + log)
but fail-closed if multiple patterns trigger on the same invocation
(compound attack indicator).
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass
class ScreeningResult:
    """Result of argument screening."""
    passed: bool
    violations: List[str] = field(default_factory=list)
    severity: str = "none"  # none, low, medium, high, critical

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "violations": self.violations,
            "severity": self.severity,
        }


# ── Pattern Libraries ────────────────────────────────────────────

# SQL injection patterns (common across SQL dialects)
_SQL_INJECTION_PATTERNS = [
    re.compile(r"('\s*OR\s+'1'\s*=\s*'1)", re.IGNORECASE),
    re.compile(r"('\s*OR\s+1\s*=\s*1)", re.IGNORECASE),
    re.compile(r";\s*(DROP|DELETE|TRUNCATE|ALTER|UPDATE|INSERT)\s+", re.IGNORECASE),
    re.compile(r"UNION\s+(ALL\s+)?SELECT\s+", re.IGNORECASE),
    re.compile(r"--\s*$", re.MULTILINE),  # SQL comment terminator
    re.compile(r"/\*.*?\*/", re.DOTALL),  # Block comment
    re.compile(r"\bEXEC\s*\(", re.IGNORECASE),
    re.compile(r"\bxp_cmdshell\b", re.IGNORECASE),
    re.compile(r"\bWAITFOR\s+DELAY\b", re.IGNORECASE),
    re.compile(r"\bBENCHMARK\s*\(", re.IGNORECASE),
    re.compile(r"\bSLEEP\s*\(\s*\d", re.IGNORECASE),
]

# Path traversal patterns
_PATH_TRAVERSAL_PATTERNS = [
    re.compile(r"\.\./"),             # Unix relative path
    re.compile(r"\.\.\\"),            # Windows relative path
    re.compile(r"%2e%2e[/\\%]", re.IGNORECASE),  # URL-encoded
    re.compile(r"\.\.%2f", re.IGNORECASE),
    re.compile(r"%2e%2e/", re.IGNORECASE),
    re.compile(r"/etc/(passwd|shadow|hosts)"),
    re.compile(r"[A-Za-z]:\\(Windows|System32|Users)", re.IGNORECASE),
    re.compile(r"\0"),                # Null byte injection
]

# Command injection patterns
_COMMAND_INJECTION_PATTERNS = [
    re.compile(r";\s*(cat|ls|dir|whoami|id|uname|curl|wget|nc|ncat)\b", re.IGNORECASE),
    re.compile(r"\|\s*(cat|ls|dir|whoami|id|bash|sh|cmd|powershell)\b", re.IGNORECASE),
    re.compile(r"`[^`]+`"),           # Backtick command substitution
    re.compile(r"\$\([^)]+\)"),       # $() command substitution
    re.compile(r"\$\{[^}]+\}"),       # ${} variable expansion
    re.compile(r">\s*/dev/tcp/", re.IGNORECASE),  # Bash reverse shell
    re.compile(r"\b(eval|exec|system|popen|subprocess)\s*\(", re.IGNORECASE),
]

# Prompt injection patterns specific to MCP context
# (tool arguments that will be passed to LLM in tool results)
_PROMPT_INJECTION_PATTERNS = [
    re.compile(r"<\|?system\|?>", re.IGNORECASE),
    re.compile(r"\[SYSTEM\]", re.IGNORECASE),
    re.compile(r"<<\s*SYS\s*>>", re.IGNORECASE),
    re.compile(r"Human:\s*", re.IGNORECASE),
    re.compile(r"Assistant:\s*", re.IGNORECASE),
    re.compile(r"<\|im_start\|>", re.IGNORECASE),
    re.compile(r"<\|im_end\|>", re.IGNORECASE),
    re.compile(r"IMPORTANT:\s*ignore", re.IGNORECASE),
    re.compile(r"CRITICAL:\s*override", re.IGNORECASE),
    re.compile(r"#{3,}\s*(system|instruction|override)", re.IGNORECASE),
]

# NoSQL injection patterns (MongoDB, etc.)
_NOSQL_INJECTION_PATTERNS = [
    re.compile(r"\$(?:gt|gte|lt|lte|ne|in|nin|or|and|not|nor|exists|regex|where)\b"),
    re.compile(r"\{\s*\$where\s*:"),
    re.compile(r"this\.\w+\s*=="),
]

# Maximum argument sizes
_MAX_STRING_LENGTH = 50_000    # 50KB per string argument
_MAX_TOTAL_SIZE = 200_000      # 200KB total across all arguments


class MCPArgumentScreener:
    """Deep argument screening for MCP tool invocations.

    Goes beyond the platform InputSanitizer with MCP-specific
    injection patterns. Designed to catch attacks that route through
    tool arguments into downstream systems.
    """

    def __init__(
        self,
        input_sanitizer=None,
        enable_sql_screen: bool = True,
        enable_path_screen: bool = True,
        enable_command_screen: bool = True,
        enable_prompt_screen: bool = True,
        enable_nosql_screen: bool = True,
        enable_url_screen: bool = True,
        enable_size_screen: bool = True,
        compound_threshold: int = 2,
    ):
        """
        Args:
            input_sanitizer: Platform InputSanitizer for base screening.
            enable_*_screen: Toggle individual pattern categories.
            compound_threshold: Number of pattern categories that must
                trigger to force a hard block (vs. flag + log).
        """
        self._sanitizer = input_sanitizer
        self._enable_sql = enable_sql_screen
        self._enable_path = enable_path_screen
        self._enable_command = enable_command_screen
        self._enable_prompt = enable_prompt_screen
        self._enable_nosql = enable_nosql_screen
        self._enable_url = enable_url_screen
        self._enable_size = enable_size_screen
        self._compound_threshold = compound_threshold

    def screen(
        self,
        arguments: Dict[str, Any],
        server_id: str = "",
        tool_name: str = "",
    ) -> ScreeningResult:
        """Screen all arguments for an MCP tool invocation.

        Returns a ScreeningResult. If passed=False, the invocation
        should be blocked.

        Compound detection: if violations span multiple categories,
        severity is escalated. A single SQL-looking pattern might be
        legitimate data; SQL + command injection together is an attack.
        """
        violations: List[str] = []
        categories_hit: set = set()

        # Size check first (cheapest)
        if self._enable_size:
            size_violations = self._check_size(arguments)
            if size_violations:
                violations.extend(size_violations)
                categories_hit.add("size")

        # Flatten arguments to string values for pattern screening
        flat_values = self._flatten_values(arguments)

        for key, value in flat_values:
            # Platform InputSanitizer (banned phrases, homoglyphs)
            if self._sanitizer:
                sanitized = self._sanitizer.sanitize(value)
                if sanitized.startswith("[SUSPICIOUS INPUT DETECTED]"):
                    violations.append(
                        f"InputSanitizer flagged argument '{key}': prompt injection pattern"
                    )
                    categories_hit.add("prompt_injection")
                elif "[REDACTED]" in sanitized:
                    violations.append(
                        f"InputSanitizer flagged argument '{key}': banned phrase"
                    )
                    categories_hit.add("banned_phrase")

            # SQL injection
            if self._enable_sql:
                for pattern in _SQL_INJECTION_PATTERNS:
                    if pattern.search(value):
                        violations.append(
                            f"SQL injection pattern in argument '{key}'"
                        )
                        categories_hit.add("sql_injection")
                        break

            # Path traversal
            if self._enable_path:
                for pattern in _PATH_TRAVERSAL_PATTERNS:
                    if pattern.search(value):
                        violations.append(
                            f"Path traversal pattern in argument '{key}'"
                        )
                        categories_hit.add("path_traversal")
                        break

            # Command injection
            if self._enable_command:
                for pattern in _COMMAND_INJECTION_PATTERNS:
                    if pattern.search(value):
                        violations.append(
                            f"Command injection pattern in argument '{key}'"
                        )
                        categories_hit.add("command_injection")
                        break

            # Prompt injection (MCP-specific)
            if self._enable_prompt:
                for pattern in _PROMPT_INJECTION_PATTERNS:
                    if pattern.search(value):
                        violations.append(
                            f"Prompt injection pattern in argument '{key}'"
                        )
                        categories_hit.add("prompt_injection")
                        break

            # NoSQL injection
            if self._enable_nosql:
                for pattern in _NOSQL_INJECTION_PATTERNS:
                    if pattern.search(value):
                        violations.append(
                            f"NoSQL injection pattern in argument '{key}'"
                        )
                        categories_hit.add("nosql_injection")
                        break

            # URL/SSRF check
            if self._enable_url:
                url_violation = self._check_url_ssrf(key, value)
                if url_violation:
                    violations.append(url_violation)
                    categories_hit.add("ssrf")

        if not violations:
            return ScreeningResult(passed=True)

        # Severity determination
        severity = self._determine_severity(categories_hit)

        # Compound attack detection
        passed = len(categories_hit) < self._compound_threshold
        if not passed:
            severity = "critical"
            logger.warning(
                "MCP compound attack detected for %s:%s — %d categories: %s",
                server_id, tool_name, len(categories_hit), sorted(categories_hit),
            )
        else:
            logger.info(
                "MCP argument screening flagged %s:%s — %d violation(s) in %d category(ies): %s",
                server_id, tool_name, len(violations),
                len(categories_hit), sorted(categories_hit),
            )

        return ScreeningResult(
            passed=passed,
            violations=violations,
            severity=severity,
        )

    def _check_size(self, arguments: Dict[str, Any]) -> List[str]:
        """Check argument sizes for resource exhaustion."""
        violations = []
        total = 0

        for key, value in arguments.items():
            if isinstance(value, str):
                size = len(value)
                total += size
                if size > _MAX_STRING_LENGTH:
                    violations.append(
                        f"Argument '{key}' exceeds max length "
                        f"({size:,} > {_MAX_STRING_LENGTH:,} chars)"
                    )
            elif isinstance(value, (list, dict)):
                import json
                serialized = json.dumps(value)
                total += len(serialized)

        if total > _MAX_TOTAL_SIZE:
            violations.append(
                f"Total argument size exceeds limit "
                f"({total:,} > {_MAX_TOTAL_SIZE:,} chars)"
            )
        return violations

    @staticmethod
    def _flatten_values(
        arguments: Dict[str, Any], prefix: str = ""
    ) -> List[tuple]:
        """Recursively flatten argument dict to (key, string_value) pairs."""
        pairs = []
        for key, value in arguments.items():
            full_key = f"{prefix}.{key}" if prefix else key
            if isinstance(value, str):
                pairs.append((full_key, value))
            elif isinstance(value, dict):
                pairs.extend(
                    MCPArgumentScreener._flatten_values(value, full_key)
                )
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, str):
                        pairs.append((f"{full_key}[{i}]", item))
                    elif isinstance(item, dict):
                        pairs.extend(
                            MCPArgumentScreener._flatten_values(
                                item, f"{full_key}[{i}]"
                            )
                        )
        return pairs

    @staticmethod
    def _check_url_ssrf(key: str, value: str) -> Optional[str]:
        """Check if a string value looks like a URL targeting internal services."""
        # Only check values that look like URLs
        if not (value.startswith("http://") or value.startswith("https://")):
            return None

        try:
            parsed = urlparse(value)
            hostname = parsed.hostname
            if not hostname:
                return None

            # Check for internal/private IPs
            try:
                addr = socket.gethostbyname(hostname)
                ip = ipaddress.ip_address(addr)
                if ip.is_private or ip.is_loopback or ip.is_link_local:
                    return (
                        f"SSRF: argument '{key}' contains URL targeting "
                        f"private/internal address {hostname} ({addr})"
                    )
            except (socket.gaierror, ValueError):
                pass

            # Check for common internal hostnames
            internal_patterns = [
                "localhost", "127.0.0.1", "0.0.0.0",
                "metadata.google", "169.254.169.254",
                "metadata.aws", "instance-data",
            ]
            for pattern in internal_patterns:
                if pattern in hostname.lower():
                    return (
                        f"SSRF: argument '{key}' contains URL targeting "
                        f"internal service: {hostname}"
                    )
        except Exception:
            pass

        return None

    @staticmethod
    def _determine_severity(categories: set) -> str:
        """Map triggered categories to severity level."""
        high_severity = {
            "command_injection", "sql_injection", "ssrf", "path_traversal"
        }
        medium_severity = {"prompt_injection", "nosql_injection", "banned_phrase"}

        if categories & high_severity:
            return "high"
        if categories & medium_severity:
            return "medium"
        return "low"
