import pytest

from src.core.orch_helpers.intent_helpers import (
    extract_literal_terms,
    is_continuation,
    is_conversational,
    is_low_risk_exec,
    needs_research,
    wants_action,
)


def test_needs_research_for_live_health_status_prompt():
    assert needs_research("Run a health check and summarize the current system status.") is True


def test_needs_research_for_runtime_status_prompt():
    assert needs_research("What is the current runtime status of the service?") is True


@pytest.mark.parametrize(
    "prompt",
    [
        "hello",
        "thanks for that",
        "ok thanks",
        "my name is Arthur",
        "never mind",
    ],
)
def test_is_conversational_accepts_short_social_messages(prompt):
    assert is_conversational(prompt) is True


def test_is_conversational_rejects_actionable_confirmation():
    assert is_conversational("ok, create the release notes") is False
    assert is_conversational("please inspect the full repository and write a report") is False


@pytest.mark.parametrize(
    "message",
    [
        "go for it",
        "what about the other option?",
        "ok, use telegram",
        "change it to the public repo",
        "retry",
    ],
)
def test_is_continuation_detects_followups(message):
    assert is_continuation(message) is True


def test_is_continuation_rejects_long_standalone_request():
    long_message = "write " + ("a " * 100) + "detailed implementation plan"
    assert is_continuation(long_message) is False


@pytest.mark.parametrize(
    "prompt",
    [
        "compare current pricing for local model hosting",
        "can you find a way to connect slack",
        "ask claude to summarize this",
        "what about Linear?",
    ],
)
def test_needs_research_for_exploratory_or_delegated_prompts(prompt):
    assert needs_research(prompt) is True


def test_wants_action_detects_write_and_notification_requests():
    assert wants_action("configure the scheduler and send me a message") is True
    assert wants_action("explain what the scheduler does") is False


def test_is_low_risk_exec_distinguishes_readonly_from_side_effects():
    assert is_low_risk_exec("review the README and summarize it") is True
    assert is_low_risk_exec("delete the old README") is False
    assert is_low_risk_exec("tell me how it works") is True


def test_extract_literal_terms_preserves_quoted_and_capitalized_terms():
    terms = extract_literal_terms('Compare "Clawd Bot" with New York Times and Show Me the result')

    assert terms == ["Clawd Bot", "New York Times"]
