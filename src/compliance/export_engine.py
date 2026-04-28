# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Compliance Export Engine — Core Pipeline.

Reads the receipt DAG and produces compliance artifacts in the requested
format.  The engine does not modify the receipt system.  It does not
write new receipts except for the COMPLIANCE_EXPORT_GENERATED receipt
that records the export event itself.

Pipeline stages:
1. Period Resolution — validate start/end, check receipt availability
2. Receipt Fetch — read-only fetch of all receipts in the period
3. Chain Integrity Check — verify parent_id chain is unbroken
4. Identity Resolution — resolve operator display names
5. Format Transform — apply format-specific transformation
6. ip_address Redaction — unconditional removal from all output
7. Output Generation — render the final artifact
8. Export Receipt — write COMPLIANCE_EXPORT_GENERATED receipt
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.shared.receipts import (
    ActionType,
    Receipt,
    ReceiptService,
    ReceiptStatus,
)
from src.compliance.chain_integrity import (
    ChainIntegrityResult,
    check_chain_integrity,
)
from src.compliance.redaction import redact_receipts

logger = logging.getLogger("lancelot.compliance.export_engine")


# ── Export Format Enum ────────────────────────────────────────────────

class ExportFormat:
    """Supported compliance export formats."""
    PDF = "PDF"
    SOC2_JSON = "SOC2_JSON"
    ISO27001_JSON = "ISO27001_JSON"
    GDPR_JSON = "GDPR_JSON"

    ALL = [PDF, SOC2_JSON, ISO27001_JSON, GDPR_JSON]


# ── Export Result ─────────────────────────────────────────────────────

@dataclass
class ExportResult:
    """Result of a compliance export operation."""
    export_id: str
    export_format: str
    period_start: str
    period_end: str
    receipt_count: int
    chain_integrity: ChainIntegrityResult
    output_path: str
    output_sha256: str
    export_duration_ms: float
    generated_at: str
    quest_id: Optional[str] = None
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "export_id": self.export_id,
            "export_format": self.export_format,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "receipt_count": self.receipt_count,
            "chain_integrity": self.chain_integrity.to_dict(),
            "output_path": self.output_path,
            "output_sha256": self.output_sha256,
            "export_duration_ms": self.export_duration_ms,
            "generated_at": self.generated_at,
            "quest_id": self.quest_id,
            "success": self.success,
            "error": self.error,
        }


# ── Period Resolution ─────────────────────────────────────────────────

class PeriodResolutionError(Exception):
    """Raised when the requested export period is invalid."""
    pass


def resolve_period(
    receipt_service: ReceiptService,
    period_start: str,
    period_end: str,
) -> int:
    """Validate the export period and return the receipt count.

    Raises PeriodResolutionError if:
    - Start is after end
    - No receipts exist in the period

    Returns:
        Number of receipts in the period.
    """
    if period_start >= period_end:
        raise PeriodResolutionError(
            f"Period start ({period_start}) must be before end ({period_end})"
        )

    receipts = receipt_service.list(
        since=period_start, until=period_end, limit=1
    )
    if not receipts:
        raise PeriodResolutionError(
            f"No receipts found in period {period_start} to {period_end}"
        )

    return receipt_service.count(since=period_start, until=period_end)


# ── Receipt Fetch ─────────────────────────────────────────────────────

def fetch_receipts(
    receipt_service: ReceiptService,
    period_start: str,
    period_end: str,
    quest_id: Optional[str] = None,
) -> List[Receipt]:
    """Fetch all receipts for the export period.  Read-only.

    Returns receipts ordered by timestamp ascending (chronological).
    """
    return receipt_service.list_chronological(
        since=period_start,
        until=period_end,
        quest_id=quest_id,
    )


# ── Export Storage ────────────────────────────────────────────────────

def _ensure_export_dir(data_dir: str) -> Path:
    """Ensure the compliance exports directory exists."""
    export_dir = Path(data_dir) / "compliance_exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    return export_dir


