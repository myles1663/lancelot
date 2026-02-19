"""
Reasoning Artifact — Data structures for the Autonomy Loop v2 (V25).

Provides:
    ReasoningArtifact  — output of the deep reasoning pass (Phase 1)
    TaskExperience     — episodic record of a completed task (Phase 6)
    GovernanceFeedback — structured feedback when Sentry blocks an action (Phase 3)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Phase 1: Deep Reasoning Pass
# ---------------------------------------------------------------------------

@dataclass
class ReasoningArtifact:
    """Output of the deep reasoning pass before agentic execution.

    Contains the model's analysis of the task, proposed approaches,
    and any capability gaps identified during reasoning.
    """
    reasoning_text: str
    model_used: str
    thinking_level: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    token_count_estimate: int = 0
    capability_gaps: List[str] = field(default_factory=list)

    def to_context_block(self) -> str:
        """Format as a context block for injection into the agentic loop."""
        lines = [
            "--- DEEP REASONING (Phase 1 Analysis) ---",
            self.reasoning_text,
        ]
        if self.capability_gaps:
            lines.append("\nCAPABILITY GAPS IDENTIFIED:")
            for gap in self.capability_gaps:
                lines.append(f"  - {gap}")
        lines.append("--- END DEEP REASONING ---")
        return "\n".join(lines)

    @staticmethod
    def parse_capability_gaps(text: str) -> List[str]:
        """Extract CAPABILITY GAP markers from reasoning text."""
        gaps = []
        for match in re.finditer(r"CAPABILITY GAP:\s*(.+?)(?:\n|$)", text):
            gap = match.group(1).strip()
            if gap and len(gap) > 5:
                gaps.append(gap)
        return gaps


# ---------------------------------------------------------------------------
# Phase 6: Task Experience Memory
# ---------------------------------------------------------------------------

@dataclass
class TaskExperience:
    """Episodic record of a completed task for future learning.

    Stored in episodic memory so the reasoning pass on future tasks
    can learn from what worked and what didn't.
    """
    task_summary: str
    approach_taken: str
    tools_used: List[str] = field(default_factory=list)
    tools_succeeded: List[str] = field(default_factory=list)
    tools_failed: List[str] = field(default_factory=list)
    actions_blocked: List[str] = field(default_factory=list)
    outcome: str = ""
    reasoning_was_used: bool = False
    duration_ms: float = 0.0
    capability_gaps: List[str] = field(default_factory=list)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_memory_content(self) -> str:
        """Format for storage in episodic memory."""
        lines = [
            f"Task: {self.task_summary}",
            f"Approach: {self.approach_taken}",
            f"Outcome: {self.outcome}",
        ]
        if self.tools_used:
            lines.append(f"Tools used: {', '.join(self.tools_used)}")
        if self.tools_failed:
            lines.append(f"Tools that failed: {', '.join(self.tools_failed)}")
        if self.actions_blocked:
            lines.append(f"Actions blocked by governance: {', '.join(self.actions_blocked)}")
        if self.capability_gaps:
            lines.append(f"Capability gaps: {', '.join(self.capability_gaps)}")
        if self.reasoning_was_used:
            lines.append("Deep reasoning pass was used for this task.")
        return "\n".join(lines)

    @staticmethod
    def from_tool_receipts(receipts: List[Dict[str, Any]]) -> dict:
        """Extract tool usage stats from agentic loop receipts."""
        used = []
        succeeded = []
        failed = []
        blocked = []
        for r in receipts:
            skill = r.get("skill", "unknown")
            result = str(r.get("result", ""))
            used.append(skill)
            if "SUCCESS" in result:
                succeeded.append(skill)
            elif "ESCALATED" in result:
                blocked.append(skill)
            elif "FAILED" in result or "EXCEPTION" in result or "REJECTED" in result:
                failed.append(skill)
        return {
            "tools_used": list(dict.fromkeys(used)),
            "tools_succeeded": list(dict.fromkeys(succeeded)),
            "tools_failed": list(dict.fromkeys(failed)),
            "actions_blocked": list(dict.fromkeys(blocked)),
        }


# ---------------------------------------------------------------------------
# Phase 3: Governed Negotiation
# ---------------------------------------------------------------------------

@dataclass
class GovernanceFeedback:
    """Structured feedback when the Sentry blocks a tool call.

    Instead of a generic 'BLOCKED' message, provides the model with
    actionable information about why the action was blocked, what its
    current permissions are, and what alternatives exist.
    """
    skill_name: str
    action_detail: str
    blocked_reason: str
    permission_state: str
    trust_record_summary: str = ""
    alternatives: List[str] = field(default_factory=list)
    resolution_hint: str = ""
    request_id: str = ""

    def to_tool_result(self) -> str:
        """Format as a structured tool result for the LLM to reason about."""
        lines = [
            f"GOVERNANCE FEEDBACK for {self.skill_name}:",
            f"  Action attempted: {self.action_detail}",
            f"  Blocked because: {self.blocked_reason}",
            f"  Permission state: {self.permission_state}",
        ]
        if self.trust_record_summary:
            lines.append(f"  Trust record: {self.trust_record_summary}")
        if self.alternatives:
            lines.append("  Suggested alternatives:")
            for alt in self.alternatives:
                lines.append(f"    - {alt}")
        if self.resolution_hint:
            lines.append(f"  How to resolve: {self.resolution_hint}")
        if self.request_id:
            lines.append(f"  Approval request ID: {self.request_id}")
        lines.append(
            "  INSTRUCTION: Adapt your approach using the alternatives above. "
            "Do NOT repeat the blocked action. Find a compliant way to achieve "
            "the same goal, or note the limitation in your response."
        )
        return "\n".join(lines)
