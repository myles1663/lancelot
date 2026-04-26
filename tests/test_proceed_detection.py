"""
Tests for expanded proceed detection and stall phrase blocking.

Validates:
- "set it up" treated as proceed when a plan exists
- "set it up" NOT treated as proceed when no plan exists
- Strong signals always treated as proceed
- "awaiting further instructions" caught as forbidden stall phrase
"""

import pytest
import sys
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))


# ---------------------------------------------------------------------------
# Proceed Detection
# ---------------------------------------------------------------------------

def _is_proceed_message_standalone(message: str, has_plan: bool) -> bool:
    """Exercise the production proceed detector without importing Orchestrator."""
    from orchestrator_approval import is_proceed_message

    runtime = SimpleNamespace(
        _last_plan_artifact=object() if has_plan else None,
        task_store=None,
    )
    return is_proceed_message(runtime, message)


class TestProceedDetection:
    """Tests for _is_proceed_message() with two-tier logic."""

    @pytest.fixture
    def orch_with_plan(self):
        """Simulate an orchestrator that has a pending plan artifact."""
        orch = MagicMock()
        orch._is_proceed_message = lambda msg: _is_proceed_message_standalone(msg, has_plan=True)
        return orch

    @pytest.fixture
    def orch_without_plan(self):
        """Simulate an orchestrator with no pending plan artifact."""
        orch = MagicMock()
        orch._is_proceed_message = lambda msg: _is_proceed_message_standalone(msg, has_plan=False)
        return orch

    # ── Strong signals: always proceed regardless of plan state ──

    def test_proceed_always(self, orch_without_plan):
        assert orch_without_plan._is_proceed_message("proceed") is True

    def test_go_ahead_always(self, orch_without_plan):
        assert orch_without_plan._is_proceed_message("go ahead") is True

    def test_approved_always(self, orch_without_plan):
        assert orch_without_plan._is_proceed_message("approved") is True

    def test_execute_always(self, orch_without_plan):
        assert orch_without_plan._is_proceed_message("execute") is True

    def test_confirm_always(self, orch_without_plan):
        assert orch_without_plan._is_proceed_message("confirm") is True

    def test_run_it_always(self, orch_without_plan):
        assert orch_without_plan._is_proceed_message("run it") is True

    # ── Contextual signals: proceed only WITH plan ──

    def test_set_it_up_with_plan(self, orch_with_plan):
        assert orch_with_plan._is_proceed_message("set it up") is True

    def test_set_it_up_please_with_plan(self, orch_with_plan):
        assert orch_with_plan._is_proceed_message("set it up please") is True

    def test_get_it_done_with_plan(self, orch_with_plan):
        assert orch_with_plan._is_proceed_message("get it done") is True

    def test_do_it_with_plan(self, orch_with_plan):
        assert orch_with_plan._is_proceed_message("do it") is True

    def test_make_it_happen_with_plan(self, orch_with_plan):
        assert orch_with_plan._is_proceed_message("make it happen") is True

    def test_lets_go_with_plan(self, orch_with_plan):
        assert orch_with_plan._is_proceed_message("let's go") is True

    def test_ok_sounds_good_with_plan(self, orch_with_plan):
        assert orch_with_plan._is_proceed_message("ok sounds good") is True

    def test_sounds_good_with_plan(self, orch_with_plan):
        assert orch_with_plan._is_proceed_message("sounds good") is True

    # ── Contextual signals: NOT proceed without plan ──

    def test_set_it_up_without_plan(self, orch_without_plan):
        assert orch_without_plan._is_proceed_message("set it up") is False

    def test_get_it_done_without_plan(self, orch_without_plan):
        assert orch_without_plan._is_proceed_message("get it done") is False

    def test_do_it_without_plan(self, orch_without_plan):
        assert orch_without_plan._is_proceed_message("do it") is False

    def test_make_it_happen_without_plan(self, orch_without_plan):
        assert orch_without_plan._is_proceed_message("make it happen") is False

    def test_ok_sounds_good_without_plan(self, orch_without_plan):
        assert orch_without_plan._is_proceed_message("ok sounds good") is False

    # ── Edge cases ──

    def test_empty_string(self, orch_with_plan):
        assert orch_with_plan._is_proceed_message("") is False

    def test_random_message(self, orch_with_plan):
        assert orch_with_plan._is_proceed_message("What's the weather?") is False

    def test_case_insensitive_proceed(self, orch_without_plan):
        assert orch_without_plan._is_proceed_message("PROCEED") is True

    def test_case_insensitive_set_it_up(self, orch_with_plan):
        assert orch_with_plan._is_proceed_message("SET IT UP") is True


