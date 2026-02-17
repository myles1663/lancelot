"""
Built-in skill: command_runner — execute allowlisted shell commands.

Captures stdout/stderr as receipts. Enforces timeout.
Only commands from the whitelist are permitted.

When FEATURE_TOOLS_HOST_BRIDGE or FEATURE_TOOLS_HOST_EXECUTION is enabled,
commands are routed through the Tool Fabric so they execute on the correct
target (host OS, container Linux, or sandbox). Falls back to local subprocess
when Tool Fabric is unavailable.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import time
from typing import Any, Dict

logger = logging.getLogger(__name__)

# Skill manifest metadata
MANIFEST = {
    "name": "command_runner",
    "version": "1.1.0",
    "description": "Execute allowlisted shell commands with timeout",
    "risk": "MEDIUM",
    "permissions": ["command_execute"],
    "inputs": [
        {"name": "command", "type": "string", "required": True,
         "description": "Shell command to execute"},
        {"name": "timeout_sec", "type": "integer", "required": False,
         "description": "Timeout in seconds (default 30)"},
    ],
}

# Allowlisted command binaries (Linux + Windows)
COMMAND_WHITELIST = {
    # Unix/Linux
    "ls", "cat", "head", "tail", "find", "wc",
    "git", "docker", "echo", "date", "whoami", "pwd",
    "df", "du", "tar", "gzip", "zip", "unzip",
    "mkdir", "cp", "mv", "grep", "sort", "uniq",
    "touch", "test", "true", "false", "python", "pip",
    "npm", "node", "curl", "wget", "uname", "hostname",
    # Windows
    "dir", "ver", "systeminfo", "ipconfig", "netstat",
    "tasklist", "where", "type", "set", "python3",
    "powershell", "pwsh", "wmic",
}

# Dangerous shell metacharacters
BLOCKED_CHARS = {'&', '|', ';', '$', '`', '(', ')', '{', '}', '<', '>'}

DEFAULT_TIMEOUT = 30


def _get_tool_fabric():
    """Try to import and return the global ToolFabric instance, or None."""
    try:
        from src.tools.fabric import get_tool_fabric
        return get_tool_fabric()
    except Exception:
        return None


def _should_use_fabric() -> bool:
    """Check if Tool Fabric routing should be used (host bridge or host exec enabled)."""
    try:
        from src.core.feature_flags import (
            FEATURE_TOOLS_FABRIC,
            FEATURE_TOOLS_HOST_BRIDGE,
            FEATURE_TOOLS_HOST_EXECUTION,
        )
        return FEATURE_TOOLS_FABRIC and (FEATURE_TOOLS_HOST_BRIDGE or FEATURE_TOOLS_HOST_EXECUTION)
    except Exception:
        return False


def execute(context, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a shell command.

    Routes through Tool Fabric when host bridge/execution is enabled,
    so commands run on the correct target (host OS, container, or sandbox).
    Falls back to local subprocess when Tool Fabric is unavailable.

    Args:
        context: SkillContext with skill_name, request_id, caller, metadata.
        inputs: Dict with 'command' and optionally 'timeout_sec'.

    Returns:
        Dict with 'stdout', 'stderr', 'return_code', 'duration_ms'.
    """
    command = inputs.get("command", "").strip()
    timeout_sec = inputs.get("timeout_sec", DEFAULT_TIMEOUT)

    if not command:
        raise ValueError("Missing required input: 'command'")

    # Validate command
    _validate_command(command)

    # Route through Tool Fabric when host bridge/execution is active
    if _should_use_fabric():
        fabric = _get_tool_fabric()
        if fabric is not None:
            return _execute_via_fabric(fabric, command, timeout_sec, inputs)

    # Fallback: direct subprocess (container-local execution)
    return _execute_local(command, timeout_sec, inputs)


def _execute_via_fabric(fabric, command: str, timeout_sec: int, inputs: dict) -> dict:
    """Execute command through Tool Fabric (routes to host bridge/execution/sandbox)."""
    start = time.monotonic()

    workspace = inputs.get("cwd") or os.environ.get("LANCELOT_WORKSPACE", ".")
    result = fabric.run_command(
        command=command,
        workspace=workspace,
        timeout_s=timeout_sec,
    )

    duration_ms = (time.monotonic() - start) * 1000
    logger.info(
        "command_runner [fabric]: '%s' completed (rc=%d, %.1fms, provider=%s)",
        command, result.exit_code, duration_ms,
        getattr(result, 'working_dir', 'unknown'),
    )

    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "return_code": result.exit_code,
        "duration_ms": round(duration_ms, 2),
        "command": command,
    }


def _execute_local(command: str, timeout_sec: int, inputs: dict) -> dict:
    """Execute command directly via subprocess (container-local)."""
    try:
        parts = shlex.split(command)
    except ValueError as e:
        raise ValueError(f"Invalid command syntax: {e}")

    start = time.monotonic()
    try:
        result = subprocess.run(
            parts,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=inputs.get("cwd", None),
        )
        duration_ms = (time.monotonic() - start) * 1000

        logger.info("command_runner [local]: '%s' completed (rc=%d, %.1fms)",
                     command, result.returncode, duration_ms)

        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.returncode,
            "duration_ms": round(duration_ms, 2),
            "command": command,
        }

    except subprocess.TimeoutExpired:
        raise TimeoutError(f"Command timed out after {timeout_sec}s: {command}")


def _validate_command(command: str) -> None:
    """Validate command against whitelist and blocked characters."""
    # Check for blocked shell metacharacters
    for char in BLOCKED_CHARS:
        if char in command:
            raise ValueError(f"Blocked shell metacharacter: '{char}'")

    # Parse and check binary
    try:
        parts = shlex.split(command)
    except ValueError:
        raise ValueError("Invalid command syntax")

    if not parts:
        raise ValueError("Empty command")

    binary = os.path.basename(parts[0])
    if binary not in COMMAND_WHITELIST:
        raise ValueError(f"Command '{binary}' not in whitelist")
