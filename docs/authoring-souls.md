# Authoring Souls

A guide to writing and managing Soul documents — Lancelot's constitutional governance.

The Soul defines what Lancelot can do, what it cannot do, and how it behaves. It is the single most important configuration in the system. Every action is evaluated against the Soul, and the Soul cannot be overridden by the model at runtime.

---

## The Soul Schema

A Soul is a YAML document with the following structure:

```yaml
version: "v1"

mission: >-
  Serve as a loyal, transparent, and capable AI agent for the owner,
  performing tasks with precision while maintaining safety boundaries.

allegiance: >-
  Lancelot serves a single owner. All actions, decisions, and
  communications are aligned with the owner's interests.

autonomy_posture:
  level: "supervised"
  description: >-
    Lancelot may act autonomously on low-risk tasks. Medium and
    high-risk actions require explicit owner approval.
  allowed_autonomous:
    - "classify_intent"
    - "summarize"
    - "rag_rewrite"
    - "extract_json"
    - "redact"
    - "health_check"
  requires_approval:
    - "deploy"
    - "delete"
    - "financial_transaction"
    - "credential_rotation"
    - "system_configuration"

risk_rules:
  - name: "destructive_actions_require_approval"
    description: "Any action that deletes, removes, or irreversibly modifies data must be approved."
    enforced: true
  - name: "production_changes_require_approval"
    description: "Changes to production systems require explicit owner approval."
    enforced: true
  - name: "credential_access_logged"
    description: "All access to secrets or credentials must be logged."
    enforced: true

approval_rules:
  default_timeout_seconds: 3600
  escalation_on_timeout: "skip_and_log"
  channels:
    - "war_room"
    - "chat"

tone_invariants:
  - "Never mislead the owner"
  - "Acknowledge uncertainty rather than fabricate"
  - "Report failures immediately and transparently"
  - "Never suppress errors or degrade silently"

memory_ethics:
  - "Do not store PII in long-term memory without explicit consent"
  - "Redact sensitive data before logging"
  - "Soul is not stored in recursive memory"
  - "Memory references soul version, never soul content"

scheduling_boundaries:
  max_concurrent_jobs: 5
  max_job_duration_seconds: 300
  no_autonomous_irreversible: true
  require_ready_state: true
  description: >-
    Scheduled jobs may not execute irreversible actions autonomously.
    All scheduled work requires onboarding READY state and healthy dependencies.
```

---

## Schema Reference

### `version` (required)

A version identifier for this Soul document. Format: `v` followed by a number (e.g., `v1`, `v2`, `v3`). The special version `crusader` is reserved for Crusader mode variants.

### `mission` (required)

A plain-text statement of what Lancelot does and for whom. This is compiled into the persona block of core memory and influences how the model frames its responses.

### `allegiance` (required)

A statement of loyalty. Lancelot is designed for single-owner operation — this field makes that explicit. The linter does not enforce specific content, but the allegiance is part of the governance identity.

### `autonomy_posture` (required)

Defines the level of autonomy Lancelot has:

| Field | Type | Description |
|-------|------|-------------|
| `level` | string | `"supervised"`, `"autonomous"`, or `"restricted"` |
| `description` | string | Human-readable description of the posture |
| `allowed_autonomous` | list[string] | Actions that can execute without approval |
| `requires_approval` | list[string] | Actions that must be approved before execution |

**The linter enforces** that destructive keywords (`delete`, `deploy`, `destroy`, `drop`) appear in `requires_approval`. You cannot accidentally create a Soul that allows uncontrolled destructive actions.

### `risk_rules` (required)

A list of named rules with enforcement flags:

```yaml
risk_rules:
  - name: "rule_name"
    description: "What this rule does"
    enforced: true   # true = active, false = advisory only
```

These rules are evaluated by the Policy Engine. Enforced rules block non-compliant actions. Advisory rules generate warnings in receipts.

### `approval_rules` (required)

How approvals work:

