import logging
import urllib.error

import pytest
from fastapi.responses import JSONResponse

import host_agent.agent as host_agent_module
from src.core import flags_api
from src.tools.contracts import ExecResult, ProviderState
from src.tools.providers.host_bridge import HostBridgeConfig, HostBridgeProvider


def test_host_bridge_config_rejects_legacy_default_token(monkeypatch):
    monkeypatch.setenv("HOST_AGENT_TOKEN", "lancelot-host-agent")

    config = HostBridgeConfig(agent_url="http://host.docker.internal:9111")

    assert config.agent_token == ""


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
