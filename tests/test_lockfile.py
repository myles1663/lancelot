"""
Tests for local_models.lockfile — schema validation and prompt loading.
Prompt 8: Local Model Package Scaffold.
"""

import copy
import os
import pathlib
import pytest
import yaml

from local_models.lockfile import (
    load_lockfile,
    validate_lockfile,
    load_prompt_template,
    load_all_prompts,
    get_model_info,
    LockfileError,
    LOCKFILE_PATH,
    PROMPTS_DIR,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def valid_lockfile_data():
    """Return a minimal valid lockfile dict for testing."""
    return {
        "model": {
            "name": "hermes-2-pro-mistral-7b",
            "version": "2.0",
            "quantization": "Q4_K_M",
            "format": "gguf",
            "filename": "Hermes-2-Pro-Mistral-7B.Q4_K_M.gguf",
            "size_mb": 4370,
            "checksum": {
                "algorithm": "sha256",
                "hash": "e1e4253b94e3c04c7b6544250f29ad864a56eb2126e61eb440991a8284453674",
            },
            "sources": [
                {
                    "url": "https://huggingface.co/NousResearch/Hermes-2-Pro-Mistral-7B-GGUF/resolve/main/Hermes-2-Pro-Mistral-7B.Q4_K_M.gguf",
                    "provider": "huggingface",
                }
            ],
            "license": {
                "model": "Apache-2.0",
                "runtime": "MIT",
            },
        },
        "runtime": {
            "engine": "llama.cpp",
            "context_length": 4096,
            "threads": 4,
            "gpu_layers": 0,
        },
        "prompts": [
            "classify_intent",
            "extract_json",
            "summarize_internal",
            "redact",
            "rag_rewrite",
        ],
    }


@pytest.fixture
def lockfile_on_disk(tmp_path, valid_lockfile_data):
    """Write a valid lockfile to tmp dir and return its path."""
    path = tmp_path / "models.lock.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(valid_lockfile_data, f)
    return path


@pytest.fixture
def prompts_on_disk(tmp_path):
    """Create a prompts directory with stub templates."""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    names = ["classify_intent", "extract_json", "summarize_internal", "redact", "rag_rewrite"]
    for name in names:
        (prompts_dir / f"{name}.txt").write_text(f"Template for {name}: {{input}}", encoding="utf-8")
    return prompts_dir


# ===================================================================
# Real lockfile on disk
# ===================================================================

class TestRealLockfile:
    """Validate the actual models.lock.yaml in the repo."""

    def test_real_lockfile_exists(self):
        assert LOCKFILE_PATH.exists(), f"Lockfile missing at {LOCKFILE_PATH}"

    def test_real_lockfile_loads_and_validates(self):
        data = load_lockfile()
        assert "model" in data
        assert "runtime" in data
        assert "prompts" in data

    def test_real_lockfile_has_correct_model_name(self):
        data = load_lockfile()
        assert data["model"]["name"] == "hermes-2-pro-mistral-7b"

    def test_real_lockfile_format_is_gguf(self):
        data = load_lockfile()
        assert data["model"]["format"] == "gguf"

    def test_real_lockfile_checksum_is_sha256(self):
        data = load_lockfile()
        assert data["model"]["checksum"]["algorithm"] == "sha256"

    def test_real_lockfile_has_five_prompts(self):
        data = load_lockfile()
        assert len(data["prompts"]) == 5


# ===================================================================
# Real prompts on disk
# ===================================================================

class TestRealPrompts:
    """Validate the actual prompt templates in the repo."""

    def test_prompts_dir_exists(self):
        assert PROMPTS_DIR.exists()

    def test_all_prompt_files_exist(self):
        data = load_lockfile()
        for name in data["prompts"]:
            path = PROMPTS_DIR / f"{name}.txt"
            assert path.exists(), f"Missing prompt template: {path}"

    def test_all_prompts_contain_input_placeholder(self):
        data = load_lockfile()
        for name in data["prompts"]:
            template = load_prompt_template(name)
            assert "{input}" in template, f"Prompt '{name}' missing {{input}} placeholder"

    def test_extract_json_has_schema_placeholder(self):
        template = load_prompt_template("extract_json")
        assert "{schema}" in template

    def test_prompts_are_non_empty(self):
        data = load_lockfile()
        for name in data["prompts"]:
            template = load_prompt_template(name)
            assert len(template) > 10, f"Prompt '{name}' suspiciously short"

    def test_load_all_prompts_from_real_lockfile(self):
        prompts = load_all_prompts()
        assert len(prompts) == 5
        assert set(prompts.keys()) == {
            "classify_intent", "extract_json", "summarize_internal",
            "redact", "rag_rewrite",
        }


# ===================================================================
# load_lockfile
# ===================================================================

class TestLoadLockfile:

    def test_loads_valid_file(self, lockfile_on_disk):
        data = load_lockfile(lockfile_on_disk)
        assert data["model"]["name"] == "hermes-2-pro-mistral-7b"

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(LockfileError, match="not found"):
            load_lockfile(tmp_path / "nope.yaml")

    def test_invalid_yaml_raises(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text(":\n  - :\n    invalid:: yaml::: [[", encoding="utf-8")
        with pytest.raises(LockfileError, match="Invalid YAML"):
            load_lockfile(bad)

    def test_non_mapping_raises(self, tmp_path):
        bad = tmp_path / "list.yaml"
        bad.write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(LockfileError, match="must be a YAML mapping"):
            load_lockfile(bad)


# ===================================================================
# validate_lockfile — model section
# ===================================================================

class TestValidateModel:

    def test_valid_passes(self, valid_lockfile_data):
        validate_lockfile(valid_lockfile_data)  # Should not raise

    def test_missing_model_key(self, valid_lockfile_data):
        del valid_lockfile_data["model"]
        with pytest.raises(LockfileError, match="model"):
            validate_lockfile(valid_lockfile_data)

    def test_model_not_mapping(self, valid_lockfile_data):
        valid_lockfile_data["model"] = "string"
        with pytest.raises(LockfileError, match="must be a mapping"):
            validate_lockfile(valid_lockfile_data)

    @pytest.mark.parametrize("key", [
        "name", "version", "quantization", "format",
        "filename", "size_mb", "checksum", "sources", "license",
    ])
    def test_missing_model_subkey(self, valid_lockfile_data, key):
        del valid_lockfile_data["model"][key]
        with pytest.raises(LockfileError, match="Missing model keys"):
            validate_lockfile(valid_lockfile_data)

    def test_bad_format_raises(self, valid_lockfile_data):
        valid_lockfile_data["model"]["format"] = "safetensors"
        with pytest.raises(LockfileError, match="Unsupported model format"):
            validate_lockfile(valid_lockfile_data)

    def test_zero_size_raises(self, valid_lockfile_data):
        valid_lockfile_data["model"]["size_mb"] = 0
        with pytest.raises(LockfileError, match="positive integer"):
            validate_lockfile(valid_lockfile_data)

    def test_negative_size_raises(self, valid_lockfile_data):
        valid_lockfile_data["model"]["size_mb"] = -100
        with pytest.raises(LockfileError, match="positive integer"):
            validate_lockfile(valid_lockfile_data)

    def test_string_size_raises(self, valid_lockfile_data):
        valid_lockfile_data["model"]["size_mb"] = "big"
        with pytest.raises(LockfileError, match="positive integer"):
            validate_lockfile(valid_lockfile_data)


# ===================================================================
# validate_lockfile — checksum
# ===================================================================

class TestValidateChecksum:

    def test_missing_checksum_keys(self, valid_lockfile_data):
        valid_lockfile_data["model"]["checksum"] = {}
        with pytest.raises(LockfileError, match="Missing checksum keys"):
            validate_lockfile(valid_lockfile_data)

    def test_non_sha256_algorithm(self, valid_lockfile_data):
        valid_lockfile_data["model"]["checksum"]["algorithm"] = "md5"
        with pytest.raises(LockfileError, match="Unsupported checksum algorithm"):
            validate_lockfile(valid_lockfile_data)

    def test_short_hash_raises(self, valid_lockfile_data):
        valid_lockfile_data["model"]["checksum"]["hash"] = "abc123"
        with pytest.raises(LockfileError, match="64-character hex"):
            validate_lockfile(valid_lockfile_data)

    def test_checksum_not_mapping(self, valid_lockfile_data):
        valid_lockfile_data["model"]["checksum"] = "sha256:abc"
        with pytest.raises(LockfileError, match="must be a mapping"):
            validate_lockfile(valid_lockfile_data)


# ===================================================================
# validate_lockfile — sources
# ===================================================================

class TestValidateSources:

    def test_empty_sources_raises(self, valid_lockfile_data):
        valid_lockfile_data["model"]["sources"] = []
        with pytest.raises(LockfileError, match="non-empty list"):
            validate_lockfile(valid_lockfile_data)

    def test_sources_not_list_raises(self, valid_lockfile_data):
        valid_lockfile_data["model"]["sources"] = "http://example.com"
        with pytest.raises(LockfileError, match="non-empty list"):
            validate_lockfile(valid_lockfile_data)

    def test_source_missing_url(self, valid_lockfile_data):
        valid_lockfile_data["model"]["sources"] = [{"provider": "huggingface"}]
        with pytest.raises(LockfileError, match="missing keys"):
            validate_lockfile(valid_lockfile_data)

    def test_source_not_mapping(self, valid_lockfile_data):
        valid_lockfile_data["model"]["sources"] = ["http://example.com"]
        with pytest.raises(LockfileError, match="must be a mapping"):
            validate_lockfile(valid_lockfile_data)


# ===================================================================
# validate_lockfile — license
# ===================================================================

class TestValidateLicense:

    def test_missing_license_keys(self, valid_lockfile_data):
        valid_lockfile_data["model"]["license"] = {}
        with pytest.raises(LockfileError, match="Missing license keys"):
            validate_lockfile(valid_lockfile_data)

    def test_license_not_mapping(self, valid_lockfile_data):
        valid_lockfile_data["model"]["license"] = "MIT"
        with pytest.raises(LockfileError, match="must be a mapping"):
            validate_lockfile(valid_lockfile_data)


# ===================================================================
# validate_lockfile — runtime
# ===================================================================

class TestValidateRuntime:

    def test_missing_runtime(self, valid_lockfile_data):
        del valid_lockfile_data["runtime"]
        with pytest.raises(LockfileError, match="runtime"):
            validate_lockfile(valid_lockfile_data)

    def test_runtime_not_mapping(self, valid_lockfile_data):
        valid_lockfile_data["runtime"] = "llama.cpp"
        with pytest.raises(LockfileError, match="must be a mapping"):
            validate_lockfile(valid_lockfile_data)

    @pytest.mark.parametrize("key", ["engine", "context_length", "threads", "gpu_layers"])
    def test_missing_runtime_key(self, valid_lockfile_data, key):
        del valid_lockfile_data["runtime"][key]
        with pytest.raises(LockfileError, match="Missing runtime keys"):
            validate_lockfile(valid_lockfile_data)


# ===================================================================
# validate_lockfile — prompts section
# ===================================================================

class TestValidatePrompts:

    def test_missing_prompts(self, valid_lockfile_data):
        del valid_lockfile_data["prompts"]
        with pytest.raises(LockfileError, match="prompts"):
            validate_lockfile(valid_lockfile_data)

    def test_empty_prompts(self, valid_lockfile_data):
        valid_lockfile_data["prompts"] = []
        with pytest.raises(LockfileError, match="non-empty list"):
            validate_lockfile(valid_lockfile_data)

    def test_prompts_not_list(self, valid_lockfile_data):
        valid_lockfile_data["prompts"] = "classify_intent"
        with pytest.raises(LockfileError, match="non-empty list"):
            validate_lockfile(valid_lockfile_data)

    def test_prompt_name_not_string(self, valid_lockfile_data):
        valid_lockfile_data["prompts"] = [123]
        with pytest.raises(LockfileError, match="must be a string"):
            validate_lockfile(valid_lockfile_data)


# ===================================================================
# load_prompt_template
# ===================================================================

class TestLoadPromptTemplate:

    def test_loads_template(self, prompts_on_disk):
        template = load_prompt_template("classify_intent", prompts_dir=prompts_on_disk)
        assert "{input}" in template
        assert "classify_intent" in template

    def test_missing_template_raises(self, prompts_on_disk):
        with pytest.raises(LockfileError, match="not found"):
            load_prompt_template("nonexistent", prompts_dir=prompts_on_disk)

    def test_template_is_stripped(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "test.txt").write_text("\n  hello {input}  \n\n", encoding="utf-8")
        template = load_prompt_template("test", prompts_dir=prompts_dir)
        assert template == "hello {input}"


# ===================================================================
# load_all_prompts
# ===================================================================

class TestLoadAllPrompts:

    def test_loads_all(self, valid_lockfile_data, prompts_on_disk):
        prompts = load_all_prompts(
            lockfile_data=valid_lockfile_data,
            prompts_dir=prompts_on_disk,
        )
        assert len(prompts) == 5
        assert set(prompts.keys()) == {
            "classify_intent", "extract_json", "summarize_internal",
            "redact", "rag_rewrite",
        }

    def test_missing_prompt_file_raises(self, valid_lockfile_data, tmp_path):
        empty_dir = tmp_path / "empty_prompts"
        empty_dir.mkdir()
        with pytest.raises(LockfileError, match="not found"):
            load_all_prompts(
                lockfile_data=valid_lockfile_data,
                prompts_dir=empty_dir,
            )


# ===================================================================
# get_model_info
# ===================================================================

class TestGetModelInfo:

    def test_extracts_model_info(self, valid_lockfile_data):
        info = get_model_info(valid_lockfile_data)
        assert info["name"] == "hermes-2-pro-mistral-7b"
        assert info["filename"] == "Hermes-2-Pro-Mistral-7B.Q4_K_M.gguf"
        assert info["size_mb"] == 4370
        assert len(info["checksum_hash"]) == 64
        assert "huggingface" in info["source_url"]
        assert info["quantization"] == "Q4_K_M"
        assert info["format"] == "gguf"

    def test_model_info_from_real_lockfile(self):
        info = get_model_info()
        assert info["name"] == "hermes-2-pro-mistral-7b"
        assert info["format"] == "gguf"
