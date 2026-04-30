# Configuration Reference

Complete reference for every configuration file and environment variable in Lancelot.

---

## Configuration Precedence

When the same setting exists in multiple places:

```
Environment variables (.env)  →  override  →  YAML config files
Soul risk overrides           →  override  →  governance.yaml defaults
```

Environment variables always win. The Soul can escalate risk tiers above governance.yaml defaults but never reduce them.

---

## Network Allowlist (`config/network_allowlist.yaml`)

Lancelot uses a single canonical network allowlist subsystem for outbound-domain enforcement. The same config file is consumed by core security, Tool Fabric policy, the War Room allowlist editor, and the direct outbound HTTP clients that talk to external providers and services (OIDC/OAuth, flagship model APIs, update checks, A2A, MCP, federation, observability webhooks, and governed connector traffic).

Built-in infrastructure domains are always allowed and do not need to be listed in the file:
- `localhost`
- `127.0.0.1`
- `api.projectlancelot.dev`
- `ghcr.io`

Configured domains are normalized to lowercase and matched by exact host or parent-domain suffix. For example, allowlisting `github.com` also permits `api.github.com`.

Local-only control-plane URLs such as `LOCAL_LLM_URL` and `HOST_AGENT_URL` are not treated as general internet egress. They remain separate operator-managed local paths, and the runtime fails closed if they are pointed at public internet hostnames.

---

## Environment Variables (`.env`)

The `.env` file is an optional deployment override file. It is never committed to git.
Fresh installs can start without it when configuration already lives in Docker secrets,
the encrypted vault, or persisted onboarding state. Create `.env` when you need to
bootstrap a new instance or override provider, auth, local-model, mount, or feature
settings:

```bash
cp .env.example .env
```

Secrets that are entered through onboarding or migrated at startup should be treated as
vault state after boot, not as long-lived `.env` values. A missing `.env` must not block
container recovery when the vault key and persisted data volumes are still present.

### LLM API Keys

| Variable | Required | Description |
|----------|----------|-------------|
| `GEMINI_API_KEY` | One of five | Google Gemini API key (starts with `AIza...`) |
| `OPENAI_API_KEY` | One of five | OpenAI API key (starts with `sk-...`) |
| `ANTHROPIC_API_KEY` | One of five | Anthropic API key (starts with `sk-ant-...`) |
| `XAI_API_KEY` | One of five | xAI (Grok) API key (starts with `xai-...`) |
| `NVIDIA_API_KEY` | One of five | NVIDIA NIM API key (starts with `nvapi-...`) |

At least one API key is required. You can configure one or more providers. Keys can be rotated from the War Room UI without restarting.

### Provider Auth Mode

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LANCELOT_AUTH_MODE` | No | _(unset)_ | Optional provider-auth bootstrap mode. Set to `OAUTH` only when you explicitly want Gemini ADC / OAuth bootstrap or another OAuth-backed provider flow. When unset, the runtime does **not** probe Gemini ADC automatically if `GEMINI_API_KEY` is missing. |

### Soul Runtime

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SOUL_DIR` | No | repo `soul/` directory | Override path for the active Soul store (`ACTIVE`, `soul_versions/`, proposals). The Soul API, Crusader mode, and runtime loaders all honor this path when it is set. |

