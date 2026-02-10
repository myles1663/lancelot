# CLAUDE.md — Lancelot Development Reference

> **This file is the authoritative coding reference for AI agents working on Lancelot.**
> It replaces the need to re-read documentation files on every session.

---

## Session Rules

- **Push after fix:** If we implement a fix on the local deployed version, we push the fixes to the git repo.
- **Merge after test:** Once we test any branch and confirm it works, merge it back to `main` for a complete updated system.
- **Never commit secrets:** `.env` contains live API keys — never commit it.
- **Container names:** `lancelot_core` (core) and `lancelot_local_llm` (local model).
- **Git Bash path mangling:** Always prefix docker exec with `MSYS_NO_PATHCONV=1` when passing Linux paths.

---

## What Is Lancelot

Lancelot is a **self-hosted Governed Autonomous System (GAS)** — not a chatbot. It is an AI agent that plans, acts, remembers, and recovers under explicit constitutional control with full receipt-based accountability.

**Core Philosophy (Non-Negotiable):**
- **Context is King** — Long-context windows over lossy RAG
- **Autonomy requires Verification** — Planner/Verifier pipeline for all autonomous actions
- **Receipts are Truth** — Every action produces a durable, auditable receipt
- **Single-Owner Allegiance** — Soul governance ensures behavioral boundaries
- **Cost-Optimized Intelligence** — Local model for routine tasks, flagship for reasoning

**Contributor Guardrails (Non-Negotiable):**
- All actions must be receipt-traced
- New autonomy requires governance gates
- Memory must remain reversible
- Security guarantees come first

---

## Repository Layout

```
C:\Users\SSAdministrator\lancelot\
├── config/                        # YAML configs (models, router, scheduler)
├── docs/                          # Specs, blueprints, operations runbooks
│   ├── blueprints/
│   ├── operations/runbooks/       # soul.md, health.md, scheduler.md, skills.md, memory.md, tools.md
│   └── specs/                     # 10 detailed technical specs
├── local_models/                  # Local GGUF model service
│   ├── weights/                   # GGUF model files (Qwen3-8B, Hermes-2-Pro)
│   ├── prompts/                   # Utility task prompts (classify, extract, summarize, redact, rag_rewrite)
│   ├── server.py                  # llama-cpp-python server with post-processing
│   └── Dockerfile
├── lancelot_data/                 # Runtime data (receipts, registries, databases)
├── soul/                          # Constitutional identity
│   ├── ACTIVE                     # Version pointer ("v1")
│   ├── soul.yaml                  # Active constitutional document
│   └── soul_versions/             # Historical versions (soul_v1.yaml, etc.)
├── src/
│   ├── agents/                    # Planner, Verifier, Crusader, AntigravityEngine
│   ├── core/                      # Core orchestration (~30 files)
│   ├── integrations/              # Telegram, voice, MCP, calendar, API discovery
│   ├── memory/                    # Librarian v1/v2, vault, indexer
│   ├── shared/                    # Receipts, sandbox, live_session
│   ├── tools/                     # Tool Fabric (contracts, fabric, policies, providers)
│   └── ui/                        # War Room, launcher, panels
├── tests/                         # 102 test files, 1900+ tests
├── docker-compose.yml
├── Dockerfile                     # Python 3.11-slim, non-root lancelot user
├── requirements.txt               # 21 packages
├── pytest.ini                     # timeout=30s, markers: integration, slow, docker, local_model
└── CHANGELOG.md
```

---

## Architecture

### Service Topology

```
lancelot-core (:8000 FastAPI, :8501 Streamlit)
    |
    |-- HTTP --> local-llm (:8080 GGUF server)
    |-- HTTPS --> Gemini API / OpenAI API / Anthropic API
```

Both services on `lancelot_net` bridge network.

### Module Dependency Map (gateway.py is the entry point)

