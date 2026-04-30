"""
HIVE Config — Pydantic configuration model + YAML loader.

Follows the BAL config pattern: Pydantic model with YAML file loading.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class HiveConfig(BaseModel):
    """Configuration for the HIVE Agent Mesh subsystem."""

    # Agent capacity
    max_concurrent_agents: int = Field(default=10, ge=1, le=50)
    default_task_timeout: int = Field(default=300, ge=10)
    max_actions_per_agent: int = Field(default=50, ge=1)
    max_subtasks_per_decomposition: int = Field(default=20, ge=1, le=100)

    # Governance
    spawn_approval_tier: str = Field(default="T2")
    default_control_method: str = Field(default="supervised")
    collapse_on_governance_violation: bool = Field(default=True)
    collapse_on_soul_violation: bool = Field(default=True)

    # UAB integration
    uab_enabled: bool = Field(default=False)
    uab_allowed_apps: list[str] = Field(default_factory=list)

    # Retry
    max_retry_attempts: int = Field(default=2, ge=0, le=5)
    never_retry_identical_plan: bool = Field(default=True)

    # Logging
    log_agent_actions: bool = Field(default=True)
    log_decomposition: bool = Field(default=True)


def load_hive_config(
    config_dir: Optional[str] = None,
) -> HiveConfig:
    """Load HIVE configuration from YAML file.

    Falls back to defaults if the config file doesn't exist.

    Args:
        config_dir: Path to the config directory containing hive.yaml.
            Defaults to the project's config/ directory.

    Returns:
        HiveConfig instance.
    """
    if config_dir is None:
        config_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "config",
        )

    config_path = Path(config_dir) / "hive.yaml"

    if not config_path.exists():
        logger.info("No hive.yaml found at %s, using defaults", config_path)
        return HiveConfig()

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        config = HiveConfig(**data)
        logger.info(
            "HIVE config loaded: max_agents=%d, timeout=%ds, control=%s",
            config.max_concurrent_agents,
            config.default_task_timeout,
            config.default_control_method,
        )
        return config
    except Exception as exc:
        logger.warning("Failed to load hive.yaml: %s — using defaults", exc)
        return HiveConfig()
