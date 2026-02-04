"""
Tests for src.core.local_utility_setup — mandatory local model onboarding state.
Prompt 12: Mandatory LOCAL_UTILITY_SETUP Onboarding State.
"""

import pytest
from unittest.mock import patch, MagicMock

from src.core.onboarding_snapshot import OnboardingSnapshot, OnboardingState
from src.core.local_utility_setup import handle_local_utility_setup


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def snap(tmp_data_dir):
    """Snapshot positioned at LOCAL_UTILITY_SETUP."""
    s = OnboardingSnapshot(str(tmp_data_dir))
    s.transition(OnboardingState.LOCAL_UTILITY_SETUP)
    return s


# ===================================================================
# Consent phase (local_model_status == "none")
# ===================================================================

class TestConsentPhase:

    def test_shows_consent_prompt_on_entry(self, snap):
        resp = handle_local_utility_setup("", snap)
        assert "Local Utility Model Setup" in resp
        assert "mandatory" in resp.lower()
        assert "skip" in resp.lower()

    def test_shows_model_name_and_size(self, snap):
        resp = handle_local_utility_setup("", snap)
        assert "Hermes 2 Pro" in resp
        assert "4.4 GB" in resp

    def test_info_command_shows_details(self, snap):
        resp = handle_local_utility_setup("info", snap)
        assert "About the Local Utility Model" in resp
        assert "Apache 2.0" in resp
        assert "Docker" in resp

    def test_details_command_also_works(self, snap):
        resp = handle_local_utility_setup("details", snap)
        assert "About the Local Utility Model" in resp

    def test_random_input_shows_consent_again(self, snap):
        resp = handle_local_utility_setup("banana", snap)
        assert "Local Utility Model Setup" in resp

    def test_no_skip_path_mentioned(self, snap):
        resp = handle_local_utility_setup("skip", snap)
        # "skip" is not a valid command — should show consent again
        assert "mandatory" in resp.lower() or "Local Utility" in resp


# ===================================================================
# Consent → download (mocked)
# ===================================================================

class TestConsentToDownload:

    @patch("local_models.fetch_model.is_model_present", return_value=False)
    @patch("local_models.fetch_model.fetch_model")
    @patch("local_models.lockfile.load_lockfile")
    @patch("local_models.lockfile.get_model_info")
    def test_yes_triggers_download(self, mock_info, mock_lock, mock_fetch, mock_present, snap):
        mock_lock.return_value = {"model": {"name": "test"}}
        mock_info.return_value = {
            "name": "hermes-2-pro", "quantization": "Q4_K_M",
            "filename": "test.gguf", "size_mb": 100,
            "checksum_hash": "a" * 64, "source_url": "http://x",
            "format": "gguf",
        }
        mock_fetch.return_value = MagicMock(name="test.gguf")

        resp = handle_local_utility_setup("yes", snap)
        assert "Download Complete" in resp or "Already Present" in resp
        mock_fetch.assert_called_once()

    @pytest.mark.parametrize("cmd", ["yes", "y", "install", "proceed", "continue", "accept"])
    @patch("local_models.fetch_model.is_model_present", return_value=True)
    @patch("local_models.lockfile.load_lockfile")
    @patch("local_models.lockfile.get_model_info")
    def test_all_consent_triggers_work(self, mock_info, mock_lock, mock_present, cmd, snap):
        mock_lock.return_value = {"model": {"name": "test"}}
        mock_info.return_value = {
            "name": "hermes-2-pro", "quantization": "Q4_K_M",
            "filename": "t.gguf", "size_mb": 1,
            "checksum_hash": "a" * 64, "source_url": "http://x",
            "format": "gguf",
        }
        resp = handle_local_utility_setup(cmd, snap)
        assert "Already Present" in resp or "Download Complete" in resp

    @patch("local_models.fetch_model.is_model_present", return_value=True)
    @patch("local_models.lockfile.load_lockfile")
    @patch("local_models.lockfile.get_model_info")
    def test_already_present_skips_download(self, mock_info, mock_lock, mock_present, snap):
        mock_lock.return_value = {"model": {"name": "test"}}
        mock_info.return_value = {
            "name": "hermes-2-pro", "quantization": "Q4_K_M",
            "filename": "t.gguf", "size_mb": 1,
            "checksum_hash": "a" * 64, "source_url": "http://x",
            "format": "gguf",
        }
        resp = handle_local_utility_setup("yes", snap)
        assert "Already Present" in resp
        assert snap.local_model_status == "downloaded"