### Authentication

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LANCELOT_OWNER_TOKEN` | Yes | — | Token for administrative operations (Soul amendments, memory writes, approvals) |
| `LANCELOT_VAULT_KEY` | Yes | — | Encryption key for credential vault (Fernet). Vault startup now fails closed when this is missing unless you explicitly set `LANCELOT_ALLOW_EPHEMERAL_VAULT=true` for development. |
| `LANCELOT_ALLOW_EPHEMERAL_VAULT` | No | `false` | Development-only override that allows an ephemeral in-memory vault key when `LANCELOT_VAULT_KEY` is missing. Credentials will not survive restart. Do not use in production. |

### Local Model

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LOCAL_LLM_URL` | No | `http://local-llm:8080` | URL of the local GGUF model server. Must stay on loopback, `host.docker.internal`, or a single-label local service hostname such as `local-llm`. |
| `LOCAL_MODEL_PROFILE` | No | `qwen3-8b` | Model profile selected from `local_models/models.lock.yaml`. Bonsai profiles are opt-in and require the Prism runtime. |
| `LOCAL_LLM_ENGINE` | No | `llama_cpp_python` | Local runtime engine. Use `prism_llama_server` only with `Dockerfile.prism`, `Dockerfile.prism.cuda`, or the optional Bonsai compose services. |
| `LOCAL_LLM_MODEL` | No | `local-llm` | Operator-visible fallback model label when the endpoint does not report its own model name. |
| `LOCAL_LLM_DOCKERFILE` | No | `Dockerfile` | Dockerfile used by the default `local-llm` compose service. Set `Dockerfile.prism.cuda` only when replacing the default service with a GPU-backed Prism Bonsai profile. |
| `BONSAI_LLM_DOCKERFILE` | No | `Dockerfile.prism` | Dockerfile used by the optional Bonsai compose services. `Dockerfile.prism` is CPU-only and suitable for portability smoke tests; `Dockerfile.prism.cuda` is the production GPU-backed path. |
| `PRISM_CUDA_BASE_IMAGE` | No | `nvidia/cuda:12.3.2-devel-ubuntu22.04` | CUDA devel image used by `Dockerfile.prism.cuda`. Match this to the customer host's NVIDIA driver support. |
| `PRISM_LLAMA_CPP_REF` | No | `d104cf1b639a909ddea521d61f7cb023c6e41f57` | Pinned PrismML llama.cpp commit used by `Dockerfile.prism.cuda`. |
| `PRISM_CMAKE_ARGS` | No | `-DGGML_CUDA=ON` | Build flags for the Prism llama.cpp backend. Set explicitly for CPU, Metal, Vulkan, or CUDA deployments. |
| `PRISM_CUDA_ARCHITECTURES` | No | _(unset)_ | Optional CUDA architecture list for `Dockerfile.prism.cuda`, such as `61` for GTX 10-series cards. Setting this can materially reduce source-build time. |
| `PRISM_BUILD_JOBS` | No | `2` | Parallel build jobs for the CUDA Prism source build. Keep conservative on small customer hosts. |
| `LOCAL_MODEL_CTX` | No | `4096` | Context window size (tokens) |
| `LOCAL_MODEL_THREADS` | No | `4` | CPU threads for inference |
| `LOCAL_MODEL_GPU_LAYERS` | No | `0` | Number of model layers offloaded to GPU |
| `LOCAL_LLM_HEALTH_START_PERIOD` | No | `900s` | Docker healthcheck grace period for first local-model load. Keep long enough for CPU-only Qwen startup. |
| `LANCELOT_LOCAL_EXECUTION_MODE` | No | `low_risk_only` | Runtime usage policy for local execution: `low_risk_only` or `disabled` |
| `LANCELOT_FRONTIER_SCRUB_MODE` | No | `required` | Runtime frontier scrub policy: `required`, `preferred`, or `disabled` |

#### Local Model Role Routing

