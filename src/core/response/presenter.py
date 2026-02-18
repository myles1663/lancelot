"""
Response Presenter — converts structured agentic output to readable chat text (V23).

The presentation layer sits between the agentic loop's structured JSON output and
the user-facing chat response. It:

1. Cross-references actions_taken against tool receipts (drops unverified actions)
2. Optionally runs ClaimVerifier on the free-text response_to_user field
3. Formats verified output into clean, readable chat text

This module is the structural replacement for prompt-level honesty enforcement.
Instead of asking the model not to hallucinate, we verify its claims against
physical evidence (tool receipts) before the user sees anything.

Public API:
    ResponsePresenter(claim_verification=False)
    presenter.present(structured, receipts) -> str
    presenter.present_fallback(raw_text, receipts) -> str
"""

import json
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


# The JSON schema that the agentic loop enforces via Gemini structured output.
# Defined here as the single source of truth — imported by the orchestrator.
AGENTIC_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "thinking": {
            "type": "STRING",
            "description": "Internal reasoning (not shown to user)",
            "nullable": True,
        },
        "actions_taken": {
            "type": "ARRAY",
            "description": "List of tool actions performed this turn",
            "nullable": True,
            "items": {
                "type": "OBJECT",
                "properties": {
                    "tool": {
                        "type": "STRING",
                        "description": "Tool/skill name that was called",
                    },
                    "summary": {
                        "type": "STRING",
                        "description": "Brief description of what was done",
                    },
                    "status": {
                        "type": "STRING",
                        "description": "Outcome of the action",
                        "enum": ["success", "failed", "pending_approval"],
                    },
                },
                "required": ["tool", "summary", "status"],
            },
        },
        "response_to_user": {
            "type": "STRING",
            "description": "The direct answer or update for the user",
        },
        "next_action": {
            "type": "STRING",
            "description": "Whether the agentic loop should continue",
            "enum": ["done", "continue", "needs_approval"],
        },
    },
    "required": ["response_to_user", "next_action"],
}


