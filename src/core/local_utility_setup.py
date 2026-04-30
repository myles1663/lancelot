"""
Local utility setup handler for the required onboarding flow.

Single-owner module for the mandatory local model install during onboarding.
Manages the full lifecycle: consent -> download -> checksum -> smoke test.

There is no skip path. The system does not reach READY without a verified
local model.
"""

import logging
import os

from src.core.onboarding_snapshot import OnboardingSnapshot, OnboardingState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sub-states tracked via snapshot.local_model_status
# ---------------------------------------------------------------------------
# none        -> initial, waiting for consent
# downloading -> download in progress
# downloaded  -> download complete, checksum verified
# verified    -> smoke test passed; ready to advance
# failed      -> something went wrong (retryable)


def handle_local_utility_setup(text: str, snapshot: OnboardingSnapshot) -> str:
    """Process user input during LOCAL_UTILITY_SETUP."""
    status = snapshot.local_model_status
    cmd = text.strip().lower()

    if status == "none":
        return _handle_consent(cmd, snapshot)
    if status == "downloading":
        return _handle_downloading(cmd, snapshot)
    if status == "downloaded":
        return _handle_smoke_test(cmd, snapshot)
    if status == "verified":
        return _advance_to_next(snapshot)
    if status == "failed":
        return _handle_failed(cmd, snapshot)

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

    model = _selected_model_summary()
    return (
        "**Local Utility Model Setup**\n\n"
        "Lancelot requires a local AI model for privacy-sensitive tasks:\n"
        "- Intent classification\n"
        "- JSON extraction\n"
        "- Summarization\n"
        "- PII redaction\n"
        "- RAG query rewriting\n\n"
        "This model runs **locally**; your data never leaves your machine.\n\n"
        f"**Model:** {model['display_name']} ({model['quantization']})\n"
        f"**Size:** {_size_label(model['size_mb'])} download\n"
        f"**License:** {model['license']} (open source)\n"
        f"**Source:** {model['source']}\n\n"
        "Type **yes** to download and install, or **info** for more details.\n\n"
        "*This step is mandatory. There is no skip path.*"
    )


def _explain_model() -> str:
    """Provide detailed information about the selected model profile."""
    model = _selected_model_summary()
    return (
        "**About the Local Utility Model**\n\n"
        "**What it does:**\n"
        "Handles utility tasks that should never touch external APIs: "
        "classification, extraction, summarization, and PII redaction.\n\n"
        "**How it works:**\n"
        f"- Downloaded from {model['source']}\n"
        "- Checksum verified with SHA-256 before use\n"
        f"- Runs inside a local Docker container ({model['engine']})\n"
        "- Never sends data externally\n\n"
        "**Requirements:**\n"
        f"- {_size_label(model['size_mb'])} disk space for the selected model\n"
        "- RAM or VRAM sized for the selected profile and context window\n"
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

    profile_name = os.environ.get("LOCAL_MODEL_PROFILE")

    try:
        lockfile_data = load_lockfile()
        info = get_model_info(lockfile_data, profile_name=profile_name)
    except LockfileError as exc:
        snapshot.local_model_status = "failed"
        snapshot.last_error = f"Lockfile error: {exc}"
        snapshot.save()
        return (
            "**Configuration Error**\n\n"
            f"Could not read model lockfile: {exc}\n\n"
            "Type **retry** to try again."
        )

    if is_model_present(lockfile_data=lockfile_data, profile_name=profile_name):
        snapshot.local_model_status = "downloaded"
        snapshot.save()
        return (
            "**Model Already Present**\n\n"
            f"- {_display_name(info)} ({info['quantization']}) checksum verified.\n\n"
            "Running smoke test... Type **test** to proceed."
        )

    snapshot.local_model_status = "downloading"
    snapshot.save()

    try:
        result_path = fetch_model(lockfile_data=lockfile_data, profile_name=profile_name)
        snapshot.local_model_status = "downloaded"
        snapshot.save()
        return (
            "**Download Complete**\n\n"
            f"- {_display_name(info)} ({info['quantization']})\n"
            "- SHA-256 checksum verified\n"
            f"- Saved to {result_path.name}\n\n"
            "Running smoke test... Type **test** to proceed."
        )
    except FetchError as exc:
        snapshot.local_model_status = "failed"
        snapshot.last_error = f"Download failed: {exc}"
        snapshot.save()
        return (
            "**Download Failed**\n\n"
            f"Error: {exc}\n\n"
            "Type **retry** to try again."
        )


def _handle_downloading(cmd: str, snapshot: OnboardingSnapshot) -> str:
    """Download was interrupted, usually by app restart."""
    return _start_download(snapshot)


def _handle_smoke_test(cmd: str, snapshot: OnboardingSnapshot) -> str:
    """Model downloaded; run smoke test."""
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
            "**Smoke Test Failed**\n\n"
            f"Error: {exc}\n\n"
            "Type **retry** to try again."
        )

    if passed:
        snapshot.local_model_status = "verified"
        snapshot.save()
        return _advance_to_next(snapshot)

    snapshot.local_model_status = "failed"
    snapshot.last_error = "Smoke test inference returned empty output"
    snapshot.save()
    return (
        "**Smoke Test Failed**\n\n"
        "The model loaded but did not produce valid output.\n\n"
        "Type **retry** to try again."
    )


