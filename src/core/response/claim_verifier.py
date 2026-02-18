"""
Claim Verifier — cross-references response text claims against tool receipts (V23).

Scans the response_to_user free-text field for action claims (past-tense verbs
implying completed tool use) and validates each claim against the tool receipts
from the current agentic loop turn.

Unverified claims — text asserting an action that has no matching receipt — are
neutralized before reaching the user.

Public API:
    ClaimVerifier()
    verifier.verify(response_text, receipts) -> VerificationResult
"""

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Claim:
    """A detected action claim in response text."""
    verb: str                # e.g. "sent", "searched", "created"
    span: tuple              # (start, end) character indices in text
    sentence: str            # The full sentence containing the claim
    matched_receipt: bool = False


@dataclass
class VerificationResult:
    """Result of claim verification against tool receipts."""
    is_clean: bool                          # True if no unverified claims found
    flagged_claims: List[str] = field(default_factory=list)  # Unverified claim descriptions
    cleaned_text: str = ""                  # Text with unverified claims neutralized


# Mapping of past-tense action verbs to the tool names that would produce them.
# Only verbs that imply a COMPLETED action are included — "found", "see", "know"
# are observations, not actions.
ACTION_VERB_TO_TOOLS = {
    # Communication
    "sent": {"telegram_send", "warroom_send", "email.send", "email_send"},
    "emailed": {"email.send", "email_send"},
    "messaged": {"telegram_send", "warroom_send"},
    "notified": {"telegram_send", "warroom_send", "email.send", "email_send"},
    "posted": {"telegram_send", "warroom_send"},
    # Search
    "searched": {"web_search", "knowledge_search", "rag_search", "network_client"},
    # File/content creation
    "created": {"document_creator", "repo_writer"},
    "wrote": {"document_creator", "repo_writer"},
    "saved": {"document_creator", "repo_writer"},
    "generated": {"document_creator", "repo_writer"},
    # Deployment/execution
    "deployed": {"service_runner"},
    "executed": {"command_runner"},
    "ran": {"command_runner"},
    "installed": {"command_runner"},
    # Scheduling
    "scheduled": {"schedule_job"},
    # Deletion
    "deleted": {"repo_writer", "command_runner"},
    "removed": {"repo_writer", "command_runner"},
}

# Regex to split text into sentences (handles common abbreviations).
_SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+')

# Pattern: "I <verb>" where verb is past tense action claim
_CLAIM_PATTERN = re.compile(
    r"\bI\s+(?:have\s+|already\s+|successfully\s+|just\s+)*"
    r"(" + "|".join(re.escape(v) for v in ACTION_VERB_TO_TOOLS) + r")\b",
    re.IGNORECASE,
)


class ClaimVerifier:
    """Cross-references response text against tool receipts.

    Designed to catch cases where the LLM claims to have performed an action
    (e.g. "I sent the email") without a corresponding tool receipt proving it
    actually happened.
    """

    def verify(self, response_text: str, receipts: List[dict]) -> VerificationResult:
        """Check response claims against actual tool receipts.

        Args:
            response_text: The response_to_user text from structured output.
            receipts: List of tool receipt dicts from the agentic loop.
                Each has keys: skill, inputs, result, and optionally outputs.

        Returns:
            VerificationResult with is_clean flag, flagged claims, and cleaned text.
        """
        if not response_text:
            return VerificationResult(is_clean=True, cleaned_text="")

        claims = self._extract_claims(response_text)

        if not claims:
            return VerificationResult(is_clean=True, cleaned_text=response_text)

        # Build set of tools that were actually called successfully
        successful_tools = set()
        all_called_tools = set()
        for r in receipts:
            skill = r.get("skill", "")
            all_called_tools.add(skill)
            result_str = str(r.get("result", ""))
            if "SUCCESS" in result_str:
                successful_tools.add(skill)

        # Verify each claim
        flagged = []
        for claim in claims:
            expected_tools = ACTION_VERB_TO_TOOLS.get(claim.verb.lower(), set())
            # Check if ANY of the expected tools were called successfully
            if expected_tools & successful_tools:
                claim.matched_receipt = True
            else:
                claim.matched_receipt = False
                flagged.append(f"'{claim.verb}' — no matching tool receipt")
                logger.info(
                    "V23 claim verification: flagged '%s' (expected tools: %s, "
                    "successful: %s)",
                    claim.verb, expected_tools, successful_tools,
                )

        if not flagged:
            return VerificationResult(
                is_clean=True,
                flagged_claims=[],
                cleaned_text=response_text,
            )

        # Neutralize unverified claims
        cleaned = self._neutralize_claims(response_text, claims)
        logger.info("V23 claim verification: %d claims flagged", len(flagged))

        return VerificationResult(
            is_clean=False,
            flagged_claims=flagged,
            cleaned_text=cleaned,
        )

    def _extract_claims(self, text: str) -> List[Claim]:
        """Find action verbs implying completed tool use."""
        claims = []
        sentences = _SENTENCE_SPLIT.split(text)

        char_offset = 0
        for sentence in sentences:
            for match in _CLAIM_PATTERN.finditer(sentence):
                verb = match.group(1).lower()
                abs_start = char_offset + match.start()
                abs_end = char_offset + match.end()
                claims.append(Claim(
                    verb=verb,
                    span=(abs_start, abs_end),
                    sentence=sentence.strip(),
                ))
            char_offset += len(sentence) + 1  # +1 for the split whitespace

        return claims

    def _neutralize_claims(self, text: str, claims: List[Claim]) -> str:
        """Remove or soften sentences with unverified claims.

        Strategy:
        - If the sentence is the entire response, soften the claim verb
        - If the sentence is one of many, remove it entirely
        """
        sentences = _SENTENCE_SPLIT.split(text)
        unverified_sentences = {
            c.sentence for c in claims if not c.matched_receipt
        }

        if len(sentences) <= 1:
            # Single sentence — soften instead of removing
            result = text
            for claim in claims:
                if not claim.matched_receipt:
                    # Replace "I sent" with "I attempted to send"
                    pattern = re.compile(
                        r"\bI\s+(have\s+|already\s+|successfully\s+|just\s+)*"
                        + re.escape(claim.verb),
                        re.IGNORECASE,
                    )
                    result = pattern.sub(
                        f"I was unable to confirm that I {claim.verb}",
                        result,
                        count=1,
                    )
            return result

        # Multiple sentences — remove unverified claim sentences
        cleaned_sentences = []
        for sentence in sentences:
            stripped = sentence.strip()
            if stripped not in unverified_sentences:
                cleaned_sentences.append(sentence)

        result = " ".join(cleaned_sentences).strip()
        return result if result else text  # Never return empty
