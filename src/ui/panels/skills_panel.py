"""
War Room — Skills Panel (E2).

Lists skills with enable/disable controls, permissions, and ownership.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from src.core.skills.registry import SkillRegistry, SkillEntry, SkillError

logger = logging.getLogger(__name__)


class SkillsPanel:
    """Skills panel data provider for the War Room."""

    def __init__(self, registry: SkillRegistry):
        self._registry = registry

    def list_skills(self) -> List[Dict[str, Any]]:
        """List all skills with safe serialization."""
        try:
            entries = self._registry.list_skills()
            return [
                {
                    "name": e.name,
                    "version": e.version,
                    "enabled": e.enabled,
                    "ownership": e.ownership.value,
                    "signature_state": e.signature_state.value,
                    "permissions": e.manifest.permissions if e.manifest else [],
                }
                for e in entries
            ]
        except Exception as exc:
            logger.warning("Skills panel error: %s", exc)
            return []

    def enable_skill(self, name: str) -> Dict[str, Any]:
        try:
            self._registry.enable_skill(name)
            return {"status": "enabled", "skill": name}
        except SkillError as exc:
            return {"error": str(exc)}

    def disable_skill(self, name: str) -> Dict[str, Any]:
        try:
            self._registry.disable_skill(name)
            return {"status": "disabled", "skill": name}
        except SkillError as exc:
            return {"error": str(exc)}

    def render_data(self) -> Dict[str, Any]:
        return {
            "panel": "skills",
            "skills": self.list_skills(),
        }