```
gateway.py (FastAPI + startup init)
    ├── orchestrator.py (chat() — central routing)
    │   ├── intent_classifier.py (PLAN_REQUEST, EXEC_REQUEST, MIXED_REQUEST, KNOWLEDGE_REQUEST, CONVERSATIONAL)
    │   ├── planning_pipeline.py (classify → build → render → governor → gate)
    │   ├── response_governor.py (blocks simulated-progress without real job_id)
    │   ├── model_router.py (4-lane routing engine)
    │   │   ├── provider_profile.py (loads models.yaml + router.yaml)
    │   │   ├── local_model_client.py (HTTP client for GGUF)
    │   │   └── usage_tracker.py
    │   ├── planner.py, verifier.py, crusader.py
    │   └── security.py (InputSanitizer, AuditLogger, NetworkInterceptor)
    ├── control_plane.py (War Room REST API)
    ├── soul/ (store, linter, amendments, api)
    ├── skills/ (schema, registry, executor, factory, governance)
    ├── health/ (types, api, monitor)
    ├── scheduler/ (schema, service, executor)
    ├── memory/ (store, schemas, commits, compiler, sqlite_store, api)
    ├── tools/ (contracts, fabric, policies, router, providers/)
    └── feature_flags.py
```

### Docker Compose (current working config)

```yaml
# lancelot-core
- Ports: 8000 (FastAPI), 8501 (Streamlit War Room)
- Volumes: ./lancelot_data → /home/lancelot/data, . → /home/lancelot/app
- PYTHONPATH: src/core, src/ui, src/agents, src/memory, src/shared, src/integrations, src/
- Command: uvicorn gateway:app --host 0.0.0.0 --port 8000 & streamlit run src/ui/war_room.py --server.port 8501
- Depends on: local-llm (healthy)

# local-llm
- Port: 8080
- GPU: NVIDIA CUDA 12.3.2, 15 GPU layers, 4096 context window
- Model: Qwen3-8B Q4_K_M (in /home/llm/models/)
- Health: curl -f http://localhost:8080/health (30s interval, 120s start period)
```

---

## Key Subsystems

### 1. Orchestrator (`src/core/orchestrator.py`)

Central nervous system. Routes messages and coordinates all modules.

```python
class LancelotOrchestrator:
    def __init__(self, data_dir: str)
    def chat(self, user_message: str, crusader_mode: bool = False) -> str
    def plan_task(self, goal: str) -> str
    def execute_plan(self, plan: str) -> str
    def execute_command(self, command: str) -> str
```

**chat() flow:** check governance → sanitize input → update context → call LLM via ModelRouter → parse confidence/actions → generate receipt → return response

**Intent routing:** PLAN_REQUEST/MIXED_REQUEST → PlanningPipeline; KNOWLEDGE_REQUEST → direct Gemini; CONVERSATIONAL → local or Gemini

### 2. Model Router (`src/core/model_router.py`)

4-lane routing with automatic escalation:

| Priority | Lane | Purpose |
|----------|------|---------|
| 1 | `local_redaction` | PII redaction (always first) |
| 2 | `local_utility` | classify_intent, extract_json, summarize, redact, rag_rewrite |
| 3 | `flagship_fast` | Orchestration, tool calls, retries (Gemini Flash / GPT-4o-mini) |
| 4 | `flagship_deep` | Planning, high-risk, complex reasoning (Gemini Pro / GPT-4o) |

**Escalation triggers:** risk (high-risk actions), complexity (multi-step), failure (fast-lane fail)

### 3. Planning Pipeline (`src/core/planning_pipeline.py`)

**Honest Closure Pipeline:** classify intent → build PlanArtifact → render markdown → governor check → output gate

**PlanArtifact required fields:** goal, context, assumptions, plan_steps, decision_points, risks, done_when, next_action

**Terminal outcomes:** COMPLETED_WITH_RECEIPT, COMPLETED_WITH_PLAN_ARTIFACT, CANNOT_COMPLETE, NEEDS_INPUT

### 4. Response Governor (`src/core/response_governor.py`)

