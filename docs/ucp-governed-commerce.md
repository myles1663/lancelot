# UCP Governed Commerce Connector Specification

**Document Version:** 0.1
**Status:** Proposed; Phase 1 foundation implemented behind disabled connector config
**Last Updated:** 2026-05-20
**Owner:** Lancelot governance / connectors
**Related Docs:** [Connectors](connectors.md), [Developing Connectors](developing-connectors.md), [Governance](governance.md)

---

## 1. Executive Summary

UCP is the governed commerce layer for Lancelot agents.

Agents may discover, price, quote, prepare, or request commerce actions, but they must not directly commit spend, create subscriptions, purchase goods or services, book paid resources, renew contracts, cancel paid services, or bind the operator to external terms without governance approval.

The UCP connector is the execution rail. Existing Lancelot governance remains the authority layer.

### Core Design Rule

**UCP does not own approval. Governance owns approval. UCP owns commerce execution.**

UCP commerce intents are converted into normal governed action proposals. They flow through the existing risk classifier, policy engine, Soul constraints, approval queue, operator identity checks, and receipt ledger. UCP executes only after governance returns an approved executable decision.

---

## 2. Goals

- Provide a first-class governed connector boundary for agent-initiated commerce.
- Keep all commerce approvals inside the existing governance approval queue.
- Make spend, recurring commitments, vendor terms, reversibility, and budget impact visible before execution.
- Produce durable receipts for proposal, policy decision, approval or rejection, execution, vendor response, and rollback or cancellation attempts.
- Support read/propose-only operation before enabling execution.
- Expose truthful War Room status for configured, authenticated, degraded, disabled, and kill-switched states.
- Support enterprise controls: budgets, vendor allowlists/blocklists, category policy, spend ceilings, approval thresholds, and audit export.

---

## 3. Non-Goals

- UCP does not replace the Lancelot approval queue.
- UCP does not create a parallel commerce approval system.
- UCP does not allow agents to autonomously commit spend by default.
- UCP does not store card numbers, bank details, or raw payment secrets in code, memory, logs, receipts, or connector config.
- UCP does not bypass connector domain allowlists, vault credential handling, or risk-tier governance.
- UCP does not guarantee rollback when a vendor action is externally irreversible.

---

## 4. Authority Boundary

| Layer | Responsibility |
|-------|----------------|
| Agent | Creates a commerce intent or asks for quote/status data |
| UCP Connector | Normalizes commerce operations and executes approved vendor/API actions |
| Governance | Classifies risk, enforces Soul/policy/budget rules, owns approval decisions |
| Existing Approval Queue | Presents pending commerce proposals to the operator |
| Receipt Ledger | Records all commerce lifecycle events |
| Trust Ledger | Tracks connector/capability outcomes and possible trust changes, subject to Soul ceilings |

Commerce is treated as a high-authority action domain. Even if individual discovery operations are low-risk, any action that may commit spend, accept terms, change billing, provision paid resources, or cancel paid resources must be gated by governance.

### Existing Gateway UCP Surface

Lancelot also has a legacy gateway UCP integration for merchant discovery, product search, and pending transaction confirmation under `/ucp/*`. That surface remains the merchant-level integration path. This specification defines the connector-registry commerce intent boundary used to expose UCP as a governed connector in War Room and to prepare commerce actions for the unified approval queue.

The two paths must not diverge on authority: gateway UCP transactions and connector-registry UCP executions both remain subject to governance, audit logging, credential controls, and operator approval for spend-committing actions.

---

## 5. Commerce Intent Model

Every UCP operation starts with a structured `CommerceIntent`.

