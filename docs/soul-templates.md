<!-- SPDX-License-Identifier: BSL-1.1 -->
<!-- Licensor: Myles Russell Hamilton -->

# Soul Template Library

## Overview

Soul Templates are pre-built Soul configurations targeting specific
industry roles and use cases. Each template is a complete Soul document
packaged with metadata, ready to be applied to a Lancelot instance
through the Soul Amendment workflow.

The library currently ships 38 governed role templates. They are not
prompt packs. A template carries the same governance surface as any other
Soul: mission, allegiance, autonomy posture, approval rules, risk rules,
memory ethics, scheduling boundaries, and optional structured controls
such as risk overrides, trust ceilings, connector policies, data
boundaries, external transmission rules, and kill-switch rules.

Templates do not bypass governance. Applying a template creates a
**Soul Amendment Proposal** that flows through the standard
propose -> approve -> activate pipeline. This ensures every template
application is auditable, reversible, and operator-approved.

---

## Template YAML Format

A template file is a standard Soul YAML document with an additional
top-level `_template_metadata` key. The metadata is stripped before
the Soul document is validated against the linter invariants.

```yaml
_template_metadata:
  name: "tier-1-support-agent"
  display_name: "Tier 1 Support Agent"
  description: "Knowledge-base answers, ticket routing, and basic troubleshooting with capped refund authority."
  industry: "customer-support"
  version: "1.0"
  author: "Lancelot"
  tags:
    - "support"
    - "tier-1"
    - "tickets"
    - "refunds"
    - "knowledge-base"

version: "v1"
mission: "Resolve basic support requests using approved knowledge-base guidance while escalating legal threats and high-value refunds."
allegiance: "Lancelot serves a single owner. Support responses align with customer care policy and risk thresholds."
autonomy_posture:
  level: "supervised"
  description: "May answer basic tickets and route cases. Refunds above threshold, credits, and legal threats require approval."
  allowed_autonomous:
    - "classify_intent"
    - "summarize"
    - "answer_knowledge_base"
    - "route_ticket"
    - "basic_troubleshooting"
    - "issue_capped_refund"
  requires_approval:
    - "deploy"
    - "delete"
    - "financial_transaction"
    - "credential_rotation"
    - "system_configuration"
    - "refund_above_threshold"
    - "issue_credit"
    - "respond_to_legal_threat"
risk_rules:
  - name: "destructive_actions_require_approval"
    description: "Deleting or modifying tickets requires approval."
    enforced: true
  - name: "refund_cap_enforced"
    description: "Refund authority is capped and over-threshold refunds require approval."
    enforced: true
approval_rules:
  default_timeout_seconds: 1200
  escalation_on_timeout: "skip_and_log"
  channels:
    - "war_room"
    - "chat"
tone_invariants:
  - "Never mislead the owner or customer about policy or resolution status"
  - "Report failures immediately and transparently"
  - "Never suppress errors or degrade silently"
memory_ethics:
  - "Do not store customer PII in long-term memory without consent"
  - "Redact customer identifiers before logging"
  - "Soul is not stored in recursive memory"
scheduling_boundaries:
  max_concurrent_jobs: 6
  max_job_duration_seconds: 300
  no_autonomous_irreversible: true
  require_ready_state: true
  description: "Scheduled support jobs may route tickets but cannot issue uncapped refunds."
risk_overrides:
  - capability: "connector.support.refund_above_threshold"
    min_tier: "T3"
    reason: "Refunds above threshold are financial transactions."
connector_policies:
  email:
    verified_recipients:
      - "*@customer.example"
    max_sends_per_day: 100
    require_content_verification: true
    pii_scrubbing_required: true
    approval_required_for_send: false
```

### Metadata Fields

| Field | Required | Description |
| --- | --- | --- |
| `name` | Yes | Unique kebab-case identifier |
| `display_name` | Yes | Human-readable name shown in UI |
| `description` | Yes | One-line summary of the template's purpose |
| `industry` | Yes | Industry category, such as `finance`, `legal`, or `healthcare` |
| `version` | Yes | Template version string |
| `author` | Yes | Author identifier (`Lancelot` for built-ins) |
| `tags` | No | Search/display metadata for operators |

