from src.tools.contracts import Capability, ExecResult, ProviderState
from src.tools.providers.host_execution import HostExecConfig, HostExecutionProvider


def test_host_execution_commit_requires_explicit_files(monkeypatch):
    provider = HostExecutionProvider(config=HostExecConfig())

    calls = []

    def fake_run(command, workspace, **kwargs):
        calls.append(command)
        return ExecResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            duration_ms=1,
            command=command,
            working_dir=workspace,
        )

    monkeypatch.setattr(provider, "run", fake_run)

    result = provider.commit("C:/repo", "test commit", files=None)

    assert result == "Error: host_execution.commit requires an explicit file list"
    assert calls == []


def test_host_execution_commit_only_stages_requested_files(monkeypatch):
    provider = HostExecutionProvider(config=HostExecConfig())

    calls = []

    def fake_run(command, workspace, **kwargs):
        calls.append(command)
        stdout = "def456\n" if command == ["git", "rev-parse", "HEAD"] else "ok"
        return ExecResult(
            exit_code=0,
            stdout=stdout,
            stderr="",
            duration_ms=1,
            command=command,
            working_dir=workspace,
        )

    monkeypatch.setattr(provider, "run", fake_run)

    result = provider.commit("C:/repo", "test commit", files=["a.txt", "b.txt"])

    assert result == "def456"
    assert "git add -A" not in calls
    assert calls[:2] == [["git", "add", "a.txt"], ["git", "add", "b.txt"]]


def test_host_execution_rejects_cwd_outside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    provider = HostExecutionProvider(config=HostExecConfig(), workspace=str(workspace))

    result = provider.run(["git", "status"], str(outside))

    assert result.exit_code == 126
    assert "outside workspace boundary" in result.stderr