```yaml
intent_id: "uuid"
domain: "commerce"
connector_id: "ucp"
operation: "purchase"
requested_by:
  actor_type: "agent"
  agent_id: "hive-agent-123"
  task_id: "task-456"
vendor:
  name: "Example Vendor"
  external_id: "vendor_abc"
  domain: "api.vendor.example"
item:
  name: "Service Plan"
  sku: "plan_pro"
  quantity: 1
financial:
  amount: "49.00"
  currency: "USD"
  recurring: true
  recurrence_interval: "month"
  budget_code: "ops.software"
commitment:
  action_type: "subscription"
  term_summary: "Monthly plan, cancel anytime"
  terms_url: "https://vendor.example/terms"
  reversible: true
  cancellation_window: "P30D"
risk:
  declared_default_tier: "T3"
  reason: "Recurring paid subscription"
expires_at: "2026-05-19T18:00:00Z"
metadata:
  quote_id: "quote_123"
  source_url: "https://vendor.example/checkout"
```

### Required Fields

| Field | Purpose |
|-------|---------|
| `intent_id` | Stable idempotency key across proposal and execution |
| `domain` | Always `commerce` for UCP-governed commerce |
| `connector_id` | `ucp` |
| `operation` | Commerce operation type |
| `requested_by` | Agent/operator provenance |
| `vendor` | Vendor identity and target domain |
| `financial` | Amount, currency, recurrence, and budget attribution |
| `commitment` | Legal/financial commitment detail |
| `risk` | Declared default tier and human-readable reason |
| `expires_at` | Quote/proposal expiration, if applicable |

---

## 6. Operation Taxonomy

| Operation | Description | Default Tier |
|-----------|-------------|--------------|
| `vendor.search` | Search approved vendor catalog or marketplace metadata | T1 |
| `quote.request` | Request price/availability without commitment | T2 |
| `quote.refresh` | Refresh an existing quote without commitment | T2 |
| `purchase` | One-time paid purchase | T3 |
| `subscription.create` | Create recurring paid commitment | T3 |
| `subscription.change` | Upgrade, downgrade, or alter renewal/payment terms | T3 |
| `subscription.cancel` | Cancel recurring paid service | T3 |
| `booking.create` | Book a paid resource or appointment | T3 |
| `procurement.request` | Submit procurement request to external system | T3 |
| `refund.request` | Request refund or credit | T3 |
| `vendor.onboard` | Add or authorize a new vendor | T3 |
| `payment_method.attach` | Attach, rotate, or select payment method | T3 |

Scope or Soul rules may escalate any operation. Unknown UCP operations must fail closed as T3.

---

## 7. Approval Queue Integration

UCP proposals are standard governance proposals with commerce-specific detail.

The existing approval queue must display enough context for an operator to make a decision without opening raw payloads:

- Vendor and domain
- Operation type
- One-time amount and currency
- Recurring amount and interval
- Budget code or cost center
- Item/service name, SKU, and quantity
- Quote id and expiration time
- Terms summary and terms URL
- Reversibility and cancellation window
- Policy decision trace
- Risk tier and escalation reason
- Requesting agent/task
- Required credential/provider
- Expected execution endpoint

### Approval Decision Contract

An approval grants permission for exactly one executable commerce intent:

- `intent_id` must match.
- Material financial fields must match.
- Vendor and operation must match.
- Expired quotes cannot execute.
- Execution is idempotent by `intent_id`.
- Any mutation after approval requires a new proposal.

---

## 8. Governance Rules

### Required Gates

1. Validate intent schema.
2. Validate connector enabled and not kill-switched.
3. Validate credentials and provider health.
4. Classify risk tier.
5. Apply Soul constraints and commerce policy.
6. Apply budget and vendor policy.
7. Create or update governance proposal.
8. Require operator approval for T3 or policy-mandated approval.
9. Execute only against the approved immutable intent.
10. Write execution and outcome receipts.

### Commerce Policy Controls

Policy should support:

- Global spend ceilings.
- Per-agent spend ceilings.
- Per-vendor spend ceilings.
- Per-budget-code spend ceilings.
- One-time vs recurring thresholds.
- Vendor allowlist/blocklist.
- Category allowlist/blocklist.
- New vendor approval requirement.
- Payment method selection restrictions.
- Maximum quote age.
- Maximum recurring term.
- Mandatory human approval for external legal/financial commitment.

