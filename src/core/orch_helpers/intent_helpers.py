# V30: Intent classification helper functions extracted from orchestrator.py
# These are pure functions — no instance state, no side effects.
# EGOS audit Phase 1: orchestrator decomposition (conservative)

import re


def is_conversational(prompt: str) -> bool:
    """Detect purely conversational messages that need no tools.

    Fix Pack V13: Prevents simple chat (greetings, name preferences,
    thanks) from entering the agentic loop where Gemini may hallucinate
    tool calls for messages that just need a text response.

    Fix Pack V17b: Split into two categories:
    - Always-conversational (greetings, thanks, farewells) — match on prefix
    - Confirmation words ("yes", "ok", "sure") — only conversational if the
      message is JUST the word (+ optional punctuation/filler). If there's
      substantive content after ("ok, create the file"), NOT conversational.
    """
    prompt_lower = prompt.lower().strip()

    if len(prompt_lower) < 60:
        # Group 1: Always conversational regardless of what follows
        always_conversational = [
            "call me ", "my name is ", "i'm ", "i am ",
            "hello", "hi ", "hey ", "yo", "sup",
            "thanks", "thank you", "cheers",
            "good morning", "good afternoon", "good evening",
            "how are you", "what's up", "whats up",
            "bye", "goodbye", "see you", "later",
            "never mind", "nevermind", "forget it",
            "no worries", "no problem", "you're welcome",
            "nice to meet", "pleased to meet",
        ]
        if any(prompt_lower.startswith(p) or prompt_lower == p
               for p in always_conversational):
            return True

        # Group 2: Confirmation words — only conversational if the message
        # is JUST the word, optionally with punctuation or filler
        confirmation_words = [
            "yes", "no", "yep", "nope", "yeah", "nah",
            "ok", "okay", "sure", "alright", "cool",
        ]
        stripped = prompt_lower.rstrip('.,!? ')
        if stripped in confirmation_words:
            return True
        # Match with trailing filler: "yes please", "ok thanks", "sure thing"
        filler = ["please", "thanks", "thank you", "mate", "man", "thing"]
        for word in confirmation_words:
            if prompt_lower.startswith(word):
                remainder = prompt_lower[len(word):].strip().lstrip('.,!').strip()
                if remainder in filler:
                    return True

    return False


def is_continuation(message: str) -> bool:
    """Detect messages that are conversational continuations of a prior thread.

    V17: Short messages that reference previous context ("it", "that", "this",
    "the spec", "the plan") should flow through the agentic loop where the
    full conversation history provides context, rather than being routed to
    the template-based PlanningPipeline which has no conversation awareness.
    """
    if len(message) > 150:
        return False

    msg_lower = message.lower().strip()

    continuation_signals = [
        "that", "this", "it ", "those", "these",
        "the same", "the other", "the one",
        "what about", "how about",
        "instead", "rather", "actually",
        "never mind", "scratch that", "forget that",
        "which one", "the first", "the second",
        "option", "go with", "go ahead",
        "sounds good", "let's do", "lets do", "let's go",
        "the spec", "the plan", "the previous",
        "like i said", "as i said", "i meant",
        "can you also", "also add", "and also",
        "what else", "anything else",
        "yes", "yeah", "yep", "no", "nah", "nope",
        "ok do", "okay do", "sure do", "sure,",
        # V17b: Confirmation + comma implies more content follows
        "ok,", "okay,", "alright,", "cool,",
        "yes,", "yeah,", "yep,", "no,", "nah,",
        # V17b: Common action follow-ups
        "do it", "try it", "run it", "send it", "save it",
        "delete it", "rename it", "retry", "try again",
        "go for it", "proceed", "continue", "carry on",
        # V20: Correction / redirect signals
        "correction", "change it to", "switch to", "redirect",
        "no no", "wait", "hold on", "not that",
        "use telegram", "use slack", "use email",
    ]

    if any(signal in msg_lower for signal in continuation_signals):
        return True

    # V17b: "it" at end of string (word boundary) — "ok create it", "just do it"
    if msg_lower.endswith(" it") or msg_lower == "it":
        return True

    # Very short messages with a question mark are usually follow-ups
    if len(msg_lower) < 60 and "?" in msg_lower:
        return True

    return False


def needs_research(prompt: str) -> bool:
    """Detect queries requiring tool-backed research.

    Fix Pack V10: Returns True for open-ended exploratory queries where
    Gemini should use network_client to research before answering.
    Prevents Gemini from generating "I will research..." text that gets
    blocked by the response governor.
    """
    prompt_lower = prompt.lower()

    research_phrases = [
        "figure out", "find out", "look into", "look up",
        "research", "investigate", "explore options",
        "find a way", "find me", "what options",
        "what are the options", "what tools", "what services",
        "is there a way", "are there any",
        "can you find", "can you figure",
        "how can we", "how could we",
        "recommend", "suggest",
        # V14: Additional research triggers
        "what about", "have you heard of", "do you know about",
        "see if", "check if", "check out",
        "alternative", "alternatives", "other options",
        "compare", "comparison", "pricing",
        "how much does", "how much is",
        "is there a free", "free way to", "free option",
        "what's the best", "what is the best",
        "come up with a plan", "plan for",
        # V18/V20: Tool-action triggers — specific phrases only (not bare keywords)
        "send a message", "send me a message",
        "send a telegram message", "send via telegram", "send on telegram",
        "send to telegram", "send to the war room", "send to warroom",
        "notify me via", "message me on",
        "post to the dashboard", "push to command center",
        # V19: Scheduling triggers — specific phrases
        "schedule a", "set an alarm", "set a reminder",
        "wake me up", "wake-up call",
        "set up a recurring", "every morning at", "every day at", "every hour",
        "remind me to", "remind me at", "create a reminder",
        "cron job", "set up a job", "create a job",
        "cancel the job", "delete the job", "list my jobs", "list scheduled jobs",
    ]
    if any(phrase in prompt_lower for phrase in research_phrases):
        return True

    # Open-ended "can/could you/we" + action verbs suggesting exploration or action
    if re.search(
        r'\b(?:can|could)\s+(?:you|we)\b.*\b(?:communicate|connect|set up|build|get|chat|talk|use|send|notify|message|tell)\b',
        prompt_lower,
    ):
        return True

    # "What about X?" pattern — user is suggesting a specific service/tool to research
    if re.search(r'\bwhat\s+about\s+\w+', prompt_lower):
        return True

    # V15: Delegation patterns — "prompt X to do Y", "ask X to do Y"
    if re.search(
        r'\b(?:prompt|ask|tell|invoke|use)\s+\w+(?:\s+\w+)?\s+to\b',
        prompt_lower,
    ):
        return True

    return False


