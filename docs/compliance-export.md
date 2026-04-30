# Compliance Export

One-click compliance report generation from Lancelot's receipt DAG. The goal is not just to dump receipts. The goal is to produce auditor-grade evidence packages that preserve traceability back to the receipt chain.

---

## Export Formats

| Format | Description | Status |
|--------|-------------|--------|
| **Forensic Timeline PDF** | Human-readable package for board presentation, legal review, and regulator or auditor walkthroughs. Includes executive summary, governance controls, authorization log, full event log, anomaly report, and schema appendix. | Available |
| **SOC 2 Type II JSON Bundle** | ZIP bundle containing the auditor-grade SOC 2 evidence JSON, a summary PDF, a control index CSV, a README, and a manifest with per-file hashes. | Available |
| **ISO 27001:2022 JSON Bundle** | ZIP bundle containing the ISO evidence JSON, a summary PDF, a control index CSV, a README, and a manifest with per-file hashes. | Available |
| **GDPR Article 30 JSON Bundle** | ZIP bundle containing the GDPR evidence JSON, a summary PDF, a processing index CSV, a README, and a manifest with per-file hashes. | Available |

---

## Export Pipeline

Every export runs through an 8-stage pipeline:

| Stage | Description |
|-------|-------------|
| 1. Period Resolution | Validate start/end timestamps and confirm receipts exist in the period |
| 2. Receipt Fetch | Read-only fetch of all receipts in the period |
| 3. Chain Integrity Check | Verify parent_id chain is unbroken - `CHAIN_INTACT` or `CHAIN_ANOMALY` |
| 4. Identity Resolution | Preserve exporter identity and receipt-level operator attribution |
| 5. Format Transform | Apply format-specific transformation (SOC 2, ISO 27001, GDPR, PDF narrative) |
| 6. `ip_address` Redaction | Unconditional removal from all output - no configuration, no bypass |
| 7. Output Generation | Render the final artifact |
| 8. Export Receipt | Write `COMPLIANCE_EXPORT_GENERATED` with the artifact SHA-256 |

The pipeline is idempotent: running the same export twice for the same period should produce the same evidence package shape for the same receipt population.

---

## Auditor-Grade JSON Contract

The JSON exports are not raw receipt dumps. Each JSON artifact now includes these top-level sections:

- `export_metadata`
  - format, version, export id, period, exporter identity, receipt count
- `system_context`
  - deployment/environment metadata plus active Soul versions observed in the evidence population
- `export_scope`
  - exact receipt and quest/workflow population included in the artifact
- `integrity`
  - chain status, anomaly detail, and the export-receipt verification reference
- `evidence_population_summary`
  - status counts, attribution counts, quest counts, and receipt-type counts for the full population
- `operator_attribution_summary`
  - human vs automation vs federated-peer vs legacy-unattributed distribution
- `exception_summary`
  - operational exceptions such as failures, pending states, and review-required evidence
- `legacy_attribution_summary`
  - pre-identity-migration evidence population surfaced separately so legacy attribution debt does not drown current-period operational exceptions

Framework-specific sections then add their own contract:

- SOC 2
  - `control_summary`
  - per-control `control_status`, `evidence_summary`, `exception_count`, `exceptions`, `evidence`
- ISO 27001
  - `statement_of_applicability`
  - per-control `control_status`, `evidence_summary`, `exception_count`, `exceptions`, `evidence`
- GDPR
  - `processing_summary`
  - per-record `evidence_summary`

The raw `evidence` arrays remain in the artifact because auditor traceability still matters. The difference is that they are now wrapped in the summary and exception structure an auditor can actually use.

---

## Bundle Packaging

All machine-readable framework exports are delivered as ZIP bundles rather than loose JSON files.

Each bundle contains:

- the primary evidence JSON
- a short summary PDF for quick auditor review
- a flat CSV index
- `manifest.json` with per-file SHA-256 hashes
- `README.txt`

The bundle itself is still covered by the `COMPLIANCE_EXPORT_GENERATED` receipt via the top-level `output_sha256`. The manifest gives auditors file-level integrity inside the bundle.

The packaging pattern is intentionally consistent across framework exports:

- SOC 2 bundle -> SOC 2 JSON plus SOC 2 summary artifacts
- ISO 27001 bundle -> ISO JSON plus ISO summary artifacts
- GDPR bundle -> GDPR JSON plus GDPR summary artifacts

The bundle shape is consistent. The evidence mapping inside the JSON and CSV is framework-specific.

---

## Chain Integrity

The foundational trust claim of the compliance export. Before generating any artifact, the engine verifies that the receipt DAG's parent_id chain is unbroken for the export period.

- `CHAIN_INTACT`: every receipt's parent_id references an existing receipt
- `CHAIN_ANOMALY`: one or more receipts reference a missing parent

A chain anomaly does not block export. It is surfaced explicitly in:

- `export_metadata.chain_integrity`
- `export_metadata.chain_anomaly_detail`
- `integrity.chain_anomaly_detail`

The standalone verification endpoint remains available at `GET /api/compliance/chain-integrity`.