### Soul Invariants

The active Soul should be able to declare commerce ceilings:

```yaml
connector_policies:
  ucp:
    enabled: true
    max_autonomous_tier: "T2"
    requires_approval:
      - "connector.ucp.purchase"
      - "connector.ucp.subscription.create"
      - "connector.ucp.subscription.change"
      - "connector.ucp.payment_method.attach"
    trust_ceiling: "T3"
    vendor_policy:
      new_vendors_require_approval: true
      blocked_categories:
        - "weapons"
        - "gambling"
```

Recommended default: UCP spend-committing operations remain T3 regardless of trust history.

---

## 9. Receipt Model

UCP must write durable receipts for:

- Intent received.
- Quote requested.
- Quote response received.
- Proposal created.
- Policy approved/blocked.
- Operator approved/rejected.
- Execution started.
- Execution succeeded/failed.
- Vendor response summarized.
- Rollback/cancel attempted.
- Rollback/cancel succeeded/failed/not-supported.
- Connector degraded/recovered.
- Kill switch toggled.

Receipts must redact secrets and payment details. Raw payment instruments must never be persisted in receipts. Vendor responses should be summarized and bounded, with hashes or external ids retained for audit.

### Commerce Receipt Fields

| Field | Purpose |
|-------|---------|
| `intent_id` | Links lifecycle events |
| `proposal_id` | Links to governance approval |
| `connector_id` | `ucp` |
| `operation` | Commerce operation |
| `vendor_id` / `vendor_name` | Vendor attribution |
| `amount` / `currency` | Financial audit |
| `recurring` / `interval` | Commitment audit |
| `budget_code` | Budget attribution |
| `operator_id` | Required for human approval/rejection |
| `agent_id` / `task_id` | Request provenance |
| `risk_tier` | Governance tier |
| `policy_trace_id` | Decision trace |
| `external_reference` | Vendor quote/order/subscription id |
| `reversibility` | `reversible`, `limited`, or `not_reversible` |

---

## 10. Connector Manifest Sketch

```python
ConnectorManifest(
    id="ucp",
    name="UCP Governed Commerce",
    version="0.1.0",
    author="Lancelot",
    source="first-party",
    description="Governed commerce connector for agent-initiated quotes, purchases, subscriptions, bookings, and procurement actions.",
    target_domains=[
        "api.ucp.example",
        "checkout.ucp.example"
    ],
    required_credentials=[
        CredentialSpec(
            name="ucp_api_token",
            type="api_key",
            vault_key="UCP_API_TOKEN",
            required=True,
            scopes=[
                "quote:read",
                "order:write",
                "subscription:write"
            ],
        )
    ],
    data_reads=[
        "commerce.quotes",
        "commerce.catalog",
        "commerce.vendor_status"
    ],
    data_writes=[
        "commerce.proposals",
        "commerce.orders",
        "commerce.subscriptions",
        "commerce.cancellations"
    ],
    does_not_access=[
        "raw_payment_card_data",
        "unscoped_memory",
        "soul"
    ],
)
```

Actual target domains and scopes should be configured by the operator. The connector must not accept arbitrary runtime domains from agent payloads unless they pass configured allowlist policy.

---

## 11. War Room UX

UCP should appear in the existing Connectors tab as a high-authority connector:

- Provider status: configured, authenticated, degraded, disabled, kill-switched.
- Credential status without revealing secrets.
- Allowed vendors/domains.
- Spend policy summary.
- Recent commerce receipts.
- Last quote, approval, execution, failure.
- Budget consumption summary.
- Link/filter into the existing Approval Queue for pending UCP proposals.

The Approval Queue should render a commerce-specific detail panel when `domain=commerce` or `connector_id=ucp`. This is not a separate queue; it is a richer view inside the unified governance approval surface.

---

## 12. Feature Flags and Kill Switches

Recommended flags:

| Flag | Default | Purpose |
|------|---------|---------|
| `FEATURE_CONNECTOR_UCP` | `false` | Enables UCP connector registration |
| `FEATURE_UCP_EXECUTION` | `false` | Allows approved commerce execution |
| `FEATURE_UCP_QUOTES` | `true` when connector enabled | Allows read/propose quote operations |

Recommended kill switches:

| Switch | Effect |
|--------|--------|
| `connector.ucp.enabled` | Disables all UCP operations |
| `connector.ucp.execution` | Allows quote/propose but blocks execution |
| `connector.ucp.recurring_commitments` | Blocks subscription create/change |
| `connector.ucp.new_vendor` | Blocks vendor onboarding or first-time vendor spend |

Disabled or degraded states must be visible in War Room and must produce receipts when state changes.

---

## 13. Security Requirements

- UCP connector inputs are untrusted.
- Vendor responses are untrusted.
- Agent-provided vendor names, domains, URLs, quote ids, amounts, and terms are untrusted until verified.
- Execution must use an immutable approved intent, not a mutable client payload.
- Credentials must come from vault-backed connector credential handling.
- Payment instruments must be tokenized by the external commerce provider or stored outside Lancelot.
- Terms URLs must be domain-validated and rendered as context, not executed.
- Amounts must use decimal-safe handling, never floating-point math.
- Currency must be explicit ISO 4217.
- Recurring commitments must be displayed as recurring, not only as first-period cost.
- External idempotency keys must derive from `intent_id`.
- Failed receipt writes must fail closed for execution results that would otherwise appear unaudited.

---

## 14. Rollout Plan

### Phase 1: Spec and Policy

- Add this spec.
- Define commerce intent schema.
- Define commerce policy config shape.
- Add tests for schema validation and risk classification.

### Phase 2: Read/Propose Only

- Register UCP as disabled-by-default first-party connector.
- Support catalog/quote operations.
- Convert quote results into governance proposals.
- Add commerce detail rendering to the existing approval queue.

### Phase 3: Approved Execution

- Enable execution behind `FEATURE_UCP_EXECUTION`.
- Execute only approved immutable intents.
- Write proposal, approval, execution, and vendor outcome receipts.
- Add idempotency and expired-quote protections.

### Phase 4: Enterprise Controls

- Add budget ledger.
- Add vendor policy editor.
- Add compliance export fields.
- Add APL guardrails that prevent automatic approval for spend-committing operations unless explicitly allowed by Soul and policy.

---

## 15. Test Plan

Minimum tests:

- Intent schema rejects missing amount, currency, vendor, operation, or commitment data.
- Unknown operation fails closed as T3.
- Purchase/subscription/payment operations require approval.
- UCP execution is blocked when connector is disabled.
- UCP execution is blocked when execution kill switch is active.
- UCP execution is blocked without matching approved proposal.
- UCP execution is blocked when approved proposal has expired.
- UCP execution is blocked when amount/vendor/operation changed after approval.
- Quote-only operation can create a proposal without committing spend.
- Receipts are written for proposal, approval, rejection, execution success, execution failure, and rollback/cancel attempt.
- Raw secrets and payment details are redacted from receipts.
- War Room approval detail renders commerce metadata without exposing credentials.
- Policy blocks disallowed vendor/category/budget.
- Budget ceilings are enforced with decimal-safe arithmetic.

---

## 16. Acceptance Criteria

UCP is acceptable for production only when:

- All spend-committing operations route through the existing governance approval queue.
- No separate commerce approval queue exists.
- Execution cannot occur without an approved immutable governance decision.
- Every operation has durable receipts.
- Kill switches and degraded states are visible and enforced.
- Operator-facing proposal cards show vendor, amount, recurrence, budget, terms, reversibility, risk, and requester.
- Connector credentials are vault-backed.
- Unknown or malformed operations fail closed.
- Tests cover approval, denial, disabled, degraded, expired, tampered, and receipt-failure cases.