class TestShortAcknowledgementGuards:
    """Tests for preventing short acknowledgement echo responses."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from chat_flow import _is_parrot_response, _is_short_acknowledgement

        self.is_parrot_response = _is_parrot_response
        self.is_short_acknowledgement = _is_short_acknowledgement

    def test_ok_sounds_good_is_short_acknowledgement(self):
        assert self.is_short_acknowledgement("ok sounds good") is True

    def test_echoed_short_ack_is_parrot_response(self):
        assert self.is_parrot_response("ok sounds good", "ok sounds good") is True

    def test_real_response_is_not_parrot_response(self):
        assert self.is_parrot_response(
            "ok sounds good",
            "Understood. I'll keep the current plan in focus.",
        ) is False


class TestExplicitToolRoutingGuards:
    """Tests for requests that must not be answered from stale chat context."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from chat_flow import (
            _explicit_tool_or_live_inspection_request,
            _explicit_write_tool_request,
        )

        self.needs_tools = _explicit_tool_or_live_inspection_request
        self.needs_writes = _explicit_write_tool_request

    def test_named_command_runner_request_requires_tools(self):
        assert self.needs_tools("Use command_runner to inspect /home/lancelot/app") is True
        assert self.needs_writes("Use command_runner to inspect /home/lancelot/app") is False

    def test_named_repo_writer_request_enables_write_path(self):
        assert self.needs_tools("Use repo_writer to overwrite README.md") is True
        assert self.needs_writes("Use repo_writer to overwrite README.md") is True

    def test_repo_word_alone_does_not_force_tools(self):
        assert self.needs_tools("What is a repo?") is False


