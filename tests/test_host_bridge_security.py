import logging
import json
import urllib.error

import pytest
from fastapi.responses import JSONResponse

import host_agent.agent as host_agent_module
from src.core import flags_api
from src.tools.contracts import Capability, ExecResult, ProviderState
from src.tools.providers.host_bridge import HostBridgeConfig, HostBridgeProvider


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _provider(**config_overrides):
    config = HostBridgeConfig(
        agent_url=config_overrides.pop("agent_url", "http://host.docker.internal:9111"),
        agent_token=config_overrides.pop("agent_token", "real-secret-token"),
        **config_overrides,
    )
    return HostBridgeProvider(config=config)


def _exec(exit_code=0, stdout="", stderr="", command="cmd", working_dir="C:/repo"):
    return ExecResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=1,
        command=command,
        working_dir=working_dir,
    )


def test_host_bridge_identity_and_capabilities():
    provider = _provider()

    assert provider.provider_id == "host_bridge"
    assert set(provider.capabilities) == {
        Capability.SHELL_EXEC,
        Capability.REPO_OPS,
        Capability.FILE_OPS,
        Capability.DEPLOY_OPS,
    }


def test_host_bridge_config_rejects_legacy_default_token(monkeypatch):
    monkeypatch.setenv("HOST_AGENT_TOKEN", "lancelot-host-agent")

    config = HostBridgeConfig(agent_url="http://host.docker.internal:9111")

    assert config.agent_token == ""


def test_host_bridge_config_reads_and_classifies_env(monkeypatch):
    monkeypatch.setenv("HOST_AGENT_URL", "http://127.0.0.1:9111")
    monkeypatch.setenv("HOST_AGENT_TOKEN", "  configured-token  ")

    config = HostBridgeConfig()

    assert config.agent_url == "http://127.0.0.1:9111"
    assert config.agent_token == "configured-token"
    assert config.agent_token_state == "configured"


def test_host_bridge_request_sends_bearer_json_and_parses_response(monkeypatch):
    provider = _provider()
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return _Response({"ok": True})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    response = provider._request("POST", "/execute", body={"command": "whoami"}, timeout=12)

    request, timeout = calls[0]
    assert response == {"ok": True}
    assert timeout == 12
    assert request.full_url == "http://host.docker.internal:9111/execute"
    assert request.get_method() == "POST"
    assert request.headers["Authorization"] == "Bearer real-secret-token"
    assert json.loads(request.data.decode("utf-8")) == {"command": "whoami"}


def test_host_bridge_request_rejects_missing_and_legacy_tokens():
    missing = _provider(agent_token="")
    missing.config.agent_token_state = "missing"
    legacy = _provider(agent_token="")
    legacy.config.agent_token_state = "legacy_default"

    with pytest.raises(ConnectionError, match="not configured"):
        missing._request("GET", "/health")
    with pytest.raises(ConnectionError, match="legacy default"):
        legacy._request("GET", "/health")


def test_host_bridge_health_check_reports_missing_token(monkeypatch):
    monkeypatch.delenv("HOST_AGENT_TOKEN", raising=False)

    provider = HostBridgeProvider(
        config=HostBridgeConfig(agent_url="http://host.docker.internal:9111")
    )
    health = provider.health_check()

    assert health.state == ProviderState.OFFLINE
    assert health.metadata["auth_configured"] is False
    assert health.metadata["auth_state"] == "missing"
    assert "HOST_AGENT_TOKEN is not configured." in health.degraded_reasons


def test_host_bridge_health_check_reports_legacy_default_token(monkeypatch):
    monkeypatch.setenv("HOST_AGENT_TOKEN", "lancelot-host-agent")

    provider = HostBridgeProvider(
        config=HostBridgeConfig(agent_url="http://host.docker.internal:9111")
    )
    health = provider.health_check()

    assert health.state == ProviderState.OFFLINE
    assert health.metadata["auth_configured"] is False
    assert health.metadata["auth_state"] == "legacy_default"
    assert "rejected legacy default value" in health.degraded_reasons[0]
    assert "legacy default" in health.error_message


def test_host_bridge_health_check_reports_healthy_agent(monkeypatch):
    provider = _provider()

    def fake_request(method, path, timeout=None, body=None):
        assert (method, path, timeout, body) == ("GET", "/health", 5, None)
        return {
            "platform": "Windows",
            "hostname": "build-host",
            "agent_version": "1.2.3",
        }

    monkeypatch.setattr(provider, "_request", fake_request)

    health = provider.health_check()

    assert health.state == ProviderState.HEALTHY
    assert health.metadata["auth_configured"] is True
    assert health.metadata["host_platform"] == "Windows"
    assert health.metadata["host_hostname"] == "build-host"


