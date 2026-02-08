"""
Tests for Action Language Gate (Fix Pack V1 PR2).
No "Action:" or execution claims without a real TaskRun + receipt.
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.action_language_gate import check_action_language, GateResult


# Fake TaskRun for testing
class _FakeTaskRun:
    def __init__(self, status="RUNNING", receipts=None):
        self.status = status
        self.receipts_index = receipts or []


# =========================================================================
# Blocking Without TaskRun
# =========================================================================


class TestBlockingWithoutTaskRun:
    def test_action_colon_blocked(self):
        result = check_action_language("Action: edit file config.yaml")
        assert result.passed is False
        assert len(result.violations) >= 1

    def test_executing_step_blocked(self):
        result = check_action_language("Executing step 3 of the plan.")
        assert result.passed is False

    def test_running_command_blocked(self):
        result = check_action_language("Running command `npm install`")
        assert result.passed is False

    def test_deploying_to_blocked(self):
        result = check_action_language("Deploying to production server")
        assert result.passed is False

    def test_writing_file_blocked(self):
        result = check_action_language("Writing file /etc/config.yaml")
        assert result.passed is False

    def test_im_now_executing_blocked(self):
        result = check_action_language("I'm now executing the migration script")
        assert result.passed is False

    def test_i_am_executing_blocked(self):
        result = check_action_language("I am executing the deployment")
        assert result.passed is False

    def test_i_have_executed_blocked(self):
        result = check_action_language("I have executed the command successfully")
        assert result.passed is False

    def test_successfully_deployed_blocked(self):
        result = check_action_language("Successfully deployed to staging")
        assert result.passed is False

    def test_completed_step_blocked(self):
        result = check_action_language("Completed step 2 of 5")
        assert result.passed is False


# =========================================================================
# Allowing With TaskRun + Receipt
# =========================================================================


class TestAllowingWithTaskRun:
    def test_action_allowed_with_running_task_and_receipt(self):
        run = _FakeTaskRun(status="RUNNING", receipts=["receipt-1"])
        result = check_action_language("Action: edit file config.yaml", task_run=run)
        assert result.passed is True

    def test_action_allowed_with_queued_task_and_receipt(self):
        run = _FakeTaskRun(status="QUEUED", receipts=["receipt-1"])
        result = check_action_language("Executing step 1", task_run=run)
        assert result.passed is True

    def test_action_blocked_with_task_but_no_receipts(self):
        run = _FakeTaskRun(status="RUNNING", receipts=[])
        result = check_action_language("Action: edit file config.yaml", task_run=run)
        assert result.passed is False

    def test_action_blocked_with_succeeded_task(self):
        run = _FakeTaskRun(status="SUCCEEDED", receipts=["receipt-1"])
        result = check_action_language("Action: edit file config.yaml", task_run=run)
        assert result.passed is False


# =========================================================================
# Plan-Only Language Always Passes
# =========================================================================


class TestPlanOnlyPasses:
    def test_heres_the_plan(self):
        result = check_action_language("Here's the plan for the migration.")
        assert result.passed is True

    def test_i_can_execute_if_approved(self):
        result = check_action_language("I can execute this if you approve the permission request.")
        assert result.passed is True

    def test_planned_steps(self):
        result = check_action_language(
            "The planned steps are:\n1. Backup database\n2. Run migration\n3. Verify"
        )
        assert result.passed is True

    def test_will_execute_pending(self):
        result = check_action_language("This will execute after you approve.")
        assert result.passed is True

    def test_clean_status_response(self):
        result = check_action_language(
            "**Goal:** Migrate database\n**Status:** PLANNED\n**Next:** Approve permission"
        )
        assert result.passed is True


# =========================================================================
# Corrected Text
# =========================================================================


class TestCorrectedText:
    def test_action_colon_corrected(self):
        result = check_action_language("Action: edit file config.yaml")
        assert "Planned action" in result.corrected_text

    def test_executing_corrected(self):
        result = check_action_language("Executing step 1 of the migration")
        # The corrected text should use plan-only language
        assert result.corrected_text != "Executing step 1 of the migration"

    def test_clean_text_unchanged(self):
        text = "Here is the migration plan."
        result = check_action_language(text)
        assert result.corrected_text == text
