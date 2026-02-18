# Technical Specifications: Project Lancelot v7.0

**Document Version:** 7.0
**Last Updated:** 2026-02-05
**Status:** Current — reflects v4 Multi-Provider + vNext2 Soul/Skills/Heartbeat/Scheduler + vNext3 Memory + Tool Fabric + Security Hardening

---

## 1. Technology Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| **Language** | Python | 3.11+ |
| **API Framework** | FastAPI | latest |
| **ASGI Server** | Uvicorn | latest |
| **UI Framework** | Streamlit | latest |
| **Native Wrapper** | pywebview | latest |
| **Data Validation** | Pydantic | v2 |
| **Configuration** | PyYAML | >= 6.0 |
| **Primary LLM** | Google GenAI SDK (google-genai) | >= 1.0.0 |
| **Local Inference** | llama-cpp-python (GGUF) | >= 0.2.0 |
| **Database** | SQLite (scheduler), JSON (registries) | stdlib |
| **Encryption** | cryptography | latest |
| **Containerization** | Docker + Docker Compose | v3.8 |
| **Testing** | pytest + pytest-cov + pytest-timeout | >= 7.0 |

---

## 2. System Architecture

### 2.1 Service Topology

```
+---------------------------+       +--------------------+
|     lancelot-core         |       |     local-llm      |
|                           |       |                    |
|  FastAPI Gateway (:8000)  | HTTP  |  GGUF Model Server |
|  Streamlit War Room(:8501)|------>|  (:8080)           |
|  Orchestrator             |       |  /health           |
|  Model Router             |       |  /v1/completions   |
|  Soul / Skills / Health   |       +--------------------+
|  Scheduler                |
+---------------------------+
         |
         | HTTPS
         v
+---------------------------+
|   Flagship LLM Providers  |
|   - Google Gemini API     |
|   - OpenAI API            |
|   - Anthropic API         |
+---------------------------+
```

### 2.2 Module Dependency Map

```
gateway.py
    |
    +-- orchestrator.py
    |       |
    |       +-- context_env.py (context management)
    |       +-- model_router.py (lane routing)
    |       |       |
    |       |       +-- provider_profile.py (config loading)
    |       |       +-- local_model_client.py (local LLM HTTP)
    |       |       +-- flagship_client.py (provider HTTP)
    |       |       +-- usage_tracker.py (telemetry)
    |       |
    |       +-- planner.py (plan generation)
    |       +-- verifier.py (step verification)
    |       +-- security.py (input sanitization)
    |       +-- crusader.py (high-agency mode)
    |
    +-- control_plane.py (REST API endpoints)
    |
    +-- soul/ (constitutional identity)
    |       +-- store.py (schema, versioning, loading)
    |       +-- linter.py (invariant validation)
    |       +-- amendments.py (proposal workflow)
    |       +-- api.py (REST endpoints)
    |
    +-- skills/ (modular capabilities)
    |       +-- schema.py (manifest model)
    |       +-- registry.py (persistence)
    |       +-- executor.py (execution engine)
    |       +-- factory.py (proposal pipeline)
    |       +-- governance.py (marketplace hooks)
    |
    +-- health/ (monitoring)
    |       +-- types.py (HealthSnapshot)
    |       +-- api.py (REST endpoints)
    |       +-- monitor.py (background loop)
    |
    +-- scheduler/ (automated jobs)
    |       +-- schema.py (config model)
    |       +-- service.py (SQLite persistence)
    |       +-- executor.py (gating pipeline)
    |
    +-- memory/ (tiered memory)
    |       +-- store.py (core block store)
    |       +-- schemas.py (block types, memory items)
    |       +-- commits.py (commit pipeline, rollback)
    |       +-- compiler.py (context compiler service)
    |       +-- sqlite_store.py (SQLite persistence)
    |       +-- index.py (full-text search)
    |       +-- config.py (tier configuration)
    |       +-- jobs.py (maintenance jobs)
    |       +-- api.py (REST endpoints)
    |       +-- memory_panel.py (War Room panel)
    |
    +-- tools/ (Tool Fabric)
            +-- contracts.py (7 capability protocols)
            +-- fabric.py (main orchestrator)
            +-- policies.py (security policy engine)
            +-- router.py (capability-based routing)
            +-- health.py (provider health monitoring)
            +-- receipts.py (tool receipt extensions)
            +-- providers/
                    +-- local_sandbox.py (Docker sandbox)
                    +-- ui_templates.py (template scaffolder)
                    +-- ui_antigravity.py (generative UI)
                    +-- vision_antigravity.py (vision control)
    |
    +-- feature_flags.py (subsystem kill switches)
```

---

## 3. Component Specifications

### 3.1 Orchestrator

**File:** `src/core/orchestrator.py` (~739 lines)

The central nervous system. Routes messages, manages state, and coordinates all specialized modules.

**Key Method:** `chat(user_message, crusader_mode=False)`

1. Check governance (token/tool limits)
2. Sanitize input via `InputSanitizer`
3. Update context history
4. Call LLM via Model Router (lane selection)
5. Parse confidence score and actions
6. Generate receipt
7. Return response

**State Machine:**
- `ACTIVE` — Normal operating mode
- `SLEEPING` — Low-power mode (entered via `enter_sleep()`, exited via `wake_up()`)

**Public API:**

```python
class LancelotOrchestrator:
    def __init__(self, data_dir: str)
    def chat(self, user_message: str, crusader_mode: bool = False) -> str
    def plan_task(self, goal: str) -> str
    def run_autonomous_mission(self, goal: str) -> str
    def execute_plan(self, plan: str) -> str
    def execute_command(self, command: str) -> str
    def enter_sleep(self) -> None
    def wake_up(self, reason: str) -> None
```

### 3.2 Model Router

**File:** `src/core/model_router.py` (~497 lines)