def _handle_failed(cmd: str, snapshot: OnboardingSnapshot) -> str:
    """Something failed; offer retry."""
    if cmd in ("retry", "yes", "y", "again", "restart"):
        snapshot.local_model_status = "none"
        snapshot.last_error = None
        snapshot.save()
        return _start_download(snapshot)

    last_err = snapshot.last_error or "Unknown error"
    return (
        "**Local Model Setup Failed**\n\n"
        f"Last error: {last_err}\n\n"
        "Type **retry** to try again."
    )


def _advance_to_next(snapshot: OnboardingSnapshot) -> str:
    """Model verified; advance to the next onboarding state."""
    snapshot.transition(OnboardingState.COMMS_SELECTION)
    return (
        "**Local Utility Model Verified**\n\n"
        "The local AI model is installed, verified, and ready.\n\n"
        "Proceeding to communication channel setup..."
    )


def _selected_model_summary() -> dict:
    try:
        from local_models.lockfile import load_lockfile, get_model_info
        lockfile_data = load_lockfile()
        info = get_model_info(
            lockfile_data,
            profile_name=os.environ.get("LOCAL_MODEL_PROFILE"),
        )
    except Exception:
        return {
            "display_name": "Qwen3 8B",
            "quantization": "Q4_K_M",
            "size_mb": 5028,
            "license": "Apache 2.0",
            "source": "Qwen on Hugging Face",
            "engine": "llama_cpp_python",
        }

    source_url = info.get("source_url", "")
    if "prism-ml" in source_url:
        source = "Prism ML on Hugging Face"
    elif "huggingface.co/Qwen" in source_url:
        source = "Qwen on Hugging Face"
    else:
        source = "Hugging Face"

    return {
        "display_name": info.get("display_name") or info.get("name") or "local model",
        "quantization": info.get("quantization") or "GGUF",
        "size_mb": int(info.get("size_mb") or 0),
        "license": "Apache 2.0",
        "source": source,
        "engine": info.get("engine") or "llama_cpp_python",
    }


def _size_label(size_mb: int) -> str:
    if size_mb >= 1000:
        gb = size_mb / 1000
        if abs(gb - round(gb)) < 0.05:
            return f"~{round(gb)} GB"
        return f"~{gb:.1f} GB"
    return f"~{size_mb} MB"


def _display_name(info: dict) -> str:
    return info.get("display_name") or info.get("name") or "local model"