# ===================================================================
# Download failure
# ===================================================================

class TestDownloadFailure:

    @patch("local_models.fetch_model.is_model_present", return_value=False)
    @patch("local_models.fetch_model.fetch_model")
    @patch("local_models.lockfile.load_lockfile")
    @patch("local_models.lockfile.get_model_info")
    def test_download_error_sets_failed(self, mock_info, mock_lock, mock_fetch, mock_present, snap):
        from local_models.fetch_model import FetchError
        mock_lock.return_value = {"model": {"name": "test"}}
        mock_info.return_value = {
            "name": "test", "quantization": "Q4_K_M",
            "filename": "t.gguf", "size_mb": 1,
            "checksum_hash": "a" * 64, "source_url": "http://x",
            "format": "gguf",
        }
        mock_fetch.side_effect = FetchError("network timeout")

        resp = handle_local_utility_setup("yes", snap)
        assert "Download Failed" in resp
        assert snap.local_model_status == "failed"
        assert "network timeout" in snap.last_error

    @patch("local_models.lockfile.load_lockfile")
    def test_lockfile_error_sets_failed(self, mock_lock, snap):
        from local_models.lockfile import LockfileError
        mock_lock.side_effect = LockfileError("missing file")

        resp = handle_local_utility_setup("yes", snap)
        assert "Configuration Error" in resp
        assert snap.local_model_status == "failed"


# ===================================================================
# Failed state → retry
# ===================================================================

class TestFailedRetry:

    def test_shows_last_error(self, snap):
        snap.local_model_status = "failed"
        snap.last_error = "checksum mismatch"
        snap.save()

        resp = handle_local_utility_setup("what happened", snap)
        assert "checksum mismatch" in resp
        assert "retry" in resp.lower()

    @patch("local_models.fetch_model.is_model_present", return_value=True)
    @patch("local_models.lockfile.load_lockfile")
    @patch("local_models.lockfile.get_model_info")
    def test_retry_resets_and_reattempts(self, mock_info, mock_lock, mock_present, snap):
        snap.local_model_status = "failed"
        snap.last_error = "previous error"
        snap.save()

        mock_lock.return_value = {"model": {"name": "test"}}
        mock_info.return_value = {
            "name": "test", "quantization": "Q4_K_M",
            "filename": "t.gguf", "size_mb": 1,
            "checksum_hash": "a" * 64, "source_url": "http://x",
            "format": "gguf",
        }

        resp = handle_local_utility_setup("retry", snap)
        assert snap.last_error is None or "Already Present" in resp

    @pytest.mark.parametrize("cmd", ["retry", "yes", "y", "again", "restart"])
    def test_all_retry_triggers(self, cmd, snap):
        snap.local_model_status = "failed"
        snap.last_error = "err"
        snap.save()

        with patch("src.core.local_utility_setup._start_download") as mock_dl:
            mock_dl.return_value = "downloading..."
            resp = handle_local_utility_setup(cmd, snap)
            mock_dl.assert_called_once()


# ===================================================================
# Smoke test phase (local_model_status == "downloaded")
# ===================================================================

class TestSmokeTestPhase:

    def test_prompts_for_test_command(self, snap):
        snap.local_model_status = "downloaded"
        snap.save()

        resp = handle_local_utility_setup("hello", snap)
        assert "test" in resp.lower()

    @patch("local_models.smoke_test.quick_inference_check", return_value=True)
    def test_smoke_test_pass_advances(self, mock_check, snap):
        snap.local_model_status = "downloaded"
        snap.save()

        resp = handle_local_utility_setup("test", snap)
        assert snap.local_model_status == "verified"
        assert snap.state == OnboardingState.COMMS_SELECTION
        assert "Verified" in resp

    @patch("local_models.smoke_test.quick_inference_check", return_value=False)
    def test_smoke_test_fail_sets_failed(self, mock_check, snap):
        snap.local_model_status = "downloaded"
        snap.save()

        resp = handle_local_utility_setup("test", snap)
        assert snap.local_model_status == "failed"
        assert "Smoke Test Failed" in resp

    @patch("local_models.smoke_test.quick_inference_check")
    def test_smoke_test_exception_sets_failed(self, mock_check, snap):
        mock_check.side_effect = RuntimeError("GPU OOM")
        snap.local_model_status = "downloaded"
        snap.save()

        resp = handle_local_utility_setup("test", snap)
        assert snap.local_model_status == "failed"
        assert "GPU OOM" in resp

    @pytest.mark.parametrize("cmd", ["test", "verify", "check", "yes", "y", "proceed", "continue"])
    @patch("local_models.smoke_test.quick_inference_check", return_value=True)
    def test_all_test_triggers(self, mock_check, cmd, snap):
        snap.local_model_status = "downloaded"
        snap.save()

        resp = handle_local_utility_setup(cmd, snap)
        assert snap.local_model_status == "verified"


