"""
Skills API — /api/skills/*

REST endpoints for the War Room to manage skill proposals and installed skills.
Proposals are created by Lancelot (via skill_manager builtin) and require
owner approval via these endpoints before installation.
"""

import logging
from importlib import import_module
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from src.core.api_auth import require_authenticated_request
from src.core.auth_api import require_operator_capability, resolve_authenticated_identity
from src.core.governance_receipts import emit_governance_receipt
from src.shared.receipts import ActionType

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/skills",
    tags=["skills"],
    dependencies=[
        Depends(require_authenticated_request),
        Depends(require_operator_capability("skills.admin")),
    ],
)

# Set by init_skills_api() at startup
_skill_factory = None
_skill_registry = None
_skill_executor = None
_actioncard_factory = None


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved_by: Optional[str] = None


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Optional[str] = None


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


def _safe_text(path: Path) -> str:
    try:
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to read skill artifact %s: %s", path, exc)
    return ""


def _builtin_manifest(skill_name: str) -> dict:
    try:
        module = import_module(f"src.core.skills.builtins.{skill_name}")
    except Exception:
        return {}
    manifest = getattr(module, "MANIFEST", None)
    return dict(manifest) if isinstance(manifest, dict) else {}


def _builtin_execute_code(skill_name: str) -> str:
    try:
        module = import_module(f"src.core.skills.builtins.{skill_name}")
    except Exception:
        return ""
    module_path = getattr(module, "__file__", "")
    return _safe_text(Path(module_path)) if module_path else ""