The default deployment uses `LOCAL_LLM_URL` for every local-model role. Operators can split the roles across multiple local model services when they want a small full-payload scrub scanner and a larger segment verifier without adding frontier latency to low-risk utility work. Role URLs are local-control-plane endpoints and must remain on loopback, `host.docker.internal`, or single-label local service names.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LOCAL_LLM_BONSAI_1_7B_URL` | No | `LOCAL_LLM_URL` | Convenience fallback endpoint for the scrub region-finder role |
| `LOCAL_LLM_BONSAI_8B_URL` | No | `LOCAL_LLM_URL` | Convenience fallback endpoint for the segment-verifier and utility roles |
| `LOCAL_LLM_SCRUB_REGION_FINDER_URL` | No | `LOCAL_LLM_BONSAI_1_7B_URL`, then `LOCAL_LLM_URL` | Local endpoint used to scan a pre-scrubbed full payload and return suspicious line regions |
| `LOCAL_LLM_SCRUB_SEGMENT_VERIFIER_URL` | No | `LOCAL_LLM_BONSAI_8B_URL`, then `LOCAL_LLM_URL` | Local endpoint used to redact only suspicious bounded segments |
| `LOCAL_LLM_UTILITY_URL` | No | `LOCAL_LLM_BONSAI_8B_URL`, then `LOCAL_LLM_URL` | Local endpoint used for low-risk utility tasks |
| `LOCAL_LLM_MODEL` | No | `local-llm` | Fallback operator-visible model label for all local roles when the endpoint does not report its own model name |
| `LOCAL_LLM_SCRUB_REGION_FINDER_MODEL` | No | `LOCAL_LLM_MODEL` | Operator-visible model label for the region-finder role |
| `LOCAL_LLM_SCRUB_SEGMENT_VERIFIER_MODEL` | No | `LOCAL_LLM_MODEL` | Operator-visible model label for the segment-verifier role |
| `LOCAL_LLM_UTILITY_MODEL` | No | `LOCAL_LLM_MODEL` | Operator-visible model label for the utility role |
| `LANCELOT_FRONTIER_SCRUB_CASCADE_ENABLED` | No | `true` | Enables deterministic pre-scrub, local region finding, local segment verification, and deterministic residual validation |
| `LANCELOT_FRONTIER_SCRUB_CASCADE_MIN_CHARS` | No | `6000` | Minimum original text length that can trigger the role cascade even when deterministic pre-scrub found nothing |
| `LANCELOT_SCRUB_REGION_FINDER_TIMEOUT_S` | No | `8.0` | Per-request timeout for the region-finder role |
| `LANCELOT_SCRUB_SEGMENT_VERIFIER_TIMEOUT_S` | No | `10.0` | Per-request timeout for the segment-verifier role |
| `LANCELOT_LOCAL_UTILITY_TIMEOUT_S` | No | `30.0` | Per-request timeout for the utility role |
| `LANCELOT_SCRUB_REGION_FINDER_MAX_CHARS` | No | `6000` | Maximum numbered text payload accepted by each region-finder call. Keep this below the local region-finder context window after prompt overhead. |
| `LANCELOT_SCRUB_REGION_FINDER_MAX_CHUNKS` | No | `8` | Maximum bounded region-finder windows attempted before the scrubber uses deterministic local validation instead of extending latency unboundedly. |
| `LANCELOT_SCRUB_SEGMENT_VERIFIER_MAX_CHARS` | No | `8000` | Maximum suspicious segment size accepted by the verifier role |
| `LANCELOT_LOCAL_UTILITY_MAX_CHARS` | No | `12000` | Maximum input size accepted by the utility role |
| `LANCELOT_SCRUB_REGION_FINDER_ENABLED` | No | `true` | Enables or disables the region-finder role |
| `LANCELOT_SCRUB_SEGMENT_VERIFIER_ENABLED` | No | `true` | Enables or disables the segment-verifier role |
| `LANCELOT_LOCAL_UTILITY_ENABLED` | No | `true` | Enables or disables the utility role |

### Host Bridge

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `HOST_AGENT_URL` | No | `http://host.docker.internal:9111` | URL of the Lancelot Host Agent. Must stay on loopback or `host.docker.internal`; public FQDNs are rejected at runtime. |
| `HOST_AGENT_TOKEN` | Yes when host bridge is enabled | â€” | Shared bearer token between the container runtime and the host agent. The legacy default token is rejected. |

### Logging

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LANCELOT_LOG_LEVEL` | No | `INFO` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LANCELOT_CHAT_RUN_STALE_AFTER_S` | No | `3600` | Startup cleanup threshold for async Command Center runs that were still queued/running when the previous gateway process exited |

