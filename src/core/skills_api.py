"""
Skills API — /api/skills/*

REST endpoints for the War Room to manage skill proposals and installed skills.
Proposals are created by Lancelot (via skill_manager builtin) and require
owner approval via these endpoints before installation.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/skills", tags=["skills"])

# Set by init_skills_api() at startup
_skill_factory = None
_skill_registry = None
_skill_executor = None


class ApproveRequest(BaseModel):
    approved_by: str = "owner"


class RejectRequest(BaseModel):
    reason: Optional[str] = None


def init_skills_api(factory, registry, executor) -> None:
    """Initialise the skills API with references to subsystems."""
    global _skill_factory, _skill_registry, _skill_executor
    _skill_factory = factory
    _skill_registry = registry
    _skill_executor = executor
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
                "status": p.status.value if hasattr(p.status, 'value') else str(p.status),
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
        "manifest_yaml": proposal.manifest_yaml,
        "execute_code": proposal.execute_code,
        "test_code": proposal.test_code,
        "tests_status": proposal.tests_status,
        "status": proposal.status.value if hasattr(proposal.status, 'value') else str(proposal.status),
        "created_at": proposal.created_at,
        "approved_by": proposal.approved_by,
    }


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(proposal_id: str, body: ApproveRequest = ApproveRequest()):
    """Approve a pending proposal (owner action)."""
    if _skill_factory is None:
        raise HTTPException(status_code=503, detail="SkillFactory not initialized")

    try:
        proposal = _skill_factory.approve_proposal(proposal_id, approved_by=body.approved_by)
        return {
            "status": "approved",
            "proposal_id": proposal.id,
            "name": proposal.name,
            "approved_by": proposal.approved_by,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(proposal_id: str, body: RejectRequest = RejectRequest()):
    """Reject a pending proposal (owner action)."""
    if _skill_factory is None:
        raise HTTPException(status_code=503, detail="SkillFactory not initialized")

    try:
        proposal = _skill_factory.reject_proposal(proposal_id)
        return {
            "status": "rejected",
            "proposal_id": proposal.id,
            "name": proposal.name,
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