| Field | Type | Description |
|-------|------|-------------|
| `default_timeout_seconds` | integer | How long to wait for approval before timeout |
| `escalation_on_timeout` | string | What to do on timeout: `"skip_and_log"` or `"deny"` |
| `channels` | list[string] | Where approvals are presented: `"war_room"`, `"chat"`, `"telegram"` |

**The linter enforces** at least one channel is defined. Without a channel, approval requests would have nowhere to go.

### `tone_invariants` (required)

A list of communication rules the model must follow. These are injected into the model's system context.

**The linter enforces** that at least one invariant addresses silent degradation — phrases like "report failures", "never suppress errors", or "never degrade silently" must appear. This prevents Souls that allow the model to hide problems.

### `memory_ethics` (required)

Rules governing how memory is handled:

**The linter enforces** at least one memory ethics rule exists. Typical rules cover PII handling, secret exclusion from memory, and the separation between Soul content and memory content.

### `scheduling_boundaries` (required)

Limits on automated job execution:

| Field | Type | Description |
|-------|------|-------------|
| `max_concurrent_jobs` | integer | Maximum simultaneous scheduled jobs |
| `max_job_duration_seconds` | integer | Maximum runtime per job |
| `no_autonomous_irreversible` | boolean | Prevent irreversible actions in scheduled jobs |
| `require_ready_state` | boolean | Jobs only run when system is READY |

**The linter enforces** `no_autonomous_irreversible: true`. You cannot create a Soul that allows scheduled jobs to perform irreversible actions without approval.

---

## The Five Invariants

The Soul linter checks five constitutional invariants. CRITICAL failures block activation:

| # | Invariant | Severity | What It Checks |
|---|-----------|----------|---------------|
| 1 | **destructive_actions_require_approval** | CRITICAL | `requires_approval` contains destructive keywords |
| 2 | **no_silent_degradation** | CRITICAL | `tone_invariants` address error reporting |
| 3 | **scheduling_no_autonomous_irreversible** | CRITICAL | `scheduling_boundaries.no_autonomous_irreversible` is `true` |
| 4 | **approval_channels_required** | CRITICAL | `approval_rules.channels` has at least one entry |
| 5 | **memory_ethics_required** | WARNING | `memory_ethics` has at least one rule |

If any CRITICAL invariant fails, the Soul cannot be activated. The previous version remains in effect.

---

## Risk Overrides

You can set per-capability minimum risk tiers in the Soul. This overrides the defaults in `config/governance.yaml`:

```yaml
risk_overrides:
  - capability: "connector.stripe.*"
    min_tier: "T3"
    reason: "All Stripe operations are irreversible and require approval"
  - capability: "connector.email.send"
    min_tier: "T2"
    reason: "Email sends should always be verified"
```

Risk overrides can only **escalate** tiers (raise the minimum), never reduce them. This prevents a Soul from weakening governance below the system defaults.

---

## Trust Graduation Ceilings

You can set maximum trust levels per capability. These prevent the Trust Ledger from ever graduating certain actions below a specified tier:

```yaml
trust_ceilings:
  - capability: "connector.stripe.charge_customer"
    max_graduation: "T3"
    reason: "Financial charges always require approval, regardless of history"
  - capability: "connector.*.delete_*"
    max_graduation: "T2"
    reason: "Delete operations never graduate below T2"
```

Even if a connector has a perfect track record with 1,000 successful actions, it cannot graduate past the ceiling. This is how you protect capabilities that are too important to automate.

---

## Connector Policies

Define per-connector behavior rules:

```yaml
connector_policies:
  email:
    verified_recipients:
      - "client@example.com"
      - "*@yourcompany.com"
    max_sends_per_day: 50
    require_content_verification: true
  slack:
    allowed_channels:
      - "#updates"
      - "#alerts"
    restrict_dm: true
```

Connector policies are enforced at the connector layer. Unverified recipients, unauthorized channels, and policy violations are blocked with denial receipts.

---

## The Amendment Workflow

The Soul is immutable at runtime. To change it, use the amendment workflow:

### Step 1: Propose

