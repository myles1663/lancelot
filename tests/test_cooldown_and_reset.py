"""
Tests for LOCKDOWN→COOLDOWN replacement and RESET ONBOARDING (Prompt 5).
"""
import os
import pytest

from src.core.onboarding_snapshot import OnboardingSnapshot, OnboardingState
from src.core.recovery_commands import try_handle


# ==================================================================
# LOCKDOWN is gone
# ==================================================================

class TestLockdownRemoved:

    def test_no_lockdown_file_reference(self, tmp_data_dir):
        """OnboardingOrchestrator no longer creates a LOCKDOWN file."""
        from src.ui.onboarding import OnboardingOrchestrator
        orch = OnboardingOrchestrator(data_dir=str(tmp_data_dir))
        assert not hasattr(orch, "lock_file")

    def test_no_enter_lockdown_method(self, tmp_data_dir):
        from src.ui.onboarding import OnboardingOrchestrator
        orch = OnboardingOrchestrator(data_dir=str(tmp_data_dir))
        assert not hasattr(orch, "_enter_lockdown")

    def test_lockdown_state_not_in_enum(self):
        """LOCKDOWN is not a valid OnboardingState."""
        with pytest.raises(ValueError):
            OnboardingState("LOCKDOWN")


# ==================================================================
# COOLDOWN in orchestrator
# ==================================================================

class TestCooldownInOrchestrator:

    def test_enter_cooldown_sets_state(self, tmp_data_dir):
        from src.ui.onboarding import OnboardingOrchestrator
        orch = OnboardingOrchestrator(data_dir=str(tmp_data_dir))
        orch._enter_cooldown(60, "test failure")
        assert orch.state == "COOLDOWN"
        assert orch.snapshot.state == OnboardingState.COOLDOWN
        assert orch.snapshot.last_error == "test failure"

    def test_process_during_active_cooldown(self, tmp_data_dir):
        from src.ui.onboarding import OnboardingOrchestrator
        orch = OnboardingOrchestrator(data_dir=str(tmp_data_dir))
        orch._enter_cooldown(9999, "locked out")
        result = orch.process("user", "hello")
        assert "cooldown" in result.lower()
        assert "remaining" in result.lower()

    def test_process_after_cooldown_expires(self, tmp_data_dir):
        from src.ui.onboarding import OnboardingOrchestrator
        orch = OnboardingOrchestrator(data_dir=str(tmp_data_dir))
        orch._enter_cooldown(0, "instant")
        # After expiry, process should re-determine state and continue
        result = orch.process("user", "hello")
        assert "cooldown" not in result.lower()

    def test_status_works_during_cooldown(self, tmp_data_dir):
        from src.ui.onboarding import OnboardingOrchestrator
        orch = OnboardingOrchestrator(data_dir=str(tmp_data_dir))
        orch._enter_cooldown(300, "rate limit")
        result = orch.process("user", "STATUS")
        assert "COOLDOWN" in result
        assert "rate limit" in result

    def test_cooldown_persists_across_restart(self, tmp_data_dir):
        from src.ui.onboarding import OnboardingOrchestrator
        orch = OnboardingOrchestrator(data_dir=str(tmp_data_dir))
        orch._enter_cooldown(9999, "persist test")

        orch2 = OnboardingOrchestrator(data_dir=str(tmp_data_dir))
        assert orch2.state == "COOLDOWN"


# ==================================================================
# COOLDOWN is always recoverable
# ==================================================================