---

## Available Templates

| Name | Display Name | Industry | Description |
| --- | --- | --- | --- |
| `finance-reporting-analyst` | Finance Reporting Analyst | finance | FP&A report generation, regulatory filing prep |
| `finance-compliance-monitor` | Finance Compliance Monitor | finance | Regulatory compliance monitoring, audit evidence collection |
| `finance-internal-ops` | Finance Internal Operations | finance | Invoice processing, expense routing, payment scheduling |
| `treasury-cash-management` | Treasury & Cash Management | finance | Cash position monitoring, bank access controls, wire gates |
| `accounts-payable-automation` | Accounts Payable Automation | finance | Invoice matching, duplicate detection, vendor payment prep |
| `tax-audit-preparation` | Tax & Audit Preparation | finance | Tax document assembly and read-heavy audit prep |
| `patient-records-accessor` | Patient Records Accessor | healthcare | PHI-scoped record access with audit trails and export gates |
| `clinical-documentation-assistant` | Clinical Documentation Assistant | healthcare | Transcription cleanup and append-only clinical note drafts |
| `insurance-claims-processor` | Insurance Claims Processor | healthcare | Claims extraction, coding validation, submission prep |
| `appointment-scheduling-coordinator` | Appointment & Scheduling Coordinator | healthcare | Patient scheduling with clinical-data exclusion |
| `contract-review-analyst` | Contract Review Analyst | legal | Clause extraction, risk flagging, and redline suggestions |
| `legal-research-assistant` | Legal Research Assistant | legal | Public legal research without client-data access |
| `ediscovery-litigation-hold` | eDiscovery & Litigation Hold | legal | Preservation, custodian tracking, privilege logs |
| `regulatory-filing-agent` | Regulatory Filing Agent | legal | Filing deadline tracking and governed submissions |
| `recruiting-screener` | Recruiting Screener | hr | Resume parsing, candidate scoring, and bias audit logging |
| `employee-onboarding-coordinator` | Employee Onboarding Coordinator | hr | Onboarding documents, provisioning requests, orientation |
| `benefits-administrator` | Benefits Administrator | hr | Enrollment processing and carrier sync controls |
| `payroll-operations-monitor` | Payroll Operations Monitor | hr | Payroll validation and read-only execution monitoring |
| `helpdesk-triage-agent` | Helpdesk Triage Agent | it-security | Ticket triage and reset orchestration without admin creds |
| `security-incident-responder` | Security Incident Responder | it-security | Alert correlation, evidence preservation, containment gates |
| `access-provisioner` | Access Provisioner | it-security | Access request routing with self-elevation prevention |
| `vulnerability-patch-monitor` | Vulnerability & Patch Monitor | it-security | Scan ingestion and patch scheduling recommendations |
| `lead-qualification-agent` | Lead Qualification Agent | sales | Lead scoring, enrichment, and CRM lead/contact routing |
| `proposal-quote-generator` | Proposal & Quote Generator | sales | Proposal assembly and governed quote generation |
| `pipeline-analyst` | Pipeline Analyst | sales | Forecast modeling and read-only opportunity analysis |
| `tier-1-support-agent` | Tier 1 Support Agent | customer-support | KB answers, ticket routing, capped refunds |
| `customer-feedback-analyst` | Customer Feedback Analyst | customer-support | Sentiment, themes, NPS trends with PII scrubbing |
| `escalation-manager` | Escalation Manager | customer-support | SLA monitoring and legal/regulatory response gates |
| `procurement-agent` | Procurement Agent | operations | Requisitions, vendor comparison, PO drafts |
| `inventory-demand-monitor` | Inventory & Demand Monitor | operations | Stock tracking, reorder alerts, demand forecasting |
| `vendor-risk-assessor` | Vendor Risk Assessor | operations | Due diligence and risk scoring without approval authority |
| `content-operations-agent` | Content Operations Agent | marketing | Drafting, approval routing, publishing queue management |
| `campaign-analyst` | Campaign Analyst | marketing | Campaign analytics with read-only spend controls |
| `social-media-monitor` | Social Media Monitor | marketing | Mention tracking, sentiment alerts, response drafting |
| `board-reporting-agent` | Board Reporting Agent | executive | Board package aggregation with read-only source access |
| `competitive-intelligence-analyst` | Competitive Intelligence Analyst | executive | Public-source market and competitor monitoring |
| `student-records-administrator` | Student Records Administrator | education | FERPA-scoped transcript and enrollment workflows |
| `curriculum-assessment-assistant` | Curriculum & Assessment Assistant | education | Rubric support without official grade modification |

