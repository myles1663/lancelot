"""
Lancelot Host Agent — Lightweight bridge for host OS execution.

This standalone Python HTTP server runs on the HOST machine (outside Docker)
and accepts command execution requests from the Lancelot container via HTTP.
This is the bridge that allows Lancelot to run commands on the actual host OS
(Windows, macOS, Linux) instead of inside its Docker container.

Usage:
    python agent.py                           # Default: 127.0.0.1:9111
    python agent.py --port 9222               # Custom port
    python agent.py --token my-secret-token   # Custom auth token

Security:
    - Listens on 127.0.0.1 ONLY (not reachable from network)
    - Requires Bearer token authentication
    - Command denylist blocks dangerous patterns
    - Output bounded to prevent memory exhaustion
    - Timeouts enforced on all commands

No external dependencies — uses only Python standard library.
"""

import argparse
import json
import logging
import os
import platform
import re
import signal
import socket
import subprocess
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import List, Tuple

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_PORT = 9111
DEFAULT_TOKEN = "lancelot-host-agent"
MAX_STDOUT_CHARS = 100_000
MAX_STDERR_CHARS = 50_000
DEFAULT_TIMEOUT_S = 300

# Dangerous command patterns (blocked regardless of allowlist)
COMMAND_DENYLIST = [
    r"rm\s+(-rf|-fr)\s+/\s*$",
    r"rm\s+(-rf|-fr)\s+/\*",
    r"mkfs\b",
    r"dd\s+if=/dev/zero",
    r":\(\)\{.*\|.*&\}\s*;",       # Fork bomb
    r"chmod\s+(-R\s+)?777\s+/\s*$",
    r"format\s+[a-zA-Z]:",          # Windows format drive
    r"del\s+/[sfq]\s+[a-zA-Z]:\\",  # Windows delete system files
    r"rd\s+/s\s+/q\s+[a-zA-Z]:\\",  # Windows rmdir system
]

COMPILED_DENYLIST = [re.compile(p, re.IGNORECASE) for p in COMMAND_DENYLIST]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [HOST-AGENT] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("host_agent")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def is_command_denied(cmd: str) -> bool:
    """Check if a command matches any denylist pattern."""
    for pattern in COMPILED_DENYLIST:
        if pattern.search(cmd):
            return True
    return False


def bound_output(output: str, max_chars: int) -> Tuple[str, bool]:
    """Truncate output to max_chars. Returns (text, was_truncated)."""
    if len(output) <= max_chars:
        return output, False
    return output[:max_chars] + "\n... (truncated)", True


