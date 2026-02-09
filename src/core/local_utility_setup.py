"""
LOCAL_UTILITY_SETUP onboarding handler (v4 Upgrade — Prompt 12).

Single-owner module for the mandatory local model install during onboarding.
Manages the full lifecycle: consent → download → checksum → smoke test.

There is NO skip path.  The system does not reach READY without a verified
local model.

Public API:
    handle_local_utility_setup(text, snapshot) → str
"""

import logging
from src.core.onboarding_snapshot import OnboardingSnapshot, OnboardingState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sub-states tracked via snapshot.local_model_status
# ---------------------------------------------------------------------------
# none         → initial, waiting for consent
# downloading  → download in progress
# downloaded   → download complete, checksum verified
# verified     → smoke test passed — ready to advance
# failed       → something went wrong (retryable)


def handle_local_utility_setup(text: str, snapshot: OnboardingSnapshot) -> str:
    """Process user input during LOCAL_UTILITY_SETUP.

    Returns a response string for the user.
    """
    status = snapshot.local_model_status
    cmd = text.strip().lower()

    if status == "none":
        return _handle_consent(cmd, snapshot)
    elif status == "downloading":
        return _handle_downloading(cmd, snapshot)
    elif status == "downloaded":
        return _handle_smoke_test(cmd, snapshot)
    elif status == "verified":
        return _advance_to_next(snapshot)
    elif status == "failed":
        return _handle_failed(cmd, snapshot)
    else:
        # Unknown status — reset to none
        snapshot.local_model_status = "none"
        snapshot.save()
        return handle_local_utility_setup(text, snapshot)


# ---------------------------------------------------------------------------
# Sub-state handlers
# ---------------------------------------------------------------------------

def _handle_consent(cmd: str, snapshot: OnboardingSnapshot) -> str:
    """Waiting for user consent to download the model."""
    if cmd in ("yes", "y", "install", "proceed", "continue", "accept"):
        return _start_download(snapshot)

    if cmd in ("info", "details", "what"):
        return _explain_model()

    # First visit or unrecognised input — show the consent prompt
    return (
        "**Local Utility Model Setup**\n\n"
        "Lancelot requires a local AI model for privacy-sensitive tasks:\n"
        "- Intent classification\n"
        "- JSON extraction\n"
        "- Summarization\n"
        "- PII redaction\n"
        "- RAG query rewriting\n\n"
        "This model runs **locally** — your data never leaves your machine.\n\n"
        "**Model:** Qwen3 8B (Q4_K_M quantisation, GPU accelerated)\n"
        "**Size:** ~5 GB download\n"
        "**License:** Apache 2.0 (open source)\n"
        "**Source:** Qwen on HuggingFace\n\n"
        "Type **yes** to download and install, or **info** for more details.\n\n"
        "*This step is mandatory — there is no skip path.*"
    )


def _explain_model() -> str:
    """Provide detailed information about the model."""
    return (
        "**About the Local Utility Model**\n\n"
        "**What it does:**\n"
        "Handles utility tasks that should never touch external APIs — "
        "classification, extraction, summarization, and PII redaction.\n\n"
        "**How it works:**\n"
        "- Downloaded from HuggingFace (official NousResearch repository)\n"
        "- Checksum verified (SHA-256) to ensure integrity\n"
        "- Runs inside a local Docker container (llama.cpp)\n"
        "- Never sends data externally\n\n"
        "**Requirements:**\n"
        "- ~5 GB disk space\n"
        "- ~8 GB VRAM (GPU) or ~10 GB RAM (CPU fallback)\n"
        "- Docker (already configured)\n\n"
        "**Licensing:**\n"
        "- Model weights: Apache 2.0\n"
        "- Runtime engine: MIT\n"
        "- No commercial restrictions\n\n"
        "Type **yes** to proceed with installation."
    )


