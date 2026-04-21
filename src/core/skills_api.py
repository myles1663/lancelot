"""
Skills API — /api/skills/*

REST endpoints for the War Room to manage skill proposals and installed skills.
Proposals are created by Lancelot (via skill_manager builtin) and require
owner approval via these endpoints before installation.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from src.core.api_auth import require_authenticated_request
from src.core.auth_api import require_operator_capability, resolve_authenticated_identity

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
        "skills": [
            {
                "name": s.name,
                "version": s.version,
                "enabled": s.enabled,
                "ownership": s.ownership.value if hasattr(s.ownership, 'value') else str(s.ownership),
            }
            for s in skills
        ],
        "total": len(skills),
    }
