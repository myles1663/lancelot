# Time-Travel Debugging

**Feature flag:** `FEATURE_TIME_TRAVEL` (default: `false`)

Time-Travel Debugging allows operators to inspect, replay, and fork past quest executions under full Soul governance. Every operation produces auditable receipts and respects the Soul's `fork_permissions` block.

---

## Concepts

### Three Modes

| Mode | Description | Soul Gate | Receipts |
|------|-------------|-----------|----------|
| **INSPECT** | Read-only state viewing at any receipt | Always allowed | `TIME_TRAVEL_INSPECT` |
| **REPLAY** | Re-execute an unchanged quest under the current Soul with a new `quest_id` | `allow_fork` required | `QUEST_REPLAYED` |
| **FORK** | Modify inputs and re-execute with Soul-validated field restrictions | `allow_fork` plus field validation | `QUEST_FORKED` |

### Fork Creation Pipeline

1. **Receipt Selection** - validate the source quest exists and retrieve its receipt chain.
2. **State Modification** - validate requested field changes.
3. **Soul Validation** - evaluate `fork_permissions` (`allow_fork`, `modifiable_fields`, `prohibited_modifications`).
4. **Risk Reclassification** - re-tier under the current Soul using the max source tier.
5. **T3 Approval Gate** - request and check the aggregate Trust Ledger approval tier.
6. **Fork Quest Creation** - mint a new `quest_id` and link it to the source.
7. **Governed Execution** - re-execute through the TaskRun pipeline using the live runtime Soul.
8. **Fork Receipt** - emit `QUEST_FORKED`.

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
| `allow_fork` | bool | `false` | Master switch for fork and replay operations |
| `require_approval_tier` | int (0-3) | `3` | Minimum aggregate Trust Ledger approval tier required for approval |
| `modifiable_fields` | list[str] | `[]` | Receipt fields that may be changed in a fork. Empty means replay only |
| `prohibited_modifications` | list[str] | identity and provenance fields | Fields that can never be modified |

**Key rules**

- `allow_fork: false` blocks all fork and replay operations.
- INSPECT is always allowed regardless of `allow_fork`.
- `prohibited_modifications` protect audit-chain integrity.
- `modifiable_fields` supports prefix matching, so `"inputs"` allows `"inputs.query"`.

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
| `GET` | `/status` | Subsystem status (enabled, fork allowed, approval tier) |
| `GET` | `/quest/{quest_id}/receipts` | Full receipt chain for a quest |
| `GET` | `/receipt/{receipt_id}/snapshot` | Governance state snapshot at a receipt |
| `POST` | `/inspect` | Create a read-only inspection |
| `POST` | `/replay` | Replay a quest (requires identity) |
| `POST` | `/fork` | Fork a quest with modifications (requires identity) |

### Authentication

- Browser requests use the authenticated War Room session cookie.
- API clients use the configured bearer or API-key auth path.
- Replay and fork derive operator identity from the authenticated request and do not trust legacy `X-Operator-ID` headers.

### Runtime Status Contract

`GET /api/timetravel/status` exposes runtime readiness explicitly:

- `engine_ready`
- `quest_executor_ready`
- `snapshot_reader_ready`
- `receipt_service_ready`
- `runtime_degraded`
- `degraded_reasons`
- `runtime_errors`

If the live Soul cannot be resolved, or replay and fork execution are not wired to the governed quest executor, the status surface degrades explicitly instead of quietly behaving as if the subsystem were operational.

---

## War Room UI

The **Time-Travel Debugger** page is available from the DEBUGGING section in the sidebar.

### Components

1. **Quest Search** - enter a Quest ID to load its receipt DAG.
2. **DAG Navigator** - SVG-based receipt-chain visualization with parent-child edges.
3. **Receipt Detail** - expandable inputs and outputs for the selected receipt.
4. **State Inspector** - governance snapshot at the selected receipt:
   - Soul version, trust tier, kill switch state
   - Cost data (tokens, receipts, duration)
   - Governance context flags (Soul, APL, Trust active)
5. **Fork/Replay Modal** - mode toggle, JSON modification editor, approval tier display.

---

## State Snapshot

The `StateSnapshotReader` reconstructs governance context at any point in time:

- **Soul version** - determined from the most recent `SOUL_UPDATED` receipt.
- **Kill switches** - replayed from `KILL_SWITCH_ISSUED` and `KILL_SWITCH_LIFTED` receipts.
- **Trust tier** - aggregate approval tier derived from the current Trust Ledger state.
- **Cost data** - aggregated token, receipt, and duration stats.
- **Feature flags** - current flag-state snapshot.
- **Receipt chain** - all quest receipts up to the snapshot point.

When the live Trust Ledger instance is available, Time Travel reads it directly. If not, it reloads persisted state from `lancelot_data/governance/trust_ledger.json`.

---

## Architecture Notes

- Fork and replay operations always run under the **current Soul**, never the historical one.
- Each fork and replay creates a **new quest_id** and never reuses the source quest.
- The T3 approval gate uses synchronous Trust Ledger approval-tier checking. Async War Room approval remains planned.
- Replay and fork fail closed if the governed quest executor is not configured.
- `FORK_SOUL_REJECTED` receipts are emitted by the system, not the operator.
- Fork operations are always classified as T3 (Synthesis tier).
- Cost accounting is isolated per fork quest.
