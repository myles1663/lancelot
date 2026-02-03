import re
import datetime

# --- Trigger patterns ---
ACTIVATE_PATTERNS = [
    r"\benter\s+crusader\s+mode\b",
    r"\bcrusader\s+mode\s+on\b",
    r"\bengage\s+crusader\b",
    r"\benable\s+crusader\s+mode\b",
]

DEACTIVATE_PATTERNS = [
    r"\bstand\s+down\b",
    r"\bcrusader\s+mode\s+off\b",
    r"\bexit\s+crusader\s+mode\b",
    r"\bdisable\s+crusader\s+mode\b",
]

# --- Crusader Allowlist: command prefixes allowed by default ---
CRUSADER_ALLOWLIST = [
    "ls", "dir", "cat", "head", "tail", "find", "wc",
    "git status", "git log", "git diff", "git branch",
    "docker ps", "docker images", "docker inspect",
    "tar", "gzip", "zip", "unzip",
    "mkdir",
]

# --- Auto-Pause patterns: always require authority ---
CRUSADER_PAUSE_PATTERNS = [
    r"\bsudo\b",
    r"\bsystemctl\b",
    r"\bchmod\b",
    r"\bchown\b",
    r"\brm\s+-rf\b",
    r"\brm\s+.*-r\b",
    r"\biptables\b",
    r"\bnetwork\b.*\bconfig\b",
    r"\bdd\b",
    r"\bcurl\b",
    r"\bwget\b",
    r"\bpip\s+install\b",
    r"\bapt\b",
    r"\byum\b",
    r"\bbrew\b",
    r"\bkill\b",
    r"\brsync\b",
    r"\bnc\b",
    r"\bncat\b",
    r"\bpython\b.*\b-c\b",
    r"\bsh\b",
    r"\bbash\b",
]

# Characters that indicate command chaining (potential injection)
COMMAND_CHAINING_CHARS = {"&&", "||", ";", "|", "`", "$("}

ACTIVATION_RESPONSE = (
    "Crusader Mode engaged.\n"
    "Commands will execute decisively.\n"
    "Type \"stand down\" to exit."
)

DEACTIVATION_RESPONSE = "Normal mode restored."


class CrusaderMode:
    """Session-scoped Crusader Mode state manager.

    Non-persistent: resets to False on process restart.
    """

    def __init__(self):
        self._active = False
        self._activated_at = None

    @property
    def is_active(self) -> bool:
        return self._active

    def activate(self) -> str:
        self._active = True
        self._activated_at = datetime.datetime.utcnow().isoformat()
        return ACTIVATION_RESPONSE

    def deactivate(self) -> str:
        self._active = False
        self._activated_at = None
        return DEACTIVATION_RESPONSE

    def should_intercept(self, message: str) -> tuple:
        """Check if a message is a mode trigger.

        Returns:
            (True, "activate") if activation trigger
            (True, "deactivate") if deactivation trigger
            (False, None) if not a trigger
        """
        msg_lower = message.strip().lower()

        for pattern in ACTIVATE_PATTERNS:
            if re.search(pattern, msg_lower):
                return (True, "activate")

        for pattern in DEACTIVATE_PATTERNS:
            if re.search(pattern, msg_lower):
                return (True, "deactivate")

        return (False, None)


class CrusaderPromptModifier:
    """Generates the Crusader Mode system prompt overlay."""

    CRUSADER_DIRECTIVE = (
        "\n\n--- CRUSADER MODE ACTIVE ---\n"
        "You are operating in Crusader Mode. Behavioral directives:\n"
        "1. Presume all commands are actionable. Do not ask for clarification.\n"
        "2. Choose the safest executable interpretation of ambiguous commands.\n"
        "3. Respond with short, confident, final statements.\n"
        "4. Do NOT explain your reasoning unless the action failed.\n"
        "5. Do NOT use conversational filler, greetings, or hedging language.\n"
        "6. Prefix successful actions with the result only.\n"
        "7. Continue to output a Confidence Score as instructed.\n"
        "--- END CRUSADER DIRECTIVE ---\n"
    )

    @staticmethod
    def modify_prompt(base_prompt: str) -> str:
        return base_prompt + CrusaderPromptModifier.CRUSADER_DIRECTIVE


