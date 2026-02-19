"""
Output Hygiene Policies — enforces chat cleanliness and War Room routing.

Rules:
- Chat output: goal (1 line) + plan summary (<=15 lines) + status (1-3 lines)
  + next actions (<=5 bullets) + optional permission prompt.
- Verbose content (assumptions, risks, decision points, tool traces, verifier
  output) routes to War Room by default.
"""

from __future__ import annotations

import re
from typing import List


    # ── Regex patterns for stripping internal tool scaffolding ──
_TOOL_SCAFFOLDING = re.compile(
    r"\s*\(Tool:\s*\w+,?\s*Params:\s*[^)]*\)",
    re.IGNORECASE,
)
_MODEL_REFERENCE = re.compile(
    r",?\s*model=[\w.\-]+",
    re.IGNORECASE,
)
_USER_MESSAGE_PARAM = re.compile(
    r",?\s*user_message=[^,\n)]+",
    re.IGNORECASE,
)

# ── Gemini tool-call syntax (Fix Pack V3) ──
# "Action:I will now browse..." prefix line
_ACTION_PREFIX = re.compile(
    r"^Action:\s?.*$",
    re.MULTILINE,
)
# ```Tool_Code ... ``` fenced blocks
_TOOL_CODE_BLOCK = re.compile(
    r"```(?:Tool_Code|tool_code)?\s*\n.*?```",
    re.DOTALL,
)
# Unfenced Tool_Code blocks (plain text)
_TOOL_CODE_INLINE = re.compile(
    r"^Tool_Code\s*\n.*?(?=\n\n|\Z)",
    re.MULTILINE | re.DOTALL,
)
# Function call syntax: print(google_search.search(...))
_FUNCTION_CALL = re.compile(
    r"print\s*\([^)]*\)",
    re.IGNORECASE,
)


class OutputPolicy:
    """Defines limits and routing rules for response output."""

    MAX_CHAT_LINES = 80
    MAX_PLAN_SUMMARY_LINES = 15
    MAX_NEXT_ACTIONS = 5
    MAX_STATUS_LINES = 3

    # Sections that are always routed to War Room (never in chat by default)
    VERBOSE_SECTIONS = [
        "assumptions",
        "decision_points",
        "risks",
        "tool_traces",
        "verifier_output",
        "full_plan_artifact",
        "security_log",
    ]

    # Markdown section headers that indicate verbose content to route to War Room.
    # Keep in chat: Goal, Plan Steps, Next Action
    # Route to War Room: everything else
    _VERBOSE_HEADERS = re.compile(
        r"^##\s*(Assumptions|Decision\s+Points|Risks|Done\s+When|Context|"
        r"MVP\s+Path|Test\s+Plan|Estimate|References)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )

    # Section headers that are allowed in chat output
    # V26: Broadened to include common research/analysis headers
    _CHAT_HEADERS = re.compile(
        r"^##\s*(Goal|Plan\s+Steps|Next\s+Action|Summary|Executive\s+Summary|"
        r"Findings|Analysis|Comparison|Competitive\s+Analysis|Recommendations|"
        r"Roadmap\s+Impact|Key\s+Differences|Architecture|Features|Overview)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )

    @staticmethod
    def strip_tool_scaffolding(text: str) -> str:
        """Remove internal tool call syntax, model refs, and LLM params from text.

        This is a safety-net filter that catches inline scaffolding that
        section-based extraction would miss (e.g. '(Tool: x, Params: y=z)').
        """
        text = _TOOL_SCAFFOLDING.sub("", text)
        text = _MODEL_REFERENCE.sub("", text)
        text = _USER_MESSAGE_PARAM.sub("", text)
        # Gemini tool-call syntax (Fix Pack V3)
        text = _ACTION_PREFIX.sub("", text)
        text = _TOOL_CODE_BLOCK.sub("", text)
        text = _TOOL_CODE_INLINE.sub("", text)
        text = _FUNCTION_CALL.sub("", text)
        # Clean up residual empty parens, trailing commas, excess blank lines
        text = re.sub(r"\(\s*\)", "", text)
        text = re.sub(r",\s*$", "", text, flags=re.MULTILINE)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text

    @staticmethod
    def should_route_to_war_room(section_name: str) -> bool:
        """Return True if this section should go to War Room, not chat."""
        return section_name.lower() in OutputPolicy.VERBOSE_SECTIONS

    @staticmethod
    def enforce_chat_limits(text: str) -> str:
        """Truncate chat output to MAX_CHAT_LINES with an overflow indicator."""
        lines = text.split("\n")
        if len(lines) <= OutputPolicy.MAX_CHAT_LINES:
            return text
        truncated = lines[:OutputPolicy.MAX_CHAT_LINES]
        truncated.append("\n*(Full details available in War Room)*")
        return "\n".join(truncated)

    @staticmethod
    def extract_verbose_sections(markdown: str) -> tuple:
        """Split markdown into (chat_content, verbose_content).

        Uses a whitelist approach: only known chat sections (Goal, Plan Steps,
        Next Action) stay in chat. All other ## sections route to War Room.
        Non-section content (no ## header) stays in chat.

        Returns:
            (chat_md, verbose_md) where verbose_md contains assumptions,
            risks, decision points, etc.
        """
        chat_parts: List[str] = []
        verbose_parts: List[str] = []

        sections = re.split(r"(?=^## )", markdown, flags=re.MULTILINE)

        for section in sections:
            if not section.strip():
                continue
            # If it starts with ##, check if it's a known chat section
            if section.strip().startswith("## "):
                if OutputPolicy._CHAT_HEADERS.search(section):
                    chat_parts.append(section.strip())
                else:
                    # All other ## sections go to War Room
                    verbose_parts.append(section.strip())
            else:
                # Non-section content (no ## header) stays in chat
                chat_parts.append(section.strip())

        chat_md = "\n\n".join(chat_parts)
        verbose_md = "\n\n".join(verbose_parts)
        return chat_md, verbose_md

    @staticmethod
    def trim_plan_summary(plan_lines: List[str]) -> List[str]:
        """Trim plan summary to MAX_PLAN_SUMMARY_LINES."""
        if len(plan_lines) <= OutputPolicy.MAX_PLAN_SUMMARY_LINES:
            return plan_lines
        return plan_lines[:OutputPolicy.MAX_PLAN_SUMMARY_LINES] + [
            f"  *(+{len(plan_lines) - OutputPolicy.MAX_PLAN_SUMMARY_LINES} more steps — see War Room)*"
        ]

    @staticmethod
    def trim_next_actions(actions: List[str]) -> List[str]:
        """Trim next actions to MAX_NEXT_ACTIONS."""
        if len(actions) <= OutputPolicy.MAX_NEXT_ACTIONS:
            return actions
        return actions[:OutputPolicy.MAX_NEXT_ACTIONS]
