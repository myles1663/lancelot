"""
Skill Registry — install, enable, disable, and persist skills (Prompt 7 / B2).

Single-owner module managing the skill lifecycle.  Persists state to
data/skills_registry.json.

Public API:
    SkillRegistry(data_dir)
    install_skill(path)   → SkillEntry
    enable_skill(name)    → None
    disable_skill(name)   → None
    list_skills()         → list[SkillEntry]
    get_skill(name)       → SkillEntry | None
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from src.core.skills.schema import SkillManifest, SkillError, load_skill_manifest

logger = logging.getLogger(__name__)

_REGISTRY_FILE = "skills_registry.json"


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class SkillOwnership(str, Enum):
    SYSTEM = "system"
    USER = "user"
    MARKETPLACE = "marketplace"


class SignatureState(str, Enum):
    UNSIGNED = "unsigned"
    SIGNED = "signed"
    VERIFIED = "verified"


class SkillEntry(BaseModel):
    """An installed skill in the registry."""
    name: str
    version: str
    enabled: bool = True
    manifest_path: str = ""
    ownership: SkillOwnership = SkillOwnership.USER
    signature_state: SignatureState = SignatureState.UNSIGNED
    installed_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    manifest: Optional[SkillManifest] = None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class SkillRegistry:
    """Manages installed skills and persists state to JSON."""

    def __init__(self, data_dir: str = "data"):
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._registry_path = self._data_dir / _REGISTRY_FILE
        self._skills: Dict[str, SkillEntry] = {}
        self._load()

    def _load(self) -> None:
        """Load registry from disk."""
        if not self._registry_path.exists():
            # Try backup if main file missing
            bak = Path(str(self._registry_path) + ".bak")
            if bak.exists():
                logger.warning("Main registry missing, loading from backup")
                try:
                    data = json.loads(bak.read_text(encoding="utf-8"))
                    if isinstance(data, list):
                        for entry_dict in data:
                            entry = SkillEntry(**entry_dict)
                            self._skills[entry.name] = entry
                except (json.JSONDecodeError, OSError) as exc:
                    logger.warning("Failed to load backup registry: %s", exc)
            return
        try:
            data = json.loads(self._registry_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                for entry_dict in data:
                    entry = SkillEntry(**entry_dict)
                    self._skills[entry.name] = entry
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load skill registry: %s", exc)
            # Try backup on corruption
            bak = Path(str(self._registry_path) + ".bak")
            if bak.exists():
                logger.warning("Loading from backup after corruption")
                try:
                    data = json.loads(bak.read_text(encoding="utf-8"))
                    if isinstance(data, list):
                        for entry_dict in data:
                            entry = SkillEntry(**entry_dict)
                            self._skills[entry.name] = entry
                except (json.JSONDecodeError, OSError):
                    pass

    def _save(self) -> None:
        """Persist registry to disk atomically."""
        data = [e.model_dump() for e in self._skills.values()]
        content = json.dumps(data, indent=2, default=str)
        tmp_path = Path(str(self._registry_path) + ".tmp")
        tmp_path.write_text(content, encoding="utf-8")
        # Create backup of current file before replacing
        if self._registry_path.exists():
            bak = Path(str(self._registry_path) + ".bak")
            try:
                os.replace(str(self._registry_path), str(bak))
            except OSError:
                pass
        os.replace(str(tmp_path), str(self._registry_path))

    def install_skill(
        self,
        path: str,
        ownership: SkillOwnership = SkillOwnership.USER,
    ) -> SkillEntry:
        """Install a skill from a manifest file.

        Args:
            path: Path to skill.yaml.
            ownership: Who owns this skill.

        Returns:
            The installed SkillEntry.

        Raises:
            SkillError if the manifest is invalid or already installed.
        """
        manifest = load_skill_manifest(path)

        if manifest.name in self._skills:
            raise SkillError(f"Skill '{manifest.name}' is already installed")

        entry = SkillEntry(
            name=manifest.name,
            version=manifest.version,
            enabled=True,
            manifest_path=str(Path(path).resolve()),
            ownership=ownership,
            manifest=manifest,
        )
        self._skills[manifest.name] = entry
        self._save()

        logger.info("skill_installed: name=%s, version=%s", entry.name, entry.version)
        return entry

    def enable_skill(self, name: str) -> None:
        """Enable an installed skill.

        Raises:
            SkillError if not found.
        """
        entry = self._skills.get(name)
        if entry is None:
            raise SkillError(f"Skill '{name}' not found in registry")
        entry.enabled = True
        self._save()
        logger.info("skill_enabled: name=%s", name)

    def disable_skill(self, name: str) -> None:
        """Disable an installed skill.

        Raises:
            SkillError if not found.
        """
        entry = self._skills.get(name)
        if entry is None:
            raise SkillError(f"Skill '{name}' not found in registry")
        entry.enabled = False
        self._save()
        logger.info("skill_disabled: name=%s", name)

    def list_skills(self) -> List[SkillEntry]:
        """List all installed skills."""
        return list(self._skills.values())

    def get_skill(self, name: str) -> Optional[SkillEntry]:
        """Get a single skill by name, or None if not found."""
        return self._skills.get(name)

    def uninstall_skill(self, name: str) -> None:
        """Remove a skill from the registry.

        Raises:
            SkillError if not found.
        """
        if name not in self._skills:
            raise SkillError(f"Skill '{name}' not found in registry")
        del self._skills[name]
        self._save()
        logger.info("skill_uninstalled: name=%s", name)