def execute_command(
    command: str,
    cwd: str = None,
    env: dict = None,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> dict:
    """Execute a command on the host and return structured result."""
    start = time.time()

    # Security: check denylist
    if is_command_denied(command):
        return {
            "exit_code": 126,
            "stdout": "",
            "stderr": f"Command blocked by host agent security policy: {command[:100]}",
            "timed_out": False,
            "duration_ms": int((time.time() - start) * 1000),
        }

    # Resolve working directory
    if cwd and os.path.isdir(cwd):
        work_dir = cwd
    else:
        work_dir = os.path.expanduser("~")

    # Build environment
    run_env = os.environ.copy()
    if env and isinstance(env, dict):
        for key, value in env.items():
            if isinstance(key, str) and isinstance(value, str):
                run_env[key] = value

    # Detect shell based on OS
    if platform.system() == "Windows":
        # Use cmd.exe on Windows for broadest compatibility
        shell_cmd = command
    else:
        shell_cmd = command

    try:
        result = subprocess.run(
            shell_cmd,
            shell=True,
            cwd=work_dir,
            env=run_env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        stdout, _ = bound_output(result.stdout, MAX_STDOUT_CHARS)
        stderr, _ = bound_output(result.stderr, MAX_STDERR_CHARS)
        duration_ms = int((time.time() - start) * 1000)

        return {
            "exit_code": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": False,
            "duration_ms": duration_ms,
        }

    except subprocess.TimeoutExpired:
        duration_ms = int((time.time() - start) * 1000)
        return {
            "exit_code": 124,
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
            "timed_out": True,
            "duration_ms": duration_ms,
        }
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": f"Host agent execution error: {str(e)[:500]}",
            "timed_out": False,
            "duration_ms": duration_ms,
        }


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------


class HostAgentHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the host agent."""

    server_version = "LancelotHostAgent/1.0"
    auth_token = DEFAULT_TOKEN

    def log_message(self, format, *args):
        """Override to use our logger."""
        logger.info("%s %s", self.address_string(), format % args)

    def _check_auth(self) -> bool:
        """Validate Bearer token. Returns True if authorized."""
        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            if token == self.auth_token:
                return True
        # Also accept X-Agent-Token header for flexibility
        token_header = self.headers.get("X-Agent-Token", "")
        if token_header == self.auth_token:
            return True
        return False

    def _send_json(self, data: dict, status: int = 200):
        """Send a JSON response."""
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, message: str):
        """Send an error response."""
        self._send_json({"error": message}, status=status)

    def do_GET(self):
        """Handle GET requests."""
        if self.path == "/health":
            self._send_json({
                "status": "ok",
                "platform": platform.system(),
                "platform_version": platform.version(),
                "hostname": socket.gethostname(),
                "python_version": platform.python_version(),
                "agent_version": "1.0.0",
            })
            return

        if self.path == "/info":
            if not self._check_auth():
                self._send_error(401, "Unauthorized")
                return
            self._send_json({
                "platform": platform.system(),
                "platform_version": platform.version(),
                "platform_release": platform.release(),
                "architecture": platform.machine(),
                "hostname": socket.gethostname(),
                "username": os.environ.get("USERNAME", os.environ.get("USER", "unknown")),
                "home_dir": os.path.expanduser("~"),
                "python_version": platform.python_version(),
                "cwd": os.getcwd(),
            })
            return

        self._send_error(404, f"Not found: {self.path}")

    def do_POST(self):
        """Handle POST requests."""
        if self.path == "/execute":
            if not self._check_auth():
                self._send_error(401, "Unauthorized")
                return

            # Parse body
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length == 0:
                self._send_error(400, "Empty request body")
                return
            if content_length > 1_000_000:  # 1MB limit
                self._send_error(413, "Request body too large")
                return

            try:
                body = json.loads(self.rfile.read(content_length))
            except json.JSONDecodeError:
                self._send_error(400, "Invalid JSON")
                return

            command = body.get("command")
            if not command or not isinstance(command, str):
                self._send_error(400, "Missing or invalid 'command' field")
                return

            cwd = body.get("cwd")
            env = body.get("env")
            timeout = body.get("timeout", DEFAULT_TIMEOUT_S)

            if not isinstance(timeout, (int, float)) or timeout <= 0:
                timeout = DEFAULT_TIMEOUT_S
            timeout = min(timeout, 600)  # Cap at 10 minutes

            logger.info("EXEC: %s (cwd=%s, timeout=%ss)", command[:100], cwd, timeout)

            result = execute_command(command, cwd=cwd, env=env, timeout=int(timeout))

            logger.info(
                "RESULT: exit=%d, stdout=%d chars, stderr=%d chars, %dms",
                result["exit_code"],
                len(result["stdout"]),
                len(result["stderr"]),
                result["duration_ms"],
            )

            self._send_json(result)
            return

        self._send_error(404, f"Not found: {self.path}")


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


def run_server(port: int = DEFAULT_PORT, token: str = DEFAULT_TOKEN):
    """Start the host agent HTTP server."""
    HostAgentHandler.auth_token = token

    server = HTTPServer(("127.0.0.1", port), HostAgentHandler)

    logger.info("=" * 60)
    logger.info("Lancelot Host Agent v1.0.0")
    logger.info("=" * 60)
    logger.info("Listening on: http://127.0.0.1:%d", port)
    logger.info("Platform:     %s %s", platform.system(), platform.release())
    logger.info("Hostname:     %s", socket.gethostname())
    logger.info("Auth token:   %s...%s", token[:4], token[-4:] if len(token) > 8 else "****")
    logger.info("=" * 60)
    logger.info("Endpoints:")
    logger.info("  GET  /health  — Health check (no auth)")
    logger.info("  GET  /info    — Host info (auth required)")
    logger.info("  POST /execute — Run command (auth required)")
    logger.info("=" * 60)
    logger.info("Press Ctrl+C to stop.")
    logger.info("")

    def shutdown(signum, frame):
        logger.info("Shutting down host agent...")
        server.shutdown()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        logger.info("Host agent stopped.")


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Lancelot Host Agent — bridge for host OS execution",
    )
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("HOST_AGENT_PORT", DEFAULT_PORT)),
        help=f"Port to listen on (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--token", type=str, default=os.environ.get("HOST_AGENT_TOKEN", DEFAULT_TOKEN),
        help="Authentication token (default: from HOST_AGENT_TOKEN env var)",
    )
    args = parser.parse_args()

    run_server(port=args.port, token=args.token)
