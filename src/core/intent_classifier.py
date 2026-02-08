"""
Intent Classifier — Deterministic keyword-based intent routing.
================================================================

Pure-function classifier that maps user text to an IntentType label
using keyword heuristics. No ML or external service required.

Routing rules (from the Honest Closure spec):
    - Planning keywords → PLAN_REQUEST
    - Execution keywords → EXEC_REQUEST
    - Both present → MIXED_REQUEST
    - Knowledge keywords (no plan/exec) → KNOWLEDGE_REQUEST
    - If uncertain → default to PLAN_REQUEST (not AMBIGUOUS)

Public API:
    classify_intent(text: str) -> IntentType
    PLANNING_KEYWORDS: frozenset[str]
    EXECUTION_KEYWORDS: frozenset[str]
    KNOWLEDGE_KEYWORDS: frozenset[str]
"""

from __future__ import annotations

import re
from typing import Set

from plan_types import IntentType


# =============================================================================
# Keyword Dictionaries
# =============================================================================

PLANNING_KEYWORDS: frozenset = frozenset({
    "plan",
    "design",
    "approach",
    "architecture",
    "blueprint",
    "strategy",
    "outline",
    "roadmap",
    "proposal",
    "diagram",
    "flowchart",
    "wireframe",
    "prototype",
    "spec",
    "specification",
})

PLANNING_PHRASES: frozenset = frozenset({
    "how would we",
    "how should we",
    "how would you",
    "how should i",
    "what's the best way to",
    "what is the best way to",
    "what approach",
    "come up with a plan",
    "create a plan",
    "make a plan",
    "draft a plan",
    "develop a strategy",
    "think through",
    "map out",
})

EXECUTION_KEYWORDS: frozenset = frozenset({
    "implement",
    "code",
    "deploy",
    "commit",
    "run",
    "execute",
    "ship",
    "build",
    "install",
    "launch",
    "start",
    "migrate",
    "push",
    "merge",
    "release",
    "compile",
    "test",
    "fix",
    "patch",
    "refactor",
    "set",          # "set up X"
    "configure",    # "configure the system"
    "setup",        # "setup voice"
    "connect",      # "connect devices"
    "enable",       # "enable voice"
    "create",       # "create a channel"
})

EXECUTION_PHRASES: frozenset = frozenset({
    "do it",
    "go ahead",
    "make it happen",
    "get it done",
    "set it up",
    "wire it up",
    "hook it up",
    "spin up",
    "roll out",
    "set up",       # "set up a way" (broader than "set it up")
    "hook up",      # "hook up voice"
    "wire up",      # "wire up X"
})

KNOWLEDGE_KEYWORDS: frozenset = frozenset({
    "what",
    "why",
    "explain",
    "describe",
    "define",
    "tell",
    "clarify",
    "meaning",
    "difference",
    "compare",
    "versus",
    "does",         # "does slack offer..."
    "how",          # "how do I..."
    "which",        # "which one..."
    "is",           # "is there a free plan?"
    "are",          # "are there options?"
    "can",          # "can I use..."
    "should",       # "should I use..."
    "offer",        # "does X offer..."
})

KNOWLEDGE_PHRASES: frozenset = frozenset({
    "what is",
    "what are",
    "what does",
    "how does",
    "why does",
    "why is",
    "can you explain",
    "tell me about",
    "what's the difference",
    "does it",      # "does it support..."
    "is there",     # "is there a free plan?"
    "can i",        # "can I use..."
    "can you",      # "can you help..."
    "should i",     # "should I use..."
    "do they",      # "do they offer..."
    "how do",       # "how do I..."
    "how can",      # "how can I..."
    "which one",    # "which one is best?"
})


# =============================================================================
# Tokenization
# =============================================================================

_WORD_RE = re.compile(r"[a-z]+(?:'[a-z]+)?")


def _tokenize(text: str) -> Set[str]:
    """Extract lowercase word tokens from text."""
    return set(_WORD_RE.findall(text.lower()))


def _contains_phrase(text_lower: str, phrases: frozenset) -> bool:
    """Check if any phrase from the set appears in the text."""
    for phrase in phrases:
        if phrase in text_lower:
            return True
    return False


# =============================================================================
# Classifier
# =============================================================================


def classify_intent(text: str) -> IntentType:
    """
    Classify user text into an IntentType using keyword heuristics.

    This is a deterministic, pure function — no external calls.

    Args:
        text: The raw user input text.

    Returns:
        An IntentType enum value.
    """
    if not text or not text.strip():
        return IntentType.AMBIGUOUS  # empty/whitespace → let Gemini handle

    text_lower = text.lower().strip()
    words = _tokenize(text_lower)

    # Check for keyword matches
    has_planning = bool(words & PLANNING_KEYWORDS) or _contains_phrase(text_lower, PLANNING_PHRASES)
    has_execution = bool(words & EXECUTION_KEYWORDS) or _contains_phrase(text_lower, EXECUTION_PHRASES)
    has_knowledge = bool(words & KNOWLEDGE_KEYWORDS) or _contains_phrase(text_lower, KNOWLEDGE_PHRASES)

    # Routing logic
    if has_planning and has_execution:
        return IntentType.MIXED_REQUEST

    if has_planning:
        return IntentType.PLAN_REQUEST

    if has_execution:
        return IntentType.EXEC_REQUEST

    if has_knowledge:
        return IntentType.KNOWLEDGE_REQUEST

    # If uncertain → default to PLAN_REQUEST (spec lines 13-14)
    return IntentType.PLAN_REQUEST