def _export_filename(
    export_format: str,
    period_start: str,
    period_end: str,
    export_id: str,
) -> str:
    """Generate self-describing filename for an export artifact.

    Format: {format}_{period_start}_{period_end}_{export_id}.{ext}
    """
    # Sanitize timestamps for filenames
    start_safe = period_start[:10]  # YYYY-MM-DD
    end_safe = period_end[:10]
    short_id = export_id[:8]

    ext = "json"
    if export_format == ExportFormat.PDF:
        ext = "pdf"
    elif export_format in {
        ExportFormat.SOC2_JSON,
        ExportFormat.ISO27001_JSON,
        ExportFormat.GDPR_JSON,
    }:
        ext = "zip"

    fmt_key = export_format.lower()
    return f"{fmt_key}_{start_safe}_{end_safe}_{short_id}.{ext}"


# ── Export Receipt Writer ─────────────────────────────────────────────

def write_export_receipt(
    receipt_service: ReceiptService,
    result: ExportResult,
    operator_id: str,
    session_id: str = "",
) -> Receipt:
    """Write a COMPLIANCE_EXPORT_GENERATED receipt for the export.

    This receipt is itself part of the audit trail — it records WHO
    generated the compliance artifact, WHEN, for WHAT period, and the
    SHA-256 hash of the output for delivery integrity verification.

    Requires OperatorIdentity (enforced by receipt writer).
    """
    receipt = Receipt(
        action_type=ActionType.COMPLIANCE_EXPORT_GENERATED.value,
        action_name="compliance_export",
        inputs={
            "export_format": result.export_format,
            "period_start": result.period_start,
            "period_end": result.period_end,
            "quest_id": result.quest_id,
        },
        outputs={
            "export_id": result.export_id,
            "receipt_count_exported": result.receipt_count,
            "chain_integrity": result.chain_integrity.status,
            "output_sha256": result.output_sha256,
            "export_duration_ms": result.export_duration_ms,
            "output_path": result.output_path,
        },
        status=ReceiptStatus.SUCCESS.value,
        duration_ms=int(result.export_duration_ms),
        operator_id=operator_id,
        session_id=session_id,
        metadata={"compliance_export": True},
    )
    return receipt_service.create(receipt)


# ── Core Export Pipeline ──────────────────────────────────────────────