class CrusaderAdapter:
    """Wraps orchestrator responses for Crusader Mode presentation.

    Strips DRAFT prefix, hides confidence scores, compresses verbose responses.
    Does NOT bypass MCP Sentry, audit logging, memory, or lockdown.
    """

    @staticmethod
    def format_response(raw_response: str) -> str:
        # Case 1: Permission required (low confidence <70%)
        if raw_response.startswith("PERMISSION REQUIRED"):
            match = re.match(
                r'PERMISSION REQUIRED\s*\(Confidence\s*\d+%?\)\s*:\s*(.*)',
                raw_response,
                re.DOTALL
            )
            action_text = match.group(1).strip() if match else raw_response
            return f"Authority required.\n{action_text}"

        # Case 2: Draft (70-90% confidence)
        if raw_response.startswith("DRAFT:"):
            action_text = raw_response[len("DRAFT:"):].strip()
            action_text = re.sub(
                r'\bConfidence[:\s]*\d{1,3}%?\s*',
                '',
                action_text,
                flags=re.IGNORECASE
            ).strip()
            return f"Awaiting confirmation.\n{action_text}"

        # Case 3: High confidence (>90%) or no-score passthrough
        cleaned = re.sub(
            r'\bConfidence[:\s]*\d{1,3}%?\s*',
            '',
            raw_response,
            flags=re.IGNORECASE
        ).strip()

        if cleaned.startswith("Action:"):
            cleaned = cleaned[len("Action:"):].strip()

        # Compress verbose responses
        lines = [l.strip() for l in cleaned.split('\n') if l.strip()]
        if len(lines) > 5:
            cleaned = '\n'.join(lines[:5])

        return f"Complete.\n{cleaned}" if cleaned else "Complete."

    @staticmethod
    def _normalize_for_check(message: str) -> str:
        """Normalizes a message for security checking.

        - Collapses whitespace
        - Removes zero-width characters
        - Removes backslash continuations
        - Lowercases
        """
        # Remove zero-width characters
        for zw in ("\u200b", "\u200c", "\u200d", "\ufeff"):
            message = message.replace(zw, "")
        # Remove backslash line continuations (join like shell: su\<newline>do → sudo)
        message = message.replace("\\\n", "").replace("\\", "")
        # Collapse whitespace
        message = re.sub(r"\s+", " ", message).strip()
        # Lowercase
        return message.lower()

    @staticmethod
    def _has_chaining_chars(text: str) -> bool:
        """Returns True if text contains any command chaining characters."""
        for chars in COMMAND_CHAINING_CHARS:
            if chars in text:
                return True
        return False

    @staticmethod
    def check_auto_pause(message: str) -> bool:
        """Returns True if the message contains an auto-pause pattern or command chaining."""
        normalized = CrusaderAdapter._normalize_for_check(message)

        # Check for command chaining characters
        if CrusaderAdapter._has_chaining_chars(normalized):
            return True

        for pattern in CRUSADER_PAUSE_PATTERNS:
            if re.search(pattern, normalized):
                return True
        return False

    @staticmethod
    def is_in_allowlist(message: str) -> bool:
        """Returns True if the message starts with an allowed command prefix.

        Also rejects commands containing command chaining characters.
        """
        msg_lower = message.strip().lower()

        # Reject if command chaining characters are present
        if CrusaderAdapter._has_chaining_chars(msg_lower):
            return False

        for prefix in CRUSADER_ALLOWLIST:
            if msg_lower.startswith(prefix):
                return True
        return False
