# Time-Travel Debugging

**Feature flag:** `FEATURE_TIME_TRAVEL` (default: false)

Time-Travel Debugging allows operators to inspect, replay, and fork past quest executions under full Soul governance. Every operation produces auditable receipts and respects the Soul's `fork_permissions` block.

---

## Concepts

### Three Modes

| Mode | Description | Soul Gate | Receipts |
|------|-------------|-----------|----------|
| **INSPECT** | Read-only state viewing at any receipt | Always allowed | `TIME_TRAVEL_INSPECT` |
| **REPLAY** | Re-execute unchanged quest under current Soul, new quest_id | `allow_fork` required | `QUEST_REPLAYED` |
| **FORK** | Modify inputs and re-execute, Soul-validated field restrictions | `allow_fork` + field validation | `QUEST_FORKED` |

### Fork Creation Pipeline (8 Stages)

1. **Receipt Selection** — validate source quest exists, retrieve receipt chain
2. **State Modification** — validate requested field changes
3. **Soul Validation** — evaluate against `fork_permissions` (allow_fork, modifiable_fields, prohibited_modifications)
4. **Risk Reclassification** — re-tier under current Soul (uses max source tier)
5. **T3 Approval Gate** — request and check trust tier approval
6. **Fork Quest Creation** — mint new quest_id, link to source
7. **Governed Execution** — actual re-execution (handled by orchestrator)
8. **Fork Receipt** — emit `QUEST_FORKED` receipt

---

## Soul Configuration

Add `fork_permissions` to your Soul YAML:

```yaml
fork_permissions:
  allow_fork: true
  require_approval_tier: 3
  modifiable_fields:
    - "inputs.query"
    - "inputs.model_tier"
  prohibited_modifications:
    - "operator_id"
    - "session_id"
    - "quest_id"
    - "timestamp"
    - "id"
```

### Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `allow_fork` | bool | `false` | Master switch for fork/replay operations |
| `require_approval_tier` | int (0-3) | `3` | Minimum trust tier for approval |
| `modifiable_fields` | list[str] | `[]` | Receipt fields that may be changed in a fork. Empty = replay only |
| `prohibited_modifications` | list[str] | identity/provenance fields | Fields that can NEVER be modified — enforced architecturally |

**Key rules:**
- `allow_fork: false` (default) blocks all fork and replay operations
- INSPECT is always allowed regardless of `allow_fork`
- `prohibited_modifications` are hardcoded defaults that protect audit chain integrity
- `modifiable_fields` supports prefix matching: `"inputs"` allows `"inputs.query"`

---

## Receipt Types

| Type | Tier | Identity Required | Description |
|------|------|-------------------|-------------|
| `QUEST_FORKED` | T3 | Yes | Fork operation completed |
| `QUEST_REPLAYED` | T2 | Yes | Replay operation completed |
| `TIME_TRAVEL_INSPECT` | T0 | Yes | Read-only inspection performed |
| `T3_FORK_APPROVAL_REQUEST` | T3 | No (SYSTEM) | Approval requested |
| `T3_FORK_APPROVED` | T3 | Yes | Approval granted |
| `T3_FORK_REJECTED` | T3 | Yes | Approval denied |
| `FORK_SOUL_REJECTED` | T0 | No | Soul denied the operation |

---

## API Endpoints

Base path: `/api/timetravel`

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/status` | Subsystem status (enabled, fork_allowed, approval tier) |
| `GET` | `/quest/{quest_id}/receipts` | Full receipt chain for a quest |
| `GET` | `/receipt/{receipt_id}/snapshot` | Governance state snapshot at a receipt |
| `POST` | `/inspect` | Create read-only inspection |
| `POST` | `/replay` | Replay a quest (requires identity) |
| `POST` | `/fork` | Fork a quest with modifications (requires identity) |

### Headers

- `X-Operator-ID` — required for replay and fork operations
- `X-Session-ID` — optional session identifier

### Example: Inspect

```bash
curl -X POST http://localhost:8000/api/timetravel/inspect \
  -H "Content-Type: application/json" \
  -d '{"receipt_id": "abc123"}'
```

### Example: Fork

```bash
curl -X POST http://localhost:8000/api/timetravel/fork \
  -H "Content-Type: application/json" \
  -H "X-Operator-ID: operator-uuid" \
  -d '{
    "source_quest_id": "quest-uuid",
    "modifications": {"inputs.query": "new prompt"}
  }'
```

---

## War Room UI

The **Time-Travel Debugger** page is accessible from the DEBUGGING section in the sidebar.

### Components

1. **Quest Search** — Enter a Quest ID to load its receipt DAG
2. **DAG Navigator** — SVG-based receipt chain visualization:
   - Color-coded nodes (green=success, red=failure, amber=pending)
   - Parent-child edges showing receipt relationships
   - Click a node to inspect its state
3. **Receipt Detail** — Expandable inputs/outputs for the selected receipt
4. **State Inspector** — Governance snapshot at the selected receipt:
   - Soul version, trust tier, kill switch state
   - Cost data (tokens, receipts, duration)
   - Governance context flags (Soul, APL, Trust active)
5. **Fork/Replay Modal** — Mode toggle, JSON modification editor, approval tier display

---

## State Snapshot

The `StateSnapshotReader` reconstructs governance context at any point in time:

- **Soul version** — determined from most recent `SOUL_UPDATED` receipt
- **Kill switches** — replayed from `KILL_SWITCH_ISSUED`/`KILL_SWITCH_LIFTED` receipts
- **Trust tier** — current effective tier from trust ledger
- **Cost data** — aggregated token/receipt/duration stats
- **Feature flags** — current flag state snapshot
- **Receipt chain** — all quest receipts up to the snapshot point

---

## Architecture Notes

- Fork/replay operations always run under the **current Soul**, never the historical one
- Each fork/replay creates a **new quest_id** — never reuses the source quest_id
- The T3 approval gate uses synchronous trust tier checking (async War Room approval planned)
- `FORK_SOUL_REJECTED` receipts are emitted by the system, not the operator
- Fork operations are always classified as T3 (Synthesis tier)
- Cost accounting is isolated per fork quest_id