def run_export(
    receipt_service: ReceiptService,
    export_format: str,
    period_start: str,
    period_end: str,
    data_dir: str,
    operator_id: str,
    operator_display_name: str = "",
    session_id: str = "",
    quest_id: Optional[str] = None,
    anomaly_threshold: int = 5,
) -> ExportResult:
    """Run the full compliance export pipeline.

    Args:
        receipt_service: Receipt store to read from.
        export_format: One of ExportFormat constants.
        period_start: ISO 8601 start of export period.
        period_end: ISO 8601 end of export period.
        data_dir: Base data directory for output storage.
        operator_id: Operator UUID of the exporting human.
        session_id: War Room session UUID.
        quest_id: Optional quest scope.
        anomaly_threshold: Blocked actions per 24h to flag as anomaly.

    Returns:
        ExportResult with output path and metadata.
    """
    start_time = time.time()
    export_id = str(uuid.uuid4())
    generated_at = datetime.now(timezone.utc).isoformat()

    # Stage 1: Period Resolution
    try:
        receipt_count = resolve_period(receipt_service, period_start, period_end)
    except PeriodResolutionError as exc:
        duration_ms = (time.time() - start_time) * 1000
        return ExportResult(
            export_id=export_id,
            export_format=export_format,
            period_start=period_start,
            period_end=period_end,
            receipt_count=0,
            chain_integrity=ChainIntegrityResult(
                status="CHAIN_ANOMALY",
                period_start=period_start,
                period_end=period_end,
                total_receipts=0,
                receipts_with_parents=0,
                orphaned_count=0,
            ),
            output_path="",
            output_sha256="",
            export_duration_ms=duration_ms,
            generated_at=generated_at,
            quest_id=quest_id,
            error=str(exc),
        )

    # Stage 2: Receipt Fetch
    receipts = fetch_receipts(
        receipt_service, period_start, period_end, quest_id
    )
    logger.info("Fetched %d receipts for export period", len(receipts))

    # Stage 3: Chain Integrity Check
    chain_result = check_chain_integrity(
        receipt_service, period_start, period_end, quest_id
    )

    # Stage 4: Identity Resolution
    # Receipt-level operator attribution comes from receipt metadata.
    # Export-level generator identity is passed in explicitly here.

    # Stage 5: Format Transform
    if export_format == ExportFormat.SOC2_JSON:
        from src.compliance.soc2_mapper import transform_soc2
        output_data = transform_soc2(
            receipts, chain_result, period_start, period_end,
            operator_id, generated_at, export_id, operator_display_name,
        )
    elif export_format == ExportFormat.ISO27001_JSON:
        from src.compliance.iso27001_mapper import transform_iso27001
        output_data = transform_iso27001(
            receipts, chain_result, period_start, period_end,
            operator_id, generated_at, export_id, operator_display_name,
        )
    elif export_format == ExportFormat.GDPR_JSON:
        from src.compliance.gdpr_mapper import transform_gdpr
        output_data = transform_gdpr(
            receipts, chain_result, period_start, period_end,
            operator_id, generated_at, export_id, operator_display_name,
        )
    elif export_format == ExportFormat.PDF:
        from src.compliance.pdf_export import generate_forensic_timeline_pdf
        pdf_bytes = generate_forensic_timeline_pdf(
            receipts, chain_result, period_start, period_end,
            operator_id, generated_at, export_id, anomaly_threshold,
        )
        output_data = None  # PDF uses raw bytes, not JSON
    else:
        output_data = {"error": f"Unknown format: {export_format}"}

    # Stage 6: ip_address Redaction (applied during format transform
    # via redact_receipts — all format transformers use redacted data)

    # Stage 7: Output Generation
    export_dir = _ensure_export_dir(data_dir)
    filename = _export_filename(
        export_format, period_start, period_end, export_id
    )
    output_path = str(export_dir / filename)

    if export_format == ExportFormat.PDF:
        output_bytes = pdf_bytes  # type: ignore[possibly-undefined]
    else:
        from src.compliance.audit_bundle import build_audit_bundle

        artifact_basename = (
            f"{export_format.lower()}_{period_start[:10]}_{period_end[:10]}_{export_id[:8]}"
        )
        output_bytes, _manifest = build_audit_bundle(
            export_format,
            output_data,
            artifact_basename=artifact_basename,
        )

    # Write to disk
    with open(output_path, "wb") as f:
        f.write(output_bytes)

    # Compute SHA-256 of the output artifact
    output_sha256 = hashlib.sha256(output_bytes).hexdigest()

    duration_ms = (time.time() - start_time) * 1000

    result = ExportResult(
        export_id=export_id,
        export_format=export_format,
        period_start=period_start,
        period_end=period_end,
        receipt_count=len(receipts),
        chain_integrity=chain_result,
        output_path=output_path,
        output_sha256=output_sha256,
        export_duration_ms=round(duration_ms, 2),
        generated_at=generated_at,
        quest_id=quest_id,
    )

    # Stage 8: Export Receipt
    try:
        write_export_receipt(
            receipt_service, result, operator_id, session_id
        )
    except Exception as exc:
        logger.error("Failed to write export receipt: %s", exc)
        # Export succeeded even if the receipt fails — the artifact
        # is on disk.  Log the error but don't fail the export.

    logger.info(
        "Compliance export complete: format=%s, receipts=%d, "
        "chain=%s, duration=%.1fms, sha256=%s",
        export_format, len(receipts), chain_result.status,
        duration_ms, output_sha256[:16],
    )

    return result
