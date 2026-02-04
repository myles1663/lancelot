"""
Tests for verification code persistence and RESEND CODE command (Prompt 4).
"""
import time
import pytest

from src.core.onboarding_snapshot import OnboardingSnapshot, OnboardingState
from src.core.recovery_commands import (
    try_handle,
    RESEND_MAX_ATTEMPTS,
    RESEND_COOLDOWN_SECONDS,
)


# ==================================================================
# Verification code persistence (snapshot fields)
# ==================================================================

class TestCodePersistence:

    def test_resend_count_defaults_zero(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        assert snap.resend_count == 0

    def test_last_resend_at_defaults_none(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        assert snap.last_resend_at is None

    def test_resend_fields_round_trip(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.resend_count = 2
        snap.last_resend_at = 1700000000.0
        snap.save()

        snap2 = OnboardingSnapshot(str(tmp_data_dir))
        assert snap2.resend_count == 2
        assert snap2.last_resend_at == 1700000000.0

    def test_reset_clears_resend_fields(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.resend_count = 3
        snap.last_resend_at = time.time()
        snap.save()
        snap.reset()
        assert snap.resend_count == 0
        assert snap.last_resend_at is None

    def test_resend_fields_in_to_dict(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        d = snap.to_dict()
        assert "resend_count" in d
        assert "last_resend_at" in d


# ==================================================================
# RESEND CODE trigger recognition
# ==================================================================

class TestResendTrigger:

    def _at_verify(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.COMMS_VERIFY)
        snap.set_verification_code("ORIGINAL")
        return snap

    def test_resend_code_lowercase(self, tmp_data_dir):
        snap = self._at_verify(tmp_data_dir)
        result = try_handle("resend code", snap)
        assert result is not None
        assert "generated" in result

    def test_resend_code_uppercase(self, tmp_data_dir):
        snap = self._at_verify(tmp_data_dir)
        result = try_handle("RESEND CODE", snap)
        assert result is not None

    def test_slash_resend_code(self, tmp_data_dir):
        snap = self._at_verify(tmp_data_dir)
        result = try_handle("/resend code", snap)
        assert result is not None

    def test_resend_shorthand(self, tmp_data_dir):
        snap = self._at_verify(tmp_data_dir)
        result = try_handle("resend", snap)
        assert result is not None

    def test_slash_resend_shorthand(self, tmp_data_dir):
        snap = self._at_verify(tmp_data_dir)
        result = try_handle("/resend", snap)
        assert result is not None


# ==================================================================
# RESEND CODE behavior
# ==================================================================

class TestResendBehavior:

    def test_generates_new_code_hash(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.COMMS_VERIFY)
        snap.set_verification_code("OLDCODE")
        old_hash = snap.verification_code_hash

        try_handle("resend code", snap)
        assert snap.verification_code_hash != old_hash

    def test_stores_pending_code_in_temp_data(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.COMMS_VERIFY)
        try_handle("resend code", snap)
        code = snap.temp_data.get("pending_resend_code")
        assert code is not None
        assert len(code) == 6

    def test_pending_code_matches_hash(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.COMMS_VERIFY)
        try_handle("resend code", snap)
        code = snap.temp_data["pending_resend_code"]
        assert snap.check_verification_code(code) is True

    def test_increments_resend_count(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.COMMS_VERIFY)
        assert snap.resend_count == 0
        try_handle("resend code", snap)
        assert snap.resend_count == 1

    def test_sets_last_resend_at(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.COMMS_VERIFY)
        before = time.time()
        try_handle("resend code", snap)
        after = time.time()
        assert snap.last_resend_at is not None
        assert before <= snap.last_resend_at <= after

    def test_shows_remaining_attempts(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.COMMS_VERIFY)
        result = try_handle("resend code", snap)
        expected = RESEND_MAX_ATTEMPTS - 1
        assert f"{expected} resend" in result

    def test_persists_across_restart(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.COMMS_VERIFY)
        try_handle("resend code", snap)

        snap2 = OnboardingSnapshot(str(tmp_data_dir))
        assert snap2.resend_count == 1
        assert snap2.last_resend_at is not None

    def test_works_in_credentials_verify(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.CREDENTIALS_VERIFY)
        result = try_handle("resend code", snap)
        assert "generated" in result


# ==================================================================
# RESEND CODE rate-limiting
# ==================================================================

class TestResendRateLimiting:

    def test_max_attempts_blocks_resend(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.COMMS_VERIFY)
        snap.resend_count = RESEND_MAX_ATTEMPTS
        snap.save()

        result = try_handle("resend code", snap)
        assert "Maximum resend attempts" in result
        assert "RESTART STEP" in result

    def test_cooldown_between_resends(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.COMMS_VERIFY)
        snap.last_resend_at = time.time()  # just now
        snap.save()

        result = try_handle("resend code", snap)
        assert "wait" in result.lower()

    def test_cooldown_expired_allows_resend(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.COMMS_VERIFY)
        snap.last_resend_at = time.time() - RESEND_COOLDOWN_SECONDS - 1
        snap.save()

        result = try_handle("resend code", snap)
        assert "generated" in result

    def test_sequential_resends_up_to_max(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.COMMS_VERIFY)

        for i in range(RESEND_MAX_ATTEMPTS):
            # Bypass cooldown for test
            snap.last_resend_at = None
            snap.save()
            result = try_handle("resend code", snap)
            assert "generated" in result
            assert snap.resend_count == i + 1

        # Next attempt should fail
        snap.last_resend_at = None
        snap.save()
        result = try_handle("resend code", snap)
        assert "Maximum" in result


# ==================================================================
# RESEND CODE state guards
# ==================================================================

class TestResendGuards:

    @pytest.mark.parametrize("state", [
        OnboardingState.WELCOME,
        OnboardingState.FLAGSHIP_SELECTION,
        OnboardingState.CREDENTIALS_CAPTURE,
        OnboardingState.LOCAL_UTILITY_SETUP,
        OnboardingState.COMMS_SELECTION,
        OnboardingState.COMMS_CONFIGURE,
        OnboardingState.FINAL_CHECKS,
        OnboardingState.READY,
        OnboardingState.COOLDOWN,
    ])
    def test_blocked_outside_verify_states(self, tmp_data_dir, state):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.state = state
        snap.save()
        result = try_handle("resend code", snap)
        assert "only available during verification" in result


# ==================================================================
# Integration: via OnboardingOrchestrator
# ==================================================================

class TestResendViaOrchestrator:

    def test_resend_intercepted(self, tmp_data_dir):
        from src.ui.onboarding import OnboardingOrchestrator
        orch = OnboardingOrchestrator(data_dir=str(tmp_data_dir))
        orch.snapshot.transition(OnboardingState.COMMS_VERIFY)
        result = orch.process("testuser", "RESEND CODE")
        assert "generated" in result
