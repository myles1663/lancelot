"""
Tests for the STATUS recovery command (Prompt 2).
"""
import pytest

from src.core.onboarding_snapshot import OnboardingSnapshot, OnboardingState
from src.core.recovery_commands import try_handle, _format_status


# ------------------------------------------------------------------
# STATUS trigger recognition
# ------------------------------------------------------------------

class TestStatusTrigger:

    def test_status_lowercase(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        result = try_handle("status", snap)
        assert result is not None
        assert "System Status" in result

    def test_status_uppercase(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        result = try_handle("STATUS", snap)
        assert result is not None

    def test_status_mixed_case(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        result = try_handle("Status", snap)
        assert result is not None

    def test_slash_status(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        result = try_handle("/status", snap)
        assert result is not None

    def test_system_status(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        result = try_handle("system status", snap)
        assert result is not None

    def test_status_with_whitespace(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        result = try_handle("  status  ", snap)
        assert result is not None

    def test_non_command_returns_none(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        assert try_handle("hello", snap) is None
        assert try_handle("set up my account", snap) is None
        assert try_handle("", snap) is None


# ------------------------------------------------------------------
# STATUS output content
# ------------------------------------------------------------------

class TestStatusContent:

    def test_shows_welcome_state(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        result = try_handle("status", snap)
        assert "WELCOME" in result
        assert "waiting for identity" in result

    def test_shows_flagship_provider(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.CREDENTIALS_CAPTURE,
                        flagship_provider="openai")
        result = try_handle("status", snap)
        assert "openai" in result

    def test_shows_not_selected_when_no_provider(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        result = try_handle("status", snap)
        assert "not selected" in result

    def test_shows_credential_status(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.CREDENTIALS_VERIFY,
                        credential_status="verified")
        result = try_handle("status", snap)
        assert "verified" in result

    def test_shows_local_model_status(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.LOCAL_UTILITY_SETUP,
                        local_model_status="downloading")
        result = try_handle("status", snap)
        assert "downloading" in result

    def test_shows_cooldown_remaining(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.enter_cooldown(300, "rate limit")
        result = try_handle("status", snap)
        assert "Cooldown Remaining" in result
        assert "rate limit" in result

    def test_shows_cooldown_expired(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.enter_cooldown(0, "done")
        result = try_handle("status", snap)
        assert "expired" in result

    def test_shows_last_error(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.last_error = "connection refused"
        snap.save()
        result = try_handle("status", snap)
        assert "connection refused" in result

    def test_no_error_section_when_none(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        result = try_handle("status", snap)
        assert "Last Error" not in result

    def test_shows_available_commands(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        result = try_handle("status", snap)
        assert "STATUS" in result
        assert "BACK" in result
        assert "RESTART STEP" in result
        assert "RESET ONBOARDING" in result

    def test_shows_ready_state(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.READY,
                        flagship_provider="anthropic",
                        credential_status="verified",
                        local_model_status="verified")
        result = try_handle("status", snap)
        assert "READY" in result
        assert "System ready" in result
        assert "anthropic" in result


# ------------------------------------------------------------------
# STATUS works at every state
# ------------------------------------------------------------------

class TestStatusAtEveryState:

    @pytest.mark.parametrize("state", list(OnboardingState))
    def test_status_available_at_all_states(self, tmp_data_dir, state):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        # Force state directly for testing (bypass transition guards)
        snap.state = state
        snap.save()
        result = try_handle("status", snap)
        assert result is not None
        assert state.value in result


# ------------------------------------------------------------------
# Integration: STATUS via OnboardingOrchestrator.process()
# ------------------------------------------------------------------

class TestStatusViaOrchestrator:

    def test_process_intercepts_status(self, tmp_data_dir):
        from src.ui.onboarding import OnboardingOrchestrator
        orch = OnboardingOrchestrator(data_dir=str(tmp_data_dir))
        result = orch.process("testuser", "STATUS")
        assert "System Status" in result

    def test_process_non_command_falls_through(self, tmp_data_dir):
        from src.ui.onboarding import OnboardingOrchestrator
        orch = OnboardingOrchestrator(data_dir=str(tmp_data_dir))
        # WELCOME state expects a name, returns identity bonding message
        result = orch.process("testuser", "hello")
        assert "System Status" not in result
