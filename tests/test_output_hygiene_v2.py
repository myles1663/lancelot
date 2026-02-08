"""
Tests for Fix Pack V2+V3 — Output Hygiene: tool parameter + Gemini syntax stripping.

Validates that internal tool call syntax (Tool:, Params:, model=, user_message=)
and Gemini tool-call syntax (Action:, Tool_Code, print()) never reach the user.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "integrations"))


# ---------------------------------------------------------------------------
# OutputPolicy.strip_tool_scaffolding
# ---------------------------------------------------------------------------

class TestStripToolScaffolding:
    """Tests for the safety-net regex filter in OutputPolicy."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from response.policies import OutputPolicy
        self.policy = OutputPolicy

    def test_strips_tool_params_simple(self):
        text = "1. Research voice options (Tool: search_workspace, Params: query=voice)"
        result = self.policy.strip_tool_scaffolding(text)
        assert "(Tool:" not in result
        assert "Params:" not in result
        assert "1. Research voice options" in result

    def test_strips_tool_params_complex(self):
        text = (
            "1. Search for solutions (Tool: search_workspace, Params: "
            "query=voice communication iPhone iPad PC secure two users)"
        )
        result = self.policy.strip_tool_scaffolding(text)
        assert "(Tool:" not in result
        assert "query=" not in result

    def test_strips_model_reference(self):
        text = "Use the chat system, model=gemini-2.0-flash"
        result = self.policy.strip_tool_scaffolding(text)
        assert "model=gemini" not in result

    def test_strips_model_in_param_list(self):
        text = "Based on the research, model=gemini-2.0-flash)"
        result = self.policy.strip_tool_scaffolding(text)
        assert "model=" not in result

    def test_strips_user_message_param(self):
        text = (
            "(Tool: chat_generation, Params: user_message=Based on the research "
            "which voice platform is best, model=gemini-2.0-flash)"
        )
        result = self.policy.strip_tool_scaffolding(text)
        assert "user_message=" not in result
        assert "Tool:" not in result
        assert "model=" not in result

    def test_preserves_clean_text(self):
        text = "Research voice communication options compatible with iPhone, iPad, and PC."
        result = self.policy.strip_tool_scaffolding(text)
        assert result == text

    def test_multiple_tool_refs_stripped(self):
        text = (
            "1. Search (Tool: search_workspace, Params: query=test)\n"
            "2. Analyze (Tool: chat_generation, Params: user_message=analyze, model=gemini-2.0-flash)\n"
            "3. Report results"
        )
        result = self.policy.strip_tool_scaffolding(text)
        assert "(Tool:" not in result
        assert "model=" not in result
        assert "1. Search" in result
        assert "2. Analyze" in result
        assert "3. Report results" in result

    def test_empty_string(self):
        assert self.policy.strip_tool_scaffolding("") == ""

    def test_cleans_residual_empty_parens(self):
        text = "Do something ()"
        result = self.policy.strip_tool_scaffolding(text)
        assert "()" not in result

    # ── V3: Gemini tool-call syntax ──

    def test_strips_action_prefix_with_space(self):
        text = "Action: I will now browse the internet."
        result = self.policy.strip_tool_scaffolding(text)
        assert "Action:" not in result

    def test_strips_action_prefix_no_space(self):
        """The exact pattern from the screenshot: 'Action:I will now browse'."""
        text = 'Action:I will now browse the internet and search for "Does Slack offer a free use account?".'
        result = self.policy.strip_tool_scaffolding(text)
        assert "Action:" not in result
        assert "browse" not in result

    def test_strips_tool_code_fenced_block(self):
        text = "Here is the result:\n```Tool_Code\nprint(google_search.search(queries=[\"slack free\"]))\n```\nDone."
        result = self.policy.strip_tool_scaffolding(text)
        assert "Tool_Code" not in result
        assert "print(" not in result
        assert "google_search" not in result
        assert "Done." in result

    def test_strips_tool_code_unfenced(self):
        text = "Searching now.\nTool_Code\nprint(google_search.search(queries=[\"test\"]))\n\nHere are results."
        result = self.policy.strip_tool_scaffolding(text)
        assert "Tool_Code" not in result
        assert "print(" not in result
        assert "Here are results." in result

    def test_strips_print_function_call(self):
        text = "print(google_search.search(queries=[\"Does Slack offer a free use account?\"]))"
        result = self.policy.strip_tool_scaffolding(text)
        assert "print(" not in result
        assert "google_search" not in result

    def test_strips_mixed_gemini_output(self):
        """Full Gemini output with Action: + Tool_Code + print()."""
        text = (
            "I'll help you find out.\n\n"
            "Action:I will now browse the internet.\n"
            "```Tool_Code\n"
            "print(google_search.search(queries=[\"slack free account\"]))\n"
            "```"
        )
        result = self.policy.strip_tool_scaffolding(text)
        assert "Action:" not in result
        assert "Tool_Code" not in result
        assert "print(" not in result
        assert "help you find" in result

    def test_clean_text_unaffected_by_v3_patterns(self):
        text = "Slack offers a free tier. You can create an account at slack.com."
        result = self.policy.strip_tool_scaffolding(text)
        assert result.strip() == text


# ---------------------------------------------------------------------------
# plan_task() output format
# ---------------------------------------------------------------------------

