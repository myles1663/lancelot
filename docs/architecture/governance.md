# Architecture: Risk-Tiered Governance (vNext4)

## Overview

Risk-tiered governance classifies every action Lancelot takes into one of four risk tiers (T0-T3) and applies proportional governance overhead. Low-risk actions (T0) execute near-instantly via precomputed policy cache, while irreversible actions (T3) require full policy evaluation, commander approval, and synchronous verification.

## Risk Tiers

| Tier | Name | Examples | Governance |
|------|------|----------|------------|
| T0 | Inert | fs.read, fs.list, git.status, git.log, git.diff, memory.read | Policy cache lookup, batch receipt |
| T1 | Reversible | fs.write, git.commit, git.branch, memory.write | Policy cache, rollback snapshot, async verification |
| T2 | Controlled | shell.exec | Full sync verify, tier boundary flush |
| T3 | Irreversible | net.post, net.delete, docker.run, deploy.* | Approval gate, full sync verify, boundary flush |

Unknown capabilities default to T3 (fail-safe).

## Execution Pipelines

### T0 Pipeline (Inert)
```
Policy Cache Lookup → Execute → Batch Receipt
```
- O(1) policy decision from precomputed cache
- Receipts batched together and flushed as group
- No verification overhead

### T1 Pipeline (Reversible)
```
Policy Cache Lookup → Snapshot → Execute → Async Verify → Receipt
```
- Pre-execution snapshot captures current state
- Action executes optimistically (no wait)
- Verification runs in background queue
- On failure: automatic rollback via snapshot

### T2 Pipeline (Controlled)
```
[Flush Batch + Drain Async Queue] → Execute → Sync Verify → Receipt
```
- Boundary enforcement: all pending T0/T1 work must complete first
- If async drain has failures, T2 is blocked
- Synchronous verification (blocks until complete)

### T3 Pipeline (Irreversible)
```
[Flush Batch + Drain Async Queue] → Approval Gate → Execute → Sync Verify → Receipt
```
- Same boundary enforcement as T2
- Commander approval required before execution
- Synchronous verification post-execution

## Modules

### Risk Classifier (`governance/risk_classifier.py`)
- Classifies capabilities into risk tiers using config defaults
- Scope escalation: certain scopes upgrade tier (e.g., fs.write outside workspace → T3)
- Pattern escalation: file patterns upgrade tier (e.g., *.env files → T3)
- Soul escalation: Soul contract can override any action to higher tier
- Unknown capabilities always default to T3

### Policy Cache (`governance/policy_cache.py`)
- Boot-time compilation of allow/deny decisions for T0/T1 actions
- O(1) lookup at runtime via `(capability, scope, pattern)` tuple
- Validates Soul version on each lookup (optional)
- Invalidates on Soul changes
- Never caches T2/T3 decisions

### Async Verification Queue (`governance/async_verifier.py`)
- Background queue for T1 action verification
- Sync fallback when queue is full (configurable)
- `drain()` for tier boundary enforcement
- Automatic `rollback_action` execution on verification failure

### Rollback Manager (`governance/rollback.py`)
- Pre-execution snapshots for T1 actions
- fs.write: captures file content or notes non-existence
- Rollback restores original content or removes new files
- Double rollback is idempotent (no-op on second call)

### Intent Template Registry (`governance/intent_templates.py`)
- Caches known-good execution plan skeletons
- Learned from successful executions
- Promoted after configurable success threshold
- Safety: templates cannot contain T2+ actions
- Invalidated on Soul changes

### Batch Receipt Buffer (`governance/batch_receipts.py`)
- Collects T0/T1 receipts and flushes as single JSON artifact
- Auto-flush at configurable buffer size
- Tier boundary flush: flushes before T2/T3 actions
- SHA-256 hashing of inputs/outputs for integrity

## Configuration

### `config/governance.yaml`
- Risk classification defaults (14 capabilities)
- 3 scope escalation rules
- Policy cache, async verification, intent templates, batch receipts configs

### Feature Flags
All flags default to `false` and are gated behind a master switch:

| Flag | Purpose |
|------|---------|
| `FEATURE_RISK_TIERED_GOVERNANCE` | Master switch for entire governance system |
| `FEATURE_POLICY_CACHE` | Boot-time policy compilation |
| `FEATURE_ASYNC_VERIFICATION` | Async verify for T1 actions |
| `FEATURE_INTENT_TEMPLATES` | Cached plan templates |
| `FEATURE_BATCH_RECEIPTS` | Batched receipt emission |

When the master switch is off, `execute_plan()` uses the legacy synchronous path with zero behavioral change.

## Tier Boundary Enforcement

The critical safety invariant: before ANY T2/T3 action:
1. All pending batch receipts are flushed to disk
2. All pending async verifications are completed
3. Any verification failures trigger rollback

This ensures no "pipeline debt" crosses the risk boundary.

## Module Map

```
src/core/governance/
  __init__.py              Module scaffold
  models.py                RiskTier, VerificationStatus, data types
  config.py                Pydantic config loader for governance.yaml
  risk_classifier.py       RiskClassifier with escalation logic
  policy_cache.py          Precomputed policy decisions
  async_verifier.py        AsyncVerificationQueue
  rollback.py              RollbackManager with snapshots
  intent_templates.py      IntentTemplateRegistry
  batch_receipts.py        BatchReceiptBuffer
  war_room_panel.py        Streamlit governance panel
```
