"""
Schema validation and loading for local_models/models.lock.yaml.

Provides:
- load_lockfile()        — parse + validate the YAML lockfile
- load_prompt_template() — load a single prompt template by name
- load_all_prompts()     — load every prompt referenced in the lockfile
- validate_lockfile()    — structural validation (required keys, types)
"""

import os
import pathlib
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_PACKAGE_DIR = pathlib.Path(__file__).resolve().parent
LOCKFILE_PATH = _PACKAGE_DIR / "models.lock.yaml"
PROMPTS_DIR = _PACKAGE_DIR / "prompts"

# ---------------------------------------------------------------------------
# Required schema keys
# ---------------------------------------------------------------------------
_REQUIRED_MODEL_KEYS = {
    "name", "version", "quantization", "format", "filename", "size_mb",
    "checksum", "sources", "license",
}
_REQUIRED_CHECKSUM_KEYS = {"algorithm", "hash"}
_REQUIRED_SOURCE_KEYS = {"url", "provider"}
_REQUIRED_LICENSE_KEYS = {"model", "runtime"}
_REQUIRED_RUNTIME_KEYS = {"engine", "context_length", "threads", "gpu_layers"}


class LockfileError(Exception):
    """Raised when the lockfile is missing, malformed, or fails validation."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_lockfile(path=None):
    """Load and validate models.lock.yaml.

    Args:
        path: Optional override path to the YAML file.
              Defaults to local_models/models.lock.yaml.

    Returns:
        dict with validated lockfile contents.

    Raises:
        LockfileError on missing file, bad YAML, or schema violation.
    """
    path = pathlib.Path(path) if path else LOCKFILE_PATH
    if not path.exists():
        raise LockfileError(f"Lockfile not found: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise LockfileError(f"Invalid YAML in lockfile: {exc}") from exc

    if not isinstance(data, dict):
        raise LockfileError("Lockfile root must be a YAML mapping")

    validate_lockfile(data)
    return data


def validate_lockfile(data):
    """Validate structural integrity of parsed lockfile data.

    Raises LockfileError on any schema violation.
    """
    # --- model section ---
    if "model" not in data:
        raise LockfileError("Missing required top-level key: 'model'")
    model = data["model"]
    if not isinstance(model, dict):
        raise LockfileError("'model' must be a mapping")

    missing = _REQUIRED_MODEL_KEYS - set(model.keys())
    if missing:
        raise LockfileError(f"Missing model keys: {sorted(missing)}")

    # checksum
    cs = model["checksum"]
    if not isinstance(cs, dict):
        raise LockfileError("'model.checksum' must be a mapping")
    missing_cs = _REQUIRED_CHECKSUM_KEYS - set(cs.keys())
    if missing_cs:
        raise LockfileError(f"Missing checksum keys: {sorted(missing_cs)}")
    if cs["algorithm"] != "sha256":
        raise LockfileError(f"Unsupported checksum algorithm: {cs['algorithm']}")
    if not isinstance(cs["hash"], str) or len(cs["hash"]) != 64:
        raise LockfileError("Checksum hash must be a 64-character hex string")

    # sources
    sources = model["sources"]
    if not isinstance(sources, list) or len(sources) == 0:
        raise LockfileError("'model.sources' must be a non-empty list")
    for i, src in enumerate(sources):
        if not isinstance(src, dict):
            raise LockfileError(f"Source {i} must be a mapping")
        missing_src = _REQUIRED_SOURCE_KEYS - set(src.keys())
        if missing_src:
            raise LockfileError(f"Source {i} missing keys: {sorted(missing_src)}")

    # license
    lic = model["license"]
    if not isinstance(lic, dict):
        raise LockfileError("'model.license' must be a mapping")
    missing_lic = _REQUIRED_LICENSE_KEYS - set(lic.keys())
    if missing_lic:
        raise LockfileError(f"Missing license keys: {sorted(missing_lic)}")

    # size_mb must be positive int
    if not isinstance(model["size_mb"], int) or model["size_mb"] <= 0:
        raise LockfileError("'model.size_mb' must be a positive integer")

    # format must be gguf
    if model["format"] != "gguf":
        raise LockfileError(f"Unsupported model format: {model['format']}")

    # --- runtime section ---
    if "runtime" not in data:
        raise LockfileError("Missing required top-level key: 'runtime'")
    rt = data["runtime"]
    if not isinstance(rt, dict):
        raise LockfileError("'runtime' must be a mapping")
    missing_rt = _REQUIRED_RUNTIME_KEYS - set(rt.keys())
    if missing_rt:
        raise LockfileError(f"Missing runtime keys: {sorted(missing_rt)}")

    # --- prompts section ---
    if "prompts" not in data:
        raise LockfileError("Missing required top-level key: 'prompts'")
    prompts = data["prompts"]
    if not isinstance(prompts, list) or len(prompts) == 0:
        raise LockfileError("'prompts' must be a non-empty list")
    for p in prompts:
        if not isinstance(p, str):
            raise LockfileError(f"Prompt name must be a string, got: {type(p).__name__}")


def load_prompt_template(name, prompts_dir=None):
    """Load a prompt template by name.

    Args:
        name: Template name without extension (e.g., "classify_intent").
        prompts_dir: Optional override for the prompts directory.

    Returns:
        Template string with {placeholder} variables.

    Raises:
        LockfileError if the template file doesn't exist.
    """
    prompts_dir = pathlib.Path(prompts_dir) if prompts_dir else PROMPTS_DIR
    path = prompts_dir / f"{name}.txt"
    if not path.exists():
        raise LockfileError(f"Prompt template not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def load_all_prompts(lockfile_data=None, prompts_dir=None):
    """Load all prompt templates referenced in the lockfile.

    Args:
        lockfile_data: Pre-loaded lockfile dict (loads from disk if None).
        prompts_dir: Optional override for the prompts directory.

    Returns:
        dict mapping prompt name → template string.

    Raises:
        LockfileError on missing lockfile or missing prompt files.
    """
    if lockfile_data is None:
        lockfile_data = load_lockfile()

    prompt_names = lockfile_data.get("prompts", [])
    result = {}
    for name in prompt_names:
        result[name] = load_prompt_template(name, prompts_dir=prompts_dir)
    return result


def get_model_info(lockfile_data=None):
    """Extract model metadata from lockfile data.

    Returns dict with name, filename, size_mb, checksum_hash, source_url.
    """
    if lockfile_data is None:
        lockfile_data = load_lockfile()

    model = lockfile_data["model"]
    return {
        "name": model["name"],
        "filename": model["filename"],
        "size_mb": model["size_mb"],
        "checksum_hash": model["checksum"]["hash"],
        "source_url": model["sources"][0]["url"],
        "quantization": model["quantization"],
        "format": model["format"],
    }