Lane-based routing engine with automatic escalation.

**Routing Algorithm:**

```
1. Classify task_type
2. If task_type in LOCAL_UTILITY_TASKS  -> local_utility lane
3. If task_type == "redact"            -> local_redaction lane
4. If task_type in DEEP_TASK_TYPES     -> flagship_deep lane
5. If risk keywords detected           -> flagship_deep lane (escalation)
6. Else                                -> flagship_fast lane
7. On fast-lane failure                -> retry on flagship_deep
```

**Deep Task Types:** `plan`, `analyze`, `architect`, `review`, `debug`

**Public API:**

```python
@dataclass(frozen=True)
class RouterDecision:
    id: str             # UUID
    timestamp: str      # ISO 8601
    task_type: str
    lane: str           # local_redaction | local_utility | flagship_fast | flagship_deep
    model: str
    rationale: str
    elapsed_ms: float
    success: bool
    error: Optional[str]

@dataclass
class RouterResult:
    decision: RouterDecision
    output: Optional[str]
    data: Optional[dict]
    executed: bool

class ModelRouter:
    def route(self, task_type: str, text: str, **kwargs) -> RouterResult
    @property
    def recent_decisions(self) -> list[RouterDecision]  # deque(maxlen=200)
    @property
    def stats(self) -> dict
    @property
    def usage(self) -> UsageTracker
```

### 3.3 Provider Profile Registry

**File:** `src/core/provider_profile.py` (~405 lines)

Loads `config/models.yaml` and `config/router.yaml` to provide typed access to provider configurations and routing rules.

**Configuration Schema (models.yaml):**

```yaml
version: "1.0"
local:
  enabled: true
  url: "http://local-llm:8080"
providers:
  <provider_name>:
    display_name: "Display Name"
    fast:
      model: "model-id"
      max_tokens: 4096
      temperature: 0.3
    deep:
      model: "model-id"
      max_tokens: 8192
      temperature: 0.7
    cache:                          # Optional
      model: "model-id"
      max_tokens: 2048
      temperature: 0.1
```

**Configuration Schema (router.yaml):**

```yaml
version: "1.0"
routing_order:
  - lane: "local_redaction"
    priority: 1
  - lane: "local_utility"
    priority: 2
  - lane: "flagship_fast"
    priority: 3
  - lane: "flagship_deep"
    priority: 4
escalation:
  triggers:
    - type: "risk"
    - type: "complexity"
    - type: "failure"
receipts:
  enabled: true
  include_rationale: true
  include_timing: true
local_utility_tasks:
  - "classify_intent"
  - "extract_json"
  - "summarize"
  - "redact"
  - "rag_rewrite"
```

**Public API:**

```python
@dataclass(frozen=True)
class LaneConfig:
    model: str
    max_tokens: int
    temperature: float

@dataclass(frozen=True)
class ProviderProfile:
    name: str
    display_name: str
    fast: LaneConfig
    deep: LaneConfig
    cache: Optional[LaneConfig]

class ProfileRegistry:
    def __init__(self, models_path: str, router_path: str)
    @property
    def provider_names(self) -> list[str]
    def get_profile(self, name: str) -> ProviderProfile
    def is_local_task(self, task_type: str) -> bool
    @property
    def local(self) -> LocalConfig
    @property
    def routing_order(self) -> list[RoutingLane]
    @property
    def escalation_triggers(self) -> list[EscalationTrigger]
```

### 3.4 Local Model Client

**File:** `src/core/local_model_client.py` (~80 lines)

HTTP client for the local GGUF inference service.

**Public API:**

```python
class LocalModelClient:
    def __init__(self, base_url: str = None)     # Default: http://localhost:8080
    def health(self) -> dict
    def is_healthy(self) -> bool                  # Returns False on error, never raises
    def complete(self, prompt: str, **kwargs) -> str
    def classify_intent(self, text: str) -> str
    def verify_routing_intent(self, text: str) -> str  # V21: Returns plan/action/question
    def extract_json(self, text: str, schema: dict) -> dict
    def summarize(self, text: str) -> str
    def redact(self, text: str) -> str
    def rag_rewrite(self, query: str) -> str

class LocalModelError(Exception): ...
```

### 3.5 Flagship Client

**File:** `src/core/flagship_client.py`

HTTP client for external LLM providers (Gemini, OpenAI, Anthropic).

**Public API:**

```python
class FlagshipClient:
    def __init__(self, provider_name: str, profile: ProviderProfile)
    def complete(self, prompt: str, lane: str = "fast", **kwargs) -> str

class FlagshipError(Exception): ...
```

**Error Behavior:**
- Missing API key raises `FlagshipError("API key not configured")`
- Unsupported provider raises `FlagshipError("Unsupported provider")`
- Invalid lane raises `FlagshipError("Unknown lane")`

### 3.6 Soul Store

**File:** `src/core/soul/store.py` (~240 lines)

Loads, validates, and manages Soul versions.

**Pydantic Models:**

```python
class AutonomyPosture(BaseModel):
    level: str                              # "supervised" | "autonomous"
    description: str
    allowed_autonomous: List[str]
    requires_approval: List[str]

class RiskRule(BaseModel):
    name: str
    description: str
    enforced: bool = True

class ApprovalRules(BaseModel):
    default_timeout_seconds: int = 3600
    escalation_on_timeout: str = "skip_and_log"
    channels: List[str]                     # Default: ["war_room"]

class SchedulingBoundaries(BaseModel):
    max_concurrent_jobs: int = 5
    max_job_duration_seconds: int = 300
    no_autonomous_irreversible: bool = True
    require_ready_state: bool = True
    description: str = ""

class Soul(BaseModel):
    version: str                            # Must match r"^v\d+$"
    mission: str                            # Non-empty
    allegiance: str                         # Non-empty
    autonomy_posture: AutonomyPosture
    risk_rules: List[RiskRule]
    approval_rules: ApprovalRules
    tone_invariants: List[str]
    memory_ethics: List[str]
    scheduling_boundaries: SchedulingBoundaries
```

