from types import SimpleNamespace

import pytest

import tool_loop


class CommandRuntime:
    def __init__(self):
        self.context_env = SimpleNamespace(
            list_workspace=lambda target: f"listed:{target}",
            read_file=lambda path: f"read:{path}",
            search_workspace=lambda query: f"search:{query}",
            get_file_outline=lambda path: f"outline:{path}",
            get_workspace_diff=lambda staged=False: f"diff:{staged}",
        )
        self.file_ops = SimpleNamespace(
            safe_copy=lambda src, dst, reason: f"copy:{src}->{dst}",
            safe_move=lambda src, dst, reason: f"move:{src}->{dst}",
            safe_delete=lambda path, reason: f"delete:{path}",
            safe_mkdir=lambda path, reason: f"mkdir:{path}",
            touch=lambda path, reason: f"touch:{path}",
        )
        self.sleep_calls = 0
        self.wake_calls = []
        self.sentry = None
        self.audit_logger = SimpleNamespace(log_command=lambda command: self.wake_calls.append(("audit", command)))
        self.network_interceptor = SimpleNamespace(check_url=lambda url: True)

    def enter_sleep(self):
        self.sleep_calls += 1

    def wake_up(self, reason):
        self.wake_calls.append(("wake", reason))


def test_execute_command_uses_safe_repl_file_workspace_operations():
    runtime = CommandRuntime()

    assert tool_loop._execute_command(runtime, ["ls"]) == "listed:."
    assert tool_loop._execute_command(runtime, ["dir", "src"]) == "listed:src"
    assert tool_loop._execute_command(runtime, ["cat"]) == "Usage: cat <file>"
    assert tool_loop._execute_command(runtime, ["cat", "README.md"]) == "read:README.md"
    assert tool_loop._execute_command(runtime, ["grep"]) == "Usage: grep <query>"
    assert tool_loop._execute_command(runtime, ["grep", "governance"]) == "search:governance"
    assert tool_loop._execute_command(runtime, ["outline"]) == "Usage: outline <file>"
    assert tool_loop._execute_command(runtime, ["outline", "src/app.py"]) == "outline:src/app.py"
    assert tool_loop._execute_command(runtime, ["diff", "--staged"]) == "diff:True"
    assert tool_loop._execute_command(runtime, ["cp"]) == "Usage: cp <src> <dst_folder>"
    assert tool_loop._execute_command(runtime, ["cp", "a", "b"]) == "copy:a->b"
    assert tool_loop._execute_command(runtime, ["mv"]) == "Usage: mv <src> <dst_folder>"
    assert tool_loop._execute_command(runtime, ["mv", "a", "b"]) == "move:a->b"
    assert tool_loop._execute_command(runtime, ["rm"]) == "Usage: rm <file>"
    assert tool_loop._execute_command(runtime, ["rm", "a"]) == "delete:a"
    assert tool_loop._execute_command(runtime, ["mkdir"]) == "Usage: mkdir <path>"
    assert tool_loop._execute_command(runtime, ["mkdir", "new"]) == "mkdir:new"
    assert tool_loop._execute_command(runtime, ["touch"]) == "Usage: touch <path>"
    assert tool_loop._execute_command(runtime, ["touch", "new.txt"]) == "touch:new.txt"
    assert tool_loop._execute_command(runtime, ["sleep"]) == "Entered SLEEP mode."
    assert runtime.sleep_calls == 1
    assert tool_loop._execute_command(runtime, ["wake"]) == "Entered ACTIVE mode."
    assert ("wake", "Manual CLI") in runtime.wake_calls


