"""
War Room Setup & Recovery Panel (v4 Upgrade — Prompt 7)

Provides a Streamlit-renderable recovery panel that reads from
the OnboardingSnapshot directly (same process, no HTTP).

Usage in war_room.py:
    from recovery_panel import render_recovery_panel
    render_recovery_panel(data_dir)
"""
import logging
from typing import Optional

from src.core.onboarding_snapshot import OnboardingSnapshot, OnboardingState
from src.core import recovery_commands

logger = logging.getLogger(__name__)


def get_panel_data(snap: OnboardingSnapshot) -> dict:
    """Build a dict of all data needed to render the recovery panel.

    This is the testable core — pure data, no Streamlit dependency.
    """
    cooldown_active = snap.is_in_cooldown()
    cooldown_remaining = snap.cooldown_remaining()

    # Determine available actions based on current state
    actions = []
    if snap.state != OnboardingState.WELCOME:
        actions.append("back")
        actions.append("restart_step")
    if snap.state in (OnboardingState.COMMS_VERIFY, OnboardingState.CREDENTIALS_VERIFY):
        actions.append("resend_code")
    if snap.state != OnboardingState.WELCOME:
        actions.append("reset_onboarding")

    # State progress as fraction (0.0 – 1.0) for progress bar
    from src.core.onboarding_snapshot import STATE_ORDER
    try:
        idx = STATE_ORDER.index(snap.state)
        # COOLDOWN is last in enum but not "progress" — treat it as current position
        progress = idx / (len(STATE_ORDER) - 2) if snap.state != OnboardingState.COOLDOWN else 0.0
        progress = min(1.0, max(0.0, progress))
    except ValueError:
        progress = 0.0

    return {
        "state": snap.state.value,
        "state_label": _state_label(snap.state),
        "flagship_provider": snap.flagship_provider or "Not selected",
        "credential_status": snap.credential_status,
        "local_model_status": snap.local_model_status,
        "is_ready": snap.is_ready,
        "cooldown_active": cooldown_active,
        "cooldown_remaining": round(cooldown_remaining, 1),
        "last_error": snap.last_error,
        "resend_count": snap.resend_count,
        "available_actions": actions,
        "progress": progress,
    }


def execute_action(snap: OnboardingSnapshot, action: str) -> str:
    """Execute a recovery action and return the response message.

    Maps UI button names to recovery commands.
    """
    command_map = {
        "back": "back",
        "restart_step": "restart step",
        "resend_code": "resend code",
        "reset_onboarding": "reset onboarding",
        "status": "status",
    }

    command = command_map.get(action)
    if command is None:
        return f"Unknown action: {action}"

    result = recovery_commands.try_handle(command, snap)
    if result is None:
        return f"Command not recognised: {command}"

    return result


def _state_label(state: OnboardingState) -> str:
    """Short human label for state."""
    labels = {
        OnboardingState.WELCOME: "Welcome",
        OnboardingState.FLAGSHIP_SELECTION: "Select Provider",
        OnboardingState.CREDENTIALS_CAPTURE: "Enter Credentials",
        OnboardingState.CREDENTIALS_VERIFY: "Verify Credentials",
        OnboardingState.LOCAL_UTILITY_SETUP: "Local Model Setup",
        OnboardingState.COMMS_SELECTION: "Select Comms Channel",
        OnboardingState.COMMS_CONFIGURE: "Configure Comms",
        OnboardingState.COMMS_VERIFY: "Verify Comms Link",
        OnboardingState.FINAL_CHECKS: "Final Checks",
        OnboardingState.READY: "System Ready",
        OnboardingState.COOLDOWN: "Cooldown",
    }
    return labels.get(state, state.value)


# ------------------------------------------------------------------
# Streamlit rendering (imported by war_room.py)
# ------------------------------------------------------------------

def render_recovery_panel(data_dir: str) -> None:
    """Render the Setup & Recovery panel inside a Streamlit app.

    Safe to call even if streamlit is not installed — import is deferred.
    """
    try:
        import streamlit as st
    except ImportError:
        return

    snap = OnboardingSnapshot(data_dir)
    data = get_panel_data(snap)

    st.subheader("Setup & Recovery")

    # Progress bar
    st.progress(data["progress"], text=f"Onboarding: {data['state_label']}")

    # Status cards
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("State", data["state"])
    with col2:
        st.metric("Provider", data["flagship_provider"])
    with col3:
        status_icon = "Verified" if data["credential_status"] == "verified" else data["credential_status"].title()
        st.metric("Credentials", status_icon)

    col4, col5 = st.columns(2)
    with col4:
        st.metric("Local Model", data["local_model_status"].title())
    with col5:
        if data["is_ready"]:
            st.success("System Ready")
        elif data["cooldown_active"]:
            st.warning(f"Cooldown: {data['cooldown_remaining']}s remaining")
        else:
            st.info(f"In progress: {data['state_label']}")

    # Error display
    if data["last_error"]:
        st.error(f"Last Error: {data['last_error']}")

    # Action buttons
    st.divider()
    st.caption("Recovery Actions")

    actions = data["available_actions"]
    if not actions:
        st.info("No recovery actions available at this state.")
        return

    btn_cols = st.columns(len(actions))

    action_labels = {
        "back": "Go Back",
        "restart_step": "Restart Step",
        "resend_code": "Resend Code",
        "reset_onboarding": "Reset Onboarding",
    }

    for i, action in enumerate(actions):
        with btn_cols[i]:
            label = action_labels.get(action, action)
            btn_type = "primary" if action == "reset_onboarding" else "secondary"
            if st.button(label, key=f"recovery_{action}", use_container_width=True,
                         type=btn_type):
                result = execute_action(snap, action)
                st.session_state["recovery_result"] = result
                st.rerun()

    # Show last action result
    if "recovery_result" in st.session_state:
        st.info(st.session_state["recovery_result"])
        del st.session_state["recovery_result"]
