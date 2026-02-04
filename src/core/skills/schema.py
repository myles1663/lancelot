"""
Skill Schema — manifest model and validator (Prompt 6 / B1).

Defines the SkillManifest Pydantic model representing a skill.yaml file,
plus a loader/validator function.

Public API:
    SkillManifest          — Pydantic model for a skill manifest
    SkillInput / SkillOutput — field descriptors
    SkillRisk              — risk level enum
    SkillError             — raised on load/validation failures
    load_skill_manifest(path) → SkillManifest
    validate_skill_manifest(data) → SkillManifest
"""

from __future__ import annotations

import logging
import re
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class SkillError(Exception):
    """Raised when skill loading or validation fails."""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SkillRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class SkillInput(BaseModel):
    """An input field for a skill."""
    name: str
    type: str = "string"
    required: bool = True
    description: str = ""


class SkillOutput(BaseModel):
    """An output field for a skill."""
    name: str
    type: str = "string"
    description: str = ""


class SentryRequirement(BaseModel):
    """A sentry/guard requirement for a skill."""
    name: str
    description: str = ""


class ReceiptConfig(BaseModel):
    """Receipt configuration for a skill."""
    emit_on_success: bool = True
    emit_on_failure: bool = True
    include_inputs: bool = False
    include_outputs: bool = False


# ---------------------------------------------------------------------------
# SkillManifest
# ---------------------------------------------------------------------------

class SkillManifest(BaseModel):
    """Validated skill manifest — defines a single skill's contract."""
    name: str
    version: str
    description: str = ""
    inputs: List[SkillInput] = Field(default_factory=list)
    outputs: List[SkillOutput] = Field(default_factory=list)
    risk: SkillRisk = SkillRisk.LOW
    permissions: List[str]
    required_brain: str = "local_utility"
    scheduler_eligible: bool = False
    sentry_requirements: List[SentryRequirement] = Field(default_factory=list)
    receipts: ReceiptConfig = Field(default_factory=ReceiptConfig)

    @field_validator("name")
    @classmethod
    def name_must_be_valid(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Skill name must not be empty")
        if not re.match(r"^[a-z][a-z0-9_]*$", v):
            raise ValueError(
                f"Skill name must be lowercase alphanumeric with underscores, got '{v}'"
            )
        return v

    @field_validator("version")
    @classmethod
    def version_must_be_valid(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Skill version must not be empty")
        return v

    @field_validator("permissions")
    @classmethod
    def permissions_must_not_be_empty(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("Skill must declare at least one permission")
        return v


# ---------------------------------------------------------------------------
# Loader / Validator
# ---------------------------------------------------------------------------

def validate_skill_manifest(data: Dict[str, Any]) -> SkillManifest:
    """Validate a dictionary as a SkillManifest.

    Args:
        data: Dictionary parsed from skill.yaml.

    Returns:
        Validated SkillManifest.

    Raises:
        SkillError on validation failure.
    """
    try:
        return SkillManifest(**data)
    except ValidationError as exc:
        raise SkillError(f"Skill manifest validation failed: {exc}") from exc


def load_skill_manifest(path: str | Path) -> SkillManifest:
    """Load and validate a skill.yaml file.

    Args:
        path: Path to the skill.yaml file.

    Returns:
        Validated SkillManifest.

    Raises:
        SkillError on missing file, invalid YAML, or validation failure.
    """
    p = Path(path)
    if not p.exists():
        raise SkillError(f"Skill manifest not found: {p}")

    try:
        text = p.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise SkillError(f"Skill manifest is not a YAML mapping: {p}")
    except yaml.YAMLError as exc:
        raise SkillError(f"Invalid YAML in skill manifest {p}: {exc}") from exc

    return validate_skill_manifest(data)
