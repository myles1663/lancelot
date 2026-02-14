# Architecture

A full walkthrough of Lancelot's system architecture — how the subsystems connect, how a request flows from input to governed execution, and the key design decisions behind each component.

For how to get the system running, see the [Quickstart](quickstart.md). For the governance model specifically, see [Governance](governance.md).

---

## System Overview

Lancelot is composed of independent, kill-switchable subsystems coordinated by a central orchestrator. Every subsystem can be disabled via feature flags without breaking the rest of the system.

```
                          ┌──────────────────────────────────────┐
                          │           War Room (UI)              │
                          │  Health │ Governance │ Trust │ APL   │
                          └───────────────┬──────────────────────┘
                                          │ REST API
                          ┌───────────────▼──────────────────────┐
                          │        Gateway (FastAPI :8000)       │
                          │  Rate Limiter → Size Check → Sanitize│
                          └───────────────┬──────────────────────┘
                                          │
     ┌────────────────────────────────────▼──────────────────────────────────────┐
     │                           Orchestrator                                    │
     │                                                                           │
     │   Intent Classifier → Planning Pipeline → Response Governor               │
     │                                                                           │
     │   ┌─────────────┐  ┌──────────────┐  ┌──────────────┐                   │
     │   │   Planner    │→ │   Executor   │→ │   Verifier   │                   │
     │   └─────────────┘  └──────────────┘  └──────────────┘                   │
     │                                                                           │
     └──┬───────┬───────┬───────┬───────┬───────┬───────┬───────┬──────────────┘
        │       │       │       │       │       │       │       │
      Soul   Memory  Skills  Tool    Health  Sched  Receipts  Security
                              Fabric          uler
                       │       │
                  ┌────▼───────▼────┐
                  │  Model Router   │
                  │ 4-Lane Routing  │
                  ├─────────────────┤
                  │ 1. Local Redact │
                  │ 2. Local Utility│
                  │ 3. Flagship Fast│
                  │ 4. Flagship Deep│
                  └────────┬────────┘
                           │
              ┌────────────▼────────────┐
              │     LLM Providers       │
              │ Local GGUF │ Gemini │   │
              │ OpenAI │ Anthropic      │
              └─────────────────────────┘
```

---

## End-to-End Action Flow

Here's what happens when a user sends a message, from input to governed response:

### 1. Input Processing

```
User Input
  → Rate Limiter (60 requests/min)
  → Size Check (1 MB max)
  → InputSanitizer (16 banned phrases, 10 regex patterns,
    Cyrillic homoglyph normalization, zero-width character stripping)
  → Orchestrator
```

The input layer is a hard boundary. Prompt injection attempts are detected and blocked before the message reaches any LLM.

### 2. Intent Classification

The orchestrator classifies the message into one of five intent types:

| Intent | Description | Route |
|--------|-------------|-------|
| `PLAN_REQUEST` | Complex goal requiring multi-step planning | Planning Pipeline |
| `EXEC_REQUEST` | Direct action request | Tool Fabric |
| `MIXED_REQUEST` | Contains both planning and execution | Planning Pipeline |
| `KNOWLEDGE_REQUEST` | Information retrieval / research | Flagship Fast/Deep |
| `CONVERSATIONAL` | General conversation | Local or Flagship Fast |

Classification is done by the local model (cost: zero cloud tokens) and determines the entire downstream execution path.

### 3. Model Routing

The Model Router selects the appropriate LLM lane based on task type, risk level, and complexity:

| Priority | Lane | Models | When Used |
|----------|------|--------|-----------|
| 1 | `local_redaction` | Qwen3-8B (local) | PII redaction — always runs locally first |
| 2 | `local_utility` | Qwen3-8B (local) | Intent classification, summarization, JSON extraction |
| 3 | `flagship_fast` | Gemini Flash / GPT-4o-mini / Claude Haiku | Standard reasoning, tool calls, orchestration |
| 4 | `flagship_deep` | Gemini Pro / GPT-4o / Claude Sonnet | Complex planning, high-risk decisions |

**Escalation triggers:** If the fast lane fails, if risk keywords are detected, or if the task involves multi-step planning, the router automatically escalates to the deep lane. Every routing decision produces a `RouterDecision` record with lane, model, rationale, timing, and outcome.

### 4. Planning Pipeline (for complex requests)

