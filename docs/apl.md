# Approval Pattern Learning (APL)

**Feature Flag:** `FEATURE_APPROVAL_LEARNING` (default: `true`, requires `FEATURE_RISK_TIERED_GOVERNANCE`)
**Codebase:** `src/core/governance/approval_learning/`
**Configuration:** `config/approval_learning.yaml`

APL learns from owner approval/denial decisions, detects repeating patterns, and auto-approves actions matching high-confidence rules. It reduces approval fatigue while maintaining safety guardrails.

APL is a deterministic pattern and rule engine, not a statistical ML subsystem. It mines owner decision history for repeatable approval or denial patterns, scores them, and proposes explicit automation rules for operator review.

---

## How It Works

1. **Every owner decision is recorded** — capability, target, risk tier, time, day, scope
2. **Pattern detection runs periodically** — after every 10 decisions (configurable)
3. **Patterns are detected** across single and multiple dimensions
4. **Rules are proposed** from high-confidence patterns for owner review
5. **Owner activates rules** — APL auto-approves (or auto-denies) matching future actions
6. **Safety limits** prevent runaway automation (daily caps, total caps, expiration)

---

## Decision Context

Every approval/denial captures:

| Field | Description |
|-------|-------------|
| `capability` | e.g., "connector.email.send_message" |
| `operation_id` | e.g., "send_message" |
| `connector_id` | e.g., "email" |
| `risk_tier` | T0–T3 |
| `target` | e.g., "bob@client.com" |
| `target_domain` | e.g., "client.com" |
| `target_category` | "verified_recipient", "new_recipient", etc. |
| `scope` | e.g., "channel:#general" |
| `day_of_week` | 0=Monday through 6=Sunday |
| `hour_of_day` | 0–23 |

---

## Pattern Detection

### Single-Dimension Patterns

Group decisions by one dimension and check for consistent approval/denial:

- By capability (supports fnmatch wildcards: `connector.*.send_*`)
- By target domain
- By target category
- By scope
- By time bucket (morning 6–12, afternoon 12–17, evening 17–22, night 22–6, business hours 9–17)
- By day bucket (weekdays Mon–Fri, weekends Sat–Sun)

### Multi-Dimension Patterns

Extend single-dimension patterns by adding more dimensions (up to 3 by default). More specific patterns are preferred.

### Confidence Score

```
confidence = (consistent_decisions / total_observations) × min(1.0, total_observations / 30)
```

Requires both consistency and sufficient observation count.

---

## Automation Rules

Rules are derived from patterns and require owner activation.

### Rule Lifecycle

```
PROPOSED → owner activates → ACTIVE
         → owner declines → cooldown (30 decisions)
ACTIVE → PAUSED → ACTIVE (resume)
ACTIVE → REVOKED (permanent)
```

### Safety Limits

| Limit | Default |
|-------|---------|
| Max active rules | 50 |
| Max auto-decisions per day | 50 per rule |
| Max auto-decisions total | 500 per rule |
| Re-confirmation interval | Every 500 auto-decisions |
| Decline cooldown | 30 decisions |

### Rule Matching

At runtime, when an action needs approval:

1. Collect all active rules matching the context
2. Split into deny rules and approve rules
3. **Deny wins** — if any deny rule matches, return most specific deny
4. Otherwise, return most specific approve rule
5. Specificity tiebreaker: max constraint count among matches

---

## Never-Automate List

Some patterns are excluded from automation regardless of confidence:

```yaml
never_automate:
  - "connector.*.delete_*"
  - "connector.*.admin_*"
```

---

## Configuration

`config/approval_learning.yaml`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `detection.min_observations` | 20 | Minimum decisions to establish a pattern |
| `detection.confidence_threshold` | 0.85 | Minimum confidence (0.0–1.0) |
| `detection.max_pattern_dimensions` | 3 | Max constraints per pattern |
| `detection.analysis_window_days` | 30 | Decision history window |
| `detection.analysis_trigger_interval` | 10 | Decisions since last analysis before trigger |
| `rules.max_active_rules` | 50 | Concurrent active rules |
| `rules.max_auto_decisions_per_day` | 50 | Daily cap per rule |
| `rules.max_auto_decisions_total` | 500 | Total cap per rule |
| `rules.re_confirmation_interval` | 500 | Reconfirm after this many auto-decisions |
| `rules.cooldown_after_decline` | 30 | Decline cooldown |

---

## API Endpoints

**Prefix:** `/api/apl/`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/decisions` | Paginated decision log |
| GET | `/patterns` | Detected patterns with confidence |
| GET | `/rules` | All rules (proposed/active/paused/revoked) |
| POST | `/rules/{id}/activate` | Owner confirms rule |
| POST | `/rules/{id}/decline` | Owner rejects rule |
| POST | `/rules/{id}/pause` | Pause rule |
| POST | `/rules/{id}/resume` | Resume rule |
| POST | `/rules/{id}/revoke` | Permanently revoke |
| GET | `/stats` | Summary statistics |
