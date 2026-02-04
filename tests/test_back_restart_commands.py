"""
Tests for BACK and RESTART STEP recovery commands (Prompt 3).
"""
import pytest

from src.core.onboarding_snapshot import OnboardingSnapshot, OnboardingState, STATE_ORDER
from src.core.recovery_commands import try_handle


# ==================================================================
# BACK command
# ==================================================================

class TestBackTrigger:

    def test_back_lowercase(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.FLAGSHIP_SELECTION)
        result = try_handle("back", snap)
        assert result is not None

    def test_back_uppercase(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.FLAGSHIP_SELECTION)
        result = try_handle("BACK", snap)
        assert result is not None

    def test_slash_back(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.FLAGSHIP_SELECTION)
        result = try_handle("/back", snap)
        assert result is not None

    def test_go_back(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.FLAGSHIP_SELECTION)
        result = try_handle("go back", snap)
        assert result is not None

    def test_back_with_whitespace(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.FLAGSHIP_SELECTION)
        result = try_handle("  back  ", snap)
        assert result is not None


class TestBackBehavior:

    def test_back_from_flagship_to_welcome(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.FLAGSHIP_SELECTION)
        result = try_handle("back", snap)
        assert snap.state == OnboardingState.WELCOME
        assert "WELCOME" in result

    def test_back_from_credentials_capture(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.CREDENTIALS_CAPTURE)
        try_handle("back", snap)
        assert snap.state == OnboardingState.FLAGSHIP_SELECTION

    def test_back_from_comms_verify(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.COMMS_VERIFY)
        try_handle("back", snap)
        assert snap.state == OnboardingState.COMMS_CONFIGURE

    def test_back_from_final_checks(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.FINAL_CHECKS)
        try_handle("back", snap)
        assert snap.state == OnboardingState.COMMS_VERIFY

    def test_back_clears_last_error(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.CREDENTIALS_VERIFY, last_error="bad key")
        try_handle("back", snap)
        assert snap.last_error is None

    def test_back_persists_new_state(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.COMMS_SELECTION)
        try_handle("back", snap)

        snap2 = OnboardingSnapshot(str(tmp_data_dir))
        assert snap2.state == OnboardingState.LOCAL_UTILITY_SETUP

    def test_back_shows_moved_message(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.CREDENTIALS_CAPTURE)
        result = try_handle("back", snap)
        assert "Moved back" in result
        assert "CREDENTIALS_CAPTURE" in result
        assert "FLAGSHIP_SELECTION" in result


class TestBackGuards:

    def test_cannot_back_from_welcome(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        result = try_handle("back", snap)
        assert snap.state == OnboardingState.WELCOME
        assert "Cannot go back" in result
        assert "first step" in result

    def test_cannot_back_from_ready(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.READY)
        result = try_handle("back", snap)
        assert snap.state == OnboardingState.READY
        assert "Cannot go back" in result
        assert "RESET ONBOARDING" in result

    def test_cannot_back_from_cooldown(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.enter_cooldown(300, "rate limit")
        result = try_handle("back", snap)
        assert snap.state == OnboardingState.COOLDOWN
        assert "Cannot go back" in result
        assert "cooldown" in result.lower()


class TestBackChaining:
    """Verify multiple BACK commands walk through the progression."""

    def test_back_three_times(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.CREDENTIALS_VERIFY)

        try_handle("back", snap)
        assert snap.state == OnboardingState.CREDENTIALS_CAPTURE

        try_handle("back", snap)
        assert snap.state == OnboardingState.FLAGSHIP_SELECTION

        try_handle("back", snap)
        assert snap.state == OnboardingState.WELCOME

        # One more — should be denied
        result = try_handle("back", snap)
        assert snap.state == OnboardingState.WELCOME
        assert "Cannot" in result


# ==================================================================
# RESTART STEP command
# ==================================================================

class TestRestartStepTrigger:

    def test_restart_step_lowercase(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.FLAGSHIP_SELECTION)
        result = try_handle("restart step", snap)
        assert result is not None

    def test_restart_step_uppercase(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.FLAGSHIP_SELECTION)
        result = try_handle("RESTART STEP", snap)
        assert result is not None

    def test_slash_restart_step(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.FLAGSHIP_SELECTION)
        result = try_handle("/restart step", snap)
        assert result is not None

    def test_restart_shorthand(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.FLAGSHIP_SELECTION)
        result = try_handle("restart", snap)
        assert result is not None

    def test_slash_restart_shorthand(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.FLAGSHIP_SELECTION)
        result = try_handle("/restart", snap)
        assert result is not None


class TestRestartStepBehavior:

    def test_restart_clears_temp_data(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.COMMS_CONFIGURE,
                        temp_data={"token": "abc123"})
        try_handle("restart step", snap)
        assert snap.temp_data == {}

    def test_restart_clears_last_error(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.CREDENTIALS_VERIFY,
                        last_error="invalid key")
        try_handle("restart step", snap)
        assert snap.last_error is None

    def test_restart_keeps_same_state(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.LOCAL_UTILITY_SETUP)
        try_handle("restart step", snap)
        assert snap.state == OnboardingState.LOCAL_UTILITY_SETUP

    def test_restart_persists(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.COMMS_VERIFY,
                        temp_data={"code": "XYZ"})
        try_handle("restart step", snap)

        snap2 = OnboardingSnapshot(str(tmp_data_dir))
        assert snap2.state == OnboardingState.COMMS_VERIFY
        assert snap2.temp_data == {}

    def test_restart_shows_confirmation(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.CREDENTIALS_CAPTURE)
        result = try_handle("restart step", snap)
        assert "restarted" in result
        assert "CREDENTIALS_CAPTURE" in result


class TestRestartStepGuards:

    def test_cannot_restart_welcome(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        result = try_handle("restart step", snap)
        assert "first step" in result

    def test_cannot_restart_ready(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.READY)
        result = try_handle("restart step", snap)
        assert "complete" in result
        assert "RESET ONBOARDING" in result

    def test_cannot_restart_during_active_cooldown(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.enter_cooldown(600, "too many failures")
        result = try_handle("restart step", snap)
        assert "cooldown" in result.lower()
        assert "remaining" in result.lower()

    def test_can_restart_expired_cooldown(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.enter_cooldown(0, "instant")
        result = try_handle("restart step", snap)
        assert "restarted" in result


# ==================================================================
# Integration: via OnboardingOrchestrator.process()
# ==================================================================

class TestViaOrchestrator:

    def test_back_intercepted(self, tmp_data_dir):
        from src.ui.onboarding import OnboardingOrchestrator
        orch = OnboardingOrchestrator(data_dir=str(tmp_data_dir))
        orch.snapshot.transition(OnboardingState.FLAGSHIP_SELECTION)
        result = orch.process("testuser", "BACK")
        assert "Moved back" in result

    def test_restart_step_intercepted(self, tmp_data_dir):
        from src.ui.onboarding import OnboardingOrchestrator
        orch = OnboardingOrchestrator(data_dir=str(tmp_data_dir))
        orch.snapshot.transition(OnboardingState.CREDENTIALS_CAPTURE)
        result = orch.process("testuser", "RESTART STEP")
        assert "restarted" in result