For `PLAN_REQUEST` or `MIXED_REQUEST` intents, the Planning Pipeline builds a structured plan:

1. **Classify** — Confirm the intent and extract the goal
2. **Build PlanArtifact** — Generate a structured plan with: goal, context, assumptions, plan_steps, decision_points, risks, done_when, next_action
3. **Render** — Convert to human-readable markdown
4. **Governor Check** — Validate against Soul constraints and policy
5. **Output Gate** — Block simulated-progress language (the Response Governor prevents phrases like "I'm working on it" without a real job running)

### 5. Execution (Plan-Execute-Verify)

For plans that require execution, the three-agent loop runs:

```
Planner → generates JSON step list
  ↓
Executor → runs steps sequentially via Model Router
  ↓ (for each step)
Verifier → analyzes output against success criteria
  ├─ Success → update context, proceed to next step
  └─ Failure → suggest correction, retry (max 3 attempts)
```

Each step generates a receipt linked to the parent plan via `parent_id` and `quest_id`, forming a traceable chain.

### 6. Risk Classification & Governance

Every action is classified into one of four risk tiers:

| Tier | Name | Examples | Governance |
|------|------|----------|------------|
| **T0** | Inert | File reads, git status, memory reads | Policy cache lookup, batch receipt |
| **T1** | Reversible | File writes, git commit, memory writes | Rollback snapshot, async verification |
| **T2** | Controlled | Shell execution, network fetch | Sync verification, tier boundary flush |
| **T3** | Irreversible | Network POST, deploy, delete | Approval gate, sync verification |

The governance overhead scales with risk. T0 actions are near-instant (precomputed policy lookup). T3 actions require explicit owner approval before execution.

**Tier boundary enforcement:** Before any T2 or T3 action:
1. All pending batch receipts are flushed to disk
2. All pending async verifications are drained and completed
3. Any verification failure triggers rollback of preceding T1 actions

### 7. Tool Execution

Actions that require tool use go through the Tool Fabric:

```
Tool Request
  → PolicyEngine.evaluate() (command denylist, path traversal,
    workspace boundary, sensitive paths, network policy, risk level)
  → ProviderRouter.select() (match capability to provider)
  → Provider.execute() (Docker sandbox, local sandbox, etc.)
  → ToolReceipt (sanitized inputs/outputs, policy decisions)
```

Seven capability types are available: `ShellExec`, `RepoOps`, `FileOps`, `WebOps`, `UIBuilder`, `DeployOps`, `VisionControl`. Each has explicit security constraints.

### 8. Receipt Generation

Every action — LLM call, tool execution, file operation, memory edit, scheduler run, verification step, governance decision — produces a receipt:

```json
{
  "id": "receipt_abc123",
  "timestamp": "2026-02-14T10:30:00Z",
  "action_type": "llm_call",
  "action_name": "flagship_fast",
  "inputs": {"text": "[sanitized]"},
  "outputs": {"response": "[sanitized]"},
  "status": "success",
  "duration_ms": 1234,
  "token_count": 500,
  "cognition_tier": "CLASSIFICATION",
  "parent_id": "receipt_parent456",
  "quest_id": "quest_789"
}
```

Receipts form the ground truth of system behavior. They are persisted to `lancelot_data/receipts/` and are searchable through the War Room.

---

## Subsystem Details

### Soul (Constitutional Governance)

The Soul is a versioned YAML document that defines Lancelot's invariant behavior. It is immutable at runtime — the running system cannot modify its own Soul.

**What the Soul defines:**
- **Mission** — What Lancelot does and for whom
- **Allegiance** — Single-owner loyalty
- **Autonomy posture** — What can be done autonomously vs. what requires approval
- **Risk rules** — Safety boundaries and enforcement flags
- **Approval rules** — Timeout, escalation, channels
- **Tone invariants** — Communication rules (never mislead, acknowledge uncertainty)
- **Memory ethics** — PII handling, secret exclusion
- **Scheduling boundaries** — Limits on automated jobs

**Soul linter:** Five invariant checks run at load time:
1. Destructive actions must appear in `requires_approval` (CRITICAL)
2. Tone invariants must prohibit silent degradation (CRITICAL)
3. Scheduling must prevent autonomous irreversible actions (CRITICAL)
4. Approval rules must define at least one channel (CRITICAL)
5. Memory ethics must contain at least one rule (WARNING)