def test_host_execution_uses_shell_false(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = HostExecutionProvider(config=HostExecConfig(), workspace=str(workspace))

    captured = {}

    class _Completed:
        def __init__(self):
            self.returncode = 0
            self.stdout = "ok"
            self.stderr = ""

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return _Completed()

    monkeypatch.setattr("src.tools.providers.host_execution.subprocess.run", fake_run)

    result = provider.run(["git", "status"], str(workspace))

    assert result.exit_code == 0
    assert captured["command"] == ["git", "status"]
    assert captured["kwargs"]["shell"] is False


def test_host_execution_filters_env_and_bounds_subprocess_output(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = HostExecutionProvider(
        config=HostExecConfig(max_stdout_chars=4, max_stderr_chars=3),
        workspace=str(workspace),
    )

    captured = {}

    class _Completed:
        returncode = 0
        stdout = "abcdef"
        stderr = "wxyz"

    def fake_run(command, **kwargs):
        captured["env"] = kwargs["env"]
        return _Completed()

    monkeypatch.setattr("src.tools.providers.host_execution.subprocess.run", fake_run)

    result = provider.run(["echo", "ok"], str(workspace), env={"SAFE_KEY": "yes", "bad-key": "no"})

    assert result.stdout.startswith("abcd")
    assert result.stderr.startswith("wxy")
    assert result.truncated is True
    assert captured["env"]["SAFE_KEY"] == "yes"
    assert "bad-key" not in captured["env"]


def test_host_execution_identity_health_and_command_guards(tmp_path):
    provider = HostExecutionProvider(
        config=HostExecConfig(command_allowlist=["git"]),
        workspace=str(tmp_path),
    )

    health = provider.health_check()
    denied = provider.run("rm -rf /", str(tmp_path))
    not_allowed = provider.run("python -V", str(tmp_path))
    empty_error, empty_args = provider._prepare_command([])

    assert provider.provider_id == "host_execution"
    assert Capability.SHELL_EXEC in provider.capabilities
    assert health.state == ProviderState.HEALTHY
    assert health.metadata["warning"] == "No container isolation"
    assert denied.exit_code == 126
    assert "blocked by security policy" in denied.stderr
    assert not_allowed.exit_code == 126
    assert not_allowed.stderr == "Command not in allowlist"
    assert (empty_error, empty_args) == ("Empty command", [])


def test_host_execution_empty_string_command_and_invalid_syntax(tmp_path):
    provider = HostExecutionProvider(config=HostExecConfig(), workspace=str(tmp_path))

    empty = provider.run("   ", str(tmp_path))
    bad = provider.run('"unterminated', str(tmp_path))

    assert empty.exit_code == 126
    assert empty.stderr == "Empty command"
    assert bad.exit_code == 126
    assert "Invalid command syntax" in bad.stderr


def test_host_execution_status_parses_porcelain_without_stripping_status_column(monkeypatch):
    provider = HostExecutionProvider(config=HostExecConfig())

    monkeypatch.setattr(
        provider,
        "run",
        lambda command, workspace, **_kwargs: ExecResult(
            exit_code=0,
            stdout=" M modified.py\nA  added.py\nD  deleted.py\n?? new.py\n",
            stderr="",
            duration_ms=1,
            command=command,
            working_dir=workspace,
        ),
    )

    assert provider.status("C:/repo") == {
        "modified": ["modified.py"],
        "added": ["added.py"],
        "deleted": ["deleted.py"],
        "untracked": ["new.py"],
    }

    monkeypatch.setattr(
        provider,
        "run",
        lambda *_args, **_kwargs: ExecResult(exit_code=1, stdout="", stderr="not a repo", duration_ms=1),
    )
    assert provider.status("C:/repo") == {"error": "not a repo", "exit_code": 1}


def test_host_execution_repo_diff_branch_checkout_and_commit_failure(monkeypatch):
    provider = HostExecutionProvider(config=HostExecConfig())
    calls = []

    def fake_run(command, workspace, **_kwargs):
        calls.append(command)
        if command[:2] == ["git", "commit"]:
            return ExecResult(exit_code=1, stdout="", stderr="nothing to commit", duration_ms=1)
        if command[:2] == ["git", "branch"]:
            return ExecResult(exit_code=1, stdout="", stderr="exists", duration_ms=1)
        return ExecResult(exit_code=0, stdout="ok", stderr="", duration_ms=1)

    monkeypatch.setattr(provider, "run", fake_run)

    assert provider.diff("C:/repo") == "ok"
    assert provider.diff("C:/repo", ref="main") == "ok"
    assert provider.commit("C:/repo", "msg", files=["a.txt"]) == "Error: nothing to commit"
    assert provider.branch("C:/repo", "codex/test", checkout=True) is True
    assert provider.branch("C:/repo", "codex/test", checkout=False) is False
    assert provider.checkout("C:/repo", "main") is True
    assert ["git", "diff"] in calls
    assert ["git", "diff", "main"] in calls


def test_host_execution_diff_reports_git_errors(monkeypatch):
    provider = HostExecutionProvider(config=HostExecConfig())
    monkeypatch.setattr(
        provider,
        "run",
        lambda *_args, **_kwargs: ExecResult(exit_code=2, stdout="", stderr="bad ref", duration_ms=1),
    )

    assert provider.diff("C:/repo", ref="missing") == "Error: bad ref"


def test_host_execution_workspace_path_and_file_error_paths(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    provider = HostExecutionProvider(config=HostExecConfig(), workspace=str(workspace))

    assert "outside workspace boundary" in provider.read(str(outside))
    assert provider.write(str(outside), "x").error_message
    assert provider.list(str(outside)) == [f"Error: Path '{outside}' is outside workspace boundary"]
    assert provider.delete(str(outside)).error_message

    file_path = workspace / "file.txt"
    monkeypatch.setattr("builtins.open", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("open failed")))
    assert "open failed" in provider.read(str(file_path))
    monkeypatch.undo()
    provider = HostExecutionProvider(config=HostExecConfig(), workspace=str(workspace))
    monkeypatch.setattr("src.tools.providers.host_execution.os.replace", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("replace failed")))
    assert provider.write(str(file_path), "x").action == "error"


def test_host_execution_file_mutation_and_validation_failures(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_path = workspace / "file.txt"
    file_path.write_text("content", encoding="utf-8")
    provider = HostExecutionProvider(config=HostExecConfig(), workspace=str(workspace))

    assert provider._validate_workspace_path(str(file_path)) is None
    monkeypatch.setattr(
        "src.tools.providers.host_execution.os.path.realpath",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("realpath failed")),
    )
    assert "Cannot validate path" in provider._validate_workspace_path(str(file_path))
    monkeypatch.undo()

    monkeypatch.setattr(
        "src.tools.providers.host_execution.os.listdir",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("list failed")),
    )
    assert provider.list(str(workspace)) == ["Error: list failed"]
    monkeypatch.undo()

    provider = HostExecutionProvider(config=HostExecConfig(), workspace=str(workspace))
    monkeypatch.setattr(
        "src.tools.providers.host_execution.os.remove",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("remove failed")),
    )
    assert provider.delete(str(file_path)).action == "error"