---

## SOC 2 Control Mapping

| SOC 2 Control | Lancelot Evidence |
|--------------|-------------------|
| CC1.1 - COSO Principles | Soul version history, governance posture records |
| CC2.2 - Internal Communication | Kill switch events with operator identity |
| CC6.1 - Logical Access Controls | Identity-required governance receipts |
| CC6.2 - New Access Provisioned | `CREDENTIAL_REGISTERED`, `MCP_SERVER_REGISTERED`, `CONNECTOR_ENABLED` |
| CC6.3 - Access Removed | `CREDENTIAL_REVOKED`, `MCP_SERVER_REVOKED`, `CONNECTOR_DISABLED` |
| CC6.6 - External Threats | `MCP_TOOL_BLOCKED` |
| CC7.1 - System Monitoring | `GOVERNANCE_WRITE_ERROR` and governance-gap evidence |
| CC7.2 - Anomalies Evaluated | `T3_APPROVED` / `T3_REJECTED`, `MCP_T3_APPROVED` / `MCP_T3_REJECTED` |
| CC7.3 - Incident Identification | Kill switch activation, `AGENT_STOPPED` |
| CC8.1 - Change Management | `SOUL_UPDATED`, `AGENT_DEPLOYED`, `ALLOWLIST_MODIFIED`, `TOOL_ENABLED`, `TOOL_DISABLED` |
| CC9.2 - Risk Mitigation | `APL_RULE_APPROVED`, `APL_RULE_REJECTED` and related governed risk actions |

SOC 2 exports now also include a `mapping_summary` block:

- `mapped_receipt_types` — every receipt type currently mapped into the SOC 2 control set
- `observed_unmapped_receipt_types` — receipt types observed in the export period that are not part of the current SOC 2 mapping
- `observed_unmapped_receipt_count` — how many receipts fell into that unmapped set

That makes mapping gaps explicit for auditors instead of leaving them implicit in mapper comments or code TODOs.

---

## ISO 27001:2022

ISO exports include the same global summary blocks plus:

- `statement_of_applicability.controls_in_scope`
- `statement_of_applicability.controls_out_of_scope`
- `excluded_controls`

Controls outside the scope of an AI agent governance platform are explicitly marked rather than silently omitted.

---

## GDPR Article 30

GDPR exports generate one processing record per workflow (`quest_id`) and include:

- whether personal data was processed
- categories of personal data detected by the scrubbing pipeline
- external recipients
- retention period note
- security measures
- per-record evidence summary

Workflows with no PII events still produce a record noting that no personal data processing was observed.

---

## `ip_address` Redaction

All `ip_address` fields are removed from every export format. This is unconditional.

- no configuration flag
- no bypass
- no exception path

---

## Pre-Identity Migration

Receipts generated before operator identity tracking was available are flagged with:

- `pre_identity_migration: true`
- `pre_identity_migration_note`

These receipts remain visible in the export. They are surfaced in `legacy_attribution_summary` so auditors can distinguish legacy attribution gaps from current-process activity without mixing them into current operational exception counts.

---

## Export Receipt

Every completed export generates a `COMPLIANCE_EXPORT_GENERATED` receipt:

```json
{
  "action_type": "compliance_export_generated",
  "operator_id": "<exporting operator>",
  "session_id": "<session>",
  "inputs": {
    "export_format": "SOC2_JSON",
    "period_start": "2026-01-01T00:00:00Z",
    "period_end": "2026-03-17T00:00:00Z"
  },
  "outputs": {
    "export_id": "<uuid>",
    "receipt_count_exported": 14823,
    "chain_integrity": "CHAIN_INTACT",
    "output_sha256": "<hash>",
    "export_duration_ms": 2840
  }
}
```

This receipt is the canonical delivery-integrity reference for the artifact. The JSON package includes an `integrity.export_receipt_verification` block pointing back to it.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/compliance/export` | Generate a compliance export |
| GET | `/api/compliance/download/{export_id}` | Download a generated export |
| GET | `/api/compliance/chain-integrity` | Standalone chain integrity check |
| GET | `/api/compliance/history` | List previous exports with metadata |
| POST | `/api/compliance/verify/{export_id}` | Verify export hash integrity |
| GET | `/api/compliance/formats` | List available export formats |

---

## Storage

Exports are persisted under `data/compliance_exports/` using self-describing filenames:

```text
{format}_{period_start}_{period_end}_{export_id_short}.{ext}
```

Example:

```text
soc2_json_2026-01-01_2026-03-17_a1b2c3d4.zip
```

---

## War Room UI

The Compliance Export panel is available at `/compliance` in War Room.

Features:

- format selector
- period picker
- optional quest scope
- anomaly threshold input
- one-click generation
- export history table
- per-export hash verification

---

## Configuration

There is no separate compliance-export config file. Behavior comes from:

- the receipt DAG
- the framework mappers in `src/compliance/`
- the PDF renderer in `src/compliance/pdf_export.py`
- runtime environment metadata such as deployment/environment identifiers
