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


class OutputPolicy:
    """Defines limits and routing rules for response output."""

    MAX_CHAT_LINES = 25
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

    # Markdown section headers that indicate verbose content
    _VERBOSE_HEADERS = re.compile(
        r"^##\s*(Assumptions|Decision\s+Points|Risks|Done\s+When|Context)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )

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
            if OutputPolicy._VERBOSE_HEADERS.search(section):
                verbose_parts.append(section.strip())
            else:
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
