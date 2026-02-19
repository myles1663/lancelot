# Functional Specifications: Project Lancelot v7.0

**Document Version:** 7.1
**Last Updated:** 2026-02-18
**Status:** Current — reflects v4 Multi-Provider + vNext2 Soul/Skills/Heartbeat/Scheduler + vNext3 Memory + Tool Fabric + Security Hardening + V24 Competitive Intelligence + V25 Autonomy Loop v2

---

## 1. System Overview

Lancelot v7.0 is a self-hosted, high-context autonomous AI agent. It operates as a single-owner "Paladin" within a secure Docker perimeter, replacing stateless chat paradigms with a persistent, stateful "War Room" experience. The system combines multi-provider LLM routing, constitutional governance (Soul), modular capabilities (Skills), real-time health monitoring (Heartbeat), automated job scheduling (Scheduler), tiered commit-based memory (Memory vNext), and provider-agnostic tool execution (Tool Fabric) into a unified platform.

### 1.1 Design Principles

- **Context over Retrieval:** Long-context windows (128k+ tokens) replace lossy vector retrieval with deterministic context loading.
- **Autonomy requires Verification:** Every autonomous action passes through a Planner/Verifier pipeline with receipt-based auditing.
- **Single-Owner Allegiance:** All decisions, actions, and communications serve one owner exclusively.
- **Constitutional Governance:** An immutable Soul document defines behavioral boundaries, risk tolerances, and ethical constraints.
- **Lane-Based Routing:** Tasks route through cost-optimized lanes (local LLM first, flagship providers for complex reasoning).

---

## 2. System Actors

| Actor | Role | Implementation |
|-------|------|----------------|
| **Commander** (Owner) | Defines goals, approves gated actions, manages Soul amendments | War Room UI, Chat interface |
| **Paladin** (Lancelot Core) | Orchestrates context, safety, routing, and execution | `orchestrator.py` |
| **Strategist** (Planner) | Decomposes complex goals into structured step plans | `planner.py` |
| **Inquisitor** (Verifier) | Audits step outputs against success criteria | `verifier.py` |
| **Sentinel** (Health Monitor) | Continuously monitors subsystem health | `health/monitor.py` |
| **Chronicler** (Scheduler) | Manages and executes periodic automated jobs | `scheduler/service.py` |
| **Archivist** (Memory Manager) | Manages tiered memory with commit-based editing | `memory/api.py` |
| **Armorer** (Tool Fabric) | Orchestrates provider-agnostic tool execution with policy enforcement | `tools/fabric.py` |

---

## 3. Core Functional Areas

### FA-01: Context Environment (The "Mind")

**Module:** `src/core/context_env.py`

- **F-01.1 Deterministic Context Loading:** Critical files (`USER.md`, `RULES.md`, `MEMORY_SUMMARY.md`) load at the top of every prompt context window.
- **F-01.2 Explicit File Loading:** Users request file reads via natural language; contents register as `ContextItem` objects that decrement the token budget.
- **F-01.3 Token Budgeting:** A hard limit (default 128k tokens) is enforced. When exceeded, Least Recently Used items are evicted or summarized.
- **F-01.4 Context Caching:** Gemini 2.0 context caching provides 75-90% token savings on repeated context windows.
- **F-01.5 Workspace Search:** Basic string matching (`search_workspace`) and AST-based code outlines (`get_file_outline`) for code navigation.

### FA-02: Autonomous Loop (Plan-Execute-Verify)

**Modules:** `src/agents/planner.py`, `src/agents/verifier.py`

- **F-02.1 Planning:** Complex goals (>50 tokens or containing action keywords) invoke the Planner, which generates a JSON-structured step list using Gemini Flash with JSON schema enforcement. Low-risk execution requests (search, draft, summarize, check status) bypass the pipeline via just-do-it mode (V21) and go straight to the agentic loop.
- **F-02.2 Execution:** The Executor runs steps sequentially through the Model Router, selecting appropriate lanes based on task type and risk level.
- **F-02.3 Verification:** After each step, the Verifier (temperature 0.1) analyzes output against the step's success criteria.
  - **Success:** Context updated, proceed to next step.
  - **Failure:** Verifier suggests correction; Executor retries (max 3 attempts per step).