Blocks simulated-progress language without a real job_id. Forbidden phrases: "I'm working on it", "I'm investigating", "Please allow me time", "I will report back", "I'm processing your request"

### 5. Soul (`src/core/soul/`)

Versioned constitutional governance document (YAML).

```python
# Load
soul = load_active_soul(soul_dir)  # Returns Soul model
# Version management
versions = list_versions(soul_dir)
set_active_version("v2", soul_dir)
```

**Autonomy posture (current v1):**
- Autonomous: classify_intent, summarize, redact, health_check
- Requires approval: deploy, delete, financial_transaction, credential_rotation

**5 invariant checks (linter):**
1. destructive_actions_require_approval (CRITICAL)
2. no_silent_degradation (CRITICAL)
3. scheduling_no_autonomous_irreversible (CRITICAL)
4. approval_channels_required (CRITICAL)
5. memory_ethics_required (WARNING)

### 6. Skills (`src/core/skills/`)

Modular capabilities with lifecycle: install → enable/disable → uninstall

```python
# Registry
registry = SkillRegistry(data_dir="data")
entry = registry.install_skill(path, ownership=SkillOwnership.USER)

# Executor
executor = SkillExecutor(registry)
result = executor.execute("skill_name", {"key": "value"})  # Returns SkillResult

# Factory (proposal pipeline)
factory = SkillFactory(data_dir="data")
proposal = factory.generate_skeleton(name, description, permissions)
```

**Built-in skills:** command_runner, repo_writer, network_client, service_runner
**Marketplace allowed permissions:** read_input, write_output, read_config (only)

### 7. Health Monitor (`src/core/health/`)

30-second interval background monitoring.

```python
# Endpoints
GET /health/live  → {"status": "alive"} (always 200)
GET /health/ready → HealthSnapshot JSON (200, never 500)
```

**HealthSnapshot fields:** ready, onboarding_state, local_llm_ready, scheduler_running, last_health_tick_at, degraded_reasons

### 8. Scheduler (`src/core/scheduler/`)

SQLite-backed job persistence with gated execution.

```python
service = SchedulerService(data_dir="data", config_dir="config")
service.register_from_config()  # Reads scheduler.yaml
jobs = service.list_jobs()
service.run_now("job_id")
```

**Gating pipeline:** job exists → job enabled → all gates pass → approvals granted → execute skill
**Config:** `config/scheduler.yaml` (currently empty — 0 jobs)
**DB:** `data/scheduler.sqlite`

### 9. Memory vNext (`src/core/memory/`)

Tiered memory with commit-based editing. **Note: FEATURE_MEMORY_VNEXT=false in current .env**

**Tiers:** working (short-term) → episodic (session) → archival (long-term)
**Core blocks:** persona, human, mission, operating_rules, workspace_state

```python
# Context compilation
compiler = ContextCompilerService(core_store, memory_manager)
ctx = compiler.compile_context(max_tokens=8000)

# Commit pipeline
cm = CommitManager(core_store, memory_manager)
cm.begin_edits(commit_id)     # Snapshot
cm.apply_core_edit(...)       # Modify
cm.finish_edits(commit_id)    # Apply atomically (or cm.rollback())
```

**Endpoints:** GET /memory/status, POST /memory/edit (Bearer), POST /memory/compile, GET /memory/search, POST /memory/quarantine/{id}/approve (Bearer), POST /memory/rollback/{commit_id} (Bearer)

### 10. Tool Fabric (`src/tools/`)

Provider-agnostic tool execution with 7 capabilities.

```python
class ToolFabric:
    def execute_command(self, command, workspace=None) -> ExecResult
    def read_file(self, path, workspace) -> str
    def write_file(self, path, content, workspace) -> FileChange
    def git_status(self, workspace) -> str
```

