"""
Governed Skill Factory.

The SkillFactory is the proposal pipeline for first-party generated skills.
It creates a proposal package, persists the reviewed artifacts, evaluates the
package through the shared skill security pipeline, and only installs an
approved artifact after a conformance re-check.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field

from src.core.skills.schema import SkillError
from src.skills.security.capability_enforcer import CapabilityEnforcer
from src.skills.security.pipeline import SkillSecurityPipeline
from src.skills.security.sandbox_tester import SandboxTester
from src.skills.security.static_analyzer import StaticAnalyzer

logger = logging.getLogger(__name__)

_PROPOSALS_FILE = "skill_proposals.json"
_PROPOSALS_DIR = "skill_proposals"
_INSTALLED_SKILLS_DIR = "installed_skills"


class ProposalStatus(str, Enum):
    PENDING = "pending"
    REVIEW_FAILED = "review_failed"
    APPROVED = "approved"
    REJECTED = "rejected"
    INSTALLED = "installed"


class SkillProposal(BaseModel):
    """Persisted record for a governed skill proposal."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str
    description: str = ""
    permissions: List[str] = Field(default_factory=list)
    risk: str = "low"
    source: str = "first-party"
    author: str = "Lancelot"
    target_domains: List[str] = Field(default_factory=list)
    credentials: List[Dict[str, str]] = Field(default_factory=list)
    approved_capabilities: List[str] = Field(default_factory=list)
    manifest_yaml: str = ""
    security_manifest_yaml: str = ""
    execute_code: str = ""
    test_code: str = ""
    tests_status: Optional[str] = None
    artifact_hashes: Dict[str, str] = Field(default_factory=dict)
    pipeline_passed: bool = False
    pipeline_failed_at_stage: Optional[str] = None
    pipeline_stage_results: Dict[str, Any] = Field(default_factory=dict)
    status: ProposalStatus = ProposalStatus.PENDING
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    rejected_reason: Optional[str] = None
    rejected_at: Optional[str] = None
    installed_at: Optional[str] = None
    artifact_dir: str = ""

    @property
    def review_ready(self) -> bool:
        return self.status == ProposalStatus.PENDING and self.pipeline_passed

    @property
    def credential_keys(self) -> List[str]:
        return [item.get("vault_key", "") for item in self.credentials if item.get("vault_key")]


