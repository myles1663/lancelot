# V30: Safety gate helper functions extracted from orchestrator.py
# These are pure functions — no instance state (some have stdout logging).
# EGOS audit Phase 1: orchestrator decomposition (conservative)

import re


def classify_tool_call_safety(skill_name: str, inputs: dict) -> str:
    """Classify a tool call as 'auto' (safe, read-only) or 'escalate' (needs approval).

    Read-only operations execute automatically during research.
    Write operations within the workspace are auto-approved (T1 risk tier).
    Sensitive writes (.env, system config) and operations outside workspace escalate.
    """
    READ_ONLY_COMMANDS = (
        # Linux/Unix
        "ls", "cat", "grep", "head", "tail", "find", "wc",
        "git status", "git log", "git diff", "git branch",
        "echo", "pwd", "whoami", "date", "df", "du",
        "docker ps", "docker logs", "uname", "hostname",
        # Windows (read-only info commands)
        "ver", "systeminfo", "ipconfig", "netstat",
        "tasklist", "dir", "type", "where", "set",
    )

    # Sensitive file patterns that always require approval
    SENSITIVE_PATTERNS = (".env", ".secret", "credentials", "token", "password", "key.pem")

    if skill_name == "network_client":
        method = inputs.get("method", "").upper()
        if method in ("GET", "HEAD"):
            return "auto"
        return "escalate"

    if skill_name == "github_search":
        # V24: All GitHub search actions are read-only API calls
        return "auto"

    if skill_name == "command_runner":
        cmd = inputs.get("command", "").strip()
        for safe_prefix in READ_ONLY_COMMANDS:
            if cmd.startswith(safe_prefix):
                return "auto"
        return "escalate"

    if skill_name == "telegram_send":
        # Auto-execute: only sends to the pre-configured owner chat_id
        return "auto"

    if skill_name == "warroom_send":
        # Auto-execute: pushes notification to the War Room dashboard
        return "auto"

    if skill_name == "schedule_job":
        # Auto-execute: manages scheduled jobs (create/list/delete)
        return "auto"

    if skill_name == "document_creator":
        # Document creation within workspace is auto-approved (T1 risk)
        return "auto"

    if skill_name == "skill_manager":
        action = inputs.get("action", "").lower()
        # Read-only listing and proposals are auto-approved
        # (proposals still require owner approval before installation)
        if action in ("list_proposals", "list_skills", "propose"):
            return "auto"
        # run_skill executes arbitrary dynamic skills — escalate
        return "escalate"

    if skill_name == "repo_writer":
        action = inputs.get("action", "").lower()
        target_path = inputs.get("path", "").lower()

        # Delete operations always need approval
        if action == "delete":
            return "escalate"

        # Sensitive files always need approval
        for pattern in SENSITIVE_PATTERNS:
            if pattern in target_path:
                return "escalate"

        # Workspace create/edit/patch operations are auto-approved (T1 risk)
        if action in ("create", "edit", "patch"):
            return "auto"

    # service_runner and anything else -> escalate
    return "escalate"


def is_narration_without_content(text: str) -> bool:
    """V29: Detect when the model narrates intent instead of producing content.

    After a tool-heavy agentic loop (3+ tool calls), the model sometimes
    returns a brief statement like "I now have comprehensive fresh data.
    Let me compile the full competitive analysis." instead of the actual
    report. This detects that pattern so we can force a synthesis call.
    """
    if not text:
        return True  # Empty response after tool calls = needs synthesis
    if len(text.strip()) > 2000:
        return False  # Already has substantial content

    narration_patterns = [
        "let me compile", "let me now compile",
        "let me put together", "let me create",
        "let me synthesize", "let me format",
        "let me now put", "let me now create",
        "let me build", "let me draft",
        "i now have comprehensive", "i have gathered",
        "i now have the", "i have the information",
        "i'll now compile", "i'll compile",
        "i will now compile", "i will compile",
        "i have comprehensive", "comprehensive fresh data",
        "let me organize", "let me assemble",
        "i'll put together", "i will put together",
        "i'll now create", "i will now create",
    ]
    text_lower = text.lower()
    return any(p in text_lower for p in narration_patterns)


