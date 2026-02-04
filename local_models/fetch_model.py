"""
Model download and checksum verification.

Downloads the GGUF model specified in models.lock.yaml from upstream,
verifies SHA-256 integrity, and stores it locally.  Weights are never
bundled — they are fetched with explicit user consent during onboarding.

Public API:
    fetch_model()    — download + verify (main entry point)
    verify_checksum() — standalone SHA-256 check on an existing file
    model_path()     — resolved path where the model should live
    is_model_present() — True if model exists and checksum matches
"""

import hashlib
import os
import pathlib
import shutil
import urllib.request

from local_models.lockfile import load_lockfile, get_model_info, LockfileError

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
_PACKAGE_DIR = pathlib.Path(__file__).resolve().parent
DEFAULT_MODELS_DIR = _PACKAGE_DIR / "weights"

# Chunk size for streaming download and hashing (64 KB)
_CHUNK_SIZE = 65_536


class FetchError(Exception):
    """Raised when model download or verification fails."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def model_path(models_dir=None, lockfile_data=None):
    """Return the expected local path for the model file.

    Args:
        models_dir: Directory where model weights are stored.
        lockfile_data: Pre-loaded lockfile dict (loads from disk if None).

    Returns:
        pathlib.Path to the model file (may not exist yet).
    """
    models_dir = pathlib.Path(models_dir) if models_dir else DEFAULT_MODELS_DIR
    info = get_model_info(lockfile_data)
    return models_dir / info["filename"]


def verify_checksum(file_path, expected_hash):
    """Verify a file's SHA-256 checksum.

    Args:
        file_path: Path to the file to check.
        expected_hash: Expected hex-encoded SHA-256 hash string.

    Returns:
        True if the checksum matches.

    Raises:
        FetchError if the file doesn't exist or checksum mismatches.
    """
    file_path = pathlib.Path(file_path)
    if not file_path.exists():
        raise FetchError(f"File not found: {file_path}")

    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(_CHUNK_SIZE)
            if not chunk:
                break
            sha.update(chunk)

    actual = sha.hexdigest()
    if actual != expected_hash:
        raise FetchError(
            f"Checksum mismatch for {file_path.name}:\n"
            f"  expected: {expected_hash}\n"
            f"  actual:   {actual}"
        )
    return True


def is_model_present(models_dir=None, lockfile_data=None):
    """Check whether the model file exists and has a valid checksum.

    Returns:
        True if the model is present and verified, False otherwise.
    """
    if lockfile_data is None:
        lockfile_data = load_lockfile()

    info = get_model_info(lockfile_data)
    path = model_path(models_dir=models_dir, lockfile_data=lockfile_data)

    if not path.exists():
        return False

    try:
        verify_checksum(path, info["checksum_hash"])
        return True
    except FetchError:
        return False


def fetch_model(
    models_dir=None,
    lockfile_data=None,
    progress_callback=None,
    force=False,
):
    """Download the model and verify its checksum.

    Args:
        models_dir: Directory to store the downloaded model.
                    Defaults to local_models/weights/.
        lockfile_data: Pre-loaded lockfile dict (loads from disk if None).
        progress_callback: Optional callable(bytes_downloaded, total_bytes)
                           called after each chunk for progress reporting.
        force: If True, re-download even if the model already exists
               and passes checksum.

    Returns:
        pathlib.Path to the verified model file.

    Raises:
        FetchError on download failure or checksum mismatch.
        LockfileError if the lockfile is invalid.
    """
    if lockfile_data is None:
        lockfile_data = load_lockfile()

    info = get_model_info(lockfile_data)
    models_dir = pathlib.Path(models_dir) if models_dir else DEFAULT_MODELS_DIR

    # Ensure target directory exists
    models_dir.mkdir(parents=True, exist_ok=True)

    dest = models_dir / info["filename"]

    # Skip download if already present and verified (unless forced)
    if not force and dest.exists():
        try:
            verify_checksum(dest, info["checksum_hash"])
            return dest
        except FetchError:
            # Existing file is corrupt — re-download
            pass

    # Download to a temp file first, then move atomically
    tmp_path = dest.with_suffix(".download")

    try:
        _download(info["source_url"], tmp_path, progress_callback)
    except Exception as exc:
        # Clean up partial download
        if tmp_path.exists():
            tmp_path.unlink()
        raise FetchError(f"Download failed: {exc}") from exc

    # Verify checksum before finalising
    try:
        verify_checksum(tmp_path, info["checksum_hash"])
    except FetchError:
        tmp_path.unlink()
        raise

    # Atomic move into place
    shutil.move(str(tmp_path), str(dest))
    return dest


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _download(url, dest_path, progress_callback=None):
    """Stream-download a URL to a local file.

    Args:
        url: Source URL.
        dest_path: Local file path to write to.
        progress_callback: Optional callable(bytes_downloaded, total_bytes).
    """
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Lancelot-Fetch/1.0")

    with urllib.request.urlopen(req, timeout=300) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0

        with open(dest_path, "wb") as out:
            while True:
                chunk = resp.read(_CHUNK_SIZE)
                if not chunk:
                    break
                out.write(chunk)
                downloaded += len(chunk)
                if progress_callback:
                    progress_callback(downloaded, total)
