"""
Skill Factory — proposal pipeline for new skills (Prompt 15 / F1-F4).

Generates skill skeletons, runs tests, and manages proposals for
owner-approved installation.

Public API:
    SkillProposal           — Pydantic model for a skill proposal
    ProposalStatus          — "pending" | "approved" | "rejected" | "installed"
    SkillFactory(data_dir)
    generate_skeleton(name, description, permissions) → SkillProposal
    list_proposals()        → list[SkillProposal]
    get_proposal(id)        → SkillProposal | None
    approve_proposal(id)    → SkillProposal
    install_proposal(id, registry) → SkillEntry
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field

from src.core.skills.schema import SkillError

logger = logging.getLogger(__name__)

_PROPOSALS_FILE = "skill_proposals.json"


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class ProposalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    INSTALLED = "installed"


class SkillProposal(BaseModel):
    """A proposal for a new skill."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str
    description: str = ""
    permissions: List[str] = Field(default_factory=list)
    manifest_yaml: str = ""
    execute_code: str = ""
    test_code: str = ""
    tests_status: Optional[str] = None
    status: ProposalStatus = ProposalStatus.PENDING
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    approved_by: Optional[str] = None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

class SkillFactory:
    """Manages the skill proposal pipeline."""

    def __init__(self, data_dir: str = "data"):
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._proposals_path = self._data_dir / _PROPOSALS_FILE

    def _load_proposals(self) -> List[SkillProposal]:
        if not self._proposals_path.exists():
            return []
        try:
            data = json.loads(self._proposals_path.read_text(encoding="utf-8"))
            return [SkillProposal(**d) for d in data] if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def _save_proposals(self, proposals: List[SkillProposal]) -> None:
        data = [p.model_dump() for p in proposals]
        self._proposals_path.write_text(
            json.dumps(data, indent=2, default=str), encoding="utf-8",
        )

    def generate_skeleton(
        self,
        name: str,
        description: str = "",
        permissions: Optional[List[str]] = None,
    ) -> SkillProposal:
        """Generate a new skill skeleton and create a proposal.

        The skeleton includes manifest YAML, execute.py, and test code.
        The proposal starts in PENDING status — cannot auto-enable.
        """
        if permissions is None:
            permissions = ["read_input"]

        # Validate skill name (defense-in-depth)
        if not re.match(r'^[a-z][a-z0-9_]*$', name):
            raise SkillError(
                f"Invalid skill name '{name}': must be lowercase alphanumeric with underscores"
            )

        # Sanitize description to prevent code injection via f-string interpolation
        safe_description = description.replace('\\', '\\\\').replace('"""', '\\"\\"\\"')

        manifest = {
            "name": name,
            "version": "0.1.0",
            "description": description,
            "inputs": [{"name": "input_data", "type": "string", "required": True}],
            "outputs": [{"name": "result", "type": "string"}],
            "risk": "low",
            "permissions": permissions,
            "required_brain": "local_utility",
            "scheduler_eligible": False,
        }
        manifest_yaml = yaml.dump(manifest, default_flow_style=False)

        execute_code = f'''"""
Skill: {name}
{safe_description}
"""


def execute(context, inputs):
    """Execute the {name} skill.

    Args:
        context: SkillContext with metadata.
        inputs: Dictionary of input values.

    Returns:
        Dictionary of output values.
    """
    input_data = inputs.get("input_data", "")
    # TODO: Implement skill logic
    return {{"result": f"Processed: {{input_data}}"}}
'''

        test_code = f'''"""
Tests for {name} skill.
"""
import pytest


def test_{name}_basic():
    from importlib import import_module
    # Placeholder test
    assert True


def test_{name}_returns_result():
    # TODO: Import and test execute function
    result = {{"result": "Processed: test"}}
    assert "result" in result
'''

        proposal = SkillProposal(
            name=name,
            description=description,
            permissions=permissions,
            manifest_yaml=manifest_yaml,
            execute_code=execute_code,
            test_code=test_code,
            tests_status="not_run",
            status=ProposalStatus.PENDING,
        )

        proposals = self._load_proposals()
        proposals.append(proposal)
        self._save_proposals(proposals)

        logger.info("Skill proposal created: name=%s, id=%s", name, proposal.id)
        return proposal

    def list_proposals(self) -> List[SkillProposal]:
        """List all proposals."""
        return self._load_proposals()

    def get_proposal(self, proposal_id: str) -> Optional[SkillProposal]:
        """Get a proposal by ID."""
        for p in self._load_proposals():
            if p.id == proposal_id:
                return p
        return None

    def approve_proposal(
        self,
        proposal_id: str,
        approved_by: str = "owner",
    ) -> SkillProposal:
        """Approve a proposal. Only owner can approve.

        Raises SkillError if not found or not pending.
        """
        proposals = self._load_proposals()
        target = None
        for p in proposals:
            if p.id == proposal_id:
                target = p
                break

        if target is None:
            raise SkillError(f"Proposal '{proposal_id}' not found")

        if target.status != ProposalStatus.PENDING:
            raise SkillError(
                f"Proposal status is '{target.status}', expected 'pending'"
            )

        target.status = ProposalStatus.APPROVED
        target.approved_by = approved_by
        self._save_proposals(proposals)

        logger.info("Skill proposal approved: id=%s, by=%s", proposal_id, approved_by)
        return target

    def reject_proposal(self, proposal_id: str) -> SkillProposal:
        """Reject a proposal."""
        proposals = self._load_proposals()
        target = None
        for p in proposals:
            if p.id == proposal_id:
                target = p
                break

        if target is None:
            raise SkillError(f"Proposal '{proposal_id}' not found")

        target.status = ProposalStatus.REJECTED
        self._save_proposals(proposals)
        return target

    def install_proposal(
        self,
        proposal_id: str,
        registry: Any,
        install_dir: Optional[str] = None,
    ) -> Any:
        """Install an approved proposal into the registry.

        Raises SkillError if not approved.
        """
        proposals = self._load_proposals()
        target = None
        for p in proposals:
            if p.id == proposal_id:
                target = p
                break

        if target is None:
            raise SkillError(f"Proposal '{proposal_id}' not found")

        if target.status != ProposalStatus.APPROVED:
            raise SkillError(
                f"Proposal must be approved before installation (status='{target.status}')"
            )

        # Run security pipeline if enabled
        from src.core.feature_flags import FEATURE_SKILL_SECURITY_PIPELINE
        if FEATURE_SKILL_SECURITY_PIPELINE:
            issues = self._run_security_pipeline(target)
            if issues:
                raise SkillError(
                    f"Security pipeline blocked installation: {'; '.join(issues)}"
                )

        # Write skill files
        base = Path(install_dir or str(self._data_dir / "installed_skills"))
        skill_dir = base / target.name
        skill_dir.mkdir(parents=True, exist_ok=True)

        manifest_path = skill_dir / "skill.yaml"
        manifest_path.write_text(target.manifest_yaml, encoding="utf-8")
        (skill_dir / "execute.py").write_text(target.execute_code, encoding="utf-8")
        (skill_dir / f"test_{target.name}.py").write_text(
            target.test_code, encoding="utf-8"
        )

        # Install in registry
        entry = registry.install_skill(str(manifest_path))

        # Mark as installed
        target.status = ProposalStatus.INSTALLED
        self._save_proposals(proposals)

        logger.info("Skill installed from proposal: name=%s, id=%s",
                     target.name, proposal_id)
        return entry

    # ------------------------------------------------------------------
    # Security Pipeline (gated by FEATURE_SKILL_SECURITY_PIPELINE)
    # ------------------------------------------------------------------

    # Patterns that indicate potentially dangerous code in skill execute.py
    _DANGEROUS_PATTERNS = [
        (r'\beval\s*\(', "eval() call detected"),
        (r'\bexec\s*\(', "exec() call detected"),
        (r'\b__import__\s*\(', "__import__() call detected"),
        (r'\bos\.system\s*\(', "os.system() call detected"),
        (r'\bos\.popen\s*\(', "os.popen() call detected"),
        (r'\bcompile\s*\(.*["\']exec["\']', "compile() with exec mode detected"),
        (r'\bctypes\b', "ctypes usage detected"),
        (r'\bopen\s*\([^)]*["\']/etc/', "Attempt to read system files"),
    ]

    # Required fields in a skill MANIFEST dict
    _REQUIRED_MANIFEST_FIELDS = {"name", "version", "description", "risk", "permissions", "inputs"}

    def _run_security_pipeline(self, proposal: SkillProposal) -> List[str]:
        """Run 4-stage security checks on a skill proposal.

        Returns a list of issues (empty = passed).
        """
        issues: List[str] = []

        # Stage 1: Manifest validation
        issues.extend(self._check_manifest(proposal))

        # Stage 2: Code scanning for dangerous patterns
        issues.extend(self._check_code_safety(proposal))

        # Stage 3: Ownership — verify proposal was approved
        if not proposal.approved_by:
            issues.append("Proposal has no approval record (approved_by is empty)")

        # Stage 4: Log audit trail
        if issues:
            logger.warning(
                "Security pipeline BLOCKED skill '%s' (id=%s): %s",
                proposal.name, proposal.id, "; ".join(issues),
            )
        else:
            logger.info(
                "Security pipeline PASSED for skill '%s' (id=%s)",
                proposal.name, proposal.id,
            )

        return issues

    def _check_manifest(self, proposal: SkillProposal) -> List[str]:
        """Stage 1: Validate manifest YAML has required fields."""
        issues = []
        try:
            manifest = yaml.safe_load(proposal.manifest_yaml) or {}
        except Exception as e:
            return [f"Invalid manifest YAML: {e}"]

        missing = self._REQUIRED_MANIFEST_FIELDS - set(manifest.keys())
        if missing:
            issues.append(f"Manifest missing required fields: {', '.join(sorted(missing))}")

        # Validate risk is a known level
        risk = manifest.get("risk", "").upper()
        if risk and risk not in ("LOW", "MEDIUM", "HIGH"):
            issues.append(f"Unknown risk level in manifest: '{risk}'")

        return issues

    def _check_code_safety(self, proposal: SkillProposal) -> List[str]:
        """Stage 2: Scan execute_code for dangerous patterns."""
        issues = []
        code = proposal.execute_code or ""
        for pattern, message in self._DANGEROUS_PATTERNS:
            if re.search(pattern, code):
                issues.append(message)
        return issues
