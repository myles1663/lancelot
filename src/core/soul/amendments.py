"""
Soul Amendments — proposal workflow objects (Prompt 4 / A4).

Manages amendment proposals for the Soul document: creation,
diff computation, and persistence.

Public API:
    SoulAmendmentProposal  — Pydantic model for a proposal
    ProposalStatus         — "pending" | "approved" | "activated" | "rejected"
    create_proposal(from_version, proposed_yaml_text, author, soul_dir) → SoulAmendmentProposal
    compute_yaml_diff(old_dict, new_dict) → list[str]
    list_proposals(soul_dir) → list[SoulAmendmentProposal]
    get_proposal(proposal_id, soul_dir) → SoulAmendmentProposal | None
    save_proposals(proposals, soul_dir) → None
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field

from src.core.soul.store import SoulStoreError, _resolve_soul_dir

logger = logging.getLogger(__name__)

_PROPOSALS_FILE = "soul_proposals.json"
_DATA_DIR = "data"


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class ProposalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    ACTIVATED = "activated"
    REJECTED = "rejected"


class SoulAmendmentProposal(BaseModel):
    """A proposed amendment to the Soul document."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    proposed_version: str
    diff_summary: List[str] = Field(default_factory=list)
    author: str = "system"
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    status: ProposalStatus = ProposalStatus.PENDING
    proposed_yaml: Optional[str] = None


# ---------------------------------------------------------------------------
# Diff computation
# ---------------------------------------------------------------------------

def compute_yaml_diff(
    old_dict: Dict[str, Any],
    new_dict: Dict[str, Any],
    prefix: str = "",
) -> List[str]:
    """Compute a human-readable diff between two soul dictionaries.

    Returns a list of change descriptions like:
        "changed: mission"
        "added: new_field"
        "removed: old_field"
        "changed: autonomy_posture.level"
    """
    changes: List[str] = []
    all_keys = sorted(set(list(old_dict.keys()) + list(new_dict.keys())))

    for key in all_keys:
        full_key = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
        old_val = old_dict.get(key)
        new_val = new_dict.get(key)

        if key not in old_dict:
            changes.append(f"added: {full_key}")
        elif key not in new_dict:
            changes.append(f"removed: {full_key}")
        elif isinstance(old_val, dict) and isinstance(new_val, dict):
            changes.extend(compute_yaml_diff(old_val, new_val, full_key))
        elif old_val != new_val:
            changes.append(f"changed: {full_key}")

    return changes


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _proposals_path(soul_dir: Optional[str] = None) -> Path:
    """Resolve the path to soul_proposals.json."""
    d = _resolve_soul_dir(soul_dir)
    data_dir = d.parent / _DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / _PROPOSALS_FILE


def _load_proposals_raw(soul_dir: Optional[str] = None) -> List[dict]:
    """Load raw proposal dicts from disk."""
    path = _proposals_path(soul_dir)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_proposals(
    proposals: List[SoulAmendmentProposal],
    soul_dir: Optional[str] = None,
) -> None:
    """Persist proposals to disk."""
    path = _proposals_path(soul_dir)
    data = [p.model_dump() for p in proposals]
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def list_proposals(
    soul_dir: Optional[str] = None,
) -> List[SoulAmendmentProposal]:
    """Load all proposals from disk."""
    raw = _load_proposals_raw(soul_dir)
    return [SoulAmendmentProposal(**r) for r in raw]


def get_proposal(
    proposal_id: str,
    soul_dir: Optional[str] = None,
) -> Optional[SoulAmendmentProposal]:
    """Get a single proposal by ID, or None if not found."""
    for p in list_proposals(soul_dir):
        if p.id == proposal_id:
            return p
    return None


# ---------------------------------------------------------------------------
# Proposal creation
# ---------------------------------------------------------------------------

def create_proposal(
    from_version: str,
    proposed_yaml_text: str,
    author: str = "system",
    soul_dir: Optional[str] = None,
) -> SoulAmendmentProposal:
    """Create and persist a new Soul amendment proposal.

    Args:
        from_version: Current version to diff against (e.g. "v1").
        proposed_yaml_text: Raw YAML text of the proposed soul.
        author: Who created the proposal.
        soul_dir: Path to soul directory.

    Returns:
        The created SoulAmendmentProposal.

    Raises:
        SoulStoreError on invalid YAML or missing base version.
    """
    d = _resolve_soul_dir(soul_dir)

    # Parse proposed YAML
    try:
        proposed_dict = yaml.safe_load(proposed_yaml_text)
        if not isinstance(proposed_dict, dict):
            raise SoulStoreError("Proposed soul is not a YAML mapping")
    except yaml.YAMLError as exc:
        raise SoulStoreError(f"Invalid YAML in proposal: {exc}") from exc

    # Load base version for diff
    base_file = d / "soul_versions" / f"soul_{from_version}.yaml"
    if not base_file.exists():
        raise SoulStoreError(f"Base version file not found: {base_file}")

    try:
        base_dict = yaml.safe_load(base_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SoulStoreError(f"Invalid YAML in base version: {exc}") from exc

    # Compute diff
    diff = compute_yaml_diff(base_dict, proposed_dict)

    # Determine proposed version
    proposed_version = proposed_dict.get("version", f"{from_version}_proposed")

    proposal = SoulAmendmentProposal(
        proposed_version=proposed_version,
        diff_summary=diff,
        author=author,
        proposed_yaml=proposed_yaml_text,
    )

    # Persist
    existing = list_proposals(soul_dir)
    existing.append(proposal)
    save_proposals(existing, soul_dir)

    logger.info("Soul amendment proposal created: id=%s, version=%s",
                proposal.id, proposal.proposed_version)
    return proposal