class TestChatToolRouting:
    """Tests that explicit live/tool requests reach the governed tool loop."""

    class _Receipt:
        def complete(self, *_args, **_kwargs):
            return self

        def fail(self, *_args, **_kwargs):
            return self

    class _Context:
        history = []

        def add_history(self, *_args, **_kwargs):
            return None

        def get_context_string(self, **_kwargs):
            return "context"

    class _Governor:
        def check_limit(self, *_args, **_kwargs):
            return True

        def log_usage(self, *_args, **_kwargs):
            return None

    def _runtime(self):
        runtime = MagicMock()
        runtime.wake_up = lambda *_args, **_kwargs: None
        runtime.governor = self._Governor()
        runtime.sanitizer.sanitize.side_effect = lambda text: text
        runtime.context_env = self._Context()
        runtime.provider.provider_name = "test"
        runtime.receipt_service.create.return_value = None
        runtime.receipt_service.update.return_value = None
        runtime.task_store = None
        runtime._memory_enabled = False
        runtime.context_compiler = None
        runtime.assembler = None
        runtime.usage_tracker = None
        runtime.local_model.is_healthy.return_value = True
        runtime.model_name = "fast-model"
        runtime._check_name_update.return_value = None
        runtime._verify_intent_with_llm.side_effect = lambda _message, intent: intent
        runtime._is_proceed_message.return_value = False
        runtime._previous_was_substantive.return_value = False
        runtime._is_continuation.return_value = False
        runtime._is_conversational.return_value = False
        runtime._is_simple_for_local.return_value = True
        runtime._needs_research.return_value = False
        runtime._wants_action.return_value = False
        runtime._route_model.return_value = "fast-model"
        runtime._build_system_instruction.return_value = "system"
        runtime._local_agentic_generate.return_value = "local"
        runtime._agentic_generate.return_value = "agentic"
        runtime._text_only_generate.return_value = "text"
        runtime._get_deep_model.return_value = "fast-model"
        runtime._validate_llm_response.side_effect = lambda text: text
        runtime._parse_response.side_effect = lambda text: text
        return runtime

    def test_short_ack_after_approval_wait_does_not_hit_model(self, monkeypatch):
        import chat_flow
        import feature_flags

        monkeypatch.setattr(feature_flags, "FEATURE_UNIFIED_CLASSIFICATION", False)
        monkeypatch.setattr(feature_flags, "FEATURE_AGENTIC_LOOP", True)
        monkeypatch.setattr(feature_flags, "FEATURE_LOCAL_AGENTIC", True)
        monkeypatch.setattr(feature_flags, "FEATURE_DEEP_REASONING_LOOP", False)
        monkeypatch.setattr(feature_flags, "FEATURE_COMPETITIVE_SCAN", False)
        monkeypatch.setattr(chat_flow, "create_receipt", lambda *_args, **_kwargs: self._Receipt())
        monkeypatch.setattr(chat_flow, "classify_intent", lambda *_args, **_kwargs: chat_flow.IntentType.KNOWLEDGE_REQUEST)

        runtime = self._runtime()
        runtime.context_env.history = [
            {"role": "user", "content": "Use repo_writer to edit README.md"},
            {
                "role": "assistant",
                "content": (
                    "Paused for Commander approval before running `repo_writer`.\n\n"
                    "Approval ID: `abc123`.\n\n"
                    "Review and resolve the ActionCard in War Room. "
                    "After approval, use that card's Continue control to resume the same run."
                ),
            },
        ]

        response = chat_flow.chat(runtime, "ok sounds good")

        assert "paused for Commander approval" in response
        assert "ActionCard" in response
        runtime._local_agentic_generate.assert_not_called()
        runtime._agentic_generate.assert_not_called()
        runtime._text_only_generate.assert_not_called()

    def test_explicit_read_tool_request_skips_simple_local_branch(self, monkeypatch):
        import chat_flow
        import feature_flags

        monkeypatch.setattr(feature_flags, "FEATURE_UNIFIED_CLASSIFICATION", False)
        monkeypatch.setattr(feature_flags, "FEATURE_AGENTIC_LOOP", True)
        monkeypatch.setattr(feature_flags, "FEATURE_LOCAL_AGENTIC", True)
        monkeypatch.setattr(feature_flags, "FEATURE_DEEP_REASONING_LOOP", False)
        monkeypatch.setattr(feature_flags, "FEATURE_COMPETITIVE_SCAN", False)
        monkeypatch.setattr(chat_flow, "create_receipt", lambda *_args, **_kwargs: self._Receipt())
        monkeypatch.setattr(chat_flow, "classify_intent", lambda *_args, **_kwargs: chat_flow.IntentType.KNOWLEDGE_REQUEST)

        runtime = self._runtime()
        response = chat_flow.chat(runtime, "Use command_runner to inspect /home/lancelot/app")

        assert response == "agentic"
        runtime._local_agentic_generate.assert_not_called()
        runtime._agentic_generate.assert_called_once()
        assert runtime._agentic_generate.call_args.kwargs["force_tool_use"] is True
        assert runtime._agentic_generate.call_args.kwargs["allow_writes"] is False

    def test_receipt_grounded_tool_response_skips_auto_escalation(self, monkeypatch):
        import chat_flow
        import feature_flags

        monkeypatch.setattr(feature_flags, "FEATURE_UNIFIED_CLASSIFICATION", False)
        monkeypatch.setattr(feature_flags, "FEATURE_AGENTIC_LOOP", True)
        monkeypatch.setattr(feature_flags, "FEATURE_LOCAL_AGENTIC", True)
        monkeypatch.setattr(feature_flags, "FEATURE_DEEP_REASONING_LOOP", False)
        monkeypatch.setattr(feature_flags, "FEATURE_COMPETITIVE_SCAN", False)
        monkeypatch.setattr(chat_flow, "create_receipt", lambda *_args, **_kwargs: self._Receipt())
        monkeypatch.setattr(chat_flow, "classify_intent", lambda *_args, **_kwargs: chat_flow.IntentType.KNOWLEDGE_REQUEST)

        runtime = self._runtime()
        runtime._get_deep_model.return_value = "deep-model"
        runtime._agentic_generate.return_value = "Completed approved governed actions:\n- repo_writer: SUCCESS"
        message = "Use command_runner to inspect /home/lancelot/app. " + ("Include operational detail. " * 12)

        response = chat_flow.chat(runtime, message)

        assert response == "Completed approved governed actions:\n- repo_writer: SUCCESS"
        runtime._provider_generate.assert_not_called()

    def test_explicit_repo_writer_request_enables_writes(self, monkeypatch):
        import chat_flow
        import feature_flags

        monkeypatch.setattr(feature_flags, "FEATURE_UNIFIED_CLASSIFICATION", False)
        monkeypatch.setattr(feature_flags, "FEATURE_AGENTIC_LOOP", True)
        monkeypatch.setattr(feature_flags, "FEATURE_LOCAL_AGENTIC", True)
        monkeypatch.setattr(feature_flags, "FEATURE_DEEP_REASONING_LOOP", False)
        monkeypatch.setattr(feature_flags, "FEATURE_COMPETITIVE_SCAN", False)
        monkeypatch.setattr(chat_flow, "create_receipt", lambda *_args, **_kwargs: self._Receipt())
        monkeypatch.setattr(chat_flow, "classify_intent", lambda *_args, **_kwargs: chat_flow.IntentType.KNOWLEDGE_REQUEST)

        runtime = self._runtime()
        response = chat_flow.chat(runtime, "Use repo_writer to overwrite README.md")

        assert response == "agentic"
        assert runtime._agentic_generate.call_args.kwargs["force_tool_use"] is True
        assert runtime._agentic_generate.call_args.kwargs["allow_writes"] is True

    def test_warroom_explicit_repo_writer_request_stays_gated(self, monkeypatch):
        import chat_flow
        import feature_flags

        monkeypatch.setattr(feature_flags, "FEATURE_UNIFIED_CLASSIFICATION", False)
        monkeypatch.setattr(feature_flags, "FEATURE_AGENTIC_LOOP", True)
        monkeypatch.setattr(feature_flags, "FEATURE_LOCAL_AGENTIC", True)
        monkeypatch.setattr(feature_flags, "FEATURE_DEEP_REASONING_LOOP", False)
        monkeypatch.setattr(feature_flags, "FEATURE_COMPETITIVE_SCAN", False)
        monkeypatch.setattr(chat_flow, "create_receipt", lambda *_args, **_kwargs: self._Receipt())
        monkeypatch.setattr(chat_flow, "classify_intent", lambda *_args, **_kwargs: chat_flow.IntentType.KNOWLEDGE_REQUEST)

        runtime = self._runtime()
        response = chat_flow.chat(
            runtime,
            "Use repo_writer to overwrite README.md",
            channel="warroom",
        )

        assert response == "agentic"
        assert runtime._agentic_generate.call_args.kwargs["force_tool_use"] is True
        assert runtime._agentic_generate.call_args.kwargs["allow_writes"] is False

    def test_explicit_write_request_starting_with_approved_skips_proceed_guard(self, monkeypatch):
        import chat_flow
        import feature_flags

        monkeypatch.setattr(feature_flags, "FEATURE_UNIFIED_CLASSIFICATION", False)
        monkeypatch.setattr(feature_flags, "FEATURE_AGENTIC_LOOP", True)
        monkeypatch.setattr(feature_flags, "FEATURE_LOCAL_AGENTIC", True)
        monkeypatch.setattr(feature_flags, "FEATURE_DEEP_REASONING_LOOP", False)
        monkeypatch.setattr(feature_flags, "FEATURE_COMPETITIVE_SCAN", False)
        monkeypatch.setattr(chat_flow, "create_receipt", lambda *_args, **_kwargs: self._Receipt())
        monkeypatch.setattr(chat_flow, "classify_intent", lambda *_args, **_kwargs: chat_flow.IntentType.KNOWLEDGE_REQUEST)

        runtime = self._runtime()
        runtime.task_store = MagicMock()
        runtime._is_proceed_message.return_value = True

        response = chat_flow.chat(
            runtime,
            "Approved. Use repo_writer to create src/core/example_models.py",
        )

        assert response == "agentic"
        runtime._handle_proceed.assert_not_called()
        assert runtime._agentic_generate.call_args.kwargs["force_tool_use"] is True
        assert runtime._agentic_generate.call_args.kwargs["allow_writes"] is True