- **F-02.4 Receipt Chain:** Every step generates a Receipt linking parent/child actions for full traceability.
- **F-02.5 Structured Output (V23):** When `FEATURE_STRUCTURED_OUTPUT` is enabled, the agentic loop's text responses are constrained to a JSON schema with explicit `response_to_user`, `actions_taken`, and `next_action` fields. A presentation layer (`ResponsePresenter`) converts the verified JSON back to readable chat text after cross-referencing `actions_taken` against tool receipts. A claim verifier (`ClaimVerifier`, controlled by `FEATURE_CLAIM_VERIFICATION`) additionally scans the free-text `response_to_user` for unverified action claims.
- **F-02.6 Unified Classification (V23):** When `FEATURE_UNIFIED_CLASSIFICATION` is enabled, a single Gemini Flash call with structured output replaces the 7-function keyword heuristic chain for intent classification. Returns intent, confidence, is_continuation, and requires_tools. Falls back to the keyword classifier on failure.
- **F-02.7 Deep Reasoning Pass (V25):** When `FEATURE_DEEP_REASONING_LOOP` is enabled, Lancelot performs a dedicated reasoning pass before entering the agentic loop on complex requests. The deep reasoning pass uses a deep model with an elevated thinking budget to analyze the request, identify capability gaps, anticipate risks, and form a strategy. The resulting `ReasoningArtifact` is injected as context into the agentic loop, giving the execution phase a strategic foundation. From the user's perspective, Lancelot now thinks deeply before acting on complex requests — responses to multi-step or tool-heavy tasks are more deliberate and better informed.
- **F-02.8 Governed Negotiation (V25):** When governance blocks an action during the agentic loop, the system now provides structured feedback (via `GovernanceFeedback`) instead of a generic `BLOCKED` message. The feedback includes the blocked action, the specific policy rule that triggered the block, and a set of alternative approaches. The model receives this feedback in-context and can adapt — choosing a lower-risk path, requesting owner approval, or decomposing the action into smaller steps. The user-visible effect is that Lancelot no longer stalls when blocked; it explains what was blocked and proposes alternatives.
- **F-02.9 Task Experience Memory (V25):** After completing a task, the orchestrator records a `TaskExperience` in episodic memory under the `task_experience` namespace. Each record captures the original request, the reasoning artifact, capability gaps encountered, actions taken, the outcome, and duration. On future requests, the context compiler retrieves relevant past experiences during the deep reasoning pass, enabling Lancelot to learn from previous task outcomes. The user-visible effect is that repeated or similar requests benefit from accumulated knowledge — Lancelot avoids strategies that failed before and reuses approaches that succeeded.
- **F-02.10 Capability Gap Identification (V25):** During the deep reasoning pass, the reasoning output is scanned for `CAPABILITY GAP:` markers that identify tools, skills, or knowledge the system lacks for the current task. These gaps are recorded in the `ReasoningArtifact` and the subsequent `TaskExperience`, making them available for future skill factory proposals and system improvement.

### FA-03: Multi-Provider Model Routing

**Modules:** `src/core/model_router.py`, `src/core/provider_profile.py`

- **F-03.1 Lane Architecture:** Four prioritized routing lanes:

  | Lane | Priority | Purpose | Provider |
  |------|----------|---------|----------|
  | `local_redaction` | 1 | PII redaction | Local GGUF model |
  | `local_utility` | 2 | Classification, intent verification, summarization, JSON extraction | Local GGUF model |
  | `flagship_fast` | 3 | Orchestration, tool calls, standard reasoning | Gemini Flash / GPT-4o-mini / Claude Haiku |
  | `flagship_deep` | 4 | Planning, high-risk decisions, complex reasoning | Gemini Pro / GPT-4o / Claude Sonnet |

