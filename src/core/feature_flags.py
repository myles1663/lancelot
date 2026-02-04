"""
Feature Flags — vNext2 subsystem kill switches (Prompt 17 / H1).

Each flag controls whether a subsystem is active. When disabled,
the system boots without that subsystem.

Environment variables:
    FEATURE_SOUL           — default: true
    FEATURE_SKILLS         — default: true
    FEATURE_HEALTH_MONITOR — default: true
    FEATURE_SCHEDULER      — default: true
"""

from __future__ import annotations

import os
import logging

logger = logging.getLogger(__name__)


def _env_bool(key: str, default: bool = True) -> bool:
    """Read a boolean from env. Accepts 'true', '1', 'yes' (case-insensitive)."""
    val = os.environ.get(key, "").strip().lower()
    if not val:
        return default
    return val in ("true", "1", "yes")


FEATURE_SOUL: bool = _env_bool("FEATURE_SOUL")
FEATURE_SKILLS: bool = _env_bool("FEATURE_SKILLS")
FEATURE_HEALTH_MONITOR: bool = _env_bool("FEATURE_HEALTH_MONITOR")
FEATURE_SCHEDULER: bool = _env_bool("FEATURE_SCHEDULER")


def reload_flags() -> None:
    """Re-read feature flags from environment. Used in tests."""
    global FEATURE_SOUL, FEATURE_SKILLS, FEATURE_HEALTH_MONITOR, FEATURE_SCHEDULER
    FEATURE_SOUL = _env_bool("FEATURE_SOUL")
    FEATURE_SKILLS = _env_bool("FEATURE_SKILLS")
    FEATURE_HEALTH_MONITOR = _env_bool("FEATURE_HEALTH_MONITOR")
    FEATURE_SCHEDULER = _env_bool("FEATURE_SCHEDULER")


def log_feature_flags() -> None:
    """Log current feature flag state at startup."""
    logger.info(
        "Feature flags: SOUL=%s, SKILLS=%s, HEALTH_MONITOR=%s, SCHEDULER=%s",
        FEATURE_SOUL, FEATURE_SKILLS, FEATURE_HEALTH_MONITOR, FEATURE_SCHEDULER,
    )