---

## Template Validation

Every template must pass the current Soul linter invariants before it can
be loaded or applied. Templates that fail validation are rejected at load
time and cannot appear in the template browser.

| # | Invariant | Severity |
| --- | --- | --- |
| 1 | `destructive_actions_require_approval` | CRITICAL |
| 2 | `no_silent_degradation` | CRITICAL |
| 3 | `scheduling_no_autonomous_irreversible` | CRITICAL |
| 4 | `approval_channels_required` | CRITICAL |
| 5 | `memory_ethics_required` | WARNING |
| 6 | `billing_requires_approval` | CRITICAL |
| 7 | `external_transmission_requires_approval` | CRITICAL |
| 8 | `sensitive_bulk_export_requires_approval` | CRITICAL |
| 9 | `kill_switch_rules_enforced` | CRITICAL |
| 10 | `messaging_no_spam` | CRITICAL |

The expanded library includes domain-specific approval gates in
`autonomy_posture.requires_approval`, `risk_rules`, and structured fields
that runtime components can enforce directly.

Structured governance fields are available for templates that need
machine-enforceable controls:

| Field | Purpose |
| --- | --- |
| `risk_overrides` | Per-capability minimum risk tiers; can only raise risk |
| `trust_ceilings` | Floors that trust graduation cannot go below |
| `connector_policies` | Recipient, channel, rate-limit, and content-review controls |
| `data_boundaries` | Sensitive data classifications and access/disclosure limits |
| `external_transmission_rules` | Approval and scrubbing rules for outbound data movement |
| `kill_switch_rules` | Halt-and-escalate triggers for critical safety conditions |

---

## Operator Workflow

1. Open the War Room and go to the Soul Viewer/Soul Inspector template tab.
2. Browse templates by industry and select the role closest to the operating model.
3. Inspect the governance fields: autonomy posture, required approvals, risk overrides, trust ceilings, data boundaries, external transmission rules, and kill-switch rules.
4. Edit customizations only where the deployment needs a local policy value, such as mission text, approval timeout, verified recipients, connector policy limits, or data boundary labels.
5. Run the evaluator/behavior contracts for representative allowed, approval-required, and denied actions before applying the template to production use.
6. Apply the template as a Soul amendment proposal.
7. Review the amendment diff, approve it, and activate it through the standard Soul workflow.
8. Verify the active Soul version, receipt details, policy-cache rebuild, and expected evaluator outcomes.
9. If behavior is not correct, roll back to the prior Soul version and re-propose a narrower customization.

Applying a template mutates the active Soul only after approval and
activation. The apply step prepares a proposal; it does not silently
switch the runtime policy.

---

## Apply Flow

1. Operator selects a template and optionally provides field customizations.
2. System creates a **Soul Amendment Proposal** with
   `author: "template:{name}"` (e.g., `template:finance-reporting-analyst`).
3. The proposal enters the standard amendment pipeline:
   **propose -> approve -> activate**.
4. On activation, a `SOUL_TEMPLATE_APPLIED` receipt is emitted.
   This receipt type is **identity-required** - it records the operator
   identity, the template name, the template version, and any
   customizations applied.

---

## Customizations

Templates accept optional field customizations at apply time.
Customizations are **deep-merged** into the template's Soul document
before validation:

- Scalar values are replaced.
- Lists are replaced, not appended.
- Nested objects are merged recursively.

The merged document must still pass all Soul linter invariants. If the
merge produces an invalid Soul, the proposal is rejected before it
enters the amendment pipeline.

---

## API Endpoints

| Method | Path | Description |
| --- | --- | --- |
| GET | `/soul/templates` | List all available templates; supports optional `industry` query filtering |
| GET | `/soul/templates/{name}` | Get a single template with full YAML |
| POST | `/soul/templates/{name}/apply` | Apply template as a Soul Amendment Proposal. Body accepts optional `customizations` object. |
| POST | `/soul/templates/reload` | Reload templates from disk; requires `soul.admin` |

Tags are template metadata used for search, display, and operator review.
They are not currently a server-side query filter.

Example apply request:

```json
{
  "customizations": {
    "mission": "Resolve basic support requests for the North America support desk using the approved knowledge base.",
    "approval_rules": {
      "default_timeout_seconds": 900
    }
  }
}
```

---

## War Room UI

The Soul Template Library is accessible from the Soul Viewer/Soul
Inspector in the War Room.

- **Template Browser** - Displays all loaded templates in a filterable
  list. Supports filtering by industry. Each card shows display name,
  industry badge, description, and version.
- **Detail View** - Selecting a template opens a detail pane with the
  full YAML preview (metadata + Soul document), tag list, and author.
- **Behavior Contracts** - Operators can evaluate representative
  capabilities against the active or candidate Soul to confirm expected
  allow, approval, or denial behavior.
- **Editable Fields** - Operators should use the structured
  customization form for local values and policy tuning. Broad YAML
  changes belong in the Soul amendment editor or source-controlled
  template files.
- **Apply as Proposal** - Button in the detail view that opens a
  customization dialog, then submits the template as a Soul Amendment
  Proposal. The operator is redirected to the amendment review flow after
  submission.

---

## Example Operating Patterns

### Finance Compliance Monitor

Use `finance-compliance-monitor` when the agent should scan financial
operations, generate audit-ready evidence, and flag anomalies without
modifying financial records. It allows monitoring actions such as
`scan_transactions`, `generate_compliance_report`, and `flag_anomaly`,
while `file_sar`, `suspend_account`, `modify_compliance_rule`, and
external regulatory escalation require approval. The template includes
financial compliance data boundaries, a T3 regulatory filing rule, a
trust ceiling for compliance connectors, and a kill switch for attempted
compliance evidence deletion or modification.

### Tier 1 Support Agent

Use `tier-1-support-agent` when the agent should answer knowledge-base
questions, route tickets, and perform basic troubleshooting. It permits
capped support actions such as `answer_knowledge_base`, `route_ticket`,
and `issue_capped_refund`, but gates `refund_above_threshold`,
`issue_credit`, and `respond_to_legal_threat`. The expected behavior
contract is that ordinary KB answers are allowed, high-value refunds
require approval, and legal threats escalate instead of receiving an
autonomous response.

### Patient Records Accessor

Use `patient-records-accessor` when the agent needs minimum-necessary
access to patient records. It allows scoped retrieval, record excerpt
summaries, redaction, and access-scope checks. Bulk PHI export, external
PHI disclosure, record modification, and access expansion require T3
approval. The template also includes a PHI data boundary, EHR trust
ceiling, external disclosure rule, and a bulk-export kill switch.

---

## Source Files

| File | Purpose |
| --- | --- |
| `src/core/soul/templates.py` | Template loader, validator, and merge logic |
| `src/core/soul/template_api.py` | FastAPI route handlers for template endpoints |
| `templates/<industry>/*.yaml` | Built-in industry template files |
| `src/warroom/src/pages/SoulInspector.tsx` | War Room Soul Inspector template UI |
| `src/warroom/src/api/soul.ts` | War Room Soul API client |
| `src/warroom/src/types/api.ts` | War Room Soul and template TypeScript types |