If any CRITICAL invariant fails, the Soul is rejected and the previous version remains active.

**Amendment workflow:** `PENDING` → owner approves → `APPROVED` → owner activates → `ACTIVATED` (with linter validation). This prevents accidental or unauthorized governance changes.

For a deeper dive, see [Governance](governance.md).

### Memory (Tiered, Commit-Based)

Lancelot maintains structured memory across four tiers:

| Tier | Persistence | Purpose |
|------|-------------|---------|
| **Core Blocks** | Permanent (pinned) | Persona, mission, operating rules, workspace state |
| **Working Memory** | Task-scoped | Current task context, intermediate results |
| **Episodic Memory** | Session-scoped | Conversation history, recent interactions |
| **Archival Memory** | Long-term | Accumulated knowledge, searchable via FTS |

**Commit-based editing:** Memory edits are atomic transactions. Each edit creates a snapshot before modification, applies changes, and can be rolled back to any previous state.

**Quarantine:** Risky memory writes (those that modify core blocks or contain sensitive patterns) land in quarantine. Promotion to active memory requires owner verification or approval.

**Context compiler:** Before each LLM call, the context compiler assembles memory tiers into a token-budgeted context window. Priority is Core > Working > Episodic > Archival, with LRU eviction when the budget is exceeded.

For more details, see [Memory](memory.md).

### Skills (Modular Capabilities)

Skills are Lancelot's extensibility mechanism — modular capabilities with declarative manifests.

**Skill manifest:** Each skill declares its name, version, required permissions, inputs/outputs, risk level, and scheduling eligibility in a `skill.yaml` file.

**Lifecycle:** Install → Enable → Execute → Disable → Uninstall

**Ownership model:**
- **SYSTEM** skills: Built-in (command_runner, repo_writer, network_client, service_runner)
- **USER** skills: Installed by the owner
- **MARKETPLACE** skills: Third-party, restricted to `read_input`, `write_output`, `read_config` permissions only

**Skill Factory:** A proposal pipeline for creating new skills. `generate_skeleton()` creates a complete skill directory (manifest, execute.py, tests). Proposals require owner approval before installation.

### Tool Fabric (Provider-Agnostic Execution)

The Tool Fabric provides sandboxed tool execution with seven capability protocols:

| Capability | Examples | Security |
|-----------|----------|----------|
| `ShellExec` | Run commands | Command denylist (shlex-tokenized), workspace boundary |
| `FileOps` | Read/write files | Path traversal check, symlink rejection, atomic writes |
| `RepoOps` | Git operations | Workspace boundary enforcement |
| `WebOps` | HTTP requests | Domain allowlist, network policy |
| `UIBuilder` | Generate UI | Template sandboxing |
| `DeployOps` | Deploy services | T3 risk, requires approval |
| `VisionControl` | Screenshot/analyze | Sandboxed browser |

**Execution pipeline:**
1. **PolicyEngine** evaluates the request against all security gates
2. **ProviderRouter** selects the appropriate execution provider
3. **Provider** executes in isolation (Docker sandbox for shell/code, direct for file ops)
4. **ToolReceipt** captures sanitized inputs, outputs, and policy decisions

**Security gates:** Command denylist (shlex-based token matching, not substring), path traversal detection, workspace boundary enforcement, sensitive file protection, network domain allowlist, and risk-tier assessment.

### Health Monitor (Heartbeat)

Continuous background monitoring at 30-second intervals.

**Endpoints:**
- `GET /health/live` — Liveness probe (always 200 if running)
- `GET /health/ready` — Full readiness snapshot

**HealthSnapshot:** Reports `ready`, `onboarding_state`, `local_llm_ready`, `scheduler_running`, `degraded_reasons`, and timestamps.

**State transitions** (healthy ↔ degraded ↔ recovered) generate receipts, making it possible to audit when degradation occurred and what recovered it.

### Scheduler (Gated Automation)

SQLite-backed job scheduler supporting cron and interval triggers.

**Gating pipeline:** Before any scheduled job executes:
1. System must be in READY state (onboarding complete)
2. Local LLM must be healthy
3. Job-specific gates must pass
4. Owner-gated jobs require explicit approval