def test_host_execution_path_resolution_and_helper_failures(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = HostExecutionProvider(config=HostExecConfig(), workspace=str(workspace))
    outside = tmp_path / "outside"
    outside.mkdir()

    assert "outside workspace boundary" in provider._resolve_working_dir(str(outside))[0]

    monkeypatch.setattr(
        "src.tools.providers.host_execution.os.path.realpath",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("realpath failed")),
    )
    assert provider._resolve_working_dir(str(workspace))[0].startswith("Invalid working directory")
    monkeypatch.undo()

    isdir_calls = []

    def fake_isdir(path):
        isdir_calls.append(path)
        return len(isdir_calls) == 1

    monkeypatch.setattr("src.tools.providers.host_execution.os.path.isdir", fake_isdir)
    assert provider._resolve_working_dir(str(workspace))[0].startswith("Invalid working directory")
    monkeypatch.undo()

    monkeypatch.setattr(
        "builtins.open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("hash failed")),
    )
    assert provider._hash_file(str(workspace / "file.txt")) == ""


def test_host_execution_command_denial_and_windows_builtin_preparation(monkeypatch):
    provider = HostExecutionProvider(config=HostExecConfig())

    assert provider._is_denied_command('"unterminated rm') is False

    monkeypatch.setattr("src.tools.providers.host_execution.os.name", "nt")
    assert provider._prepare_command("dir")[1] == ["cmd.exe", "/d", "/s", "/c", "dir"]
    assert provider._prepare_command("dir & echo bad")[0] == "Blocked shell metacharacter in Windows builtin command"
    assert provider._prepare_command("python -V")[1] == ["python", "-V"]
    assert provider._prepare_command("   ")[0] == "Empty command"
    assert "Invalid command syntax" in provider._prepare_command('"unterminated')[0]


def test_host_execution_apply_patch_and_diff_helpers(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_path = workspace / "a.txt"
    file_path.write_text("before", encoding="utf-8")
    provider = HostExecutionProvider(config=HostExecConfig(), workspace=str(workspace))

    assert provider.apply_patch(str(workspace), "../bad.patch").success is False
    assert provider._extract_files_from_patch("+++ b/a.txt\n+++ /dev/null\n+++ plain.txt") == ["a.txt", "plain.txt"]

    monkeypatch.setattr(
        provider,
        "run",
        lambda command, workspace, **_kwargs: ExecResult(exit_code=1, stdout="", stderr="reject", duration_ms=1),
    )
    dry = provider.apply_patch(str(workspace), "+++ b/a.txt", dry_run=True)
    failed = provider.apply_patch(str(workspace), "+++ b/a.txt")

    assert dry.success is False
    assert dry.error_message == "reject"
    assert failed.rejected_hunks == ["reject"]


def test_host_execution_apply_patch_success_tracks_deleted_files(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_path = workspace / "gone.txt"
    file_path.write_text("before", encoding="utf-8")
    provider = HostExecutionProvider(config=HostExecConfig(), workspace=str(workspace))

    def fake_run(command, _workspace, **_kwargs):
        if command == ["git", "apply", ".tmp_patch"]:
            file_path.unlink()
        return ExecResult(exit_code=0, stdout="", stderr="", duration_ms=1)

    monkeypatch.setattr(provider, "run", fake_run)

    result = provider.apply_patch(str(workspace), "+++ b/gone.txt\n")

    assert result.success is True
    assert result.files_changed[0].path == "gone.txt"
    assert result.files_changed[0].action == "deleted"