- **F-03.2 Provider Profiles:** Three flagship providers supported, each with `fast` and `deep` lane configurations:
  - **Google Gemini:** gemini-3-flash-preview (fast), gemini-3-pro-preview (deep)
  - **OpenAI:** gpt-4o-mini (fast), gpt-4o (deep)
  - **Anthropic:** claude-3-5-haiku (fast), claude-sonnet-4 (deep)

- **F-03.3 Escalation:** Automatic escalation from fast to deep lane on:
  - Risk keyword detection in input
  - Task complexity exceeding fast-lane capacity
  - Fast-lane execution failure (automatic retry on deep)

- **F-03.4 Routing Decisions:** Every routing event produces a `RouterDecision` with: id, timestamp, task_type, lane, model, rationale, elapsed_ms, success, and error fields.

- **F-03.5 Usage Tracking:** Per-lane token consumption, cost estimates, and local-model savings are tracked in real time.

### FA-04: Receipt System (Accountability)

**Modules:** `src/shared/receipts.py`, `src/shared/receipt_service.py`

- **F-04.1 Universal Receipt Generation:** Every discrete action (LLM call, file read, tool call, plan step, verification) generates a structured Receipt.
- **F-04.2 Receipt Schema:** Receipts include: id (UUID), timestamp, action_type, action_name, inputs, outputs, status, duration_ms, token_count, cognition_tier, parent_id, quest_id, error_message, and metadata.
- **F-04.3 Cognition Tiers:**

  | Tier | Value | Description |
  |------|-------|-------------|
  | DETERMINISTIC | 0 | No LLM involved |
  | CLASSIFICATION | 1 | Simple routing decisions |
  | PLANNING | 2 | Complex multi-step planning |
  | SYNTHESIS | 3 | High-risk synthesis and generation |

- **F-04.4 Receipt Persistence:** Receipts are stored in `lancelot_data/receipts/` and indexed for short-term memory and audit trails.
- **F-04.5 Traceability:** Parent-child receipt linking enables full reconstruction of decision chains across multi-step operations.

### FA-05: Soul Subsystem (Constitutional Identity)

**Modules:** `src/core/soul/store.py`, `src/core/soul/linter.py`, `src/core/soul/amendments.py`, `src/core/soul/api.py`

- **F-05.1 Soul Document:** A versioned YAML document defining Lancelot's mission, allegiance, autonomy posture, risk rules, approval rules, tone invariants, memory ethics, and scheduling boundaries.
- **F-05.2 Soul Versioning:** Multiple soul versions stored in `soul/soul_versions/soul_vN.yaml`. An `ACTIVE` pointer file designates the current version.
- **F-05.3 Soul Linting:** Five invariant checks enforce constitutional constraints at load time:
  - Destructive actions must appear in `requires_approval`
  - Tone invariants must prohibit silent degradation
  - Scheduling boundaries must prevent autonomous irreversible actions
  - Approval rules must define at least one channel
  - Memory ethics must contain at least one rule
- **F-05.4 Soul Amendments:** Proposals follow a controlled workflow: `PENDING` -> `APPROVED` -> `ACTIVATED`. Only the authenticated owner can approve and activate amendments.
- **F-05.5 Soul API Endpoints:**
  - `GET /soul/status` — Active version, available versions, pending proposals
  - `POST /soul/proposals/{id}/approve` — Owner approves amendment (Bearer token required)
  - `POST /soul/proposals/{id}/activate` — Owner activates amendment (triggers linter validation)

### FA-06: Skills Subsystem (Modular Capabilities)

**Modules:** `src/core/skills/schema.py`, `src/core/skills/registry.py`, `src/core/skills/executor.py`, `src/core/skills/factory.py`, `src/core/skills/governance.py`