**Job receipts:** Every run, failure, and skip generates a typed receipt (`scheduled_job_run`, `scheduled_job_failed`, `scheduled_job_skipped`).

### War Room (Operator Dashboard)

The War Room is a React SPA (Vite + React 18 + TypeScript + Tailwind) providing full system observability:

- **Command** — Chat interface for interacting with Lancelot
- **Health** — System status, subsystem health, degradation alerts
- **Governance** — Risk tier distribution, policy decisions, approval queue
- **Trust** — Per-connector trust scores, graduation history
- **APL** — Approval pattern learning rules, proposals, confidence
- **Receipts** — Searchable audit trail with drill-down traces
- **Scheduler** — Active jobs, run history, skip reasons
- **Memory** — Tier sizes, quarantine queue, recent commits

The War Room communicates with Lancelot exclusively through the Gateway REST API — it has no direct access to internal objects.

---

## Security Architecture

Security is enforced in layers, not delegated to the model:

```
Input Layer:   Rate Limiter → Size Check → InputSanitizer (16 patterns, homoglyphs)
                                    ↓
Governance:    Soul constraints → Policy Engine → Risk classification
                                    ↓
Execution:     Command denylist → Path traversal → Workspace boundary → Docker sandbox
                                    ↓
Output:        Receipt generation → PII redaction → Response assembly
```

**Key principles:**
- The model is treated as **untrusted logic** inside a governed system
- Governance is enforced **outside the model** (Soul + Policy Engine)
- Tool outputs are treated as **untrusted input** — never executed directly
- Secrets are **never stored in memory**, never logged in plaintext, never sent to models unless explicitly required
- All subsystems have **kill switches** (feature flags)

For the full security model, see [Security Posture](security.md).

---

## Subsystem Independence

A core architectural principle: **any subsystem can be disabled without breaking the system.**

| If You Disable... | What Happens |
|-------------------|-------------|
| Soul | Actions run without constitutional constraints (not recommended) |
| Skills | Only built-in capabilities available |
| Health Monitor | No background health checks, endpoints still respond |
| Scheduler | No automated jobs, manual execution still works |
| Memory vNext | Falls back to basic context management |
| Tool Fabric | No tool execution, conversation-only mode |

This is implemented through feature flags (`FEATURE_SOUL`, `FEATURE_SKILLS`, etc.) that gate each subsystem at initialization. When a subsystem is disabled, its code paths are skipped and its API endpoints return appropriate "not available" responses.

---

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11+ |
| API Framework | FastAPI (Uvicorn ASGI) |
| War Room | React 18 + Vite + TypeScript + Tailwind |
| Legacy UI | Streamlit |
| Data Validation | Pydantic v2 |
| Configuration | PyYAML |
| LLM Providers | Google GenAI, OpenAI, Anthropic SDKs |
| Local Inference | llama-cpp-python (GGUF format) |
| Persistence | SQLite (scheduler, memory), JSON (registries, receipts) |
| Encryption | cryptography library |
| Containerization | Docker + Docker Compose |
| Testing | pytest (1900+ tests) |

---

## Design Decisions

1. **Context over retrieval.** Lancelot uses long-context windows (128k+ tokens) with deterministic context loading instead of vector-based RAG. This eliminates the information loss inherent in embedding similarity search.

2. **Lane-based routing for cost optimization.** The local model handles 60-80% of tasks (classification, redaction, summarization) at zero API cost. Only complex reasoning escalates to cloud providers.

3. **Constitutional governance, not prompt engineering.** The Soul is a data structure enforced by code, not a system prompt that the model might ignore. If the Soul forbids an action, the code blocks it before the model is consulted.

4. **Proportional governance overhead.** T0 actions (reads, status checks) get near-instant policy lookup. T3 actions (irreversible operations) get full approval gates and sync verification. The overhead matches the risk.

5. **Receipts as ground truth.** Every action produces a durable record. This enables post-hoc auditing, decision chain reconstruction, and trust scoring based on observed outcomes rather than model confidence.

6. **Single-owner allegiance.** Lancelot serves one owner. This eliminates an entire category of security concerns (multi-tenant data isolation, role-based access control, permission escalation between users) and keeps the governance model simple.

7. **Docker-first deployment.** The Tool Fabric relies on Docker for execution sandboxing. Bare-metal is supported but loses the container isolation that makes tool execution safe.
