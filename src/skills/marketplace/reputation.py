"""
Marketplace Reputation — community trust scoring for skills.

Tracks installs, stars, issues, and security reports to compute
a reputation score that influences installation decisions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SkillReputation:
    """Reputation data for a marketplace skill."""
    skill_id: str
    author: str
    install_count: int = 0
    star_count: int = 0
    issue_count: int = 0
    security_reports: int = 0
    last_scanned: str = ""
    scan_version: str = ""

    @property
    def score(self) -> float:
        """Weighted reputation score.

        Stars and installs increase score.
        Issues decrease score.
        Security reports decrease score heavily (3x weight).
        """
        positive = (self.star_count * 2.0) + (self.install_count * 0.5)
        negative = (self.issue_count * 1.0) + (self.security_reports * 3.0)
        return max(0.0, positive - negative)


class ReputationRegistry:
    """Tracks reputation for all marketplace skills."""

    def __init__(self) -> None:
        self._skills: Dict[str, SkillReputation] = {}

    def register_skill(self, skill_id: str, author: str) -> SkillReputation:
        """Register a new skill in the reputation system."""
        rep = SkillReputation(skill_id=skill_id, author=author)
        self._skills[skill_id] = rep
        return rep

    def update_score(self, skill_id: str, **kwargs) -> SkillReputation:
        """Update reputation metrics for a skill."""
        rep = self._skills.get(skill_id)
        if rep is None:
            raise KeyError(f"Skill '{skill_id}' not registered")
        for key, value in kwargs.items():
            if hasattr(rep, key):
                setattr(rep, key, value)
        return rep

    def get_reputation(self, skill_id: str) -> Optional[SkillReputation]:
        """Get reputation data for a skill."""
        return self._skills.get(skill_id)

    def flag_security_issue(self, skill_id: str, report: str) -> None:
        """Flag a security issue for a skill."""
        rep = self._skills.get(skill_id)
        if rep is None:
            raise KeyError(f"Skill '{skill_id}' not registered")
        rep.security_reports += 1
        logger.warning("Security report for %s: %s", skill_id, report)

    def needs_rescan(self, skill_id: str, current_version: str) -> bool:
        """Check if a skill needs re-scanning (version changed)."""
        rep = self._skills.get(skill_id)
        if rep is None:
            return True
        return rep.scan_version != current_version
