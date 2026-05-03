# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Playbook REST API — list, detail, and reload playbooks.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from src.core.api_auth import require_authenticated_request
from src.core.auth_api import require_operator_capability

from src.incidents.playbooks import (
    get_playbook,
    list_playbook_metadata,
    load_playbooks,
    invalidate_cache,
)

logger = logging.getLogger("lancelot.incidents.playbook_api")

router = APIRouter(
    prefix="/api/playbooks",
    tags=["playbooks"],
    dependencies=[Depends(require_authenticated_request)],
)

_playbooks_dir: Optional[str] = None


def init_playbook_api(playbooks_dir: str) -> None:
    """Initialize the playbook API with the playbooks directory."""
    global _playbooks_dir
    _playbooks_dir = playbooks_dir
    load_playbooks(playbooks_dir)
    logger.info("Playbook API initialized: %s", playbooks_dir)


def shutdown_playbook_api() -> None:
    """Clear playbook API runtime references for hot-toggle shutdown."""
    global _playbooks_dir
    _playbooks_dir = None
    invalidate_cache()
    logger.info("Playbook API shutdown complete")


@router.get("")
def list_playbooks(
    category: Optional[str] = Query(None),
    industry: Optional[str] = Query(None),
):
    """List all available playbooks with metadata."""
    return {"playbooks": list_playbook_metadata(category=category, industry=industry)}


@router.get("/{name}")
def get_playbook_detail(name: str):
    """Get full playbook detail including steps."""
    pb = get_playbook(name)
    if pb is None:
        raise HTTPException(status_code=404, detail=f"Playbook not found: {name}")

    return {
        "name": pb.metadata.name,
        "display_name": pb.metadata.display_name,
        "description": pb.metadata.description,
        "category": pb.metadata.category,
        "severity_default": pb.metadata.severity_default,
        "industry": pb.metadata.industry,
        "version": pb.metadata.version,
        "tags": pb.metadata.tags,
        "extends": pb.extends,
        "trigger": pb.trigger,
        "paging": pb.paging,
        "steps": [
            {
                "step": s.step,
                "title": s.title,
                "description": s.description,
                "action_type": s.action_type,
                "sla_minutes": s.sla_minutes,
                "decision_points": s.decision_points,
                "actions": s.actions,
            }
            for s in pb.steps
        ],
    }


@router.post("/reload")
def reload_playbooks(
    request: Request,
    _authz: None = Depends(require_operator_capability("incidents.admin")),
):
    """Invalidate cache and reload all playbooks from disk."""
    invalidate_cache()
    playbooks = load_playbooks(_playbooks_dir)
    try:
        from src.core.governance_receipts import emit_governance_receipt
        from src.shared.receipts import ActionType

        emit_governance_receipt(
            request,
            ActionType.PLAYBOOK_UPDATED,
            action_name="reload_playbooks",
            inputs={"playbooks_dir": _playbooks_dir or "", "reload_action": "api"},
            outputs={"playbook_count": len(playbooks)},
            metadata={"subsystem": "incident_response"},
        )
    except Exception as exc:
        logger.warning("Failed to emit playbook reload receipt: %s", exc)
    return {"status": "reloaded", "count": len(playbooks)}