- **F-06.1 Skill Manifests:** Declarative `skill.yaml` files define each skill's name, version, permissions, inputs, outputs, risk level, and scheduling eligibility.
- **F-06.2 Skill Registry:** JSON-persisted registry (`data/skills_registry.json`) manages installation, enabling, disabling, and uninstallation of skills. Each entry tracks ownership (SYSTEM/USER/MARKETPLACE) and signature state (UNSIGNED/SIGNED/VERIFIED).
- **F-06.3 Skill Execution:** The SkillExecutor loads skill modules, validates permissions, and emits receipts for every execution.
- **F-06.4 Skill Factory:** Proposal pipeline for creating new skills:
  - `generate_skeleton()` creates a complete skill directory structure with manifest, execute.py, and tests
  - Proposals start as PENDING and require owner approval before installation
  - Approved proposals install into the registry automatically
- **F-06.5 Marketplace Governance:**
  - Marketplace skills default to restricted permissions: `read_input`, `write_output`, `read_config`
  - Elevated permissions require explicit owner approval
  - `build_skill_package()` creates distributable .zip archives
  - `verify_marketplace_permissions()` validates permission compliance

### FA-07: Heartbeat Subsystem (Health Monitoring)

**Modules:** `src/core/health/types.py`, `src/core/health/api.py`, `src/core/health/monitor.py`

- **F-07.1 Health Endpoints:**
  - `GET /health/live` — Liveness probe, always returns 200 if process is running
  - `GET /health/ready` — Readiness probe with full HealthSnapshot
- **F-07.2 HealthSnapshot Model:** Reports: ready (bool), onboarding_state, local_llm_ready, scheduler_running, last_health_tick_at, last_scheduler_tick_at, degraded_reasons (array), and timestamp.
- **F-07.3 Health Monitor Loop:** Background thread runs periodic health checks against registered check functions. Emits receipts on state transitions:
  - `health_ok` — All checks passing
  - `health_degraded` — One or more checks failing
  - `health_recovered` — Transition from degraded back to healthy
- **F-07.4 Safe Error Handling:** Health endpoints never leak stack traces or internal error details. Provider failures return safe fallback snapshots.

### FA-08: Scheduler Subsystem (Automated Jobs)

**Modules:** `src/core/scheduler/schema.py`, `src/core/scheduler/service.py`, `src/core/scheduler/executor.py`

- **F-08.1 Job Configuration:** YAML-based job definitions (`config/scheduler.yaml`) supporting:
  - **Interval triggers:** Execute every N seconds
  - **Cron triggers:** Standard 5-field cron expressions
- **F-08.2 Job Persistence:** SQLite-backed job store (`data/scheduler.sqlite`) tracks job state, run history, and execution counts.
- **F-08.3 Gating Pipeline:** Jobs pass through configurable gates before execution:
  - **Onboarding gate:** System must be in READY state
  - **LLM health gate:** Local model must be responding
  - **Approval gate:** Owner-gated jobs skip unless approvals are granted
- **F-08.4 Job Receipts:** Every execution emits a typed receipt:
  - `scheduled_job_run` — Successful execution
  - `scheduled_job_failed` — Execution error
  - `scheduled_job_skipped` — Gating or approval rejection
- **F-08.5 Default Jobs:**
  - `health_sweep` — Periodic health check (60s interval)
  - `memory_cleanup` — Daily memory maintenance (cron: 0 3 * * *)
  - `credential_rotation` — Monthly credential rotation (disabled, requires owner approval)

### FA-09: Unified Onboarding

**Modules:** `src/ui/onboarding.py`, `src/core/onboarding_snapshot.py`

- **F-09.1 Identity Bond:** System requires a user name to create `USER.md` persistent profile.
- **F-09.2 Authentication Fork:**
  - **API Mode:** Prompts for `GEMINI_API_KEY` (or other provider keys)
  - **OAuth Mode:** Detects `application_default_credentials.json` (ADC) for enterprise integration
- **F-09.3 Communications Setup:** Configures Google Chat and/or Telegram webhook endpoints.
- **F-09.4 Onboarding State Machine:** Persistent state tracking with recovery commands: STATUS, BACK, RESTART STEP, RESEND CODE, RESET.

### FA-10: Crusader Mode (High-Agency Autonomy)

**Module:** `src/agents/crusader.py`

