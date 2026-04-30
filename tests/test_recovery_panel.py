"""
Tests for War Room Setup & Recovery Panel (Prompt 7).

Tests the data-building and action-execution logic.
Streamlit rendering is tested via the data layer, not widget calls.
"""
import pytest

from src.core.onboarding_snapshot import OnboardingSnapshot, OnboardingState
from src.ui.recovery_panel import get_panel_data, execute_action


# ==================================================================
# Panel data structure
# ==================================================================

class TestPanelData:

    def test_contains_all_fields(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        data = get_panel_data(snap)
        expected_keys = {
            "state", "state_label", "flagship_provider",
            "credential_status", "local_model_status", "is_ready",
            "cooldown_active", "cooldown_remaining", "last_error",
            "resend_count", "available_actions", "progress",
        }
        assert expected_keys == set(data.keys())

    def test_welcome_state(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        data = get_panel_data(snap)
        assert data["state"] == "WELCOME"
        assert data["state_label"] == "Welcome"
        assert data["is_ready"] is False
        assert data["progress"] == 0.0

    def test_ready_state(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.READY,
                        flagship_provider="anthropic",
                        credential_status="verified",
                        local_model_status="verified")
        data = get_panel_data(snap)
        assert data["state"] == "READY"
        assert data["is_ready"] is True
        assert data["flagship_provider"] == "anthropic"
        assert data["progress"] == pytest.approx(1.0, abs=0.1)

    def test_mid_progress(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.COMMS_SELECTION)
        data = get_panel_data(snap)
        assert 0.0 < data["progress"] < 1.0

    def test_cooldown_data(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.enter_cooldown(300, "rate limit")
        data = get_panel_data(snap)
        assert data["cooldown_active"] is True
        assert data["cooldown_remaining"] > 0
        assert data["last_error"] == "rate limit"

    def test_default_provider_label(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        data = get_panel_data(snap)
        assert data["flagship_provider"] == "Not selected"


# ==================================================================
# Available actions per state
# ==================================================================

class TestAvailableActions:

    def test_no_actions_at_welcome(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        data = get_panel_data(snap)
        assert data["available_actions"] == []

    def test_back_available_after_welcome(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.FLAGSHIP_SELECTION)
        data = get_panel_data(snap)
        assert "back" in data["available_actions"]

    def test_restart_step_available(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.CREDENTIALS_CAPTURE)
        data = get_panel_data(snap)
        assert "restart_step" in data["available_actions"]

    def test_resend_code_at_comms_verify(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.COMMS_VERIFY)
        data = get_panel_data(snap)
        assert "resend_code" in data["available_actions"]

    def test_resend_code_at_creds_verify(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.CREDENTIALS_VERIFY)
        data = get_panel_data(snap)
        assert "resend_code" in data["available_actions"]

    def test_no_resend_code_at_other_states(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.COMMS_SELECTION)
        data = get_panel_data(snap)
        assert "resend_code" not in data["available_actions"]

    def test_reset_available_after_welcome(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.READY)
        data = get_panel_data(snap)
        assert "reset_onboarding" in data["available_actions"]

    @pytest.mark.parametrize("state", [s for s in OnboardingState if s != OnboardingState.WELCOME])
    def test_reset_always_available_outside_welcome(self, tmp_data_dir, state):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.state = state
        snap.save()
        data = get_panel_data(snap)
        assert "reset_onboarding" in data["available_actions"]


# ==================================================================
# Action execution
# ==================================================================

class TestExecuteAction:

    def test_back_action(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.FLAGSHIP_SELECTION)
        result = execute_action(snap, "back")
        assert "Moved back" in result
        assert snap.state == OnboardingState.WELCOME

    def test_restart_step_action(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.CREDENTIALS_CAPTURE,
                        temp_data={"key": "val"})
        result = execute_action(snap, "restart_step")
        assert "restarted" in result
        assert snap.temp_data == {}

    def test_resend_code_action(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.COMMS_VERIFY)
        result = execute_action(snap, "resend_code")
        assert "generated" in result

    def test_reset_action(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.READY, flagship_provider="openai")
        result = execute_action(snap, "reset_onboarding")
        assert "reset" in result.lower()
        assert snap.state == OnboardingState.WELCOME

    def test_status_action(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        result = execute_action(snap, "status")
        assert "System Status" in result

    def test_unknown_action(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        result = execute_action(snap, "explode")
        assert "Unknown action" in result


# ==================================================================
# State labels
# ==================================================================

class TestStateLabels:

    @pytest.mark.parametrize("state", list(OnboardingState))
    def test_every_state_has_label(self, tmp_data_dir, state):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.state = state
        snap.save()
        data = get_panel_data(snap)
        assert len(data["state_label"]) > 0
        assert data["state_label"] != state.value  # Label differs from raw enum
