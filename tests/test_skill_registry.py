"""
Tests for src.core.skills.registry — Skill registry persistence (Prompt 7 / B2).
"""

import json
import pytest
import yaml
from pathlib import Path

from src.core.skills.schema import SkillError
from src.core.skills.registry import (
    SkillRegistry,
    SkillEntry,
    SkillOwnership,
    SignatureState,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_skill_manifest(tmp_path, name="classify_intent", **overrides):
    """Write a valid skill.yaml and return its path."""
    manifest = {
        "name": name,
        "version": "1.0.0",
        "description": f"Skill: {name}",
        "inputs": [{"name": "text", "type": "string", "required": True}],
        "outputs": [{"name": "result", "type": "string"}],
        "risk": "low",
        "permissions": ["read_input"],
        "required_brain": "local_utility",
        "scheduler_eligible": False,
    }
    manifest.update(overrides)
    skill_dir = tmp_path / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = skill_dir / "skill.yaml"
    manifest_path.write_text(yaml.dump(manifest), encoding="utf-8")
    return str(manifest_path)


# ===================================================================
# Install + enable persists across reload
# ===================================================================

class TestInstallAndPersistence:

    def test_install_persists_across_reload(self, tmp_path):
        """Blueprint requirement: install + enable persists across reload."""
        data_dir = str(tmp_path / "data")
        manifest_path = _write_skill_manifest(tmp_path)

        # Install in first instance
        reg1 = SkillRegistry(data_dir)
        entry = reg1.install_skill(manifest_path)
        assert entry.name == "classify_intent"
        assert entry.enabled is True

        # Reload in new instance
        reg2 = SkillRegistry(data_dir)
        skills = reg2.list_skills()
        assert len(skills) == 1
        assert skills[0].name == "classify_intent"
        assert skills[0].enabled is True

    def test_install_returns_entry(self, tmp_path):
        data_dir = str(tmp_path / "data")
        manifest_path = _write_skill_manifest(tmp_path)
        reg = SkillRegistry(data_dir)
        entry = reg.install_skill(manifest_path)
        assert isinstance(entry, SkillEntry)
        assert entry.version == "1.0.0"

    def test_install_duplicate_raises(self, tmp_path):
        data_dir = str(tmp_path / "data")
        manifest_path = _write_skill_manifest(tmp_path)
        reg = SkillRegistry(data_dir)
        reg.install_skill(manifest_path)
        with pytest.raises(SkillError, match="already installed"):
            reg.install_skill(manifest_path)

    def test_install_invalid_manifest_raises(self, tmp_path):
        data_dir = str(tmp_path / "data")
        bad_path = tmp_path / "bad_skill.yaml"
        bad_path.write_text(yaml.dump({"name": "BAD_NAME", "version": ""}), encoding="utf-8")
        reg = SkillRegistry(data_dir)
        with pytest.raises(SkillError):
            reg.install_skill(str(bad_path))

    def test_install_sets_ownership(self, tmp_path):
        data_dir = str(tmp_path / "data")
        manifest_path = _write_skill_manifest(tmp_path)
        reg = SkillRegistry(data_dir)
        entry = reg.install_skill(manifest_path, ownership=SkillOwnership.SYSTEM)
        assert entry.ownership == SkillOwnership.SYSTEM

    def test_install_sets_signature_state(self, tmp_path):
        data_dir = str(tmp_path / "data")
        manifest_path = _write_skill_manifest(tmp_path)
        reg = SkillRegistry(data_dir)
        entry = reg.install_skill(manifest_path)
        assert entry.signature_state == SignatureState.UNSIGNED


# ===================================================================
# Disable persists
# ===================================================================

class TestDisablePersistence:

    def test_disable_persists_across_reload(self, tmp_path):
        """Blueprint requirement: disable persists."""
        data_dir = str(tmp_path / "data")
        manifest_path = _write_skill_manifest(tmp_path)

        reg1 = SkillRegistry(data_dir)
        reg1.install_skill(manifest_path)
        reg1.disable_skill("classify_intent")

        reg2 = SkillRegistry(data_dir)
        skill = reg2.get_skill("classify_intent")
        assert skill is not None
        assert skill.enabled is False

    def test_disable_not_found_raises(self, tmp_path):
        data_dir = str(tmp_path / "data")
        reg = SkillRegistry(data_dir)
        with pytest.raises(SkillError, match="not found"):
            reg.disable_skill("nonexistent")

    def test_enable_after_disable(self, tmp_path):
        data_dir = str(tmp_path / "data")
        manifest_path = _write_skill_manifest(tmp_path)
        reg = SkillRegistry(data_dir)
        reg.install_skill(manifest_path)
        reg.disable_skill("classify_intent")
        reg.enable_skill("classify_intent")
        skill = reg.get_skill("classify_intent")
        assert skill.enabled is True


# ===================================================================
# list_skills / get_skill
# ===================================================================

class TestListAndGet:

    def test_empty_registry(self, tmp_path):
        data_dir = str(tmp_path / "data")
        reg = SkillRegistry(data_dir)
        assert reg.list_skills() == []

    def test_get_not_found_returns_none(self, tmp_path):
        data_dir = str(tmp_path / "data")
        reg = SkillRegistry(data_dir)
        assert reg.get_skill("nonexistent") is None

    def test_multiple_skills(self, tmp_path):
        data_dir = str(tmp_path / "data")
        p1 = _write_skill_manifest(tmp_path, name="skill_one")
        p2 = _write_skill_manifest(tmp_path, name="skill_two")
        reg = SkillRegistry(data_dir)
        reg.install_skill(p1)
        reg.install_skill(p2)
        skills = reg.list_skills()
        names = {s.name for s in skills}
        assert names == {"skill_one", "skill_two"}


# ===================================================================
# Uninstall
# ===================================================================

class TestUninstall:

    def test_uninstall_removes_skill(self, tmp_path):
        data_dir = str(tmp_path / "data")
        manifest_path = _write_skill_manifest(tmp_path)
        reg = SkillRegistry(data_dir)
        reg.install_skill(manifest_path)
        reg.uninstall_skill("classify_intent")
        assert reg.get_skill("classify_intent") is None

    def test_uninstall_not_found_raises(self, tmp_path):
        data_dir = str(tmp_path / "data")
        reg = SkillRegistry(data_dir)
        with pytest.raises(SkillError, match="not found"):
            reg.uninstall_skill("nonexistent")


# ===================================================================
# Persistence file
# ===================================================================

class TestPersistenceFile:

    def test_creates_data_dir(self, tmp_path):
        data_dir = str(tmp_path / "new_data")
        SkillRegistry(data_dir)
        assert Path(data_dir).exists()

    def test_registry_file_is_valid_json(self, tmp_path):
        data_dir = str(tmp_path / "data")
        manifest_path = _write_skill_manifest(tmp_path)
        reg = SkillRegistry(data_dir)
        reg.install_skill(manifest_path)
        registry_file = Path(data_dir) / "skills_registry.json"
        assert registry_file.exists()
        data = json.loads(registry_file.read_text(encoding="utf-8"))
        assert isinstance(data, list)

    def test_corrupted_file_starts_empty(self, tmp_path):
        data_dir = str(tmp_path / "data")
        Path(data_dir).mkdir(parents=True)
        (Path(data_dir) / "skills_registry.json").write_text("not json!", encoding="utf-8")
        reg = SkillRegistry(data_dir)
        assert reg.list_skills() == []
