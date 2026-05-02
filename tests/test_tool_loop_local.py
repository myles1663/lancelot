from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import tool_loop_local
from tool_loop_local import local_agentic_generate
from src.core.skills.executor import SkillResult


class _FakeGovernor:
    def __init__(self):
        self.logged = []

    def log_usage(self, *args):
        self.logged.append(args)


class _FakeReceiptService:
    def __init__(self):
        self.created = []

    def create(self, receipt):
        self.created.append(receipt)


class _FakeToolflowEmitter:
    def __init__(self):
        self.completed = []

    def tool_call_completed(self, *args):
        self.completed.append(args)


class _SequencedLocalModel:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def is_healthy(self):
        return True

    def chat_with_tools(self, **kwargs):
        self.requests.append(kwargs)
        return self.responses.pop(0)


class _LocalRuntime:
    def __init__(self, local_model=None):
        self.local_model = local_model
        self.context_env = SimpleNamespace(get_context_string=lambda: "context")
        self.governor = _FakeGovernor()
        self.usage_tracker = None
        self.toolflow_emitter = None
        self.receipt_service = _FakeReceiptService()
        self.skill_executor = SimpleNamespace(
            run=lambda *_: (_ for _ in ()).throw(AssertionError("skill should not run"))
        )
        self.actioncard_factory = None
        self.sentry = None
        self.fallback_calls = []
        self.governance_events = []
        self.progress_events = []

    def build_openai_tool_declarations(self):
        return []

    def agentic_generate(self, **kwargs):
        self.fallback_calls.append(kwargs)
        return "flagship-fallback"

    def format_tool_receipts(self, receipts, note="", error=None):
        return f"{note}:{len(receipts)}:{error or ''}"

    def set_last_tool_receipts(self, receipts):
        self._last_tool_receipts = receipts

    def classify_tool_call_safety(self, *_):
        return "auto"

    def record_governance_event(self, *args):
        self.governance_events.append(args)

    def emit_chat_progress(self, phase, message, **metadata):
        self.progress_events.append({"phase": phase, "message": message, **metadata})

    def get_trust_summary(self, *_):
        return "trust summary"

    def suggest_alternatives(self, *_):
        return ["use a narrower tool target"]


