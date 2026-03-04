# Hive Agent Mesh

Lancelot's ephemeral sub-agent architecture — task decomposition, scoped souls, governance bridges, and operator intervention.

For the system architecture overview, see [Architecture](architecture.md). For governance details, see [Governance](governance.md). For operational procedures, see [Hive Runbook](operations/runbooks/hive.md).

---

## What Hive Is and Why

Complex tasks often require multiple independent steps that could benefit from parallel execution, specialized context, or different autonomy levels. Rather than having the orchestrator handle everything sequentially, Hive decomposes high-level goals into subtasks and spawns ephemeral sub-agents to execute them.

Each sub-agent:
- Gets a **scoped Soul** that is always more restrictive than the parent
- Runs in its own thread with independent timeout and action limits
- Produces receipts for every action
- Can be paused, resumed, killed, or modified by the operator at any time
- Is automatically collapsed (destroyed) when done — no persistent state

**Key design principles:**
- **Ephemeral by design** — sub-agents exist only for the duration of their task
- **Monotonic restriction** — scoped Souls can only be more restrictive, never less
- **Operator control** — every agent can be paused, killed, or replanned at any time
- **Receipt-traced** — every agent action, state transition, and intervention is auditable
- **No identical retry** — after failure or intervention, the Architect must produce a new plan

**Feature flag:** `FEATURE_HIVE` (default: `false`)

---

## Architecture

```
ArchitectAgent (persistent singleton)
    │
    ├── TaskDecomposer (LLM-powered, flagship_deep lane)
    │   └── Breaks goal into TaskSpec[] with execution_order groups
    │
    ├── AgentLifecycleManager
    │   ├── spawn() → Register + create runtime
    │   ├── execute() → Submit to ThreadPoolExecutor
    │   ├── pause() / resume() / kill() / kill_all()
    │   └── intervene() → Dispatch operator actions
    │
    ├── AgentRegistry (thread-safe state machine)
    │   ├── _agents: Dict[agent_id → SubAgentRecord]
    │   ├── _archive: List[SubAgentRecord] (collapsed)
    │   └── Enforces valid state transitions
    │
    ├── ScopedSoulGenerator
    │   └── Generates task-specific Souls (always more restrictive)
    │
    ├── GovernanceBridge
    │   ├── RiskClassifier → TrustLedger → MCPSentry
    │   └── validate_action() → GovernanceResult
    │
    ├── HiveReceiptManager
    │   └── Emits HIVE_TASK_EVENT, HIVE_AGENT_EVENT, HIVE_INTERVENTION_EVENT
    │
    └── SubAgentRuntime (one per executing agent)
        └── Per-action loop: check collapse → check pause → check timeout
            → check max_actions → governance check → execute → receipt
```

### Module Layout

```
src/hive/
├── __init__.py
├── types.py               # Core data models and enums
├── config.py              # HiveConfig (Pydantic + YAML loader)
├── errors.py              # Exception hierarchy
├── registry.py            # AgentRegistry (thread-safe state machine)
├── lifecycle.py           # AgentLifecycleManager
├── runtime.py             # SubAgentRuntime (per-agent thread)
├── decomposer.py          # TaskDecomposer (LLM-powered)
├── scoped_soul.py         # ScopedSoulGenerator
├── architect.py           # ArchitectAgent (persistent orchestrator)
├── receipts.py            # Receipt emission helpers
├── receipt_manager.py     # HiveReceiptManager
├── api.py                 # FastAPI router (/api/hive/*)
└── integration/
    ├── governance_bridge.py   # GovernanceBridge
    ├── uab_bridge.py         # UABBridge (desktop app control)
    └── uab_executor.py       # HiveUABExecutor (LLM-planned UAB)
```

---

## Agent State Machine

Every sub-agent follows a strict state machine. Invalid transitions are rejected by the registry.

```
SPAWNING ──► READY ──► EXECUTING ──► COMPLETING ──► COLLAPSED
                          │    ▲
                          ▼    │
                        PAUSED

Any non-COLLAPSED state ──► COLLAPSED (on kill, error, timeout, etc.)
```

```python
class AgentState(str, Enum):
    SPAWNING = "spawning"       # Being created, Soul being generated
    READY = "ready"             # Ready to execute
    EXECUTING = "executing"     # Running actions
    PAUSED = "paused"           # Temporarily suspended by operator
    COMPLETING = "completing"   # Finishing up, assembling results
    COLLAPSED = "collapsed"     # Terminal state — agent destroyed
```