def _manifest_dict(entry) -> dict:
    manifest = getattr(entry, "manifest", None)
    if manifest is not None:
        if hasattr(manifest, "model_dump"):
            return manifest.model_dump(mode="json")
        if isinstance(manifest, dict):
            return manifest

    manifest_path = getattr(entry, "manifest_path", "")
    if manifest_path:
        try:
            data = yaml.safe_load(Path(manifest_path).read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("Failed to load installed skill manifest %s: %s", manifest_path, exc)

    return _builtin_manifest(getattr(entry, "name", ""))


def _manifest_source(entry, manifest: dict) -> str:
    if getattr(entry, "manifest", None) is not None:
        return "registry"
    manifest_path = getattr(entry, "manifest_path", "")
    if manifest_path and Path(manifest_path).exists():
        return "registry"
    if manifest:
        return "builtin"
    return "missing"


def _manifest_yaml(entry, manifest: dict) -> str:
    manifest_path = getattr(entry, "manifest_path", "")
    if manifest_path:
        text = _safe_text(Path(manifest_path))
        if text:
            return text
    return yaml.safe_dump(manifest, sort_keys=False) if manifest else ""


def _artifact_code(entry, artifact_name: str) -> str:
    manifest_path = getattr(entry, "manifest_path", "")
    if not manifest_path:
        return ""
    return _safe_text(Path(manifest_path).parent / artifact_name)


def _installed_skill_summary(entry) -> dict:
    manifest = _manifest_dict(entry)
    return {
        "name": getattr(entry, "name", ""),
        "version": getattr(entry, "version", ""),
        "enabled": bool(getattr(entry, "enabled", False)),
        "ownership": str(_enum_value(getattr(entry, "ownership", ""))),
        "signature_state": str(_enum_value(getattr(entry, "signature_state", ""))),
        "installed_at": getattr(entry, "installed_at", None),
        "description": str(manifest.get("description", "")),
        "risk": str(_enum_value(manifest.get("risk", ""))),
        "permissions": list(manifest.get("permissions") or []),
    }


def _source_proposal_for_skill(skill_name: str) -> Optional[dict]:
    if _skill_factory is None or not hasattr(_skill_factory, "list_proposals"):
        return None
    try:
        proposals = _skill_factory.list_proposals()
    except Exception as exc:
        logger.warning("Failed to load skill proposals for installed skill detail: %s", exc)
        return None

    matches = [p for p in proposals if getattr(p, "name", None) == skill_name]
    if not matches:
        return None
    proposal = sorted(matches, key=lambda p: getattr(p, "created_at", ""), reverse=True)[0]
    return {
        "id": getattr(proposal, "id", ""),
        "status": str(_enum_value(getattr(proposal, "status", ""))),
        "source": getattr(proposal, "source", ""),
        "author": getattr(proposal, "author", ""),
        "pipeline_passed": bool(getattr(proposal, "pipeline_passed", False)),
        "pipeline_failed_at_stage": getattr(proposal, "pipeline_failed_at_stage", None),
        "pipeline_stage_results": getattr(proposal, "pipeline_stage_results", {}),
        "artifact_hashes": getattr(proposal, "artifact_hashes", {}),
        "approved_capabilities": getattr(proposal, "approved_capabilities", []),
        "created_at": getattr(proposal, "created_at", None),
        "approved_by": getattr(proposal, "approved_by", None),
        "approved_at": getattr(proposal, "approved_at", None),
        "installed_at": getattr(proposal, "installed_at", None),
    }


def _installed_skill_detail(entry) -> dict:
    manifest = _manifest_dict(entry)
    manifest_path = getattr(entry, "manifest_path", "")
    execute_code = _artifact_code(entry, "execute.py") or _builtin_execute_code(getattr(entry, "name", ""))
    test_code = ""
    if manifest_path:
        manifest_dir = Path(manifest_path).parent
        test_files = sorted(manifest_dir.glob("test_*.py"))
        test_code = "\n\n".join(_safe_text(path) for path in test_files)

    return {
        **_installed_skill_summary(entry),
        "manifest_path": manifest_path,
        "manifest": manifest or None,
        "manifest_source": _manifest_source(entry, manifest),
        "manifest_yaml": _manifest_yaml(entry, manifest),
        "execute_code": execute_code,
        "test_code": test_code,
        "inputs": list(manifest.get("inputs") or []),
        "outputs": list(manifest.get("outputs") or []),
        "required_brain": manifest.get("required_brain", ""),
        "scheduler_eligible": bool(manifest.get("scheduler_eligible", False)),
        "sentry_requirements": list(manifest.get("sentry_requirements") or []),
        "receipts": manifest.get("receipts") or {},
        "source_proposal": _source_proposal_for_skill(getattr(entry, "name", "")),
    }


def _get_installed_skill_or_404(skill_name: str):
    if _skill_registry is None:
        raise HTTPException(status_code=503, detail="SkillRegistry not initialized")
    if not hasattr(_skill_registry, "get_skill"):
        raise HTTPException(status_code=503, detail="SkillRegistry detail lookup not available")
    entry = _skill_registry.get_skill(skill_name)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    return entry


def init_skills_api(factory, registry, executor, actioncard_factory=None) -> None:
    """Initialise the skills API with references to subsystems."""
    global _skill_factory, _skill_registry, _skill_executor, _actioncard_factory
    _skill_factory = factory
    _skill_registry = registry
    _skill_executor = executor
    _actioncard_factory = actioncard_factory
    logger.info("Skills API initialized.")


# ---------------------------------------------------------------------------
# Proposals
# ---------------------------------------------------------------------------

@router.get("/proposals")
async def list_proposals():
    """List all skill proposals."""
    if _skill_factory is None:
        raise HTTPException(status_code=503, detail="SkillFactory not initialized")

    proposals = _skill_factory.list_proposals()
    return {
        "proposals": [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "permissions": p.permissions,
                "risk": p.risk,
                "source": p.source,
                "target_domains": p.target_domains,
                "credential_keys": p.credential_keys,
                "approved_capabilities": p.approved_capabilities,
                "status": p.status.value if hasattr(p.status, 'value') else str(p.status),
                "pipeline_passed": p.pipeline_passed,
                "pipeline_failed_at_stage": p.pipeline_failed_at_stage,
                "created_at": p.created_at,
                "approved_by": p.approved_by,
            }
            for p in proposals
        ],
        "total": len(proposals),
    }


