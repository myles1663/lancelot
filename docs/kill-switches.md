# Kill Switches (Feature Flags)

Lancelot uses hot-toggleable feature flags as kill switches for every high-risk subsystem. Each flag can be toggled at runtime via the War Room without container restart.

---

## How Kill Switches Work

### Priority System

1. **Persisted state** (`data/.flag_state.json`) — Written by War Room toggles, survives restart
2. **Environment variable** (`.env` / `docker-compose`) — Set at deployment
3. **Hardcoded default** — Safe fallback

### Runtime Control

- `toggle_flag(name)` — Hot-toggle, persists to disk immediately
- `set_flag(name, value)` — Set specific value, persists
- `reload_flags()` — Re-read from environment
- War Room Kill Switches page provides toggle UI for all flags

---

## Complete Flag Reference

### Core Subsystems

| Flag | Default | Description |
|------|---------|-------------|
| `FEATURE_SOUL` | true | Constitutional identity and governance rules |
| `FEATURE_SKILLS` | true | Skill registry, ownership tracking, factory pipeline |
| `FEATURE_HEALTH_MONITOR` | true | Background liveness/readiness probes |
| `FEATURE_SCHEDULER` | true | Cron/interval job scheduling. *Requires: FEATURE_SKILLS* |
| `FEATURE_MEMORY_VNEXT` | false | Tiered memory (persona, human, mission, workspace) |

### Tool Fabric

| Flag | Default | Description |
|------|---------|-------------|
| `FEATURE_TOOLS_FABRIC` | true | Global enable for all tool providers |
| `FEATURE_TOOLS_CLI_PROVIDERS` | false | CLI adapters for shell tools. *Requires: FEATURE_TOOLS_FABRIC* |
| `FEATURE_TOOLS_ANTIGRAVITY` | false | UI scaffolding, vision, browser. *Requires: FEATURE_TOOLS_FABRIC* |
| `FEATURE_TOOLS_NETWORK` | false | Network access in sandbox. *Requires: FEATURE_TOOLS_FABRIC* |
| `FEATURE_TOOLS_HOST_EXECUTION` | false | Docker Linux access. *Requires: FEATURE_TOOLS_FABRIC* |
| `FEATURE_TOOLS_HOST_BRIDGE` | false | **DANGEROUS:** Direct host OS bridge. *Requires: FEATURE_TOOLS_FABRIC* |
| `FEATURE_HOST_WRITE_COMMANDS` | false | **EXTREME DANGER:** rm, del, kill on host. *Requires: FEATURE_TOOLS_HOST_BRIDGE* |
| `FEATURE_TOOLS_UAB` | false | Universal App Bridge for desktop control. *Requires: FEATURE_TOOLS_FABRIC + FEATURE_TOOLS_HOST_BRIDGE* |

### Execution & Runtime

| Flag | Default | Description |
|------|---------|-------------|
| `FEATURE_RESPONSE_ASSEMBLER` | true | Response formatting, citation injection, artifact extraction |
| `FEATURE_EXECUTION_TOKENS` | true | Time-limited, permission-scoped execution tokens |
| `FEATURE_TASK_GRAPH_EXECUTION` | true | Multi-step task planning with DAG execution |
| `FEATURE_NETWORK_ALLOWLIST` | false | Domain-based network restrictions |
| `FEATURE_VOICE_NOTES` | true | Audio transcription |
| `FEATURE_AGENTIC_LOOP` | false | Multi-step autonomous execution. *Requires: FEATURE_SKILLS* |
| `FEATURE_LOCAL_AGENTIC` | false | Use local LLM for agentic steps. *Requires: FEATURE_AGENTIC_LOOP* |

### Governance (vNext4)

| Flag | Default | Description |
|------|---------|-------------|
| `FEATURE_RISK_TIERED_GOVERNANCE` | false | Master switch for 4-tier risk classification (T0–T3). *Requires: FEATURE_SOUL* |
| `FEATURE_POLICY_CACHE` | false | Boot-time policy compilation. *Requires: FEATURE_RISK_TIERED_GOVERNANCE* |
| `FEATURE_ASYNC_VERIFICATION` | false | Background verification for T1 actions. *Requires: FEATURE_RISK_TIERED_GOVERNANCE* |
| `FEATURE_INTENT_TEMPLATES` | false | Cached execution plan templates. *Requires: FEATURE_RISK_TIERED_GOVERNANCE* |
| `FEATURE_BATCH_RECEIPTS` | false | Batched receipt I/O |

### Capability Upgrades

