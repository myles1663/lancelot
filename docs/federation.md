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
| `audit.py` | Cross-instance forensic timeline reconstruction |
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

- Full stop → Push → Activate → Per-instance confirmation required
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

---

## Divergence and Reconciliation

When connectivity is lost, the `DivergenceDetector` tracks:

- Last confirmed contact time
- Soul hash at point of divergence
- Active task count and Hive spawn states
- Pending handoffs and budget utilization

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
| POST | `/peer/register` | Initiate registration (challenge/response) |
| POST | `/peer/confirm` | Confirm registration |
| DELETE | `/peers/{instance_id}` | Unregister peer |

### Soul Management
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/soul` | Fetch this instance's Soul |
| GET | `/soul/hash` | Get Soul version hash |
| POST | `/soul/update` | Receive Soul push from peer |
| POST | `/pause` | Pause for Soul propagation |

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

- **Federation Overview** — Status dashboard with peer list, heartbeats, Soul consistency
- **Federation Audit** — Searchable cross-instance audit trail
- **Graph Builder** — Visual topology editor with deployment gate validation

---

## Peer Registry (SQLite Backend)

Peer state is persisted in SQLite with WAL mode for concurrent reads and thread-safe access via `threading.Lock`.

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
