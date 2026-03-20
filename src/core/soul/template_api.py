# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Soul Template API — REST endpoints for browsing and applying Soul templates.

Endpoints:
    GET  /soul/templates                — list available templates
    GET  /soul/templates/{name}         — get template details + full YAML
    POST /soul/templates/{name}/apply   — apply template as a Soul Amendment Proposal
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from src.core.soul.templates import (
    get_template,
    list_template_metadata,
    apply_template,
    invalidate_cache,
)
from src.core.soul.store import SoulStoreError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/soul/templates", tags=["soul-templates"])

_TEMPLATES_DIR: Optional[str] = os.environ.get("TEMPLATES_DIR", None)
_SOUL_DIR: Optional[str] = os.environ.get("SOUL_DIR", None)


def _set_templates_dir(d: str) -> None:
    """Set templates directory (used in tests)."""
    global _TEMPLATES_DIR
    _TEMPLATES_DIR = d


def _set_soul_dir(d: str) -> None:
    """Set soul directory (used in tests)."""
    global _SOUL_DIR
    _SOUL_DIR = d


# ---------------------------------------------------------------------------
# GET /soul/templates — list templates
# ---------------------------------------------------------------------------

@router.get("")
async def list_templates(industry: Optional[str] = None):
    """List available Soul templates with metadata."""
    try:
        templates = list_template_metadata(_TEMPLATES_DIR, industry=industry)
        return {"templates": templates, "count": len(templates)}
    except Exception as exc:
        logger.error("list_templates error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# GET /soul/templates/{name} — get template details
# ---------------------------------------------------------------------------

@router.get("/{name}")
async def get_template_detail(name: str):
    """Get full template details including YAML content."""
    template = get_template(name, _TEMPLATES_DIR)
    if template is None:
        raise HTTPException(status_code=404, detail=f"Template not found: {name}")
    return template.to_dict()


# ---------------------------------------------------------------------------
# POST /soul/templates/{name}/apply — apply template
# ---------------------------------------------------------------------------

@router.post("/{name}/apply")
async def apply_template_endpoint(name: str, request: Request):
    """Apply a Soul template as a new Soul Amendment Proposal.

    Body (optional): {"customizations": {...}, "operator_id": "...", "session_id": "..."}

    The template creates a proposal with author="template:{name}".
    The proposal must then be approved and activated via the standard
    Soul Amendment workflow (/soul/proposals/{id}/approve → activate).
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    customizations = body.get("customizations")
    operator_id = body.get("operator_id")
    session_id = body.get("session_id")

    if not operator_id:
        raise HTTPException(
            status_code=400,
            detail="operator_id is required to apply a template",
        )

    try:
        result = apply_template(
            template_name=name,
            customizations=customizations,
            operator_id=operator_id,
            session_id=session_id,
            soul_dir=_SOUL_DIR,
            templates_dir=_TEMPLATES_DIR,
        )

        # Emit SOUL_TEMPLATE_APPLIED receipt
        _emit_template_receipt(result, operator_id, session_id)

        return result
    except SoulStoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.error("apply_template error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# POST /soul/templates/reload — invalidate cache
# ---------------------------------------------------------------------------

@router.post("/reload")
async def reload_templates():
    """Invalidate template cache, forcing reload on next list/get."""
    invalidate_cache()
    return {"status": "cache_invalidated"}


# ---------------------------------------------------------------------------
# Receipt emission
# ---------------------------------------------------------------------------

def _emit_template_receipt(
    result: dict,
    operator_id: str,
    session_id: Optional[str],
) -> None:
    """Emit a SOUL_TEMPLATE_APPLIED receipt for the template application."""
    try:
        from src.shared.receipts import (
            ActionType,
            ReceiptStatus,
            Receipt,
            get_receipt_service,
        )

        receipt = Receipt(
            action_type=ActionType.SOUL_TEMPLATE_APPLIED.value,
            action_name=f"template_applied:{result['template_name']}",
            inputs={
                "template_name": result["template_name"],
                "template_version": result["template_version"],
                "fields_customized": result.get("fields_customized", []),
            },
            outputs={
                "proposal_id": result["proposal_id"],
                "proposed_version": result["proposed_version"],
                "diff_summary": result.get("diff_summary", []),
            },
            status=ReceiptStatus.SUCCESS.value,
            operator_id=operator_id,
            session_id=session_id or "",
            metadata={
                "template_name": result["template_name"],
                "template_version": result["template_version"],
            },
        )

        service = get_receipt_service()
        service.create(receipt)
    except Exception as exc:
        logger.error("Failed to emit SOUL_TEMPLATE_APPLIED receipt: %s", exc)
