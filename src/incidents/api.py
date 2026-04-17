# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Incident REST API — endpoints for incident lifecycle management.

All mutation endpoints require operator session and generate receipts.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from src.core.api_auth import require_authenticated_request
from src.core.auth_api import require_operator_capability, resolve_authenticated_identity

from src.incidents.models import (
    IncidentRecord,
    IncidentStatus,
    IncidentSeverity,
    TimelineEntry,
)
from src.incidents.store import get_incident_store
from src.shared.receipts import (
    ActionType,
    CognitionTier,
    create_receipt,
    get_receipt_service,
)

logger = logging.getLogger("lancelot.incidents.api")

router = APIRouter(
    prefix="/api/incidents",
    tags=["incidents"],
    dependencies=[
        Depends(require_authenticated_request),
        Depends(require_operator_capability("incidents.admin")),
    ],
)

_data_dir: Optional[str] = None
_receipt_service = None


def init_incidents_api(receipt_service, data_dir: str) -> None:
    """Initialize the incidents API."""
    global _data_dir, _receipt_service
    _data_dir = data_dir
    _receipt_service = receipt_service
    # Ensure store is initialized
    get_incident_store(data_dir)
    logger.info("Incidents API initialized")


# ── Request Models ────────────────────────────────────────────────────

class AcknowledgeRequest(BaseModel):
    pass


class StatusUpdateRequest(BaseModel):
    status: str
    note: str = ""


class TimelineEntryRequest(BaseModel):
    entry_text: str


class LinkReceiptRequest(BaseModel):
    receipt_id: str


class EscalateRequest(BaseModel):
    new_severity: str
    reason: str


class CloseRequest(BaseModel):
    root_cause: Optional[str] = None
    false_positive: bool = False
    false_positive_reason: Optional[str] = None
    generate_report: bool = False


# ── Endpoints ─────────────────────────────────────────────────────────

