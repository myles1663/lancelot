# Receipts

Lancelot's ground truth — every action produces a durable, auditable record. If there's no receipt, it didn't happen.

---

## What Generates Receipts

Every discrete action in the system emits a receipt:

| Source | Examples |
|--------|----------|
| **LLM calls** | Every model invocation — local or cloud, every lane |
| **Tool executions** | Shell commands, file operations, git operations, network requests |
| **Memory edits** | Commits, rollbacks, quarantine promotions, block updates |
| **Scheduler runs** | Job executions, failures, skips, gating decisions |
| **Verification steps** | Verifier pass/fail for each plan step |
| **Governance decisions** | Policy evaluations, approval grants/denials, risk tier assignments |
| **Health transitions** | State changes: healthy → degraded → recovered |
| **Soul operations** | Amendment proposals, approvals, activations |
| **Skill operations** | Install, enable, disable, uninstall, execution |
| **Trust changes** | Graduation proposals, trust score updates, revocations |
| **APL events** | Pattern detection, rule proposals, auto-approvals |

---

## What's in a Receipt

Every receipt follows a consistent schema:

```json
{
  "id": "receipt_abc123",
  "timestamp": "2026-02-14T10:30:00Z",
  "action_type": "tool_exec",
  "action_name": "fs.write",
  "inputs": {
    "path": "/workspace/output.md",
    "content_length": 1234
  },
  "outputs": {
    "status": "success",
    "bytes_written": 1234
  },
  "status": "success",
  "duration_ms": 45,
  "token_count": 0,
  "cognition_tier": "DETERMINISTIC",
  "parent_id": "receipt_parent456",
  "quest_id": "quest_789",
  "error_message": null,
  "metadata": {
    "risk_tier": "T1",
    "policy_decision": "APPROVED",
    "snapshot_id": "snap_001"
  }
}
```

### Field Reference

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique identifier (UUID) |
| `timestamp` | datetime | When the action occurred |
| `action_type` | string | Category: `llm_call`, `tool_exec`, `file_op`, `memory_edit`, `scheduler_run`, `verification`, `governance`, `health_transition`, `soul_op`, `skill_op` |
| `action_name` | string | Specific action: model name, tool capability, job name |
| `inputs` | object | Sanitized request data (secrets redacted, PII stripped) |
| `outputs` | object | Sanitized response data |
| `status` | string | `success` or `failure` |
| `duration_ms` | number | Execution time in milliseconds |
| `token_count` | number | Tokens consumed (0 for non-LLM actions) |
| `cognition_tier` | string | Processing complexity level |
| `parent_id` | string | ID of the parent receipt (for chain linking) |
| `quest_id` | string | ID of the originating goal/quest |
| `error_message` | string | Error details (on failure only) |
| `metadata` | object | Action-specific additional data |

### Cognition Tiers

Receipts are tagged with a cognition tier indicating the level of processing involved:

| Tier | Value | Description |
|------|-------|-------------|
| **DETERMINISTIC** | 0 | No LLM involved — file operations, health checks, policy cache lookups |
| **CLASSIFICATION** | 1 | Simple routing decisions — intent classification, risk tier assignment |
| **PLANNING** | 2 | Multi-step planning and decomposition |
| **SYNTHESIS** | 3 | Complex generation, high-risk reasoning |

### Input/Output Sanitization

Receipt inputs and outputs are sanitized before persistence:

- **Secrets** are redacted (API keys, tokens, passwords replaced with `[REDACTED]`)
- **PII** is stripped via local model redaction
- **Large payloads** are truncated with size noted
- **Binary content** is replaced with type and size metadata

This means receipts are safe to review, export, and share without exposing sensitive data.

---

## Parent-Child Linking

Receipts form a tree structure through `parent_id` and `quest_id` fields. This enables complete decision chain reconstruction.

### Example: A Governed Tool Execution

```
receipt_001  (action_type: governance, action_name: risk_classify)
  → Classified fs.write as T1
  │
  ├─ receipt_002  (action_type: governance, action_name: policy_check)
  │   → Policy cache: APPROVED
  │
  ├─ receipt_003  (action_type: tool_exec, action_name: fs.write)
  │   → File written to /workspace/output.md
  │
  └─ receipt_004  (action_type: verification, action_name: async_verify)
      → Verification: PASS
```

### Example: A Multi-Step Plan

```
receipt_plan_001  (action_type: llm_call, action_name: planner)
  → Generated 4-step plan
  │
  ├─ receipt_step_001  (action_type: llm_call, parent_id: plan_001)
  │   → Step 1 executed (flagship_fast)
  │   └─ receipt_ver_001  (action_type: verification)
  │       → PASS
  │
  ├─ receipt_step_002  (parent_id: plan_001)
  │   → Step 2 executed
  │   └─ receipt_ver_002  → PASS
  │
  ├─ receipt_step_003  (parent_id: plan_001)
  │   → Step 3 executed
  │   └─ receipt_ver_003  → FAIL (retry)
  │   └─ receipt_step_003_retry  (parent_id: plan_001)
  │       → Step 3 retried
  │       └─ receipt_ver_003b  → PASS
  │
  └─ receipt_step_004  (parent_id: plan_001)
      → Step 4 executed
      └─ receipt_ver_004  → PASS
```

By following `parent_id` links, you can reconstruct the complete causal chain from any receipt back to the originating request.

---

## Batch Receipts

For high-volume T0 and T1 actions, receipts are batched together and flushed as a single JSON artifact with a SHA-256 integrity hash.

**When batches flush:**
- When the buffer reaches the configured size (default: 20)
- Before any T2 or T3 action (tier boundary enforcement)
- On task completion

**Batch receipt format:**
```json
{
  "batch_id": "batch_abc123",
  "receipts": [...],
  "count": 20,
  "integrity_hash": "sha256:abc123..."
}
```

The integrity hash covers all receipt inputs and outputs, enabling tamper detection.

---

## Finding and Reading Receipts

### War Room

The **Receipts** panel in the War Room provides:

- **Recent receipts** — chronological list of all recent actions
- **Search** — filter by action type, status, time range, quest ID
- **Drill-down** — click any receipt to see full details
- **Chain view** — follow parent-child links to see the complete action trace
- **Governance trace** — for any receipt, see the risk tier, policy decision, and approval status

### API

```
GET /router/decisions         → Recent routing decisions (which lane, which model, why)
GET /router/stats             → Aggregate routing statistics
```

Receipts are persisted as JSON files in `lancelot_data/receipts/`.

### Tracing a Complete Action

To trace a complete action from request through execution:

1. Find the initial receipt (the user's request or scheduler trigger)
2. Follow `quest_id` to find all receipts in that quest
3. Follow `parent_id` links to see the causal chain
4. Look at `metadata` for governance details (risk tier, policy decisions, approvals)

---

## Receipt Retention

Receipts are append-only — they cannot be modified or deleted at runtime. They accumulate in `lancelot_data/receipts/` over time.

**Storage considerations:**
- Each receipt is a few hundred bytes to a few KB
- A typical day of active use generates hundreds to low thousands of receipts
- Monitor `lancelot_data/receipts/` disk usage as part of regular maintenance

**Archival:** Receipts can be safely backed up or moved to external storage. The live system only reads recent receipts — older receipts are for auditing and incident investigation.

---

## Key Guarantee

The receipt system provides one fundamental guarantee:

> **If there's no receipt, it didn't happen.**

Every code path that performs an action emits a receipt. Missing receipts indicate a system issue (crash, bug) rather than a silent action. This makes receipts the authoritative record of what Lancelot did, when, why, and whether it succeeded.