- **F-10.1 Activation:** Engaged via "Engage Crusader" button or "Crusader" keyword in chat.
- **F-10.2 Behavior Changes:**
  - Disables draft mode (unless extremely low confidence)
  - Increases tool autonomy (auto-approves low-risk file operations)
  - Uses decisive system prompt injection for assertive responses
- **F-10.3 Safety Bounds:** Crusader Mode does not override Soul constraints, approval requirements, or risk rules.

### FA-11: Security Layer

**Module:** `src/core/security.py`

- **F-11.1 Input Sanitization:** Detects and blocks prompt injection attempts including:
  - Banned phrase matching (16 common injection patterns)
  - Cyrillic homoglyph normalization
  - Regex-based suspicious pattern detection (10 patterns)
  - Zero-width character stripping and URL decoding
- **F-11.2 Audit Logging:** All security-relevant events are logged with timestamps and request context.
- **F-11.3 Network Interception:** Monitors and controls outbound network requests.
- **F-11.4 Rate Limiting:** Sliding-window per-IP rate limiter (60 requests/60 seconds default).
- **F-11.5 Request Size Limits:** 1 MB maximum request body size.

### FA-12: SafeREPL (Internal Execution)

- **F-12.1 Supported Commands:** `ls`, `cat`, `grep`, `find`, `cp`, `mv`
- **F-12.2 Implementation:** Commands execute via Python standard library functions (`shutil`, `os`, `glob`) rather than shell subprocesses, eliminating shell injection risks.

### FA-13: Feature Flags (Subsystem Kill Switches)

**Module:** `src/core/feature_flags.py`

- **F-13.1 Available Flags:**
  - `FEATURE_SOUL` — Enable/disable Soul subsystem (default: true)
  - `FEATURE_SKILLS` — Enable/disable Skills subsystem (default: true)
  - `FEATURE_HEALTH_MONITOR` — Enable/disable background health monitoring (default: true)
  - `FEATURE_SCHEDULER` — Enable/disable Scheduler subsystem (default: true)
  - `FEATURE_MEMORY_VNEXT` — Enable/disable Memory vNext subsystem (default: true)
  - `FEATURE_TOOLS_FABRIC` — Enable/disable Tool Fabric subsystem (default: true)
  - `FEATURE_TOOLS_CLI_PROVIDERS` — Enable/disable CLI tool providers (default: false)
  - `FEATURE_TOOLS_ANTIGRAVITY` — Enable/disable Antigravity providers (default: false)
  - `FEATURE_TOOLS_NETWORK` — Enable/disable network operations (default: false)
  - `FEATURE_TOOLS_HOST_EXECUTION` — Enable/disable host (non-Docker) execution (default: false)
  - `FEATURE_GITHUB_SEARCH` — Enable/disable GitHub public API search (default: false)
  - `FEATURE_COMPETITIVE_SCAN` — Enable/disable competitive intelligence scanning (default: false)
  - `FEATURE_DEEP_REASONING_LOOP` — Enable/disable deep reasoning pass, governed negotiation, and task experience memory (default: false)
- **F-13.2 Configuration:** Flags read from environment variables at startup. Accept `true`, `1`, `yes` (case-insensitive).
- **F-13.3 Runtime Reload:** `reload_flags()` re-reads flags from environment without restart.

### FA-14: Memory vNext (Tiered Memory)

**Modules:** `src/core/memory/store.py`, `src/core/memory/commits.py`, `src/core/memory/compiler.py`, `src/core/memory/api.py`, `src/core/memory/sqlite_store.py`, `src/core/memory/index.py`

- **F-14.1 Core Block Store:** Five core memory blocks (persona, human, mission, operating_rules, workspace_state) stored with schema validation and versioning. Each block has a type, content string, and metadata.
- **F-14.2 Tiered Memory Storage:** Three memory tiers with distinct lifecycles:
  - **Working:** Short-term memory for active session context
  - **Episodic:** Session-scoped memory persisted across interactions
  - **Archival:** Long-term memory with permanent retention
