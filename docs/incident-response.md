# Incident Response Playbooks

Structured response protocols for every governance event the Lancelot platform can generate. The Incident Response subsystem detects anomalous patterns in the receipt stream, opens incident records, attaches playbook checklists, and generates PDF reports — all under feature-flag control.

**Feature flag:** `FEATURE_INCIDENT_RESPONSE` (default: `false`)

---

## Architecture Overview

```
Receipt Stream
  → receipt_bridge.py (non-blocking hook)
    → receipt_hook.py (callback adapter)
      → trigger_engine.py (12 rules, burst counters, dedup)
        → IncidentStore (JSON persistence in data/incidents/)
          → playbooks.py (attach matching playbook + variant overlay)
          → War Room IncidentsDashboard (real-time display)
          → report_generator.py (PDF export via shared pdf_helpers)
```

The subsystem is **read-only with respect to governance** — it reads receipts but does not write to the governance pipeline. Incident records are stored separately from the receipt system in `data/incidents/`.

### Key Components

| Module | Path | Responsibility |
|--------|------|---------------|
| Models | `src/incidents/models.py` | `IncidentRecord`, `IncidentCategory`, `IncidentStatus`, `IncidentSeverity`, `TimelineEntry` |
| Store | `src/incidents/store.py` | JSON file persistence, singleton `IncidentStore`, CRUD operations |
| Trigger Engine | `src/incidents/trigger_engine.py` | 12 trigger rules, fixed-window burst counters, per-trigger dedup |
| Receipt Hook | `src/incidents/receipt_hook.py` | Receipt bridge integration, non-blocking callback adapter |
| Playbook Registry | `src/incidents/playbooks.py` | YAML playbook loader, industry variant overlay support |
| Playbook API | `src/incidents/playbook_api.py` | REST API for playbook listing and reload |
| Report Generator | `src/incidents/report_generator.py` | PDF incident report generation |
| Incident API | `src/incidents/api.py` | 11 REST endpoints for incident lifecycle management |
| Shared PDF Helpers | `src/shared/pdf_helpers.py` | Extracted ReportLab utilities (color palette, styles, table builder, cover page) |

---

## Incident Categories

| Category | Description | Example Triggers |
|----------|------------|-----------------|
| **Governance** | Soul violations, policy overrides, approval timeouts | Burst of governance blocks, Soul amendment rejected |
| **Security** | Injection attempts, credential failures, unauthorized access | Prompt injection detected, repeated auth failures |
| **Cost** | Budget threshold breaches, runaway spend | Cost threshold exceeded, API spend spike |
| **Availability** | Service degradation, provider failures, health check failures | Provider timeout burst, health check failures |
| **Compliance** | Audit trail gaps, PII exposure, retention violations | Receipt chain integrity failure, compliance export error |

---

## Trigger Engine

The trigger engine evaluates every receipt as it is created via a non-blocking hook in the receipt bridge. It applies 12 trigger rules to detect anomalous patterns.

### Design Decisions

- **Fixed-window burst counters** — Simpler than sliding window; counts reset at window boundaries. Upgrade path to sliding window exists if needed.
- **Per-trigger dedup** — Configurable dedup window prevents duplicate incidents from the same event source. Dedup key is `(trigger_type, source_identifier)`.
- **Non-blocking** — Trigger evaluation never blocks the receipt write path. If the trigger engine fails, receipts are still persisted normally.

### 12 Trigger Rules

The trigger engine ships with 12 built-in rules covering all five incident categories. Each rule defines a receipt pattern match, burst threshold (count within time window), severity level, and the playbook to attach.

---

## Playbooks

### Base Playbooks (12)

Playbooks are YAML files in the `playbooks/` directory, organized by category:

