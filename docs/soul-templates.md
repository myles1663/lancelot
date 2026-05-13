<!-- SPDX-License-Identifier: BSL-1.1 -->
<!-- Licensor: Myles Russell Hamilton -->

# Soul Template Library

## Overview

Soul Templates are pre-built Soul configurations targeting specific
industry roles and use cases. Each template is a complete Soul document
packaged with metadata, ready to be applied to a Lancelot instance
through the Soul Amendment workflow.

Templates do not bypass governance. Applying a template creates a
**Soul Amendment Proposal** that flows through the standard
propose → approve → activate pipeline. This ensures every template
application is auditable, reversible, and operator-approved.

---

## Template YAML Format

A template file is a standard Soul YAML document with an additional
top-level `_template_metadata` key. The metadata is stripped before
the Soul document is validated against the linter invariants.

```yaml
_template_metadata:
  name: finance-reporting-analyst        # unique identifier (kebab-case)
  display_name: Finance Reporting Analyst
  description: FP&A report generation, regulatory filing prep
  industry: finance
  version: "1.0.0"
  author: lancelot
  tags:
    - fp&a
    - reporting
    - regulatory

# — Standard Soul fields below —
identity:
  role: ...
permissions:
  ...
constraints:
  ...
# ... remaining Soul document fields
```

### Metadata Fields

| Field          | Required | Description                                    |
|----------------|----------|------------------------------------------------|
| `name`         | Yes      | Unique kebab-case identifier                   |
| `display_name` | Yes      | Human-readable name shown in UI                |
| `description`  | Yes      | One-line summary of the template's purpose      |
| `industry`     | Yes      | Industry category (e.g., `finance`, `legal`)   |
| `version`      | Yes      | SemVer string                                  |
| `author`       | Yes      | Author identifier (`lancelot` for built-ins)   |
| `tags`         | No       | List of searchable tags                         |

---

## Available Templates

| Name                               | Display Name                     | Industry         | Description                                                |
|------------------------------------|----------------------------------|------------------|------------------------------------------------------------|
| `finance-reporting-analyst`        | Finance Reporting Analyst        | finance          | FP&A report generation, regulatory filing prep             |
| `finance-compliance-monitor`       | Finance Compliance Monitor       | finance          | Regulatory compliance monitoring, audit evidence collection |
| `finance-internal-ops`             | Finance Internal Operations      | finance          | Invoice processing, expense routing, payment scheduling    |
| `treasury-cash-management`         | Treasury & Cash Management       | finance          | Cash position monitoring, bank access controls, wire gates  |
| `accounts-payable-automation`      | Accounts Payable Automation      | finance          | Invoice matching, duplicate detection, vendor payment prep |
| `tax-audit-preparation`            | Tax & Audit Preparation          | finance          | Tax document assembly and read-heavy audit prep            |
| `patient-records-accessor`         | Patient Records Accessor         | healthcare       | PHI-scoped record access with audit trails and export gates |
| `clinical-documentation-assistant` | Clinical Documentation Assistant | healthcare       | Transcription cleanup and append-only clinical note drafts |
| `insurance-claims-processor`       | Insurance Claims Processor       | healthcare       | Claims extraction, coding validation, submission prep      |
| `appointment-scheduling-coordinator` | Appointment & Scheduling Coordinator | healthcare  | Patient scheduling with clinical-data exclusion            |
| `contract-review-analyst`          | Contract Review Analyst          | legal            | Clause extraction, risk flagging, and redline suggestions  |
| `legal-research-assistant`         | Legal Research Assistant         | legal            | Public legal research without client-data access           |
| `ediscovery-litigation-hold`       | eDiscovery & Litigation Hold     | legal            | Preservation, custodian tracking, privilege logs           |
| `regulatory-filing-agent`          | Regulatory Filing Agent          | legal            | Filing deadline tracking and governed submissions          |
| `recruiting-screener`              | Recruiting Screener              | hr               | Resume parsing, candidate scoring, and bias audit logging  |
| `employee-onboarding-coordinator`  | Employee Onboarding Coordinator  | hr               | Onboarding documents, provisioning requests, orientation   |
| `benefits-administrator`           | Benefits Administrator           | hr               | Enrollment processing and carrier sync controls            |
| `payroll-operations-monitor`       | Payroll Operations Monitor       | hr               | Payroll validation and read-only execution monitoring      |
| `helpdesk-triage-agent`            | Helpdesk Triage Agent            | it-security      | Ticket triage and reset orchestration without admin creds  |
| `security-incident-responder`      | Security Incident Responder      | it-security      | Alert correlation, evidence preservation, containment gates |
| `access-provisioner`               | Access Provisioner               | it-security      | Access request routing with self-elevation prevention      |
| `vulnerability-patch-monitor`      | Vulnerability & Patch Monitor    | it-security      | Scan ingestion and patch scheduling recommendations        |
| `lead-qualification-agent`         | Lead Qualification Agent         | sales            | Lead scoring, enrichment, and CRM lead/contact routing     |
| `proposal-quote-generator`         | Proposal & Quote Generator       | sales            | Proposal assembly and governed quote generation            |
| `pipeline-analyst`                 | Pipeline Analyst                 | sales            | Forecast modeling and read-only opportunity analysis       |
| `tier-1-support-agent`             | Tier 1 Support Agent             | customer-support | KB answers, ticket routing, capped refunds                 |
| `customer-feedback-analyst`        | Customer Feedback Analyst        | customer-support | Sentiment, themes, NPS trends with PII scrubbing           |
| `escalation-manager`               | Escalation Manager               | customer-support | SLA monitoring and legal/regulatory response gates         |
| `procurement-agent`                | Procurement Agent                | operations       | Requisitions, vendor comparison, PO drafts                 |
| `inventory-demand-monitor`         | Inventory & Demand Monitor       | operations       | Stock tracking, reorder alerts, demand forecasting         |
| `vendor-risk-assessor`             | Vendor Risk Assessor             | operations       | Due diligence and risk scoring without approval authority  |
| `content-operations-agent`         | Content Operations Agent         | marketing        | Drafting, approval routing, publishing queue management    |
| `campaign-analyst`                 | Campaign Analyst                 | marketing        | Campaign analytics with read-only spend controls           |
| `social-media-monitor`             | Social Media Monitor             | marketing        | Mention tracking, sentiment alerts, response drafting      |
| `board-reporting-agent`            | Board Reporting Agent            | executive        | Board package aggregation with read-only source access     |
| `competitive-intelligence-analyst` | Competitive Intelligence Analyst | executive        | Public-source market and competitor monitoring             |
| `student-records-administrator`    | Student Records Administrator    | education        | FERPA-scoped transcript and enrollment workflows           |
| `curriculum-assessment-assistant`  | Curriculum & Assessment Assistant | education       | Rubric support without official grade modification         |

