import sys
from types import SimpleNamespace

from src.core.providers.codex_responses_client import OpenAICodexResponsesProviderClient
from src.core.providers.tool_schema import NormalizedToolDeclaration


class _FakeResponses:
    def __init__(self, stream):
        self.stream = stream
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return list(self.stream)


class _FakeOpenAI:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.responses = _FakeResponses([])
        self.__class__.instances.append(self)


def _install_fake_openai(monkeypatch, stream):
    class _BoundFakeOpenAI(_FakeOpenAI):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.responses = _FakeResponses(stream)

    _BoundFakeOpenAI.instances = []
    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(OpenAI=_BoundFakeOpenAI),
    )
    return _BoundFakeOpenAI


def _event(event_type, **kwargs):
    return SimpleNamespace(type=event_type, **kwargs)


def test_codex_responses_generate_parses_streamed_text(monkeypatch):
    fake_openai = _install_fake_openai(monkeypatch, [
        _event("response.output_text.delta", delta="o"),
        _event("response.output_text.delta", delta="k"),
        _event(
            "response.completed",
            response=SimpleNamespace(usage=SimpleNamespace(input_tokens=3, output_tokens=2)),
        ),
    ])

    client = OpenAICodexResponsesProviderClient(auth_token="token")
    result = client.generate(
        "gpt-5.4-mini",
        [{"role": "user", "content": "Reply ok"}],
        "",
        config={"temperature": 0.1, "max_tokens": 12},
    )

    assert fake_openai.instances[-1].kwargs["base_url"] == OpenAICodexResponsesProviderClient.CODEX_BASE_URL
    assert result.text == "ok"
    assert result.usage == {"input_tokens": 3, "output_tokens": 2}
    call = fake_openai.instances[-1].responses.calls[-1]
    assert call["instructions"] == OpenAICodexResponsesProviderClient._DEFAULT_INSTRUCTIONS
    assert call["stream"] is True
    assert call["store"] is False
    assert "max_output_tokens" not in call
    assert call["temperature"] == 0.1
    assert client.validate_model("gpt-5.4-mini") is True
    assert client.validate_model("gpt-5.4-nano") is False


def test_codex_responses_generate_with_tools_parses_function_call(monkeypatch):
    item = SimpleNamespace(
        type="function_call",
        call_id="call-1",
        name="command_runner",
        arguments='{"command":"pwd"}',
        id="fc-1",
        status="completed",
    )
    fake_openai = _install_fake_openai(monkeypatch, [
        _event("response.output_item.done", item=item),
        _event("response.completed", response=SimpleNamespace(usage=None)),
    ])

    client = OpenAICodexResponsesProviderClient(auth_token="token")
    result = client.generate_with_tools(
        "gpt-5.4-mini",
        [{"role": "user", "content": "Inspect cwd"}],
        "Use tools.",
        [
            NormalizedToolDeclaration(
                name="command_runner",
                description="Run a governed command",
                parameters={
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            )
        ],
        tool_config={"mode": "ANY"},
    )

    assert result.text is None
    assert result.tool_calls[0].name == "command_runner"
    assert result.tool_calls[0].id == "call-1"
    assert result.tool_calls[0].args == {"command": "pwd"}
    assert result.raw == [{
        "type": "function_call",
        "call_id": "call-1",
        "name": "command_runner",
        "arguments": '{"command":"pwd"}',
        "id": "fc-1",
        "status": "completed",
    }]
    call = fake_openai.instances[-1].responses.calls[-1]
    assert call["tool_choice"] == "required"
    assert call["tools"][0]["type"] == "function"
    assert call["tools"][0]["name"] == "command_runner"


def test_codex_responses_tool_response_message_uses_responses_items(monkeypatch):
    _install_fake_openai(monkeypatch, [])
    client = OpenAICodexResponsesProviderClient(auth_token="token")

    assert client.build_tool_response_message([("call-1", "command_runner", "out")]) == [
        {"type": "function_call_output", "call_id": "call-1", "output": "out"}
    ]
