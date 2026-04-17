<!-- SPDX-License-Identifier: BUSL-1.1 — Copyright (c) Myles Russell Hamilton. Licensed under the Business Source License 1.1 -->

# Federation Data Plane

**Feature Flag:** `FEATURE_FEDERATION` (default: `false`)
**Configuration:** `config/federation.yaml`
**Codebase:** `src/federation/` (30 modules, ~9,000 lines)

Lancelot Federation enables multi-instance coordination — hierarchical parent-child trees or peer-to-peer meshes — with Soul-governed task handoff, cost governance, and complete cross-instance audit trails.

---

## Architecture Overview

Federation adds three capabilities to a standalone Lancelot instance:

1. **Soul Propagation** — Root Soul document pushed to all instances with risk-tiered delivery
2. **Task Handoff** — Structured task transfer between instances with contract validation
3. **Governance Coordination** — Kill switches, cost budgets, and audit trails that span instances

### Deployment Modes

| Mode | Topology | Example |
|------|----------|---------|
| **STANDALONE** | No peers, single instance | Default deployment |
| **HIERARCHICAL** | Parent-child tree (root + children) | Team with central Soul governance |
| **FEDERATED** | Peer mesh (no single root) | Equal instances sharing workload |

Mode is derived automatically from the topology shape — not configured directly.

### Instance Roles

| Role | Description |
|------|-------------|
| **ROOT** | Top of hierarchy; only one per tree |
| **CHILD** | Child in hierarchical deployment |
| **PEER** | Equal peer in mesh deployment |
| **SELF** | This instance |

---

## Module Reference

| Module | Purpose |
|--------|---------|
| `identity.py` | Ed25519 keypair generation and signing |
| `auth.py` | Request signing, canonical string construction, replay protection |
| `transport.py` | Async HTTP client with circuit breakers and retry |
| `config.py` | Pydantic config model + YAML loader |
| `topology.py` | Peer registry and deployment mode derivation |
| `peer_registry.py` | SQLite-backed peer persistence with nonce replay protection |
| `peer_protocol.py` | Mutual peer registration handshake (challenge/response) |
| `heartbeat.py` | Heartbeat emission and staleness tracking |
| `heartbeat_mesh.py` | Federation-wide heartbeat aggregation |
| `soul_transport.py` | Soul document push/pull over HTTP |
| `soul_handshake.py` | Soul version propagation protocol |
| `soul_propagation.py` | Risk-tiered propagation engine (T1/T2/T3) |
| `soul_compat.py` | Soul compatibility validation between versions |
| `handoff_protocol.py` | Task handoff lifecycle (initiate → accept → complete) |
| `command_relay.py` | Kill switch broadcast to peers with ack/reject tracking |
| `kill_switch.py` | Federation-wide kill switch engine with authority hierarchy |
| `budget.py` | Spawn budget enforcement per instance |
| `cost_aggregation.py` | Real-time cost governance with 5-level thresholds |
| `cost_reporter.py` | Cost data pushed via heartbeat payloads |
| `divergence.py` | Connectivity loss detection and reconciliation |
| `contradiction_detector.py` | Receipt DAG consistency checking |
| `receipt_manager.py` | Federation receipt emission |
| `receipts.py` | Federation-specific receipt schema |
| `audit.py` | Cross-instance forensic timeline reconstruction with persisted audit log |
| `graph_models.py` | Graph Builder data models |
| `graph_api.py` | Graph Builder REST API |
| `graph_persistence.py` | Topology document storage |
| `graph_validator.py` | Deployment gate and compatibility validation |
| `api.py` | FastAPI router with 7 endpoint categories |

---

## Identity and Security

Every Lancelot instance generates a unique **Ed25519 keypair** on first activation, stored in the Credential Vault.

### Per-Instance Identity

- **Instance ID** — UUID
- **Public Key** — Ed25519, shared with peers during registration
- **Fingerprint** — `SHA256(public_key)[:16]`
- **Private Key** — Signs all outbound federation requests

