"""
Tests for Fix Pack V2 — Expanded proceed detection + stall phrase blocking.

Validates:
- "set it up" treated as proceed when a plan exists
- "set it up" NOT treated as proceed when no plan exists
- Strong signals always treated as proceed
- "awaiting further instructions" caught as forbidden stall phrase
"""

import pytest
import sys
import os
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))


# ---------------------------------------------------------------------------
# Proceed Detection
# ---------------------------------------------------------------------------

def _is_proceed_message_standalone(message: str, has_plan: bool) -> bool:
    """Standalone copy of Orchestrator._is_proceed_message logic for testing.

    This avoids importing the full Orchestrator which has heavy dependencies.
    Must be kept in sync with orchestrator.py::_is_proceed_message.
    """
    lower = message.strip().lower()

    strong_phrases = [
        "proceed", "go ahead", "approved", "approve",
        "yes, proceed", "yes proceed", "execute",
        "run it", "start execution", "yes go ahead",
        "confirmed", "confirm",
    ]
    if any(lower.startswith(p) or lower == p for p in strong_phrases):
        return True

    contextual_phrases = [
        "do it", "set it up", "get it done", "make it happen",
        "wire it up", "hook it up", "let's go", "do this",
        "yes do it", "yes, do it",
    ]
    if has_plan and any(lower.startswith(p) or lower == p for p in contextual_phrases):
        return True

    return False


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

    # ── Contextual signals: NOT proceed without plan ──

    def test_set_it_up_without_plan(self, orch_without_plan):
        assert orch_without_plan._is_proceed_message("set it up") is False

    def test_get_it_done_without_plan(self, orch_without_plan):
        assert orch_without_plan._is_proceed_message("get it done") is False

    def test_do_it_without_plan(self, orch_without_plan):
        assert orch_without_plan._is_proceed_message("do it") is False

    def test_make_it_happen_without_plan(self, orch_without_plan):
        assert orch_without_plan._is_proceed_message("make it happen") is False

    # ── Edge cases ──

    def test_empty_string(self, orch_with_plan):
        assert orch_with_plan._is_proceed_message("") is False

    def test_random_message(self, orch_with_plan):
        assert orch_with_plan._is_proceed_message("What's the weather?") is False

    def test_case_insensitive_proceed(self, orch_without_plan):
        assert orch_without_plan._is_proceed_message("PROCEED") is True

    def test_case_insensitive_set_it_up(self, orch_with_plan):
        assert orch_with_plan._is_proceed_message("SET IT UP") is True


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
