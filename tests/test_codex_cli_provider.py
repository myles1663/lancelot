import json
import subprocess
from pathlib import Path

import pytest

from src.core.providers.codex_cli_client import (
    CodexCLIProviderClient,
    has_codex_cli_auth,
    resolve_codex_auth_file,
)
from src.core.providers.tool_schema import NormalizedToolDeclaration


def test_generate_uses_codex_exec(monkeypatch, tmp_path):
    home = tmp_path / "home"
    auth_dir = home / ".codex"
    auth_dir.mkdir(parents=True)
    (auth_dir / "auth.json").write_text("{}", encoding="utf-8")

    captured = {}

    def fake_run(cmd, text, capture_output, stdin, env, timeout, check):
        output_path = Path(cmd[cmd.index("--output-last-message") + 1])
        output_path.write_text("CLI_OK", encoding="utf-8")
        captured["cmd"] = cmd
        captured["env"] = env
        captured["stdin"] = stdin
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("src.core.providers.codex_cli_client.shutil.which", lambda _: "/usr/bin/codex")
    monkeypatch.setattr("src.core.providers.codex_cli_client.subprocess.run", fake_run)

    client = CodexCLIProviderClient(workdir="/workspace", codex_home=str(home))
    result = client.generate(
        model="gpt-5.4",
        messages=[{"role": "user", "content": "ping"}],
        system_instruction="system prompt",
    )

    assert result.text == "CLI_OK"
    assert captured["cmd"][:6] == [
        "codex",
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
    ]
    assert "-m" in captured["cmd"]
    assert captured["env"]["HOME"] == str(home)
    assert captured["stdin"] is subprocess.DEVNULL
    assert "SYSTEM:\nsystem prompt" in captured["cmd"][-1]
    assert "USER:\nping" in captured["cmd"][-1]


def test_generate_with_tools_returns_tool_calls(monkeypatch, tmp_path):
    home = tmp_path / "home"
    auth_dir = home / ".codex"
    auth_dir.mkdir(parents=True)
    (auth_dir / "auth.json").write_text("{}", encoding="utf-8")

    captured = {}

    def fake_run(cmd, text, capture_output, stdin, env, timeout, check):
        output_path = Path(cmd[cmd.index("--output-last-message") + 1])
        output_path.write_text(
            json.dumps(
                {
                    "action": "tool_calls",
                    "tool_calls": [
                        {"name": "health_check", "args_json": "{}"},
                        {"name": "warroom_send", "args_json": "{\"message\":\"done\"}"},
                    ],
                    "final_text": "",
                }
            ),
            encoding="utf-8",
        )
        captured["cmd"] = cmd
        captured["stdin"] = stdin
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("src.core.providers.codex_cli_client.shutil.which", lambda _: "/usr/bin/codex")
    monkeypatch.setattr("src.core.providers.codex_cli_client.subprocess.run", fake_run)

    client = CodexCLIProviderClient(workdir="/workspace", codex_home=str(home))
    result = client.generate_with_tools(
        model="gpt-5.4",
        messages=[{"role": "user", "content": "Run a health check"}],
        system_instruction="Follow governance.",
        tools=[
            NormalizedToolDeclaration(
                name="health_check",
                description="Check system health",
                parameters={"type": "object", "properties": {}, "required": []},
            ),
            NormalizedToolDeclaration(
                name="warroom_send",
                description="Send a war room notification",
                parameters={
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                },
            ),
        ],
        tool_config={"mode": "ANY"},
    )

    assert [call.name for call in result.tool_calls] == ["health_check", "warroom_send"]
    assert result.tool_calls[1].args == {"message": "done"}
    assert result.text is None
    assert "--output-schema" in captured["cmd"]
    assert captured["stdin"] is subprocess.DEVNULL
    assert "must request at least one declared tool call" in captured["cmd"][-1]
    assert "current system health" in captured["cmd"][-1]
    assert "DECLARED LANCELOT TOOLS:" in captured["cmd"][-1]
    assert "TOOL[health_check]" not in captured["cmd"][-1]


def test_generate_with_tools_returns_final_text(monkeypatch, tmp_path):
    home = tmp_path / "home"
    auth_dir = home / ".codex"
    auth_dir.mkdir(parents=True)
    (auth_dir / "auth.json").write_text("{}", encoding="utf-8")

    def fake_run(cmd, text, capture_output, stdin, env, timeout, check):
        output_path = Path(cmd[cmd.index("--output-last-message") + 1])
        output_path.write_text(
            json.dumps(
                {
                    "action": "final",
                    "tool_calls": [],
                    "final_text": "Health is green.",
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("src.core.providers.codex_cli_client.shutil.which", lambda _: "/usr/bin/codex")
    monkeypatch.setattr("src.core.providers.codex_cli_client.subprocess.run", fake_run)

    client = CodexCLIProviderClient(workdir="/workspace", codex_home=str(home))
    result = client.generate_with_tools(
        model="gpt-5.4-mini",
        messages=[
            {"role": "user", "content": "Summarize the current status."},
            {"role": "tool", "name": "health_check", "content": "{'status': 'ok'}"},
        ],
        system_instruction="Follow governance.",
        tools=[
            NormalizedToolDeclaration(
                name="health_check",
                description="Check system health",
                parameters={"type": "object", "properties": {}, "required": []},
            ),
        ],
    )

    assert result.text == "Health is green."
    assert result.tool_calls == []


def test_missing_auth_file_raises(monkeypatch, tmp_path):
    monkeypatch.setattr("src.core.providers.codex_cli_client.shutil.which", lambda _: "/usr/bin/codex")
    monkeypatch.delenv("LANCELOT_CODEX_HOME", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(
        "src.core.providers.codex_cli_client.os.path.expanduser",
        lambda _value: str(tmp_path / "missing-home"),
    )

    with pytest.raises(Exception, match="auth is not available"):
        CodexCLIProviderClient(workdir="/workspace", codex_home=str(tmp_path / "home"))


def test_resolve_auth_file_uses_explicit_codex_home_env(monkeypatch, tmp_path):
    mounted_home = tmp_path / "mounted-home"
    auth_dir = mounted_home / ".codex"
    auth_dir.mkdir(parents=True)
    auth_file = auth_dir / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("LANCELOT_CODEX_HOME", str(mounted_home))
    monkeypatch.setattr("src.core.providers.codex_cli_client.os.path.expanduser", lambda _value: "/root")

    assert resolve_codex_auth_file() == auth_file
    assert has_codex_cli_auth() is True


def test_any_mode_schema_requires_tool_calls():
    schema = CodexCLIProviderClient._build_tool_decision_schema(
        [{"name": "health_check", "description": "Check system health", "parameters": {"type": "object"}}],
        mode="ANY",
    )

    assert schema["properties"]["action"]["enum"] == ["tool_calls"]
    assert schema["properties"]["tool_calls"]["minItems"] == 1