### Request Signing Protocol

All inter-instance HTTP requests include signed headers:

| Header | Content |
|--------|---------|
| `X-Federation-Instance-Id` | Sender's instance UUID |
| `X-Federation-Timestamp` | ISO 8601 UTC |
| `X-Federation-Nonce` | Single-use random value |
| `X-Federation-Signature` | Ed25519 signature (hex) |

**Canonical string:** `"{METHOD}\n{PATH}\n{TIMESTAMP}\n{NONCE}\n{SHA256(BODY)}"`

### Replay Protection

- Nonces are persisted in SQLite and deduplicated
- Timestamp window: ±30 seconds (configurable via `auth_timestamp_window_s`)
- Nonces older than 120 seconds are pruned automatically
- Pending mutual-registration handshakes are persisted with bounded TTL, so a restart during `/peer/register -> /peer/confirm` does not silently drop the in-flight peer trust exchange.

---

## Heartbeat System

Each instance emits heartbeats at a configurable interval (default: 2 seconds).

### Heartbeat Payload

```json
{
  "instance_id": "uuid",
  "timestamp": "2026-03-17T12:00:00Z",
  "soul_version_hash": "sha256...",
  "deployment_mode": "hierarchical",
  "active_task_count": 3,
  "budget_utilization_pct": 45.2,
  "peer_count": 4,
  "signature": "ed25519hex..."
}
```

### Staleness Levels

| Level | Threshold | Meaning |
|-------|-----------|---------|
| **FRESH** | < 10s | Normal operation |
| **WARNING** | 10–20s | Possibly transient delay |
| **CRITICAL** | 20–30s | Likely connectivity issue |
| **LOST** | > 30s | Instance unreachable |

The heartbeat mesh now subscribes to newly registered peers and removes dropped peers immediately, so federation SSE coverage follows live topology changes without requiring a restart.
Subscription status is now connection-truthful instead of task-truthful: peers report `connecting`, `connected`, `reconnecting`, `failed`, or `disconnected` from the real SSE state instead of showing a generic "active" status whenever a reconnect loop task exists.

---

## Soul Propagation

Soul updates are pushed from root to all instances using a **3-tier risk model**:

### T1 — Minor Changes (Tone, Naming)

- Direct push via heartbeat
- No pause required
- Applies from next decision point

### T2 — Significant Changes (Autonomy Posture, Approval Rules)

- Pause → Push → Activate simultaneously → Resume
- All instances update together

### T3 — Critical Changes (Risk Rules, Scheduling, Budgets)

- Full stop → Push → Activate → Per-instance confirmation required before resume
- Most disruptive but safest

### Soul Consistency States

| State | Meaning |
|-------|---------|
| **SYNCHRONIZED** | All instances on same Soul version |
| **PROPAGATING** | Update in flight |
| **STALE** | Some instances behind |
| **DIVERGED** | Incompatible versions detected |

### MCP Ceiling Enforcement

When a Soul is pushed to a child/peer, the child's `mcp_permissions` are intersected with the root's. This enforces the **monotonic narrowing** principle — child instances can only have equal or more restrictive MCP permissions.

### Runtime Soul Transport Contract

The federation Soul transport is now a live runtime boundary rather than a planning stub:

- `GET /api/federation/soul` returns the current active runtime Soul document plus its deterministic hash.
- `POST /api/federation/soul/update` is a **root-authority** path. Only authenticated peers registered as `role=root` can push a runtime Soul update into another instance.
- incoming Soul pushes validate the pushed Soul as a real Soul document, lint it, verify the supplied hash, narrow `mcp_permissions` against the receiver's current runtime Soul ceiling, persist the version locally, refresh the live runtime, and emit a fresh heartbeat so peers can observe the new hash immediately.
- the sender hash is validated against the raw pushed Soul first; if the receiver enforces an MCP ceiling, the applied local Soul hash is recomputed from the narrowed document instead of rejecting the push as if it were tampered in transit.
- `POST /api/federation/soul/handshake` now evaluates a real remote Soul document against the live local runtime Soul and returns compatibility metadata instead of doing a hash-only comparison.
- the gateway instantiates the `SoulPropagationEngine`, and `push_soul_update(...)` now records live T1/T2/T3 rollout state so `/api/federation/status` reports real Soul consistency and active propagation events instead of static placeholders.
- push results are also summarized through the Soul handshake model, so governance gaps and timed-out peer acknowledgements are surfaced explicitly instead of being left as silent transport failures.
- stale propagation events are finalized from the live runtime view rather than remaining indefinitely "active" after the timeout window expires.
- T2 propagation performs an explicit federation resume phase after successful update delivery instead of pausing peers indefinitely.
- T3 propagation no longer auto-confirms on delivery. Peers stay in `confirming` until each updated instance sends a second-leg `/api/federation/soul/confirm` callback after local apply, and only then does the root instance issue the resume phase.
- Federation full-stop paths now run through the real local pause engine: budget `hard_stop` and T3 full-stop pauses cancel scheduler and Sentry approval queues, pause active HIVE execution when available, and remain operator-resume-only after the event is cleared.

---

## Task Handoff Protocol

### Lifecycle

```
Source                          Target
  │                               │
  ├─ POST /handoff/initiate ─────►│
  │                               ├─ Validate contract
  │◄─────────── ACCEPTED ─────────┤
  │                               ├─ Execute under provided Soul
  │◄─────────── COMPLETED ────────┤
  │  (includes receipts)          │
```

### Handoff Package Contents

- **Task context** — Goal, constraints, partial results
- **Soul context** — Operating Soul document
- **HandoffContract** — Assumptions, success criteria, data schema
- **Receipt chain** — Prior work receipts for audit continuity

### Acceptance Enforcement

Handoff acceptance is now enforced as a runtime boundary, not just a planning convention.

- The incoming source must already be a known peer.
- `data_payload_schema` is validated against the incoming `task_context` before the handoff is accepted.
- The incoming `soul_context` is validated as a real Soul document.
- The target computes a Soul intersection between its active Soul and the received Soul context.
- That intersection must remain more restrictive than both the receiver's Soul and the incoming Soul context.
- If `soul_context_constraints` are present in the contract, the computed operating Soul must satisfy them or the handoff is rejected.
- `RED` compatibility outcomes are rejected outright instead of being recorded as advisory metadata.
- Incoming receipt chains are checked for temporal contradictions before the handoff is accepted.
- Completion reports are re-validated against the contract on return: declared result schemas, numeric bounds, and success-criteria expectations can all reject a completion as contradictory.
- Detected contradictions are written into the federation audit timeline so post-incident review sees the failed handoff boundary explicitly.

### Handoff States

```
INITIATED → ACCEPTED → IN_PROGRESS → COMPLETED
INITIATED → REJECTED
ACCEPTED  → FAILED
```

---

## Kill Switches (Federation-Wide)

### Authority Hierarchy

| Level | Scope | Who |
|-------|-------|-----|
| **L1_FEDERATION_ROOT** | Any instance | Root only |
| **L2_LOCAL_INSTANCE** | Self only | Any instance |
| **L3_AUTOMATED** | Context-dependent | Governance triggers |

### Command Types

| Command | Effect |
|---------|--------|
| `LOCAL_KILL` | Kill all agents on local instance |
| `FEDERATION_KILL` | Kill all agents across all instances |
| `TARGETED_KILL` | Kill specific agent on specific instance |
| `CASCADING_KILL` | Kill agent + all downstream dependents |
| `FEATURE_KILL` | Disable feature flag federation-wide |

### Command States

```
PENDING → PROPAGATING → COMPLETED
                      → PARTIAL (some acks failed)
                      → FAILED (all acks failed)
LIFTED (after operator review)
```

