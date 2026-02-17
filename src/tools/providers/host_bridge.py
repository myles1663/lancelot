"""
HostBridgeProvider — Remote Host OS Execution via Host Agent
=============================================================

Executes commands on the actual host operating system (Windows, macOS,
Linux) by communicating with the Lancelot Host Agent over HTTP. The
host agent runs outside Docker on the real host machine.

This is the true host execution bridge — unlike HostExecutionProvider
which runs subprocess inside the container's Linux environment.

Gated by: FEATURE_TOOLS_HOST_BRIDGE (default: false)

Architecture:
    Container (Lancelot) ---HTTP---> host.docker.internal:9111 ---> Host Agent ---> Host OS

Security model:
    - Host agent must be running on the host machine
    - Bearer token authentication between container and agent
    - Command denylist enforced on both sides (defense in depth)
    - Output bounded to prevent memory exhaustion
    - Timeouts enforced
    - Host agent only listens on 127.0.0.1

Required:
    - Lancelot Host Agent running on the host (host_agent/start_agent.bat)
    - HOST_AGENT_TOKEN env var matching on both sides
    - Docker extra_hosts mapping for host.docker.internal
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shlex
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from src.tools.contracts import (
    BaseProvider,
    Capability,
    ExecResult,
    FileChange,
    PatchResult,
    ProviderHealth,
    ProviderState,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class HostBridgeConfig:
    """Configuration for HostBridgeProvider."""

    # Agent connection
    agent_url: str = ""  # Set from env in __post_init__
    agent_token: str = ""  # Set from env in __post_init__
    connect_timeout_s: int = 5
    read_timeout_s: int = 300

    # Output limits
    max_stdout_chars: int = 100_000
    max_stderr_chars: int = 50_000

    # Command denylist (defense-in-depth — agent also checks)
    command_denylist: List[str] = field(default_factory=lambda: [
        "rm -rf /",
        "rm -rf /*",
        "mkfs",
        "dd if=/dev/zero",
        ":(){:|:&};:",
        "chmod -R 777 /",
        "format c:",
        "del /s /q c:\\",
        "rd /s /q c:\\",
    ])

    def __post_init__(self):
        if not self.agent_url:
            self.agent_url = os.environ.get(
                "HOST_AGENT_URL", "http://host.docker.internal:9111"
            )
        if not self.agent_token:
            self.agent_token = os.environ.get(
                "HOST_AGENT_TOKEN", "lancelot-host-agent"
            )


# =============================================================================
# HostBridgeProvider
# =============================================================================


class HostBridgeProvider(BaseProvider):
    """
    Host OS Bridge provider — executes on the real host via the Host Agent.

    This provider communicates with a lightweight HTTP server running on
    the host machine to execute commands on the actual host OS.
    """

    def __init__(
        self,
        config: Optional[HostBridgeConfig] = None,
        workspace: Optional[str] = None,
    ):
        self.config = config or HostBridgeConfig()
        self._workspace = workspace

    @property
    def provider_id(self) -> str:
        return "host_bridge"

    @property
    def capabilities(self) -> List[Capability]:
        return [
            Capability.SHELL_EXEC,
            Capability.REPO_OPS,
            Capability.FILE_OPS,
            Capability.DEPLOY_OPS,
        ]

    # =========================================================================
    # HTTP Communication
    # =========================================================================

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[dict] = None,
        timeout: Optional[int] = None,
    ) -> dict:
        """Make an HTTP request to the host agent."""
        url = f"{self.config.agent_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.config.agent_token}",
            "Content-Type": "application/json",
        }

        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")

        req = urllib.request.Request(url, data=data, headers=headers, method=method)

        effective_timeout = timeout or self.config.read_timeout_s

        try:
            with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise ConnectionError(
                f"Host agent returned {e.code}: {error_body[:200]}"
            ) from e
        except urllib.error.URLError as e:
            raise ConnectionError(
                f"Cannot reach host agent at {url}: {e.reason}"
            ) from e
        except Exception as e:
            raise ConnectionError(
                f"Host agent request failed: {str(e)[:200]}"
            ) from e

    # =========================================================================
    # Health Check
    # =========================================================================

    def health_check(self) -> ProviderHealth:
        """Check if the host agent is reachable."""
        try:
            info = self._request("GET", "/health", timeout=self.config.connect_timeout_s)
            return ProviderHealth(
                provider_id=self.provider_id,
                state=ProviderState.HEALTHY,
                version="host_bridge",
                last_check=datetime.now(timezone.utc).isoformat(),
                capabilities=[c.value for c in self.capabilities],
                degraded_reasons=[],
                error_message=None,
                metadata={
                    "mode": "host_bridge",
                    "host_platform": info.get("platform", "unknown"),
                    "host_hostname": info.get("hostname", "unknown"),
                    "agent_version": info.get("agent_version", "unknown"),
                },
            )
        except Exception as e:
            return ProviderHealth(
                provider_id=self.provider_id,
                state=ProviderState.OFFLINE,
                version="host_bridge",
                last_check=datetime.now(timezone.utc).isoformat(),
                capabilities=[c.value for c in self.capabilities],
                degraded_reasons=[f"Host agent unreachable: {str(e)[:100]}"],
                error_message=str(e)[:200],
                metadata={"mode": "host_bridge", "agent_url": self.config.agent_url},
            )

    # =========================================================================
    # ShellExec Capability
    # =========================================================================

    def run(
        self,
        command: Union[str, List[str]],
        cwd: str,
        env: Optional[Dict[str, str]] = None,
        timeout_s: int = 60,
        stream: bool = False,
        network: bool = False,
    ) -> ExecResult:
        """Execute a command on the host via the host agent."""
        start_time = time.time()
        cmd_str = command if isinstance(command, str) else " ".join(command)

        # Defense-in-depth: check denylist locally before sending to agent
        if self._is_denied_command(cmd_str):
            return ExecResult(
                exit_code=126,
                stdout="",
                stderr=f"Command blocked by security policy: {cmd_str[:100]}",
                duration_ms=int((time.time() - start_time) * 1000),
                command=cmd_str,
                working_dir=cwd,
            )

        # Send to host agent
        try:
            result = self._request("POST", "/execute", body={
                "command": cmd_str,
                "cwd": cwd,
                "env": env,
                "timeout": timeout_s,
            }, timeout=timeout_s + 10)  # Extra margin for network overhead

            stdout, _ = self._bound_output(
                result.get("stdout", ""), self.config.max_stdout_chars
            )
            stderr, _ = self._bound_output(
                result.get("stderr", ""), self.config.max_stderr_chars
            )

            return ExecResult(
                exit_code=result.get("exit_code", 1),
                stdout=stdout,
                stderr=stderr,
                duration_ms=result.get("duration_ms", int((time.time() - start_time) * 1000)),
                truncated=False,
                command=cmd_str,
                working_dir=cwd,
                timed_out=result.get("timed_out", False),
            )

        except ConnectionError as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return ExecResult(
                exit_code=1,
                stdout="",
                stderr=f"Host agent error: {str(e)[:300]}",
                duration_ms=duration_ms,
                command=cmd_str,
                working_dir=cwd,
            )
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return ExecResult(
                exit_code=1,
                stdout="",
                stderr=f"Host bridge error: {str(e)[:200]}",
                duration_ms=duration_ms,
                command=cmd_str,
                working_dir=cwd,
            )

    # =========================================================================
    # RepoOps Capability
    # =========================================================================

    def status(self, workspace: str) -> Dict[str, Any]:
        """Get Git repository status on host."""
        result = self.run("git status --porcelain", workspace)
        if not result.success:
            return {"error": result.stderr, "exit_code": result.exit_code}

        files = {"modified": [], "added": [], "deleted": [], "untracked": []}
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            status_code = line[:2]
            filepath = line[3:]
            if status_code[0] == "M" or status_code[1] == "M":
                files["modified"].append(filepath)
            elif status_code[0] == "A":
                files["added"].append(filepath)
            elif status_code[0] == "D":
                files["deleted"].append(filepath)
            elif status_code == "??":
                files["untracked"].append(filepath)
        return files

    def diff(self, workspace: str, ref: Optional[str] = None) -> str:
        """Get diff output on host."""
        cmd = "git diff" if ref is None else f"git diff {shlex.quote(ref)}"
        result = self.run(cmd, workspace)
        if not result.success:
            return f"Error: {result.stderr}"
        return result.stdout

    def apply_patch(
        self,
        workspace: str,
        patch: str,
        dry_run: bool = False,
    ) -> PatchResult:
        """Apply a unified diff patch on host."""
        if ".." in patch or patch.startswith("/"):
            return PatchResult(
                success=False,
                files_changed=[],
                error_message="Path traversal detected in patch",
            )

        # Write patch via agent, apply, return result
        if dry_run:
            result = self.run(
                f'echo {shlex.quote(patch)} | git apply --check -',
                workspace,
            )
            return PatchResult(
                success=result.exit_code == 0,
                files_changed=[],
                error_message=result.stderr if result.exit_code != 0 else None,
            )

        result = self.run(
            f'echo {shlex.quote(patch)} | git apply -',
            workspace,
        )
        if result.exit_code != 0:
            return PatchResult(
                success=False,
                files_changed=[],
                rejected_hunks=[result.stderr],
                error_message=result.stderr,
            )

        return PatchResult(success=True, files_changed=[])

    def commit(
        self,
        workspace: str,
        message: str,
        files: Optional[List[str]] = None,
    ) -> str:
        """Create a commit on host."""
        if files:
            for f in files:
                self.run(f"git add {shlex.quote(f)}", workspace)
        else:
            self.run("git add -A", workspace)

        safe_message = shlex.quote(message)
        result = self.run(f"git commit -m {safe_message}", workspace)

        if result.exit_code != 0:
            return f"Error: {result.stderr}"

        hash_result = self.run("git rev-parse HEAD", workspace)
        return hash_result.stdout.strip()

    def branch(self, workspace: str, name: str, checkout: bool = True) -> bool:
        """Create and optionally checkout a branch on host."""
        safe_name = shlex.quote(name)
        if checkout:
            result = self.run(f"git checkout -b {safe_name}", workspace)
        else:
            result = self.run(f"git branch {safe_name}", workspace)
        return result.exit_code == 0

    def checkout(self, workspace: str, ref: str) -> bool:
        """Checkout a ref on host."""
        result = self.run(f"git checkout {shlex.quote(ref)}", workspace)
        return result.exit_code == 0

    # =========================================================================
    # FileOps Capability (via shell commands on host)
    # =========================================================================

    def read(self, path: str) -> str:
        """Read file contents on host."""
        # Use 'type' on Windows, 'cat' on Unix — agent handles OS detection
        result = self.run(f'python -c "print(open(r\'{path}\', encoding=\'utf-8\').read(), end=\'\')"', os.path.dirname(path) or ".")
        if not result.success:
            return f"Error: {result.stderr}"
        return result.stdout

    def write(self, path: str, content: str, atomic: bool = True) -> FileChange:
        """Write content to file on host."""
        # Use Python one-liner via agent for safe cross-platform writes
        escaped = content.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "\\r")
        script = f"import os; os.makedirs(os.path.dirname(r'{path}') or '.', exist_ok=True); open(r'{path}', 'w', encoding='utf-8').write('{escaped}')"
        result = self.run(f'python -c "{script}"', os.path.dirname(path) or ".")
        if not result.success:
            return FileChange(path=path, action="error", error_message=result.stderr)
        return FileChange(path=path, action="modified" if os.path.exists(path) else "created")

    def list(self, path: str, recursive: bool = False) -> List[str]:
        """List files in directory on host."""
        if recursive:
            result = self.run(f'python -c "import os; [print(os.path.relpath(os.path.join(r,f), r\'{path}\')) for r,d,files in os.walk(r\'{path}\') for f in files]"', path)
        else:
            result = self.run(f'python -c "import os; [print(f) for f in sorted(os.listdir(r\'{path}\'))]"', path)
        if not result.success:
            return [f"Error: {result.stderr}"]
        return [line for line in result.stdout.strip().split("\n") if line]

    def delete(self, path: str) -> FileChange:
        """Delete a file on host."""
        result = self.run(f'python -c "import os; os.remove(r\'{path}\')"', os.path.dirname(path) or ".")
        if not result.success:
            return FileChange(path=path, action="error", error_message=result.stderr)
        return FileChange(path=path, action="deleted")

    # =========================================================================
    # Helpers
    # =========================================================================

    def _is_denied_command(self, cmd: str) -> bool:
        """Check if command matches any denylist pattern."""
        cmd_lower = cmd.lower().strip()
        for denied in self.config.command_denylist:
            if denied.lower() in cmd_lower:
                return True
        return False

    def _bound_output(self, output: str, max_chars: int) -> Tuple[str, bool]:
        """Bound output to maximum character length."""
        if len(output) <= max_chars:
            return output, False
        return output[:max_chars] + "\n... (truncated)", True
