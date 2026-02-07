"""
Tests for intent_classifier — deterministic keyword-based intent routing.
"""

import os
import sys
import pytest

# Ensure src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))

from intent_classifier import (
    classify_intent,
    PLANNING_KEYWORDS,
    EXECUTION_KEYWORDS,
    KNOWLEDGE_KEYWORDS,
)
from plan_types import IntentType


# =========================================================================
# Pure Planning Requests
# =========================================================================


class TestPlanRequest:
    def test_plan_keyword(self):
        assert classify_intent("Create a plan for migrating the database") == IntentType.PLAN_REQUEST

    def test_design_keyword(self):
        assert classify_intent("Design the authentication system") == IntentType.PLAN_REQUEST

    def test_approach_keyword(self):
        assert classify_intent("What approach should we take?") == IntentType.PLAN_REQUEST

    def test_architecture_keyword(self):
        assert classify_intent("Describe the architecture for the microservices") == IntentType.PLAN_REQUEST

    def test_blueprint_keyword(self):
        assert classify_intent("Give me a blueprint for the project") == IntentType.PLAN_REQUEST

    def test_strategy_keyword(self):
        assert classify_intent("We need a strategy for scaling") == IntentType.PLAN_REQUEST

    def test_how_would_we_phrase(self):
        assert classify_intent("How would we handle user authentication?") == IntentType.PLAN_REQUEST

    def test_how_should_we_phrase(self):
        assert classify_intent("How should we structure the API?") == IntentType.PLAN_REQUEST

    def test_roadmap_keyword(self):
        assert classify_intent("Draft a roadmap for Q3") == IntentType.PLAN_REQUEST

    def test_spec_keyword(self):
        assert classify_intent("Write a spec for the new feature") == IntentType.PLAN_REQUEST


# =========================================================================
# Pure Execution Requests
# =========================================================================


class TestExecRequest:
    def test_implement_keyword(self):
        assert classify_intent("Implement the login page") == IntentType.EXEC_REQUEST

    def test_code_keyword(self):
        assert classify_intent("Code the REST endpoint") == IntentType.EXEC_REQUEST

    def test_deploy_keyword(self):
        assert classify_intent("Deploy the application to staging") == IntentType.EXEC_REQUEST

    def test_commit_keyword(self):
        assert classify_intent("Commit these changes") == IntentType.EXEC_REQUEST

    def test_run_keyword(self):
        assert classify_intent("Run the test suite") == IntentType.EXEC_REQUEST

    def test_execute_keyword(self):
        assert classify_intent("Execute the migration script") == IntentType.EXEC_REQUEST

    def test_ship_keyword(self):
        assert classify_intent("Ship this feature to production") == IntentType.EXEC_REQUEST

    def test_do_it_phrase(self):
        assert classify_intent("Do it now") == IntentType.EXEC_REQUEST

    def test_go_ahead_phrase(self):
        assert classify_intent("Go ahead with the changes") == IntentType.EXEC_REQUEST

    def test_fix_keyword(self):
        assert classify_intent("Fix the broken CSS") == IntentType.EXEC_REQUEST

    def test_refactor_keyword(self):
        assert classify_intent("Refactor the user service") == IntentType.EXEC_REQUEST


# =========================================================================
# Mixed Requests
# =========================================================================


class TestMixedRequest:
    def test_plan_and_implement(self):
        assert classify_intent("Plan and implement the auth system") == IntentType.MIXED_REQUEST

    def test_design_and_deploy(self):
        assert classify_intent("Design the API and deploy it") == IntentType.MIXED_REQUEST

    def test_blueprint_and_build(self):
        assert classify_intent("Create a blueprint then build the service") == IntentType.MIXED_REQUEST

    def test_approach_and_code(self):
        assert classify_intent("What approach should we take? Then code it.") == IntentType.MIXED_REQUEST

    def test_strategy_and_execute(self):
        assert classify_intent("Develop a strategy and execute it") == IntentType.MIXED_REQUEST


# =========================================================================
# Knowledge Requests
# =========================================================================