Incoming federation kill commands now fail closed if the local instance has no live kill engine configured. In the current gateway wiring, the local kill path is backed by HIVE `kill_all`, so an acknowledged federation kill reflects a real local intervention rather than a placeholder success response.
Incoming federation kills are also now **root-authority only** at the receiving boundary: peers registered as `role=peer` or `role=child` cannot issue a federation kill into another instance just by claiming `L1_FEDERATION_ROOT` in the payload. Active kill-command state is persisted so propagation acknowledgements, partial failures, and lift-review state survive restart instead of being lost with process memory.
Operator-issued federation kills now use that same persisted kill ledger: `/api/federation/manage/kill` issues through `FederatedKillSwitch`, executes the local target leg, propagates to peers, and records remote acknowledgements/rejections into the shared command record instead of bypassing the hardened kill engine with a relay-only broadcast.
Kill timeout progression is also live now. Reading active or historical kill commands advances overdue `pending` targets into `timeout`, so commands do not remain stuck in `propagating` forever unless a separate sweeper is called manually.

---

## Cost Governance

Real-time cost tracking across the federation with threshold-based enforcement.

### Threshold Levels

| Threshold | Action |
|-----------|--------|
| **75%** | WARNING: operator notified |
| **85%** | SPAWN_RESTRICTED: new spawns need approval |
| **95%** | SPAWN_GATED: new spawns blocked |
| **100%** | HARD_STOP: all activity paused |

Cost data is aggregated from heartbeat payloads. Per-instance budgets are enforced independently via Soul parameters.

The live runtime now wires federation budget status to the main usage tracker and HIVE registry:

- local `actual_today_usd`, `projected_today_usd`, and `total_tokens_today` come from the orchestrator usage tracker
- local `daily_ceiling_usd` now comes from the deployed/active Graph Builder local-node budget when present, with `federation.yaml` fallback via `daily_budget_ceiling_usd` instead of a gateway hardcoded `$10/day`
- entering `HARD_STOP` now drives the real persisted runtime pause manager instead of only blocking HIVE spawns, so chat, scheduler dispatch, inbound A2A, and other paused-runtime ingress points honor the federation budget stop as a true instance-wide halt
- recovery from federation `HARD_STOP` is operator-controlled; dropping back under 100% does not auto-resume the runtime
- `active_spawns` comes from the HIVE registry
- root instances aggregate remote cost reports through `FederatedCostAggregator`; non-root/peer instances do not accept remote budget reports that can drive local threshold actions
- `/api/federation/budget`, `/api/federation/budget/threshold`, and `/api/federation/status` now report live threshold state instead of a hardcoded `normal`

Budget governance now also gates the real HIVE spawn path:

- threshold transitions emit `budget_threshold` receipts and federation audit entries
- federation-wide `spawn_gated` and `hard_stop` states block new HIVE spawns before sub-agents are registered
- per-instance spawn ceilings from `budget.py` are enforced before spawn registration
- successful spawn and collapse events update the live federation budget tracker so runtime enforcement follows actual agent lifecycle state instead of status-only estimates
- persisted peer cost snapshots are freshness-aware: stale remote budget data is surfaced in the aggregate status and blocks new spawns until refreshed or the peer is removed, rather than silently preserving stale pressure or stale free capacity
- inbound peer budget reports now fail closed if the root-side aggregate cannot be updated, and `/api/federation/budget` surfaces aggregate computation failures directly instead of silently falling back to a partial local-only snapshot
- `/api/federation/status` and `/api/federation/health` now fail closed on runtime inspection: degraded transport, mesh, Soul, divergence, or budget introspection is surfaced explicitly instead of silently returning optimistic defaults such as `synchronized`, `normal`, or `connected`
- heartbeat-mesh divergence evaluation failures are now surfaced as explicit degraded runtime state, and T3 HIVE spawns fail closed while divergence evaluation is unavailable instead of proceeding as if the mesh were still healthy

