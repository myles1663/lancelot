"""
HostExecutionProvider — Direct Host Machine Tool Runner
========================================================

Optional provider that executes commands directly on the host OS
instead of inside a Docker container. Bypasses container isolation.

**DANGEROUS**: This provider runs commands with the same permissions
as the Lancelot process. Only enable for trusted development
environments. Never in production.

Gated by: FEATURE_TOOLS_HOST_EXECUTION (default: false)

Capabilities provided:
- ShellExec: Run commands directly via subprocess
- RepoOps: Git operations on host filesystem
- FileOps: File operations on host filesystem

Security model:
- Command denylist still enforced
- Output bounded to prevent memory exhaustion
- Timeouts enforced via subprocess
- Workspace boundary checks for file ops
- NO container isolation
- NO network restrictions
"""

from __future__ import annotations

import hashlib
import logging
import os
import shlex
import shutil
import subprocess
import tempfile
import time
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
class HostExecConfig:
    """Configuration for HostExecutionProvider."""

    # Output limits
    max_stdout_chars: int = 100000
    max_stderr_chars: int = 50000

    # Default timeout
    default_timeout_s: int = 300

    # Command denylist (always blocked — same as sandbox)
    command_denylist: List[str] = field(default_factory=lambda: [
        "rm -rf /",
        "rm -rf /*",
        "mkfs",
        "dd if=/dev/zero",
        ":(){:|:&};:",  # Fork bomb
        "chmod -R 777 /",
        "chown -R",
    ])

    # Command allowlist (empty = allow all)
    command_allowlist: List[str] = field(default_factory=list)


# =============================================================================
# HostExecutionProvider
# =============================================================================