def test_execute_command_enforces_sentry_network_and_subprocess_paths(monkeypatch):
    runtime = CommandRuntime()
    runtime.sentry = SimpleNamespace(
        check_permission=lambda capability, payload: {
            "status": "PENDING",
            "message": "needs approval",
            "request_id": "req-1",
        }
    )
    assert "PERMISSION REQUIRED" in tool_loop._execute_command(runtime, ["python", "--version"])

    runtime.sentry = SimpleNamespace(
        check_permission=lambda capability, payload: {"status": "DENIED", "message": "blocked"}
    )
    assert tool_loop._execute_command(runtime, ["python", "--version"]) == "ACCESS DENIED: blocked"

    sentry_log = []
    runtime.sentry = SimpleNamespace(
        check_permission=lambda capability, payload: {"status": "APPROVED"},
        log_execution=lambda capability, payload, output: sentry_log.append((capability, payload, output)),
    )
    runtime.network_interceptor = SimpleNamespace(check_url=lambda url: False)
    assert (
        tool_loop._execute_command(runtime, ["curl", "https://blocked.example"])
        == "SECURITY BLOCK: Connection to https://blocked.example denied."
    )

    runtime.network_interceptor = SimpleNamespace(check_url=lambda url: True)
    monkeypatch.setattr(
        tool_loop.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="ok\n"),
    )
    assert tool_loop._execute_command(runtime, ["echo", "ok"]) == "ok"
    assert sentry_log[-1][0] == "cli_shell"

    def raise_called_process(*args, **kwargs):
        raise tool_loop.subprocess.CalledProcessError(2, args[0], stderr="bad command")

    monkeypatch.setattr(tool_loop.subprocess, "run", raise_called_process)
    assert tool_loop._execute_command(runtime, ["bad"]) == "Error executing command: bad command"

    monkeypatch.setattr(
        tool_loop.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("spawn failed")),
    )
    assert tool_loop._execute_command(runtime, ["bad"]) == "Error executing command: spawn failed"


def _graph():
    return SimpleNamespace(
        goal="ship the feature",
        steps=[
            SimpleNamespace(type="inspect", inputs={"description": "inspect repository"}),
            SimpleNamespace(type="edit", inputs={}),
        ],
    )


def test_execute_with_llm_uses_agentic_loop_and_cleans_output(monkeypatch):
    calls = []
    runtime = SimpleNamespace(
        provider=object(),
        context_env=SimpleNamespace(get_history_string=lambda limit=12: "user correction"),
        _build_execution_instruction=lambda: "execution system",
        _agentic_generate=lambda **kwargs: calls.append(kwargs) or "tool scaffolding result",
    )
    monkeypatch.setattr(tool_loop._ff, "FEATURE_AGENTIC_LOOP", True, raising=False)
    monkeypatch.setattr(
        "response.policies.OutputPolicy.strip_tool_scaffolding",
        lambda text: text.replace("tool scaffolding ", ""),
    )

    result = tool_loop._execute_with_llm(runtime, _graph(), user_text="do work")

    assert result == "result"
    assert calls[0]["allow_writes"] is True
    assert calls[0]["force_tool_use"] is True
    assert calls[0]["skip_structured_reformat"] is True
    assert "user correction" in calls[0]["prompt"]


def test_execute_with_llm_handles_no_provider_direct_provider_and_failures(monkeypatch):
    no_provider = SimpleNamespace(provider=None)
    assert tool_loop._execute_with_llm(no_provider, _graph()) == ""

    direct_calls = []
    direct = SimpleNamespace(
        provider=object(),
        context_env=SimpleNamespace(
            get_history_string=lambda limit=12: "",
            get_context_string=lambda: "context",
        ),
        _build_execution_instruction=lambda: "execution system",
        _build_frontier_user_message=lambda text: {"role": "user", "content": text},
        _llm_call_with_retry=lambda fn: fn(),
        _provider_generate=lambda **kwargs: direct_calls.append(kwargs) or SimpleNamespace(text="raw direct"),
        _route_model=lambda goal: "model-for-goal",
        _get_thinking_config=lambda: {"budget": 0},
    )
    monkeypatch.setattr(tool_loop._ff, "FEATURE_AGENTIC_LOOP", False, raising=False)
    monkeypatch.setattr("response.policies.OutputPolicy.strip_tool_scaffolding", lambda text: text)

    assert tool_loop._execute_with_llm(direct, _graph()) == "raw direct"
    assert direct_calls[0]["model"] == "model-for-goal"

    failing = SimpleNamespace(
        provider=object(),
        context_env=SimpleNamespace(get_history_string=lambda limit=12: ""),
        _build_execution_instruction=lambda: "execution system",
        _agentic_generate=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("model down")),
    )
    monkeypatch.setattr(tool_loop._ff, "FEATURE_AGENTIC_LOOP", True, raising=False)
    assert tool_loop._execute_with_llm(failing, _graph()) == ""