---

## Divergence and Reconciliation

When connectivity is lost, the `DivergenceDetector` tracks:

- Last confirmed contact time
- Soul hash at point of divergence
- Active task count and Hive spawn states
- Pending handoffs and budget utilization

The live heartbeat mesh now feeds those richer runtime fields into divergence detection instead of only passing peer heartbeat timestamps.

Reconnection is now a live runtime flow instead of a passive state flag:

- when fresh peer heartbeats arrive after divergence, the mesh runs `reconcile_divergence(...)` against the current runtime Soul hash and current federation budget utilization
- compatible reconciliations emit reconnection receipts/audit entries and return the detector to `connected`
- incompatible reconciliations remain visible in detector state for operator review instead of being silently cleared
- `/api/federation/status` now exposes `divergence_state`, `divergence_duration_s`, and the last reconciliation outcome/conflicts
- while divergence is active, T3 HIVE spawns are blocked at the live runtime boundary instead of continuing as normal or pretending to queue work that the platform cannot yet resume automatically
- Soul propagation events and federated cost-aggregate state are now persisted, so restart does not silently clear an in-flight rollout or reset cost governance back to `normal`
- divergence is evaluated immediately when the heartbeat mesh starts from the persisted peer heartbeat ledger, so a cold restart during an outage does not leave the instance falsely `connected` until a new SSE event arrives

### Divergence States

```
CONNECTED → DIVERGED → RECONNECTING → RECONCILED
```

### Reconciliation Outcomes

| Outcome | Meaning |
|---------|---------|
| **COMPATIBLE** | No conflicts — merge and continue |
| **INCOMPATIBLE** | Conflicts detected — operator review required |

---

## Contradiction Detection

The `ContradictionDetector` validates downstream outputs against upstream `HandoffContract` assumptions.

### Assumption Categories

| Category | Validation |
|----------|-----------|
| **FACTUAL** | Schema validation — does data match declared structure? |
| **CONSTRAINT** | Range checks — values within declared bounds? |
| **TEMPORAL** | Timestamp consistency — ordering preserved? |

### Contradiction Severity

| Level | Response |
|-------|----------|
| **LOW** | Informational, may self-resolve |
| **MEDIUM** | Requires attention, not blocking |
| **HIGH** | Blocking, workflow should pause |
| **CRITICAL** | Operator intervention required |

---

## Graph Builder

The Graph Builder provides a visual topology editor in the War Room for designing and validating federation deployments.

### Topology Documents

- **Nodes** — Lancelot instances (position, budget, HIVE config)
- **Edges** — Relationships (hierarchical parent/child, federated handoff)

### Edge Validation Dimensions

| Dimension | Check |
|-----------|-------|
| Soul compatibility | Does target Soul match source assumptions? |
| Budget alignment | Are daily ceilings sufficient? |
| Capability scope | Can target execute required actions? |
| Handoff contract match | Data payload schema compatibility? |
| Divergence preparedness | Can instances tolerate connectivity loss? |

Graph Builder remains a preflight and deployment-gate tool. Runtime handoff acceptance re-validates Soul compatibility and contract constraints independently, so a previously green graph does not bypass the live federation boundary.

### Deployment Gates

| Gate | Meaning |
|------|---------|
| **GREEN** | All edges compatible — deploy allowed |
| **YELLOW** | Conditional — requires operator acknowledgment |
| **RED** | Incompatible — deployment blocked |

---

## Circuit Breaker (Per-Peer Resilience)

Each peer connection has an independent circuit breaker:

```
CLOSED (normal) → OPEN (5 consecutive failures)
                      ↓ (60s timeout)
                  HALF_OPEN (test request)
                      ↓ success → CLOSED
                      ↓ failure → OPEN
```

### Transport Configuration

