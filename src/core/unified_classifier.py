"""
Unified Intent Classifier — single LLM call replaces 7 heuristic functions (V23).

Uses Gemini structured output to classify user intent in a single call with
a JSON schema response. Replaces the V1-V22 chain of:
    classify_intent() -> _verify_intent_with_llm() -> _is_continuation()
    -> _needs_research() -> _is_low_risk_exec() -> _is_conversational()
    -> _is_simple_for_local()

Falls back to the keyword-based classifier if the LLM call fails (network error,
timeout, malformed response, etc.). The keyword classifier is never deleted —
it's the permanent safety net.

Public API:
    UnifiedClassifier(provider)
    classifier.classify(message, history=None) -> ClassificationResult
"""

import json
import logging
import os
from dataclasses import dataclass
from typing import List, Optional

from plan_types import IntentType

logger = logging.getLogger(__name__)


@dataclass
class ClassificationResult:
    """Result of unified intent classification."""
    intent: str                   # question, action_low_risk, action_high_risk,
                                  # plan_request, continuation, conversational
    confidence: float             # 0.0 to 1.0
    is_continuation: bool         # True if this modifies/corrects a previous request
    requires_tools: bool          # True if answering requires calling tools
    reasoning: str = ""           # Brief explanation (for debugging)

    def to_intent_type(self) -> IntentType:
        """Map unified classification to legacy IntentType for routing."""
        mapping = {
            "question": IntentType.KNOWLEDGE_REQUEST,
            "action_low_risk": IntentType.KNOWLEDGE_REQUEST,   # Just-do-it: route to agentic
            "action_high_risk": IntentType.EXEC_REQUEST,       # Needs planning pipeline
            "plan_request": IntentType.PLAN_REQUEST,
            "continuation": IntentType.KNOWLEDGE_REQUEST,      # Corrections route to agentic
            "conversational": IntentType.KNOWLEDGE_REQUEST,
        }
        return mapping.get(self.intent, IntentType.KNOWLEDGE_REQUEST)


# Gemini structured output schema for intent classification.
INTENT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "intent": {
            "type": "STRING",
            "description": "The classified intent of the user's message",
            "enum": [
                "question",
                "action_low_risk",
                "action_high_risk",
                "plan_request",
                "continuation",
                "conversational",
            ],
        },
        "confidence": {
            "type": "NUMBER",
            "description": "Confidence in the classification, from 0.0 to 1.0",
        },
        "is_continuation": {
            "type": "BOOLEAN",
            "description": "True if this modifies, corrects, or redirects a previous request",
        },
        "requires_tools": {
            "type": "BOOLEAN",
            "description": "True if answering this message requires calling external tools",
        },
        "reasoning": {
            "type": "STRING",
            "description": "Brief explanation of why this classification was chosen",
        },
    },
    "required": ["intent", "confidence", "is_continuation", "requires_tools"],
}


# Compact system prompt for the classifier — NOT the full Lancelot system prompt.
# Purpose-built for classification only (~200 tokens).
CLASSIFIER_SYSTEM_PROMPT = """You are an intent classifier for an AI assistant. Classify the user's message into exactly one category.

Categories:
- question: Asking for information, explanation, or clarification. "What is X?", "How does Y work?", "Tell me about Z"
- action_low_risk: Wants something done NOW that is read-only or low-risk. Search, lookup, summarize, analyze, compare, draft, check, list, fetch
- action_high_risk: Wants something done NOW that modifies state. Send email, deploy, delete, install, execute commands, commit, push, restart services
- plan_request: Wants a PLAN, DESIGN, STRATEGY, or ARCHITECTURE — not immediate action. "Plan how to...", "Design a system for...", "What approach should we take?"
- continuation: Modifying, correcting, or redirecting a PREVIOUS request. "No, change it to...", "Actually send that to telegram", "Use the other one", "Wait, not that"
- conversational: Greetings, thanks, small talk, acknowledgments. "Hello", "Thanks", "OK cool", "Got it"

Rules:
- If the message references or corrects something said before, it's "continuation"
- "Search for X" or "Look up X" is action_low_risk, not question
- "Send X to Y" or "Deploy X" is action_high_risk
- Only classify as plan_request if they explicitly want a plan/design/strategy
- Default to "question" when uncertain"""


