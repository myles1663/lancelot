"""
Global Recovery Commands (v4 Upgrade — Prompts 2-5)

Commands available at every onboarding step.  The onboarding orchestrator
calls ``try_handle()`` *before* state-specific dispatch.  If a recovery
command is recognised, its response is returned directly; otherwise
``None`` signals normal processing should continue.

Spec reference: §5.2 Recovery Commands (Global)
"""
import logging
import random
import string
import time
from typing import Optional

from src.core.onboarding_snapshot import (
    OnboardingSnapshot,
    OnboardingState,
    STATE_ORDER,
    BACKABLE_STATES,
)

logger = logging.getLogger(__name__)

# Commands are matched case-insensitively after stripping whitespace
_STATUS_TRIGGERS = {"status", "/status", "system status"}
_BACK_TRIGGERS = {"back", "/back", "go back"}
_RESTART_STEP_TRIGGERS = {"restart step", "/restart step", "restart", "/restart"}
_RESEND_CODE_TRIGGERS = {"resend code", "/resend code", "resend", "/resend"}
_RESET_TRIGGERS = {"reset onboarding", "/reset onboarding", "reset", "/reset"}

# Rate-limit constants for RESEND CODE
RESEND_MAX_ATTEMPTS = 3
RESEND_COOLDOWN_SECONDS = 60


def _format_state_label(state: OnboardingState) -> str:
    """Human-friendly label for a state enum value."""
    labels = {
        OnboardingState.WELCOME: "Welcome — waiting for identity",
        OnboardingState.FLAGSHIP_SELECTION: "Flagship provider selection",
        OnboardingState.CREDENTIALS_CAPTURE: "Capturing credentials",
        OnboardingState.CREDENTIALS_VERIFY: "Verifying credentials",
        OnboardingState.LOCAL_UTILITY_SETUP: "Local utility model setup",
        OnboardingState.COMMS_SELECTION: "Communication channel selection",
        OnboardingState.COMMS_CONFIGURE: "Configuring communication channel",
        OnboardingState.COMMS_VERIFY: "Verifying communication link",
        OnboardingState.FINAL_CHECKS: "Running final checks",
        OnboardingState.READY: "System ready",
        OnboardingState.COOLDOWN: "Cooldown — temporary hold",
    }
    return labels.get(state, state.value)


def _format_status(snap: OnboardingSnapshot) -> str:
    """Build the STATUS response string."""
    lines = [
        "**System Status**",
        "---",
        f"**Onboarding State:** {snap.state.value} — {_format_state_label(snap.state)}",
        f"**Flagship Provider:** {snap.flagship_provider or 'not selected'}",
        f"**Credential Status:** {snap.credential_status}",
        f"**Local Model Status:** {snap.local_model_status}",
    ]

    if snap.state == OnboardingState.COOLDOWN:
        remaining = snap.cooldown_remaining()
        if remaining > 0:
            mins, secs = divmod(int(remaining), 60)
            lines.append(f"**Cooldown Remaining:** {mins}m {secs}s")
        else:
            lines.append("**Cooldown:** expired — ready to resume")

    if snap.last_error:
        lines.append(f"**Last Error:** {snap.last_error}")

    lines.append("---")
    lines.append(
        "Available commands: `STATUS` · `BACK` · `RESTART STEP` "
        "· `RESEND CODE` · `RESET ONBOARDING`"
    )
    return "\n".join(lines)


def try_handle(text: str, snap: OnboardingSnapshot) -> Optional[str]:
    """Check if *text* is a global recovery command.

    Returns the response string if handled, or ``None`` to fall through
    to normal state processing.
    """
    normalised = text.strip().lower()

    if normalised in _STATUS_TRIGGERS:
        logger.info("STATUS command invoked at state %s", snap.state.value)
        return _format_status(snap)

    if normalised in _BACK_TRIGGERS:
        logger.info("BACK command invoked at state %s", snap.state.value)
        return _handle_back(snap)

    if normalised in _RESTART_STEP_TRIGGERS:
        logger.info("RESTART STEP command invoked at state %s", snap.state.value)
        return _handle_restart_step(snap)

    if normalised in _RESEND_CODE_TRIGGERS:
        logger.info("RESEND CODE command invoked at state %s", snap.state.value)
        return _handle_resend_code(snap)

    if normalised in _RESET_TRIGGERS:
        logger.info("RESET ONBOARDING command invoked at state %s", snap.state.value)
        return _handle_reset_onboarding(snap)

    return None


# ------------------------------------------------------------------
# BACK
# ------------------------------------------------------------------