class TestKnowledgeRequest:
    def test_what_is(self):
        assert classify_intent("What is a microservice?") == IntentType.KNOWLEDGE_REQUEST

    def test_explain(self):
        assert classify_intent("Explain how OAuth works") == IntentType.KNOWLEDGE_REQUEST

    def test_why_does(self):
        assert classify_intent("Why does this error occur?") == IntentType.KNOWLEDGE_REQUEST

    def test_describe(self):
        assert classify_intent("Describe the difference between REST and GraphQL") == IntentType.KNOWLEDGE_REQUEST

    def test_tell_me_about(self):
        assert classify_intent("Tell me about Docker networking") == IntentType.KNOWLEDGE_REQUEST

    def test_can_you_explain(self):
        assert classify_intent("Can you explain dependency injection?") == IntentType.KNOWLEDGE_REQUEST


# =========================================================================
# Default Behavior (Uncertain → AMBIGUOUS)
# =========================================================================


class TestDefaultBehavior:
    def test_empty_string(self):
        assert classify_intent("") == IntentType.AMBIGUOUS

    def test_whitespace_only(self):
        assert classify_intent("   ") == IntentType.AMBIGUOUS

    def test_short_ambiguous(self):
        assert classify_intent("hello") == IntentType.AMBIGUOUS

    def test_gibberish(self):
        assert classify_intent("asdf jkl xyz") == IntentType.AMBIGUOUS

    def test_none_like_input(self):
        # Very short input with no matching keywords defaults to AMBIGUOUS
        assert classify_intent("ok") == IntentType.AMBIGUOUS


# =========================================================================
# Edge Cases
# =========================================================================


class TestEdgeCases:
    def test_case_insensitive(self):
        assert classify_intent("PLAN the migration") == IntentType.PLAN_REQUEST

    def test_mixed_case(self):
        assert classify_intent("DeSign the System") == IntentType.PLAN_REQUEST

    def test_with_punctuation(self):
        assert classify_intent("Can you plan this?") == IntentType.PLAN_REQUEST

    def test_execution_with_exclamation(self):
        assert classify_intent("Deploy now!") == IntentType.EXEC_REQUEST

    def test_planning_phrase_in_sentence(self):
        assert classify_intent("I need you to think through the problem") == IntentType.PLAN_REQUEST

    def test_execution_phrase_in_sentence(self):
        assert classify_intent("Please set it up for me") == IntentType.EXEC_REQUEST

    def test_knowledge_takes_precedence_over_ambiguous(self):
        # "compare" is a knowledge keyword
        assert classify_intent("Compare the two options for me") == IntentType.KNOWLEDGE_REQUEST


# =========================================================================
# Keyword Dictionaries Sanity Checks
# =========================================================================


class TestKeywordDictionaries:
    def test_planning_keywords_are_frozenset(self):
        assert isinstance(PLANNING_KEYWORDS, frozenset)

    def test_execution_keywords_are_frozenset(self):
        assert isinstance(EXECUTION_KEYWORDS, frozenset)

    def test_knowledge_keywords_are_frozenset(self):
        assert isinstance(KNOWLEDGE_KEYWORDS, frozenset)

    def test_no_overlap_plan_exec(self):
        overlap = PLANNING_KEYWORDS & EXECUTION_KEYWORDS
        assert len(overlap) == 0, f"Overlap between planning and execution: {overlap}"

    def test_planning_keywords_non_empty(self):
        assert len(PLANNING_KEYWORDS) > 0

    def test_execution_keywords_non_empty(self):
        assert len(EXECUTION_KEYWORDS) > 0

    def test_knowledge_keywords_non_empty(self):
        assert len(KNOWLEDGE_KEYWORDS) > 0


# =========================================================================
# Return Type Validation
# =========================================================================


class TestReturnTypes:
    def test_returns_intent_type(self):
        result = classify_intent("Design a system")
        assert isinstance(result, IntentType)

    def test_all_possible_returns_are_intent_type(self):
        """Ensure various inputs all return IntentType instances."""
        inputs = [
            "plan the API",
            "deploy it",
            "plan and deploy",
            "what is REST?",
            "asdfghjkl",
        ]
        for text in inputs:
            result = classify_intent(text)
            assert isinstance(result, IntentType), f"Failed for input: {text}"