- **F-14.3 Commit-Based Editing:** All memory modifications go through a transactional commit pipeline:
  - `begin_edits(commit_id)` — Start a staged commit with snapshot isolation
  - `apply_edit(commit_id, op, target, data)` — Stage an insert, update, delete, or rethink operation
  - `finish_edits(commit_id)` — Apply all staged changes atomically
  - `rollback(commit_id)` — Restore to pre-commit snapshot state
- **F-14.4 Context Compiler:** Assembles memory into a token-budgeted context window:
  - Core blocks loaded first with per-type token budgets
  - Tiered items appended by relevance and recency
  - Total output bounded by configurable max token limit
- **F-14.5 Governed Self-Edits:** Memory operations enforce governance:
  - Provenance tracking (system, user, agent, tool) on every edit
  - Quarantine flow for suspicious edits requiring owner approval
  - Undo log for rollback of item-level operations
- **F-14.6 SQLite Persistence:** Thread-safe SQLite storage with:
  - Thread-local connections for concurrent access
  - Tiered tables for working, episodic, and archival memory
  - Full-text search index with position-based relevance scoring
- **F-14.7 Memory API Endpoints:**
  - `GET /memory/status` — Current memory statistics and tier counts
  - `POST /memory/edit` — Submit a governed memory edit
  - `POST /memory/compile` — Compile context from memory
  - `GET /memory/search` — Search across memory tiers
  - `POST /memory/quarantine/{id}/approve` — Approve quarantined edit
- **F-14.8 Scheduled Maintenance:** Automatic memory jobs:
  - `memory_cleanup` — Daily cleanup of expired working memory items
  - Archival promotion of mature episodic items

### FA-15: Tool Fabric (Provider-Agnostic Execution)

**Modules:** `src/tools/fabric.py`, `src/tools/contracts.py`, `src/tools/policies.py`, `src/tools/router.py`, `src/tools/health.py`, `src/tools/receipts.py`

- **F-15.1 Capability Interfaces:** Seven protocol-based capability types:

  | Capability | Operations | Risk Level |
  |-----------|-----------|------------|
  | ShellExec | Run commands with stdout/stderr capture | MEDIUM-HIGH |
  | RepoOps | git status, diff, apply_patch, commit, branch, checkout | MEDIUM |
  | FileOps | read, write (atomic), list, delete with file hashing | LOW-MEDIUM |
  | WebOps | HTTP requests with policy filtering | MEDIUM |
  | UIBuilder | Template and generative UI scaffolding | LOW |
  | DeployOps | Deployment operations | HIGH |
  | VisionControl | Screen capture, element location, UI actions | MEDIUM |

- **F-15.2 Provider Architecture:** Pluggable provider implementations:
  - **LocalSandboxProvider:** Docker-based execution for ShellExec, RepoOps, FileOps
  - **TemplateScaffolder:** Template-based UI scaffolding (4 template packs)
  - **AntigravityUIProvider:** AI-generative UI scaffolding with template fallback
  - **AntigravityVisionProvider:** Vision-based UI control via Antigravity
- **F-15.3 Policy Engine:** Centralized security enforcement:
  - Command denylist with shlex-based token matching
  - Path traversal detection (encoded, double-encoded)
  - Workspace boundary enforcement with symlink resolution
  - Sensitive path pattern blocking (.env, .ssh, credentials)
  - Network policy (disabled by default)
  - Automatic redaction of passwords, API keys, tokens in receipts
- **F-15.4 Provider Routing:** Capability-based provider selection:
  - Priority-based selection with health-aware failover
  - Policy engine integration for intent-based routing
  - RouteDecision captures selection rationale and alternatives
- **F-15.5 Tool Receipts:** Every invocation generates typed receipts:
  - ToolReceipt: capability, action, provider, inputs (redacted), outputs (bounded), policy snapshot, file changes, verification results
  - VisionReceipt: screenshots (hashed), detected elements, actions, confidence scores
- **F-15.6 Safe Mode:** Toggle restricts execution to local sandbox providers only, disabling optional/external providers.