def _handle_back(snap: OnboardingSnapshot) -> str:
    """Move to the previous state in the progression.

    Guards:
    - Cannot go back from WELCOME (nothing before it).
    - Cannot go back from READY (onboarding is done).
    - Cannot go back from COOLDOWN (must wait for timer).
    """
    if snap.state not in BACKABLE_STATES:
        return (
            f"Cannot go back from **{snap.state.value}**. "
            f"{_back_denial_reason(snap.state)}"
        )

    current_idx = STATE_ORDER.index(snap.state)
    prev_state = STATE_ORDER[current_idx - 1]

    old_state = snap.state
    snap.transition(prev_state, last_error=None)

    return (
        f"Moved back from **{old_state.value}** to **{prev_state.value}** "
        f"— {_format_state_label(prev_state)}."
    )


def _back_denial_reason(state: OnboardingState) -> str:
    """Explain why BACK is not allowed from this state."""
    if state == OnboardingState.WELCOME:
        return "This is the first step."
    if state == OnboardingState.READY:
        return "Onboarding is complete. Use `RESET ONBOARDING` to start over."
    if state == OnboardingState.COOLDOWN:
        return "Wait for cooldown to expire first."
    return ""


# ------------------------------------------------------------------
# RESTART STEP
# ------------------------------------------------------------------

def _handle_restart_step(snap: OnboardingSnapshot) -> str:
    """Re-enter the current state, clearing step-specific temp data.

    Guards:
    - Cannot restart WELCOME (nothing to reset).
    - Cannot restart READY (onboarding is done).
    - Cannot restart during active COOLDOWN.
    """
    if snap.state == OnboardingState.WELCOME:
        return "Already at the first step. Nothing to restart."

    if snap.state == OnboardingState.READY:
        return "Onboarding is complete. Use `RESET ONBOARDING` to start over."

    if snap.state == OnboardingState.COOLDOWN and snap.is_in_cooldown():
        remaining = snap.cooldown_remaining()
        mins, secs = divmod(int(remaining), 60)
        return f"Cannot restart during cooldown. {mins}m {secs}s remaining."

    current = snap.state
    snap.temp_data = {}
    snap.last_error = None
    snap.save()

    return (
        f"Step **{current.value}** restarted. "
        f"{_format_state_label(current)}. Ready for input."
    )


# ------------------------------------------------------------------
# RESEND CODE
# ------------------------------------------------------------------

def _generate_code(length: int = 6) -> str:
    """Generate a random alphanumeric verification code."""
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


def _handle_resend_code(snap: OnboardingSnapshot) -> str:
    """Generate a new verification code with rate-limiting.

    Guards:
    - Only allowed in COMMS_VERIFY or CREDENTIALS_VERIFY states.
    - Max RESEND_MAX_ATTEMPTS resends per verification cycle.
    - Minimum RESEND_COOLDOWN_SECONDS between resends.

    The new code is stored as:
    - ``snap.verification_code_hash`` — SHA-256 for checking
    - ``snap.temp_data["pending_resend_code"]`` — plaintext for
      the orchestrator to dispatch via the active comms provider
    """
    # --- state guard ---
    allowed_states = {
        OnboardingState.COMMS_VERIFY,
        OnboardingState.CREDENTIALS_VERIFY,
    }
    if snap.state not in allowed_states:
        return (
            f"RESEND CODE is only available during verification steps. "
            f"Current state: **{snap.state.value}**."
        )

    # --- rate-limit: max attempts ---
    if snap.resend_count >= RESEND_MAX_ATTEMPTS:
        return (
            f"Maximum resend attempts reached ({RESEND_MAX_ATTEMPTS}). "
            "Use `RESTART STEP` to begin verification again."
        )

    # --- rate-limit: cooldown between resends ---
    if snap.last_resend_at is not None:
        elapsed = time.time() - snap.last_resend_at
        if elapsed < RESEND_COOLDOWN_SECONDS:
            wait = int(RESEND_COOLDOWN_SECONDS - elapsed)
            return f"Please wait **{wait}s** before requesting another code."

    # --- generate + persist ---
    code = _generate_code()
    snap.set_verification_code(code)
    snap.resend_count += 1
    snap.last_resend_at = time.time()
    snap.temp_data["pending_resend_code"] = code
    snap.save()

    remaining_attempts = RESEND_MAX_ATTEMPTS - snap.resend_count
    return (
        f"New verification code generated. "
        f"Check your communication channel. "
        f"({remaining_attempts} resend{'s' if remaining_attempts != 1 else ''} remaining)"
    )


# ------------------------------------------------------------------
# RESET ONBOARDING
# ------------------------------------------------------------------

def _handle_reset_onboarding(snap: OnboardingSnapshot) -> str:
    """Full reset — returns to WELCOME state.

    This is the nuclear option.  Allowed from any state, including
    COOLDOWN (even active), because the spec says "no dead-end states".
    """
    if snap.state == OnboardingState.WELCOME:
        return "Already at WELCOME. Nothing to reset."

    old_state = snap.state
    snap.reset()

    return (
        f"Onboarding reset from **{old_state.value}** to **WELCOME**. "
        "All provisioning state has been cleared. "
        "You can start fresh."
    )