### Command Center Continuity

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LANCELOT_WORK_QUIET_CHECKPOINT_AFTER_S` | No | `300` | Quiet-phase threshold for automatically checkpointing active work when the Command Center polls `/api/work/active` |
| `LANCELOT_CHAT_HISTORY_MAX_MESSAGES` | No | `200` | Maximum recent chat messages retained verbatim before deterministic compaction runs |
| `LANCELOT_CHAT_HISTORY_RECENT_KEEP` | No | `120` | Number of most recent chat messages kept verbatim after compaction |
| `LANCELOT_CHAT_HISTORY_COMPACT_BATCH` | No | `40` | Number of older chat messages summarized per deterministic compaction record |
| `LANCELOT_CHAT_SUMMARIES_MAX` | No | `40` | Maximum compacted chat summary records retained in `lancelot_data/chat/chat_summaries.json` |

### Integrations

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | No | — | Telegram bot token for messaging integration |
| `TELEGRAM_CHAT_ID` | No | — | Telegram chat ID for delivery |
| `GOOGLE_CHAT_WEBHOOK_URL` | No | — | Google Chat webhook URL |

### Feature Flags

All feature flags are boolean: `true`/`1`/`yes` to enable, anything else to disable. All flags are **hot-toggleable** — changes take effect immediately without a container restart. Core subsystem flags (Soul, Skills, Scheduler, Health Monitor, Memory, BAL) use the SubsystemManager to lazily initialize or gracefully shut down their subsystems at runtime.

**Dependency enforcement:** Some flags have `requires` dependencies (e.g., `FEATURE_SCHEDULER` requires `FEATURE_SKILLS`). The API validates these before toggling — enabling a flag without its dependencies returns a 400 error. Disabling a flag that other enabled flags depend on is also blocked. See the War Room Kill Switches page for the full dependency graph.

| Variable | Default | Description |
|----------|---------|-------------|
| `FEATURE_SOUL` | `true` | Constitutional governance subsystem |
| `FEATURE_SKILLS` | `true` | Modular skill system |
| `FEATURE_HEALTH_MONITOR` | `true` | Background health monitoring |
| `FEATURE_SCHEDULER` | `true` | Automated job scheduling |
| `FEATURE_MEMORY_VNEXT` | `false` | Tiered memory system |
| `FEATURE_BAL` | `false` | Business Automation Layer |
| `FEATURE_TOOLS_FABRIC` | `true` | Tool execution layer |
| `FEATURE_TOOLS_CLI_PROVIDERS` | `false` | CLI tool adapters |
| `FEATURE_TOOLS_ANTIGRAVITY` | `false` | Generative UI/Vision providers |
| `FEATURE_TOOLS_NETWORK` | `false` | Network access from sandbox |
| `FEATURE_TOOLS_HOST_EXECUTION` | `false` | Host execution (no Docker sandbox — **DANGEROUS**) |
| `FEATURE_AGENTIC_LOOP` | `false` | Agentic tool loop |
| `FEATURE_LOCAL_AGENTIC` | `false` | Enable the local utility execution lane for bounded low-risk work |
| `FEATURE_RESPONSE_ASSEMBLER` | `true` | Response assembly pipeline |
| `FEATURE_EXECUTION_TOKENS` | `true` | Execution token minting |
| `FEATURE_TASK_GRAPH_EXECUTION` | `true` | Task graph compilation |
| `FEATURE_NETWORK_ALLOWLIST` | `true` | Network domain allowlist enforcement |
| `FEATURE_VOICE_NOTES` | `true` | Voice note support |
| `FEATURE_RISK_TIERED_GOVERNANCE` | `false` | Risk-tiered governance master switch |
| `FEATURE_POLICY_CACHE` | `false` | Boot-time policy compilation |
| `FEATURE_ASYNC_VERIFICATION` | `false` | Async verification for T1 actions |
| `FEATURE_INTENT_TEMPLATES` | `false` | Cached intent plan templates |
| `FEATURE_BATCH_RECEIPTS` | `false` | Batched receipt emission |
| `FEATURE_TOOLS_UAB` | `false` | Universal Application Bridge (desktop app control) |
| `FEATURE_HIVE` | `false` | Hive Agent Mesh (ephemeral sub-agents) |
| `FEATURE_HIVE_UAB` | `false` | UAB integration for Hive sub-agents |

#### Feature Flag Metadata Properties

Some feature flags include extended metadata for the War Room UI:

| Property | Description |
|----------|-------------|
| `category` | Grouping for the Kill Switches page (e.g., `"tools"`, `"agents"`) |
| `requires` | List of prerequisite flags that must be enabled first |
| `warning` | Warning text shown when enabling a potentially dangerous flag |
| `confirm_enable` | If `true`, requires confirmation dialog before enabling |
| `has_editor` | Special UI editor panel (e.g., `"uab_panel"` for the UAB status panel) |

#### Feature Flag Dependencies

| Flag | Requires |
|------|----------|
| `FEATURE_TOOLS_UAB` | `FEATURE_TOOLS_FABRIC` |
| `FEATURE_HIVE` | — |
| `FEATURE_HIVE_UAB` | `FEATURE_HIVE`, `FEATURE_TOOLS_UAB` |

---

## YAML Configuration Files

All YAML configs live in the `config/` directory.

### `config/models.yaml`

LLM provider and model assignments. Controls which models are used for each routing lane.

```yaml
models:
  primary:
    provider: google        # google, openai, or anthropic
    name: gemini-3-flash-preview
    temperature: 0.7
    max_tokens: 8192
  orchestrator:
    provider: google
    name: gemini-3-flash-preview
    temperature: 0.3
    max_tokens: 4096
  utility:
    provider: google
    name: gemini-3-flash-preview
    temperature: 0.5
    max_tokens: 2048

