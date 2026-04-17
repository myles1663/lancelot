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
        stdout = "def456\n" if command == "git rev-parse HEAD" else "ok"
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
    assert calls[:2] == ["git add a.txt", "git add b.txt"]
