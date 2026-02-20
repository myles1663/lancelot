"""
Unified Intent Classifier — single LLM call replaces 7 heuristic functions (V23).

Uses the active provider to classify user intent in a single call with
a JSON response. Provider-aware: uses Gemini's response_schema when available,
falls back to JSON-in-prompt for Anthropic/OpenAI/xAI.

Replaces the V1-V22 chain of:
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
_CLASSIFIER_RULES = """You are an intent classifier for an AI assistant. Classify the user's message into exactly one category.

Categories:
- question: Asking for information, explanation, or clarification. "What is X?", "How does Y work?", "Tell me about Z"
- action_low_risk: Wants something done NOW that is read-only or low-risk. Search, lookup, summarize, analyze, compare, draft, check, list, fetch, schedule
- action_high_risk: Wants something done NOW that modifies state. Send email, deploy, delete, install, execute commands, commit, push, restart services
- plan_request: Wants a PLAN, DESIGN, STRATEGY, or ARCHITECTURE — not immediate action. "Plan how to...", "Design a system for...", "What approach should we take?"
- continuation: Modifying, correcting, or adding to a PREVIOUS request. "No, change it to...", "Actually send that to telegram", "Use the other one", "Wait, not that", "include links", "also add X", "but make it Y"
- conversational: Greetings, thanks, small talk, acknowledgments. "Hello", "Thanks", "OK cool", "Got it"

Rules:
- If the message references, corrects, or adds requirements to something said before, it's "continuation"
- If there is conversation history and the message adds to the previous request, it's "continuation"
- "Search for X" or "Look up X" is action_low_risk, not question
- "Send X to Y" or "Deploy X" is action_high_risk
- "Schedule X" or "Set up a daily/weekly Y" is action_low_risk
- Only classify as plan_request if they explicitly want a plan/design/strategy
- Default to "question" when uncertain"""

# Gemini gets structured output via response_schema
CLASSIFIER_SYSTEM_PROMPT_GEMINI = _CLASSIFIER_RULES

# Non-Gemini providers must be told to respond as JSON
CLASSIFIER_SYSTEM_PROMPT_JSON = _CLASSIFIER_RULES + """

You MUST respond with ONLY a JSON object (no markdown, no explanation). Format:
{"intent": "<category>", "confidence": <0.0-1.0>, "is_continuation": <true/false>, "requires_tools": <true/false>, "reasoning": "<brief>"}"""

# Default classification models per provider (cheapest/fastest tier)
_DEFAULT_CLASSIFIER_MODELS = {
    "gemini": "gemini-3-flash-preview",
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-4o-mini",
    "xai": "grok-2",
}


class UnifiedClassifier:
    """Single-call LLM intent classification with structured output.

    Provider-aware: uses Gemini response_schema when available, otherwise
    instructs the model to respond in JSON and parses the output.
    Falls back to the keyword classifier on any failure.
    """

    def __init__(self, provider):
        """Initialize with a ProviderClient instance.

        Args:
            provider: A ProviderClient (Gemini, Anthropic, OpenAI, or xAI).
        """
        self._provider = provider
        self._provider_type = getattr(provider, "provider_name", "gemini")
        # Use env override if set, otherwise pick the right model for the provider
        self._model = os.getenv(
            "CLASSIFIER_MODEL",
            _DEFAULT_CLASSIFIER_MODELS.get(self._provider_type, "gemini-3-flash-preview"),
        )
        self._is_gemini = self._provider_type == "gemini"

    def classify(
        self,
        message: str,
        history: Optional[List[dict]] = None,
    ) -> ClassificationResult:
        """Classify user intent with a single LLM call.

        Provider-aware: uses Gemini response_schema when available, otherwise
        instructs the model to respond in JSON via the system prompt.
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

            # Gemini: use structured output (response_schema).
            # Other providers: use JSON-in-prompt approach.
            if self._is_gemini:
                config = {
                    "response_mime_type": "application/json",
                    "response_schema": INTENT_SCHEMA,
                }
                sys_prompt = CLASSIFIER_SYSTEM_PROMPT_GEMINI
            else:
                config = {}
                sys_prompt = CLASSIFIER_SYSTEM_PROMPT_JSON

            result = self._provider.generate(
                model=self._model,
                messages=[user_msg],
                system_instruction=sys_prompt,
                config=config,
            )

            parsed = self._parse(result.text)
            if parsed:
                logger.debug(
                    "V23 unified classifier (%s/%s): intent=%s confidence=%.2f reasoning=%s",
                    self._provider_type, self._model,
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
        """Parse the JSON response into a ClassificationResult.

        Handles both clean JSON (Gemini structured output) and JSON wrapped
        in markdown fences or surrounded by text (Anthropic/OpenAI).
        """
        if not raw_text:
            return None

        text = raw_text.strip()
        # Strip markdown code fences if present (```json ... ```)
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first line (```json) and last line (```)
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()

        # Try to extract JSON object from text
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]

        try:
            data = json.loads(text)
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