class ResponsePresenter:
    """Converts structured agentic output to readable chat text.

    This is NOT just a formatter — it's a verifier + formatter. Actions claimed
    in the structured output are cross-referenced against tool receipts before
    being included in the user-facing response.
    """

    def __init__(self, claim_verification: bool = False):
        """Initialize the presenter.

        Args:
            claim_verification: If True, also run ClaimVerifier on the free-text
                response_to_user field. Requires FEATURE_CLAIM_VERIFICATION flag.
        """
        self._claim_verification = claim_verification

    def present(self, structured: dict, receipts: List[dict]) -> str:
        """Convert verified structured output to natural chat response.

        Args:
            structured: Parsed JSON dict from the agentic loop's structured output.
                Expected keys: response_to_user, actions_taken (optional),
                next_action, thinking (optional).
            receipts: List of tool receipt dicts from the agentic loop.
                Each has keys: skill, inputs, result, and optionally outputs.

        Returns:
            Clean, readable chat text for the user.
        """
        response_text = structured.get("response_to_user", "")
        actions = structured.get("actions_taken") or []
        next_action = structured.get("next_action", "done")

        # 1. Verify actions_taken against receipts
        verified_actions = self._verify_actions(actions, receipts)

        # 2. Optionally verify free-text claims
        if self._claim_verification and response_text:
            try:
                from response.claim_verifier import ClaimVerifier
                verifier = ClaimVerifier()
                result = verifier.verify(response_text, receipts)
                if not result.is_clean:
                    logger.info(
                        "V23 presenter: %d claims flagged in response_to_user",
                        len(result.flagged_claims),
                    )
                response_text = result.cleaned_text
            except Exception as e:
                logger.warning("V23 claim verification failed: %s", e)

        # 3. Build final chat output
        return self._format_chat(response_text, verified_actions, next_action)

    def present_fallback(self, raw_text: str, receipts: List[dict]) -> str:
        """Fallback presenter for when structured output parsing fails.

        Runs claim verification on raw text if enabled, otherwise returns as-is.

        Args:
            raw_text: Raw text response from the model.
            receipts: Tool receipt dicts from the agentic loop.

        Returns:
            Cleaned text (or original if claim verification is disabled).
        """
        if not self._claim_verification or not raw_text:
            return raw_text

        try:
            from response.claim_verifier import ClaimVerifier
            verifier = ClaimVerifier()
            result = verifier.verify(raw_text, receipts)
            return result.cleaned_text
        except Exception as e:
            logger.warning("V23 fallback claim verification failed: %s", e)
            return raw_text

    def _verify_actions(
        self, actions: List[dict], receipts: List[dict]
    ) -> List[dict]:
        """Cross-reference actions_taken against tool receipts.

        Only actions with a matching receipt are included in the output.
        Actions the model claims but that have no receipt are silently dropped.

        Args:
            actions: List of action dicts from structured output.
            receipts: List of tool receipt dicts from the agentic loop.

        Returns:
            List of verified action dicts (subset of input).
        """
        if not actions:
            return []

        # Build lookup: tool name -> list of receipts for that tool
        receipt_by_tool = {}
        for r in receipts:
            skill = r.get("skill", "")
            receipt_by_tool.setdefault(skill, []).append(r)

        verified = []
        for action in actions:
            tool = action.get("tool", "")
            claimed_status = action.get("status", "")

            if tool not in receipt_by_tool:
                # No receipt for this tool — the model hallucinated this action
                logger.info(
                    "V23 presenter: dropped hallucinated action '%s' (no receipt)",
                    tool,
                )
                continue

            # Verify the claimed status matches reality
            tool_receipts = receipt_by_tool[tool]
            actual_success = any(
                "SUCCESS" in str(r.get("result", "")) for r in tool_receipts
            )
            actual_failed = any(
                "FAILED" in str(r.get("result", "")) or "EXCEPTION" in str(r.get("result", ""))
                for r in tool_receipts
            )
            actual_escalated = any(
                "ESCALATED" in str(r.get("result", "")) for r in tool_receipts
            )

            # Correct the status if the model lied about it
            if claimed_status == "success" and not actual_success:
                if actual_failed:
                    action = {**action, "status": "failed"}
                    logger.info(
                        "V23 presenter: corrected '%s' status from success to failed",
                        tool,
                    )
                elif actual_escalated:
                    action = {**action, "status": "pending_approval"}
                else:
                    # Receipt exists but unclear status — keep claimed
                    pass

            verified.append(action)

        dropped = len(actions) - len(verified)
        if dropped:
            logger.info("V23 presenter: dropped %d/%d unverified actions", dropped, len(actions))

        return verified

    def _format_chat(
        self,
        response_text: str,
        verified_actions: List[dict],
        next_action: str,
    ) -> str:
        """Format verified output into clean chat text.

        Args:
            response_text: The verified response_to_user text.
            verified_actions: List of verified action dicts.
            next_action: "done", "continue", or "needs_approval".

        Returns:
            Formatted chat text.
        """
        parts = []

        # Main response text is always included
        if response_text:
            parts.append(response_text.strip())

        # Append a brief action summary if there were verified actions
        if verified_actions:
            action_lines = []
            for action in verified_actions:
                status_icon = {
                    "success": "+",
                    "failed": "x",
                    "pending_approval": "?",
                }.get(action.get("status", ""), "-")
                summary = action.get("summary", action.get("tool", ""))
                action_lines.append(f"  [{status_icon}] {summary}")

            if action_lines:
                # Only add action summary if not already described in response text
                # (avoid redundancy when the model's response already covers the actions)
                if not self._actions_described_in_text(response_text, verified_actions):
                    parts.append("\n" + "\n".join(action_lines))

        # Approval prompt if needed
        if next_action == "needs_approval":
            parts.append("\nApprove or Deny?")

        return "\n".join(parts) if parts else "Done."

    def _actions_described_in_text(
        self, text: str, actions: List[dict]
    ) -> bool:
        """Heuristic: check if the response text already describes the actions.

        Returns True if most action summaries or tool names appear in the text,
        meaning an explicit action list would be redundant.
        """
        if not text or not actions:
            return False

        text_lower = text.lower()
        described = 0
        for action in actions:
            tool = action.get("tool", "").lower().replace("_", " ")
            summary = action.get("summary", "").lower()
            # Check if the tool name or key words from summary appear in text
            summary_words = [w for w in summary.split() if len(w) > 3]
            tool_mentioned = tool in text_lower
            summary_mentioned = sum(1 for w in summary_words if w in text_lower) >= len(summary_words) * 0.5
            if tool_mentioned or summary_mentioned:
                described += 1

        return described >= len(actions) * 0.5


def parse_structured_response(raw_text: str) -> Optional[dict]:
    """Parse a structured JSON response from the model.

    Returns the parsed dict if valid, or None if parsing fails.
    This is the single entry point for JSON parsing of structured output,
    ensuring consistent error handling.
    """
    if not raw_text:
        return None

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        # Try stripping markdown code fences
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()
            try:
                parsed = json.loads(cleaned)
            except json.JSONDecodeError:
                return None
        else:
            return None

    # Validate required fields
    if not isinstance(parsed, dict):
        return None
    if "response_to_user" not in parsed:
        return None

    return parsed
