import os
import subprocess
from types import SimpleNamespace

import pytest

from src.tools.providers.local_sandbox import (
    DockerRunValidator,
    LocalSandboxProvider,
    SandboxConfig,
)


def test_docker_run_validator_blocks_privilege_escape_images_memory_and_mounts():
    safe = ["docker", "run", "--rm", "--memory=256m", "python:3.11-slim", "sh", "-c", "echo ok"]
    assert DockerRunValidator.validate(safe) is None
    assert DockerRunValidator.validate(["docker", "run", "--privileged", "python:3.11-slim"]) == (
        "Blocked Docker flag: --privileged"
    )
    assert DockerRunValidator.validate(["docker", "run", "--user", "root", "python:3.11-slim"]) == (
        "Cannot run Docker container as root"
    )
    assert DockerRunValidator.validate(["docker", "run", "--user=root", "python:3.11-slim"]) == (
        "Cannot run Docker container as root"
    )
    assert "allowlist" in DockerRunValidator.validate(["docker", "run", "ubuntu:latest", "sh", "-c", "true"])
    assert DockerRunValidator.validate(["docker", "run", "--memory=3g", "python:3.11-slim"]).startswith(
        "Memory limit"
    )
    assert DockerRunValidator.validate(["docker", "run", "-v", "/root:/mnt", "python:3.11-slim"]) == (
        "Mount from blocked host path: /root"
    )
    assert DockerRunValidator.validate(["docker", "run", "--volume=/tmp:/proc", "python:3.11-slim"]) == (
        "Mount to blocked container path: /proc"
    )


def test_docker_run_validator_parses_images_and_memory_units():
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        "C:/work:/workspace",
        "-e",
        "A=B",
        "-w",
        "/workspace",
        "python:3.11-slim",
        "sh",
        "-c",
        "echo ok",
    ]

    assert DockerRunValidator._extract_image(cmd) == "python:3.11-slim"
    assert DockerRunValidator._extract_image(["docker", "ps"]) is None
    assert DockerRunValidator._parse_memory_mb("1g") == 1024
    assert DockerRunValidator._parse_memory_mb("512m") == 512
    assert DockerRunValidator._parse_memory_mb("1024k") == 1
    assert DockerRunValidator._parse_memory_mb(str(2 * 1024 * 1024)) == 2
    assert DockerRunValidator._parse_memory_mb("not-a-size") is None


def _sandbox(tmp_path, **overrides):
    config = SandboxConfig(
        docker_image="python:3.11-slim",
        command_allowlist=["git", "echo", "python", "patch"],
        max_stdout_chars=8,
        max_stderr_chars=8,
        **overrides,
    )
    sandbox = LocalSandboxProvider(config=config, workspace=str(tmp_path))
    sandbox._docker_available = True
    return sandbox


def test_run_executes_docker_and_bounds_output(tmp_path, monkeypatch):
    sandbox = _sandbox(tmp_path)
    seen = []

    def _run(cmd, capture_output, text, timeout):
        seen.append((cmd, timeout))
        return SimpleNamespace(returncode=0, stdout="abcdefghijk", stderr="123456789")

    monkeypatch.setattr(subprocess, "run", _run)

    result = sandbox.run("echo hello", str(tmp_path), env={"SAFE_NAME": "hello world", "BAD-NAME": "skip"})

    assert result.exit_code == 0
    assert result.stdout.endswith("... (truncated)")
    assert result.stderr.endswith("... (truncated)")
    assert result.truncated is True
    docker_cmd = seen[0][0]
    assert "-e" in docker_cmd
    assert any(item.startswith("SAFE_NAME=") for item in docker_cmd)
    assert not any(item.startswith("BAD-NAME=") for item in docker_cmd)
    assert f"{os.path.abspath(str(tmp_path))}:/workspace" in docker_cmd


def test_run_handles_docker_timeout_exception_and_validation_failure(tmp_path, monkeypatch):
    sandbox = _sandbox(tmp_path)

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired(cmd="docker", timeout=1)),
    )
    timed_out = sandbox.run("echo hello", str(tmp_path), timeout_s=1)
    assert timed_out.exit_code == 124
    assert timed_out.timed_out is True

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("docker blew up")))
    failed = sandbox.run("echo hello", str(tmp_path))
    assert failed.exit_code == 1
    assert "docker blew up" in failed.stderr

    monkeypatch.setattr(DockerRunValidator, "validate", lambda _cmd: "bad mount")
    blocked = sandbox.run("echo hello", str(tmp_path))
    assert blocked.exit_code == 126
    assert "bad mount" in blocked.stderr


def test_docker_health_checks_success_failure_and_exceptions(monkeypatch):
    sandbox = LocalSandboxProvider(config=SandboxConfig())

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="25.0.1\n"),
    )
    assert sandbox._check_docker() == (True, "25.0.1")
    assert sandbox._check_image() is True

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=""))
    assert sandbox._check_docker() == (False, None)
    assert sandbox._check_image() is False

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError()))
    assert sandbox._check_docker() == (False, None)
    assert sandbox._check_image() is False


