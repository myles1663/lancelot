"""
Skill Governance — marketplace hooks, packaging, and signing (Prompt 16 / G1-G5).

Provides skill packaging, signing interface contracts, and marketplace
permission policies.

Public API:
    build_skill_package(skill_name, registry, output_dir) → Path
    MarketplacePolicy
    verify_marketplace_permissions(entry) → list[str]
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.skills.registry import SkillRegistry, SkillEntry, SkillOwnership, SignatureState
from src.core.skills.schema import SkillError

logger = logging.getLogger(__name__)

# Default restricted permissions for marketplace skills
_MARKETPLACE_ALLOWED_PERMISSIONS = frozenset({
    "read_input",
    "write_output",
    "read_config",
})


# ---------------------------------------------------------------------------
# Packaging
# ---------------------------------------------------------------------------

def build_skill_package(
    skill_name: str,
    registry: SkillRegistry,
    output_dir: str = ".",
) -> Path:
    """Build a .zip package for a skill.

    The package contains:
    - skill.yaml (manifest)
    - execute.py (if present)
    - Any .py files in the skill directory

    Args:
        skill_name: Name of the installed skill.
        registry: The skill registry.
        output_dir: Directory to write the zip file.

    Returns:
        Path to the created .zip file.

    Raises:
        SkillError if skill not found or manifest_path missing.
    """
    entry = registry.get_skill(skill_name)
    if entry is None:
        raise SkillError(f"Skill '{skill_name}' not found in registry")

    if not entry.manifest_path:
        raise SkillError(f"Skill '{skill_name}' has no manifest_path")

    manifest_path = Path(entry.manifest_path)
    if not manifest_path.exists():
        raise SkillError(f"Manifest file not found: {manifest_path}")

    skill_dir = manifest_path.parent
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    zip_path = out / f"{skill_name}-{entry.version}.zip"

    with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
        # Add manifest
        zf.write(str(manifest_path), "skill.yaml")

        # Add all .py files in the skill directory
        for py_file in skill_dir.glob("*.py"):
            zf.write(str(py_file), py_file.name)

    logger.info("Skill package built: %s", zip_path)
    return zip_path


# ---------------------------------------------------------------------------
# Marketplace permission policy
# ---------------------------------------------------------------------------

def verify_marketplace_permissions(entry: SkillEntry) -> List[str]:
    """Check if a marketplace skill has restricted permissions.

    Marketplace skills default to restricted permissions unless
    explicitly approved. Returns a list of disallowed permissions.

    Args:
        entry: The skill registry entry.

    Returns:
        List of permission strings that exceed marketplace restrictions.
        Empty list if all permissions are allowed.
    """
    if entry.ownership != SkillOwnership.MARKETPLACE:
        return []  # Non-marketplace skills are unrestricted

    if entry.manifest is None:
        return []

    disallowed = []
    for perm in entry.manifest.permissions:
        if perm not in _MARKETPLACE_ALLOWED_PERMISSIONS:
            disallowed.append(perm)

    return disallowed


def is_marketplace_approved(entry: SkillEntry) -> bool:
    """Check if a marketplace skill is approved (verified signature)."""
    return (
        entry.ownership == SkillOwnership.MARKETPLACE
        and entry.signature_state == SignatureState.VERIFIED
    )