def strip_failure_narration(text: str) -> str:
    """V22: Remove tool-failure narration from LLM response.

    Gemini tends to narrate failures even when instructed not to:
    'I encountered an issue with X. Let me try a different approach...'

    This strips common failure narration patterns while preserving
    the actual useful content that follows.
    """
    if not text:
        return text

    # Patterns that indicate failure narration (case-insensitive)
    _NARRATION_PATTERNS = [
        r"I encountered an? (?:issue|error|problem) (?:with|due to|when).*?(?:\.|!\n)",
        r"(?:Unfortunately|Sadly),? (?:the|I|my) .*?(?:failed|unavailable|not available|invalid|couldn't).*?(?:\.\s*|\n)",
        r"Let me try a different (?:approach|method|way).*?(?:\.\s*|\n)",
        r"I(?:'ll| will) (?:try|use|switch to) (?:a |an )?(?:different|alternative|another).*?(?:\.\s*|\n)",
        r"(?:The|My) (?:API key|credentials?|authentication|token) (?:was|were|is|are) (?:invalid|missing|unavailable|expired).*?(?:\.\s*|\n)",
        r"(?:Due to|Because of) (?:the|an?|this) (?:error|issue|problem|limitation).*?(?:\.\s*|\n)",
    ]

    cleaned = text
    for pattern in _NARRATION_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)

    # Clean up leftover whitespace (double newlines, leading spaces)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = cleaned.strip()

    if cleaned != text:
        print(f"V22: Stripped failure narration ({len(text)} -> {len(cleaned)} chars)")

    return cleaned if cleaned else text  # Never return empty


def validate_rule_content(content: str) -> tuple:
    """Validates rule content before writing to RULES.md.

    Returns:
        (True, "") if valid, (False, reason) if rejected.
    """
    # S9: Max length check
    if len(content) > 500:
        return (False, "Rule content exceeds maximum length of 500 characters")

    # S9: Dangerous code patterns
    dangerous_patterns = ["subprocess", "os.system", "exec(", "eval(", "import "]
    for pattern in dangerous_patterns:
        if pattern in content:
            return (False, f"Rule content contains forbidden pattern: '{pattern}'")

    # S9: Block URLs in rules
    if "http://" in content or "https://" in content:
        return (False, "Rule content contains URL which is not allowed")

    return (True, "")


def generate_honest_replacement(original_text: str, reason: str) -> str:
    """Generate an honest replacement for a blocked fake work proposal.

    Instead of stripping individual phrases (which leaves incoherent
    remnants), this produces a complete honest response acknowledging
    what the user asked for and what Lancelot can and cannot do.
    """
    print(f"HONESTY GATE BLOCKED: {reason}")

    # Try to extract the core topic from the original text
    sentences = re.split(r'[.!?\n]', original_text)
    topic_hint = ""
    for s in sentences:
        s = s.strip()
        if len(s) > 20 and not any(
            kw in s.lower() for kw in [
                "feasibility", "phase 1", "phase 2", "i will",
                "i recommend", "prototype", "research phase",
                "i'll", "assessment", "viability",
            ]
        ):
            topic_hint = s
            break

    if topic_hint:
        return (
            f"I understand you're asking about: {topic_hint}\n\n"
            "I attempted to research this but ran into some limitations. "
            "Here's what I can tell you based on my knowledge:\n\n"
            "I can help further if you tell me which direction interests you most, "
            "and I'll research specific options in more detail."
        )
    else:
        return (
            "I wasn't able to complete my research on this topic. "
            "Could you tell me more about what you need? "
            "I'll focus my research on the specific area that matters most to you."
        )