### Valid Transitions

| From | To | Trigger |
|------|----|---------|
| SPAWNING | READY | Soul generated, runtime created |
| READY | EXECUTING | Actions submitted |
| EXECUTING | PAUSED | Operator pause or governance hold |
| PAUSED | EXECUTING | Operator resume |
| EXECUTING | COMPLETING | All actions done or collapse requested |
| COMPLETING | COLLAPSED | Results assembled |
| _any non-collapsed_ | COLLAPSED | Kill, error, timeout, Soul violation |

---

## Control Methods

Each task specifies an autonomy level for its sub-agent:

```python
class ControlMethod(str, Enum):
    FULLY_AUTONOMOUS = "fully_autonomous"    # No approval gates
    SUPERVISED = "supervised"                # Approval for T2/T3 actions
    MANUAL_CONFIRM = "manual_confirm"        # All actions require approval
```

The default control method is `supervised` (configurable in `config/hive.yaml`).

---

## Collapse Reasons

When a sub-agent reaches the COLLAPSED state, it records why:

```python
class CollapseReason(str, Enum):
    COMPLETED = "completed"                  # Task finished successfully
    OPERATOR_KILL = "operator_kill"          # Operator killed this agent
    OPERATOR_KILL_ALL = "operator_kill_all"  # Emergency kill-all
    SOUL_VIOLATION = "soul_violation"        # Scoped Soul constraint violated
    GOVERNANCE_DENIED = "governance_denied"  # Governance pipeline rejected action
    TIMEOUT = "timeout"                      # Exceeded task timeout
    ERROR = "error"                          # Runtime error
    MAX_ACTIONS_EXCEEDED = "max_actions_exceeded"  # Hit action count limit
```

---

## Task Decomposition

The `TaskDecomposer` uses the flagship_deep LLM lane to break high-level goals into concrete subtasks.

### Process

1. **Discover context** — check available UAB apps (if UABBridge available)
2. **Build prompt** — include goal, constraints, max_subtasks, available apps
3. **Route to flagship_deep** — complex reasoning required
4. **Parse response** — extract subtasks and execution order from LLM JSON
5. **Build TaskSpecs** — map to typed objects with priorities and control methods
6. **Validate** — ensure non-empty, valid indices, within limits

### TaskSpec

```python
@dataclass
class TaskSpec:
    task_id: str                     # UUID
    description: str                 # What the agent should do
    control_method: ControlMethod    # Autonomy level
    priority: TaskPriority           # CRITICAL(0), HIGH(1), NORMAL(2), LOW(3)
    timeout_seconds: int             # Default: 300
    max_actions: int                 # Default: 50
    allowed_apps: List[str]          # For UAB filtering
    allowed_categories: List[str]    # For Soul scoping
    context: Dict[str, Any]          # Task-specific context
    parent_task_id: Optional[str]
    execution_group: int             # Tasks in same group run in parallel
```

### DecomposedTask

```python
@dataclass
class DecomposedTask:
    quest_id: str                    # Unique task identifier
    goal: str                        # Original high-level goal
    subtasks: List[TaskSpec]
    execution_order: List[List[str]] # Grouped by parallel execution
    context: Dict[str, Any]
    decomposed_at: str               # ISO timestamp
```

### Execution Order

Tasks are grouped by execution order. Tasks within the same group run in parallel; groups execute sequentially:

```
execution_order: [["0"], ["1", "2"], ["3"]]
```
- Group 1: Task 0 runs alone
- Group 2: Tasks 1 and 2 run in parallel
- Group 3: Task 3 runs after both 1 and 2 complete

---

## Scoped Soul Generation

Every sub-agent gets a task-specific Soul that is always more restrictive than the parent. This is the **monotonic restriction principle** — scoped Souls can only tighten constraints, never loosen them.

### Generation Process

```python
def generate(parent_soul, task_spec, extra_risk_rules=None) -> Soul:
```

