"""
Tests for OnboardingSnapshot persistence (Prompt 1).
"""
import json
import os
import time
import pytest

from src.core.onboarding_snapshot import (
    OnboardingSnapshot,
    OnboardingState,
    STATE_ORDER,
    BACKABLE_STATES,
)


# ------------------------------------------------------------------
# Construction & defaults
# ------------------------------------------------------------------

class TestSnapshotDefaults:

    def test_fresh_snapshot_starts_at_welcome(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        assert snap.state == OnboardingState.WELCOME

    def test_default_field_values(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        assert snap.flagship_provider is None
        assert snap.credential_status == "none"
        assert snap.local_model_status == "none"
        assert snap.verification_code_hash is None
        assert snap.cooldown_until is None
        assert snap.last_error is None
        assert snap.temp_data == {}
        assert snap.is_ready is False


# ------------------------------------------------------------------
# Persistence round-trip
# ------------------------------------------------------------------

class TestPersistence:

    def test_save_creates_file(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.save()
        path = tmp_data_dir / OnboardingSnapshot.FILENAME
        assert path.exists()

    def test_round_trip_preserves_state(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.state = OnboardingState.CREDENTIALS_CAPTURE
        snap.flagship_provider = "openai"
        snap.credential_status = "captured"
        snap.local_model_status = "downloading"
        snap.temp_data = {"foo": "bar"}
        snap.last_error = "test error"
        snap.save()

        snap2 = OnboardingSnapshot(str(tmp_data_dir))
        assert snap2.state == OnboardingState.CREDENTIALS_CAPTURE
        assert snap2.flagship_provider == "openai"
        assert snap2.credential_status == "captured"
        assert snap2.local_model_status == "downloading"
        assert snap2.temp_data == {"foo": "bar"}
        assert snap2.last_error == "test error"

    def test_corrupt_snapshot_falls_back_to_welcome(self, tmp_data_dir):
        path = tmp_data_dir / OnboardingSnapshot.FILENAME
        path.write_text("{broken json!!")
        snap = OnboardingSnapshot(str(tmp_data_dir))
        assert snap.state == OnboardingState.WELCOME

    def test_missing_snapshot_starts_fresh(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        assert snap.state == OnboardingState.WELCOME

    def test_unknown_state_in_file_falls_back(self, tmp_data_dir):
        path = tmp_data_dir / OnboardingSnapshot.FILENAME
        path.write_text(json.dumps({"state": "NONEXISTENT_STATE"}))
        snap = OnboardingSnapshot(str(tmp_data_dir))
        assert snap.state == OnboardingState.WELCOME

    def test_atomic_write_no_partial(self, tmp_data_dir):
        """After save(), there should be no leftover .tmp file."""
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.save()
        tmp_file = tmp_data_dir / (OnboardingSnapshot.FILENAME + ".tmp")
        assert not tmp_file.exists()

    def test_updated_at_set_on_save(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        assert snap.updated_at is None
        before = time.time()
        snap.save()
        after = time.time()
        assert before <= snap.updated_at <= after


# ------------------------------------------------------------------
# State transitions
# ------------------------------------------------------------------

class TestTransitions:

    def test_transition_changes_state(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.FLAGSHIP_SELECTION)
        assert snap.state == OnboardingState.FLAGSHIP_SELECTION

    def test_transition_persists(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.COMMS_SELECTION,
                        flagship_provider="anthropic")

        snap2 = OnboardingSnapshot(str(tmp_data_dir))
        assert snap2.state == OnboardingState.COMMS_SELECTION
        assert snap2.flagship_provider == "anthropic"

    def test_transition_applies_kwargs(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.CREDENTIALS_VERIFY,
                        credential_status="verified",
                        flagship_provider="gemini")
        assert snap.credential_status == "verified"
        assert snap.flagship_provider == "gemini"

    def test_transition_to_ready(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.READY)
        assert snap.is_ready is True


# ------------------------------------------------------------------
# Verification codes
# ------------------------------------------------------------------

class TestVerificationCodes:

    def test_set_and_check_code(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.set_verification_code("ABC123")
        assert snap.check_verification_code("ABC123") is True
        assert snap.check_verification_code("WRONG") is False

    def test_code_stored_as_hash(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.set_verification_code("SECRET")
        assert snap.verification_code_hash is not None
        assert snap.verification_code_hash != "SECRET"
        assert len(snap.verification_code_hash) == 64  # SHA-256 hex

    def test_code_persists_across_restart(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.set_verification_code("XYZ789")

        snap2 = OnboardingSnapshot(str(tmp_data_dir))
        assert snap2.check_verification_code("XYZ789") is True

    def test_check_with_no_code_set(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        assert snap.check_verification_code("anything") is False


# ------------------------------------------------------------------
# Cooldown
# ------------------------------------------------------------------

class TestCooldown:

    def test_enter_cooldown(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.enter_cooldown(60, "rate limit")
        assert snap.state == OnboardingState.COOLDOWN
        assert snap.is_in_cooldown() is True
        assert snap.last_error == "rate limit"

    def test_cooldown_expires(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.enter_cooldown(0, "instant")
        # With 0-second cooldown, should already be expired
        assert snap.is_in_cooldown() is False
        assert snap.cooldown_remaining() == 0.0

    def test_cannot_leave_cooldown_early(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.enter_cooldown(9999, "locked out")
        with pytest.raises(ValueError, match="Cannot leave COOLDOWN"):
            snap.transition(OnboardingState.WELCOME)

    def test_cooldown_remaining_positive(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.enter_cooldown(300, "test")
        assert snap.cooldown_remaining() > 0

    def test_cooldown_persists(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.enter_cooldown(600, "persist test")

        snap2 = OnboardingSnapshot(str(tmp_data_dir))
        assert snap2.state == OnboardingState.COOLDOWN
        assert snap2.is_in_cooldown() is True


# ------------------------------------------------------------------
# Reset
# ------------------------------------------------------------------

class TestReset:

    def test_reset_clears_everything(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.COMMS_VERIFY,
                        flagship_provider="openai",
                        credential_status="verified",
                        temp_data={"key": "val"})
        snap.set_verification_code("CODE")

        snap.reset()
        assert snap.state == OnboardingState.WELCOME
        assert snap.flagship_provider is None
        assert snap.credential_status == "none"
        assert snap.local_model_status == "none"
        assert snap.verification_code_hash is None
        assert snap.cooldown_until is None
        assert snap.last_error is None
        assert snap.temp_data == {}

    def test_reset_persists(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.READY, flagship_provider="gemini")
        snap.reset()

        snap2 = OnboardingSnapshot(str(tmp_data_dir))
        assert snap2.state == OnboardingState.WELCOME
        assert snap2.flagship_provider is None


# ------------------------------------------------------------------
# Serialisation
# ------------------------------------------------------------------

class TestSerialisation:

    def test_to_dict_contains_all_fields(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        d = snap.to_dict()
        expected_keys = {
            "state", "flagship_provider", "credential_status",
            "local_model_status", "verification_code_hash",
            "resend_count", "last_resend_at",
            "cooldown_until", "last_error", "temp_data", "updated_at",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_is_json_safe(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        snap.transition(OnboardingState.FLAGSHIP_SELECTION)
        raw = json.dumps(snap.to_dict())
        assert isinstance(raw, str)

    def test_repr(self, tmp_data_dir):
        snap = OnboardingSnapshot(str(tmp_data_dir))
        r = repr(snap)
        assert "WELCOME" in r


# ------------------------------------------------------------------
# State enum helpers
# ------------------------------------------------------------------

class TestStateEnum:

    def test_state_order_matches_spec(self):
        assert STATE_ORDER[0] == OnboardingState.WELCOME
        assert STATE_ORDER[-1] == OnboardingState.COOLDOWN
        assert OnboardingState.READY in STATE_ORDER

    def test_backable_states_excludes_welcome_and_ready(self):
        assert OnboardingState.WELCOME not in BACKABLE_STATES
        assert OnboardingState.READY not in BACKABLE_STATES
        assert OnboardingState.COOLDOWN not in BACKABLE_STATES