class SkillFactory:
    """Governed proposal pipeline for first-party skills."""

    _PERMISSION_CAPABILITY_MAP = {
        "read_input": "tool.read_input",
        "write_output": "tool.write_output",
        "read_config": "tool.read_config",
        "file_read": "fs.read",
        "file_write": "fs.write",
        "shell_exec": "shell.exec",
        "execute_commands": "shell.exec",
        "network_fetch": "network.fetch",
        "network_post": "network.post",
        "skill_manage": "tool.skill_manage",
        "schedule_job": "schedule.create",
        "memory_read": "memory.read",
        "memory_write": "memory.write",
        "credential_read": "credential.read",
        "document_create": "tool.document_create",
    }

    def __init__(
        self,
        data_dir: str = "data",
        security_pipeline: SkillSecurityPipeline | None = None,
        trust_ledger: Any = None,
    ):
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._proposals_path = self._data_dir / _PROPOSALS_FILE
        self._proposals_root = self._data_dir / _PROPOSALS_DIR
        self._proposals_root.mkdir(parents=True, exist_ok=True)
        self._security_pipeline = security_pipeline or SkillSecurityPipeline(
            static_analyzer=StaticAnalyzer(),
            sandbox_tester=SandboxTester(),
            capability_enforcer=CapabilityEnforcer(),
            trust_ledger=trust_ledger,
        )
        self.actioncard_factory = None

    def _load_proposals(self) -> List[SkillProposal]:
        if not self._proposals_path.exists():
            return []
        try:
            data = json.loads(self._proposals_path.read_text(encoding="utf-8"))
            return [SkillProposal(**d) for d in data] if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load skill proposals: %s", exc)
            return []

    def _save_proposals(self, proposals: List[SkillProposal]) -> None:
        data = [p.model_dump(mode="json") for p in proposals]
        self._proposals_path.write_text(
            json.dumps(data, indent=2, default=str),
            encoding="utf-8",
        )

    def _replace_proposal(self, updated: SkillProposal) -> SkillProposal:
        proposals = self._load_proposals()
        replaced = False
        for index, proposal in enumerate(proposals):
            if proposal.id == updated.id:
                proposals[index] = updated
                replaced = True
                break
        if not replaced:
            proposals.append(updated)
        self._save_proposals(proposals)
        return updated

    def _proposal_dir(self, proposal_id: str) -> Path:
        return self._proposals_root / proposal_id

    @staticmethod
    def _utcnow() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _validate_skill_name(name: str) -> None:
        if not re.match(r"^[a-z][a-z0-9_]*$", name):
            raise SkillError(
                f"Invalid skill name '{name}': must be lowercase alphanumeric with underscores"
            )

    def _permission_to_capability(self, permission: str) -> str:
        return self._PERMISSION_CAPABILITY_MAP.get(permission, f"tool.{permission}")

    def _build_runtime_manifest(
        self,
        *,
        name: str,
        description: str,
        permissions: List[str],
        risk: str,
    ) -> Dict[str, Any]:
        return {
            "name": name,
            "version": "0.1.0",
            "description": description,
            "inputs": [{"name": "input_data", "type": "string", "required": True}],
            "outputs": [{"name": "result", "type": "string"}],
            "risk": risk.lower(),
            "permissions": permissions,
            "required_brain": "local_utility",
            "scheduler_eligible": False,
        }

    def _build_security_manifest(
        self,
        *,
        name: str,
        description: str,
        author: str,
        source: str,
        permissions: List[str],
        target_domains: List[str],
        credentials: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        capabilities_required = [
            {
                "capability": self._permission_to_capability(permission),
                "description": f"Derived from runtime permission '{permission}'",
                "required": True,
            }
            for permission in permissions
        ]
        return {
            "id": name,
            "name": name,
            "version": "0.1.0",
            "author": author,
            "source": source,
            "description": description,
            "capabilities_required": capabilities_required,
            "capabilities_optional": [],
            "credentials": credentials,
            "target_domains": target_domains,
            "data_reads": ["skill.input"],
            "data_writes": ["skill.output"],
            "does_not_access": [
                "undeclared_network",
                "undeclared_credentials",
                "undeclared_shell",
            ],
        }

    @staticmethod
    def _default_execute_code(name: str, description: str) -> str:
        safe_description = description.replace('"""', '\\"\\"\\"')
        return f'''"""
Skill: {name}
{safe_description}
"""


def execute(context, inputs):
    """Execute the {name} skill using its declared low-risk contract."""
    input_data = inputs.get("input_data", "")
    normalized = str(input_data).strip()
    return {{
        "result": normalized,
        "skill": "{name}",
        "received": bool(normalized),
    }}
'''

    @staticmethod
    def _default_test_code(name: str) -> str:
        return f'''"""
Governed tests for {name}.
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace


def _load_execute_module():
    spec = spec_from_file_location("execute", Path(__file__).with_name("execute.py"))
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_execute_returns_contract_shape():
    module = _load_execute_module()
    result = module.execute(SimpleNamespace(), {{"input_data": "example"}})
    assert isinstance(result, dict)
    assert "result" in result
'''

    @staticmethod
    def _normalize_credentials(raw_credentials: Any) -> List[Dict[str, str]]:
        if raw_credentials is None:
            return []
        normalized: List[Dict[str, str]] = []
        if isinstance(raw_credentials, list):
            for item in raw_credentials:
                if isinstance(item, str) and item.strip():
                    normalized.append(
                        {
                            "vault_key": item.strip(),
                            "type": "secret",
                            "purpose": "Skill runtime access",
                        }
                    )
                elif isinstance(item, dict) and item.get("vault_key"):
                    normalized.append(
                        {
                            "vault_key": str(item["vault_key"]).strip(),
                            "type": str(item.get("type", "secret")),
                            "purpose": str(item.get("purpose", "Skill runtime access")),
                        }
                    )
        return normalized

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        digest.update(path.read_bytes())
        return digest.hexdigest()

    def _artifact_hashes(self, package_dir: Path) -> Dict[str, str]:
        hashes: Dict[str, str] = {}
        for artifact in sorted(package_dir.glob("*")):
            if artifact.is_file():
                hashes[artifact.name] = self._sha256(artifact)
        return hashes

    def _write_artifact_package(self, proposal: SkillProposal) -> SkillProposal:
        package_dir = self._proposal_dir(proposal.id)
        package_dir.mkdir(parents=True, exist_ok=True)
        (package_dir / "skill.yaml").write_text(proposal.manifest_yaml, encoding="utf-8")
        (package_dir / "security_manifest.yaml").write_text(
            proposal.security_manifest_yaml,
            encoding="utf-8",
        )
        (package_dir / "execute.py").write_text(proposal.execute_code, encoding="utf-8")
        (package_dir / f"test_{proposal.name}.py").write_text(
            proposal.test_code,
            encoding="utf-8",
        )
        (package_dir / "README.md").write_text(
            (
                f"# {proposal.name}\n\n"
                f"Governed skill proposal `{proposal.id}`.\n\n"
                f"- Source: {proposal.source}\n"
                f"- Risk: {proposal.risk}\n"
                f"- Runtime permissions: {', '.join(proposal.permissions) or 'none'}\n"
                f"- Target domains: {', '.join(proposal.target_domains) or 'none'}\n"
            ),
            encoding="utf-8",
        )
        proposal.artifact_dir = str(package_dir)
        proposal.artifact_hashes = self._artifact_hashes(package_dir)
        return proposal

    def _ensure_artifact_package(self, proposal: SkillProposal) -> SkillProposal:
        package_dir = Path(proposal.artifact_dir) if proposal.artifact_dir else self._proposal_dir(proposal.id)
        required_files = {
            "skill.yaml",
            "security_manifest.yaml",
            "execute.py",
            f"test_{proposal.name}.py",
        }
        if package_dir.exists() and required_files.issubset({path.name for path in package_dir.glob("*")}):
            if not proposal.artifact_hashes:
                proposal.artifact_hashes = self._artifact_hashes(package_dir)
            proposal.artifact_dir = str(package_dir)
            return proposal
        return self._write_artifact_package(proposal)

    def _evaluate_artifact_package(self, proposal: SkillProposal) -> SkillProposal:
        proposal = self._ensure_artifact_package(proposal)
        package_dir = Path(proposal.artifact_dir)
        security_manifest = yaml.safe_load(proposal.security_manifest_yaml) or {}
        pipeline_result = self._security_pipeline.evaluate(package_dir, security_manifest)
        existing_owner_review = proposal.pipeline_stage_results.get("owner_review")
        proposal.pipeline_passed = pipeline_result.passed
        proposal.pipeline_failed_at_stage = pipeline_result.failed_at_stage or None
        proposal.pipeline_stage_results = pipeline_result.stage_results
        if existing_owner_review and existing_owner_review.get("status") != "pending":
            proposal.pipeline_stage_results["owner_review"] = existing_owner_review
        if pipeline_result.manifest is not None:
            proposal.approved_capabilities = pipeline_result.manifest.all_capabilities()
        proposal.status = (
            ProposalStatus.PENDING
            if pipeline_result.passed
            else ProposalStatus.REVIEW_FAILED
        )
        proposal.tests_status = "pipeline_passed" if pipeline_result.passed else "pipeline_failed"
        return proposal

    def _assert_artifact_conformance(self, proposal: SkillProposal) -> None:
        proposal = self._ensure_artifact_package(proposal)
        current_hashes = self._artifact_hashes(Path(proposal.artifact_dir))
        if current_hashes != proposal.artifact_hashes:
            raise SkillError(
                "Proposal artifacts changed after review; regenerate or re-approve before installation"
            )

    def create_proposal(
        self,
        *,
        name: str,
        description: str = "",
        permissions: Optional[List[str]] = None,
        execute_code: Optional[str] = None,
        test_code: Optional[str] = None,
        target_domains: Optional[List[str]] = None,
        credentials: Optional[List[Dict[str, str]]] = None,
        risk: str = "low",
        source: str = "first-party",
        author: str = "Lancelot",
    ) -> SkillProposal:
        permissions = permissions or ["read_input"]
        target_domains = target_domains or []
        credentials = self._normalize_credentials(credentials)
        self._validate_skill_name(name)

        runtime_manifest = self._build_runtime_manifest(
            name=name,
            description=description,
            permissions=permissions,
            risk=risk,
        )
        security_manifest = self._build_security_manifest(
            name=name,
            description=description,
            author=author,
            source=source,
            permissions=permissions,
            target_domains=target_domains,
            credentials=credentials,
        )

        proposal = SkillProposal(
            name=name,
            description=description,
            permissions=permissions,
            risk=risk.lower(),
            source=source,
            author=author,
            target_domains=target_domains,
            credentials=credentials,
            manifest_yaml=yaml.safe_dump(runtime_manifest, sort_keys=False),
            security_manifest_yaml=yaml.safe_dump(security_manifest, sort_keys=False),
            execute_code=execute_code or self._default_execute_code(name, description),
            test_code=test_code or self._default_test_code(name),
            tests_status="generated",
        )

        proposal = self._write_artifact_package(proposal)
        proposal = self._evaluate_artifact_package(proposal)
        self._replace_proposal(proposal)

        if self.actioncard_factory:
            try:
                self.actioncard_factory.from_skill_proposal(
                    proposal_id=proposal.id,
                    name=name,
                    description=description,
                )
            except Exception as exc:
                logger.warning("Failed to create ActionCard for skill proposal: %s", exc)

        logger.info(
            "Governed skill proposal created: name=%s id=%s status=%s",
            name,
            proposal.id,
            proposal.status.value,
        )
        return proposal

    def generate_skeleton(
        self,
        name: str,
        description: str = "",
        permissions: Optional[List[str]] = None,
    ) -> SkillProposal:
        """Backward-compatible wrapper for proposal generation."""
        return self.create_proposal(
            name=name,
            description=description,
            permissions=permissions,
        )

    def list_proposals(self) -> List[SkillProposal]:
        return self._load_proposals()

    def get_proposal(self, proposal_id: str) -> Optional[SkillProposal]:
        for proposal in self._load_proposals():
            if proposal.id == proposal_id:
                return proposal
        return None

    def approve_proposal(
        self,
        proposal_id: str,
        approved_by: str = "owner",
    ) -> SkillProposal:
        proposals = self._load_proposals()
        target = next((proposal for proposal in proposals if proposal.id == proposal_id), None)
        if target is None:
            raise SkillError(f"Proposal '{proposal_id}' not found")
        if target.status != ProposalStatus.PENDING:
            raise SkillError(
                f"Proposal status is '{target.status}', expected 'pending'"
            )
        if not target.pipeline_passed:
            raise SkillError("Proposal is not install-ready; security pipeline did not pass")

        target.approved_by = approved_by
        target.approved_at = self._utcnow()
        target.status = ProposalStatus.APPROVED
        target.pipeline_stage_results.setdefault("owner_review", {})
        target.pipeline_stage_results["owner_review"] = {
            "status": "approved",
            "approved_by": approved_by,
            "approved_at": target.approved_at,
        }
        self._save_proposals(proposals)
        logger.info("Skill proposal approved: id=%s by=%s", proposal_id, approved_by)
        return target

    def reject_proposal(self, proposal_id: str, reason: Optional[str] = None) -> SkillProposal:
        proposals = self._load_proposals()
        target = next((proposal for proposal in proposals if proposal.id == proposal_id), None)
        if target is None:
            raise SkillError(f"Proposal '{proposal_id}' not found")
        if target.status == ProposalStatus.INSTALLED:
            raise SkillError("Installed proposals cannot be rejected")

        target.status = ProposalStatus.REJECTED
        target.rejected_reason = reason
        target.rejected_at = self._utcnow()
        target.pipeline_stage_results.setdefault("owner_review", {})
        target.pipeline_stage_results["owner_review"] = {
            "status": "rejected",
            "reason": reason,
            "rejected_at": target.rejected_at,
        }
        self._save_proposals(proposals)
        return target

    def install_proposal(
        self,
        proposal_id: str,
        registry: Any,
        install_dir: Optional[str] = None,
    ) -> Any:
        proposals = self._load_proposals()
        target = next((proposal for proposal in proposals if proposal.id == proposal_id), None)
        if target is None:
            raise SkillError(f"Proposal '{proposal_id}' not found")
        if target.status != ProposalStatus.APPROVED:
            raise SkillError(
                f"Proposal must be approved before installation (status='{target.status}')"
            )

        self._assert_artifact_conformance(target)
        target = self._evaluate_artifact_package(target)
        if not target.pipeline_passed:
            raise SkillError(
                f"Security pipeline blocked installation at stage '{target.pipeline_failed_at_stage or 'unknown'}'"
            )

        security_manifest = yaml.safe_load(target.security_manifest_yaml) or {}
        pipeline_result = self._security_pipeline.evaluate(Path(target.artifact_dir), security_manifest)
        if not pipeline_result.passed or pipeline_result.manifest is None:
            raise SkillError(
                f"Security pipeline blocked installation at stage '{pipeline_result.failed_at_stage or 'unknown'}'"
            )
        approved_capabilities = pipeline_result.manifest.all_capabilities()
        if not self._security_pipeline.approve_and_install(pipeline_result, approved_capabilities):
            raise SkillError("Security pipeline approval stage failed")

        base = Path(install_dir or str(self._data_dir / _INSTALLED_SKILLS_DIR))
        base.mkdir(parents=True, exist_ok=True)
        skill_dir = base / f"{target.name}-{target.id}"
        if skill_dir.exists():
            shutil.rmtree(skill_dir)
        shutil.copytree(Path(target.artifact_dir), skill_dir)
        entry = registry.install_skill(str(skill_dir / "skill.yaml"))

        target.pipeline_passed = True
        target.pipeline_failed_at_stage = None
        target.pipeline_stage_results = pipeline_result.stage_results
        if target.approved_by:
            target.pipeline_stage_results["owner_review"] = {
                "status": "approved",
                "approved_by": target.approved_by,
                "approved_at": target.approved_at,
            }
        target.pipeline_stage_results["installation"] = {
            "status": "installed",
            "installed_at": self._utcnow(),
        }
        target.approved_capabilities = approved_capabilities
        target.status = ProposalStatus.INSTALLED
        target.installed_at = target.pipeline_stage_results["installation"]["installed_at"]
        self._save_proposals(proposals)

        logger.info("Skill installed from governed proposal: name=%s id=%s", target.name, target.id)
        return entry
