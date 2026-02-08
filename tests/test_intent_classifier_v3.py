"""
Tests for Fix Pack V3 — Intent Classifier broadened keywords + default fix.

Validates:
- "set up a way for us to communicate" → EXEC_REQUEST (new "set" keyword)
- "does slack offer a free use account" → KNOWLEDGE_REQUEST (new "does" keyword)
- "which one would be best" → KNOWLEDGE_REQUEST (new "which" keyword)
- Default fallback → PLAN_REQUEST (not AMBIGUOUS)
- Previous EXEC_REQUEST / PLAN_REQUEST / KNOWLEDGE_REQUEST cases still work
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))

from intent_classifier import classify_intent
from plan_types import IntentType


# ---------------------------------------------------------------------------
# Fix 1A: Default fallback is PLAN_REQUEST (not AMBIGUOUS)
# ---------------------------------------------------------------------------

class TestDefaultFallback:
    """Spec lines 13-14: uncertain → PLAN_REQUEST, never AMBIGUOUS."""

    def test_random_message_not_ambiguous(self):
        result = classify_intent("hello there friend")
        assert result != IntentType.AMBIGUOUS

    def test_random_message_is_plan_request(self):
        result = classify_intent("hello there friend")
        assert result == IntentType.PLAN_REQUEST

    def test_gibberish_defaults_plan(self):
        result = classify_intent("xyzzy plugh")
        assert result == IntentType.PLAN_REQUEST

    def test_empty_still_ambiguous(self):
        """Empty/whitespace is a special case — still AMBIGUOUS."""
        assert classify_intent("") == IntentType.AMBIGUOUS
        assert classify_intent("   ") == IntentType.AMBIGUOUS


# ---------------------------------------------------------------------------
# Fix 1B: Broadened EXECUTION keywords catch real user messages
# ---------------------------------------------------------------------------

class TestBroadenedExecution:
    """New keywords: set, configure, setup, connect, enable, create."""

    def test_set_up_voice_communication(self):
        """The exact failing user message from screenshots."""
        result = classify_intent(
            "Set up a way for us to communicate via voice from my iPad iPhone and pc"
        )
        assert result == IntentType.EXEC_REQUEST

    def test_set_up_short(self):
        result = classify_intent("set up voice")
        assert result == IntentType.EXEC_REQUEST

    def test_configure_system(self):
        result = classify_intent("configure the notification system")
        assert result == IntentType.EXEC_REQUEST

    def test_setup_one_word(self):
        result = classify_intent("setup voice chat")
        assert result == IntentType.EXEC_REQUEST

    def test_connect_devices(self):
        result = classify_intent("connect my devices together")
        assert result == IntentType.EXEC_REQUEST

    def test_enable_feature(self):
        result = classify_intent("enable push notifications")
        assert result == IntentType.EXEC_REQUEST

    def test_create_channel(self):
        result = classify_intent("create a new voice channel")
        assert result == IntentType.EXEC_REQUEST

    def test_set_up_phrase_match(self):
        """'set up' phrase match (broader than 'set it up')."""
        result = classify_intent("set up encryption for messages")
        assert result == IntentType.EXEC_REQUEST


# ---------------------------------------------------------------------------
# Fix 1C: Broadened KNOWLEDGE keywords catch question-form messages
# ---------------------------------------------------------------------------

class TestBroadenedKnowledge:
    """New keywords: does, how, which, is, are, can, should, offer."""

    def test_does_slack_offer(self):
        """The exact failing user message from screenshots."""
        result = classify_intent("Does slack offer a free use account?")
        assert result == IntentType.KNOWLEDGE_REQUEST

    def test_which_one_best(self):
        result = classify_intent("I don't have a work platform which one would be best?")
        assert result == IntentType.KNOWLEDGE_REQUEST

    def test_how_do_i(self):
        result = classify_intent("how do I get started with Slack")
        assert result == IntentType.KNOWLEDGE_REQUEST

    def test_is_there_free_tier(self):
        result = classify_intent("is there a free tier available")
        assert result == IntentType.KNOWLEDGE_REQUEST

    def test_are_there_options(self):
        result = classify_intent("are there better options than Zoom")
        assert result == IntentType.KNOWLEDGE_REQUEST

    def test_can_i_use(self):
        result = classify_intent("can I use Signal on Windows")
        assert result == IntentType.KNOWLEDGE_REQUEST

    def test_should_i_pick(self):
        result = classify_intent("should I pick Discord or Teams")
        assert result == IntentType.KNOWLEDGE_REQUEST

    def test_does_it_support(self):
        result = classify_intent("does it support video calls")
        assert result == IntentType.KNOWLEDGE_REQUEST

    def test_how_can_i(self):
        result = classify_intent("how can I invite someone")
        assert result == IntentType.KNOWLEDGE_REQUEST


# ---------------------------------------------------------------------------
# Regression: Previous cases still work
# ---------------------------------------------------------------------------

class TestRegressionExisting:
    """Ensure V1/V2 classifications haven't broken."""

    def test_plan_keyword(self):
        assert classify_intent("plan the migration") == IntentType.PLAN_REQUEST

    def test_design_keyword(self):
        assert classify_intent("design the architecture") == IntentType.PLAN_REQUEST

    def test_implement_keyword(self):
        assert classify_intent("implement the feature") == IntentType.EXEC_REQUEST

    def test_deploy_keyword(self):
        assert classify_intent("deploy to production") == IntentType.EXEC_REQUEST

    def test_what_keyword(self):
        assert classify_intent("what is a microservice") == IntentType.KNOWLEDGE_REQUEST

    def test_why_keyword(self):
        assert classify_intent("why does it crash") == IntentType.KNOWLEDGE_REQUEST

    def test_mixed_plan_and_exec(self):
        result = classify_intent("plan and then implement the auth system")
        assert result == IntentType.MIXED_REQUEST

    def test_do_it_phrase(self):
        assert classify_intent("do it") == IntentType.EXEC_REQUEST

    def test_roll_out_phrase(self):
        assert classify_intent("roll out the update") == IntentType.EXEC_REQUEST


# ---------------------------------------------------------------------------
# Edge cases: MIXED_REQUEST when both plan + exec keywords present
# ---------------------------------------------------------------------------

class TestMixedWithNewKeywords:
    """Messages containing both planning and new execution keywords."""

    def test_plan_and_set_up(self):
        """'plan' (PLANNING) + 'set' (EXECUTION) → MIXED."""
        result = classify_intent("plan how to set up the voice system")
        assert result == IntentType.MIXED_REQUEST

    def test_design_and_create(self):
        """'design' (PLANNING) + 'create' (EXECUTION) → MIXED."""
        result = classify_intent("design and create the notification service")
        assert result == IntentType.MIXED_REQUEST
