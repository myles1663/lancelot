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
    assert "HOST_AGENT_TOKEN is not configured." in health.degraded_reasons


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