**Capabilities:** ShellExec, RepoOps, FileOps, WebOps, UIBuilder, DeployOps, VisionControl
**Execution pipeline:** PolicyEngine.evaluate() → ProviderRouter.select() → Provider.execute() → ToolReceipt
**Security gates:** command denylist (shlex), path traversal, workspace boundary, sensitive paths, network policy, risk assessment

---

## Feature Flags

All read from environment variables. `true`/`1`/`yes` = enabled; everything else = disabled.

| Flag | Default | Current (.env) | Purpose |
|------|---------|----------------|---------|
| `FEATURE_SOUL` | true | true | Constitutional identity |
| `FEATURE_SKILLS` | true | true | Modular capabilities |
| `FEATURE_HEALTH_MONITOR` | true | true | Health monitoring |
| `FEATURE_SCHEDULER` | true | true | Job scheduling |
| `FEATURE_MEMORY_VNEXT` | **false** | **false** | Tiered memory (not yet active) |
| `FEATURE_TOOLS_FABRIC` | true | true | Tool execution layer |
| `FEATURE_TOOLS_CLI_PROVIDERS` | false | — | CLI adapters |
| `FEATURE_TOOLS_ANTIGRAVITY` | false | true | Generative UI/Vision |
| `FEATURE_TOOLS_NETWORK` | false | — | Network access in sandbox |
| `FEATURE_TOOLS_HOST_EXECUTION` | false | — | **DANGEROUS:** Host execution |
| `FEATURE_AGENTIC_LOOP` | false | true | Agentic tool loop |
| `FEATURE_LOCAL_AGENTIC` | false | true | Route simple queries to local model |
| `FEATURE_RESPONSE_ASSEMBLER` | true | — | Response assembly |
| `FEATURE_EXECUTION_TOKENS` | true | — | Execution token minting |
| `FEATURE_TASK_GRAPH_EXECUTION` | true | — | Task graph compilation |
| `FEATURE_NETWORK_ALLOWLIST` | true | — | Network allowlist |
| `FEATURE_VOICE_NOTES` | true | — | Voice note support |

---

## Model Configuration

### Providers (`config/models.yaml`)

| Provider | Fast Model | Deep Model | Cache Model |
|----------|-----------|------------|-------------|
| Gemini (active) | gemini-2.0-flash (4096, 0.3) | gemini-2.0-pro (8192, 0.7) | gemini-2.0-flash (2048, 0.1) |
| OpenAI | gpt-4o-mini | gpt-4o | gpt-4o-mini |
| Anthropic | claude-3-5-haiku-latest | claude-sonnet-4-20250514 | claude-3-5-haiku-latest |

### Local Model

- **Model:** Qwen3-8B Q4_K_M (`local_models/weights/`)
- **Server:** `local_models/server.py` — OpenAI-compatible `/v1/chat/completions`
- **Post-processing:** strips `<think>` tags, converts `<tool_call>` XML → OpenAI format
- **Auto-injects** `/no_think` into system messages to suppress chain-of-thought
- **GPU:** 15 layers offloaded, 4096 context (GTX 1070, ~5GB VRAM free)

---

## API Endpoints

### Gateway (`gateway.py` — :8000)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/chat` | None/Bearer | Main chat endpoint. Field: `"text"` (not "message") |
| GET | `/health/live` | None | Liveness probe |
| GET | `/health/ready` | None | Readiness with HealthSnapshot |
| GET | `/system/status` | None | Full provisioning status |
| GET | `/router/decisions` | None | Recent routing decisions (max 50) |
| GET | `/router/stats` | None | Routing statistics |
| GET | `/usage/summary` | None | Token/cost telemetry |
| GET | `/usage/lanes` | None | Per-lane usage |
| GET | `/usage/savings` | None | Local model savings |
| POST | `/usage/reset` | None | Reset usage counters |
| GET | `/soul/status` | None | Soul version info |
| POST | `/soul/proposals/{id}/approve` | Bearer | Approve amendment |
| POST | `/soul/proposals/{id}/activate` | Bearer | Activate amendment |
| GET | `/memory/status` | None | Memory tier stats |
| POST | `/memory/edit` | Bearer | Governed memory edit |
| POST | `/memory/compile` | None | Compile context |
| GET | `/memory/search` | None | Search memory tiers |