| Category | Playbooks |
|----------|-----------|
| **governance/** | 3 playbooks (Soul violation response, approval escalation, policy override investigation) |
| **security/** | 3 playbooks (injection attempt response, credential compromise, unauthorized access) |
| **cost/** | 2 playbooks (budget threshold breach, runaway spend containment) |
| **availability/** | 2 playbooks (provider failure response, service degradation) |
| **compliance/** | 3 playbooks (audit trail gap remediation, PII exposure response, retention violation) |

Each playbook defines:
- **Name and description** — Human-readable identification
- **Category and severity** — Classification metadata
- **Steps** — Ordered checklist of response actions with descriptions
- **Escalation criteria** — Conditions under which the incident should be escalated

### Industry Variant Overlays (3)

Variant overlays live in `playbooks/variants/` and append additional steps to base playbooks for industry-specific requirements:

| Variant | Path | Purpose |
|---------|------|---------|
| **Finance** | `playbooks/variants/finance/` | Regulatory reporting (SEC, FINRA), transaction freeze procedures, AML considerations |
| **Healthcare** | `playbooks/variants/healthcare/` | HIPAA breach notification, PHI containment, HHS reporting |
| **Regulated General** | `playbooks/variants/regulated-general/` | General regulatory compliance steps applicable across regulated industries |

**Overlay mechanism:** Variant files use an `insert_after` field to specify where additional steps should be appended in the base playbook. This is an append-after strategy — variant steps are inserted after the named base step, preserving the original playbook order.

### Playbook Reload

Playbooks can be hot-reloaded without restart via `POST /api/playbooks/reload`. This invalidates the in-memory registry cache and re-reads all YAML files from disk.

---

## Incident Lifecycle

```
OPEN → IN_PROGRESS → RESOLVED → CLOSED
                   → ESCALATED → RESOLVED → CLOSED
```

1. **Trigger fires** — The trigger engine detects a pattern match and creates an `IncidentRecord` with status `OPEN`.
2. **Playbook attached** — The matching playbook (with any applicable variant overlay) is attached to the incident.
3. **Investigation** — Operator works through the playbook checklist in the War Room, adding timeline entries and notes.
4. **Resolution** — Operator marks the incident as resolved with a resolution summary.
5. **Close** — Incident is closed with final disposition.

Escalation can occur at any point, bumping severity and notifying via the configured channels.

---

## Incident Store

Incident records are persisted as JSON files in `data/incidents/`. The `IncidentStore` is a singleton that provides CRUD operations with file-level locking.

**Storage format:** One JSON file per incident, named by incident ID. This keeps incidents independent and avoids contention on a single data file.

**Separation from receipts:** Incident records are deliberately stored outside the receipt system. Receipts record *what happened*; incidents record *what we did about it*. The incident system generates its own receipts (10 new types) to maintain audit trail continuity.

---

## API Endpoints

### Incident API (`/api/incidents/`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/incidents/` | List incidents (filterable by status, category, severity) |
| `GET` | `/api/incidents/{id}` | Get incident detail |
| `POST` | `/api/incidents/` | Create incident manually |
| `PUT` | `/api/incidents/{id}` | Update incident fields |
| `POST` | `/api/incidents/{id}/close` | Close incident with resolution |
| `GET` | `/api/incidents/{id}/timeline` | Get incident timeline entries |
| `POST` | `/api/incidents/{id}/assign` | Assign incident to operator |
| `POST` | `/api/incidents/{id}/escalate` | Escalate incident severity |
| `GET` | `/api/incidents/stats` | Aggregate incident statistics |
| `GET` | `/api/incidents/{id}/export` | Export incident as PDF report |
| `POST` | `/api/incidents/bulk` | Bulk operations on multiple incidents |

### Playbook API (`/api/playbooks/`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/playbooks` | List all available playbooks |
| `GET` | `/api/playbooks/{name}` | Get playbook detail (with variant overlay if applicable) |
| `POST` | `/api/playbooks/reload` | Hot-reload playbook registry from disk |

---

## Receipt Types

10 new `ActionType` values added to `src/shared/receipts.py`:

| Receipt Type | Description | Identity Required |
|-------------|-------------|:-:|
| `INCIDENT_OPENED` | New incident created by trigger engine | No |
| `INCIDENT_CLOSED` | Incident closed with resolution | Yes |
| `INCIDENT_ESCALATED` | Incident severity escalated | Yes |
| `INCIDENT_ASSIGNED` | Incident assigned to operator | Yes |
| `INCIDENT_NOTE_ADDED` | Note added to incident timeline | Yes |
| `INCIDENT_TIMELINE_ENTRY` | Timeline event recorded | Yes |
| `PLAYBOOK_STARTED` | Playbook checklist attached to incident | Yes |
| `PLAYBOOK_STEP_COMPLETED` | Playbook checklist step completed | Yes |
| `PLAYBOOK_COMPLETED` | All playbook steps completed | Yes |
| `PLAYBOOK_UPDATED` | Playbook definition modified via reload | Yes |

**Identity policy:** All receipt types except `INCIDENT_OPENED` (system-generated by trigger engine) require operator identity attribution via `operator_identity.py`.

---

## Webhook Integration

The `INCIDENT_RESPONSE` webhook category is registered in `src/observability/webhook_categories.py` with mappings for all 10 incident receipt types. When `FEATURE_OBSERVABILITY` is enabled, incident events are delivered to configured webhook endpoints alongside standard receipt webhooks.

---

## War Room UI

### Incidents Dashboard (`/incidents`)

The Incidents Dashboard (`src/warroom/src/pages/IncidentsDashboard.tsx`) provides:

- **Stats cards** — Open incidents count, in-progress count, average resolution time, incidents by category
- **Incident table** — Sortable, filterable list with status badges, severity indicators, category tags, and timestamps
- **Incident detail view** — Full incident record with metadata, assigned operator, and linked receipts
- **Playbook checklist** — Interactive step-by-step checklist from the attached playbook; steps can be checked off as completed
- **Timeline** — Chronological event stream showing all actions taken on the incident
- **Close flow** — Resolution form with summary field and final disposition

### API Client

`src/warroom/src/api/incidents.ts` provides the TypeScript API client for all incident and playbook endpoints, used by the dashboard components.

---

## Feature Flag

| Flag | Default | Effect When Disabled |
|------|---------|---------------------|
| `FEATURE_INCIDENT_RESPONSE` | `false` | No incident auto-detection, no trigger evaluation, no playbook attachment, no incident API endpoints. Receipt DAG and manual audit remain available. |

The flag gates the receipt bridge hook (no trigger evaluation), API router mounts (endpoints return "not available"), and War Room dashboard (panel hidden).

---

## PDF Reports

The report generator (`src/incidents/report_generator.py`) produces PDF incident reports using ReportLab. Shared helpers were extracted to `src/shared/pdf_helpers.py` to avoid duplication with other report-generating subsystems.

**Shared PDF helpers provide:**
- Color palette (consistent branding across all PDF reports)
- Paragraph and heading styles
- Table builder with alternating row colors
- Cover page template with logo and metadata

Reports include incident metadata, full timeline, playbook checklist status, linked receipt references, and resolution summary.

---

## Relationship to Other Subsystems

- **Receipt Bridge** (`src/observability/receipt_bridge.py`) — The incident trigger hook is registered here, evaluated on every receipt write
- **Operator Identity** (`src/core/operator_identity.py`) — 8 incident receipt types require identity attribution
- **Feature Flags** (`src/core/feature_flags.py`) — `FEATURE_INCIDENT_RESPONSE` gates the entire subsystem
- **Observability** (`src/observability/webhook_categories.py`) — `INCIDENT_RESPONSE` webhook category for external notification
- **Gateway** (`src/core/gateway.py`) — Incident API and Playbook API routers mounted alongside other API routers

The subsystem is **read-only with respect to governance** — it consumes receipts to detect patterns but does not inject decisions into the governance pipeline. This is a deliberate separation: the incident system observes and responds to governance events, it does not participate in governance evaluation.
