# Trust Ledger

**Feature Flag:** `FEATURE_TRUST_LEDGER` (default: `false`, requires `FEATURE_RISK_TIERED_GOVERNANCE`)
**Codebase:** `src/core/governance/trust_ledger.py`, `trust_models.py`, `trust_api.py`
**Configuration:** `config/trust_graduation.yaml`

The Trust Ledger tracks per-capability execution history and proposes tier graduations when consecutive success thresholds are met. It enables progressive trust — capabilities start at their default risk tier and can graduate to lower tiers through demonstrated reliability.

---

## How It Works

1. Every governed action records `success` or `failure` against a capability
2. Consecutive successes accumulate toward graduation thresholds
3. When threshold is met, a `GraduationProposal` is created for owner review
4. Owner approves → tier lowers by one level (less governance overhead)
5. Any failure → trust revoked, tier resets to default, cooldown applied

---

## Risk Tiers

| Tier | Name | Governance Overhead |
|------|------|-------------------|
| **T0** | Inert | Precomputed policy lookup, batch receipt |
| **T1** | Reversible | Rollback snapshot, async verification |
| **T2** | Controlled | Sync verification, tier boundary flush |
| **T3** | Irreversible | Approval gate, sync verification |

---

## Graduation Thresholds

| Transition | Consecutive Successes Required |
|-----------|-------------------------------|
| T3 → T2 | 50 |
| T2 → T1 | 100 |
| T1 → T0 | 200 |

---

## Trust Record

Each capability+scope combination has a `TrustRecord`:

| Field | Description |
|-------|-------------|
| `capability` | Capability ID (e.g., "connector.email.send_message") |
| `scope` | Execution scope (e.g., "default", "external") |
| `current_tier` | Current risk tier (may be lower than default if graduated) |
| `default_tier` | Baseline tier |
| `soul_minimum_tier` | Lowest tier Soul allows (graduation floor) |
| `consecutive_successes` | Counter toward next graduation |
| `total_successes` / `total_failures` / `total_rollbacks` | Lifetime metrics |
| `graduation_history` | List of tier transitions |
| `pending_proposal` | Current graduation proposal (if any) |
| `cooldown_remaining` | Blocks graduation after denial/revocation |

---

## Graduation Lifecycle

```
50+ consecutive successes
  → GraduationProposal created (status: pending)
    → Owner approves → Tier lowered by 1, cooldown reset
    → Owner denies → Cooldown set (50 decisions), no change
```

---

## Revocation

Any failure or rollback immediately revokes graduated trust:

| Trigger | Action |
|---------|--------|
| **Failure** | Reset to default tier, cooldown = 25 |
| **Rollback** | Reset to `min(default_tier + 1, T3)`, cooldown = 25 |

Consecutive success counter resets to 0 on any failure.

---

## Cooldowns

| Event | Cooldown (decision count) |
|-------|--------------------------|
| Graduation denied | 50 |
| Trust revoked | 25 |

During cooldown, no graduation proposals are created. Each manual decision (approve/deny on any capability) decrements cooldowns by 1.

---

## Soul Minimum Tier

The Soul can set a floor for graduation. If `soul_minimum_tier = T1`, the capability can never graduate below T1 regardless of success history.

---

## API Endpoints

**Prefix:** `/api/trust/`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/records` | All trust records |
| GET | `/proposals` | Pending graduation proposals |
| GET | `/timeline` | Full graduation event history |
| POST | `/proposals/{id}/approve` | Approve graduation |
| POST | `/proposals/{id}/decline` | Decline graduation |