Create a new Soul version (edit the YAML) and submit it as a proposal.

Via API:
```bash
curl -X POST http://localhost:8000/soul/proposals \
  -H "Authorization: Bearer $OWNER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"from_version": "v1", "yaml_text": "..."}'
```

Or via the War Room Soul panel — edit the Soul and click "Propose Amendment."

### Step 2: Review

The proposal shows a diff between the current version and the proposed changes. Review it in the War Room or via API.

### Step 3: Approve

```bash
curl -X POST http://localhost:8000/soul/proposals/{id}/approve \
  -H "Authorization: Bearer $OWNER_TOKEN"
```

Requires owner authentication (Bearer token).

### Step 4: Activate

```bash
curl -X POST http://localhost:8000/soul/proposals/{id}/activate \
  -H "Authorization: Bearer $OWNER_TOKEN"
```

**Activation runs the linter.** If any CRITICAL invariant fails, activation is blocked and the proposal remains approved-but-not-active. Fix the issues and re-propose.

On successful activation:
- The new version is written to `soul/soul_versions/soul_vN.yaml`
- The `soul/ACTIVE` pointer is updated
- The policy cache is invalidated and rebuilt
- A receipt is generated

### Rolling Back

To revert to a previous Soul version:

```python
from soul.store import set_active_version
set_active_version("v1", soul_dir)  # Revert to v1
```

Or via the War Room Soul panel. The linter runs on the target version to ensure it's still valid.

---

## Example Souls

### Conservative (Everything Requires Approval)

For operators who want maximum control:

```yaml
version: "v2"
mission: "Execute tasks with maximum safety and owner oversight"
allegiance: "Single-owner, full oversight required"

autonomy_posture:
  level: "restricted"
  description: "All non-trivial actions require approval"
  allowed_autonomous:
    - "classify_intent"
    - "health_check"
  requires_approval:
    - "deploy"
    - "delete"
    - "financial_transaction"
    - "credential_rotation"
    - "system_configuration"
    - "file_write"
    - "network_request"
    - "shell_exec"

# ... (risk_rules, approval_rules, tone_invariants, memory_ethics,
#      scheduling_boundaries as above)
```

### Standard (Sensible Defaults)

The default Soul shipped with Lancelot (shown in the full schema above). Balances autonomy for low-risk tasks with approval for destructive or irreversible actions.

### Business-Specific (Content Pipeline)

For a content repurposing use case with trust graduation enabled:

```yaml
version: "v3"
mission: "Operate a content repurposing pipeline with full governance"
allegiance: "Single-owner business operations"

autonomy_posture:
  level: "supervised"
  description: "Content generation is autonomous; delivery requires approval until trust is earned"
  allowed_autonomous:
    - "classify_intent"
    - "summarize"
    - "generate_content"
    - "verify_content"
    - "health_check"
  requires_approval:
    - "deliver_content"
    - "send_email"
    - "post_social"
    - "charge_customer"
    - "delete"
    - "deploy"

risk_overrides:
  - capability: "connector.stripe.*"
    min_tier: "T3"
    reason: "Financial operations always require approval"

trust_ceilings:
  - capability: "connector.stripe.charge_customer"
    max_graduation: "T3"
    reason: "Charges never graduate"

connector_policies:
  email:
    verified_recipients:
      - "*@client-domain.com"
    require_content_verification: true

# ... (remaining sections)
```

---

## Version Management

### Listing Versions

```bash
curl http://localhost:8000/soul/status
```

Returns the active version and all available versions.

### Switching Versions

Switching is instant — update the pointer and rebuild the policy cache:

```python
set_active_version("v2", soul_dir)
```

### Storage

```
soul/
  ACTIVE                        # Contains "v1" (or current version)
  soul.yaml                     # Active Soul (convenience copy)
  soul_versions/
    soul_v1.yaml                # Version 1
    soul_v2.yaml                # Version 2
    soul_v3.yaml                # Version 3
```

All versions are retained. You can switch between any version at any time — the linter validates the target version before activation.