| Flag | Default | Description |
|------|---------|-------------|
| `FEATURE_CONNECTORS` | false | External connector system. *Requires: FEATURE_TOOLS_FABRIC* |
| `FEATURE_TRUST_LEDGER` | false | Progressive tier relaxation. *Requires: FEATURE_RISK_TIERED_GOVERNANCE* |
| `FEATURE_SKILL_SECURITY_PIPELINE` | false | 6-stage security pipeline for new skills. *Requires: FEATURE_SKILLS* |
| `FEATURE_APPROVAL_LEARNING` | false | Learn owner decision patterns. *Requires: FEATURE_RISK_TIERED_GOVERNANCE* |

### Intelligence & Reasoning

| Flag | Default | Description |
|------|---------|-------------|
| `FEATURE_STRUCTURED_OUTPUT` | false | JSON schema output with receipt verification. *Requires: FEATURE_AGENTIC_LOOP* |
| `FEATURE_CLAIM_VERIFICATION` | false | Cross-reference response claims vs tool receipts |
| `FEATURE_UNIFIED_CLASSIFICATION` | false | Single LLM call for intent routing |
| `FEATURE_GITHUB_SEARCH` | true | GitHub API search skill. *Requires: FEATURE_AGENTIC_LOOP* |
| `FEATURE_COMPETITIVE_SCAN` | false | Episodic memory for scan diffing. *Requires: FEATURE_MEMORY_VNEXT* |
| `FEATURE_DEEP_REASONING_LOOP` | false | Deep reasoning pass before agentic execution |

### Authentication

| Flag | Default | Description |
|------|---------|-------------|
| `FEATURE_GOOGLE_OAUTH` | false | OAuth 2.0 for Gmail + Calendar |
| `FEATURE_VAULT_SECRETS` | true | Fernet-encrypted credential vault |

### Streaming & UI

| Flag | Default | Description |
|------|---------|-------------|
| `FEATURE_TOOL_FLOW_STREAMING` | false | Real-time tool execution progress events |
| `FEATURE_ACTION_CARDS` | false | Interactive approval/action buttons |

### Agent Systems

| Flag | Default | Description |
|------|---------|-------------|
| `FEATURE_HIVE` | false | Ephemeral sub-agent architecture |
| `FEATURE_HIVE_UAB` | false | HIVE agents control desktop apps. *Requires: FEATURE_HIVE + FEATURE_TOOLS_UAB* |
| `FEATURE_FEDERATION` | false | Multi-instance coordination |
| `FEATURE_MCP` | false | Governed MCP tool invocations |

### Business Automation

| Flag | Default | Description |
|------|---------|-------------|
| `FEATURE_BAL` | false | CRM, intake, delivery, billing workflows |

---

## Safe Defaults

- Dangerous features default to **off** (network, host bridge, write commands, UAB)
- Core subsystems default to **on** (Soul, Skills, Health)
- New subsystems default to **off** until validated (Federation, MCP, HIVE)
- Unknown capabilities are classified as **T3** (maximum governance)

---

## Dependency Chains

Some flags require others to be enabled:

```
FEATURE_TOOLS_FABRIC
  ├── FEATURE_TOOLS_CLI_PROVIDERS
  ├── FEATURE_TOOLS_ANTIGRAVITY
  ├── FEATURE_TOOLS_NETWORK
  ├── FEATURE_TOOLS_HOST_EXECUTION
  ├── FEATURE_TOOLS_HOST_BRIDGE
  │     ├── FEATURE_HOST_WRITE_COMMANDS
  │     └── FEATURE_TOOLS_UAB
  └── FEATURE_CONNECTORS
        └── FEATURE_MCP

FEATURE_SOUL
  └── FEATURE_RISK_TIERED_GOVERNANCE
        ├── FEATURE_POLICY_CACHE
        ├── FEATURE_ASYNC_VERIFICATION
        ├── FEATURE_INTENT_TEMPLATES
        ├── FEATURE_TRUST_LEDGER
        └── FEATURE_APPROVAL_LEARNING

FEATURE_SKILLS
  ├── FEATURE_SCHEDULER
  ├── FEATURE_SKILL_SECURITY_PIPELINE
  └── FEATURE_AGENTIC_LOOP
        ├── FEATURE_LOCAL_AGENTIC
        ├── FEATURE_STRUCTURED_OUTPUT
        └── FEATURE_GITHUB_SEARCH

FEATURE_HIVE
  └── FEATURE_HIVE_UAB (also requires FEATURE_TOOLS_UAB)
```