aliases:
  default: primary
  planner: orchestrator
  quick: utility
```

**Example file:** `config/models.example.yaml`

### `config/model_profiles.yaml`

Static capability database for known models. Provides cost rates, context windows, and capability tiers for lane assignment. Updated with Lancelot releases.

| Field | Description |
|-------|-------------|
| `capability_tier` | `fast` or `deep` |
| `context_window` | Maximum context in tokens |
| `supports_tools` | Whether the model supports tool/function calling |
| `cost_input_per_1k` | Cost per 1,000 input tokens (USD) |
| `cost_output_per_1k` | Cost per 1,000 output tokens (USD) |

### `config/router.yaml`

Routing rules for directing requests to agents. Controls the Model Router's lane selection behavior.

```yaml
router:
  default_agent: orchestrator
  rules:
    - pattern: "execute|deploy|automate"
      agent: crusader
      confidence_threshold: 0.8
    - pattern: "plan|schedule|organize"
      agent: planner
      confidence_threshold: 0.7
  fallback:
    agent: orchestrator
    log_unrouted: true
```

**Example file:** `config/router.example.yaml`

### `config/governance.yaml`

Risk-tiered governance configuration.

```yaml
version: "1.0"

risk_classification:
  defaults:                          # Base risk tier per capability
    fs.read: 0                       # T0_INERT
    fs.write: 1                      # T1_REVERSIBLE
    shell.exec: 2                    # T2_CONTROLLED
    net.post: 3                      # T3_IRREVERSIBLE

  scope_escalations:                 # Conditions that upgrade the tier
    - capability: "fs.write"
      scope: "outside_workspace"
      escalate_to: 3

policy_cache:
  enabled: true
  recompile_on_soul_change: true

async_verification:
  enabled: true
  max_workers: 2
  queue_max_depth: 10

intent_templates:
  enabled: true
  promotion_threshold: 3
  max_template_age_days: 30

batch_receipts:
  enabled: true
  buffer_size: 20
  flush_on_tier_boundary: true
```

See [Governance](governance.md) for detailed explanations of each section.

### `config/connectors.yaml`

Connector registry, rate limits, and per-connector settings.

```yaml
version: '2.0'

settings:
  max_concurrent_requests: 10
  default_timeout_seconds: 30
  retry_max_attempts: 3
  retry_backoff_seconds: 1

rate_limits:
  default:
    max_requests_per_minute: 60
    burst_limit: 10
  per_connector:
    email:
      max_requests_per_minute: 30
      burst_limit: 5

connectors:
  email:
    enabled: true
    backend: smtp
    settings:
      max_results_per_query: 50
  telegram:
    enabled: true
    settings:
      chat_id: ''
  x:
    enabled: false
    settings: {}