**Public API:**

```python
def load_active_soul(soul_dir: str = None) -> Soul
def list_versions(soul_dir: str = None) -> list[str]
def get_active_version(soul_dir: str = None) -> str
def set_active_version(version: str, soul_dir: str = None) -> None

class SoulStoreError(Exception): ...
```

**File Layout:**

```
soul/
  ACTIVE              # Contains version string, e.g. "v1"
  soul.yaml           # Symlink to active version
  soul_versions/
    soul_v1.yaml      # Version 1 constitutional document
    soul_v2.yaml      # Version 2 (if amended)
```

### 3.7 Soul Linter

**File:** `src/core/soul/linter.py` (~195 lines)

Validates constitutional invariants beyond schema validation.

**Invariant Checks:**

| Check | Severity | Rule |
|-------|----------|------|
| `destructive_actions_require_approval` | CRITICAL | `requires_approval` must include destructive keywords (delete, deploy, destroy, drop) |
| `no_silent_degradation` | CRITICAL | `tone_invariants` must mention error/failure suppression (>= 2 keywords from: suppress, silent, degrade, error, failure) |
| `scheduling_no_autonomous_irreversible` | CRITICAL | `scheduling_boundaries.no_autonomous_irreversible` must be true |
| `approval_channels_required` | CRITICAL | `approval_rules.channels` must contain at least one channel |
| `memory_ethics_required` | WARNING | `memory_ethics` should contain at least one rule |

**Public API:**

```python
class LintSeverity(Enum):
    CRITICAL = "critical"
    WARNING = "warning"

@dataclass(frozen=True)
class LintIssue:
    rule: str
    severity: LintSeverity
    message: str

def lint(soul: Soul) -> List[LintIssue]
def lint_or_raise(soul: Soul) -> List[LintIssue]  # Raises SoulStoreError on critical
```

### 3.8 Soul Amendments

**File:** `src/core/soul/amendments.py`

Manages soul modification proposals with diff computation.

**Public API:**

```python
class ProposalStatus(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    ACTIVATED = "activated"
    REJECTED = "rejected"

class SoulAmendmentProposal(BaseModel):
    id: str
    from_version: str
    proposed_yaml: str
    diff: List[str]
    status: ProposalStatus
    created_at: str

def compute_yaml_diff(old: dict, new: dict) -> List[str]
def create_proposal(from_version: str, yaml_text: str, data_dir: str = "data") -> SoulAmendmentProposal
def list_proposals(data_dir: str = "data") -> List[SoulAmendmentProposal]
def get_proposal(proposal_id: str, data_dir: str = "data") -> Optional[SoulAmendmentProposal]
```

**Persistence:** `data/soul_proposals.json`

### 3.9 Soul API

**File:** `src/core/soul/api.py`

FastAPI router for soul management.