@router.get("")
def list_incidents(
    status: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List incidents with optional filters."""
    store = get_incident_store()
    if store is None:
        raise HTTPException(status_code=503, detail="Incident store not initialized")

    incidents = store.list_incidents(
        status=status, category=category, severity=severity,
        limit=limit, offset=offset,
    )
    return {"incidents": incidents, "count": len(incidents)}


@router.get("/stats")
def incident_stats():
    """Aggregate stats: open count by severity, total counts."""
    store = get_incident_store()
    if store is None:
        raise HTTPException(status_code=503, detail="Incident store not initialized")

    open_counts = store.count_open()
    all_incidents = store.list_incidents(limit=10000)
    total = len(all_incidents)
    closed = len([i for i in all_incidents if i.get("status") in (
        IncidentStatus.CLOSED.value, IncidentStatus.FALSE_POSITIVE.value,
    )])

    return {
        "total": total,
        "open": total - closed,
        "closed": closed,
        "by_severity": open_counts,
    }


@router.get("/{incident_id}")
def get_incident(incident_id: str):
    """Get full incident detail with timeline."""
    store = get_incident_store()
    if store is None:
        raise HTTPException(status_code=503, detail="Incident store not initialized")

    incident = store.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    return incident.to_dict()


@router.post("/{incident_id}/acknowledge")
def acknowledge_incident(incident_id: str, req: AcknowledgeRequest, request: Request):
    """Acknowledge an incident. Sets status to INVESTIGATING."""
    store = get_incident_store()
    incident = _get_or_404(store, incident_id)

    identity = resolve_authenticated_identity(request)
    operator_id = identity.operator_id
    actor = identity.display_name or identity.operator_id
    now = datetime.now(timezone.utc).isoformat()
    incident.status = IncidentStatus.INVESTIGATING.value
    incident.responder_id = operator_id
    incident.acknowledged_at = now
    incident.add_timeline_entry(TimelineEntry(
        timestamp=now,
        entry_type="acknowledged",
        actor=actor,
        detail="Incident acknowledged",
    ))
    store.update(incident)

    _emit_receipt(ActionType.INCIDENT_ACKNOWLEDGED, {
        "incident_id": incident_id,
        "responder_id": operator_id,
        "acknowledged_at": now,
    }, operator_id=operator_id)

    return {"status": "acknowledged", "incident_id": incident_id}


@router.post("/{incident_id}/status")
def update_status(incident_id: str, req: StatusUpdateRequest, request: Request):
    """Update incident status."""
    store = get_incident_store()
    incident = _get_or_404(store, incident_id)

    try:
        new_status = IncidentStatus(req.status)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status: {req.status}. Valid: {[s.value for s in IncidentStatus]}",
        )

    identity = resolve_authenticated_identity(request)
    operator_id = identity.operator_id
    actor = identity.display_name or identity.operator_id
    previous_status = incident.status
    now = datetime.now(timezone.utc).isoformat()
    incident.status = new_status.value
    incident.add_timeline_entry(TimelineEntry(
        timestamp=now,
        entry_type="status_change",
        actor=actor,
        detail=f"Status changed: {previous_status} → {new_status.value}. {req.note}",
    ))
    store.update(incident)

    _emit_receipt(ActionType.INCIDENT_STATUS_UPDATED, {
        "incident_id": incident_id,
        "previous_status": previous_status,
        "new_status": new_status.value,
        "note": req.note,
    }, operator_id=operator_id)

    return {"status": "updated", "previous": previous_status, "new": new_status.value}


@router.post("/{incident_id}/timeline")
def add_timeline_entry(incident_id: str, req: TimelineEntryRequest, request: Request):
    """Add a timeline entry to an incident."""
    store = get_incident_store()
    incident = _get_or_404(store, incident_id)

    identity = resolve_authenticated_identity(request)
    operator_id = identity.operator_id
    actor = identity.display_name or identity.operator_id
    now = datetime.now(timezone.utc).isoformat()
    incident.add_timeline_entry(TimelineEntry(
        timestamp=now,
        entry_type="note",
        actor=actor,
        detail=req.entry_text,
    ))
    store.update(incident)

    _emit_receipt(ActionType.INCIDENT_TIMELINE_ENTRY, {
        "incident_id": incident_id,
        "entry_text": req.entry_text,
    }, operator_id=operator_id)

    return {"status": "added", "incident_id": incident_id}


@router.post("/{incident_id}/link-receipt")
def link_receipt(incident_id: str, req: LinkReceiptRequest, request: Request):
    """Link a remediation receipt to an incident."""
    store = get_incident_store()
    incident = _get_or_404(store, incident_id)

    identity = resolve_authenticated_identity(request)
    operator_id = identity.operator_id
    actor = identity.display_name or identity.operator_id
    if req.receipt_id not in incident.remediation_receipts:
        incident.remediation_receipts.append(req.receipt_id)
    now = datetime.now(timezone.utc).isoformat()
    incident.add_timeline_entry(TimelineEntry(
        timestamp=now,
        entry_type="remediation_linked",
        actor=actor,
        detail=f"Linked remediation receipt: {req.receipt_id}",
        receipt_id=req.receipt_id,
    ))
    store.update(incident)

    _emit_receipt(ActionType.INCIDENT_REMEDIATION_LINKED, {
        "incident_id": incident_id,
        "linked_receipt_id": req.receipt_id,
    }, operator_id=operator_id)

    return {"status": "linked", "receipt_id": req.receipt_id}


@router.post("/{incident_id}/escalate")
def escalate_incident(incident_id: str, req: EscalateRequest, request: Request):
    """Escalate incident severity."""
    store = get_incident_store()
    incident = _get_or_404(store, incident_id)

    try:
        new_severity = IncidentSeverity(req.new_severity)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid severity: {req.new_severity}",
        )

    identity = resolve_authenticated_identity(request)
    operator_id = identity.operator_id
    actor = identity.display_name or identity.operator_id
    previous_severity = incident.severity
    now = datetime.now(timezone.utc).isoformat()
    incident.severity = new_severity.value
    incident.add_timeline_entry(TimelineEntry(
        timestamp=now,
        entry_type="escalation",
        actor=actor,
        detail=f"Escalated: {previous_severity} → {new_severity.value}. Reason: {req.reason}",
    ))
    store.update(incident)

    _emit_receipt(ActionType.INCIDENT_ESCALATED, {
        "incident_id": incident_id,
        "previous_severity": previous_severity,
        "new_severity": new_severity.value,
        "escalation_reason": req.reason,
    }, operator_id=operator_id)

    return {"status": "escalated", "previous": previous_severity, "new": new_severity.value}


@router.post("/{incident_id}/close")
def close_incident(incident_id: str, req: CloseRequest, request: Request):
    """Close an incident or mark as false positive."""
    store = get_incident_store()
    incident = _get_or_404(store, incident_id)

    # Validate: HIGH/CRITICAL require root_cause
    if not req.false_positive:
        if incident.severity in ("HIGH", "CRITICAL") and not req.root_cause:
            raise HTTPException(
                status_code=400,
                detail=f"Root cause is required for {incident.severity} incidents",
            )

    identity = resolve_authenticated_identity(request)
    operator_id = identity.operator_id
    actor = identity.display_name or identity.operator_id
    now = datetime.now(timezone.utc).isoformat()

    if req.false_positive:
        incident.status = IncidentStatus.FALSE_POSITIVE.value
        incident.add_timeline_entry(TimelineEntry(
            timestamp=now,
            entry_type="closed_false_positive",
            actor=actor,
            detail=f"Closed as false positive: {req.false_positive_reason or 'No reason provided'}",
        ))
        _emit_receipt(ActionType.INCIDENT_FALSE_POSITIVE, {
            "incident_id": incident_id,
            "false_positive_reason": req.false_positive_reason,
            "playbook_adjustment_recommended": False,
        }, operator_id=operator_id)
    else:
        incident.status = IncidentStatus.CLOSED.value
        incident.root_cause = req.root_cause
        incident.add_timeline_entry(TimelineEntry(
            timestamp=now,
            entry_type="closed",
            actor=actor,
            detail=f"Incident closed. Root cause: {req.root_cause}",
        ))
        _emit_receipt(ActionType.INCIDENT_CLOSED, {
            "incident_id": incident_id,
            "root_cause": req.root_cause,
            "board_report_generated": req.generate_report,
        }, operator_id=operator_id)

    incident.closed_at = now
    incident.closed_by = operator_id

    # Generate report if requested
    if req.generate_report:
        try:
            from src.incidents.report_generator import generate_incident_report
            reports_dir = os.path.join(_data_dir, "incident_reports") if _data_dir else None
            generate_incident_report(incident, output_dir=reports_dir)
            incident.board_report_generated = True
        except Exception as exc:
            logger.error("Report generation failed: %s", exc)

    store.update(incident)

    return {
        "status": "closed" if not req.false_positive else "false_positive",
        "incident_id": incident_id,
        "board_report_generated": incident.board_report_generated,
    }


@router.post("/{incident_id}/report")
def generate_report(incident_id: str):
    """Generate a board report PDF for a closed incident."""
    store = get_incident_store()
    incident = _get_or_404(store, incident_id)

    try:
        from src.incidents.report_generator import generate_incident_report
        reports_dir = os.path.join(_data_dir, "incident_reports") if _data_dir else None
        pdf_bytes = generate_incident_report(incident, output_dir=reports_dir)
        incident.board_report_generated = True
        store.update(incident)
        return {"status": "generated", "size_bytes": len(pdf_bytes)}
    except Exception as exc:
        logger.error("Report generation failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{incident_id}/report/download")
def download_report(incident_id: str):
    """Download a generated incident report PDF."""
    if not _data_dir:
        raise HTTPException(status_code=503, detail="Data dir not configured")

    path = os.path.join(_data_dir, "incident_reports", f"{incident_id}.pdf")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Report not generated yet")

    def stream():
        with open(path, "rb") as f:
            yield f.read()

    return StreamingResponse(
        stream(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="incident-{incident_id[:8]}.pdf"'
        },
    )


# ── Helpers ───────────────────────────────────────────────────────────

def _get_or_404(store, incident_id: str) -> IncidentRecord:
    """Get an incident or raise 404."""
    if store is None:
        raise HTTPException(status_code=503, detail="Incident store not initialized")
    incident = store.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


def _emit_receipt(
    action_type: ActionType,
    metadata: Dict[str, Any],
    operator_id: Optional[str] = None,
) -> None:
    """Emit a receipt for an incident action."""
    try:
        receipt = create_receipt(
            action_type,
            f"incident_api:{action_type.value}",
            metadata,
            tier=CognitionTier.DETERMINISTIC,
        )
        if operator_id:
            receipt.operator_id = operator_id

        svc = _receipt_service or get_receipt_service()
        if svc:
            svc.create(receipt)
    except Exception as exc:
        logger.debug("Failed to emit receipt %s: %s", action_type.value, exc)
    identity = resolve_authenticated_identity(request)
    operator_id = identity.operator_id
    actor = identity.display_name or identity.operator_id
