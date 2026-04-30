"""
Tests for src.core.skills.schema — Skill manifest schema (Prompt 6 / B1).
"""

import pytest
import yaml
from pathlib import Path

from src.core.skills.schema import (
    SkillManifest,
    SkillInput,
    SkillOutput,
    SkillRisk,
    SkillError,
    load_skill_manifest,
    validate_skill_manifest,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_manifest(**overrides) -> dict:
    """Return a minimal valid skill manifest dictionary."""
    base = {
        "name": "classify_intent",
        "version": "1.0.0",
        "description": "Classify user intent from text.",
        "inputs": [
            {"name": "text", "type": "string", "required": True},
        ],
        "outputs": [
            {"name": "intent", "type": "string"},
            {"name": "confidence", "type": "float"},
        ],
        "risk": "low",
        "permissions": ["read_input"],
        "required_brain": "local_utility",
        "scheduler_eligible": False,
        "sentry_requirements": [],
        "receipts": {
            "emit_on_success": True,
            "emit_on_failure": True,
        },
    }
    base.update(overrides)
    return base


# ===================================================================
# Valid manifest passes
# ===================================================================

class TestValidManifest:

    def test_valid_manifest_passes(self):
        """Blueprint requirement: valid manifest passes."""
        m = validate_skill_manifest(_valid_manifest())
        assert m.name == "classify_intent"
        assert m.version == "1.0.0"

    def test_inputs_parsed(self):
        m = validate_skill_manifest(_valid_manifest())
        assert len(m.inputs) == 1
        assert m.inputs[0].name == "text"
        assert m.inputs[0].required is True

    def test_outputs_parsed(self):
        m = validate_skill_manifest(_valid_manifest())
        assert len(m.outputs) == 2

    def test_risk_parsed(self):
        m = validate_skill_manifest(_valid_manifest(risk="high"))
        assert m.risk == SkillRisk.HIGH

    def test_permissions_parsed(self):
        m = validate_skill_manifest(_valid_manifest(permissions=["read_input", "write_output"]))
        assert len(m.permissions) == 2

    def test_required_brain_parsed(self):
        m = validate_skill_manifest(_valid_manifest(required_brain="flagship_fast"))
        assert m.required_brain == "flagship_fast"

    def test_scheduler_eligible(self):
        m = validate_skill_manifest(_valid_manifest(scheduler_eligible=True))
        assert m.scheduler_eligible is True

    def test_receipts_config(self):
        m = validate_skill_manifest(_valid_manifest())
        assert m.receipts.emit_on_success is True

    def test_sentry_requirements(self):
        data = _valid_manifest(
            sentry_requirements=[{"name": "input_sanitizer", "description": "Sanitize inputs"}]
        )
        m = validate_skill_manifest(data)
        assert len(m.sentry_requirements) == 1
        assert m.sentry_requirements[0].name == "input_sanitizer"


# ===================================================================
# Missing permissions fails
# ===================================================================

class TestMissingPermissions:

    def test_empty_permissions_fails(self):
        """Blueprint requirement: missing permissions fails."""
        with pytest.raises(SkillError, match="permission"):
            validate_skill_manifest(_valid_manifest(permissions=[]))

    def test_no_permissions_key_fails(self):
        d = _valid_manifest()
        del d["permissions"]
        with pytest.raises(SkillError, match="permission"):
            validate_skill_manifest(d)


# ===================================================================
# Missing/empty version fails
# ===================================================================

class TestMissingVersion:

    def test_empty_version_fails(self):
        """Blueprint requirement: missing version fails."""
        with pytest.raises(SkillError, match="version"):
            validate_skill_manifest(_valid_manifest(version=""))

    def test_whitespace_version_fails(self):
        with pytest.raises(SkillError, match="version"):
            validate_skill_manifest(_valid_manifest(version="  "))

    def test_no_version_key_fails(self):
        d = _valid_manifest()
        del d["version"]
        with pytest.raises(SkillError):
            validate_skill_manifest(d)


# ===================================================================
# Name validation
# ===================================================================

class TestNameValidation:

    def test_empty_name_fails(self):
        with pytest.raises(SkillError, match="name"):
            validate_skill_manifest(_valid_manifest(name=""))

    def test_uppercase_name_fails(self):
        with pytest.raises(SkillError, match="lowercase"):
            validate_skill_manifest(_valid_manifest(name="ClassifyIntent"))

    def test_name_with_spaces_fails(self):
        with pytest.raises(SkillError, match="lowercase"):
            validate_skill_manifest(_valid_manifest(name="classify intent"))

    def test_valid_snake_case_passes(self):
        m = validate_skill_manifest(_valid_manifest(name="my_cool_skill_v2"))
        assert m.name == "my_cool_skill_v2"


# ===================================================================
# load_skill_manifest from file
# ===================================================================

class TestLoadSkillManifest:

    def test_load_from_file(self, tmp_path):
        manifest_path = tmp_path / "skill.yaml"
        manifest_path.write_text(yaml.dump(_valid_manifest()), encoding="utf-8")
        m = load_skill_manifest(manifest_path)
        assert m.name == "classify_intent"

    def test_missing_file_raises(self):
        with pytest.raises(SkillError, match="not found"):
            load_skill_manifest("/nonexistent/skill.yaml")

    def test_invalid_yaml_raises(self, tmp_path):
        manifest_path = tmp_path / "skill.yaml"
        manifest_path.write_text("{{bad yaml: [", encoding="utf-8")
        with pytest.raises(SkillError, match="Invalid YAML"):
            load_skill_manifest(manifest_path)

    def test_non_mapping_raises(self, tmp_path):
        manifest_path = tmp_path / "skill.yaml"
        manifest_path.write_text("- just\n- a\n- list", encoding="utf-8")
        with pytest.raises(SkillError, match="not a YAML mapping"):
            load_skill_manifest(manifest_path)

    def test_validation_error_from_file(self, tmp_path):
        bad_data = {"name": "ok_skill", "version": ""}
        manifest_path = tmp_path / "skill.yaml"
        manifest_path.write_text(yaml.dump(bad_data), encoding="utf-8")
        with pytest.raises(SkillError, match="validation failed"):
            load_skill_manifest(manifest_path)
