import re
import datetime
import logging

logger = logging.getLogger(__name__)

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
    "Soul switched to Crusader constitution. Capabilities elevated.\n"
    "Commands will execute decisively.\n"
    "Type \"stand down\" to exit."
)

DEACTIVATION_RESPONSE = (
    "Normal mode restored.\n"
    "Soul and feature flags reverted to standard configuration."
)

# --- Crusader Flag Profile ---
# Balanced: maximize capabilities, keep basic safety nets
CRUSADER_FLAG_PROFILE = {
    # Enable maximum capabilities
    "FEATURE_AGENTIC_LOOP": True,
    "FEATURE_TASK_GRAPH_EXECUTION": True,
    "FEATURE_TOOLS_CLI_PROVIDERS": True,
    "FEATURE_TOOLS_NETWORK": True,
    "FEATURE_CONNECTORS": True,
    "FEATURE_LOCAL_AGENTIC": True,
    # Reduce approval friction
    "FEATURE_RISK_TIERED_GOVERNANCE": False,
    "FEATURE_APPROVAL_LEARNING": False,
}


class CrusaderMode:
    """Session-scoped Crusader Mode state manager.

    Non-persistent: resets to False on process restart.
    On activation: snapshots current flags + soul, applies crusader profile.
    On deactivation: restores original flags + soul.
    """

    def __init__(self):
        self._active = False
        self._activated_at = None
        self._saved_flags = {}
        self._saved_soul_version = None

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def flag_overrides_count(self) -> int:
        """Number of flags that were changed from their original state."""
        if not self._active:
            return 0
        return len(self._saved_flags)

    @property
    def soul_override(self) -> str:
        """The soul version that was replaced, or empty if not active."""
        return self._saved_soul_version or ""

    def activate(self) -> str:
        """Activate Crusader Mode: snapshot state, apply flag profile, switch soul."""
        if self._active:
            return "Crusader Mode already active."

        # --- Snapshot current flags ---
        try:
            import feature_flags as ff
            self._saved_flags = {}
            for flag_name, target_val in CRUSADER_FLAG_PROFILE.items():
                current = getattr(ff, flag_name, None)
                if isinstance(current, bool) and current != target_val:
                    self._saved_flags[flag_name] = current
                    ff.set_flag(flag_name, target_val)
                    logger.info("Crusader: %s %s → %s", flag_name, current, target_val)
            logger.info("Crusader: %d flags overridden", len(self._saved_flags))
        except Exception as e:
            logger.error("Crusader flag override failed: %s", e)

        # --- Switch soul to crusader ---
        try:
            from src.core.soul.store import get_active_version, set_active_version
            self._saved_soul_version = get_active_version()
            set_active_version("crusader")
            logger.info("Crusader: soul switched from %s to crusader", self._saved_soul_version)
        except Exception as e:
            logger.error("Crusader soul switch failed: %s", e)
            self._saved_soul_version = None

        self._active = True
        self._activated_at = datetime.datetime.utcnow().isoformat()
        return ACTIVATION_RESPONSE

    def deactivate(self) -> str:
        """Deactivate Crusader Mode: restore flags and soul."""
        if not self._active:
            return "Crusader Mode is not active."

        # --- Restore flags ---
        try:
            import feature_flags as ff
            for flag_name, original_val in self._saved_flags.items():
                ff.set_flag(flag_name, original_val)
                logger.info("Crusader restore: %s → %s", flag_name, original_val)
            logger.info("Crusader: %d flags restored", len(self._saved_flags))
        except Exception as e:
            logger.error("Crusader flag restore failed: %s", e)

        # --- Restore soul ---
        if self._saved_soul_version:
            try:
                from src.core.soul.store import set_active_version
                set_active_version(self._saved_soul_version)
                logger.info("Crusader: soul restored to %s", self._saved_soul_version)
            except Exception as e:
                logger.error("Crusader soul restore failed: %s", e)

        self._active = False
        self._activated_at = None
        self._saved_flags = {}
        self._saved_soul_version = None
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

    def get_status(self) -> dict:
        """Return full crusader mode status for API."""
        return {
            "crusader_mode": self._active,
            "activated_at": self._activated_at,
            "flag_overrides": self.flag_overrides_count,
            "soul_override": self.soul_override,
            "overridden_flags": list(self._saved_flags.keys()) if self._active else [],
        }


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
        """Normalizes a message for security checking."""
        for zw in ("\u200b", "\u200c", "\u200d", "\ufeff"):
            message = message.replace(zw, "")
        message = message.replace("\\\n", "").replace("\\", "")
        message = re.sub(r"\s+", " ", message).strip()
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

        if CrusaderAdapter._has_chaining_chars(normalized):
            return True

        for pattern in CRUSADER_PAUSE_PATTERNS:
            if re.search(pattern, normalized):
                return True
        return False

    @staticmethod
    def is_in_allowlist(message: str) -> bool:
        """Returns True if the message starts with an allowed command prefix."""
        msg_lower = message.strip().lower()

        if CrusaderAdapter._has_chaining_chars(msg_lower):
            return False

        for prefix in CRUSADER_ALLOWLIST:
            if msg_lower.startswith(prefix):
                return True
        return False
