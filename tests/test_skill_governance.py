"""
Tests for src.core.skills.governance — Governance & marketplace (Prompt 16 / G1-G5).
"""

import pytest
import yaml
import zipfile
from pathlib import Path

from src.core.skills.schema import SkillError
from src.core.skills.registry import (
    SkillRegistry,
    SkillEntry,
    SkillOwnership,
    SignatureState,
)
from src.core.skills.governance import (
    build_skill_package,
    verify_marketplace_permissions,
    is_marketplace_approved,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_skill(tmp_path, name="echo", extra_files=None):
    """Write a skill directory with manifest and optional files."""
    manifest = {
        "name": name, "version": "1.0.0",
        "permissions": ["read_input"],
        "inputs": [{"name": "text", "type": "string"}],
        "outputs": [{"name": "result", "type": "string"}],
        "risk": "low",
    }
    skill_dir = tmp_path / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.yaml").write_text(yaml.dump(manifest), encoding="utf-8")
    (skill_dir / "execute.py").write_text(
        "def execute(ctx, inputs): return {}", encoding="utf-8"
    )
    if extra_files:
        for fname, content in extra_files.items():
            (skill_dir / fname).write_text(content, encoding="utf-8")
    return str(skill_dir / "skill.yaml")


# ===================================================================
# Packaging produces expected files
# ===================================================================

class TestPackaging:

    def test_produces_zip(self, tmp_path):
        """Blueprint requirement: packaging produces expected files."""
        reg = SkillRegistry(str(tmp_path / "data"))
        manifest_path = _write_skill(tmp_path)
        reg.install_skill(manifest_path)

        output_dir = str(tmp_path / "packages")
        zip_path = build_skill_package("echo", reg, output_dir)
        assert zip_path.exists()
        assert zip_path.suffix == ".zip"

    def test_zip_contains_manifest(self, tmp_path):
        reg = SkillRegistry(str(tmp_path / "data"))
        manifest_path = _write_skill(tmp_path)
        reg.install_skill(manifest_path)

        zip_path = build_skill_package("echo", reg, str(tmp_path / "packages"))
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            names = zf.namelist()
            assert "skill.yaml" in names

    def test_zip_contains_execute_py(self, tmp_path):
        reg = SkillRegistry(str(tmp_path / "data"))
        manifest_path = _write_skill(tmp_path)
        reg.install_skill(manifest_path)

        zip_path = build_skill_package("echo", reg, str(tmp_path / "packages"))
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            assert "execute.py" in zf.namelist()

    def test_zip_contains_extra_py_files(self, tmp_path):
        reg = SkillRegistry(str(tmp_path / "data"))
        manifest_path = _write_skill(
            tmp_path,
            extra_files={"helpers.py": "# helper code"},
        )
        reg.install_skill(manifest_path)

        zip_path = build_skill_package("echo", reg, str(tmp_path / "packages"))
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            assert "helpers.py" in zf.namelist()

    def test_zip_name_includes_version(self, tmp_path):
        reg = SkillRegistry(str(tmp_path / "data"))
        manifest_path = _write_skill(tmp_path)
        reg.install_skill(manifest_path)

        zip_path = build_skill_package("echo", reg, str(tmp_path / "packages"))
        assert "echo-1.0.0.zip" in zip_path.name

    def test_package_not_found_raises(self, tmp_path):
        reg = SkillRegistry(str(tmp_path / "data"))
        with pytest.raises(SkillError, match="not found"):
            build_skill_package("nonexistent", reg, str(tmp_path / "packages"))


# ===================================================================
# Registry retains ownership/signature fields
# ===================================================================

class TestOwnershipAndSignature:

    def test_registry_retains_ownership(self, tmp_path):
        """Blueprint requirement: registry retains ownership/signature fields."""
        reg = SkillRegistry(str(tmp_path / "data"))
        manifest_path = _write_skill(tmp_path)
        entry = reg.install_skill(manifest_path, ownership=SkillOwnership.MARKETPLACE)
        assert entry.ownership == SkillOwnership.MARKETPLACE

        # Reload
        reg2 = SkillRegistry(str(tmp_path / "data"))
        loaded = reg2.get_skill("echo")
        assert loaded.ownership == SkillOwnership.MARKETPLACE

    def test_registry_retains_signature_state(self, tmp_path):
        reg = SkillRegistry(str(tmp_path / "data"))
        manifest_path = _write_skill(tmp_path)
        entry = reg.install_skill(manifest_path)
        assert entry.signature_state == SignatureState.UNSIGNED

    def test_system_ownership(self, tmp_path):
        reg = SkillRegistry(str(tmp_path / "data"))
        manifest_path = _write_skill(tmp_path)
        entry = reg.install_skill(manifest_path, ownership=SkillOwnership.SYSTEM)
        assert entry.ownership == SkillOwnership.SYSTEM

    def test_user_ownership_default(self, tmp_path):
        reg = SkillRegistry(str(tmp_path / "data"))
        manifest_path = _write_skill(tmp_path)
        entry = reg.install_skill(manifest_path)
        assert entry.ownership == SkillOwnership.USER


# ===================================================================
# Marketplace permission policy
# ===================================================================

class TestMarketplacePolicy:

    def test_marketplace_restricted_perms(self, tmp_path):
        manifest = {
            "name": "risky_skill", "version": "1.0.0",
            "permissions": ["read_input", "execute_commands", "access_network"],
            "inputs": [], "outputs": [], "risk": "high",
        }
        skill_dir = tmp_path / "skills" / "risky_skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "skill.yaml").write_text(yaml.dump(manifest), encoding="utf-8")

        reg = SkillRegistry(str(tmp_path / "data"))
        entry = reg.install_skill(
            str(skill_dir / "skill.yaml"),
            ownership=SkillOwnership.MARKETPLACE,
        )

        disallowed = verify_marketplace_permissions(entry)
        assert "execute_commands" in disallowed
        assert "access_network" in disallowed
        assert "read_input" not in disallowed

    def test_non_marketplace_unrestricted(self, tmp_path):
        reg = SkillRegistry(str(tmp_path / "data"))
        manifest_path = _write_skill(tmp_path)
        entry = reg.install_skill(manifest_path, ownership=SkillOwnership.USER)
        disallowed = verify_marketplace_permissions(entry)
        assert disallowed == []

    def test_marketplace_approved_when_verified(self):
        entry = SkillEntry(
            name="test",
            version="1.0.0",
            ownership=SkillOwnership.MARKETPLACE,
            signature_state=SignatureState.VERIFIED,
        )
        assert is_marketplace_approved(entry) is True

    def test_marketplace_not_approved_when_unsigned(self):
        entry = SkillEntry(
            name="test",
            version="1.0.0",
            ownership=SkillOwnership.MARKETPLACE,
            signature_state=SignatureState.UNSIGNED,
        )
        assert is_marketplace_approved(entry) is False