@router.get("/proposals/{proposal_id}")
async def get_proposal(proposal_id: str):
    """Get a single proposal with full detail (including code)."""
    if _skill_factory is None:
        raise HTTPException(status_code=503, detail="SkillFactory not initialized")

    proposal = _skill_factory.get_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail=f"Proposal '{proposal_id}' not found")

    return {
        "id": proposal.id,
        "name": proposal.name,
        "description": proposal.description,
        "permissions": proposal.permissions,
        "risk": proposal.risk,
        "source": proposal.source,
        "author": proposal.author,
        "target_domains": proposal.target_domains,
        "credentials": proposal.credentials,
        "credential_keys": proposal.credential_keys,
        "approved_capabilities": proposal.approved_capabilities,
        "manifest_yaml": proposal.manifest_yaml,
        "security_manifest_yaml": proposal.security_manifest_yaml,
        "execute_code": proposal.execute_code,
        "test_code": proposal.test_code,
        "tests_status": proposal.tests_status,
        "status": proposal.status.value if hasattr(proposal.status, 'value') else str(proposal.status),
        "pipeline_passed": proposal.pipeline_passed,
        "pipeline_failed_at_stage": proposal.pipeline_failed_at_stage,
        "pipeline_stage_results": proposal.pipeline_stage_results,
        "artifact_hashes": proposal.artifact_hashes,
        "created_at": proposal.created_at,
        "approved_by": proposal.approved_by,
        "approved_at": proposal.approved_at,
        "rejected_reason": proposal.rejected_reason,
        "rejected_at": proposal.rejected_at,
        "installed_at": proposal.installed_at,
    }


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(
    proposal_id: str,
    request: Request,
    body: ApproveRequest = ApproveRequest(),
):
    """Approve a pending proposal (owner action)."""
    if _skill_factory is None:
        raise HTTPException(status_code=503, detail="SkillFactory not initialized")

    try:
        identity = resolve_authenticated_identity(request)
        approved_by = identity.display_name or identity.operator_id or "operator"
        proposal = _skill_factory.approve_proposal(proposal_id, approved_by=approved_by)
        return {
            "status": "approved",
            "proposal_id": proposal.id,
            "name": proposal.name,
            "approved_by": proposal.approved_by,
            "approved_at": getattr(proposal, "approved_at", None),
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(proposal_id: str, body: RejectRequest = RejectRequest()):
    """Reject a pending proposal (owner action)."""
    if _skill_factory is None:
        raise HTTPException(status_code=503, detail="SkillFactory not initialized")

    try:
        proposal = _skill_factory.reject_proposal(proposal_id, reason=body.reason)
        return {
            "status": "rejected",
            "proposal_id": proposal.id,
            "name": proposal.name,
            "rejected_reason": getattr(proposal, "rejected_reason", None),
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/proposals/{proposal_id}/install")
async def install_proposal(proposal_id: str):
    """Install an approved proposal into the skill registry."""
    if _skill_factory is None or _skill_registry is None:
        raise HTTPException(status_code=503, detail="Skill subsystem not initialized")

    try:
        entry = _skill_factory.install_proposal(proposal_id, registry=_skill_registry)
        return {
            "status": "installed",
            "proposal_id": proposal_id,
            "name": entry.name if hasattr(entry, 'name') else str(entry),
            "validated_capabilities": (
                getattr(proposal, "approved_capabilities", [])
                if (proposal := _skill_factory.get_proposal(proposal_id))
                else []
            ),
            "message": f"Skill installed and registered. It can now be run via skill_manager.",
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# Installed Skills
# ---------------------------------------------------------------------------

@router.get("")
async def list_skills():
    """List all installed skills."""
    if _skill_registry is None:
        raise HTTPException(status_code=503, detail="SkillRegistry not initialized")

    skills = _skill_registry.list_skills()
    return {
        "skills": [_installed_skill_summary(s) for s in skills],
        "total": len(skills),
    }


@router.get("/{skill_name}")
async def get_skill(skill_name: str):
    """Get installed skill detail for operator inspection."""
    entry = _get_installed_skill_or_404(skill_name)
    return _installed_skill_detail(entry)


@router.post("/{skill_name}/enable")
async def enable_skill(skill_name: str, request: Request):
    """Enable an installed skill."""
    _get_installed_skill_or_404(skill_name)
    try:
        _skill_registry.enable_skill(skill_name)
        entry = _get_installed_skill_or_404(skill_name)
        emit_governance_receipt(
            request,
            ActionType.TOOL_ENABLED,
            action_name="enable_skill",
            inputs={"skill_name": skill_name},
            outputs={"enabled": True},
            metadata={"subsystem": "skills"},
        )
        return _installed_skill_detail(entry)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{skill_name}/disable")
async def disable_skill(skill_name: str, request: Request):
    """Disable an installed skill."""
    _get_installed_skill_or_404(skill_name)
    try:
        _skill_registry.disable_skill(skill_name)
        entry = _get_installed_skill_or_404(skill_name)
        emit_governance_receipt(
            request,
            ActionType.TOOL_DISABLED,
            action_name="disable_skill",
            inputs={"skill_name": skill_name},
            outputs={"enabled": False},
            metadata={"subsystem": "skills"},
        )
        return _installed_skill_detail(entry)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
