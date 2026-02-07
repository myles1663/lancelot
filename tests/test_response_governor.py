"""
Tests for response_governor — "No Simulated Work" policy enforcement.
"""

import os
import sys
import pytest

# Ensure src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))

from response_governor import (
    detect_forbidden_async_language,
    detect_fake_work_proposal,
    enforce_no_simulated_work,
    ResponseContext,
    JobContext,
    FORBIDDEN_PHRASES,
)
from plan_types import OutcomeType


# =========================================================================
# Detection of Forbidden Phrases
# =========================================================================


class TestDetectForbiddenLanguage:
    def test_clean_text(self):
        assert detect_forbidden_async_language("Here is your migration plan.") == []

    def test_working_on_it(self):
        matches = detect_forbidden_async_language("I'm working on it right now")
        assert len(matches) >= 1

    def test_investigating(self):
        matches = detect_forbidden_async_language("I'm investigating the issue")
        assert len(matches) >= 1

    def test_allow_me_time(self):
        matches = detect_forbidden_async_language("Please allow me time to review")
        assert len(matches) >= 1

    def test_report_back(self):
        matches = detect_forbidden_async_language("I will report back soon")
        assert len(matches) >= 1

    def test_processing_request(self):
        matches = detect_forbidden_async_language("I'm processing your request")
        assert len(matches) >= 1

    def test_case_insensitive(self):
        matches = detect_forbidden_async_language("I'M WORKING ON IT")
        assert len(matches) >= 1

    def test_mixed_case(self):
        matches = detect_forbidden_async_language("i'm Working On It")
        assert len(matches) >= 1

    def test_multiple_violations(self):
        text = "I'm working on it and I will report back soon"
        matches = detect_forbidden_async_language(text)
        assert len(matches) >= 2

    def test_empty_text(self):
        assert detect_forbidden_async_language("") == []

    def test_none_like_empty(self):
        assert detect_forbidden_async_language("") == []

    def test_give_me_a_moment(self):
        matches = detect_forbidden_async_language("Give me a moment to check")
        assert len(matches) >= 1

    def test_currently_processing(self):
        matches = detect_forbidden_async_language("Currently processing the data")
        assert len(matches) >= 1

    def test_please_wait(self):
        matches = detect_forbidden_async_language("Please wait while I look into it")
        assert len(matches) >= 1

    def test_allowed_alternative_text(self):
        # These should NOT trigger
        assert detect_forbidden_async_language("I can't verify X with current tools.") == []
        assert detect_forbidden_async_language("Here's a plan using stated assumptions.") == []
        assert detect_forbidden_async_language("To proceed further, I need Y.") == []


# =========================================================================
# Enforcement Without Job ID (Should Block)
# =========================================================================


class TestEnforcementNoJob:
    def test_forbidden_text_blocked(self):
        ctx = ResponseContext(text="I'm working on it and will report back.")
        result = enforce_no_simulated_work(ctx)
        assert result.passed is False
        assert len(result.violations) >= 1

    def test_recommended_outcome_provided(self):
        ctx = ResponseContext(text="I'm investigating the problem")
        result = enforce_no_simulated_work(ctx)
        assert result.passed is False
        assert result.recommended_outcome is not None
        assert result.recommended_outcome in (
            OutcomeType.CANNOT_COMPLETE,
            OutcomeType.NEEDS_INPUT,
            OutcomeType.COMPLETED_WITH_PLAN_ARTIFACT,
        )

    def test_reason_provided(self):
        ctx = ResponseContext(text="Please allow me time to check")
        result = enforce_no_simulated_work(ctx)
        assert result.passed is False
        assert result.reason is not None
        assert "simulated" in result.reason.lower() or "no real" in result.reason.lower()

    def test_clean_text_passes(self):
        ctx = ResponseContext(text="Here is your detailed plan for the migration.")
        result = enforce_no_simulated_work(ctx)
        assert result.passed is True
        assert result.violations == []

    def test_empty_job_context(self):
        ctx = ResponseContext(text="I'm processing your request")
        job = JobContext(job_id="", status=None)
        result = enforce_no_simulated_work(ctx, job)
        assert result.passed is False

    def test_none_job_context(self):
        ctx = ResponseContext(text="I'm processing your request")
        result = enforce_no_simulated_work(ctx, None)
        assert result.passed is False


# =========================================================================
# Enforcement With Job ID (Should Allow)
# =========================================================================


class TestEnforcementWithJob:
    def test_forbidden_text_allowed_with_job(self):
        ctx = ResponseContext(text="I'm working on it")
        job = JobContext(job_id="job-12345", status="running")
        result = enforce_no_simulated_work(ctx, job)
        assert result.passed is True

    def test_multiple_violations_allowed_with_job(self):
        ctx = ResponseContext(text="I'm working on it and I will report back")
        job = JobContext(job_id="job-99", status="in_progress")
        result = enforce_no_simulated_work(ctx, job)
        assert result.passed is True
        assert len(result.violations) >= 2  # violations still detected
        assert result.reason is not None  # reason explains why allowed

    def test_clean_text_with_job_passes(self):
        ctx = ResponseContext(text="Here is the result of your migration.")
        job = JobContext(job_id="job-42", status="completed")
        result = enforce_no_simulated_work(ctx, job)
        assert result.passed is True
        assert result.violations == []


# =========================================================================
# Edge Cases
# =========================================================================


class TestEdgeCases:
    def test_whitespace_only_job_id_is_not_real(self):
        ctx = ResponseContext(text="I'm working on it")
        job = JobContext(job_id="   ", status="running")
        result = enforce_no_simulated_work(ctx, job)
        assert result.passed is False

    def test_partial_phrase_no_match(self):
        # "working" alone should not trigger "i'm working on it"
        result = detect_forbidden_async_language("I'm working hard")
        # Shouldn't match the full phrase
        assert not any("working on it" in v for v in result)

    def test_final_output_no_forbidden_after_enforcement(self):
        """After enforcement, if blocked, the violations should be actionable."""
        ctx = ResponseContext(text="I'm investigating your request")
        result = enforce_no_simulated_work(ctx)
        assert result.passed is False
        # The caller should use recommended_outcome to produce a safe response
        assert result.recommended_outcome is not None


# =========================================================================
# Forbidden Phrases List Sanity
# =========================================================================


class TestForbiddenPhrasesList:
    def test_non_empty(self):
        assert len(FORBIDDEN_PHRASES) > 0

    def test_all_lowercase(self):
        for phrase in FORBIDDEN_PHRASES:
            assert phrase == phrase.lower(), f"Phrase not lowercase: {phrase}"

    def test_minimum_count(self):
        # The spec requires at least 5 core phrases
        assert len(FORBIDDEN_PHRASES) >= 5


# =========================================================================
# Structural Fake Work Proposal Detection (Integration)
# =========================================================================


class TestFakeWorkProposalInGovernor:
    """Integration: detect_fake_work_proposal is accessible and works."""

    def test_function_exists(self):
        assert callable(detect_fake_work_proposal)

    def test_returns_none_for_clean(self):
        assert detect_fake_work_proposal("Here is your answer.") is None

    def test_returns_string_for_fake_work(self):
        result = detect_fake_work_proposal(
            "Phase 1: Research (2 hours)\n"
            "Phase 2: Development (4 hours)\n"
            "I recommend starting with the feasibility study."
        )
        assert isinstance(result, str)
        assert len(result) > 0
