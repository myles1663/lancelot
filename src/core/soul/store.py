"""
Soul Store — loads, validates, and manages Soul versions (Prompt 1 / A1).

Single-owner module responsible for reading the soul directory,
validating YAML against the Soul schema, and resolving the active version.

Public API:
    Soul              — Pydantic model for a validated soul document
    SoulStoreError    — raised on load/validation failures
    load_active_soul(soul_dir) → Soul
    list_versions(soul_dir)    → list[str]
    get_active_version(soul_dir) → str
    set_active_version(version, soul_dir) → None
"""

import logging
import os
import re
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

_DEFAULT_SOUL_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "soul")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class AutonomyPosture(BaseModel):
    level: str
    description: str
    allowed_autonomous: List[str] = Field(default_factory=list)
    requires_approval: List[str] = Field(default_factory=list)


class RiskRule(BaseModel):
    name: str
    description: str
    enforced: bool = True


class ApprovalRules(BaseModel):
    default_timeout_seconds: int = 3600
    escalation_on_timeout: str = "skip_and_log"
    channels: List[str] = Field(default_factory=lambda: ["war_room"])


class SchedulingBoundaries(BaseModel):
    max_concurrent_jobs: int = 5
    max_job_duration_seconds: int = 300
    no_autonomous_irreversible: bool = True
    require_ready_state: bool = True
    description: str = ""


class Soul(BaseModel):
    """Validated Soul document — Lancelot's constitutional identity."""
    version: str
    mission: str
    allegiance: str
    autonomy_posture: AutonomyPosture
    risk_rules: List[RiskRule] = Field(default_factory=list)
    approval_rules: ApprovalRules = Field(default_factory=ApprovalRules)
    tone_invariants: List[str] = Field(default_factory=list)
    memory_ethics: List[str] = Field(default_factory=list)
    scheduling_boundaries: SchedulingBoundaries = Field(
        default_factory=SchedulingBoundaries,
    )

    @field_validator("version")
    @classmethod
    def version_must_be_valid(cls, v: str) -> str:
        if not re.match(r"^(v\d+|crusader)$", v):
            raise ValueError(f"Version must match 'vN' or 'crusader' pattern, got '{v}'")
        return v

    @field_validator("mission")
    @classmethod
    def mission_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Mission must not be empty")
        return v

    @field_validator("allegiance")
    @classmethod
    def allegiance_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Allegiance must not be empty")
        return v


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class SoulStoreError(Exception):
    """Raised when the soul store encounters an error."""


# ---------------------------------------------------------------------------
# Store functions
# ---------------------------------------------------------------------------

def _resolve_soul_dir(soul_dir: Optional[str] = None) -> Path:
    """Resolve the soul directory path."""
    if soul_dir:
        return Path(soul_dir)
    return Path(_DEFAULT_SOUL_DIR).resolve()


def get_active_version(soul_dir: Optional[str] = None) -> str:
    """Read the active soul version from the ACTIVE pointer file.

    If ACTIVE is missing, falls back to the latest version found in
    soul_versions/.

    Returns:
        Version string (e.g. "v1").

    Raises:
        SoulStoreError if no version can be determined.
    """
    d = _resolve_soul_dir(soul_dir)
    active_file = d / "ACTIVE"

    if active_file.exists():
        version = active_file.read_text(encoding="utf-8").strip()
        if version:
            return version

    # Fallback: find latest version
    versions = list_versions(soul_dir)
    if not versions:
        raise SoulStoreError(
            f"No ACTIVE pointer and no versions found in {d / 'soul_versions'}"
        )
    return versions[-1]


def set_active_version(version: str, soul_dir: Optional[str] = None) -> None:
    """Write the ACTIVE pointer file to switch the active soul version.

    Args:
        version: Version string (e.g. "v1").
        soul_dir: Path to soul directory.

    Raises:
        SoulStoreError if the version file doesn't exist.
    """
    d = _resolve_soul_dir(soul_dir)
    version_file = d / "soul_versions" / f"soul_{version}.yaml"
    if not version_file.exists():
        raise SoulStoreError(f"Cannot activate — version file not found: {version_file}")

    active_file = d / "ACTIVE"
    active_file.write_text(version, encoding="utf-8")
    logger.info("Soul active version set to %s", version)


def list_versions(soul_dir: Optional[str] = None) -> list[str]:
    """List all available soul versions, sorted ascending.

    Scans soul_versions/ for files matching soul_v*.yaml.

    Returns:
        List of version strings, e.g. ["v1", "v2"].
    """
    d = _resolve_soul_dir(soul_dir)
    versions_dir = d / "soul_versions"

    if not versions_dir.exists():
        return []

    versions = []
    for f in sorted(versions_dir.iterdir()):
        m = re.match(r"^soul_(v\d+)\.yaml$", f.name)
        if m:
            versions.append(m.group(1))

    return versions


def _load_yaml(path: Path) -> dict:
    """Load and parse a YAML file."""
    try:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise SoulStoreError(f"Soul file is not a YAML mapping: {path}")
        return data
    except yaml.YAMLError as exc:
        raise SoulStoreError(f"Invalid YAML in {path}: {exc}") from exc
    except OSError as exc:
        raise SoulStoreError(f"Cannot read {path}: {exc}") from exc


def load_active_soul(soul_dir: Optional[str] = None) -> Soul:
    """Load and validate the active soul version.

    Resolution order:
    1. Read ACTIVE pointer → load soul_versions/soul_{version}.yaml
    2. If ACTIVE missing → fall back to latest version
    3. Validate against Pydantic Soul model

    Returns:
        Validated Soul instance.

    Raises:
        SoulStoreError on missing files or validation failure.
    """
    d = _resolve_soul_dir(soul_dir)
    version = get_active_version(soul_dir)

    version_file = d / "soul_versions" / f"soul_{version}.yaml"
    if not version_file.exists():
        raise SoulStoreError(
            f"Soul version file not found: {version_file}"
        )

    data = _load_yaml(version_file)

    try:
        soul = Soul(**data)
    except ValidationError as exc:
        raise SoulStoreError(
            f"Soul validation failed for {version}: {exc}"
        ) from exc

    # Run linter — fail on critical invariant violations
    from src.core.soul.linter import lint_or_raise  # local import to avoid circular
    lint_or_raise(soul)

    logger.info("soul_loaded: version=%s", soul.version)
    return soul
