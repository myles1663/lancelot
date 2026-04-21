from src.tools.contracts import ExecResult
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
