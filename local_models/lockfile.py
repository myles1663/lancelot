"""
Schema validation and loading for local_models/models.lock.yaml.

Provides:
- load_lockfile()        - parse and validate the YAML lockfile
- load_prompt_template() - load a single prompt template by name
- load_all_prompts()     - load every prompt referenced in the lockfile
- validate_lockfile()    - structural validation
- get_model_info()       - selected model metadata
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
_PROFILE_ENV = "LOCAL_MODEL_PROFILE"


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
    if "model" not in data:
        raise LockfileError("Missing required top-level key: 'model'")
    model = data["model"]
    if not isinstance(model, dict):
        raise LockfileError("'model' must be a mapping")
    _validate_model_mapping(model, path="model")

    if "runtime" not in data:
        raise LockfileError("Missing required top-level key: 'runtime'")
    _validate_runtime_mapping(data["runtime"], path="runtime")

    profiles = data.get("profiles")
    if profiles is not None:
        if not isinstance(profiles, dict) or not profiles:
            raise LockfileError("'profiles' must be a non-empty mapping")
        for profile_name, profile in profiles.items():
            if not isinstance(profile_name, str) or not profile_name.strip():
                raise LockfileError("Profile name must be a non-empty string")
            if not isinstance(profile, dict):
                raise LockfileError(f"Profile '{profile_name}' must be a mapping")
            _validate_model_mapping(profile, path=f"profiles.{profile_name}")
            profile_runtime = profile.get("runtime")
            if profile_runtime is not None:
                _validate_runtime_mapping(
                    profile_runtime,
                    path=f"profiles.{profile_name}.runtime",
                )

        default_profile = data.get("default_profile")
        if default_profile is not None:
            if not isinstance(default_profile, str):
                raise LockfileError("'default_profile' must be a string")
            if default_profile not in profiles:
                raise LockfileError(
                    f"default_profile '{default_profile}' is not defined in profiles"
                )

    if "prompts" not in data:
        raise LockfileError("Missing required top-level key: 'prompts'")
    prompts = data["prompts"]
    if not isinstance(prompts, list) or len(prompts) == 0:
        raise LockfileError("'prompts' must be a non-empty list")
    for prompt in prompts:
        if not isinstance(prompt, str):
            raise LockfileError(
                f"Prompt name must be a string, got: {type(prompt).__name__}"
            )


def load_prompt_template(name, prompts_dir=None):
    """Load a prompt template by name.

    Args:
        name: Template name without extension (e.g., "classify_intent").
        prompts_dir: Optional override for the prompts directory.

    Returns:
        Template string with {placeholder} variables.

    Raises:
        LockfileError if the template file does not exist.
    """
    prompts_dir = pathlib.Path(prompts_dir) if prompts_dir else PROMPTS_DIR
    path = prompts_dir / f"{name}.txt"
    if not path.exists():
        raise LockfileError(f"Prompt template not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def load_all_prompts(lockfile_data=None, prompts_dir=None):
    """Load all prompt templates referenced in the lockfile."""
    if lockfile_data is None:
        lockfile_data = load_lockfile()

    prompt_names = lockfile_data.get("prompts", [])
    result = {}
    for name in prompt_names:
        result[name] = load_prompt_template(name, prompts_dir=prompts_dir)
    return result


def get_model_profile(lockfile_data=None, profile_name=None, *, use_env=None):
    """Return selected model profile and merged runtime metadata.

    The legacy top-level ``model`` remains the default. Deployments can select
    a named profile with LOCAL_MODEL_PROFILE or an explicit profile_name.
    """
    loaded_from_default = lockfile_data is None
    if lockfile_data is None:
        lockfile_data = load_lockfile()
    if use_env is None:
        use_env = loaded_from_default

    selected = profile_name
    if selected is None and use_env:
        selected = os.environ.get(_PROFILE_ENV)
    if selected is None:
        selected = lockfile_data.get("default_profile")

    profiles = lockfile_data.get("profiles") or {}
    if selected:
        if selected not in profiles:
            raise LockfileError(f"Unknown local model profile: {selected}")
        model = profiles[selected]
        profile_id = selected
    else:
        model = lockfile_data["model"]
        profile_id = model["name"]

    runtime = dict(lockfile_data["runtime"])
    profile_runtime = model.get("runtime")
    if isinstance(profile_runtime, dict):
        runtime.update(profile_runtime)

    return {
        "profile": profile_id,
        "model": model,
        "runtime": runtime,
    }


def get_model_info(lockfile_data=None, profile_name=None):
    """Extract selected model metadata from lockfile data."""
    profile = get_model_profile(lockfile_data, profile_name=profile_name)
    model = profile["model"]
    runtime = profile["runtime"]
    return {
        "name": model["name"],
        "display_name": model.get("display_name", model["name"]),
        "profile": profile["profile"],
        "filename": model["filename"],
        "size_mb": model["size_mb"],
        "checksum_hash": model["checksum"]["hash"],
        "source_url": model["sources"][0]["url"],
        "quantization": model["quantization"],
        "format": model["format"],
        "runtime": runtime,
        "engine": runtime["engine"],
        "context_length": runtime["context_length"],
        "threads": runtime["threads"],
        "gpu_layers": runtime["gpu_layers"],
    }


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_model_mapping(model, *, path: str):
    missing = _REQUIRED_MODEL_KEYS - set(model.keys())
    if missing:
        raise LockfileError(f"Missing model keys at {path}: {sorted(missing)}")

    checksum = model["checksum"]
    if not isinstance(checksum, dict):
        raise LockfileError(f"'{path}.checksum' must be a mapping")
    missing_checksum = _REQUIRED_CHECKSUM_KEYS - set(checksum.keys())
    if missing_checksum:
        raise LockfileError(
            f"Missing checksum keys at {path}: {sorted(missing_checksum)}"
        )
    if checksum["algorithm"] != "sha256":
        raise LockfileError(
            f"Unsupported checksum algorithm at {path}: {checksum['algorithm']}"
        )
    hash_value = checksum["hash"]
    if (
        not isinstance(hash_value, str)
        or len(hash_value) != 64
        or any(ch not in "0123456789abcdef" for ch in hash_value.lower())
    ):
        raise LockfileError(
            f"Checksum hash at {path} must be a 64-character hex string"
        )

    sources = model["sources"]
    if not isinstance(sources, list) or len(sources) == 0:
        raise LockfileError(f"'{path}.sources' must be a non-empty list")
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise LockfileError(f"Source {index} at {path} must be a mapping")
        missing_source = _REQUIRED_SOURCE_KEYS - set(source.keys())
        if missing_source:
            raise LockfileError(
                f"Source {index} at {path} missing keys: {sorted(missing_source)}"
            )

    license_info = model["license"]
    if not isinstance(license_info, dict):
        raise LockfileError(f"'{path}.license' must be a mapping")
    missing_license = _REQUIRED_LICENSE_KEYS - set(license_info.keys())
    if missing_license:
        raise LockfileError(
            f"Missing license keys at {path}: {sorted(missing_license)}"
        )

    if not isinstance(model["size_mb"], int) or model["size_mb"] <= 0:
        raise LockfileError(f"'{path}.size_mb' must be a positive integer")

    if model["format"] != "gguf":
        raise LockfileError(f"Unsupported model format at {path}: {model['format']}")


def _validate_runtime_mapping(runtime, *, path: str):
    if not isinstance(runtime, dict):
        raise LockfileError(f"'{path}' must be a mapping")
    missing_runtime = _REQUIRED_RUNTIME_KEYS - set(runtime.keys())
    if missing_runtime:
        raise LockfileError(
            f"Missing runtime keys at {path}: {sorted(missing_runtime)}"
        )
