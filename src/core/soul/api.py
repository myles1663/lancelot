# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Soul API endpoints for soul status, proposals, and activation.

Endpoints:
    GET  /soul/status                    — active version + pending proposals
    POST /soul/proposals/{id}/approve    — owner approves a proposal
    POST /soul/proposals/{id}/activate   — owner activates an approved proposal

All mutation endpoints require the Soul admin capability.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from src.core.api_auth import require_authenticated_request
from src.core.auth_api import get_api_key_identity, request_has_capability, resolve_operator_identity

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

router = APIRouter(
    prefix="/soul",
    tags=["soul"],
    dependencies=[Depends(require_authenticated_request)],
)

# Soul directory — configurable via env or default
_SOUL_DIR: Optional[str] = os.environ.get("SOUL_DIR", None)
_proposals_lock = threading.Lock()

# V31: ActionCard factory — set by gateway.py during startup
_actioncard_factory = None
_runtime_reload_callback = None


class ProposeAmendmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposed_yaml: str = ""


def init_soul_actioncards(factory) -> None:
    """Inject ActionCard factory for proposal notifications."""
    global _actioncard_factory
    _actioncard_factory = factory


def init_soul_runtime(reload_callback) -> None:
    """Inject a runtime callback for live Soul activation."""
    global _runtime_reload_callback
    _runtime_reload_callback = reload_callback


def _approve_proposal_direct(
    proposal_id: str,
    *,
    actor: str = "operator",
):
    """Approve a pending Soul proposal outside the HTTP request layer."""
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

    logger.info(
        "soul_approved: proposal=%s, version=%s, actor=%s",
        target.id,
        target.proposed_version,
        actor,
    )
    return {"status": "approved", "proposal_id": target.id}


def _reject_proposal_direct(
    proposal_id: str,
    *,
    actor: str = "operator",
):
    """Reject a pending Soul proposal outside the HTTP request layer."""
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

        target.status = ProposalStatus.REJECTED
        save_proposals(proposals, _SOUL_DIR)

    logger.info(
        "soul_rejected: proposal=%s, version=%s, actor=%s",
        target.id,
        target.proposed_version,
        actor,
    )
    return {"status": "denied", "proposal_id": target.id}


def _set_soul_dir(soul_dir: str) -> None:
    """Set the soul directory (used in tests)."""
    global _SOUL_DIR
    _SOUL_DIR = soul_dir


async def _parse_request_model(request: Request, model_cls: type[BaseModel]) -> BaseModel:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Request body must be valid JSON") from exc
    try:
        return model_cls.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


def _verify_owner(request: Request) -> bool:
    """Check that the request has the Soul admin capability."""
    return request_has_capability(request, "soul.admin")


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


def _load_merged_active_soul() -> Soul:
    """Load the active Soul and apply any currently enabled overlays."""
    from src.core.soul.store import load_active_soul
    from src.core.soul.layers import load_overlays, merge_soul

    active_soul = load_active_soul(_SOUL_DIR)
    overlays = load_overlays(_SOUL_DIR)
    if overlays:
        return merge_soul(active_soul, overlays)
    return active_soul


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
        from src.core.soul.layers import load_overlays

        base_soul = load_active_soul(_SOUL_DIR)
        d = _resolve_soul_dir(_SOUL_DIR)
        version_file = d / "soul_versions" / f"soul_{base_soul.version}.yaml"
        raw_yaml = version_file.read_text(encoding="utf-8") if version_file.exists() else ""

        # Apply overlays to show the actual merged soul
        overlays = load_overlays(_SOUL_DIR)
        active_overlays = _get_active_overlays()

        if overlays:
            merged = _load_merged_active_soul()
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

    Body: {"proposed_yaml": "<full yaml text>"}
    """
    if not _verify_owner(request):
        raise HTTPException(status_code=403, detail="Owner identity required")

    try:
        body = await _parse_request_model(request, ProposeAmendmentRequest)
        proposed_yaml = body.proposed_yaml
        identity = resolve_operator_identity(request)
        if identity is None:
            identity = get_api_key_identity(request)
        author = identity.display_name or identity.operator_id or "owner"

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

        # V31: Emit ActionCard for cross-channel approval notification
        if _actioncard_factory:
            try:
                _actioncard_factory.from_soul_proposal(
                    proposal_id=proposal.id,
                    version=proposal.proposed_version,
                    diff_summary=proposal.diff_summary or [],
                )
            except Exception as _ac_exc:
                logger.warning("Failed to create ActionCard for soul proposal: %s", _ac_exc)

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
    identity = resolve_operator_identity(request) or get_api_key_identity(request)
    actor = identity.display_name or identity.operator_id or "operator"
    return _approve_proposal_direct(proposal_id, actor=actor)


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

        previous_version = get_active_version(_SOUL_DIR)

        # Set active pointer
        set_active_version(soul.version, _SOUL_DIR)

        # Refresh the live runtime before reporting success.
        if _runtime_reload_callback is not None:
            try:
                runtime_soul = _load_merged_active_soul()
                _runtime_reload_callback(runtime_soul)
            except Exception as exc:
                set_active_version(previous_version, _SOUL_DIR)
                raise HTTPException(
                    status_code=500,
                    detail=f"Activated Soul version {soul.version} on disk but failed to refresh runtime: {exc}",
                )

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