def test_repo_operations_parse_status_and_surface_errors(tmp_path, monkeypatch):
    sandbox = _sandbox(tmp_path)

    def _run(command, workspace, **_kwargs):
        if command == "git status --porcelain":
            return SimpleNamespace(success=True, stdout=" M modified.py\nA  added.py\nD  deleted.py\n?? new.py\n")
        if command.startswith("git diff"):
            return SimpleNamespace(success=True, stdout="diff body", stderr="", exit_code=0)
        if command.startswith("git add"):
            return SimpleNamespace(success=True, stdout="", stderr="", exit_code=0)
        if command.startswith("git commit"):
            return SimpleNamespace(success=False, stdout="", stderr="nothing to commit", exit_code=1)
        raise AssertionError(command)

    monkeypatch.setattr(sandbox, "run", _run)

    status = sandbox.status(str(tmp_path))
    assert status == {
        "modified": ["modified.py"],
        "added": ["added.py"],
        "deleted": ["deleted.py"],
        "untracked": ["new.py"],
    }
    assert sandbox.diff(str(tmp_path), ref="HEAD~1") == "diff body"
    assert sandbox.commit(str(tmp_path), "msg", files=[]) == "Error: local_sandbox.commit requires an explicit file list"
    assert sandbox.commit(str(tmp_path), "msg", files=["a.py"]) == "Error: nothing to commit"

    monkeypatch.setattr(
        sandbox,
        "run",
        lambda command, workspace, **_kwargs: SimpleNamespace(success=False, stderr="git failed", exit_code=2, stdout=""),
    )
    assert sandbox.status(str(tmp_path)) == {"error": "git failed", "exit_code": 2}
    assert sandbox.diff(str(tmp_path)) == "Error: git failed"


def test_apply_patch_dry_run_failure_success_and_cleanup(tmp_path, monkeypatch):
    sandbox = _sandbox(tmp_path)
    patch = """diff --git a/a.txt b/a.txt
--- a/a.txt
+++ b/a.txt
@@ -1 +1 @@
-old
+new
"""

    assert sandbox.apply_patch(str(tmp_path), "../escape").error_message == "Path traversal detected in patch"

    monkeypatch.setattr(
        sandbox,
        "run",
        lambda command, workspace: SimpleNamespace(exit_code=1, stderr="patch rejected", stdout="", success=False),
    )
    rejected = sandbox.apply_patch(str(tmp_path), patch, dry_run=True)
    assert rejected.success is False
    assert rejected.error_message == "patch rejected"

    target = tmp_path / "a.txt"
    target.write_text("old\n", encoding="utf-8")
    calls = []

    def _run(command, workspace):
        calls.append(command)
        return SimpleNamespace(exit_code=0, stderr="", stdout="", success=True)

    monkeypatch.setattr(sandbox, "run", _run)
    applied = sandbox.apply_patch(str(tmp_path), patch)
    assert applied.success is True
    assert applied.files_changed[0].path == "a.txt"
    assert calls == ["git apply .tmp_patch"]
    assert not (tmp_path / ".tmp_patch").exists()


def test_file_boundaries_non_atomic_write_list_apply_diff_and_delete_failures(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sandbox = _sandbox(workspace)
    outside = tmp_path / "outside.txt"

    assert "outside workspace boundary" in sandbox.read(str(outside))
    assert sandbox.write(str(outside), "blocked").action == "error"
    assert sandbox.delete(str(outside)).action == "error"

    target = workspace / "note.txt"
    created = sandbox.write(str(target), "one", atomic=False)
    assert created.action == "created"
    assert target.read_text(encoding="utf-8") == "one"

    monkeypatch.setattr("builtins.open", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("write failed")))
    failed = sandbox.write(str(workspace / "failed.txt"), "x", atomic=False)
    assert failed.action == "error"

    monkeypatch.undo()
    monkeypatch.setattr(sandbox, "run", lambda *args, **kwargs: SimpleNamespace(exit_code=1, stderr="bad patch"))
    target.write_text("one\n", encoding="utf-8")
    assert sandbox.apply_diff(str(target), "--- bad").action == "error"

    monkeypatch.setattr(os, "remove", lambda _path: (_ for _ in ()).throw(OSError("delete failed")))
    assert sandbox.delete(str(target)).action == "error"


def test_branch_checkout_and_workspace_validation_exception(tmp_path, monkeypatch):
    sandbox = _sandbox(tmp_path)
    commands = []
    monkeypatch.setattr(
        sandbox,
        "run",
        lambda command, workspace: commands.append(command)
        or SimpleNamespace(exit_code=0, stdout="hash123\n", stderr="", success=True),
    )

    assert sandbox.branch(str(tmp_path), "feature/test") is True
    assert sandbox.branch(str(tmp_path), "feature/test", checkout=False) is True
    assert sandbox.checkout(str(tmp_path), "main") is True
    assert commands == [
        "git checkout -b feature/test",
        "git branch feature/test",
        "git checkout main",
    ]

    monkeypatch.setattr(os.path, "realpath", lambda _path: (_ for _ in ()).throw(RuntimeError("bad path")))
    assert "Cannot validate path" in sandbox._validate_workspace_path(str(tmp_path / "file.txt"))