| Parameter | Default |
|-----------|---------|
| Max retry attempts | 3 |
| Backoff | Exponential: 1s, 2s, 4s |
| Connect timeout | 5 seconds |
| Read timeout | 30 seconds |
| Circuit breaker threshold | 5 failures |
| Circuit breaker recovery | 60 seconds |

---

## Audit Trail

The `FederationAuditEngine` maintains a complete cross-instance timeline.

### Audit Event Types

- `HANDOFF_INITIATED`, `HANDOFF_COMPLETED`, `HANDOFF_RECEIVED`, `HANDOFF_REJECTED`
- `SOUL_PUSH`, `SOUL_ACTIVATED`
- `KILL_ISSUED`, `KILL_ACKNOWLEDGED`
- `DIVERGENCE_DETECTED`, `RECONCILIATION_COMPLETED`
- `CONTRADICTION_DETECTED`, `COST_THRESHOLD_CROSSED`
- `PEER_REGISTERED`, `PEER_REMOVED`

### Forensic Timeline

The audit engine supports complete timeline reconstruction across all instances, with quest ID grouping, time range queries, and per-instance filtering.

---

## API Endpoints

**Prefix:** `/api/federation/`

### State & Health
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/stream` | SSE heartbeat stream + initial snapshot |
| GET | `/health` | Current health summary |

### Discovery
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/identity` | This instance's public identity |
| GET | `/status` | Deployment mode, peer count, Soul consistency |
| GET | `/peers` | All known peers |
| GET | `/peers/{instance_id}` | Single peer detail |

### Peer Registration
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/peer/register` | Initiate registration (challenge + signed identity proof) |
| POST | `/peer/confirm` | Complete the mutual counter-challenge leg |
| DELETE | `/peer/{instance_id}` | Unregister peer |

Registration now follows a strict mutual-confirm bootstrap:

1. The initiator sends `/peer/register` with its instance identity, its externally reachable `self_address`, and a signed random challenge.
2. The target verifies the submitted key material, rejects silent key/address changes for already-known peers, then calls back to `/peer/confirm` on the initiator.
3. The initiator verifies the target's response against the pending challenge, pins the target identity locally, signs the counter-challenge, and returns the confirmation signature.
4. Only after that confirm leg succeeds does the target persist the new peer.

Important federation trust rules:

- Caller-supplied fingerprints are not trusted. The receiver recomputes them from the submitted public key.
- Existing peer identities are pinned. A registration that changes a known peer's public key or address is rejected by default.
- Rekeying or address migration is treated as an explicit operator workflow, not something an inbound bootstrap request can do silently.
- `self_address` must be configured for outbound peer bootstrap so the callback leg can complete.
- For signed federation traffic, the authenticated peer identity from the request signature is authoritative. Body fields such as `issuer_instance_id`, `source_instance_id`, and `instance_id` must match the signed peer or the request is rejected.

### Runtime Status Contract

`/api/federation/status` is now the source of truth for live federation runtime health, not just topology shape.

Important fields include:

- `runtime_degraded`
- `degraded_reasons`
- `runtime_errors`
- `transport_started`
- `heartbeat_mesh_running`
- `cost_reporter_running`
- `subscription_status`
- `subscription_stream_outcome`
- `subscription_stream_errors`
- `circuit_breaker_summary`
- `stale_instance_ids`
- `local_soul_hash`
- `divergence_state`

`/api/federation/health` now includes the same runtime degradation signals in addition to peer freshness counts.

If Soul, budget, divergence, or transport inspection fails, these endpoints now fail closed by surfacing degraded runtime state instead of returning optimistic defaults.

### Soul Management
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/soul` | Fetch this instance's Soul |
| GET | `/soul/hash` | Get Soul version hash |
| POST | `/soul/update` | Receive Soul push from peer |
| POST | `/soul/confirm` | Record T3 confirmation from a peer before resume |
| POST | `/pause` | Pause for Soul propagation |

Pause semantics are now fail-closed and runtime-backed:

- A peer pause is only acknowledged if this instance has a real local pause engine wired.
- Pause and resume are now **root-authority** federation controls at the receiving boundary; known `peer` or `child` instances cannot pause or resume another instance's runtime.
- On the live standalone runtime, that pause engine is the persisted global runtime pause manager plus local HIVE intervention hooks.
- Outbound federation pause propagation now preserves the `full_stop` flag so T3/full-stop pauses trigger the stricter local handling path on receiving instances.
- A successful peer pause now sets the instance-wide runtime pause state so new work is blocked across chat, scheduler, HIVE spawn, A2A ingress, and TaskRun execution, and currently executing HIVE agents are paused when the HIVE lifecycle is available.
- If no local pause engine is available, `/api/federation/pause` rejects the request instead of reporting a false successful pause.

### Task Handoff
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/handoff/initiate` | Start task handoff |
| POST | `/handoff/accept` | Accept received handoff |
| POST | `/handoff/complete` | Report handoff completion |

### Governance
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/killswitch` | Receive kill command |
| POST | `/cost/report` | Report cost data |

Budget-report authority is intentionally narrow:

- remote budget reports are accepted for aggregation only on the authority side of the topology
- non-root/peer instances do not accept arbitrary remote budget reports that can drive a local `hard_stop`
- local `hard_stop` now becomes a real instance-wide runtime pause, not just a HIVE spawn denial

### Graph Builder
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/graph/topology` | Create topology document |
| GET | `/graph/topologies` | List all topologies |
| GET | `/graph/{id}` | Get topology |
| POST | `/graph/{id}/nodes` | Add node |
| POST | `/graph/{id}/edges` | Add edge |
| POST | `/graph/{id}/validate` | Validate topology |
| POST | `/graph/{id}/deploy` | Check deployment gates |
| POST | `/graph/{id}/acknowledge-yellow` | Acknowledge yellow warnings |

---

## War Room Integration

Three War Room pages provide federation visibility:

- **Federation Overview** — Status dashboard with peer list, heartbeats, Soul consistency, plus the local instance identity card for `self_address`, instance ID, fingerprint, and public key
- **Federation Audit** — Searchable cross-instance audit trail
- **Graph Builder** — Visual topology editor with deployment gate validation; the local node should reuse the configured `self_address` from Federation Overview rather than inventing a second endpoint source of truth

---

## Peer Registry (SQLite Backend)

Peer state is persisted in SQLite with WAL mode for concurrent reads and thread-safe access via `threading.Lock`, while topology and federation control-plane state are persisted to disk so restart does not drop live heartbeats, Soul hash observations, active handoff status, or audit timelines.

**Tables:**
- `peers` — instance_id, fingerprint, public_key_hex, address, role, soul_version_hash, heartbeat times
- `nonces` — Replay protection with automatic 120s pruning

---

## Configuration Reference

`config/federation.yaml`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `heartbeat_interval_s` | 2.0 | Heartbeat emission interval |
| `staleness_warning_s` | 10.0 | Warning threshold |
| `staleness_critical_s` | 20.0 | Critical threshold |
| `staleness_lost_s` | 30.0 | Lost threshold |
| `tls_required` | false | Require TLS for inter-instance traffic |
| `max_peers` | 50 | Maximum peer count |
| `command_timeout_s` | 5.0 | Kill command timeout |
| `handoff_timeout_s` | 30.0 | Handoff timeout |
| `budget_warning_pct` | 80.0 | Budget warning threshold |
| `budget_critical_pct` | 95.0 | Budget critical threshold |
| `retry_max_attempts` | 3 | Transport retry count |
| `circuit_breaker_threshold` | 5 | Failures before circuit opens |
| `circuit_breaker_recovery_s` | 60.0 | Recovery timeout |
| `auth_timestamp_window_s` | 30.0 | Replay protection window |
| `nonce_cache_size` | 10000 | Max cached nonces |
| `cost_report_interval_s` | 30.0 | Cost reporting interval |
