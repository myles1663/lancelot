from __future__ import annotations

from types import SimpleNamespace

from tool_loop_local import local_agentic_generate


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

    def _build_openai_tool_declarations(self):
        return []

    def _agentic_generate(self, **kwargs):
        self.fallback_calls.append(kwargs)
        return "flagship-fallback"

    def _format_tool_receipts(self, receipts, note="", error=None):
        return f"{note}:{len(receipts)}:{error or ''}"

    def _classify_tool_call_safety(self, *_):
        return "auto"

    def _record_governance_event(self, *args):
        self.governance_events.append(args)

    def _get_trust_summary(self, *_):
        return "trust summary"

    def _suggest_alternatives(self, *_):
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


def test_local_agentic_generate_rejects_incomplete_tool_input_before_safety_check():
    model = _SequencedLocalModel(
        _tool_response("repo_writer", '{"action":"edit"}', tokens=11),
        _text_response("could not write without a path", tokens=5),
    )
    runtime = _LocalRuntime(model)
    runtime.toolflow_emitter = _FakeToolflowEmitter()
    runtime._classify_tool_call_safety = lambda *_: (_ for _ in ()).throw(
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