def test_host_bridge_health_check_reports_unreachable_agent(monkeypatch):
    provider = _provider()
    monkeypatch.setattr(provider, "_request", lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError("down")))

    health = provider.health_check()

    assert health.state == ProviderState.OFFLINE
    assert health.metadata["auth_configured"] is True
    assert "Host agent unreachable" in health.degraded_reasons[0]


def test_host_agent_run_server_rejects_legacy_default_token():
    with pytest.raises(ValueError, match="HOST_AGENT_TOKEN must be explicitly configured"):
        host_agent_module.run_server(port=9111, token="lancelot-host-agent")


@pytest.mark.asyncio
async def test_host_agent_shutdown_requires_configured_token(monkeypatch):
    monkeypatch.delenv("HOST_AGENT_TOKEN", raising=False)

    response = await flags_api.shutdown_host_agent()

    assert isinstance(response, JSONResponse)
    assert response.status_code == 503
    assert b"HOST_AGENT_TOKEN is not configured" in response.body


@pytest.mark.asyncio
async def test_host_agent_status_reports_auth_configuration(monkeypatch):
    monkeypatch.delenv("HOST_AGENT_TOKEN", raising=False)

    async def _status():
        return await flags_api.get_host_agent_status()

    result = await _status()

    assert result["auth_configured"] is False
    assert result["auth_state"] == "missing"


@pytest.mark.asyncio
async def test_host_agent_shutdown_rejects_legacy_default_token(monkeypatch):
    monkeypatch.setenv("HOST_AGENT_TOKEN", "lancelot-host-agent")

    response = await flags_api.shutdown_host_agent()

    assert isinstance(response, JSONResponse)
    assert response.status_code == 503
    assert b"legacy default value" in response.body


def test_host_bridge_commit_requires_explicit_files(monkeypatch):
    provider = HostBridgeProvider(
        config=HostBridgeConfig(
            agent_url="http://host.docker.internal:9111",
            agent_token="real-secret-token",
        )
    )

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

    assert result == "Error: host_bridge.commit requires an explicit file list"
    assert calls == []


def test_host_bridge_commit_only_stages_requested_files(monkeypatch):
    provider = HostBridgeProvider(
        config=HostBridgeConfig(
            agent_url="http://host.docker.internal:9111",
            agent_token="real-secret-token",
        )
    )

    calls = []

    def fake_run(command, workspace, **kwargs):
        calls.append(command)
        stdout = "abc123\n" if command == "git rev-parse HEAD" else "ok"
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

    assert result == "abc123"
    assert "git add -A" not in calls
    assert calls[:2] == ["git add a.txt", "git add b.txt"]


