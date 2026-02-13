"""
Scheduler Schema — JobSpec model and config loader (Prompt 11 / D1).

Defines the JobSpec Pydantic model and loads scheduler.yaml configuration.

Public API:
    JobSpec             — Pydantic model for a scheduled job
    TriggerSpec         — trigger configuration (interval or cron)
    TriggerType         — "interval" | "cron"
    SchedulerConfig     — top-level config with list of jobs
    SchedulerError      — raised on load/validation failures
    load_scheduler_config(config_dir) → SchedulerConfig
"""

from __future__ import annotations

import logging
import re
import shutil
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

_EXAMPLE_FILE = "scheduler.example.yaml"
_CONFIG_FILE = "scheduler.yaml"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class SchedulerError(Exception):
    """Raised when scheduler config loading or validation fails."""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TriggerType(str, Enum):
    INTERVAL = "interval"
    CRON = "cron"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class TriggerSpec(BaseModel):
    """Trigger configuration for a scheduled job."""
    type: TriggerType
    seconds: Optional[int] = None
    expression: Optional[str] = None

    @field_validator("seconds")
    @classmethod
    def seconds_must_be_positive(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v <= 0:
            raise ValueError("Trigger seconds must be positive")
        return v

    @field_validator("expression")
    @classmethod
    def expression_must_be_valid_cron(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            parts = v.strip().split()
            if len(parts) != 5:
                raise ValueError(
                    f"Cron expression must have 5 fields, got {len(parts)}: '{v}'"
                )
        return v


class JobSpec(BaseModel):
    """Specification for a single scheduled job."""
    id: str
    name: str
    trigger: TriggerSpec
    enabled: bool = True
    requires_ready: bool = True
    requires_approvals: List[str] = Field(default_factory=list)
    timeout_s: int = 300
    skill: str = ""
    inputs: Dict[str, Any] = Field(default_factory=dict)
    description: str = ""

    @field_validator("id")
    @classmethod
    def id_must_be_valid(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Job id must not be empty")
        if not re.match(r"^[a-z][a-z0-9_]*$", v):
            raise ValueError(
                f"Job id must be lowercase alphanumeric with underscores, got '{v}'"
            )
        return v

    @field_validator("timeout_s")
    @classmethod
    def timeout_must_be_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("timeout_s must be positive")
        return v


class SchedulerConfig(BaseModel):
    """Top-level scheduler configuration."""
    jobs: List[JobSpec] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def load_scheduler_config(config_dir: str = "config") -> SchedulerConfig:
    """Load scheduler.yaml, copying from example if needed.

    On first run, if scheduler.yaml doesn't exist, copies from
    scheduler.example.yaml.

    Args:
        config_dir: Path to the config directory.

    Returns:
        Validated SchedulerConfig.

    Raises:
        SchedulerError on missing files, invalid YAML, or validation failure.
    """
    d = Path(config_dir)
    config_path = d / _CONFIG_FILE
    example_path = d / _EXAMPLE_FILE

    # Copy example on first run
    if not config_path.exists():
        if example_path.exists():
            shutil.copy2(str(example_path), str(config_path))
            logger.info("Created %s from example", config_path)
        else:
            raise SchedulerError(
                f"No {_CONFIG_FILE} or {_EXAMPLE_FILE} found in {d}"
            )

    try:
        text = config_path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise SchedulerError(f"Scheduler config is not a YAML mapping: {config_path}")
    except yaml.YAMLError as exc:
        raise SchedulerError(f"Invalid YAML in {config_path}: {exc}") from exc

    try:
        return SchedulerConfig(**data)
    except ValidationError as exc:
        raise SchedulerError(f"Scheduler config validation failed: {exc}") from exc
