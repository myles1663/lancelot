from types import SimpleNamespace
from unittest.mock import MagicMock

from providers.base import GenerateResult
import orchestrator as orch_mod


def _make_orchestrator():
    orch = orch_mod.LancelotOrchestrator.__new__(orch_mod.LancelotOrchestrator)
    orch.provider = MagicMock()
    orch.model_router = MagicMock()
    orch.local_model = None
    orch.soul = None
    orch.context_env = SimpleNamespace(get_context_string=lambda: "CTX")
    orch._get_thinking_config = lambda: None
    orch._route_model = lambda prompt: "gpt-4o"
    orch._llm_call_with_retry = lambda fn: fn()
    return orch


def test_text_only_generate_redacts_frontier_prompt_via_model_router():
    orch = _make_orchestrator()
    orch.model_router.route.return_value = SimpleNamespace(executed=True, output="Contact [EMAIL]")
    orch.provider.build_user_message.side_effect = lambda text, images=None: {"role": "user", "content": text}
    orch.provider.generate.return_value = GenerateResult(text="ok")

    result = orch._text_only_generate(
        "Contact me at alice@example.com",
        system_instruction="test",
        context_str="CTX",
    )

    assert result == "ok"
    orch.model_router.route.assert_called()
    call = orch.provider.generate.call_args
    assert call.kwargs["messages"][0]["content"] == "Contact [EMAIL]"


def test_frontier_tool_results_are_redacted_before_provider_message_build():
    orch = _make_orchestrator()
    orch.model_router.route.return_value = SimpleNamespace(executed=True, output='{"email":"[EMAIL]"}')
    orch.provider.build_tool_response_message.side_effect = lambda tool_results: tool_results

    tool_msg = orch._build_frontier_tool_response_message(
        [("call-1", "lookup_customer", '{"email":"alice@example.com"}')]
    )

    assert tool_msg == [("call-1", "lookup_customer", '{"email":"[EMAIL]"}')]
    orch.model_router.route.assert_called_once()


def test_redaction_falls_back_to_direct_local_model_when_router_missing():
    orch = _make_orchestrator()
    orch.model_router = None
    orch.local_model = MagicMock()
    orch.local_model.is_healthy.return_value = True
    orch.local_model.redact.return_value = "SSN [SSN]"

    redacted = orch._redact_for_frontier("SSN 123-45-6789")

    assert redacted == "SSN [SSN]"
    orch.local_model.redact.assert_called_once_with("SSN 123-45-6789")
