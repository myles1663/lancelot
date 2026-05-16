"""
Skills API — /api/skills/*

REST endpoints for the War Room to manage skill proposals and installed skills.
Proposals are created by Lancelot (via skill_manager builtin) and require
owner approval via these endpoints before installation.
"""

import importlib
import logging
import re
from copy import deepcopy
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from src.core.api_auth import require_authenticated_request
from src.core.auth_api import require_operator_capability, resolve_authenticated_identity
from src.core.skills.registry import SkillError

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

_BUILTIN_SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

_BUILTIN_MANIFEST_FALLBACKS = {
    "echo": {
        "name": "echo",
        "version": "1.0.0",
        "description": "Return the submitted input payload for connectivity and skill-loop smoke tests.",
        "risk": "LOW",
        "permissions": [],
        "inputs": [
            {
                "name": "input_data",
                "type": "object",
                "required": False,
                "description": "Payload to return unchanged.",
            }
        ],
        "outputs": [
            {
                "name": "result",
                "type": "object",
                "description": "Echoed input payload.",
            }
        ],
    }
}


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved_by: Optional[str] = None


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Optional[str] = None


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


def _enum_value(value):
    return value.value if hasattr(value, "value") else str(value)


def _skill_summary(skill):
    return {
        "name": skill.name,
        "version": skill.version,
        "enabled": skill.enabled,
        "ownership": _enum_value(skill.ownership),
    }


def _manifest_payload(manifest):
    if manifest is None:
        return None
    if hasattr(manifest, "model_dump"):
        return manifest.model_dump(mode="json")
    if hasattr(manifest, "dict"):
        return manifest.dict()
    return manifest


def _builtin_manifest_payload(skill_name: str):
    fallback = _BUILTIN_MANIFEST_FALLBACKS.get(skill_name)
    if fallback is not None:
        return deepcopy(fallback)

    if not _BUILTIN_SKILL_NAME_RE.fullmatch(skill_name):
        return None

    try:
        module = importlib.import_module(f"src.core.skills.builtins.{skill_name}")
    except ImportError:
        return None

    manifest = getattr(module, "MANIFEST", None)
    if isinstance(manifest, dict):
        return deepcopy(manifest)
    return _manifest_payload(manifest)


def _manifest_source(registry_manifest, resolved_manifest):
    if registry_manifest is not None:
        return "registry"
    if resolved_manifest is not None:
        return "builtin"
    return "missing"


@router.get("")
async def list_skills():
    """List all installed skills."""
    if _skill_registry is None:
        raise HTTPException(status_code=503, detail="SkillRegistry not initialized")

    skills = _skill_registry.list_skills()
    return {
        "skills": [_skill_summary(s) for s in skills],
        "total": len(skills),
    }


@router.get("/{skill_name}")
async def get_skill(skill_name: str):
    """Get a single installed skill with registry and manifest detail."""
    if _skill_registry is None:
        raise HTTPException(status_code=503, detail="SkillRegistry not initialized")

    skill = _skill_registry.get_skill(skill_name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

    manifest = _manifest_payload(skill.manifest)
    if manifest is None:
        manifest = _builtin_manifest_payload(skill.name)

    return {
        **_skill_summary(skill),
        "signature_state": _enum_value(skill.signature_state),
        "installed_at": skill.installed_at,
        "manifest_path": skill.manifest_path,
        "manifest_source": _manifest_source(skill.manifest, manifest),
        "description": manifest.get("description") if isinstance(manifest, dict) else None,
        "permissions": manifest.get("permissions", []) if isinstance(manifest, dict) else [],
        "inputs": manifest.get("inputs", []) if isinstance(manifest, dict) else [],
        "outputs": manifest.get("outputs", []) if isinstance(manifest, dict) else [],
        "risk": manifest.get("risk") if isinstance(manifest, dict) else None,
        "manifest": manifest,
    }


@router.post("/{skill_name}/enable")
async def enable_skill(skill_name: str):
    """Enable an installed skill."""
    if _skill_registry is None:
        raise HTTPException(status_code=503, detail="SkillRegistry not initialized")

    try:
        _skill_registry.enable_skill(skill_name)
    except SkillError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    skill = _skill_registry.get_skill(skill_name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    return {"status": "enabled", "skill": _skill_summary(skill)}


@router.post("/{skill_name}/disable")
async def disable_skill(skill_name: str):
    """Disable an installed skill."""
    if _skill_registry is None:
        raise HTTPException(status_code=503, detail="SkillRegistry not initialized")

    try:
        _skill_registry.disable_skill(skill_name)
    except SkillError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    skill = _skill_registry.get_skill(skill_name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    return {"status": "disabled", "skill": _skill_summary(skill)}