class TestCooldownRecoverable:

    def test_reset_escapes_cooldown(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.enter_cooldown(9999, "stuck")
        result = try_handle("reset onboarding", snap)
        assert snap.state == OnboardingState.WELCOME
        assert "reset" in result.lower()

    def test_cooldown_auto_recovers(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.enter_cooldown(0, "instant")
        assert snap.is_in_cooldown() is False
        assert snap.cooldown_remaining() == 0.0


# ==================================================================
# RESET ONBOARDING command
# ==================================================================

class TestResetOnboardingTrigger:

    def test_reset_onboarding_lowercase(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.COMMS_SELECTION)
        result = try_handle("reset onboarding", snap)
        assert result is not None
        assert "reset" in result.lower()

    def test_reset_onboarding_uppercase(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.READY)
        result = try_handle("RESET ONBOARDING", snap)
        assert result is not None

    def test_slash_reset_onboarding(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.FLAGSHIP_SELECTION)
        result = try_handle("/reset onboarding", snap)
        assert result is not None

    def test_reset_shorthand(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.CREDENTIALS_CAPTURE)
        result = try_handle("reset", snap)
        assert result is not None

    def test_slash_reset(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.COMMS_VERIFY)
        result = try_handle("/reset", snap)
        assert result is not None


class TestResetOnboardingBehavior:

    def test_resets_to_welcome(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.READY,
                        flagship_provider="gemini",
                        credential_status="verified")
        try_handle("reset onboarding", snap)
        assert snap.state == OnboardingState.WELCOME
        assert snap.flagship_provider is None
        assert snap.credential_status == "none"

    def test_clears_all_fields(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.COMMS_VERIFY,
                        flagship_provider="openai",
                        credential_status="captured",
                        local_model_status="installed",
                        temp_data={"key": "val"})
        snap.set_verification_code("ABC123")
        snap.resend_count = 2

        try_handle("reset onboarding", snap)
        assert snap.flagship_provider is None
        assert snap.credential_status == "none"
        assert snap.local_model_status == "none"
        assert snap.verification_code_hash is None
        assert snap.resend_count == 0
        assert snap.temp_data == {}

    def test_shows_old_state_in_message(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.FINAL_CHECKS)
        result = try_handle("reset onboarding", snap)
        assert "FINAL_CHECKS" in result
        assert "WELCOME" in result

    def test_persists_after_reset(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.READY, flagship_provider="anthropic")
        try_handle("reset onboarding", snap)

        snap2 = OnboardingSnapshot(str(tmp_data_dir))
        assert snap2.state == OnboardingState.WELCOME
        assert snap2.flagship_provider is None

    def test_noop_at_welcome(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        result = try_handle("reset onboarding", snap)
        assert "Already at WELCOME" in result

    def test_reset_from_cooldown(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.enter_cooldown(9999, "big failure")
        result = try_handle("reset onboarding", snap)
        assert snap.state == OnboardingState.WELCOME
        assert snap.cooldown_until is None

    @pytest.mark.parametrize("state", [s for s in OnboardingState if s != OnboardingState.WELCOME])
    def test_reset_works_from_every_non_welcome_state(self, tmp_data_dir, state):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.state = state
        snap.save()
        result = try_handle("reset onboarding", snap)
        assert snap.state == OnboardingState.WELCOME
        assert "reset" in result.lower()


# ==================================================================
# Integration: via OnboardingOrchestrator
# ==================================================================

class TestResetViaOrchestrator:

    def test_reset_intercepted(self, tmp_data_dir):
        from src.ui.onboarding import OnboardingOrchestrator
        orch = OnboardingOrchestrator(data_dir=str(tmp_data_dir))
        orch.snapshot.transition(OnboardingState.READY)
        result = orch.process("user", "RESET ONBOARDING")
        assert "reset" in result.lower()
        assert orch.snapshot.state == OnboardingState.WELCOME

    def test_reset_escapes_active_cooldown(self, tmp_data_dir):
        from src.ui.onboarding import OnboardingOrchestrator
        orch = OnboardingOrchestrator(data_dir=str(tmp_data_dir))
        orch._enter_cooldown(9999, "stuck")
        result = orch.process("user", "RESET ONBOARDING")
        assert orch.snapshot.state == OnboardingState.WELCOME
        assert "reset" in result.lower()