### FA-16: Autonomy Loop v2 (V25)

**Modules:** `src/core/orchestrator.py`, `src/core/reasoning_artifact.py`

- **F-16A.1 Deep Reasoning Before Action:** When `FEATURE_DEEP_REASONING_LOOP` is enabled and the request meets complexity thresholds (multi-step, tool-heavy, or high-risk), a deep reasoning pass runs before the agentic loop. The user sees more thoughtful, strategically grounded responses on complex tasks.
- **F-16A.2 Capability Gap Surfacing:** The reasoning pass identifies tools and skills the system lacks. These gaps are captured in the `ReasoningArtifact` and surfaced in task experience records. Over time, this data can drive skill factory proposals for missing capabilities.
- **F-16A.3 Adaptive Governance Response:** Governed negotiation replaces hard `BLOCKED` stops with structured `GovernanceFeedback` that the model uses to adapt in-context. The user sees explanations and alternatives rather than dead ends.
- **F-16A.4 Experiential Learning:** Task experience records in episodic memory enable the system to recall past task outcomes. Future requests of a similar nature benefit from accumulated successes and failures, improving reliability over time.
- **F-16A.5 GitHub Search Reliability:** The `github_search` builtin skill now works correctly in all registration scenarios. A fallback path in `executor.run()` ensures the skill resolves even when the registry-based lookup fails, eliminating silent failures during competitive research.

### FA-17: Competitive Intelligence (V24)

**Modules:** `src/core/competitive_scan.py`, `src/tools/github_search.py`

- **F-17.1 Sourced Intelligence:** When performing competitive research or external intelligence gathering, Lancelot must cite specific source URLs for each finding. Unsourced claims must not be presented as verified intelligence. This requirement is enforced via a Soul `tone_invariant` that rejects intelligence outputs lacking provenance URLs.
- **F-17.2 GitHub Search:** When `FEATURE_GITHUB_SEARCH` is enabled, Lancelot can search GitHub's public API for repositories, commits, issues/PRs, and releases. Results include browseable source URLs that satisfy the sourced-intelligence requirement (F-17.1). Searches are executed through the Tool Fabric WebOps capability and produce standard tool receipts.
- **F-17.3 Competitive Scan Memory:** When both `FEATURE_COMPETITIVE_SCAN` and `FEATURE_MEMORY_VNEXT` are enabled, competitive research results are stored in episodic memory with a `competitive_scan` provenance tag. Subsequent scans of the same target retrieve previous scan results from memory and produce a change-analysis summary highlighting new, removed, and modified findings since the last scan.

---

## 4. Interfaces

### 4.1 War Room (Streamlit Dashboard)

**Module:** `src/ui/war_room.py`

The primary command center with specialized panels:

| Panel | Module | Capabilities |
|-------|--------|-------------|
| **Soul Panel** | `src/ui/panels/soul_panel.py` | View active soul version, pending proposals, soul details |
| **Skills Panel** | `src/ui/panels/skills_panel.py` | Browse installed skills, enable/disable, view manifests |
| **Health Panel** | `src/ui/panels/health_panel.py` | Real-time health snapshot, degraded reasons, LLM status |
| **Scheduler Panel** | `src/ui/panels/scheduler_panel.py` | Job listing, manual triggers, enable/disable jobs |
| **Memory Panel** | `src/ui/panels/memory_panel.py` | Tier browser, edit history, quarantine approvals |
| **Tool Fabric Panel** | `src/ui/panels/tools_panel.py` | Provider health, capability routing, receipts, safe mode |

All panels handle backend-down scenarios gracefully with safe fallback displays.

### 4.2 Chat Interface

- Continuous scroll conversation with distinct separation from system logs
- Supports Crusader Mode toggle
- Natural language command routing

### 4.3 REST API (FastAPI Gateway)

**Module:** `src/core/gateway.py`

