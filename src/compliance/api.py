# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Compliance Export API — /api/compliance/*

War Room endpoints for one-click compliance report generation.
Provides export configuration, generation, download, and history.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from src.core.api_auth import require_authenticated_request
from src.core.auth_api import require_operator_capability

from src.shared.receipts import ReceiptService

logger = logging.getLogger("lancelot.compliance.api")

router = APIRouter(
    prefix="/api/compliance",
    tags=["compliance"],
    dependencies=[
        Depends(require_authenticated_request),
        Depends(require_operator_capability("compliance.admin")),
    ],
)

# Module-level references, set during app startup
_receipt_service: Optional[ReceiptService] = None
_data_dir: str = "/home/lancelot/data"


def init_compliance_api(receipt_service: ReceiptService, data_dir: str) -> None:
    """Initialize API with receipt service and data directory."""
    global _receipt_service, _data_dir
    _receipt_service = receipt_service
    _data_dir = data_dir
    logger.info("Compliance Export API initialised (data_dir=%s)", data_dir)


# ── Request / Response Models ─────────────────────────────────────────

class ExportRequest(BaseModel):
    format: str = Field(
        ...,
        description="Export format: PDF, SOC2_JSON, ISO27001_JSON, GDPR_JSON",
    )
    period_start: str = Field(..., description="ISO 8601 start of export period")
    period_end: str = Field(..., description="ISO 8601 end of export period")
    quest_id: Optional[str] = Field(
        None, description="Optional quest scope"
    )
    anomaly_threshold: int = Field(
        5, description="Blocked actions per 24h to flag as anomaly"
    )


class ExportResponse(BaseModel):
    export_id: str
    export_format: str
    period_start: str
    period_end: str
    receipt_count: int
    chain_integrity: str
    output_sha256: str
    export_duration_ms: float
    generated_at: str
    success: bool
    error: Optional[str] = None
    download_url: str = ""


class ExportHistoryEntry(BaseModel):
    export_id: str
    export_format: str
    period_start: str
    period_end: str
    receipt_count: int
    chain_integrity: str
    output_sha256: str
    export_duration_ms: float
    generated_at: str
    operator_id: Optional[str] = None
    filename: str = ""


class ChainIntegrityResponse(BaseModel):
    status: str
    period_start: str
    period_end: str
    total_receipts: int
    receipts_with_parents: int
    orphaned_count: int
    is_intact: bool


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/export", response_model=ExportResponse)
async def generate_export(body: ExportRequest, request: Request):
    """Generate a compliance export.  Requires authenticated session."""
    if _receipt_service is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Receipt service not initialised"},
        )

    # Resolve operator identity
    from src.core.auth_api import resolve_operator_identity, get_api_key_identity
    identity = resolve_operator_identity(request)
    if identity is None:
        identity = get_api_key_identity(request)

    # Validate format
    from src.compliance.export_engine import ExportFormat
    if body.format not in ExportFormat.ALL:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid format '{body.format}'. Options: {ExportFormat.ALL}",
        )

    # Run export pipeline
    from src.compliance.export_engine import run_export
    result = run_export(
        receipt_service=_receipt_service,
        export_format=body.format,
        period_start=body.period_start,
        period_end=body.period_end,
        data_dir=_data_dir,
        operator_id=identity.operator_id,
        operator_display_name=identity.display_name,
        session_id=identity.session_id,
        quest_id=body.quest_id,
        anomaly_threshold=body.anomaly_threshold,
    )

    download_url = (
        f"/api/compliance/download/{result.export_id}"
        if result.success else ""
    )

    return ExportResponse(
        export_id=result.export_id,
        export_format=result.export_format,
        period_start=result.period_start,
        period_end=result.period_end,
        receipt_count=result.receipt_count,
        chain_integrity=result.chain_integrity.status,
        output_sha256=result.output_sha256,
        export_duration_ms=result.export_duration_ms,
        generated_at=result.generated_at,
        success=result.success,
        error=result.error,
        download_url=download_url,
    )


@router.get("/download/{export_id}")
async def download_export(export_id: str):
    """Download a previously generated compliance export."""
    export_dir = Path(_data_dir) / "compliance_exports"
    if not export_dir.exists():
        raise HTTPException(status_code=404, detail="No exports found")

    # Find export file by export_id (short ID is in the filename)
    short_id = export_id[:8]
    matches = list(export_dir.glob(f"*_{short_id}.*"))
    if not matches:
        raise HTTPException(
            status_code=404,
            detail=f"Export {export_id} not found",
        )

    filepath = matches[0]
    media_type = (
        "application/pdf" if filepath.suffix == ".pdf"
        else "application/json"
    )

    return FileResponse(
        path=str(filepath),
        media_type=media_type,
        filename=filepath.name,
    )


