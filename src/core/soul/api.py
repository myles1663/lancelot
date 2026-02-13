"""
Soul API — REST endpoints for soul status, proposals, and activation (Prompt 5 / A5).

Endpoints:
    GET  /soul/status                    — active version + pending proposals
    POST /soul/proposals/{id}/approve    — owner approves a proposal
    POST /soul/proposals/{id}/activate   — owner activates an approved proposal

All mutation endpoints require owner identity (Bearer token).
"""

from __future__ import annotations

import hmac
import logging
import os
import threading
from typing import Optional

import yaml
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from src.core.soul.store import (
    Soul,
    SoulStoreError,
    get_active_version,
    set_active_version,
    list_versions,
)
from src.core.soul.amendments import (
    ProposalStatus,
    create_proposal,
    list_proposals,
    get_proposal,
    save_proposals,
)
from src.core.soul.linter import lint, lint_or_raise

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/soul", tags=["soul"])

# Soul directory — configurable via env or default
_SOUL_DIR: Optional[str] = os.environ.get("SOUL_DIR", None)
_API_TOKEN = os.environ.get("LANCELOT_API_TOKEN", os.environ.get("API_TOKEN", ""))
_proposals_lock = threading.Lock()


def _set_soul_dir(soul_dir: str) -> None:
    """Set the soul directory (used in tests)."""
    global _SOUL_DIR
    _SOUL_DIR = soul_dir


def _verify_owner(request: Request) -> bool:
    """Check that the request comes from the owner (Bearer token)."""
    if not _API_TOKEN:
        logger.warning(
            "SECURITY: Soul API running in dev mode — no authentication token configured. "
            "Set LANCELOT_API_TOKEN for production."
        )
        return True  # dev mode — no token configured
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        return hmac.compare_digest(auth_header[7:], _API_TOKEN)
    return False


def _get_active_overlays() -> list:
    """Return metadata about currently active soul overlays."""
    try:
        from src.core.soul.layers import load_overlays
        overlays = load_overlays(_SOUL_DIR)
        return [
            {
                "name": o.overlay_name,
                "feature_flag": o.feature_flag,
                "description": o.description,
                "risk_rules_count": len(o.risk_rules),
                "tone_invariants_count": len(o.tone_invariants),
                "memory_ethics_count": len(o.memory_ethics),
                "autonomy_additions": len(o.autonomy_posture.allowed_autonomous) + len(o.autonomy_posture.requires_approval),
            }
            for o in overlays
        ]
    except Exception as exc:
        logger.debug("Could not load overlays: %s", exc)
        return []


# ---------------------------------------------------------------------------
# GET /soul/status
# ---------------------------------------------------------------------------

@router.get("/status")
async def soul_status():
    """Return the active soul version, pending proposals, and active overlays."""
    try:
        version = get_active_version(_SOUL_DIR)
        versions = list_versions(_SOUL_DIR)
        proposals = list_proposals(_SOUL_DIR)
        pending = [p.model_dump() for p in proposals
                   if p.status == ProposalStatus.PENDING]

        # Load active overlays
        active_overlays = _get_active_overlays()

        return {
            "active_version": version,
            "available_versions": versions,
            "pending_proposals": pending,
            "active_overlays": active_overlays,
        }
    except SoulStoreError as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


# ---------------------------------------------------------------------------
# GET /soul/content — full parsed soul document
# ---------------------------------------------------------------------------

@router.get("/content")
async def soul_content():
    """Return the full active soul document (with overlays merged) as structured JSON."""
    try:
        from src.core.soul.store import load_active_soul, _resolve_soul_dir
        from src.core.soul.layers import load_overlays, merge_soul

        base_soul = load_active_soul(_SOUL_DIR)
        d = _resolve_soul_dir(_SOUL_DIR)
        version_file = d / "soul_versions" / f"soul_{base_soul.version}.yaml"
        raw_yaml = version_file.read_text(encoding="utf-8") if version_file.exists() else ""

        # Apply overlays to show the actual merged soul
        overlays = load_overlays(_SOUL_DIR)
        active_overlays = _get_active_overlays()

        if overlays:
            merged = merge_soul(base_soul, overlays)
            return {
                "soul": merged.model_dump(),
                "raw_yaml": raw_yaml,
                "active_overlays": active_overlays,
            }

        return {
            "soul": base_soul.model_dump(),
            "raw_yaml": raw_yaml,
            "active_overlays": [],
        }
    except SoulStoreError as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


# ---------------------------------------------------------------------------
# POST /soul/propose — create amendment proposal from edited YAML
# ---------------------------------------------------------------------------

