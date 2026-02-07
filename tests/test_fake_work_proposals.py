"""
Tests for Fake Work Proposal detection — prevents Gemini from generating
elaborate multi-phase plans with time estimates that it cannot execute.

AC-5: No Fake Work Proposals
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))

from response_governor import (
    detect_forbidden_async_language,
    detect_fake_work_proposal,
    enforce_no_simulated_work,
    ResponseContext,
    JobContext,
)
from plan_types import OutcomeType


# =========================================================================
# New Forbidden Phrases (v3)
# =========================================================================


class TestNewForbiddenPhrases:
    """Individual stalling phrases added in v3."""

    NEW_PHRASES = [
        "i will now proceed with",
        "i'll now proceed with",
        "i will proceed with",
        "feasibility study",
        "feasibility assessment",
        "feasibility analysis",
        "i recommend starting with",
        "assess the viability",
        "assessing the viability",
        "once the feasibility is confirmed",
        "once feasibility is confirmed",
        "i will begin by",
        "i'll begin by",
        "let me begin by",
        "i will start by researching",
        "prototype development",
        "proof of concept phase",
        "initial research phase",
        "research phase",
        "discovery phase",
        "assessment phase",
        "i will conduct",
        "i'll conduct",
        "i will research",
        "i'll research",
        "i will investigate",
        "i'll investigate",
        "i will analyze the",
        "i'll analyze the",
        "i will evaluate",
        "i'll evaluate",
        "i will explore",
        "i'll explore",
        "i will assess",
        "i'll assess",
        "let me assess",
        "let me evaluate",
        "let me research",
        "let me investigate",
        "let me explore",
        "let me analyze",
    ]

    @pytest.mark.parametrize("phrase", NEW_PHRASES)
    def test_new_phrase_detected(self, phrase):
        matches = detect_forbidden_async_language(phrase)
        assert len(matches) >= 1, f"Governor missed new phrase: {phrase}"

    @pytest.mark.parametrize("phrase", NEW_PHRASES)
    def test_new_phrase_case_insensitive(self, phrase):
        matches = detect_forbidden_async_language(phrase.upper())
        assert len(matches) >= 1, f"Case-insensitive failed for: {phrase}"


# =========================================================================
# Structural Fake Work Proposal Detection
# =========================================================================


class TestDetectFakeWorkProposal:
    """Tests for the structural fake work proposal detector."""

    def test_clean_text_passes(self):
        result = detect_fake_work_proposal(
            "Here is how to set up voice chat with ElevenLabs. "
            "First, install the python-telegram-bot library. "
            "Then configure your webhook."
        )
        assert result is None

    def test_time_estimates_trigger(self):
        result = detect_fake_work_proposal(
            "Phase 1: Research (2 hours)\n"
            "Phase 2: Prototype Development (4 hours)\n"
            "Phase 3: Testing (1 hour)\n"
            "I recommend starting with the feasibility study."
        )
        assert result is not None

    def test_feasibility_study_proposal(self):
        result = detect_fake_work_proposal(
            "I will now proceed with the feasibility study to assess "
            "the viability of integrating ElevenLabs with Telegram. "
            "I will research the available APIs and then I will evaluate "
            "the best approach. I will conduct a preliminary assessment."
        )
        assert result is not None

    def test_multi_phase_plan(self):
        result = detect_fake_work_proposal(
            "Phase 1: Initial Research\n"
            "I will investigate the Telegram Bot API.\n"
            "Phase 2: Prototype Development\n"
            "I will build a proof of concept.\n"
            "Phase 3: Integration Testing\n"
            "I will evaluate the complete solution."
        )
        assert result is not None

    def test_legitimate_plan_not_blocked(self):
        """A legitimate structured response with concrete steps should pass."""
        result = detect_fake_work_proposal(
            "To set up voice chat, you need:\n"
            "1. Install python-telegram-bot: pip install python-telegram-bot\n"
            "2. Get an ElevenLabs API key from their dashboard\n"
            "3. Create a Telegram bot via BotFather\n"
            "4. Configure the webhook URL in your server\n"
            "5. Write a handler that forwards audio to ElevenLabs\n"
            "I cannot autonomously run these steps, but here is the code."
        )
        assert result is None

    def test_single_i_will_not_triggered(self):
        """A single 'I will' statement should not trigger."""
        result = detect_fake_work_proposal(
            "I will explain how to configure the Telegram bot. "
            "First, create the bot via BotFather."
        )
        assert result is None

    def test_short_text_not_triggered(self):
        result = detect_fake_work_proposal("OK")
        assert result is None

    def test_empty_text_not_triggered(self):
        result = detect_fake_work_proposal("")
        assert result is None

    def test_proceed_with_gemini_stalling(self):
        """The 'I will now proceed' continuation pattern."""
        result = detect_fake_work_proposal(
            "I will now proceed with the feasibility study to assess "
            "the viability of this integration. Once the feasibility is "
            "confirmed, I will then proceed to the prototype development "
            "phase. After completing the prototype, I will evaluate the results."
        )
        assert result is not None

    def test_real_world_example_from_bug_report(self):
        """The exact pattern from the bug report."""
        result = detect_fake_work_proposal(
            "Feasibility Study (1 hour): Research Telegram bot frameworks "
            "and ElevenLabs API capabilities. Assess the viability of "
            "real-time voice streaming.\n\n"
            "Prototype Development (4 hours): Build a proof of concept "
            "integrating the Telegram Bot API with ElevenLabs text-to-speech.\n\n"
            "I recommend starting with the feasibility study to assess "
            "the viability of this approach."
        )
        assert result is not None


# =========================================================================
# Enforcement Integration
# =========================================================================


class TestEnforcementWithFakeWorkProposal:
    """enforce_no_simulated_work should also catch fake work proposals."""

    def test_fake_work_blocked(self):
        ctx = ResponseContext(
            text=(
                "Phase 1: Research (2 hours)\n"
                "Phase 2: Development (4 hours)\n"
                "I recommend starting with the feasibility study."
            )
        )
        result = enforce_no_simulated_work(ctx)
        assert result.passed is False

    def test_fake_work_allowed_with_job(self):
        ctx = ResponseContext(
            text=(
                "Phase 1: Research (2 hours)\n"
                "Phase 2: Development (4 hours)\n"
                "I recommend starting with the feasibility study."
            )
        )
        job = JobContext(job_id="job-real-789", status="running")
        result = enforce_no_simulated_work(ctx, job)
        assert result.passed is True

    def test_clean_response_passes(self):
        ctx = ResponseContext(
            text="Here is how to set up the integration. Step 1: Install the library."
        )
        result = enforce_no_simulated_work(ctx)
        assert result.passed is True


# =========================================================================
# False Positive Prevention
# =========================================================================


class TestNonFalsePositives:
    """Ensure legitimate content is not incorrectly blocked."""

    def test_plan_artifact_with_numbered_steps_ok(self):
        """PlanArtifact output uses numbered steps, not Phase N: headers."""
        result = detect_fake_work_proposal(
            "## Plan Steps\n\n"
            "1. Analyze requirements: Review the objective\n"
            "2. Gather information: Collect missing details\n"
            "3. Design solution: Outline the approach\n"
            "4. Validate approach: Review against criteria\n"
            "5. Execute plan: Implement step by step"
        )
        assert result is None

    def test_user_discussion_of_feasibility_ok(self):
        """Talking ABOUT feasibility studies (not proposing to do one) stays under threshold."""
        result = detect_fake_work_proposal(
            "A feasibility study is a structured evaluation of a proposed "
            "project or system. It typically examines technical, economic, "
            "and operational dimensions."
        )
        # Score: 2 (one keyword "feasibility") — below threshold of 5
        assert result is None

    def test_legitimate_time_reference_ok(self):
        """Mentioning time in context (not as a work estimate) is fine."""
        result = detect_fake_work_proposal(
            "The server response time improved from 200ms to 50ms "
            "after we added caching. The deployment took about 30 minutes."
        )
        assert result is None

    def test_code_with_phase_variable_ok(self):
        """Code that uses 'phase' as a variable name should not trigger."""
        result = detect_fake_work_proposal(
            "def get_phase(moon_data):\n"
            "    phase = calculate_moon_phase(moon_data)\n"
            "    return phase\n"
        )
        assert result is None

    def test_simple_conversational_response_ok(self):
        result = detect_fake_work_proposal(
            "Sure, I can help with that. Here are the steps you need to follow "
            "to configure the Telegram bot for voice messages."
        )
        assert result is None
