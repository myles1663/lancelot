"""
LocalSandboxProvider — Docker-Based Tool Runner
================================================

Required provider implementing core capabilities via containerized execution.
All commands run inside a Docker container with workspace mounting,
timeout protection, and bounded output capture.

Capabilities provided:
- ShellExec: Run commands with stdout/stderr capture
- RepoOps: Git operations (status, diff, apply_patch, commit, branch)
- FileOps: File operations (read, write, list, apply_diff)
- DeployOps: Build, test, package operations

Security model:
- All execution happens in container (no host execution)
- Workspace mounted as volume
- Network disabled by default
- Command allowlist enforced
- Output bounded to prevent memory exhaustion
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import shlex
import shutil
import tempfile
import time
from dataclasses import dataclass, field
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
# F-001: Docker Run Validator
# =============================================================================


class DockerRunValidator:
    """Validates Docker run parameters before execution.

    Ensures every ``docker run`` invocation uses only approved images,
    stays within resource limits, and never requests dangerous capabilities.
    Works alongside the Docker socket proxy sidecar to provide defense-in-depth.
    """

    ALLOWED_IMAGES: set = {"python:3.11-slim"}
    MAX_MEMORY_MB: int = 512

    # Flags that must never appear in a docker run command
    BLOCKED_FLAGS: set = {
        "--privileged",
        "--cap-add",
        "--pid=host",
        "--ipc=host",
        "--userns=host",
        "--security-opt=seccomp=unconfined",
    }

    # Mount targets that must never be used
    BLOCKED_MOUNT_TARGETS: set = {
        "/",
        "/etc",
        "/root",
        "/home",
        "/proc",
        "/sys",
        "/dev",
        "/var/run/docker.sock",
    }

    @classmethod
    def validate(cls, docker_cmd: list) -> str | None:
        """Validate a docker run command list.

        Returns None if the command is safe, or an error message string
        if it violates any security policy.
        """
        cmd_str = " ".join(docker_cmd)

        # 1. Check for blocked flags
        for flag in cls.BLOCKED_FLAGS:
            if flag in docker_cmd:
                logger.warning("SECURITY BLOCK: Blocked Docker flag '%s' in: %s", flag, cmd_str[:200])
                return f"Blocked Docker flag: {flag}"

        # Also catch --user root
        for i, arg in enumerate(docker_cmd):
            if arg == "--user" and i + 1 < len(docker_cmd) and docker_cmd[i + 1] == "root":
                logger.warning("SECURITY BLOCK: Docker --user root in: %s", cmd_str[:200])
                return "Cannot run Docker container as root"
            if arg.startswith("--user=root"):
                logger.warning("SECURITY BLOCK: Docker --user=root in: %s", cmd_str[:200])
                return "Cannot run Docker container as root"

        # 2. Validate image (argument just before "sh" or the last non-flag arg)
        image = cls._extract_image(docker_cmd)
        if image and image not in cls.ALLOWED_IMAGES:
            logger.warning("SECURITY BLOCK: Unapproved Docker image '%s'", image)
            return f"Docker image not in allowlist: {image}"

        # 3. Validate memory limit
        for arg in docker_cmd:
            if arg.startswith("--memory="):
                mem_str = arg.split("=", 1)[1].lower()
                mem_mb = cls._parse_memory_mb(mem_str)
                if mem_mb is not None and mem_mb > cls.MAX_MEMORY_MB:
                    logger.warning("SECURITY BLOCK: Memory %dMB exceeds %dMB limit", mem_mb, cls.MAX_MEMORY_MB)
                    return f"Memory limit {mem_mb}MB exceeds maximum {cls.MAX_MEMORY_MB}MB"

        # 4. Validate volume mounts
        for i, arg in enumerate(docker_cmd):
            mount_value = None
            if arg == "-v" and i + 1 < len(docker_cmd):
                mount_value = docker_cmd[i + 1]
            elif arg.startswith("-v="):
                mount_value = arg[3:]
            elif arg.startswith("--volume="):
                mount_value = arg[9:]

            if mount_value:
                # Format: host_path:container_path[:options]
                parts = mount_value.split(":")
                if len(parts) >= 2:
                    host_source = parts[0]
                    container_target = parts[1]
                    # Block mounting sensitive host paths as source
                    for blocked in cls.BLOCKED_MOUNT_TARGETS:
                        if host_source == blocked or host_source.rstrip("/") == blocked:
                            logger.warning(
                                "SECURITY BLOCK: Mount from blocked host path '%s'",
                                host_source,
                            )
                            return f"Mount from blocked host path: {host_source}"
                    # Block mounting to sensitive container paths
                    for blocked in cls.BLOCKED_MOUNT_TARGETS:
                        if container_target == blocked or container_target.rstrip("/") == blocked:
                            logger.warning(
                                "SECURITY BLOCK: Mount to blocked target '%s'",
                                container_target,
                            )
                            return f"Mount to blocked container path: {container_target}"

        return None  # All checks passed

    @classmethod
    def _extract_image(cls, docker_cmd: list) -> str | None:
        """Extract the Docker image name from a docker run command."""
        # The image is the first argument after all flags/options
        # In our format: docker run --rm --memory=X --cpus=1 [--network=none] [-v ...] IMAGE sh -c CMD
        skip_next = False
        past_run = False
        for arg in docker_cmd:
            if arg == "run":
                past_run = True
                continue
            if not past_run:
                continue
            if skip_next:
                skip_next = False
                continue
            # Arguments that take a value
            if arg in ("-v", "-e", "-w", "--name", "--network", "--user", "--workdir"):
                skip_next = True
                continue
            # Flags with values attached
            if arg.startswith("-") or arg.startswith("--"):
                continue
            # First non-flag, non-option argument is the image
            return arg
        return None

    @staticmethod
    def _parse_memory_mb(mem_str: str) -> int | None:
        """Parse a Docker memory string (e.g. '512m', '1g') to megabytes."""
        try:
            if mem_str.endswith("g"):
                return int(float(mem_str[:-1]) * 1024)
            if mem_str.endswith("m"):
                return int(mem_str[:-1])
            if mem_str.endswith("k"):
                return max(1, int(int(mem_str[:-1]) / 1024))
            return int(mem_str) // (1024 * 1024)  # bytes to MB
        except (ValueError, TypeError):
            return None


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class SandboxConfig:
    """Configuration for LocalSandboxProvider."""

    # Docker settings
    docker_image: str = "python:3.11-slim"
    docker_timeout_s: int = 300
    docker_memory_limit: str = "512m"

    # Output limits
    max_stdout_chars: int = 100000
    max_stderr_chars: int = 50000

    # Network
    network_enabled: bool = False

    # Command allowlist (empty = allow all)
    command_allowlist: List[str] = field(default_factory=list)

    # Command denylist (always blocked)
    command_denylist: List[str] = field(default_factory=lambda: [
        "rm -rf /",
        "rm -rf /*",
        "mkfs",
        "dd if=/dev/zero",
        ":(){:|:&};:",  # Fork bomb
        "chmod -R 777 /",
        "chown -R",
    ])

    # Workspace settings
    workspace_mount_path: str = "/workspace"
    read_only_mounts: Dict[str, str] = field(default_factory=dict)


# =============================================================================
# LocalSandboxProvider
# =============================================================================


class LocalSandboxProvider(BaseProvider):
    """
    Docker-based tool runner providing core execution capabilities.

    This is the required baseline provider. Lancelot can function with
    only this provider - all other providers are optional enhancements.
    """

    def __init__(
        self,
        config: Optional[SandboxConfig] = None,
        workspace: Optional[str] = None,
    ):
        """
        Initialize the LocalSandboxProvider.

        Args:
            config: Optional SandboxConfig (uses defaults if not provided)
            workspace: Default workspace path for operations
        """
        self.config = config or SandboxConfig()
        self._workspace = workspace
        self._docker_available: Optional[bool] = None
        self._last_health_check: Optional[str] = None

    @property
    def provider_id(self) -> str:
        """Unique provider identifier."""
        return "local_sandbox"

    @property
    def capabilities(self) -> List[Capability]:
        """List of capabilities this provider implements."""
        return [
            Capability.SHELL_EXEC,
            Capability.REPO_OPS,
            Capability.FILE_OPS,
            Capability.WEB_OPS,
            Capability.DEPLOY_OPS,
        ]

    # =========================================================================
    # Health Check
    # =========================================================================

    def health_check(self) -> ProviderHealth:
        """
        Check provider health by verifying Docker availability.

        Returns:
            ProviderHealth with current state
        """
        from datetime import datetime, timezone

        degraded_reasons = []
        error_message = None
        state = ProviderState.HEALTHY

        # Check Docker availability
        docker_ok, docker_version = self._check_docker()
        if not docker_ok:
            state = ProviderState.OFFLINE
            error_message = "Docker not available"
        else:
            # Check if image is available
            image_ok = self._check_image()
            if not image_ok:
                state = ProviderState.DEGRADED
                degraded_reasons.append(f"Image {self.config.docker_image} not pulled")

        self._docker_available = docker_ok
        self._last_health_check = datetime.now(timezone.utc).isoformat()

        return ProviderHealth(
            provider_id=self.provider_id,
            state=state,
            version=docker_version,
            last_check=self._last_health_check,
            capabilities=[c.value for c in self.capabilities],
            degraded_reasons=degraded_reasons,
            error_message=error_message,
            metadata={
                "docker_image": self.config.docker_image,
                "network_enabled": self.config.network_enabled,
            },
        )

    def _check_docker(self) -> Tuple[bool, Optional[str]]:
        """Check if Docker is available and get version."""
        try:
            result = subprocess.run(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return True, result.stdout.strip()
            return False, None
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
            logger.warning("Docker check failed: %s", e)
            return False, None

    def _check_image(self) -> bool:
        """Check if the configured Docker image is available."""
        try:
            result = subprocess.run(
                ["docker", "image", "inspect", self.config.docker_image],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return False

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
        """
        Execute a command in the Docker sandbox.

        Args:
            command: Command string or list of arguments
            cwd: Working directory (must be within workspace)
            env: Optional environment variables
            timeout_s: Timeout in seconds
            stream: If True, stream output (not yet implemented)
            network: If True, allow network access

        Returns:
            ExecResult with stdout, stderr, exit code, and timing
        """
        start_time = time.time()

        # Validate command against denylist
        cmd_str = command if isinstance(command, str) else " ".join(command)
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
                    stderr=f"Command not in allowlist",
                    duration_ms=int((time.time() - start_time) * 1000),
                    command=cmd_str,
                    working_dir=cwd,
                )

        # Check Docker availability
        if self._docker_available is None:
            self.health_check()
        if not self._docker_available:
            return ExecResult(
                exit_code=127,
                stdout="",
                stderr="Docker not available. Cannot execute command.",
                duration_ms=int((time.time() - start_time) * 1000),
                command=cmd_str,
                working_dir=cwd,
            )

        # Determine network mode
        use_network = network and self.config.network_enabled

        # Build Docker command
        docker_cmd = self._build_docker_command(
            command=cmd_str,
            workspace=cwd,
            env=env,
            network=use_network,
            timeout_s=timeout_s,
        )

        # F-001: Validate Docker command before execution
        validation_error = DockerRunValidator.validate(docker_cmd)
        if validation_error:
            return ExecResult(
                exit_code=126,
                stdout="",
                stderr=f"Docker security validation failed: {validation_error}",
                duration_ms=int((time.time() - start_time) * 1000),
                command=cmd_str,
                working_dir=cwd,
            )

        # Execute
        try:
            result = subprocess.run(
                docker_cmd,
                capture_output=True,
                text=True,
                timeout=timeout_s + 5,  # Extra buffer for Docker overhead
            )

            # Bound output
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
                working_dir=cwd,
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
                working_dir=cwd,
                timed_out=True,
            )
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return ExecResult(
                exit_code=1,
                stdout="",
                stderr=f"Execution error: {str(e)[:200]}",
                duration_ms=duration_ms,
                command=cmd_str,
                working_dir=cwd,
            )

    def _build_docker_command(
        self,
        command: str,
        workspace: str,
        env: Optional[Dict[str, str]],
        network: bool,
        timeout_s: int,
    ) -> List[str]:
        """Build the Docker run command."""
        docker_cmd = [
            "docker", "run",
            "--rm",
            f"--memory={self.config.docker_memory_limit}",
            "--cpus=1",
        ]

        # Network mode
        if not network:
            docker_cmd.append("--network=none")

        # Workspace mount
        if workspace and os.path.exists(workspace):
            abs_workspace = os.path.abspath(workspace)
            docker_cmd.extend(["-v", f"{abs_workspace}:{self.config.workspace_mount_path}"])
            docker_cmd.extend(["-w", self.config.workspace_mount_path])

        # Read-only mounts
        for host_path, container_path in self.config.read_only_mounts.items():
            if os.path.exists(host_path):
                docker_cmd.extend(["-v", f"{host_path}:{container_path}:ro"])

        # Environment variables
        if env:
            for key, value in env.items():
                # Sanitize env var names and values
                if key.isidentifier():
                    safe_value = shlex.quote(value)
                    docker_cmd.extend(["-e", f"{key}={safe_value}"])

        # Image and command
        docker_cmd.append(self.config.docker_image)
        docker_cmd.extend(["sh", "-c", command])

        return docker_cmd

    def _is_denied_command(self, cmd: str) -> bool:
        """Check if command matches any denylist pattern."""
        cmd_lower = cmd.lower().strip()

        # Try to parse the command to check first token more precisely
        try:
            tokens = shlex.split(cmd_lower)
        except ValueError:
            tokens = None

        for denied in self.config.command_denylist:
            denied_lower = denied.lower()
            # Check if the first token matches single-word denylist entries
            if tokens and " " not in denied_lower:
                if tokens[0] == denied_lower:
                    return True
            # Fall back to substring match for multi-word patterns
            if denied_lower in cmd_lower:
                return True
        return False

    def _is_allowed_command(self, cmd: str) -> bool:
        """Check if command starts with an allowed executable."""
        # Extract first word (the executable)
        parts = cmd.strip().split()
        if not parts:
            return False
        executable = parts[0]

        # Check against allowlist
        for allowed in self.config.command_allowlist:
            if executable == allowed or executable.endswith(f"/{allowed}"):
                return True
        return False

    def _bound_output(self, output: str, max_chars: int) -> Tuple[str, bool]:
        """Bound output to maximum character length."""
        if len(output) <= max_chars:
            return output, False
        return output[:max_chars] + "\n... (truncated)", True

    # =========================================================================
    # RepoOps Capability
    # =========================================================================

    def status(self, workspace: str) -> Dict[str, Any]:
        """Get Git repository status."""
        result = self.run("git status --porcelain", workspace)
        if not result.success:
            return {"error": result.stderr, "exit_code": result.exit_code}

        # Parse porcelain output
        files = {"modified": [], "added": [], "deleted": [], "untracked": []}
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            status = line[:2]
            filepath = line[3:]
            if status[0] == "M" or status[1] == "M":
                files["modified"].append(filepath)
            elif status[0] == "A":
                files["added"].append(filepath)
            elif status[0] == "D":
                files["deleted"].append(filepath)
            elif status == "??":
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
        # Validate patch doesn't contain path traversal
        if ".." in patch or patch.startswith("/"):
            return PatchResult(
                success=False,
                files_changed=[],
                error_message="Path traversal detected in patch",
            )

        # Write patch to temp file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".patch", delete=False
        ) as f:
            f.write(patch)
            patch_file = f.name

        try:
            # Copy patch file to workspace
            patch_in_workspace = os.path.join(workspace, ".tmp_patch")
            shutil.copy(patch_file, patch_in_workspace)

            # Get files that will be changed (for hashing before)
            check_cmd = "git apply --check .tmp_patch 2>&1"
            if dry_run:
                result = self.run(check_cmd, workspace)
                os.remove(patch_in_workspace)
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

            # Apply the patch
            apply_cmd = "git apply .tmp_patch"
            result = self.run(apply_cmd, workspace)

            # Clean up
            if os.path.exists(patch_in_workspace):
                os.remove(patch_in_workspace)

            if result.exit_code != 0:
                return PatchResult(
                    success=False,
                    files_changed=[],
                    rejected_hunks=[result.stderr],
                    error_message=result.stderr,
                )

            # Get after hashes and build file changes
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
                    path=filepath,
                    action=action,
                    hash_before=hash_before,
                    hash_after=hash_after,
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
        # Stage files
        if files:
            for f in files:
                self.run(f"git add {shlex.quote(f)}", workspace)
        else:
            self.run("git add -A", workspace)

        # Commit (shlex.quote handles all shell escaping)
        safe_message = shlex.quote(message)
        result = self.run(f"git commit -m {safe_message}", workspace)

        if result.exit_code != 0:
            return f"Error: {result.stderr}"

        # Get commit hash
        hash_result = self.run("git rev-parse HEAD", workspace)
        return hash_result.stdout.strip()

    def branch(
        self,
        workspace: str,
        name: str,
        checkout: bool = True,
    ) -> bool:
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
        """Validate that path is within the configured workspace.

        Returns None if valid, or an error message if not.
        """
        if not self._workspace:
            return None  # No workspace restriction configured
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
        # Get hash before if file exists
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
                # Write to temp file, then rename
                dir_path = os.path.dirname(path) or "."
                os.makedirs(dir_path, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    dir=dir_path,
                    delete=False,
                    encoding="utf-8",
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
                path=path,
                action=action,
                hash_before=hash_before,
                hash_after=hash_after,
                size_before=size_before,
                size_after=size_after,
            )
        except Exception as e:
            return FileChange(
                path=path,
                action="error",
                hash_before=hash_before,
            )

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
        # Get before hash
        hash_before = self._hash_file(path) if os.path.exists(path) else None

        # Use patch command via run
        workspace = os.path.dirname(path) or "."
        filename = os.path.basename(path)

        # Write diff to temp file in workspace
        diff_path = os.path.join(workspace, ".tmp_diff")
        with open(diff_path, "w") as f:
            f.write(diff)

        try:
            result = self.run(f"patch {filename} < .tmp_diff", workspace)

            if result.exit_code != 0:
                return FileChange(
                    path=path,
                    action="error",
                    hash_before=hash_before,
                )

            hash_after = self._hash_file(path)
            return FileChange(
                path=path,
                action="modified",
                hash_before=hash_before,
                hash_after=hash_after,
            )
        finally:
            if os.path.exists(diff_path):
                os.remove(diff_path)

    def delete(self, path: str) -> FileChange:
        """Delete a file."""
        error = self._validate_workspace_path(path)
        if error:
            return FileChange(path=path, action="error", error_message=error)
        hash_before = None
        size_before = None

        if os.path.exists(path):
            hash_before = self._hash_file(path)
            size_before = os.path.getsize(path)
            try:
                os.remove(path)
                return FileChange(
                    path=path,
                    action="deleted",
                    hash_before=hash_before,
                    size_before=size_before,
                )
            except Exception as e:
                return FileChange(
                    path=path,
                    action="error",
                    hash_before=hash_before,
                )
        else:
            return FileChange(
                path=path,
                action="error",
            )

    # =========================================================================
    # Helper Methods
    # =========================================================================

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
                # Handle non-git diffs
                path = line[4:].split("\t")[0]
                if path != "/dev/null":
                    files.append(path)
        return files


# =============================================================================
# Convenience Factory
# =============================================================================


def create_local_sandbox(
    workspace: Optional[str] = None,
    docker_image: str = "python:3.11-slim",
    network_enabled: bool = False,
    allowlist: Optional[List[str]] = None,
) -> LocalSandboxProvider:
    """
    Factory function for creating LocalSandboxProvider.

    Args:
        workspace: Default workspace path
        docker_image: Docker image to use
        network_enabled: Whether to enable network access
        allowlist: Command allowlist (empty = allow all)

    Returns:
        Configured LocalSandboxProvider
    """
    config = SandboxConfig(
        docker_image=docker_image,
        network_enabled=network_enabled,
        command_allowlist=allowlist or [],
    )
    return LocalSandboxProvider(config=config, workspace=workspace)