def test_host_bridge_repo_status_parses_porcelain_and_errors(monkeypatch):
    provider = _provider()

    monkeypatch.setattr(
        provider,
        "run",
        lambda command, workspace, **_kwargs: _exec(
            stdout=" M modified.py\nA  added.py\nD  deleted.py\n?? new.py\n",
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

    monkeypatch.setattr(provider, "run", lambda *_args, **_kwargs: _exec(exit_code=1, stderr="not a repo"))
    assert provider.status("C:/repo") == {"error": "not a repo", "exit_code": 1}


def test_host_bridge_diff_quotes_refs_and_reports_errors(monkeypatch):
    provider = _provider()
    calls = []

    def fake_run(command, workspace, **_kwargs):
        calls.append(command)
        return _exec(stdout="diff text", command=command, working_dir=workspace)

    monkeypatch.setattr(provider, "run", fake_run)

    assert provider.diff("C:/repo") == "diff text"
    assert provider.diff("C:/repo", ref="feature branch") == "diff text"
    assert calls == ["git diff", "git diff 'feature branch'"]

    monkeypatch.setattr(provider, "run", lambda *_args, **_kwargs: _exec(exit_code=1, stderr="bad ref"))
    assert provider.diff("C:/repo", ref="missing") == "Error: bad ref"


def test_host_bridge_apply_patch_guards_dry_run_and_apply_paths(monkeypatch):
    provider = _provider()
    calls = []

    assert provider.apply_patch("C:/repo", "../escape.patch").success is False
    assert provider.apply_patch("C:/repo", "/absolute.patch").success is False

    def ok_run(command, workspace, **_kwargs):
        calls.append((command, workspace))
        return _exec(command=command, working_dir=workspace)

    monkeypatch.setattr(provider, "run", ok_run)

    dry = provider.apply_patch("C:/repo", "diff --git a/a b/a", dry_run=True)
    applied = provider.apply_patch("C:/repo", "diff --git a/a b/a", dry_run=False)

    assert dry.success is True
    assert applied.success is True
    assert "--check" in calls[0][0]
    assert "git apply -" in calls[1][0]

    monkeypatch.setattr(provider, "run", lambda command, workspace, **_kwargs: _exec(exit_code=1, stderr="reject", command=command, working_dir=workspace))
    failed = provider.apply_patch("C:/repo", "diff --git a/a b/a")
    assert failed.success is False
    assert failed.rejected_hunks == ["reject"]


def test_host_bridge_commit_branch_and_checkout_report_results(monkeypatch):
    provider = _provider()
    calls = []

    def fake_run(command, workspace, **_kwargs):
        calls.append(command)
        if command.startswith("git commit"):
            return _exec(exit_code=1, stderr="nothing to commit", command=command, working_dir=workspace)
        if command.startswith("git checkout -b"):
            return _exec(command=command, working_dir=workspace)
        if command.startswith("git branch"):
            return _exec(exit_code=1, stderr="exists", command=command, working_dir=workspace)
        return _exec(command=command, working_dir=workspace)

    monkeypatch.setattr(provider, "run", fake_run)

    assert provider.commit("C:/repo", "message", files=["a.txt"]) == "Error: nothing to commit"
    assert provider.branch("C:/repo", "codex/test", checkout=True) is True
    assert provider.branch("C:/repo", "codex/test", checkout=False) is False
    assert provider.checkout("C:/repo", "main") is True
    assert "git add a.txt" in calls
    assert "git checkout -b codex/test" in calls
    assert "git branch codex/test" in calls
    assert "git checkout main" in calls


def test_host_bridge_request_logs_http_error_body_decode_failure(monkeypatch, caplog):
    provider = HostBridgeProvider(
        config=HostBridgeConfig(
            agent_url="http://host.docker.internal:9111",
            agent_token="real-secret-token",
        )
    )

    class BrokenHTTPError(urllib.error.HTTPError):
        def read(self):
            raise ValueError("boom")

    def fake_urlopen(*args, **kwargs):
        raise BrokenHTTPError(
            url="http://host.docker.internal:9111/health",
            code=502,
            msg="bad gateway",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with caplog.at_level(logging.WARNING):
        with pytest.raises(ConnectionError, match="Host agent returned 502"):
            provider._request("GET", "/health")

    assert "Host agent HTTP response error body could not be decoded" in caplog.text


def test_host_bridge_request_rejects_non_local_agent_url(monkeypatch):
    provider = HostBridgeProvider(
        config=HostBridgeConfig(
            agent_url="https://agent.example.com",
            agent_token="real-secret-token",
        )
    )

    def fake_urlopen(*args, **kwargs):
        raise AssertionError("urlopen should not be called for non-local host bridge URLs")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(ConnectionError, match="local-only URL policy"):
        provider._request("GET", "/health")


def test_host_bridge_request_wraps_url_error(monkeypatch):
    provider = _provider()

    def fake_urlopen(*_args, **_kwargs):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(ConnectionError, match="Cannot reach host agent"):
        provider._request("GET", "/health")


def test_host_bridge_run_blocks_denylisted_command_without_agent_call(monkeypatch):
    provider = _provider()
    monkeypatch.setattr(provider, "_request", lambda *_args, **_kwargs: pytest.fail("agent should not be called"))

    result = provider.run("rm -rf /", "C:/repo")

    assert result.exit_code == 126
    assert "blocked by security policy" in result.stderr


def test_host_bridge_run_posts_command_and_marks_truncated_output(monkeypatch):
    provider = _provider(max_stdout_chars=5, max_stderr_chars=4)
    calls = []

    def fake_request(method, path, body=None, timeout=None):
        calls.append((method, path, body, timeout))
        return {
            "exit_code": 7,
            "stdout": "abcdefgh",
            "stderr": "wxyz123",
            "duration_ms": 33,
            "timed_out": True,
        }

    monkeypatch.setattr(provider, "_request", fake_request)

    result = provider.run(["python", "-V"], "C:/repo", env={"A": "B"}, timeout_s=2)

    assert calls == [
        (
            "POST",
            "/execute",
            {"command": "python -V", "cwd": "C:/repo", "env": {"A": "B"}, "timeout": 2},
            12,
        )
    ]
    assert result.exit_code == 7
    assert result.stdout.startswith("abcde")
    assert result.stderr.startswith("wxyz")
    assert result.truncated is True
    assert result.timed_out is True


def test_host_bridge_run_returns_connection_and_unexpected_errors(monkeypatch):
    provider = _provider()

    monkeypatch.setattr(provider, "_request", lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError("agent down")))
    connection = provider.run("echo hi", "C:/repo")

    monkeypatch.setattr(provider, "_request", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad payload")))
    unexpected = provider.run("echo hi", "C:/repo")

    assert connection.exit_code == 1
    assert "Host agent error: agent down" in connection.stderr
    assert unexpected.exit_code == 1
    assert "Host bridge error: bad payload" in unexpected.stderr


@pytest.mark.asyncio
async def test_host_agent_status_rejects_non_local_control_url(monkeypatch):
    monkeypatch.setenv("HOST_AGENT_TOKEN", "real-secret-token")
    monkeypatch.setattr(flags_api, "HOST_AGENT_URL", "https://agent.example.com")

    def fake_urlopen(*args, **kwargs):
        raise AssertionError("urlopen should not be called for non-local host agent URLs")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = await flags_api.get_host_agent_status()

    assert result["reachable"] is False
    assert result["auth_configured"] is True
    assert result["auth_state"] == "configured"


@pytest.mark.asyncio
async def test_host_agent_shutdown_rejects_non_local_control_url(monkeypatch):
    monkeypatch.setenv("HOST_AGENT_TOKEN", "real-secret-token")
    monkeypatch.setattr(flags_api, "HOST_AGENT_URL", "https://agent.example.com")

    def fake_urlopen(*args, **kwargs):
        raise AssertionError("urlopen should not be called for non-local host agent URLs")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    response = await flags_api.shutdown_host_agent()

    assert isinstance(response, JSONResponse)
    assert response.status_code == 502
    assert b"local-only URL policy" in response.body


def test_host_bridge_read_does_not_embed_raw_path_in_shell_command(monkeypatch):
    provider = HostBridgeProvider(
        config=HostBridgeConfig(
            agent_url="http://host.docker.internal:9111",
            agent_token="real-secret-token",
        )
    )
    commands = []

    def fake_run(command, workspace, **kwargs):
        commands.append(command)
        return ExecResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            duration_ms=1,
            command=command,
            working_dir=workspace,
        )

    monkeypatch.setattr(provider, "run", fake_run)

    raw_path = "C:/tmp/weird'path & calc.txt"
    assert provider.read(raw_path) == "ok"

    assert len(commands) == 1
    assert "base64.b64decode" in commands[0]
    assert raw_path not in commands[0]


def test_host_bridge_write_does_not_embed_raw_content_in_shell_command(monkeypatch):
    provider = HostBridgeProvider(
        config=HostBridgeConfig(
            agent_url="http://host.docker.internal:9111",
            agent_token="real-secret-token",
        )
    )
    commands = []

    def fake_run(command, workspace, **kwargs):
        commands.append(command)
        return ExecResult(
            exit_code=0,
            stdout="",
            stderr="",
            duration_ms=1,
            command=command,
            working_dir=workspace,
        )

    monkeypatch.setattr(provider, "run", fake_run)
    monkeypatch.setattr("os.path.exists", lambda _path: False)

    raw_path = "C:/tmp/weird'path.txt"
    raw_content = "line 1\n'; Remove-Item C:\\ #"

    result = provider.write(raw_path, raw_content)

    assert result.action == "created"
    assert len(commands) == 1
    assert "base64.b64decode" in commands[0]
    assert raw_path not in commands[0]
    assert raw_content not in commands[0]


def test_host_bridge_file_operations_return_success_and_errors(monkeypatch):
    provider = _provider()
    calls = []

    def fake_run(command, workspace, **_kwargs):
        calls.append((command, workspace))
        if "iterdir" in command:
            return _exec(stdout="a.txt\nb.txt\n", command=command, working_dir=workspace)
        if "rglob" in command:
            return _exec(stdout="dir/a.txt\n", command=command, working_dir=workspace)
        if "unlink" in command:
            return _exec(command=command, working_dir=workspace)
        return _exec(stdout="file contents", command=command, working_dir=workspace)

    monkeypatch.setattr(provider, "run", fake_run)
    monkeypatch.setattr("os.path.exists", lambda _path: True)

    assert provider.read("C:/repo/file.txt") == "file contents"
    assert provider.write("C:/repo/file.txt", "new").action == "modified"
    assert provider.list("C:/repo", recursive=False) == ["a.txt", "b.txt"]
    assert provider.list("C:/repo", recursive=True) == ["dir/a.txt"]
    assert provider.delete("C:/repo/file.txt").action == "deleted"

    monkeypatch.setattr(provider, "run", lambda command, workspace, **_kwargs: _exec(exit_code=1, stderr="denied", command=command, working_dir=workspace))

    assert provider.read("C:/repo/file.txt") == "Error: denied"
    assert provider.write("C:/repo/file.txt", "new").error_message == "denied"
    assert provider.list("C:/repo") == ["Error: denied"]
    assert provider.delete("C:/repo/file.txt").error_message == "denied"


def test_host_bridge_helper_methods():
    provider = _provider(max_stdout_chars=4)

    bounded, truncated = provider._bound_output("abcdef", 4)
    encoded = provider._encode_python_arg("hello")

    assert provider._is_denied_command("FORMAT C:") is True
    assert provider._is_denied_command("echo ok") is False
    assert bounded == "abcd\n... (truncated)"
    assert truncated is True
    assert encoded == "aGVsbG8="
