"""
Built-in skill: command_runner — execute allowlisted shell commands.

Captures stdout/stderr as receipts. Enforces timeout.
Only commands from the whitelist are permitted.
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
    "version": "1.0.0",
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

# Allowlisted command binaries
COMMAND_WHITELIST = {
    "ls", "dir", "cat", "head", "tail", "find", "wc",
    "git", "docker", "echo", "date", "whoami", "pwd",
    "df", "du", "tar", "gzip", "zip", "unzip",
    "mkdir", "cp", "mv", "grep", "sort", "uniq",
    "touch", "test", "true", "false", "python", "pip",
    "npm", "node", "curl", "wget",
}

# Dangerous shell metacharacters
BLOCKED_CHARS = {'&', '|', ';', '$', '`', '(', ')', '{', '}', '<', '>'}

DEFAULT_TIMEOUT = 30


def execute(context, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a shell command.

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

    # Parse command
    try:
        parts = shlex.split(command)
    except ValueError as e:
        raise ValueError(f"Invalid command syntax: {e}")

    # Execute
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

        logger.info("command_runner: '%s' completed (rc=%d, %.1fms)",
                     command, result.returncode, duration_ms)

        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.returncode,
            "duration_ms": round(duration_ms, 2),
            "command": command,
        }

    except subprocess.TimeoutExpired:
        duration_ms = (time.monotonic() - start) * 1000
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
