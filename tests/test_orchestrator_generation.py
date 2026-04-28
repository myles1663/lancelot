from types import SimpleNamespace

import pytest

import orchestrator_generation
from orchestrator_generation import (
    build_reasoning_instruction,
    get_thinking_config,
    is_retryable_error,
    llm_call_with_retry,
    should_use_deep_reasoning,
    text_only_generate,
)


def test_is_retryable_error_identifies_provider_transients():
    assert is_retryable_error(TimeoutError("provider timeout")) is True
    assert is_retryable_error(RuntimeError("HTTP 503 service_unavailable")) is True
    assert is_retryable_error(ValueError("bad prompt schema")) is False


def test_llm_call_with_retry_uses_exponential_backoff(monkeypatch):
    waits = []
    attempts = {"count": 0}
    runtime = SimpleNamespace(
        _stop_event=None,
        is_retryable_error=lambda exc: is_retryable_error(exc),
    )
    runtime.provider_stop_event = lambda: runtime._stop_event
    monkeypatch.setattr(
        orchestrator_generation,
        "wait_before_provider_retry",
        lambda delay, *_args, **_kwargs: waits.append(delay),
    )

    def flaky_call():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise TimeoutError("provider timeout")
        return "ok"

    assert llm_call_with_retry(runtime, flaky_call, max_retries=3, base_delay=0.25) == "ok"
    assert attempts["count"] == 3
    assert waits == [0.25, 0.5]


def test_llm_call_with_retry_does_not_retry_non_transient_errors(monkeypatch):
    waits = []
    runtime = SimpleNamespace(
        _stop_event=None,
        is_retryable_error=lambda exc: is_retryable_error(exc),
    )
    runtime.provider_stop_event = lambda: runtime._stop_event
    monkeypatch.setattr(
        orchestrator_generation,
        "wait_before_provider_retry",
        lambda delay, *_args, **_kwargs: waits.append(delay),
    )

    with pytest.raises(ValueError, match="bad request"):
        llm_call_with_retry(runtime, lambda: (_ for _ in ()).throw(ValueError("bad request")))

    assert waits == []


def test_text_only_generate_builds_frontier_safe_provider_call():
    provider_calls = []
    runtime = SimpleNamespace(
        provider=object(),
        context_env=SimpleNamespace(get_context_string=lambda: "CTX"),
        build_system_instruction=lambda: "system",
        build_frontier_user_message=lambda text, images=None: {"role": "user", "content": text, "images": images},
        route_model=lambda prompt: "gpt-test",
        get_thinking_config=lambda: {"thinking_level": "low"},
        llm_call_with_retry=lambda fn: fn(),
    )

    def provider_generate(**kwargs):
        provider_calls.append(kwargs)
        return SimpleNamespace(text="ok")

    runtime.provider_generate = provider_generate

    result = text_only_generate(runtime, "answer this", image_parts=["img"])

    assert result == "ok"
    assert provider_calls == [
        {
            "model": "gpt-test",
            "messages": [{"role": "user", "content": "CTX\n\nanswer this", "images": ["img"]}],
            "system_instruction": "system",
            "config": {"thinking": {"thinking_level": "low"}},
        }
    ]


def test_get_thinking_config_reads_environment(monkeypatch):
    monkeypatch.setenv("GEMINI_THINKING_LEVEL", "high")
    assert get_thinking_config() == {"thinking_level": "high"}

    monkeypatch.setenv("GEMINI_THINKING_LEVEL", "off")
    assert get_thinking_config() is None


def test_should_use_deep_reasoning_routes_complex_requests():
    runtime = SimpleNamespace(
        is_continuation=lambda _message: False,
        needs_research=lambda _message: False,
    )

    assert should_use_deep_reasoning(runtime, "ok") is False
    assert should_use_deep_reasoning(runtime, "Please compare the options for our deployment strategy") is True
    assert should_use_deep_reasoning(runtime, "What should we do about the release merge plan?") is True


def test_should_use_deep_reasoning_skips_continuations():
    runtime = SimpleNamespace(
        is_continuation=lambda _message: True,
        needs_research=lambda _message: True,
    )

    assert should_use_deep_reasoning(runtime, "Continue with the plan we already discussed in detail") is False


def test_build_reasoning_instruction_includes_context_and_capability_gap_contract():
    runtime = SimpleNamespace(
        soul=SimpleNamespace(mission="ship governed software", allegiance="Commander"),
        context_env=SimpleNamespace(get_context_string=lambda: "current context"),
    )

    instruction = build_reasoning_instruction(runtime)

    assert "Mission: ship governed software" in instruction
    assert "AVAILABLE TOOLS" in instruction
    assert "CURRENT CONTEXT:\ncurrent context" in instruction
    assert "CAPABILITY GAP: <description>" in instruction
    assert "Do NOT call tools or take actions" in instruction
