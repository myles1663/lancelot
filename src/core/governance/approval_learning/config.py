"""
APL Configuration — Pydantic v2 models + YAML loader.

Follows the same pattern as governance/config.py.
"""

from __future__ import annotations

import fnmatch
import logging
import os
from typing import List, Optional

import yaml
from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)


class DetectionConfig(BaseModel):
    """Thresholds for pattern detection."""
    min_observations: int = 20
    confidence_threshold: float = 0.85
    max_pattern_dimensions: int = 3
    analysis_window_days: int = 30
    analysis_trigger_interval: int = 10

    @field_validator("confidence_threshold")
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        if v < 0.0 or v > 1.0:
            raise ValueError(f"confidence_threshold must be 0.0-1.0, got {v}")
        return v

    @field_validator("max_pattern_dimensions")
    @classmethod
    def validate_dimensions(cls, v: int) -> int:
        if v < 1 or v > 6:
            raise ValueError(f"max_pattern_dimensions must be 1-6, got {v}")
        return v


class RulesConfig(BaseModel):
    """Limits and guardrails for automation rules."""
    max_active_rules: int = 50
    max_auto_decisions_per_day: int = 50
    max_auto_decisions_total: int = 500
    re_confirmation_interval: int = 500
    cooldown_after_decline: int = 30


class PersistenceConfig(BaseModel):
    """File paths for persistent storage."""
    decision_log_path: str = "data/apl/decisions.jsonl"
    rules_path: str = "data/apl/rules.json"
    patterns_path: str = "data/apl/patterns.json"


class APLConfig(BaseModel):
    """Top-level Approval Pattern Learning configuration."""
    version: str = "1.0"
    detection: DetectionConfig = DetectionConfig()
    rules: RulesConfig = RulesConfig()
    never_automate: List[str] = []
    persistence: PersistenceConfig = PersistenceConfig()

    def is_never_automate(self, capability: str) -> bool:
        """Check capability against never_automate list.

        Supports wildcards: 'connector.*.delete_*' matches
        'connector.email.delete_message'.
        """
        for pattern in self.never_automate:
            if fnmatch.fnmatch(capability, pattern):
                return True
        return False


def load_apl_config(path: Optional[str] = None) -> APLConfig:
    """Load APL config from YAML.

    Returns defaults if file is missing.
    """
    if path is None:
        candidates = [
            "config/approval_learning.yaml",
            "/home/lancelot/app/config/approval_learning.yaml",
            os.path.join(os.path.dirname(__file__), "../../../../config/approval_learning.yaml"),
        ]
        for candidate in candidates:
            if os.path.exists(candidate):
                path = candidate
                break

    if path is None or not os.path.exists(path):
        logger.warning("APL config not found, using defaults")
        return APLConfig()

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        if raw is None:
            logger.warning("APL config is empty, using defaults")
            return APLConfig()

        return APLConfig.model_validate(raw)
    except Exception as e:
        logger.error("Failed to load APL config: %s", e)
        return APLConfig()