# ---------------------------------------------------------------------------
# Stall Phrase Detection
# ---------------------------------------------------------------------------

class TestStallPhrases:
    """Tests that new stall phrases are caught by response_governor."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from response_governor import detect_forbidden_async_language
        self.detect = detect_forbidden_async_language

    def test_awaiting_further_instructions(self):
        text = "I am ready and awaiting further instructions, Commander."
        violations = self.detect(text)
        assert len(violations) > 0

    def test_awaiting_your_instructions(self):
        text = "Awaiting your instructions to proceed."
        violations = self.detect(text)
        assert len(violations) > 0

    def test_awaiting_your_command(self):
        text = "I am awaiting your command, Commander."
        violations = self.detect(text)
        assert len(violations) > 0

    def test_ready_and_awaiting(self):
        text = "I am ready and awaiting your next directive."
        violations = self.detect(text)
        assert len(violations) > 0

    def test_standing_by_for(self):
        text = "Standing by for your instructions."
        violations = self.detect(text)
        assert len(violations) > 0

    def test_waiting_for_your(self):
        text = "Waiting for your response before I continue."
        violations = self.detect(text)
        assert len(violations) > 0

    def test_at_your_command(self):
        text = "At your command, Commander."
        violations = self.detect(text)
        assert len(violations) > 0

    def test_clean_message_passes(self):
        text = "Here is the plan for setting up voice communication."
        violations = self.detect(text)
        assert len(violations) == 0

    def test_existing_phrases_still_caught(self):
        """Verify pre-existing forbidden phrases still work."""
        text = "I'm working on it and will report back shortly."
        violations = self.detect(text)
        assert len(violations) > 0