1. **Start with parent** `allowed_autonomous` actions
2. **Filter by task categories** — if `task_spec.allowed_categories` is set, only matching actions are kept
3. **Apply control method** — if `MANUAL_CONFIRM`, move all actions to `requires_approval`
4. **Preserve all parent risk rules** — add Hive-specific rules
5. **Tighten scheduling** — `max_concurrent_jobs=1`, `no_autonomous_irreversible=True`, duration capped to task timeout
6. **Set autonomy level** to `"scoped"`

### Validation

```python
def validate_more_restrictive(scoped, parent) -> bool:
```

Checks:
- All parent risk rule names are preserved
- No new `allowed_autonomous` actions beyond parent
- Scheduling boundaries not loosened
- `no_autonomous_irreversible` maintained if parent has it

### Hashing

Each scoped Soul is hashed (`SHA256[:16]`) and stored on the `SubAgentRecord` for audit linkage.

---

## Soul Overlay

The file `soul/overlays/hive.yaml` defines Hive-specific governance rules that overlay the base Soul.

### Non-Negotiable Rules

1. **hive_no_autonomous_t3** — Sub-agents may NEVER autonomously execute T3 actions. All T3 requires explicit operator approval, even with `control_method=fully_autonomous`.

2. **hive_collapse_on_governance_violation** — Any sub-agent failing a governance check is immediately collapsed. No retry for governance violations.

3. **hive_scoped_soul_monotonic** — Scoped Souls can ONLY be more restrictive than parent. Constraints are additive only.

4. **hive_intervention_requires_reason** — ALL operator interventions require a non-empty reason string for audit accountability.

5. **hive_never_retry_identical** — After failed task or intervention, the Architect must produce a NEW plan. Retrying an identical plan is forbidden (tracked via plan hash).

### Allowed Autonomous Actions

```yaml
- hive_task_decompose
- hive_agent_spawn
- hive_agent_collapse_completed
- hive_receipt_emit
- hive_status_query
```

### Requires Approval

```yaml
- hive_agent_t3_action
- hive_kill_all
- hive_modify_constraints
```

---

## Governance Bridge

The `GovernanceBridge` connects sub-agents to Lancelot's governance pipeline.

### Validation Flow

```python
def validate_action(capability, scope, target, agent_id) -> GovernanceResult:
```

1. **RiskClassifier** — classify capability into tier (T0–T3)
   - T3 always requires approval for Hive agents
   - T2 requires supervision
2. **TrustLedger** — check effective tier (may lower based on history)
3. **MCPSentry** — check hard permission rules (deny takes precedence)

### GovernanceResult

```python
@dataclass
class GovernanceResult:
    approved: bool                   # Can the action proceed?
    tier: Optional[str]              # T0–T3
    reason: str
    requires_operator_approval: bool # Pause agent and wait?
```

### Behavior on Denial

- If `requires_operator_approval=True`: agent is paused, waiting for operator resume
- If `approved=False` without operator option: agent is collapsed with `GOVERNANCE_DENIED`

---

## Operator Intervention

The operator has full control over every running agent.

### Intervention Types

```python
class InterventionType(str, Enum):
    PAUSE = "pause"        # Suspend agent execution
    RESUME = "resume"      # Resume paused agent
    KILL = "kill"          # Terminate agent permanently
    MODIFY = "modify"      # Kill + replan with operator feedback
    KILL_ALL = "kill_all"  # Emergency: collapse all agents
```

### Intervention Rules

- **Reason is REQUIRED** — every pause, kill, modify, and kill_all must include a non-empty reason string. This is validated by both the API and the lifecycle manager.
- **MODIFY** kills the current agent and triggers a replan with the operator's feedback injected into the decomposition context.
- **KILL_ALL** is an emergency stop — all active agents are collapsed immediately.

### No Identical Retry Rule

After a failed task or MODIFY intervention, the Architect generates a new plan. If the new plan's hash matches any previous plan in the `_plan_history`, it is rejected. This prevents infinite retry loops where the LLM keeps generating the same failing plan.

### Intervention Record

```python
@dataclass
class OperatorIntervention:
    intervention_id: str
    intervention_type: InterventionType
    agent_id: Optional[str]
    task_id: Optional[str]
    reason: str                      # Required, non-empty
    feedback: Optional[str]          # For MODIFY interventions
    constraints: Optional[Dict]
    created_at: str
    resolved: bool
    resolution: Optional[str]
```

---

## Receipt System

All Hive events are receipt-traced via the shared receipt system.

### Receipt Types