```

**SMTP Email Credentials:**

When using the `smtp` backend for the email connector, the following credential vault keys must be configured:

| Vault Key | Type | Required | Description |
|-----------|------|----------|-------------|
| `email.smtp_host` | config | Yes | SMTP server hostname (e.g., `smtp.gmail.com`) |
| `email.smtp_port` | config | Yes | SMTP server port (e.g., `587` for TLS, `465` for SSL) |
| `email.smtp_username` | config | Yes | SMTP login username |
| `email.smtp_password` | api_key | Yes | SMTP password or app password |
| `email.smtp_from_address` | config | Yes | Sender email address |
| `email.smtp_use_tls` | config | No | `true` to use STARTTLS (default: true) |
| `email.imap_host` | config | No | IMAP server hostname for reading email |
| `email.imap_port` | config | No | IMAP server port (e.g., `993`) |

**X (Twitter) Credentials:**

| Vault Key | Type | Required | Description |
|-----------|------|----------|-------------|
| `x.api_key` | api_key | Yes | X API Key (from developer portal) |
| `x.api_key_secret` | api_key | Yes | X API Key Secret |
| `x.access_token` | api_key | Yes | X Access Token (OAuth 1.0a) |
| `x.access_token_secret` | api_key | Yes | X Access Token Secret |

### `config/scheduler.yaml`

Automated job definitions.

```yaml
jobs:
  - id: health_sweep
    name: "Health Sweep"
    trigger:
      type: interval          # interval or cron
      seconds: 60             # for interval triggers
    enabled: true
    requires_ready: true
    requires_approvals: []    # list of approval requirements
    timeout_s: 30
    skill: health_check
    description: "Periodic health check sweep."

  - id: memory_cleanup
    name: "Memory Cleanup"
    trigger:
      type: cron
      expression: "0 3 * * *"  # 5-field cron expression
    enabled: true
    requires_ready: true
    requires_approvals: []
    timeout_s: 120
    skill: memory_cleanup
```

**Example file:** `config/scheduler.example.yaml`

**Trigger types:**
- `interval` — runs every N seconds (`seconds` field)
- `cron` — runs on a cron schedule (`expression` field, 5-field format: minute hour day-of-month month day-of-week)

### `config/trust_graduation.yaml`

Trust Ledger thresholds and revocation behavior.

```yaml
version: "1.0"

thresholds:
  T3_to_T2: 50              # Actions needed to graduate T3 → T2
  T2_to_T1: 100             # Actions needed to graduate T2 → T1
  T1_to_T0: 200             # Actions needed to graduate T1 → T0

revocation:
  on_failure: "reset_to_default"
  on_rollback: "reset_above_default"
  cooldown_after_denial: 50
  cooldown_after_revocation: 25

proposal_delivery: "war_room"
```

### `config/approval_learning.yaml`

Approval Pattern Learning (APL) detection and rule parameters.

```yaml
version: "1.0"

detection:
  min_observations: 20
  confidence_threshold: 0.85
  max_pattern_dimensions: 3
  analysis_window_days: 30
  analysis_trigger_interval: 10

rules:
  max_active_rules: 50
  max_auto_decisions_per_day: 50
  max_auto_decisions_total: 500
  re_confirmation_interval: 500
  cooldown_after_decline: 30

never_automate:
  - "connector.stripe.charge_customer"
  - "connector.stripe.refund_charge"
  - "connector.*.delete_*"

persistence:
  decision_log_path: "data/apl/decisions.jsonl"
  rules_path: "data/apl/rules.json"
  patterns_path: "data/apl/patterns.json"
```

### `config/hive.yaml`

Hive Agent Mesh configuration. Controls capacity, governance, UAB integration, and retry behavior.

```yaml
# Capacity
max_concurrent_agents: 10           # Max active agents (paused count toward limit)
default_task_timeout: 300            # Default seconds per agent before timeout
max_actions_per_agent: 50            # Default action count limit per agent
max_subtasks_per_decomposition: 20   # Max subtasks from LLM decomposer

# Governance
spawn_approval_tier: "T2"           # Minimum tier for spawn approval
default_control_method: "supervised" # Default: fully_autonomous, supervised, manual_confirm
collapse_on_governance_violation: true  # Collapse agent on governance denial
collapse_on_soul_violation: true     # Collapse agent on Soul constraint violation

# UAB Integration
uab_enabled: false                   # Enable desktop app control for sub-agents
uab_allowed_apps: []                 # App name allowlist (empty = all allowed)

# Retry
max_retry_attempts: 2                # Max replan attempts
never_retry_identical_plan: true     # Reject replans that produce identical plan hash

# Logging
log_agent_actions: true              # Log individual agent actions
log_decomposition: true              # Log task decomposition details
```

### Soul Overlay: `soul/overlays/hive.yaml`

Hive governance overlay — adds subsystem-specific rules on top of the base Soul. Contains five non-negotiable rules (hive_no_autonomous_t3, hive_collapse_on_governance_violation, hive_scoped_soul_monotonic, hive_intervention_requires_reason, hive_never_retry_identical) plus allowed_autonomous and requires_approval action lists.

See [Hive Agent Mesh](hive.md) for full rule descriptions.

### `config/network_allowlist.yaml`

Outbound domain allowlist. Only these domains can be contacted from within the Lancelot container.
When `FEATURE_NETWORK_ALLOWLIST=true`, an empty or missing allowlist now fails closed for Tool Fabric and other governed outbound paths.

```yaml
domains:
  - api.anthropic.com
  - api.github.com
  - api.telegram.org
  - api.x.com
  - generativelanguage.googleapis.com
  - github.com
  - raw.githubusercontent.com