@router.get("/chain-integrity", response_model=ChainIntegrityResponse)
async def check_chain(
    period_start: str = Query(..., description="ISO 8601 start"),
    period_end: str = Query(..., description="ISO 8601 end"),
    quest_id: Optional[str] = Query(None),
):
    """Run a chain integrity check without generating an export."""
    if _receipt_service is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Receipt service not initialised"},
        )

    from src.compliance.chain_integrity import check_chain_integrity
    result = check_chain_integrity(
        _receipt_service, period_start, period_end, quest_id
    )

    return ChainIntegrityResponse(
        status=result.status,
        period_start=result.period_start,
        period_end=result.period_end,
        total_receipts=result.total_receipts,
        receipts_with_parents=result.receipts_with_parents,
        orphaned_count=result.orphaned_count,
        is_intact=result.is_intact,
    )


@router.get("/history")
async def export_history():
    """List all previous compliance exports with metadata."""
    if _receipt_service is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Receipt service not initialised"},
        )

    from src.shared.receipts import ActionType
    export_receipts = _receipt_service.list(
        action_type=ActionType.COMPLIANCE_EXPORT_GENERATED.value,
        limit=100,
    )

    entries = []
    for r in export_receipts:
        outputs = r.outputs or {}
        inputs = r.inputs or {}
        entries.append(ExportHistoryEntry(
            export_id=outputs.get("export_id", r.id),
            export_format=inputs.get("export_format", ""),
            period_start=inputs.get("period_start", ""),
            period_end=inputs.get("period_end", ""),
            receipt_count=outputs.get("receipt_count_exported", 0),
            chain_integrity=outputs.get("chain_integrity", ""),
            output_sha256=outputs.get("output_sha256", ""),
            export_duration_ms=outputs.get("export_duration_ms", 0),
            generated_at=r.timestamp,
            operator_id=r.operator_id,
            filename=Path(outputs.get("output_path", "")).name,
        ).dict())

    return {"exports": entries, "total": len(entries)}


@router.post("/verify/{export_id}")
async def verify_export(export_id: str):
    """Re-download and verify an export's SHA-256 hash matches the stored hash."""
    if _receipt_service is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Receipt service not initialised"},
        )

    # Find the export file
    export_dir = Path(_data_dir) / "compliance_exports"
    short_id = export_id[:8]
    matches = list(export_dir.glob(f"*_{short_id}.*"))
    if not matches:
        raise HTTPException(
            status_code=404,
            detail=f"Export {export_id} not found on disk",
        )

    filepath = matches[0]

    # Compute current hash
    with open(filepath, "rb") as f:
        current_hash = hashlib.sha256(f.read()).hexdigest()

    # Find the original export receipt
    from src.shared.receipts import ActionType
    export_receipts = _receipt_service.list(
        action_type=ActionType.COMPLIANCE_EXPORT_GENERATED.value,
        limit=200,
    )

    original_hash = None
    for r in export_receipts:
        if r.outputs and r.outputs.get("export_id", "").startswith(short_id):
            original_hash = r.outputs.get("output_sha256", "")
            break

    if original_hash is None:
        return {
            "export_id": export_id,
            "verified": False,
            "reason": "No export receipt found for this export_id",
            "current_sha256": current_hash,
        }

    match = current_hash == original_hash

    return {
        "export_id": export_id,
        "verified": match,
        "current_sha256": current_hash,
        "original_sha256": original_hash,
        "mismatch": not match,
    }


@router.get("/formats")
async def list_formats():
    """List available export formats."""
    from src.compliance.export_engine import ExportFormat
    return {
        "formats": [
            {
                "id": ExportFormat.PDF,
                "name": "Forensic Timeline PDF",
                "description": "Human-readable PDF for board presentation, legal review, and regulatory submission.",
                "available": True,
            },
            {
                "id": ExportFormat.SOC2_JSON,
                "name": "SOC 2 Type II JSON",
                "description": "Structured JSON mapped to SOC 2 Trust Services Criteria. Machine-readable for GRC platforms.",
                "available": True,
            },
            {
                "id": ExportFormat.ISO27001_JSON,
                "name": "ISO 27001:2022 JSON",
                "description": "Structured JSON mapped to ISO 27001:2022 Annex A controls.",
                "available": True,
            },
            {
                "id": ExportFormat.GDPR_JSON,
                "name": "GDPR Article 30 Processing Record",
                "description": "Article 30 records of processing activities for GDPR compliance.",
                "available": True,
            },
        ]
    }