### Local LLM (`:8080`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness probe |
| POST | `/v1/chat/completions` | OpenAI-compatible completions |

---

## Data Persistence

| Store | Format | Location (container) | Purpose |
|-------|--------|---------------------|---------|
| Soul versions | YAML | `soul/soul_versions/soul_vN.yaml` | Constitutional identity |
| Soul proposals | JSON | `data/soul_proposals.json` | Amendment workflow |
| Skill registry | JSON | `data/skills_registry.json` | Installed skills |
| Skill proposals | JSON | `data/skill_proposals.json` | Factory pipeline |
| Scheduler jobs | SQLite | `data/scheduler.sqlite` | Job state + run history |
| Memory items | SQLite | `data/memory.sqlite` | Tiered memory + FTS index |
| Memory blocks | In-memory | `CoreBlockStore` | Core memory blocks |
| Receipts | JSON | `lancelot_data/receipts/` | Audit trail |
| Chat log | JSON | `lancelot_data/chat_log.json` | Chat history |
| User profile | Markdown | `lancelot_data/USER.md` | Owner identity |
| Models config | YAML | `config/models.yaml` | Provider profiles |
| Router config | YAML | `config/router.yaml` | Routing rules |
| Scheduler config | YAML | `config/scheduler.yaml` | Job definitions |

---

## Security Architecture

### Input Layer
```
User Input → Rate Limiter (60/min) → Size Check (1MB) → InputSanitizer → Orchestrator
```
InputSanitizer: 16 banned phrases, 10 regex patterns, Cyrillic homoglyph normalization, zero-width stripping

### Tool Execution Security
```
Tool Request → PolicyEngine → [command denylist | path traversal | workspace boundary | sensitive paths | network policy | risk level] → Provider Router → Docker Sandbox
```

### Authentication
- Soul amendments: Bearer token (`LANCELOT_OWNER_TOKEN`)
- Memory writes: Bearer token
- General API: rate limiting only (single-owner assumption)
- War Room: local access only

### Error Safety Rules
- Never include stack traces in API responses
- Never expose internal file paths
- Return structured JSON: `{"error": "message", "status": code}`
- Health endpoints return 200 with `ready=false`, never 500

---

## Engineering SOP

### Branching Model (trunk-based, short-lived branches)
```
main = stable, releasable, protected (no direct commits)
feat/<slug>, fix/<slug>, chore/<slug>, docs/<slug>
```
One branch = one spec/blueprint slice.

### Commit Standards
```
feat: add scheduler receipts
fix: onboarding READY gating
docs: add health monitor runbook
```

### PR Checklist (Required)
- [ ] Spec created (new, not modified active ones)
- [ ] Blueprint created
- [ ] Feature gated (kill switch)
- [ ] Contracts defined
- [ ] Unit + integration tests added
- [ ] Receipts emitted
- [ ] War Room visibility (if applicable)
- [ ] Runbook added/updated
- [ ] CHANGELOG updated

### New Add-On Requirements
Any new subsystem/background process/runtime dependency = **new upgrade slice** with own spec + blueprint. Never modify active specs — create new versioned ones.

### Testing Standards
- **Unit tests:** deterministic, no network, injected timers
- **Integration tests:** `@pytest.mark.integration`, real services, env-gated
- **Run tests:** `pytest tests/ -x` from repo root (inside container: `/home/lancelot/app/`)
- **Test helpers:** `_minimal_soul_dict()`, `_write_config()`, `_write_sched_config()`
- **Cleanup:** feature flag tests call `reload_flags()` in teardown

---

## Common Docker Commands