def wants_action(prompt: str) -> bool:
    """Detect queries where the user wants Lancelot to take action.

    Fix Pack V12: Returns True when the user expects code writing,
    file creation, or system configuration — not just information.
    Used to set allow_writes=True in the agentic loop.
    """
    prompt_lower = prompt.lower()
    action_phrases = [
        "set up", "create", "build", "write", "implement", "configure",
        "make", "develop", "code", "install", "deploy", "set it up",
        "figure out a way", "figure out a plan", "figure out how",
        "figure out", "find a way", "get it working",
        "hook up", "wire up", "connect", "enable",
        "send", "notify", "message", "tell",
        "schedule", "alarm", "remind", "wake up", "cancel",
    ]
    return any(phrase in prompt_lower for phrase in action_phrases)


def is_low_risk_exec(prompt: str) -> bool:
    """V21: Detect execution requests that are low-risk (read-only or text generation).

    Used by just-do-it mode to skip PlanningPipeline -> TaskGraph -> Permission
    for actions that have no destructive side effects. These go straight to
    the agentic loop where Gemini can use tools immediately.

    Low-risk: search, draft, summarize, check status, list, compare, analyze
    High-risk (still needs pipeline): deploy, delete, send, install, execute commands
    """
    prompt_lower = prompt.lower()

    # High-risk signals — if ANY of these are present, keep in pipeline
    high_risk = [
        "deploy", "push", "ship", "release", "publish",
        "delete", "remove", "drop", "destroy", "wipe",
        "send", "post", "notify", "message", "email", "telegram",
        "install", "migrate", "upgrade", "downgrade",
        "execute", "run command", "run script", "run the",
        "commit", "merge", "rebase",
        "shut down", "shutdown", "restart", "reboot", "kill",
        "move", "rename", "overwrite",
        "create", "write", "save", "update", "modify", "edit",
    ]
    if any(phrase in prompt_lower for phrase in high_risk):
        return False

    # Low-risk signals — read-only or text-generation actions
    low_risk = [
        "search", "find", "look up", "look for", "lookup",
        "draft", "compose", "write a draft", "write a summary",
        "summarize", "summary of", "recap",
        "check", "status", "health check", "what's the status",
        "list", "show me", "display", "show all",
        "compare", "analyze", "analyse", "review",
        "explain", "describe", "tell me",
        "fetch", "get", "retrieve", "pull up",
        "count", "how many", "calculate",
        "test", "verify", "validate", "check if",
    ]
    return any(phrase in prompt_lower for phrase in low_risk)


def extract_literal_terms(text: str) -> list:
    """V22: Extract high-confidence proper nouns and quoted strings to preserve verbatim.

    Returns a list of terms that should NOT be corrected, substituted,
    or interpreted by the LLM. These are injected into the agentic loop
    prompt to prevent autocorrection (e.g., "Clawd Bot" -> "Claude").

    Conservative — only extracts terms with high confidence of being intentional:
    - Quoted strings: "Clawd Bot", 'ACME Corp' (user explicitly quoted = always preserve)
    - Multi-word capitalized sequences: Clawd Bot, New York Times (2+ capitalized
      words together = almost certainly a proper noun, not a typo)

    Does NOT extract single capitalized words — those could be sentence starters,
    common nouns, or actual misspellings. This avoids locking in typos.
    """
    terms = []

    # 1. Quoted strings (user explicitly quoted them — always preserve)
    quoted = re.findall(r'["\']([^"\']{2,50})["\']', text)
    terms.extend(quoted)

    # 2. Multi-word capitalized sequences: "Clawd Bot", "New York Times"
    # 2+ consecutive capitalized words = very likely a proper noun
    proper_nouns = re.findall(r'\b([A-Z][a-zA-Z]*(?:\s+[A-Z][a-zA-Z]*)+)\b', text)
    # Filter out common multi-word patterns that aren't proper nouns
    _COMMON_PHRASES = {
        "Search For", "Look For", "Find Out", "Tell Me", "Show Me",
        "Send To", "Let Me", "Can You", "How Do", "What Is",
    }
    for noun in proper_nouns:
        if noun not in _COMMON_PHRASES:
            terms.append(noun)

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for t in terms:
        if t.lower() not in seen:
            seen.add(t.lower())
            unique.append(t)

    return unique
