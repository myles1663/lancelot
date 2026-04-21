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
from pathlib import Path
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
WINDOWS_SHELL_BUILTINS = {"echo", "dir", "type", "set", "ver"}

DEFAULT_TIMEOUT = 30

# Path to the host write commands config (relative to project root)
_WRITE_COMMANDS_CONFIG = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "config", "host_write_commands.yaml"
)


def _load_write_commands() -> set:
    """Load the host write commands list from config file.

    Returns a set of command binary names. Reads fresh each call so
    edits via the War Room UI take effect immediately.
    """
    commands = set()
    try:
        config_path = os.path.normpath(_WRITE_COMMANDS_CONFIG)
        # Also check /home/lancelot/config/ (Docker volume mount)
        if not os.path.exists(config_path):
            config_path = "/home/lancelot/config/host_write_commands.yaml"
        if not os.path.exists(config_path):
            return commands
        with open(config_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    commands.add(line)
    except Exception as e:
        logger.warning("Failed to load host write commands config: %s", e)
    return commands


def _is_write_commands_enabled() -> bool:
    """Check if FEATURE_HOST_WRITE_COMMANDS flag is on."""
    try:
        from src.core.feature_flags import FEATURE_HOST_WRITE_COMMANDS
        return FEATURE_HOST_WRITE_COMMANDS
    except Exception:
        return False


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

    workspace = _resolve_workspace(inputs)
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
    cwd = _resolve_workspace(inputs)
    binary, parts = _parse_command(command)

    start = time.monotonic()
    try:
        if os.name == "nt" and binary in WINDOWS_SHELL_BUILTINS:
            exec_args = ["cmd.exe", "/d", "/s", "/c", command]
        else:
            exec_args = parts
        result = subprocess.run(
            exec_args,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=cwd,
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
    binary, _ = _parse_command(command)
    if binary in COMMAND_WHITELIST:
        return  # Always allowed

    # Check host write commands list (only when flag is enabled)
    if _is_write_commands_enabled():
        write_commands = _load_write_commands()
        if binary in write_commands:
            logger.info("command_runner: write command '%s' allowed via FEATURE_HOST_WRITE_COMMANDS", binary)
            return

    raise ValueError(f"Command '{binary}' not in whitelist")


def _parse_command(command: str) -> tuple[str, list[str]]:
    """Parse a command string into the executable and argv list."""
    try:
        parts = shlex.split(command, posix=os.name != "nt")
    except ValueError as e:
        raise ValueError(f"Invalid command syntax: {e}")

    if not parts:
        raise ValueError("Empty command")

    return os.path.basename(parts[0]), parts


def _resolve_workspace(inputs: dict) -> str:
    """Resolve and validate the execution cwd against the workspace boundary."""
    configured_root = os.environ.get("LANCELOT_WORKSPACE")
    requested = inputs.get("cwd")

    if configured_root:
        root = Path(configured_root).resolve()
    elif requested:
        root = Path(requested).resolve()
    else:
        root = Path.cwd().resolve()

    target = Path(requested).resolve() if requested else root

    if not target.exists() or not target.is_dir():
        raise ValueError(f"Invalid command cwd: {target}")

    if configured_root and not _is_within_workspace(target, root):
        raise ValueError(f"Command cwd '{target}' is outside workspace boundary '{root}'")

    return str(target)


def _is_within_workspace(path: Path, workspace: Path) -> bool:
    """Return True when path is within workspace, including the workspace root."""
    try:
        path.relative_to(workspace)
        return True
    except ValueError:
        return path == workspace
