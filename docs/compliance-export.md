# Compliance Export

One-click compliance report generation from Lancelot's receipt DAG. Turns the internal governance trail into external audit artifacts in the formats compliance teams expect.

---

## Export Formats

| Format | Description | Status |
|--------|-------------|--------|
| **Forensic Timeline PDF** | Human-readable PDF for board presentation, legal review, regulatory submission. Executive summary, governance controls, human authorization log, full event log, anomaly report. | Available |
| **SOC 2 Type II JSON** | Structured JSON mapped to Trust Services Criteria (CC1–CC9). Machine-readable for GRC platforms (Vanta, Drata, Secureframe). | Available |
| **ISO 27001:2022 JSON** | Structured JSON mapped to Annex A controls. Covers A.5, A.8 with explicit exclusion notes for out-of-scope controls. | Available |
| **GDPR Article 30 JSON** | Processing activity records per workflow. PII category detection, data recipients, retention periods, security measures. | Available |

---

## Export Pipeline

Every export runs through an 8-stage pipeline:

| Stage | Description |
|-------|-------------|
| 1. Period Resolution | Validate start/end timestamps, confirm receipts exist in the period |
| 2. Receipt Fetch | Read-only fetch of all receipts in the period |
| 3. Chain Integrity Check | Verify parent_id chain is unbroken — CHAIN_INTACT or CHAIN_ANOMALY |
| 4. Identity Resolution | Resolve operator display names from operator registry |
| 5. Format Transform | Apply format-specific transformation (SOC 2 mapping, ISO 27001 mapping, GDPR records) |
| 6. ip_address Redaction | Unconditional removal from all output — no configuration, no bypass |
| 7. Output Generation | Render final artifact (JSON or PDF) |
| 8. Export Receipt | Write COMPLIANCE_EXPORT_GENERATED receipt with output SHA-256 |

The pipeline is **idempotent** — running the same export twice for the same period produces the same output.

---

## Chain Integrity Check

The foundational trust claim of the compliance export. Before generating any artifact, the engine verifies that the receipt DAG's parent_id chain is unbroken for the export period.

- **CHAIN_INTACT**: Every receipt's parent_id references an existing receipt. The governance record is tamper-evident.
- **CHAIN_ANOMALY**: One or more receipts reference a parent that doesn't exist. Gap details are included in the export metadata.

A chain anomaly does **not** block export. It is reported with full detail. An auditor receiving a Lancelot export with CHAIN_INTACT has evidence of a tamper-evident governance record.

The chain integrity check endpoint is also available standalone at `GET /api/compliance/chain-integrity` for verification without generating a full export.

---

## SOC 2 Control Mapping

| SOC 2 Control | Lancelot Evidence |
|--------------|-------------------|
| CC1.1 — COSO Principles | Soul version history, governance posture records |
| CC2.2 — Internal Communication | Kill switch events with operator identity |
| CC6.1 — Logical Access Controls | All identity-required receipt types (26 types) |
| CC6.2 — New Access Provisioned | CREDENTIAL_REGISTERED, MCP_SERVER_REGISTERED, CONNECTOR_ENABLED |
| CC6.3 — Access Removed | CREDENTIAL_REVOKED, MCP_SERVER_REVOKED, CONNECTOR_DISABLED |
| CC6.6 — External Threats | MCP_TOOL_BLOCKED (injection detection, allowlist enforcement) |
| CC7.1 — System Monitoring | GOVERNANCE_WRITE_ERROR, T3 approval response times |
| CC7.2 — Anomalies Evaluated | T3_APPROVED/REJECTED, MCP_T3_APPROVED/REJECTED |
| CC7.3 — Incident Identification | Kill switch activation, AGENT_STOPPED |
| CC8.1 — Change Management | SOUL_UPDATED, AGENT_DEPLOYED, ALLOWLIST_MODIFIED, TOOL_ENABLED/DISABLED |
| CC9.2 — Risk Mitigation | APL_RULE_APPROVED/REJECTED, risk tier distribution |

---

## ISO 27001:2022 Control Mapping

Covers Annex A controls directly addressed by an AI agent governance platform. Controls outside scope (physical security, supplier relationships, business continuity) are explicitly excluded with explanatory notes in the export.

---

## GDPR Article 30 Processing Records

One processing activity record per workflow (`quest_id`) where PII scrubbing was triggered. Records include:

- **Purpose**: Derived from Soul document
- **Categories of personal data**: From PII scrubbing pipeline (or "detected, category not recorded" if pipeline records only occurrence)
- **Recipients**: External services that received outputs (connectors, MCP servers)
- **Retention period**: From receipt TTL configuration
- **Security measures**: Soul constraints, risk tier controls, input sanitization, credential vault isolation

Workflows with no PII events produce a brief "no personal data processing" record — not an omission.

---

## ip_address Redaction

All `ip_address` fields from OperatorIdentity records are removed from every export format. This is **unconditional**. There is no configuration flag, no bypass, and no exception. The redaction is applied at the compliance redaction layer before any format-specific transformation.

---

## PRE_IDENTITY_MIGRATION

Receipts generated before Operator Identity tracking was implemented (operator_id is NULL) are flagged with `pre_identity_migration: true` and an explanatory note in all export formats. They do not cause export failure. They are present in the artifact with the flag so auditors can distinguish between "automated action" and "action from before identity tracking existed."

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

This receipt requires OperatorIdentity — anonymous exports are blocked. The `output_sha256` allows recipients to verify they received an unmodified copy.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/compliance/export` | Generate a compliance export |
| GET | `/api/compliance/download/{export_id}` | Download a generated export |
| GET | `/api/compliance/chain-integrity` | Standalone chain integrity check |
| GET | `/api/compliance/history` | List all previous exports with metadata |
| POST | `/api/compliance/verify/{export_id}` | Verify export hash integrity |
| GET | `/api/compliance/formats` | List available export formats |

---

## Storage

Exports are persisted to `data/compliance_exports/` with self-describing filenames:

```
{format}_{period_start}_{period_end}_{export_id_short}.{ext}
```

Example: `soc2_json_2026-01-01_2026-03-17_a1b2c3d4.json`

---

## War Room UI

The Compliance Export panel is available at `/compliance` in the War Room sidebar under the **COMPLIANCE** section.

**Features:**
- **Format selector** — choose PDF, SOC 2, ISO 27001, or GDPR export format
- **Period picker** — custom date range with presets (7d, 30d, 90d, YTD)
- **Scope selector** — optional quest_id to scope exports to a single workflow
- **Anomaly threshold** — configurable blocked-actions-per-24h threshold
- **One-click generate** — progress indicator with inline results (receipt count, duration, chain status, SHA-256, download link)
- **Export history table** — all previous exports with format badge, period, receipt count, chain integrity status, duration, SHA-256 preview, download, and hash verification
- **Hash verification** — per-export SHA-256 re-verification button that compares current disk hash against the original export receipt

---

## Configuration

No separate configuration file. Export behavior is determined by:

- **Receipt store**: All data comes from the existing receipt DAG
- **SOC 2 control mapping**: Maintained in `src/compliance/soc2_mapper.py`
- **ISO 27001 control mapping**: Maintained in `src/compliance/iso27001_mapper.py`
- **Anomaly threshold**: Configurable per-export (default: 5 blocked actions per 24h)