| Receipt Type | Event Category | Examples |
|-------------|----------------|----------|
| `HIVE_TASK_EVENT` | Task lifecycle | task_received, decomposition, task_completed, task_failed, replan |
| `HIVE_AGENT_EVENT` | Agent lifecycle | agent_spawned, state_transition, action_executed, paused, resumed, collapsed |
| `HIVE_INTERVENTION_EVENT` | Operator control | pause, resume, kill, modify, kill_all |

### Receipt Hierarchy

- **quest_id** — groups all receipts for a single high-level task
- **parent_id** — links receipts to their parent (e.g., agent spawn links to task decomposition)
- **metadata** — contains `hive_subsystem`, `hive_agent_id`, intervention details

### HiveReceiptManager Methods

**Task events:**
- `record_task_received(goal, quest_id, context)`
- `record_decomposition(decomposed, parent_receipt_id)`
- `record_task_completed(quest_id, results, parent_receipt_id)`
- `record_task_failed(quest_id, error, parent_receipt_id)`
- `record_replan(quest_id, original_summary, new_summary, trigger, parent_receipt_id)`

**Agent events:**
- `record_agent_spawned(record, parent_receipt_id)`
- `record_agent_state_transition(agent_id, from_state, to_state, quest_id, parent_receipt_id)`
- `record_agent_action(agent_id, action_name, inputs, result, quest_id, parent_receipt_id)`
- `record_agent_paused(agent_id, reason, quest_id, parent_receipt_id)`
- `record_agent_resumed(agent_id, quest_id, parent_receipt_id)`
- `record_agent_collapsed(agent_id, reason, message, quest_id, parent_receipt_id)`

**Intervention events:**
- `record_intervention(type, agent_id, reason, feedback, quest_id, parent_receipt_id)`

**Governance events:**
- `record_governance_check(agent_id, capability, approved, tier, quest_id, parent_receipt_id)`

---

## API Endpoints

All endpoints are under `/api/hive` and gated by `FEATURE_HIVE`.

### Status & Discovery

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/hive/status` | Mesh status: idle/decomposing/executing, active agent count, quest info |
| GET | `/api/hive/roster` | Full roster: active + archived agents |
| GET | `/api/hive/agents` | Active agents only |
| GET | `/api/hive/agents/history` | Archived (collapsed) agents |
| GET | `/api/hive/agents/{agent_id}` | Single agent details (404 if not found) |
| GET | `/api/hive/agents/{agent_id}/soul` | Agent's scoped Soul (if executing) |

### Task Submission

| Method | Path | Body | Description |
|--------|------|------|-------------|
| POST | `/api/hive/tasks` | `{"goal": str, "context": {}}` | Submit a task for decomposition and execution |
| GET | `/api/hive/tasks/{quest_id}` | — | Get all receipts for a quest |
| GET | `/api/hive/tasks/{quest_id}/tree` | — | Get hierarchical receipt tree |

### Agent Control

| Method | Path | Body | Description |
|--------|------|------|-------------|
| POST | `/api/hive/agents/{id}/pause` | `{"reason": str}` | Pause an executing agent (reason required) |
| POST | `/api/hive/agents/{id}/resume` | — | Resume a paused agent |
| POST | `/api/hive/agents/{id}/kill` | `{"reason": str}` | Kill an agent (reason required) |
| POST | `/api/hive/agents/{id}/modify` | `{"reason": str, "feedback": str}` | Kill + replan with feedback |
| POST | `/api/hive/kill-all` | `{"reason": str}` | Emergency: collapse all agents |

### Interventions

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/hive/interventions` | All intervention records |
| GET | `/api/hive/interventions/{quest_id}` | Interventions for a specific quest |

---

## UAB Integration

When `FEATURE_HIVE_UAB` is enabled, Hive agents can control desktop applications via UAB.

### HiveUABBridge

Wraps the `UABProvider` with governance checks:

- **Read-only** operations (enumerate, query, state): No governance gate
- **Mutating** operations (act): Full governance check via GovernanceBridge
- **App access validation**: Checks `allowed_apps` in scoped Soul

### HiveUABExecutor

Translates subtask descriptions into real UAB commands using LLM-powered step planning:

1. **Connect** to target app via PID
2. **Enumerate** UI elements
3. **Plan steps** — LLM generates specific UAB commands from task description (max 1–5 steps)
4. **Execute steps** sequentially
5. **Return results** with per-step success/error details