def _text_response(content: str, tokens: int = 7):
    return {
        "choices": [
            {
                "message": {"content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"total_tokens": tokens},
    }


def _tool_response(name: str, arguments: str, tokens: int = 11):
    return {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "function": {
                                "name": name,
                                "arguments": arguments,
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"total_tokens": tokens},
    }


def _tool_response_with_id(name: str, arguments: str, call_id: str, tokens: int = 11):
    response = _tool_response(name, arguments, tokens=tokens)
    response["choices"][0]["message"]["tool_calls"][0]["id"] = call_id
    return response


def test_local_agentic_generate_falls_back_without_local_model():
    runtime = _LocalRuntime()

    assert local_agentic_generate(runtime, "hello") == "flagship-fallback"
    assert runtime.fallback_calls[0]["prompt"] == "hello"


def test_local_agentic_generate_returns_text_and_tracks_usage():
    model = _SequencedLocalModel(_text_response("local answer", tokens=17))
    runtime = _LocalRuntime(model)
    usage = []
    runtime.usage_tracker = SimpleNamespace(record_simple=lambda *args: usage.append(args))

    assert local_agentic_generate(runtime, "hello") == "local answer"
    assert "emoji sparingly" in model.requests[0]["messages"][0]["content"]
    assert ("tokens", 17) in runtime.governor.logged
    assert usage == [("local-llm", 17)]
    assert runtime.progress_events == [
        {
            "phase": "local_model_call",
            "message": "Waiting for local utility model response (timeout: 60s)",
            "model": "local-llm",
            "wait_reason": "local_model_call",
            "iteration": 1,
            "timeout_s": 60.0,
        }
    ]


def test_local_agentic_generate_rejects_incomplete_tool_input_before_safety_check():
    model = _SequencedLocalModel(
        _tool_response("repo_writer", '{"action":"edit"}', tokens=11),
        _text_response("could not write without a path", tokens=5),
    )
    runtime = _LocalRuntime(model)
    runtime.toolflow_emitter = _FakeToolflowEmitter()
    runtime.classify_tool_call_safety = lambda *_: (_ for _ in ()).throw(
        AssertionError("invalid tool input should not reach safety classification")
    )

    response = local_agentic_generate(runtime, "write file")

    assert response == "could not write without a path"
    assert runtime._last_tool_receipts[0] == {
        "skill": "repo_writer",
        "inputs": {"action": "edit"},
        "result": "REJECTED - repo_writer missing required input(s): path.",
    }
    assert runtime.receipt_service.created[0].status == "failure"
    assert runtime.toolflow_emitter.completed == [
        (
            "",
            1,
            "repo_writer",
            "REJECTED",
            "repo_writer missing required input(s): path.",
            "api",
        )
    ]


def test_initial_messages_truncate_long_context_from_tail():
    runtime = _LocalRuntime()
    long_context = "HEAD" + "A" * (tool_loop_local.LOCAL_CONTEXT_BUDGET + 10) + "TAIL"

    messages = tool_loop_local._initial_local_messages(runtime, "summarize", long_context)

    assert "HEAD" not in messages[1]["content"]
    assert "TAIL" in messages[1]["content"]
    assert "LATEST USER REQUEST:\nsummarize" in messages[1]["content"]


def test_check_local_sentry_handles_approved_pending_and_failures(monkeypatch):
    class FakeMCPSentry:
        def __init__(self, status):
            self.status = status

        def check_permission(self, skill_name, inputs):
            if self.status == "RAISE":
                raise RuntimeError("sentry unavailable")
            return {"status": self.status, "request_id": f"req-{self.status.lower()}"}

    monkeypatch.setitem(sys.modules, "mcp_sentry", SimpleNamespace(MCPSentry=FakeMCPSentry))

    runtime = _LocalRuntime()
    runtime.classify_tool_call_safety = lambda *_: "escalate"

    runtime.sentry = FakeMCPSentry("APPROVED")
    assert tool_loop_local._check_local_sentry(runtime, "command_runner", {}, False) == (
        "auto",
        "req-approved",
        False,
    )

    runtime.sentry = FakeMCPSentry("PENDING")
    assert tool_loop_local._check_local_sentry(runtime, "command_runner", {}, False) == (
        "escalate",
        "req-pending",
        True,
    )

    runtime.sentry = FakeMCPSentry("RAISE")
    assert tool_loop_local._check_local_sentry(runtime, "command_runner", {}, False) == (
        "escalate",
        None,
        False,
    )


def test_local_agentic_generate_falls_back_when_local_model_unhealthy():
    model = _SequencedLocalModel()
    model.is_healthy = lambda: False
    runtime = _LocalRuntime(model)

    assert local_agentic_generate(runtime, "hello") == "flagship-fallback"
    assert runtime.fallback_calls[0]["prompt"] == "hello"


def test_local_agentic_generate_uses_local_role_model_and_timeout():
    model = _SequencedLocalModel(_text_response("role answer", tokens=3))
    runtime = _LocalRuntime()
    runtime.local_model_roles = SimpleNamespace(
        config_for=lambda role: SimpleNamespace(model="utility-small", timeout_s=0.25),
        client_for=lambda role: model,
    )

    assert local_agentic_generate(runtime, "hello") == "role answer"
    assert model.requests[0]["timeout"] == 1.0
    assert runtime.progress_events[0]["model"] == "utility-small"


def test_local_agentic_generate_falls_back_when_role_lookup_fails():
    model = _SequencedLocalModel(_text_response("default model answer"))
    runtime = _LocalRuntime(model)
    runtime.local_model_roles = SimpleNamespace(
        config_for=lambda role: (_ for _ in ()).throw(RuntimeError("role table missing"))
    )

    assert local_agentic_generate(runtime, "hello") == "default model answer"


def test_local_agentic_generate_handles_model_error_before_and_after_tool_receipts():
    failing_model = _SequencedLocalModel()
    failing_model.chat_with_tools = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("model failed"))
    runtime = _LocalRuntime(failing_model)

    assert local_agentic_generate(runtime, "hello") == "flagship-fallback"

    model = _SequencedLocalModel(_tool_response("echo", '{"message":"first"}'))
    model.chat_with_tools = lambda **kwargs: (
        model.requests.append(kwargs)
        or (
            _tool_response("echo", '{"message":"first"}')
            if len(model.requests) == 1
            else (_ for _ in ()).throw(RuntimeError("second pass failed"))
        )
    )
    runtime = _LocalRuntime(model)
    runtime.skill_executor = SimpleNamespace(
        run=lambda name, inputs: SkillResult(success=True, outputs={"ok": inputs["message"]})
    )

    response = local_agentic_generate(runtime, "use echo")

    assert response.startswith("Stopped after local planner error: second pass failed. Results so far:")
    assert ":1:" in response


def test_local_agentic_generate_handles_empty_choices_and_blank_text():
    runtime = _LocalRuntime(_SequencedLocalModel({"choices": [], "usage": {"total_tokens": 1}}))
    assert local_agentic_generate(runtime, "hello") == "Error: Local model returned no response."

    runtime = _LocalRuntime(_SequencedLocalModel(_text_response("")))
    assert local_agentic_generate(runtime, "hello") == "No response from local model."


def test_local_agentic_generate_rejects_unintended_network_tool_call():
    model = _SequencedLocalModel(
        _tool_response("network_client", '{"url":"https://example.com","method":"GET"}'),
        _text_response("used local evidence instead"),
    )
    runtime = _LocalRuntime(model)
    runtime.toolflow_emitter = _FakeToolflowEmitter()

    response = local_agentic_generate(runtime, "check runtime health locally")

    assert response == "used local evidence instead"
    receipt = runtime._last_tool_receipts[0]
    assert receipt["skill"] == "network_client"
    assert receipt["result"].startswith("REJECTED - network_client requires explicit operator intent")
    assert runtime.receipt_service.created[0].status == "failure"
    assert runtime.toolflow_emitter.completed[0][3] == "REJECTED"


def test_local_agentic_generate_suppresses_duplicate_successful_tool_call():
    model = _SequencedLocalModel(
        _tool_response_with_id("echo", '{"message":"same"}', "call-1"),
        _tool_response_with_id("echo", '{"message":"same"}', "call-2"),
        _text_response("done"),
    )
    runtime = _LocalRuntime(model)
    calls = []
    runtime.skill_executor = SimpleNamespace(
        run=lambda name, inputs: calls.append((name, inputs))
        or SkillResult(success=True, outputs={"echo": inputs})
    )

    assert local_agentic_generate(runtime, "repeat same echo") == "done"
    assert calls == [("echo", {"message": "same"})]
    assert "already_completed" in model.requests[2]["messages"][-1]["content"]


def test_local_agentic_generate_returns_approval_when_sentry_blocks(monkeypatch):
    monkeypatch.setattr(tool_loop_local, "_create_approval_card", lambda *args: (object(), "approval-1", 1))
    runtime = _LocalRuntime(_SequencedLocalModel(_tool_response("echo", '{"message":"blocked"}')))
    runtime.classify_tool_call_safety = lambda *_: "escalate"

    response = local_agentic_generate(runtime, "echo this", allow_writes=False)

    assert "approval" in response.lower()
    assert runtime._last_tool_receipts[0]["result"] == "ESCALATED - needs Commander approval"
    assert runtime.receipt_service.created[0].status == "pending"


def test_execute_local_tool_records_success_failure_and_exception():
    runtime = _LocalRuntime()
    receipts = []
    events = []
    runtime.record_governance_event = lambda *args, **kwargs: events.append((args, kwargs))
    runtime.skill_executor = SimpleNamespace(
        run=lambda name, inputs: SkillResult(success=True, outputs={"value": 42})
    )

    success = tool_loop_local._execute_local_tool(
        runtime,
        tool_receipts=receipts,
        skill_name="echo",
        inputs={"message": "ok"},
        iteration=0,
    )

    assert "value" in success
    assert events[-1][0][3] is True

    runtime.skill_executor = SimpleNamespace(
        run=lambda name, inputs: SkillResult(success=False, error="skill refused")
    )
    failure = tool_loop_local._execute_local_tool(
        runtime,
        tool_receipts=receipts,
        skill_name="echo",
        inputs={},
        iteration=1,
    )
    assert "skill refused" in failure
    assert events[-1][0][3] is False

    runtime.skill_executor = SimpleNamespace(
        run=lambda name, inputs: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    error = tool_loop_local._execute_local_tool(
        runtime,
        tool_receipts=receipts,
        skill_name="echo",
        inputs={},
        iteration=2,
    )
    assert "boom" in error
    assert events[-1][0][3] is False


def test_local_agentic_generate_formats_receipts_at_iteration_limit(monkeypatch):
    monkeypatch.setattr(tool_loop_local, "MAX_LOCAL_ITERATIONS", 2)
    model = _SequencedLocalModel(
        _tool_response("echo", '{"message":"one"}'),
        _tool_response("echo", '{"message":"two"}'),
    )
    runtime = _LocalRuntime(model)
    runtime.skill_executor = SimpleNamespace(
        run=lambda name, inputs: SkillResult(success=True, outputs={"echo": inputs})
    )

    response = local_agentic_generate(runtime, "keep calling tools")

    assert response.startswith("Reached maximum local tool call limit. Here's what I found:")
    assert ":2:" in response
