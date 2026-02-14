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

## Environment Variables (`.env`)

The `.env` file is the primary configuration for secrets and runtime settings. It is never committed to git.

### LLM API Keys

| Variable | Required | Description |
|----------|----------|-------------|
| `GEMINI_API_KEY` | One of three | Google Gemini API key (starts with `AIza...`) |
| `OPENAI_API_KEY` | One of three | OpenAI API key (starts with `sk-...`) |
| `ANTHROPIC_API_KEY` | One of three | Anthropic API key (starts with `sk-ant-...`) |

At least one API key is required. You can configure one, two, or all three providers.

### Authentication

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LANCELOT_OWNER_TOKEN` | Yes | — | Token for administrative operations (Soul amendments, memory writes, approvals) |
| `LANCELOT_VAULT_KEY` | No | — | Encryption key for credential vault (Fernet) |

### Local Model

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LOCAL_LLM_URL` | No | `http://local-llm:8080` | URL of the local GGUF model server |
| `LOCAL_MODEL_CTX` | No | `4096` | Context window size (tokens) |
| `LOCAL_MODEL_THREADS` | No | `4` | CPU threads for inference |
| `LOCAL_MODEL_GPU_LAYERS` | No | `0` | Number of model layers offloaded to GPU |

### Logging

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LANCELOT_LOG_LEVEL` | No | `INFO` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

### Integrations

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | No | — | Telegram bot token for messaging integration |
| `TELEGRAM_CHAT_ID` | No | — | Telegram chat ID for delivery |
| `GOOGLE_CHAT_WEBHOOK_URL` | No | — | Google Chat webhook URL |

### Feature Flags

All feature flags are boolean: `true`/`1`/`yes` to enable, anything else to disable.

| Variable | Default | Description |
|----------|---------|-------------|
| `FEATURE_SOUL` | `true` | Constitutional governance subsystem |
| `FEATURE_SKILLS` | `true` | Modular skill system |
| `FEATURE_HEALTH_MONITOR` | `true` | Background health monitoring |
| `FEATURE_SCHEDULER` | `true` | Automated job scheduling |
| `FEATURE_MEMORY_VNEXT` | `false` | Tiered memory system |
| `FEATURE_TOOLS_FABRIC` | `true` | Tool execution layer |
| `FEATURE_TOOLS_CLI_PROVIDERS` | `false` | CLI tool adapters |
| `FEATURE_TOOLS_ANTIGRAVITY` | `false` | Generative UI/Vision providers |
| `FEATURE_TOOLS_NETWORK` | `false` | Network access from sandbox |
| `FEATURE_TOOLS_HOST_EXECUTION` | `false` | Host execution (no Docker sandbox — **DANGEROUS**) |
| `FEATURE_AGENTIC_LOOP` | `false` | Agentic tool loop |
| `FEATURE_LOCAL_AGENTIC` | `false` | Route simple queries to local model |
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

---

## YAML Configuration Files

All YAML configs live in the `config/` directory.

### `config/models.yaml`

LLM provider and model assignments. Controls which models are used for each routing lane.

```yaml
models:
  primary:
    provider: google        # google, openai, or anthropic
    name: gemini-2.0-flash
    temperature: 0.7
    max_tokens: 8192
  orchestrator:
    provider: google
    name: gemini-2.0-flash
    temperature: 0.3
    max_tokens: 4096
  utility:
    provider: google
    name: gemini-2.0-flash
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
```

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

### `config/network_allowlist.yaml`

Outbound domain allowlist. Only these domains can be contacted from within the Lancelot container.

```yaml
domains:
  - api.anthropic.com
  - api.github.com
  - api.telegram.org
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

| Path | Description |
|------|-------------|
| `lancelot_data/receipts/` | Audit trail (JSON files) |
| `lancelot_data/chat_log.json` | Conversation history |
| `lancelot_data/USER.md` | Owner profile |
| `lancelot_data/scheduler.sqlite` | Scheduler job state and run history |
| `lancelot_data/memory.sqlite` | Memory database (if Memory vNext enabled) |
| `lancelot_data/skills_registry.json` | Installed skills |
| `lancelot_data/skill_proposals.json` | Skill proposal pipeline |
| `lancelot_data/soul_proposals.json` | Soul amendment proposals |
| `lancelot_data/vault/` | Encrypted credential storage |
| `lancelot_data/apl/` | APL decision logs and rules |
| `lancelot_data/governance/` | Policy cache and intent templates |