@router.post("/propose")
async def propose_amendment(request: Request):
    """Create a soul amendment proposal from edited YAML.

    Body: {"proposed_yaml": "<full yaml text>", "author": "Commander"}
    """
    if not _verify_owner(request):
        raise HTTPException(status_code=403, detail="Owner identity required")

    try:
        body = await request.json()
        proposed_yaml = body.get("proposed_yaml", "")
        author = body.get("author", "Commander")

        if not proposed_yaml.strip():
            raise HTTPException(status_code=400, detail="proposed_yaml is required")

        # Validate the YAML parses and passes schema
        proposed_dict = yaml.safe_load(proposed_yaml)
        soul = Soul(**proposed_dict)

        # Run linter — return warnings but don't block
        issues = lint(soul)
        warnings = [{"rule": i.rule, "severity": i.severity.value, "message": i.message}
                    for i in issues]

        # Check for critical issues
        critical = [w for w in warnings if w["severity"] == "critical"]
        if critical:
            return JSONResponse(status_code=422, content={
                "error": "Critical linter issues found",
                "issues": warnings,
            })

        # Create proposal
        current_version = get_active_version(_SOUL_DIR)
        proposal = create_proposal(
            from_version=current_version,
            proposed_yaml_text=proposed_yaml,
            author=author,
            soul_dir=_SOUL_DIR,
        )

        return {
            "proposal_id": proposal.id,
            "proposed_version": proposal.proposed_version,
            "diff_summary": proposal.diff_summary,
            "warnings": warnings,
            "status": proposal.status.value,
        }
    except HTTPException:
        raise
    except SoulStoreError as exc:
        return JSONResponse(status_code=422, content={"error": str(exc)})
    except Exception as exc:
        logger.error("propose_amendment error: %s", exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})


# ---------------------------------------------------------------------------
# POST /soul/proposals/{id}/approve
# ---------------------------------------------------------------------------

@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(proposal_id: str, request: Request):
    """Approve a pending soul amendment proposal. Owner only."""
    if not _verify_owner(request):
        raise HTTPException(status_code=403, detail="Owner identity required")

    with _proposals_lock:
        proposals = list_proposals(_SOUL_DIR)
        target = None
        for p in proposals:
            if p.id == proposal_id:
                target = p
                break

        if target is None:
            raise HTTPException(status_code=404, detail="Proposal not found")

        if target.status != ProposalStatus.PENDING:
            raise HTTPException(
                status_code=409,
                detail=f"Proposal status is '{target.status}', expected 'pending'",
            )

        target.status = ProposalStatus.APPROVED
        save_proposals(proposals, _SOUL_DIR)

    logger.info("soul_approved: proposal=%s, version=%s",
                target.id, target.proposed_version)
    return {"status": "approved", "proposal_id": target.id}


# ---------------------------------------------------------------------------
# POST /soul/proposals/{id}/activate
# ---------------------------------------------------------------------------

@router.post("/proposals/{proposal_id}/activate")
async def activate_proposal(proposal_id: str, request: Request):
    """Activate an approved soul amendment proposal. Owner only.

    Steps:
    1. Verify owner identity
    2. Check proposal is approved
    3. Write proposed YAML to soul_versions/
    4. Validate with Pydantic + linter
    5. Set ACTIVE pointer
    6. Log receipt
    """
    if not _verify_owner(request):
        raise HTTPException(status_code=403, detail="Owner identity required")

    with _proposals_lock:
        proposals = list_proposals(_SOUL_DIR)
        target = None
        for p in proposals:
            if p.id == proposal_id:
                target = p
                break

        if target is None:
            raise HTTPException(status_code=404, detail="Proposal not found")

        if target.status != ProposalStatus.APPROVED:
            raise HTTPException(
                status_code=409,
                detail=f"Proposal must be approved first (status='{target.status}')",
            )

        if not target.proposed_yaml:
            raise HTTPException(status_code=400, detail="Proposal has no YAML content")

        # Parse and validate
        try:
            proposed_dict = yaml.safe_load(target.proposed_yaml)
            soul = Soul(**proposed_dict)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Proposed soul validation failed: {exc}",
            )

        # Run linter
        try:
            lint_or_raise(soul)
        except SoulStoreError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

        # Write version file
        from src.core.soul.store import _resolve_soul_dir
        d = _resolve_soul_dir(_SOUL_DIR)
        version_file = d / "soul_versions" / f"soul_{soul.version}.yaml"
        version_file.write_text(target.proposed_yaml, encoding="utf-8")

        # Set active pointer
        set_active_version(soul.version, _SOUL_DIR)

        # Update proposal status
        target.status = ProposalStatus.ACTIVATED
        save_proposals(proposals, _SOUL_DIR)

    logger.info("soul_activated: proposal=%s, version=%s",
                target.id, soul.version)
    return {
        "status": "activated",
        "proposal_id": target.id,
        "active_version": soul.version,
    }