class HostExecutionProvider(BaseProvider):
    """
    Direct host execution provider — runs commands on the host OS.

    This provider is the opt-in alternative to LocalSandboxProvider.
    When enabled, tool commands run directly on the host machine with
    no Docker container isolation.
    """

    def __init__(
        self,
        config: Optional[HostExecConfig] = None,
        workspace: Optional[str] = None,
    ):
        self.config = config or HostExecConfig()
        self._workspace = workspace

    @property
    def provider_id(self) -> str:
        return "host_execution"

    @property
    def capabilities(self) -> List[Capability]:
        return [
            Capability.SHELL_EXEC,
            Capability.REPO_OPS,
            Capability.FILE_OPS,
            Capability.DEPLOY_OPS,
        ]

    # =========================================================================
    # Health Check
    # =========================================================================

    def health_check(self) -> ProviderHealth:
        """Host is always available — we're running on it."""
        return ProviderHealth(
            provider_id=self.provider_id,
            state=ProviderState.HEALTHY,
            version="host",
            last_check=datetime.now(timezone.utc).isoformat(),
            capabilities=[c.value for c in self.capabilities],
            degraded_reasons=[],
            error_message=None,
            metadata={"mode": "host_execution", "warning": "No container isolation"},
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
        """Execute a command directly on the host."""
        start_time = time.time()
        cmd_str = command if isinstance(command, str) else " ".join(command)

        # Validate command against denylist
        if self._is_denied_command(cmd_str):
            return ExecResult(
                exit_code=126,
                stdout="",
                stderr=f"Command blocked by security policy: {cmd_str[:100]}",
                duration_ms=int((time.time() - start_time) * 1000),
                command=cmd_str,
                working_dir=cwd,
            )

        # Check allowlist if configured
        if self.config.command_allowlist:
            if not self._is_allowed_command(cmd_str):
                return ExecResult(
                    exit_code=126,
                    stdout="",
                    stderr="Command not in allowlist",
                    duration_ms=int((time.time() - start_time) * 1000),
                    command=cmd_str,
                    working_dir=cwd,
                )

        # Build environment
        run_env = os.environ.copy()
        if env:
            for key, value in env.items():
                if key.isidentifier():
                    run_env[key] = value

        # Resolve working directory
        work_dir = cwd if cwd and os.path.isdir(cwd) else self._workspace or "."

        # Execute directly on host
        try:
            result = subprocess.run(
                cmd_str,
                shell=True,
                cwd=work_dir,
                env=run_env,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )

            stdout, stdout_truncated = self._bound_output(
                result.stdout, self.config.max_stdout_chars
            )
            stderr, stderr_truncated = self._bound_output(
                result.stderr, self.config.max_stderr_chars
            )

            duration_ms = int((time.time() - start_time) * 1000)

            return ExecResult(
                exit_code=result.returncode,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
                truncated=stdout_truncated or stderr_truncated,
                command=cmd_str,
                working_dir=work_dir,
                timed_out=False,
            )

        except subprocess.TimeoutExpired:
            duration_ms = int((time.time() - start_time) * 1000)
            return ExecResult(
                exit_code=124,
                stdout="",
                stderr=f"Command timed out after {timeout_s}s",
                duration_ms=duration_ms,
                command=cmd_str,
                working_dir=work_dir,
                timed_out=True,
            )
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return ExecResult(
                exit_code=1,
                stdout="",
                stderr=f"Host execution error: {str(e)[:200]}",
                duration_ms=duration_ms,
                command=cmd_str,
                working_dir=work_dir,
            )

    # =========================================================================
    # RepoOps Capability
    # =========================================================================

    def status(self, workspace: str) -> Dict[str, Any]:
        """Get Git repository status."""
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
        """Get diff output."""
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
        """Apply a unified diff patch."""
        if ".." in patch or patch.startswith("/"):
            return PatchResult(
                success=False,
                files_changed=[],
                error_message="Path traversal detected in patch",
            )

        patch_file = os.path.join(workspace, ".tmp_patch")
        try:
            with open(patch_file, "w") as f:
                f.write(patch)

            if dry_run:
                result = self.run("git apply --check .tmp_patch 2>&1", workspace)
                return PatchResult(
                    success=result.exit_code == 0,
                    files_changed=[],
                    error_message=result.stderr if result.exit_code != 0 else None,
                )

            # Get before hashes
            files_in_patch = self._extract_files_from_patch(patch)
            before_hashes = {}
            for filepath in files_in_patch:
                full_path = os.path.join(workspace, filepath)
                if os.path.exists(full_path):
                    before_hashes[filepath] = self._hash_file(full_path)

            result = self.run("git apply .tmp_patch", workspace)

            if result.exit_code != 0:
                return PatchResult(
                    success=False,
                    files_changed=[],
                    rejected_hunks=[result.stderr],
                    error_message=result.stderr,
                )

            file_changes = []
            for filepath in files_in_patch:
                full_path = os.path.join(workspace, filepath)
                hash_before = before_hashes.get(filepath)
                if os.path.exists(full_path):
                    hash_after = self._hash_file(full_path)
                    action = "created" if hash_before is None else "modified"
                else:
                    hash_after = None
                    action = "deleted"
                file_changes.append(FileChange(
                    path=filepath, action=action,
                    hash_before=hash_before, hash_after=hash_after,
                ))

            return PatchResult(success=True, files_changed=file_changes)

        finally:
            if os.path.exists(patch_file):
                os.remove(patch_file)

    def commit(
        self,
        workspace: str,
        message: str,
        files: Optional[List[str]] = None,
    ) -> str:
        """Create a commit. Returns commit hash."""
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
        """Create and optionally checkout a branch."""
        safe_name = shlex.quote(name)
        if checkout:
            result = self.run(f"git checkout -b {safe_name}", workspace)
        else:
            result = self.run(f"git branch {safe_name}", workspace)
        return result.exit_code == 0

    def checkout(self, workspace: str, ref: str) -> bool:
        """Checkout a ref (branch, tag, commit)."""
        result = self.run(f"git checkout {shlex.quote(ref)}", workspace)
        return result.exit_code == 0

    # =========================================================================
    # FileOps Capability
    # =========================================================================

    def _validate_workspace_path(self, path: str) -> Optional[str]:
        """Validate that path is within the configured workspace."""
        if not self._workspace:
            return None
        try:
            abs_path = os.path.realpath(path)
            abs_workspace = os.path.realpath(self._workspace)
            if abs_path == abs_workspace or abs_path.startswith(abs_workspace + os.sep):
                return None
            return f"Path '{path}' is outside workspace boundary"
        except Exception:
            return f"Cannot validate path '{path}'"

    def read(self, path: str) -> str:
        """Read file contents."""
        error = self._validate_workspace_path(path)
        if error:
            return f"Error: {error}"
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            return f"Error: {str(e)}"

    def write(self, path: str, content: str, atomic: bool = True) -> FileChange:
        """Write content to file with atomic write support."""
        error = self._validate_workspace_path(path)
        if error:
            return FileChange(path=path, action="error", error_message=error)

        hash_before = None
        size_before = None
        if os.path.exists(path):
            hash_before = self._hash_file(path)
            size_before = os.path.getsize(path)
            action = "modified"
        else:
            action = "created"

        try:
            if atomic:
                dir_path = os.path.dirname(path) or "."
                os.makedirs(dir_path, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    mode="w", dir=dir_path, delete=False, encoding="utf-8",
                ) as f:
                    f.write(content)
                    temp_path = f.name
                os.replace(temp_path, path)
            else:
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)

            hash_after = self._hash_file(path)
            size_after = os.path.getsize(path)

            return FileChange(
                path=path, action=action,
                hash_before=hash_before, hash_after=hash_after,
                size_before=size_before, size_after=size_after,
            )
        except Exception:
            return FileChange(path=path, action="error", hash_before=hash_before)

    def list(self, path: str, recursive: bool = False) -> List[str]:
        """List files in directory."""
        error = self._validate_workspace_path(path)
        if error:
            return [f"Error: {error}"]
        try:
            if recursive:
                files = []
                for root, dirs, filenames in os.walk(path):
                    for filename in filenames:
                        rel_path = os.path.relpath(os.path.join(root, filename), path)
                        files.append(rel_path)
                return sorted(files)
            else:
                return sorted(os.listdir(path))
        except Exception as e:
            return [f"Error: {str(e)}"]

    def apply_diff(self, path: str, diff: str) -> FileChange:
        """Apply a unified diff to a file."""
        hash_before = self._hash_file(path) if os.path.exists(path) else None
        workspace = os.path.dirname(path) or "."
        filename = os.path.basename(path)

        diff_path = os.path.join(workspace, ".tmp_diff")
        with open(diff_path, "w") as f:
            f.write(diff)

        try:
            result = self.run(f"patch {filename} < .tmp_diff", workspace)
            if result.exit_code != 0:
                return FileChange(path=path, action="error", hash_before=hash_before)
            hash_after = self._hash_file(path)
            return FileChange(
                path=path, action="modified",
                hash_before=hash_before, hash_after=hash_after,
            )
        finally:
            if os.path.exists(diff_path):
                os.remove(diff_path)

    def delete(self, path: str) -> FileChange:
        """Delete a file."""
        error = self._validate_workspace_path(path)
        if error:
            return FileChange(path=path, action="error", error_message=error)

        if os.path.exists(path):
            hash_before = self._hash_file(path)
            size_before = os.path.getsize(path)
            try:
                os.remove(path)
                return FileChange(
                    path=path, action="deleted",
                    hash_before=hash_before, size_before=size_before,
                )
            except Exception:
                return FileChange(path=path, action="error", hash_before=hash_before)
        else:
            return FileChange(path=path, action="error")

    # =========================================================================
    # Helpers
    # =========================================================================

    def _is_denied_command(self, cmd: str) -> bool:
        """Check if command matches any denylist pattern."""
        cmd_lower = cmd.lower().strip()
        try:
            tokens = shlex.split(cmd_lower)
        except ValueError:
            tokens = None

        for denied in self.config.command_denylist:
            denied_lower = denied.lower()
            if tokens and " " not in denied_lower:
                if tokens[0] == denied_lower:
                    return True
            if denied_lower in cmd_lower:
                return True
        return False

    def _is_allowed_command(self, cmd: str) -> bool:
        """Check if command starts with an allowed executable."""
        parts = cmd.strip().split()
        if not parts:
            return False
        executable = parts[0]
        for allowed in self.config.command_allowlist:
            if executable == allowed or executable.endswith(f"/{allowed}"):
                return True
        return False

    def _bound_output(self, output: str, max_chars: int) -> Tuple[str, bool]:
        """Bound output to maximum character length."""
        if len(output) <= max_chars:
            return output, False
        return output[:max_chars] + "\n... (truncated)", True

    def _hash_file(self, path: str) -> str:
        """Compute SHA-256 hash of file contents."""
        try:
            with open(path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()
        except Exception:
            return ""

    def _extract_files_from_patch(self, patch: str) -> List[str]:
        """Extract file paths from a unified diff."""
        files = []
        for line in patch.split("\n"):
            if line.startswith("+++ b/"):
                files.append(line[6:])
            elif line.startswith("+++ "):
                path = line[4:].split("\t")[0]
                if path != "/dev/null":
                    files.append(path)
        return files