```

Add domains as needed for your connectors and integrations.

### `config/vault.yaml`

Credential vault configuration.

```yaml
version: "1.0"

storage:
  path: "data/vault/credentials.enc"
  backup_path: "data/vault/credentials.enc.bak"
  encryption: fernet
  key_env: "LANCELOT_VAULT_KEY"

audit:
  access_log: "data/vault/access.log"
  log_access: true
```

---

### UAB Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `UAB_DAEMON_URL` | `http://host.docker.internal:7900` | UAB daemon address (for container → host communication) |
| `UAB_DAEMON_PORT` | `7900` | UAB daemon listen port (for host-side startup) |
| `UAB_LOG_LEVEL` | `info` | Daemon log level: `debug`, `info`, `warn`, `error` |
| `UAB_LOG_FILE` | _(none)_ | Optional daemon log file path |

---

## Soul Configuration

Soul files live in the `soul/` directory, not in `config/`. See [Authoring Souls](authoring-souls.md) for the complete Soul schema reference.

| File | Description |
|------|-------------|
| `soul/ACTIVE` | Text file containing the active version (e.g., `v1`) |
| `soul/soul.yaml` | Active Soul document (convenience copy) |
| `soul/soul_versions/soul_vN.yaml` | Versioned Soul files |

---

## Data Directories

Runtime data lives in `lancelot_data/` (container path: `/home/lancelot/data`).

The runtime data directory is no longer used as a source-controlled seed location. Canonical bootstrap templates live in `config/bootstrap/`, and the runtime seeds missing `RULES.md` / `CAPABILITIES.md` into `lancelot_data/` on first use.

| Path | Description |
|------|-------------|
| `lancelot_data/receipts/` | Audit trail directory (`receipts.db` immutable log + staging tables, plus `receipt_integrity_key.json` for the persisted finalized-receipt signing key when no external key override is configured) |
| `lancelot_data/chat/chat_log.json` | Recent conversation history retained verbatim |
| `lancelot_data/chat/chat_summaries.json` | Deterministic summaries of compacted older chat history |
| `lancelot_data/USER.md` | Owner profile |
| `lancelot_data/RULES.md` | Runtime copy of the operating-rules bootstrap template |
| `lancelot_data/CAPABILITIES.md` | Runtime copy of the capabilities bootstrap template |
| `lancelot_data/scheduler.sqlite` | Scheduler job state and run history |
| `lancelot_data/memory.sqlite` | Memory database (if structured memory enabled) |
| `lancelot_data/skills_registry.json` | Installed skills |
| `lancelot_data/skill_proposals.json` | Skill proposal index and review state |
| `lancelot_data/skill_proposals/` | Per-proposal governed artifact packages (`skill.yaml`, `security_manifest.yaml`, code, tests, README, hashes) |
| `lancelot_data/soul_proposals.json` | Soul amendment proposals |
| `lancelot_data/vault/` | Encrypted credential storage |
| `lancelot_data/apl/` | APL decision logs and rules |
| `lancelot_data/governance/` | Policy cache and intent templates |
| `lancelot_data/governance/trust_ledger.json` | Persisted Trust Ledger records, proposals, and graduation history |
| `lancelot_data/work/work_ledger.sqlite` | Active-work ledger for long-running Command Center tasks and checkpoints |
| `lancelot_data/receipts/uab/` | UAB action receipt exports and session artifacts |
| `lancelot_data/receipts/uab/sessions/` | UAB session summaries |
| `config/bootstrap/` | Source-controlled bootstrap templates seeded into the runtime data dir |
| `config/hive.yaml` | Hive Agent Mesh configuration |
| `config/federation_topology.example.json` | Example federation topology document; copy to `config/federation_topology.json` for local deployments |
| `soul/overlays/hive.yaml` | Hive governance overlay |