```bash
# Rebuild and restart
docker compose up -d --build

# View logs
docker compose logs -f lancelot-core
docker compose logs -f local-llm

# Execute commands in container (MUST use MSYS_NO_PATHCONV=1 on Git Bash)
MSYS_NO_PATHCONV=1 docker exec -it lancelot_core bash
MSYS_NO_PATHCONV=1 docker exec lancelot_core python -c "import gateway; print('ok')"

# Run tests inside container
MSYS_NO_PATHCONV=1 docker exec lancelot_core pytest tests/ -x

# Quick API test
curl http://localhost:8000/health/live
curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" -d '{"text": "hello"}'

# Copy file into container
docker cp local_file.py lancelot_core:/home/lancelot/app/src/core/
```

---

## Known Gotchas

| Issue | Workaround |
|-------|-----------|
| Git Bash path mangling | `MSYS_NO_PATHCONV=1` before docker commands |
| Memory `__init__.py` relative import | try/except fallback to absolute import |
| `get_memory_service()` default path | Must construct manually with correct data_dir |
| Gemini context cache | Requires >4096 tokens of static content (not feasible) |
| Scheduler needs config | `config/scheduler.yaml` required (currently empty) |
| Gateway /chat field name | Uses `"text"` not `"message"` |
| Gemini function calling | N calls → ALL N responses in single Content(role="tool") with N Parts |
| Gemini AUTO mode | Skips tools — use `FunctionCallingConfig(mode="ANY")` to force tool call |
| TaskRunner echo skill | Bypasses real result check — bypass when agentic loop on |
| GTX 1070 VRAM limits | 15 layers + 4096 ctx works; 20 layers OOM; 28 layers OOM |
| Qwen3 thinking tags | Outputs `<think>...</think>` — `/no_think` suppresses |
| Qwen3 tool calling | llama-cpp-python doesn't convert XML → OpenAI; server post-processes |
| llama-cpp-python CUDA | Use pre-built wheel, don't compile from source (linker errors) |
| Docker CUDA version | Must match host driver (12.3), not latest |
| libgomp1 | Required by CUDA wheel for OpenMP; not in nvidia/cuda base image |
| War Room bare orchestrator | Streamlit = separate process, can't share objects — must call gateway API |

---

## Fix Pack History (Most Recent First)

| Pack | Commit | Summary |
|------|--------|---------|
| V12 | latest | Research-backed plans + code writing + retry on failure |
| V11 | 004063e | War Room routes through gateway API for subsystem access |
| V10 | 8222b12 | Fix inaction loop — force tool use for research queries |
| V9 | dcf232a+ | Intent & conversation fixes (keywords, default fallback, history injection) |
| V8 | 9c3e1d6 | Local model upgrade: Qwen3-8B, CUDA 12.3, GPU offload |
| V7-V1 | various | Agentic loop, skill execution, planning pipeline iterations |

---

## NOT YET IMPLEMENTED

### Risk-Tiered Memory Governance Upgrade
- Spec exists at: `Desktop/Lancelot v7 documents/lancelot Risk tierd memory upgrade/`
- Blueprint: `Lancelot_vNext4_Blueprint_RiskTiered_Governance.md` (97KB)
- Spec: `Lancelot_vNext4_Spec_RiskTiered_Governance.md` (41KB)
- **Status:** NOT started. `FEATURE_MEMORY_VNEXT=false` in current .env.
- This is the next major upgrade planned.

---

## Anti-Roadmap (What We Will NOT Build)

1. Consumer assistant features (voice-first UX, social companion, personality tuning)
2. Unconstrained computer control (autonomous GUI, uncontrolled browsing)
3. Generic agent SDK/framework
4. Uncontrolled third-party skill marketplaces
5. RAG as primary truth source
6. Skipping verification for speed
7. Expanding Crusader mode into irreversible actions
8. Multi-tenant enterprise features
9. Integration sprawl (dozens of shallow SaaS connectors)

**Decision test before building anything:**
1. Does this increase trust more than risk?
2. Is it reversible?
3. Is it fully receipt-traced?
4. Does it fit the GAS category?

If not: **do not build.**