# ===================================================================
# Advancement to COMMS_SELECTION
# ===================================================================

class TestAdvancement:

    @patch("local_models.smoke_test.quick_inference_check", return_value=True)
    def test_transitions_to_comms_selection(self, mock_check, snap):
        snap.local_model_status = "downloaded"
        snap.save()

        handle_local_utility_setup("test", snap)
        assert snap.state == OnboardingState.COMMS_SELECTION

    @patch("local_models.smoke_test.quick_inference_check", return_value=True)
    def test_persists_verified_status(self, mock_check, snap):
        snap.local_model_status = "downloaded"
        snap.save()

        handle_local_utility_setup("test", snap)

        # Reload from disk
        reloaded = OnboardingSnapshot(snap.data_dir)
        assert reloaded.local_model_status == "verified"
        assert reloaded.state == OnboardingState.COMMS_SELECTION

    def test_verified_status_auto_advances(self, snap):
        snap.local_model_status = "verified"
        snap.save()

        resp = handle_local_utility_setup("", snap)
        assert snap.state == OnboardingState.COMMS_SELECTION
        assert "Verified" in resp


# ===================================================================
# Downloading sub-state (app restart mid-download)
# ===================================================================

class TestDownloadingRestart:

    @patch("local_models.fetch_model.is_model_present", return_value=True)
    @patch("local_models.lockfile.load_lockfile")
    @patch("local_models.lockfile.get_model_info")
    def test_downloading_reattempts(self, mock_info, mock_lock, mock_present, snap):
        snap.local_model_status = "downloading"
        snap.save()

        mock_lock.return_value = {"model": {"name": "test"}}
        mock_info.return_value = {
            "name": "test", "quantization": "Q4_K_M",
            "filename": "t.gguf", "size_mb": 1,
            "checksum_hash": "a" * 64, "source_url": "http://x",
            "format": "gguf",
        }

        resp = handle_local_utility_setup("", snap)
        assert "Already Present" in resp or "Download" in resp


# ===================================================================
# No skip path
# ===================================================================

class TestNoSkipPath:

    def test_skip_not_accepted(self, snap):
        resp = handle_local_utility_setup("skip", snap)
        # Should NOT advance past LOCAL_UTILITY_SETUP
        assert snap.state == OnboardingState.LOCAL_UTILITY_SETUP
        assert snap.local_model_status == "none"

    def test_no_not_accepted(self, snap):
        resp = handle_local_utility_setup("no", snap)
        assert snap.state == OnboardingState.LOCAL_UTILITY_SETUP

    def test_cancel_not_accepted(self, snap):
        resp = handle_local_utility_setup("cancel", snap)
        assert snap.state == OnboardingState.LOCAL_UTILITY_SETUP


# ===================================================================
# Orchestrator integration
# ===================================================================

class TestOrchestratorIntegration:

    def test_orchestrator_routes_to_handler(self, tmp_data_dir):
        from src.ui.onboarding import OnboardingOrchestrator

        orch = OnboardingOrchestrator(str(tmp_data_dir))
        orch.state = "LOCAL_UTILITY_SETUP"
        orch.snapshot.transition(OnboardingState.LOCAL_UTILITY_SETUP)

        resp = orch.process("user", "hello")
        assert "Local Utility Model Setup" in resp

    def test_orchestrator_preserves_snapshot(self, tmp_data_dir):
        from src.ui.onboarding import OnboardingOrchestrator

        orch = OnboardingOrchestrator(str(tmp_data_dir))
        orch.state = "LOCAL_UTILITY_SETUP"
        orch.snapshot.transition(OnboardingState.LOCAL_UTILITY_SETUP)

        orch.process("user", "info")
        assert orch.snapshot.state == OnboardingState.LOCAL_UTILITY_SETUP