def _start_download(snapshot: OnboardingSnapshot) -> str:
    """Initiate model download."""
    from local_models.lockfile import load_lockfile, get_model_info, LockfileError
    from local_models.fetch_model import (
        fetch_model, is_model_present, FetchError,
    )

    try:
        lockfile_data = load_lockfile()
        info = get_model_info(lockfile_data)
    except LockfileError as exc:
        snapshot.local_model_status = "failed"
        snapshot.last_error = f"Lockfile error: {exc}"
        snapshot.save()
        return (
            f"**Configuration Error**\n\n"
            f"Could not read model lockfile: {exc}\n\n"
            "Type **retry** to try again."
        )

    # Check if already downloaded and valid
    if is_model_present(lockfile_data=lockfile_data):
        snapshot.local_model_status = "downloaded"
        snapshot.save()
        return (
            f"**Model Already Present**\n\n"
            f"✓ {info['name']} ({info['quantization']}) — checksum verified.\n\n"
            "Running smoke test... Type **test** to proceed."
        )

    # Start download
    snapshot.local_model_status = "downloading"
    snapshot.save()

    try:
        result_path = fetch_model(lockfile_data=lockfile_data)
        snapshot.local_model_status = "downloaded"
        snapshot.save()
        return (
            f"**Download Complete**\n\n"
            f"✓ {info['name']} ({info['quantization']})\n"
            f"✓ SHA-256 checksum verified\n"
            f"✓ Saved to {result_path.name}\n\n"
            "Running smoke test... Type **test** to proceed."
        )
    except FetchError as exc:
        snapshot.local_model_status = "failed"
        snapshot.last_error = f"Download failed: {exc}"
        snapshot.save()
        return (
            f"**Download Failed**\n\n"
            f"Error: {exc}\n\n"
            "Type **retry** to try again."
        )


def _handle_downloading(cmd: str, snapshot: OnboardingSnapshot) -> str:
    """Download was interrupted (app restart mid-download)."""
    # Re-attempt download
    return _start_download(snapshot)


def _handle_smoke_test(cmd: str, snapshot: OnboardingSnapshot) -> str:
    """Model downloaded — run smoke test."""
    if cmd not in ("test", "verify", "check", "yes", "y", "proceed", "continue"):
        return (
            "Model is downloaded and checksum verified.\n\n"
            "Type **test** to run the inference smoke test."
        )

    from local_models.smoke_test import quick_inference_check

    try:
        passed = quick_inference_check()
    except Exception as exc:
        snapshot.local_model_status = "failed"
        snapshot.last_error = f"Smoke test error: {exc}"
        snapshot.save()
        return (
            f"**Smoke Test Failed**\n\n"
            f"Error: {exc}\n\n"
            "Type **retry** to try again."
        )

    if passed:
        snapshot.local_model_status = "verified"
        snapshot.save()
        return _advance_to_next(snapshot)
    else:
        snapshot.local_model_status = "failed"
        snapshot.last_error = "Smoke test inference returned empty output"
        snapshot.save()
        return (
            "**Smoke Test Failed**\n\n"
            "The model loaded but did not produce valid output.\n\n"
            "Type **retry** to try again."
        )


def _handle_failed(cmd: str, snapshot: OnboardingSnapshot) -> str:
    """Something failed — offer retry."""
    if cmd in ("retry", "yes", "y", "again", "restart"):
        snapshot.local_model_status = "none"
        snapshot.last_error = None
        snapshot.save()
        return _start_download(snapshot)

    last_err = snapshot.last_error or "Unknown error"
    return (
        f"**Local Model Setup Failed**\n\n"
        f"Last error: {last_err}\n\n"
        "Type **retry** to try again."
    )


def _advance_to_next(snapshot: OnboardingSnapshot) -> str:
    """Model verified — advance to the next onboarding state."""
    snapshot.transition(OnboardingState.COMMS_SELECTION)
    return (
        "**Local Utility Model Verified** ✓\n\n"
        "The local AI model is installed, verified, and ready.\n\n"
        "Proceeding to communication channel setup..."
    )