**Endpoints:**

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/soul/status` | None | Active version, available versions, pending proposals |
| POST | `/soul/proposals/{id}/approve` | Bearer | Owner approves amendment proposal |
| POST | `/soul/proposals/{id}/activate` | Bearer | Owner activates approved proposal (triggers lint) |

**Authentication:** Bearer token validated against `LANCELOT_OWNER_TOKEN` environment variable.

### 3.10 Skill Schema

**File:** `src/core/skills/schema.py` (~172 lines)

**Pydantic Models:**

```python
class SkillRisk(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

class SkillInput(BaseModel):
    name: str
    type: str = "string"
    required: bool = True
    description: str = ""

class SkillOutput(BaseModel):
    name: str
    type: str = "string"
    description: str = ""

class SkillManifest(BaseModel):
    name: str                          # Validated: snake_case, lowercase
    version: str                       # Validated: non-empty
    description: str = ""
    inputs: List[SkillInput]
    outputs: List[SkillOutput]
    risk: SkillRisk = SkillRisk.LOW
    permissions: List[str]             # Required, no default
    required_brain: str = ""
    scheduler_eligible: bool = False
    sentry_requirements: List[SentryRequirement]
    receipts: ReceiptConfig
```

**Validation Rules:**
- Name: lowercase, alphanumeric + underscores only (snake_case)
- Version: must not be empty or whitespace
- Permissions: required field (no default), must be explicitly provided

### 3.11 Skill Registry

**File:** `src/core/skills/registry.py` (~180 lines)

**Public API:**

```python
class SkillOwnership(Enum):
    SYSTEM = "system"
    USER = "user"
    MARKETPLACE = "marketplace"

class SignatureState(Enum):
    UNSIGNED = "unsigned"
    SIGNED = "signed"
    VERIFIED = "verified"

class SkillEntry(BaseModel):
    name: str
    version: str
    enabled: bool = True
    manifest_path: str
    ownership: SkillOwnership
    signature_state: SignatureState = SignatureState.UNSIGNED
    installed_at: str

class SkillRegistry:
    def __init__(self, data_dir: str = "data")
    def install_skill(self, path: str, ownership: SkillOwnership = SkillOwnership.USER) -> SkillEntry
    def enable_skill(self, name: str) -> None
    def disable_skill(self, name: str) -> None
    def list_skills(self) -> List[SkillEntry]
    def get_skill(self, name: str) -> Optional[SkillEntry]
    def uninstall_skill(self, name: str) -> None
```

**Persistence:** `data/skills_registry.json`

### 3.12 Skill Executor

**File:** `src/core/skills/executor.py`

Loads skill modules, executes them with context, and emits receipts.

**Public API:**

```python
@dataclass
class SkillContext:
    skill_name: str
    inputs: Dict[str, Any]
    permissions: List[str]

@dataclass
class SkillResult:
    success: bool
    outputs: Dict[str, Any]
    error: Optional[str] = None
    receipt: Optional[Dict[str, Any]] = None

class SkillExecutor:
    def __init__(self, registry: SkillRegistry)
    def execute(self, skill_name: str, inputs: Dict[str, Any]) -> SkillResult
```

**Built-in Skills:** `echo` (returns inputs as outputs, for testing)

### 3.13 Skill Factory

**File:** `src/core/skills/factory.py`

Proposal pipeline for creating new skills.

**Public API:**

```python
class ProposalStatus(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    INSTALLED = "installed"

class SkillProposal(BaseModel):
    id: str
    name: str
    description: str
    permissions: List[str]
    status: ProposalStatus
    skeleton_path: Optional[str]
    created_at: str

class SkillFactory:
    def __init__(self, data_dir: str = "data")
    def generate_skeleton(self, name: str, description: str, permissions: List[str]) -> SkillProposal
    def list_proposals(self) -> List[SkillProposal]
    def get_proposal(self, proposal_id: str) -> Optional[SkillProposal]
    def approve_proposal(self, proposal_id: str) -> SkillProposal
    def reject_proposal(self, proposal_id: str) -> SkillProposal
    def install_proposal(self, proposal_id: str, registry: SkillRegistry) -> SkillEntry
```

**Persistence:** `data/skill_proposals.json`

### 3.14 Skill Governance

**File:** `src/core/skills/governance.py`

Marketplace permission policies and packaging.

**Marketplace Allowed Permissions:**
```python
frozenset({"read_input", "write_output", "read_config"})
```

**Public API:**

```python
def build_skill_package(skill_name: str, registry: SkillRegistry, output_dir: str) -> Path
def verify_marketplace_permissions(entry: SkillEntry) -> List[str]  # Returns violations
def is_marketplace_approved(entry: SkillEntry) -> bool
```

### 3.15 Health Types

**File:** `src/core/health/types.py`

```python
class HealthSnapshot(BaseModel):
    ready: bool = False
    onboarding_state: str = "UNKNOWN"
    local_llm_ready: bool = False
    scheduler_running: bool = False
    last_health_tick_at: Optional[str] = None
    last_scheduler_tick_at: Optional[str] = None
    degraded_reasons: List[str] = Field(default_factory=list)
    timestamp: str  # ISO 8601, auto-generated
```

### 3.16 Health API

**File:** `src/core/health/api.py`

| Endpoint | Response | Error Behavior |
|----------|----------|----------------|
| `GET /health/live` | `{"status": "alive"}` (200) | Always 200 |
| `GET /health/ready` | HealthSnapshot JSON (200) | Returns safe fallback, never 500 |

**Snapshot Provider Pattern:** A function registered via `set_snapshot_provider(fn)` computes the current HealthSnapshot. If the provider errors, a safe fallback snapshot with `ready=false` is returned.

### 3.17 Health Monitor

**File:** `src/core/health/monitor.py`

**Public API:**

```python
@dataclass
class HealthCheck:
    name: str
    check_fn: Callable[[], bool]
    description: str = ""

class HealthMonitor:
    def __init__(self, checks: List[HealthCheck], interval_s: float = 30.0)
    def start_monitor(self) -> None       # Starts background thread
    def stop_monitor(self) -> None        # Stops background thread
    def compute_snapshot(self) -> HealthSnapshot
    @property
    def latest_snapshot(self) -> HealthSnapshot
    @property
    def receipts(self) -> list[dict]
```

**State Transition Receipts:**
- `health_ok` — All checks passing
- `health_degraded` — One or more checks failing (includes `degraded_reasons`)
- `health_recovered` — Transition from degraded back to healthy

### 3.18 Scheduler Schema

**File:** `src/core/scheduler/schema.py` (~160 lines)

**Configuration Schema (scheduler.yaml):**

```yaml
jobs:
  - id: "job_id"                      # Validated: lowercase + underscores
    name: "Human Readable Name"
    trigger:
      type: "interval"                # "interval" | "cron"
      seconds: 60                     # For interval triggers
      expression: "0 3 * * *"         # For cron triggers (5-field)
    enabled: true
    requires_ready: true
    requires_approvals: []            # List of approval names
    timeout_s: 30                     # Positive integer
    skill: "skill_name"
    description: "Job description"
```

**Auto-Copy:** If `config/scheduler.yaml` doesn't exist, `config/scheduler.example.yaml` is copied automatically on first load.

### 3.19 Scheduler Service

**File:** `src/core/scheduler/service.py`

SQLite-backed job persistence and lifecycle management.

**Database Schema:**

```sql
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    skill TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    trigger_type TEXT DEFAULT 'interval',
    trigger_value TEXT DEFAULT '',
    requires_ready INTEGER DEFAULT 1,
    requires_approvals TEXT DEFAULT '[]',    -- JSON array
    timeout_s INTEGER DEFAULT 300,
    description TEXT DEFAULT '',
    last_run_at TEXT,
    last_run_status TEXT,
    run_count INTEGER DEFAULT 0,
    registered_at TEXT NOT NULL
);
```

**Public API:**

```python
class JobRecord(BaseModel):
    id: str
    name: str
    skill: str
    enabled: bool
    trigger_type: str
    trigger_value: str
    requires_ready: bool
    requires_approvals: List[str]
    timeout_s: int
    description: str
    last_run_at: Optional[str]
    last_run_status: Optional[str]
    run_count: int
    registered_at: str

class SchedulerService:
    def __init__(self, data_dir: str = "data", config_dir: str = "config")
    def register_from_config(self) -> int   # Returns newly registered count
    def list_jobs(self) -> List[JobRecord]
    def get_job(self, job_id: str) -> Optional[JobRecord]
    def enable_job(self, job_id: str) -> None
    def disable_job(self, job_id: str) -> None
    def run_now(self, job_id: str) -> JobRecord
    @property
    def last_scheduler_tick_at(self) -> Optional[str]
```

**Database Location:** `data/scheduler.sqlite`

### 3.20 Scheduler Executor

**File:** `src/core/scheduler/executor.py`

Job execution pipeline with configurable gating.

**Gating Pipeline (sequential):**

```
1. Job exists? (skip if not found)
2. Job enabled? (skip if disabled)
3. All Gates pass? (skip on first failure)
4. Approvals granted? (skip if required but not granted)
5. Execute via skill function
```

**Public API:**

```python
@dataclass
class Gate:
    name: str
    check_fn: Callable[[], bool]
    skip_reason: str = ""

@dataclass
class JobExecutionResult:
    job_id: str
    executed: bool = False
    skipped: bool = False
    skip_reason: Optional[str] = None
    success: bool = False
    error: Optional[str] = None
    duration_ms: float = 0.0
    receipt: Optional[Dict[str, Any]] = None

class JobExecutor:
    def __init__(self, scheduler_service, skill_execute_fn=None, gates=None)
    def execute_job(self, job_id: str) -> JobExecutionResult
    @property
    def receipts(self) -> List[Dict[str, Any]]
```

**Gate Exception Handling:** If a gate's `check_fn` raises an exception, the job is skipped with a descriptive receipt. Exceptions do not propagate.

### 3.21 Feature Flags

**File:** `src/core/feature_flags.py` (~50 lines)

**Flags:**

| Flag | Environment Variable | Default |
|------|---------------------|---------|
| FEATURE_SOUL | `FEATURE_SOUL` | true |
| FEATURE_SKILLS | `FEATURE_SKILLS` | true |
| FEATURE_HEALTH_MONITOR | `FEATURE_HEALTH_MONITOR` | true |
| FEATURE_SCHEDULER | `FEATURE_SCHEDULER` | true |
| FEATURE_MEMORY_VNEXT | `FEATURE_MEMORY_VNEXT` | true |
| FEATURE_TOOLS_FABRIC | `FEATURE_TOOLS_FABRIC` | true |
| FEATURE_TOOLS_CLI_PROVIDERS | `FEATURE_TOOLS_CLI_PROVIDERS` | false |
| FEATURE_TOOLS_ANTIGRAVITY | `FEATURE_TOOLS_ANTIGRAVITY` | false |
| FEATURE_TOOLS_NETWORK | `FEATURE_TOOLS_NETWORK` | false |
| FEATURE_TOOLS_HOST_EXECUTION | `FEATURE_TOOLS_HOST_EXECUTION` | false |

**Accepted Values:** `true`, `1`, `yes` (case-insensitive). Everything else is treated as false.

**Public API:**

```python
FEATURE_SOUL: bool
FEATURE_SKILLS: bool
FEATURE_HEALTH_MONITOR: bool
FEATURE_SCHEDULER: bool

def reload_flags() -> None          # Re-read from environment
def log_feature_flags() -> None     # Log current state at INFO level
```

### 3.22 Receipt System

**File:** `src/shared/receipts.py`

**Schema:**

```python
class ActionType(Enum):
    TOOL_CALL = "tool_call"
    LLM_CALL = "llm_call"
    FILE_OP = "file_op"
    ENV_QUERY = "env_query"
    PLAN_STEP = "plan_step"
    VERIFICATION = "verification"
    USER_INTERACTION = "user_interaction"
    SYSTEM = "system"

class ReceiptStatus(Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"

class CognitionTier(Enum):
    DETERMINISTIC = 0
    CLASSIFICATION = 1
    PLANNING = 2
    SYNTHESIS = 3

@dataclass
class Receipt:
    id: str                     # UUID
    timestamp: str              # ISO 8601
    action_type: ActionType
    action_name: str
    inputs: dict
    outputs: dict
    status: ReceiptStatus
    duration_ms: float
    token_count: int
    tier: CognitionTier
    parent_id: Optional[str]
    quest_id: Optional[str]
    error_message: Optional[str]
    metadata: dict

    def complete(self, outputs, duration_ms, token_count) -> Receipt
    def fail(self, error_message, duration_ms) -> Receipt
    def to_dict(self) -> dict
```

### 3.23 Security

**File:** `src/core/security.py` (~200 lines)

**Components:**

| Component | Purpose |
|-----------|---------|
| `InputSanitizer` | Prompt injection detection with 16 banned phrases, 10 regex patterns, homoglyph normalization |
| `AuditLogger` | Security event logging with timestamps and context |
| `NetworkInterceptor` | Outbound request monitoring and control |

**Normalization Pipeline:**
1. Strip zero-width characters
2. Collapse multiple spaces
3. Replace Cyrillic homoglyphs with Latin equivalents
4. Decode URL-encoded sequences

### 3.24 Control Plane

**File:** `src/core/control_plane.py` (~288 lines)

FastAPI router providing system management endpoints for the War Room.

**Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/system/status` | Full provisioning status |
| GET | `/onboarding/status` | Onboarding state details |
| POST | `/onboarding/command` | Execute recovery commands |
| POST | `/onboarding/back` | Go back one step |
| POST | `/onboarding/restart-step` | Restart current step |
| POST | `/onboarding/resend-code` | Resend verification code |
| POST | `/onboarding/reset` | Reset onboarding |
| GET | `/router/decisions` | Recent routing decisions (max 50) |
| GET | `/router/stats` | Routing statistics |
| GET | `/usage/summary` | Token and cost telemetry |
| GET | `/usage/lanes` | Per-lane usage breakdown |
| GET | `/usage/savings` | Local model savings estimate |
| POST | `/usage/reset` | Reset usage counters |

### 3.25 Memory Block Store

**File:** `src/core/memory/store.py`

Core memory block storage with in-memory caching.

**Public API:**

```python
class CoreBlockStore:
    def __init__(self)
    def get_block(self, block_type: str) -> Optional[CoreBlock]
    def set_block(self, block_type: str, content: str, metadata: dict = None) -> None
    def list_blocks(self) -> List[CoreBlock]
    def delete_block(self, block_type: str) -> None
```

**Block Types:** persona, human, mission, operating_rules, workspace_state

### 3.26 Memory Commit Pipeline

**File:** `src/core/memory/commits.py`

Transactional memory editing with snapshot isolation and rollback support.

**Public API:**

```python
class MemoryEditOp(Enum):
    INSERT = "insert"
    UPDATE = "update"
    DELETE = "delete"
    RETHINK = "rethink"

class CommitManager:
    def __init__(self, core_store: CoreBlockStore, memory_manager: MemoryStoreManager)
    def begin_edits(self, commit_id: str) -> None    # Snapshot current state
    def apply_core_edit(self, commit_id, op, block_type, data) -> None
    def apply_item_edit(self, commit_id, op, tier, item_id, data) -> None
    def finish_edits(self, commit_id: str) -> None    # Apply atomically
    def rollback(self, commit_id: str) -> None         # Restore snapshot
```

**Snapshot Management:**
- MAX_RETAINED_SNAPSHOTS = 50 with LRU eviction on `begin_edits`
- Snapshots preserved after commit for rollback support
- Item-level undo log for insert/update/delete rollback

### 3.27 Context Compiler

**File:** `src/core/memory/compiler.py`

Assembles memory into a token-budgeted context window for LLM prompts.

**Public API:**

```python
class ContextCompilerService:
    def __init__(self, core_store=None, memory_manager=None)
    def compile_context(self, max_tokens: int = 8000) -> CompiledContext
    def get_tier_summary(self) -> Dict[str, int]
```

**Compilation Order:**
1. Core blocks (persona first, then human, mission, operating_rules, workspace_state)
2. Working memory items (most recent first)
3. Episodic memory items (most relevant first)
4. Archival memory items (if budget remains)

### 3.28 Memory SQLite Store

**File:** `src/core/memory/sqlite_store.py`

Thread-safe SQLite persistence for tiered memory items.

**Public API:**

```python
class MemoryStoreManager:
    def __init__(self, db_path: str = "data/memory.sqlite")
    def insert_item(self, tier: str, item: MemoryItem) -> None
    def get_item(self, tier: str, item_id: str) -> Optional[MemoryItem]
    def update_item(self, tier: str, item_id: str, data: dict) -> None
    def delete_item(self, tier: str, item_id: str) -> None
    def list_items(self, tier: str) -> List[MemoryItem]
    def search(self, query: str, tier: str = None) -> List[SearchResult]
    def close_all(self) -> None    # Cleanup thread-local connections
```

**Thread Safety:** Thread-local SQLite connections with `atexit` cleanup registration.

### 3.29 Memory API

**File:** `src/core/memory/api.py`

FastAPI router for memory operations with thread-safe singleton initialization.

**Endpoints:**

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/memory/status` | None | Memory statistics, tier counts, compiler info |
| POST | `/memory/edit` | Bearer | Submit governed memory edit |
| POST | `/memory/compile` | None | Compile context from memory tiers |
| GET | `/memory/search` | None | Search across memory tiers |
| POST | `/memory/quarantine/{id}/approve` | Bearer | Approve quarantined edit |
| POST | `/memory/rollback/{commit_id}` | Bearer | Rollback a committed edit |

**Singleton Pattern:** `get_memory_service()` with `threading.Lock()` double-checked locking.

### 3.30 Tool Fabric Contracts

**File:** `src/tools/contracts.py`

Protocol-based capability interfaces and result types.

**Capability Protocols:**

```python
class Capability(Enum):
    SHELL_EXEC = "shell_exec"
    REPO_OPS = "repo_ops"
    FILE_OPS = "file_ops"
    WEB_OPS = "web_ops"
    UI_BUILDER = "ui_builder"
    DEPLOY_OPS = "deploy_ops"
    VISION_CONTROL = "vision_control"

class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

@dataclass
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int
    success: bool
    duration_ms: int

@dataclass
class FileChange:
    path: str
    operation: str      # "read" | "write" | "delete" | "list"
    hash_before: Optional[str]
    hash_after: Optional[str]
    success: bool
```

### 3.31 Tool Fabric Orchestrator

**File:** `src/tools/fabric.py`

Main orchestration class coordinating policy, routing, and execution.

**Public API:**

```python
class ToolFabric:
    def __init__(self)
    def register_provider(self, provider: BaseProvider) -> None
    def execute_command(self, command: str, workspace: str = None) -> ExecResult
    def git_status(self, workspace: str) -> str
    def git_diff(self, workspace: str) -> str
    def read_file(self, path: str, workspace: str) -> str
    def write_file(self, path: str, content: str, workspace: str) -> FileChange
    def list_files(self, workspace: str) -> List[str]
    def health_status(self) -> Dict[str, Any]
    def set_safe_mode(self, enabled: bool) -> None
```

**Execution Pipeline:** PolicyEngine.evaluate() -> ProviderRouter.select() -> Provider.execute() -> ToolReceipt

### 3.32 Policy Engine

**File:** `src/tools/policies.py`

Centralized security enforcement for tool operations.

**Security Gates:**

| Gate | Function |
|------|----------|
| Command denylist | shlex-based token matching against dangerous commands |
| Path traversal | Encoded/double-encoded `../` detection |
| Workspace boundary | `os.path.realpath()` + `os.sep` suffix matching |
| Sensitive paths | Pattern matching for .env, .ssh, .aws, credentials |
| Network policy | Disabled by default, capability-based exceptions |
| Risk assessment | LOW (read), MEDIUM (modify), HIGH (network/delete/deploy) |

**Public API:**

```python
class PolicyEngine:
    def __init__(self, workspace: str = None)
    def evaluate_command(self, command: str) -> PolicySnapshot
    def evaluate_path(self, path: str) -> PolicySnapshot
    def evaluate_intent(self, intent: ToolIntent) -> PolicySnapshot
    def redact_sensitive(self, text: str) -> str
```

### 3.33 Provider Router

**File:** `src/tools/router.py`

Capability-based provider selection with health-aware failover.

**Public API:**

```python
class ProviderRouter:
    def __init__(self)
    def select(self, capability: Capability, intent: ToolIntent = None) -> RouteDecision
    def register_provider(self, provider: BaseProvider) -> None
```

**Selection Algorithm:**
1. Filter providers by capability support
2. Filter by health state (HEALTHY or DEGRADED)
3. Sort by priority (lower = higher priority)
4. Apply policy engine constraints
5. Return highest-priority healthy provider
6. On failure, failover to next healthy provider

### 3.34 Tool Receipts

**File:** `src/tools/receipts.py`

Extended receipt types for tool operations.

**Receipt Types:**

```python
@dataclass
class ToolReceipt:
    receipt_id: str           # UUID
    timestamp: str            # ISO 8601
    capability: str
    action: str
    provider_id: str
    inputs_summary: dict      # Redacted
    stdout: str               # Bounded
    stderr: str               # Bounded
    exit_code: Optional[int]
    changed_files: List[dict] # With hashes
    policy_snapshot: dict
    risk_level: str
    duration_ms: Optional[int]
    success: bool

@dataclass
class VisionReceipt:
    receipt_id: str
    action: str               # capture_screen, locate_element, perform_action, verify_state
    screenshot_before_hash: Optional[str]
    screenshot_after_hash: Optional[str]
    elements_detected: List[dict]
    confidence_score: float
    success: bool
```

---

## 4. Security Architecture

### 4.1 Input Layer

```
User Input
    |
    v
Rate Limiter (60/min per IP)
    |
    v
Request Size Check (1 MB max)
    |
    v
InputSanitizer
    +-- Banned phrase scan
    +-- Cyrillic homoglyph normalization
    +-- Regex pattern detection
    +-- Zero-width character stripping
    |
    v
[PASSED] --> Orchestrator
[FAILED] --> 400 Bad Request (sanitized message)
```

### 4.5 Tool Execution Security

```
Tool Request
    |
    v
PolicyEngine
    +-- Command denylist (shlex tokenization)
    +-- Path traversal detection (encoded variants)
    +-- Workspace boundary (realpath + sep check)
    +-- Sensitive path blocking (.env, .ssh, .aws)
    +-- Network policy evaluation
    +-- Risk level assessment
    |
    v
[ALLOWED] --> Provider Router --> Docker Sandbox
[DENIED] --> PolicyViolation (structured error)
```

### 4.6 Memory Security

- Governed self-edits require provenance tracking on every operation
- Quarantine flow holds suspicious edits for owner review
- Memory API endpoints require Bearer token for write operations
- Error messages sanitized (no internal paths or stack traces in API responses)
- Thread-safe singleton initialization with double-checked locking

### 4.2 Error Safety

All API endpoints follow these rules:
- Never include stack traces in responses
- Never expose internal file paths
- Return structured JSON error objects: `{"error": "message", "status": code}`
- Health endpoints return 200 with `ready=false` rather than 500

### 4.3 Authentication

| Resource | Method | Requirement |
|----------|--------|-------------|
| Soul amendments | Bearer token | `LANCELOT_OWNER_TOKEN` environment variable |
| API endpoints | None | Rate limiting only (single-owner assumption) |
| War Room | None | Local access only (Docker network) |

### 4.4 Secret Management

- API keys stored in `.env` file (never committed)
- Sensitive data encrypted at rest via `vault.py` (cryptography library)
- No secrets exposed through API responses
- PII redacted via local model before external API calls

---

## 5. Deployment Architecture

### 5.1 Docker Compose

**Services:**

| Service | Container | Ports | Dependencies |
|---------|-----------|-------|-------------|
| `lancelot-core` | `lancelot_core` | 8000 (FastAPI), 8501 (Streamlit) | local-llm (healthy) |
| `local-llm` | `lancelot_local_llm` | 8080 | None |

**Networking:** Both services on `lancelot_net` bridge network.

### 5.2 Persistent Volumes

| Volume Mount | Purpose |
|-------------|---------|
| `./lancelot_data:/home/lancelot/data` | Receipts, registries, databases |
| `.:/home/lancelot/app` | Application code (dev mode) |
| `./local_models/weights:/home/llm/models:ro` | GGUF model weights |
| ADC credentials path | Google OAuth (read-only) |

### 5.3 Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `GEMINI_API_KEY` | Google Gemini API key | Required |
| `OPENAI_API_KEY` | OpenAI API key | Optional |
| `ANTHROPIC_API_KEY` | Anthropic API key | Optional |
| `LANCELOT_OWNER_TOKEN` | Bearer token for soul/admin ops | Required for soul API |
| `LOCAL_LLM_URL` | Local model service URL | `http://local-llm:8080` |
| `LOCAL_MODEL_CTX` | Local model context window | 4096 |
| `LOCAL_MODEL_THREADS` | Local model CPU threads | 4 |
| `LOCAL_MODEL_GPU_LAYERS` | GPU layer offload count | 0 |
| `LANCELOT_LOG_LEVEL` | Log verbosity | INFO |
| `FEATURE_SOUL` | Soul subsystem toggle | true |
| `FEATURE_SKILLS` | Skills subsystem toggle | true |
| `FEATURE_HEALTH_MONITOR` | Health monitor toggle | true |
| `FEATURE_SCHEDULER` | Scheduler toggle | true |

### 5.4 Health Checks

| Service | Endpoint | Interval | Start Period | Retries |
|---------|----------|----------|-------------|---------|
| local-llm | `curl -f http://localhost:8080/health` | 30s | 60s | 3 |

---

## 6. Data Persistence

| Store | Format | Location | Purpose |
|-------|--------|----------|---------|
| Soul versions | YAML | `soul/soul_versions/soul_vN.yaml` | Constitutional identity |
| Soul proposals | JSON | `data/soul_proposals.json` | Amendment workflow state |
| Skill registry | JSON | `data/skills_registry.json` | Installed skill state |
| Skill proposals | JSON | `data/skill_proposals.json` | Factory pipeline state |
| Scheduler jobs | SQLite | `data/scheduler.sqlite` | Job state and run history |
| Receipts | JSON | `lancelot_data/receipts/` | Audit trail |
| Config (models) | YAML | `config/models.yaml` | Provider profiles |
| Config (router) | YAML | `config/router.yaml` | Routing rules |
| Config (scheduler) | YAML | `config/scheduler.yaml` | Job definitions |
| User profile | Markdown | `lancelot_data/USER.md` | Owner identity |
| Context log | JSON | `lancelot_data/chat_log.json` | Chat history |
| Memory blocks | In-memory | `CoreBlockStore` | Core memory blocks |
| Memory items | SQLite | `data/memory.sqlite` | Tiered memory items |
| Memory search | SQLite FTS | `data/memory.sqlite` | Full-text search index |

---

## 7. Test Architecture

### 7.1 Test Configuration

**File:** `pytest.ini`

```ini
[pytest]
testpaths = tests
asyncio_mode = strict
timeout = 30
```

**Markers:**
- `@pytest.mark.integration` — Requires `GEMINI_API_KEY`
- `@pytest.mark.docker` — Requires Docker runtime
- `@pytest.mark.local_model` — Requires llama-cpp-python + model weights
- `@pytest.mark.slow` — Long-running tests

### 7.2 Test Coverage by Subsystem

| Subsystem | Test Files | Test Count |
|-----------|-----------|------------|
| Soul Store | `test_soul_store.py` | 29 |
| Soul Linter | `test_soul_linter.py` | 22 |
| Soul Versioning | `test_soul_versioning.py` | 11 |
| Soul Amendments | `test_soul_amendments.py` | 22 |
| Soul API | `test_soul_api.py` | 17 |
| Skill Schema | `test_skill_schema.py` | 23 |
| Skill Registry | `test_skill_registry.py` | 17 |
| Skill Executor | `test_skill_executor.py` | 11 |
| Skill Factory | `test_skill_factory.py` | 18 |
| Skill Governance | `test_skill_governance.py` | 14 |
| Health Endpoints | `test_heartbeat.py` | 12 |
| Health Monitor | `test_health_monitor.py` | 15 |
| Scheduler Schema | `test_scheduler_schema.py` | 18 |
| Scheduler Service | `test_scheduler_service.py` | 18 |
| Scheduler Executor | `test_scheduler_executor.py` | 12 |
| War Room Panels | `test_war_room_panels.py` | 16 |
| Hardening/Regression | `test_vnext2_hardening.py` | 42 |
| Model Router | `test_model_router.py` | ~30 |
| Security | `test_security_s*.py` (11 files) | ~60 |
| Control Plane | `test_control_plane.py` | ~15 |
| Hardening (v4) | `test_hardening.py` | ~40 |
| Memory API | `test_memory_api.py` | ~40 |
| Memory Commits | `test_context_compiler.py` | ~30 |
| Memory Panel | `test_memory_panel.py` | ~20 |
| Tool Contracts | `test_tool_contracts.py` | 59 |
| Tool Policies | `test_tool_policies.py` | 63 |
| Tool Router | `test_tool_router.py` | 43 |
| Tool Fabric | `test_tool_fabric_integration.py` | 36 |
| Repo/File Ops | `test_repo_file_ops.py` | 49 |
| UI Templates | `test_ui_templates.py` | 45 |
| UI Antigravity | `test_ui_antigravity.py` | 38 |
| Vision Control | `test_vision_control.py` | 35 |
| Tools Panel | `test_tools_panel.py` | 50 |
| Tool Hardening | `test_tool_fabric_hardening.py` | 105 |
| Security Hardening | `test_vnext3_hardening.py` | ~50 |
| **Total** | **~40 files** | **1900+** |

### 7.3 Test Patterns

- **Temp directory isolation:** All file-based tests use `tmp_path` fixtures
- **Config helpers:** `_write_config()` / `_write_sched_config()` create temporary YAML files
- **Soul helpers:** `_minimal_soul_dict()` / `_valid_soul_dict()` provide linter-passing defaults
- **Service fixtures:** Database-backed services use temporary SQLite paths
- **HTTP testing:** `TestClient` from FastAPI for endpoint testing
- **Cleanup:** Feature flag tests call `reload_flags()` in teardown

---

## 8. Operational Runbooks

Located in `docs/operations/runbooks/`:

| Runbook | Subsystem | Key Topics |
|---------|-----------|------------|
| `soul.md` | Soul | Version checking, amendment proposals, lint failures |
| `health.md` | Heartbeat | Health endpoints, degraded reasons, LLM troubleshooting |
| `scheduler.md` | Scheduler | Job listing, manual triggers, SQLite locking |
| `skills.md` | Skills | Installation, factory proposals, marketplace permissions |
| `memory.md` | Memory | Tier browsing, commit rollback, quarantine management |
| `tools.md` | Tool Fabric | Provider registration, policy configuration, safe mode |