class UnifiedClassifier:
    """Single-call LLM intent classification with structured output.

    Uses Gemini's response_schema to get a validated JSON response in one call.
    Falls back to the keyword classifier on any failure.
    """

    def __init__(self, provider):
        """Initialize with a ProviderClient instance.

        Args:
            provider: A ProviderClient (typically GeminiProviderClient).
        """
        self._provider = provider
        self._model = os.getenv("CLASSIFIER_MODEL", "gemini-3-flash-preview")

    def classify(
        self,
        message: str,
        history: Optional[List[dict]] = None,
    ) -> ClassificationResult:
        """Classify user intent with a single LLM call.

        Uses Gemini structured output — returns validated JSON, never free text.
        Falls back to keyword classifier on any failure.

        Args:
            message: The user's message text.
            history: Optional recent conversation history for context.
                List of {"role": str, "text": str} dicts.

        Returns:
            ClassificationResult with intent, confidence, is_continuation, requires_tools.
        """
        if not message or not message.strip():
            return ClassificationResult(
                intent="conversational",
                confidence=1.0,
                is_continuation=False,
                requires_tools=False,
                reasoning="Empty message",
            )

        try:
            context_msg = self._build_context(message, history)
            user_msg = self._provider.build_user_message(context_msg)

            result = self._provider.generate(
                model=self._model,
                messages=[user_msg],
                system_instruction=CLASSIFIER_SYSTEM_PROMPT,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": INTENT_SCHEMA,
                },
            )

            parsed = self._parse(result.text)
            if parsed:
                logger.debug(
                    "V23 unified classifier: intent=%s confidence=%.2f reasoning=%s",
                    parsed.intent, parsed.confidence, parsed.reasoning,
                )
                return parsed

            logger.warning("V23 unified classifier: parse failed, falling back to keywords")
            return self._keyword_fallback(message)

        except Exception as e:
            logger.warning("V23 unified classifier failed: %s — falling back to keywords", e)
            return self._keyword_fallback(message)

    def _build_context(self, message: str, history: Optional[List[dict]]) -> str:
        """Build the classifier input with optional conversation context.

        Includes the last few messages for continuation detection, but keeps
        it compact to minimize classification latency.
        """
        parts = []

        if history:
            # Include last 3 messages max for continuation context
            recent = history[-3:]
            for msg in recent:
                role = msg.get("role", "user")
                text = msg.get("text", "")
                if text:
                    # Truncate long messages for classification
                    if len(text) > 200:
                        text = text[:200] + "..."
                    parts.append(f"[{role}]: {text}")
            parts.append("")  # Blank line separator

        parts.append(f"Classify this message: {message}")
        return "\n".join(parts)

    def _parse(self, raw_text: str) -> Optional[ClassificationResult]:
        """Parse the JSON response into a ClassificationResult."""
        if not raw_text:
            return None

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            return None

        if not isinstance(data, dict):
            return None

        intent = data.get("intent", "")
        valid_intents = {
            "question", "action_low_risk", "action_high_risk",
            "plan_request", "continuation", "conversational",
        }
        if intent not in valid_intents:
            return None

        return ClassificationResult(
            intent=intent,
            confidence=float(data.get("confidence", 0.5)),
            is_continuation=bool(data.get("is_continuation", False)),
            requires_tools=bool(data.get("requires_tools", False)),
            reasoning=str(data.get("reasoning", "")),
        )

    def _keyword_fallback(self, message: str) -> ClassificationResult:
        """Fall back to the existing keyword classifier.

        The keyword classifier is never deleted — it's the permanent safety net.
        """
        from intent_classifier import classify_intent

        intent = classify_intent(message)
        return ClassificationResult(
            intent=self._map_legacy_intent(intent),
            confidence=0.5,
            is_continuation=False,
            requires_tools=intent in (IntentType.EXEC_REQUEST, IntentType.PLAN_REQUEST, IntentType.MIXED_REQUEST),
            reasoning=f"Keyword fallback: {intent.value}",
        )

    @staticmethod
    def _map_legacy_intent(intent: IntentType) -> str:
        """Map legacy IntentType to unified classifier intent string."""
        mapping = {
            IntentType.PLAN_REQUEST: "plan_request",
            IntentType.EXEC_REQUEST: "action_high_risk",
            IntentType.MIXED_REQUEST: "action_high_risk",
            IntentType.KNOWLEDGE_REQUEST: "question",
            IntentType.AMBIGUOUS: "question",
        }
        return mapping.get(intent, "question")