**Available methods for LLM planning:**
```json
{"method": "act", "element_id": "<id>", "action": "click|type|clear|focus|select"}
{"method": "keypress", "key": "<key>"}
{"method": "hotkey", "keys": ["ctrl", "a"]}
{"method": "maximize"}
{"method": "restore"}
{"method": "state"}
{"method": "query", "selector": {...}}
```

**Heuristic fallback:** If LLM is unavailable, uses pattern matching on the task description to generate basic commands.

---

## Configuration

### `config/hive.yaml`

```yaml
# Capacity
max_concurrent_agents: 10           # Paused agents count toward limit
default_task_timeout: 300            # Seconds per agent
max_actions_per_agent: 50            # Action count limit per agent
max_subtasks_per_decomposition: 20   # Max subtasks from decomposer

# Governance
spawn_approval_tier: "T2"           # Min tier for spawn approval
default_control_method: "supervised" # Default autonomy level
collapse_on_governance_violation: true
collapse_on_soul_violation: true

# UAB Integration
uab_enabled: false                   # Requires FEATURE_HIVE_UAB
uab_allowed_apps: []                 # App name allowlist

# Retry
max_retry_attempts: 2
never_retry_identical_plan: true     # Enforce plan diversity

# Logging
log_agent_actions: true
log_decomposition: true
```

---

## Error Hierarchy

```
HiveError (base)
├── AgentCollapsedError
├── AgentPausedError
├── AgentSpawnDeniedError
├── ScopedSoulViolationError
├── TaskDecompositionError
├── SubAgentTimeoutError
├── UABControlError
├── MaxAgentsExceededError
└── InterventionRequiresReasonError
```

---

## War Room Integration

### HiveAgentMesh Page

The Hive Agent Mesh page provides real-time monitoring and control:

- **Task submission form** — enter a goal and submit for decomposition
- **Active agents table** — agent ID, state, task description, action count, control method
- **Archived agents table** — collapsed agents with reason and history
- **Per-agent controls** — pause, resume, kill, modify buttons
- **Kill-all emergency button** — collapses all agents immediately

**State badges:**

| State | Color |
|-------|-------|
| spawning | Gray |
| ready | Blue |
| executing | Green |
| paused | Yellow |
| completing | Purple |
| collapsed | Red |

**Collapse reason badges:**

| Reason | Color |
|--------|-------|
| completed | Green |
| operator_kill / operator_kill_all | Red |
| soul_violation / governance_denied | Orange |
| timeout / max_actions_exceeded | Yellow |
| error | Red |

**Polling:** 3-second refresh interval for live status.

### InterventionDialog Component

Modal dialog for pause, kill, and modify actions:

- **Type-specific title and description** — explains the consequences
- **Required reason field** — validated non-empty before submission
- **Optional feedback textarea** — shown only for MODIFY interventions
- **Type-specific button styling** — yellow (pause), red (kill), blue (modify)
- **Agent ID display** — shown in monospace for clarity

---

## Key Files

| Path | Purpose |
|------|---------|
| `src/hive/types.py` | Core data models, enums, state machine |
| `src/hive/config.py` | HiveConfig loader |
| `src/hive/registry.py` | Thread-safe agent state management |
| `src/hive/lifecycle.py` | Spawn, execute, pause, resume, kill |
| `src/hive/runtime.py` | Per-agent execution loop |
| `src/hive/decomposer.py` | LLM-powered task decomposition |
| `src/hive/scoped_soul.py` | Scoped Soul generation and validation |
| `src/hive/architect.py` | Persistent orchestrator singleton |
| `src/hive/api.py` | FastAPI router |
| `src/hive/integration/governance_bridge.py` | Governance pipeline bridge |
| `src/hive/integration/uab_executor.py` | LLM-planned UAB execution |
| `config/hive.yaml` | Mesh configuration |
| `soul/overlays/hive.yaml` | Hive governance overlay |
| `src/warroom/src/pages/HiveAgentMesh.tsx` | War Room monitoring page |
| `src/warroom/src/components/InterventionDialog.tsx` | Operator control dialog |
| `src/warroom/src/api/hive.ts` | War Room API client |
| `tests/hive/` | 14 test modules |
