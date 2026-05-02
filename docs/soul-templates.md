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

| Name                        | Display Name                 | Industry | Description                                                  |
|-----------------------------|------------------------------|----------|--------------------------------------------------------------|
| `finance-reporting-analyst` | Finance Reporting Analyst    | finance  | FP&A report generation, regulatory filing prep               |
| `finance-compliance-monitor`| Finance Compliance Monitor   | finance  | Regulatory compliance monitoring, audit evidence collection   |
| `finance-internal-ops`      | Finance Internal Operations  | finance  | Invoice processing, expense routing, payment scheduling      |

---

## Template Validation

Every template must pass the **7 Soul linter invariants** before it can
be loaded or applied. Templates that fail validation are rejected at
load time and cannot appear in the template browser.

| #  | Invariant                              | Condition   |
|----|----------------------------------------|-------------|
| 1  | `destructive_actions_require_approval` | Always      |
| 2  | `no_silent_degradation`                | Always      |
| 3  | `scheduling_no_autonomous_irreversible`| Always      |
| 4  | `approval_channels_required`           | Always      |
| 5  | `memory_ethics_required`               | Always      |

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

The merged document must still pass all 7 linter invariants. If the
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