---

## Template Validation

Every template must pass the current Soul linter invariants before it can
be loaded or applied. Templates that fail validation are rejected at load
time and cannot appear in the template browser.

| #  | Invariant                               | Severity |
|----|-----------------------------------------|----------|
| 1  | `destructive_actions_require_approval`  | CRITICAL |
| 2  | `no_silent_degradation`                 | CRITICAL |
| 3  | `scheduling_no_autonomous_irreversible` | CRITICAL |
| 4  | `approval_channels_required`            | CRITICAL |
| 5  | `memory_ethics_required`                | WARNING  |
| 6  | `billing_requires_approval`             | CRITICAL |
| 7  | `external_transmission_requires_approval` | CRITICAL |
| 8  | `sensitive_bulk_export_requires_approval` | CRITICAL |
| 9  | `kill_switch_rules_enforced`            | CRITICAL |
| 10 | `messaging_no_spam`                     | CRITICAL |

The expanded library includes domain-specific approval gates in
`autonomy_posture.requires_approval`, `risk_rules`, and structured fields
that runtime components can enforce directly.

Structured governance fields are now available for templates that need
machine-enforceable controls:

| Field                         | Purpose                                                       |
|-------------------------------|---------------------------------------------------------------|
| `risk_overrides`              | Per-capability minimum risk tiers; can only raise risk        |
| `trust_ceilings`              | Floors that trust graduation cannot go below                  |
| `connector_policies`          | Recipient, channel, rate-limit, and content-review controls   |
| `data_boundaries`             | Sensitive data classifications and access/disclosure limits   |
| `external_transmission_rules` | Approval and scrubbing rules for outbound data movement       |
| `kill_switch_rules`           | Halt-and-escalate triggers for critical safety conditions     |

---

## Apply Flow

1. Operator selects a template and optionally provides field overrides.
2. System creates a **Soul Amendment Proposal** with
   `author: "template:{name}"` (e.g., `template:finance-reporting-analyst`).
3. The proposal enters the standard amendment pipeline:
   **propose → approve → activate**.
4. On activation, a `SOUL_TEMPLATE_APPLIED` receipt is emitted.
   This receipt type is **identity-required** — it records the operator
   identity, the template name, the template version, and any
   customization overrides applied.

---

## Customizations

Templates accept optional field overrides at apply time. Overrides are
**deep-merged** into the template's Soul document before validation:

- Scalar values are replaced.
- Lists are replaced (not appended).
- Nested objects are merged recursively.

The merged document must still pass all Soul linter invariants. If the
merge produces an invalid Soul, the proposal is rejected before it
enters the amendment pipeline.

---

## API Endpoints

| Method | Path                                | Description                              |
|--------|-------------------------------------|------------------------------------------|
| GET    | `/soul/templates`                   | List all available templates (filterable by `industry`, `tags`) |
| GET    | `/soul/templates/{name}`            | Get a single template with full YAML     |
| POST   | `/soul/templates/{name}/apply`      | Apply template as a Soul Amendment Proposal. Body accepts optional `overrides` object. |
| POST   | `/soul/templates/reload`            | Reload templates from disk (operator-only) |

---

## War Room UI

The Soul Template Library is accessible from the **Templates** tab
within the Soul Inspector panel in the War Room.

- **Template Browser** — Displays all loaded templates in a filterable
  list. Supports filtering by industry. Each card shows display name,
  industry badge, description, and version.
- **Detail View** — Selecting a template opens a detail pane with the
  full YAML preview (metadata + Soul document), tag list, and author.
- **Apply as Proposal** — Button in the detail view that opens a
  customization dialog (optional overrides), then submits the template
  as a Soul Amendment Proposal. The operator is redirected to the
  amendment review flow after submission.

---

## Source Files

| File                              | Purpose                                      |
|-----------------------------------|----------------------------------------------|
| `templates.py`                    | Template loader, validator, and merge logic   |
| `template_api.py`                 | FastAPI route handlers for template endpoints |
| `templates/finance/*.yaml`        | Built-in finance industry template files      |
| `SoulInspector.tsx`               | War Room Soul Inspector with Templates tab    |