class TestPlanTaskOutput:
    """Tests that plan_task() no longer leaks tool/param internals."""

    def test_plan_task_no_tool_syntax(self):
        """Mock a plan and verify output format."""
        from unittest.mock import MagicMock, patch
        from dataclasses import dataclass

        @dataclass
        class FakeParam:
            key: str
            value: str

        @dataclass
        class FakeStep:
            id: int
            description: str
            tool: str
            params: list

        @dataclass
        class FakePlan:
            goal: str
            steps: list

        plan = FakePlan(
            goal="Set up voice communication",
            steps=[
                FakeStep(1, "Research options", "search_workspace",
                         [FakeParam("query", "voice communication")]),
                FakeStep(2, "Select platform", "chat_generation",
                         [FakeParam("user_message", "which platform"), FakeParam("model", "gemini-2.0-flash")]),
            ],
        )

        # Import orchestrator and mock _create_plan
        try:
            from orchestrator import Orchestrator
        except ImportError:
            pytest.skip("Cannot import Orchestrator in test env")

        orch = MagicMock(spec=Orchestrator)
        orch._create_plan = MagicMock(return_value=plan)

        # Call plan_task directly with unbound method
        output = Orchestrator.plan_task(orch, "Set up voice communication")

        assert "(Tool:" not in output
        assert "Params:" not in output
        assert "model=" not in output
        assert "user_message=" not in output
        assert "1. Research options" in output
        assert "2. Select platform" in output
        assert "Plan for: Set up voice communication" in output


# ---------------------------------------------------------------------------
# Telegram sanitization
# ---------------------------------------------------------------------------

class TestTelegramSanitize:
    """Tests for the last-resort Telegram filter."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from telegram_bot import TelegramBot
        self.bot = TelegramBot

    def test_strips_tool_params(self):
        text = "1. Do X (Tool: search_workspace, Params: query=voice)"
        result = self.bot._sanitize_for_telegram(text)
        assert "(Tool:" not in result

    def test_strips_model_ref(self):
        text = "Analysis complete, model=gemini-2.0-flash"
        result = self.bot._sanitize_for_telegram(text)
        assert "model=" not in result

    def test_strips_user_message(self):
        text = "(Tool: chat_gen, Params: user_message=hello world, model=gemini-2.0-flash)"
        result = self.bot._sanitize_for_telegram(text)
        assert "user_message=" not in result
        assert "model=" not in result

    def test_preserves_clean_message(self):
        text = "Here is the plan:\n1. Set up Signal on all devices\n2. Configure encryption"
        result = self.bot._sanitize_for_telegram(text)
        assert result == text

    def test_empty_string(self):
        assert self.bot._sanitize_for_telegram("") == ""

    # ── V3: Gemini tool-call syntax in Telegram ──

    def test_strips_action_prefix_telegram(self):
        text = 'Action:I will now browse the internet and search for "Does Slack offer a free use account?".'
        result = self.bot._sanitize_for_telegram(text)
        assert "Action:" not in result

    def test_strips_tool_code_block_telegram(self):
        text = "Result:\n```Tool_Code\nprint(google_search.search(queries=[\"slack free\"]))\n```\nEnd."
        result = self.bot._sanitize_for_telegram(text)
        assert "Tool_Code" not in result
        assert "print(" not in result
        assert "End." in result

    def test_strips_print_call_telegram(self):
        text = "print(google_search.search(queries=[\"test\"]))"
        result = self.bot._sanitize_for_telegram(text)
        assert "print(" not in result

    def test_strips_full_gemini_leak_telegram(self):
        """Full Gemini tool-call output as seen in screenshots."""
        text = (
            "I'll help.\n\n"
            "Action:I will now browse the internet.\n"
            "```Tool_Code\n"
            "print(google_search.search(queries=[\"slack free account\"]))\n"
            "```"
        )
        result = self.bot._sanitize_for_telegram(text)
        assert "Action:" not in result
        assert "Tool_Code" not in result
        assert "print(" not in result
        assert "help" in result


# ---------------------------------------------------------------------------
# Assembler applies stripping
# ---------------------------------------------------------------------------

class TestAssemblerStripping:
    """Tests that the assembler strips tool scaffolding from raw markdown."""

    def test_assembler_strips_tool_params_from_markdown(self):
        from response.assembler import ResponseAssembler

        raw = (
            "Plan for: Set up voice communication\n"
            "1. Research options (Tool: search_workspace, Params: query=voice)\n"
            "2. Select platform (Tool: chat_generation, Params: user_message=which, model=gemini-2.0-flash)"
        )

        assembler = ResponseAssembler()
        result = assembler.assemble(raw_planner_output=raw)

        assert "(Tool:" not in result.chat_response
        assert "Params:" not in result.chat_response
        assert "model=" not in result.chat_response
        assert "1. Research options" in result.chat_response

    def test_assembler_clean_input_unchanged(self):
        from response.assembler import ResponseAssembler

        raw = "Plan for: Set up voice communication\n1. Research options\n2. Select platform"
        assembler = ResponseAssembler()
        result = assembler.assemble(raw_planner_output=raw)
        assert "1. Research options" in result.chat_response
        assert "2. Select platform" in result.chat_response
