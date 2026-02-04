"""
Onboarding Snapshot Persistence (v4 Upgrade — Prompt 1)

Disk-backed JSON storage for onboarding state so that app restarts
always resume correctly.  There are no dead-end states.

Atomic writes (write-tmp → rename) prevent corruption on crash.
"""
import os
import json
import time
import hashlib
import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class OnboardingState(str, Enum):
    """All valid onboarding states (v4 spec §4.1)."""
    WELCOME = "WELCOME"
    FLAGSHIP_SELECTION = "FLAGSHIP_SELECTION"
    CREDENTIALS_CAPTURE = "CREDENTIALS_CAPTURE"
    CREDENTIALS_VERIFY = "CREDENTIALS_VERIFY"
    LOCAL_UTILITY_SETUP = "LOCAL_UTILITY_SETUP"
    COMMS_SELECTION = "COMMS_SELECTION"
    COMMS_CONFIGURE = "COMMS_CONFIGURE"
    COMMS_VERIFY = "COMMS_VERIFY"
    FINAL_CHECKS = "FINAL_CHECKS"
    READY = "READY"
    COOLDOWN = "COOLDOWN"


# Ordered progression — used by BACK command (Prompt 3)
STATE_ORDER = list(OnboardingState)

# States that are safe to go BACK from
BACKABLE_STATES = {
    OnboardingState.FLAGSHIP_SELECTION,
    OnboardingState.CREDENTIALS_CAPTURE,
    OnboardingState.CREDENTIALS_VERIFY,
    OnboardingState.LOCAL_UTILITY_SETUP,
    OnboardingState.COMMS_SELECTION,
    OnboardingState.COMMS_CONFIGURE,
    OnboardingState.COMMS_VERIFY,
    OnboardingState.FINAL_CHECKS,
}


class OnboardingSnapshot:
    """Disk-backed onboarding state.

    Every mutation auto-persists to ``<data_dir>/onboarding_snapshot.json``.
    Reads are from the in-memory copy (populated on construction).
    """

    FILENAME = "onboarding_snapshot.json"

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self._path = os.path.join(data_dir, self.FILENAME)

        # ---- schema fields ----
        self.state: OnboardingState = OnboardingState.WELCOME
        self.flagship_provider: Optional[str] = None
        self.credential_status: str = "none"          # none | captured | verified
        self.local_model_status: str = "none"          # none | downloading | installed | verified
        self.verification_code_hash: Optional[str] = None
        self.resend_count: int = 0                      # rate-limit counter
        self.last_resend_at: Optional[float] = None     # epoch of last resend
        self.cooldown_until: Optional[float] = None     # epoch timestamp
        self.last_error: Optional[str] = None
        self.temp_data: dict = {}
        self.updated_at: Optional[float] = None

        # Hydrate from disk if a snapshot exists
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load snapshot from disk.  Missing file → keep defaults."""
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            self._apply(raw)
            logger.info("Onboarding snapshot loaded from %s", self._path)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Corrupt snapshot at %s — starting fresh: %s",
                           self._path, exc)

    def _apply(self, raw: dict) -> None:
        """Apply a raw dict to fields, with safe fallbacks."""
        state_str = raw.get("state", "WELCOME")
        try:
            self.state = OnboardingState(state_str)
        except ValueError:
            self.state = OnboardingState.WELCOME

        self.flagship_provider = raw.get("flagship_provider")
        self.credential_status = raw.get("credential_status", "none")
        self.local_model_status = raw.get("local_model_status", "none")
        self.verification_code_hash = raw.get("verification_code_hash")
        self.resend_count = raw.get("resend_count", 0)
        self.last_resend_at = raw.get("last_resend_at")
        self.cooldown_until = raw.get("cooldown_until")
        self.last_error = raw.get("last_error")
        self.temp_data = raw.get("temp_data", {})
        self.updated_at = raw.get("updated_at")

    def save(self) -> None:
        """Atomic write: tmp file → rename."""
        self.updated_at = time.time()
        os.makedirs(self.data_dir, exist_ok=True)
        tmp_path = self._path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(self.to_dict(), fh, indent=2)
            # Atomic rename (POSIX) / replace (Windows)
            os.replace(tmp_path, self._path)
        except OSError:
            # Fallback: direct write if rename fails
            with open(self._path, "w", encoding="utf-8") as fh:
                json.dump(self.to_dict(), fh, indent=2)

    def to_dict(self) -> dict:
        """Serialise current state to a JSON-safe dict."""
        return {
            "state": self.state.value,
            "flagship_provider": self.flagship_provider,
            "credential_status": self.credential_status,
            "local_model_status": self.local_model_status,
            "verification_code_hash": self.verification_code_hash,
            "resend_count": self.resend_count,
            "last_resend_at": self.last_resend_at,
            "cooldown_until": self.cooldown_until,
            "last_error": self.last_error,
            "temp_data": self.temp_data,
            "updated_at": self.updated_at,
        }

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def transition(self, new_state: OnboardingState, **updates) -> None:
        """Move to *new_state*, apply optional field updates, and persist.

        Raises ValueError for illegal transitions.
        """
        if self.state == OnboardingState.COOLDOWN and new_state != OnboardingState.COOLDOWN:
            if self.is_in_cooldown():
                raise ValueError(
                    f"Cannot leave COOLDOWN until timer expires "
                    f"(remaining: {self.cooldown_remaining():.0f}s)"
                )

        self.state = new_state

        for key, val in updates.items():
            if hasattr(self, key):
                setattr(self, key, val)

        self.save()
        logger.info("Onboarding → %s", new_state.value)

    # ------------------------------------------------------------------
    # Verification codes (hashed, never stored in clear)
    # ------------------------------------------------------------------

    @staticmethod
    def hash_code(code: str) -> str:
        """SHA-256 hash a verification code."""
        return hashlib.sha256(code.encode("utf-8")).hexdigest()

    def set_verification_code(self, code: str) -> None:
        """Store a hashed verification code and persist."""
        self.verification_code_hash = self.hash_code(code)
        self.save()

    def check_verification_code(self, candidate: str) -> bool:
        """Return True if *candidate* matches the stored hash."""
        if self.verification_code_hash is None:
            return False
        return self.hash_code(candidate) == self.verification_code_hash

    # ------------------------------------------------------------------
    # Cooldown helpers
    # ------------------------------------------------------------------

    def enter_cooldown(self, seconds: int, reason: str) -> None:
        """Move to COOLDOWN state with a timed recovery."""
        self.state = OnboardingState.COOLDOWN
        self.cooldown_until = time.time() + seconds
        self.last_error = reason
        self.save()

    def is_in_cooldown(self) -> bool:
        if self.cooldown_until is None:
            return False
        return time.time() < self.cooldown_until

    def cooldown_remaining(self) -> float:
        if self.cooldown_until is None:
            return 0.0
        return max(0.0, self.cooldown_until - time.time())

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Full reset to WELCOME — wipes snapshot on disk."""
        self.state = OnboardingState.WELCOME
        self.flagship_provider = None
        self.credential_status = "none"
        self.local_model_status = "none"
        self.verification_code_hash = None
        self.resend_count = 0
        self.last_resend_at = None
        self.cooldown_until = None
        self.last_error = None
        self.temp_data = {}
        self.save()

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @property
    def is_ready(self) -> bool:
        return self.state == OnboardingState.READY

    def __repr__(self) -> str:
        return (f"<OnboardingSnapshot state={self.state.value} "
                f"provider={self.flagship_provider} "
                f"creds={self.credential_status}>")