- `POST /chat` — Send messages to the orchestrator
- `GET /health/live` — Liveness probe
- `GET /health/ready` — Readiness probe with HealthSnapshot
- `GET /system/status` — Full system provisioning status
- `GET /onboarding/status` — Onboarding progress
- `GET /router/decisions` — Recent routing decisions (max 50, deque of 200)
- `GET /router/stats` — Routing statistics
- `GET /usage/summary` — Token consumption and cost telemetry
- `GET /usage/lanes` — Per-lane usage breakdown
- `GET /usage/savings` — Local model cost savings estimate
- `GET /soul/status` — Soul version and proposal status
- WebSocket `/ws` — Real-time bidirectional communication

### 4.4 Native Launcher

**Module:** `src/ui/lancelot_gui.py`

Desktop application wrapper using `pywebview` for OS-native window experience.

---

## 5. Data Flows

### 5.1 Message Processing Flow

```
User Message
    |
    v
InputSanitizer --> [Rejected if injection detected]
    |
    v
Orchestrator.chat()
    |
    +--> Context Environment (load deterministic context)
    |
    +--> Model Router (select lane)
    |        |
    |        +--> Local LLM (utility/redaction tasks)
    |        |
    |        +--> Flagship Provider (fast/deep reasoning)
    |
    +--> Receipt Generation
    |
    v
Response (with confidence score)
```

### 5.2 Scheduled Job Flow

```
Scheduler Tick
    |
    v
Job Record (enabled?)
    |
    v
Gate Pipeline
    +--> Onboarding READY?
    +--> LLM Healthy?
    +--> Approvals Granted?
    |
    v
Skill Executor
    |
    v
Receipt Emission (run/failed/skipped)
```

### 5.3 Soul Amendment Flow

```
create_proposal(from_version, yaml_text)
    |
    v
PENDING Proposal
    |
    v
POST /soul/proposals/{id}/approve  (owner Bearer token)
    |
    v
APPROVED Proposal
    |
    v
POST /soul/proposals/{id}/activate  (owner Bearer token)
    |
    +--> Lint validation (5 invariant checks)
    +--> Write version file
    +--> Update ACTIVE pointer
    |
    v
ACTIVATED (new soul version live)
```

### 5.4 Memory Commit Flow

```
Memory Edit Request
    |
    v
begin_edits(commit_id) --> Snapshot current state
    |
    v
apply_edit(commit_id, op, target, data)
    +--> Provenance check
    +--> Quarantine check (suspicious? -> hold for review)
    +--> Record undo entry
    |
    v
finish_edits(commit_id) --> Apply atomically
    |
    v
Receipt Emission (memory_edit_committed)
```

### 5.5 Tool Execution Flow

```
Tool Invocation Request
    |
    v
PolicyEngine.evaluate(intent)
    +--> Command denylist check
    +--> Path traversal check
    +--> Risk assessment
    |
    v
ProviderRouter.select(capability)
    +--> Health check providers
    +--> Priority-based selection
    +--> Failover if unhealthy
    |
    v
Provider.execute(action)
    +--> Docker sandbox (if ShellExec)
    +--> Workspace boundary enforcement (if FileOps)
    |
    v
ToolReceipt Generation
    +--> Policy snapshot
    +--> File changes (hashed)
    +--> Output bounding
```

---

## 6. Error Handling

- **API Endpoints:** Return structured JSON error responses. Never leak stack traces, internal paths, or exception details to clients.
- **Health Probes:** Always return 200 status. Degradation is communicated via `ready=false` and `degraded_reasons` array.
- **Skill Execution:** Errors are caught, logged, and reported via receipts. Failed skills do not crash the scheduler or orchestrator.
- **Gate Failures:** Jobs are skipped with descriptive receipts when gates fail, including gate exception handling.
- **Soul Loading:** Lint failures at load time produce descriptive `SoulStoreError` messages identifying which invariants were violated.
- **Memory Commits:** Failed commits rollback to snapshot state with item-level undo support. Error messages sanitized for API responses.
- **Tool Fabric:** Policy violations return structured errors without leaking internal paths. Provider failures trigger failover to next healthy provider.
