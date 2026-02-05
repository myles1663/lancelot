# Lancelot Tool Fabric Specification

**Document Version:** 1.0
**Status:** In Progress
**Last Updated:** 2026-02-05
**Depends on:** v6.0 Core (Soul, Skills, Heartbeat, Scheduler)

---

## 1. Executive Summary

### Goal

Decouple Lancelot from Google-specific execution tooling (Gemini CLI, Antigravity) by introducing a **Tool Fabric**: a stable set of capability interfaces with multiple provider implementations (local sandbox runner, agent CLIs, optional Antigravity adapter). Lancelot keeps the same outcomes (edit repo, run commands, scaffold UI, deploy, automate) but can switch tool backends without code churn.

### Core Design Rule

Lancelot must remain functional if all external agent CLIs are disabled.
Agent CLIs become enhancers, not a single point of failure.

---

## 2. Scope

### In Scope

- **Capability Contracts** (stable interfaces): ShellExec, RepoOps, FileOps, WebOps, UIBuilder, DeployOps
- **Local Tool Runner** (containerized): the default execution path for shell/git/files
- **Agent CLI adapters** (optional providers): OpenCode, Aider, Continue CLI, Open Interpreter
- **Provider Router**: policy-based selection + health-based failover
- **Receipts integration**: every tool invocation produces auditable receipts
- **Security gates**: allowlists/denylists, sandbox constraints, risk levels
- **Health checks**: validate presence/versions/config on startup + runtime probes
- **UI + config**: enable/disable providers, prioritize, view health, view receipts
- **Testing**: unit + integration + "tool offline" degradation tests

### Explicitly Supported Capabilities

- Vision-based GUI driving (via Antigravity)
- App generation from high-level intent
- Hybrid flows (template scaffold + generative refinement)

### Out of Scope (Deferred)

- Native, non-Antigravity vision engine
- Alternative generative UI engines
- Multi-provider vision arbitration

---

## 3. Non-Functional Requirements

### Reliability

Tool Fabric must support graceful degradation:
- If a provider fails, router selects the next provider
- If all agent CLIs fail, fall back to Local Tool Runner for core operations

### Determinism & Auditability

Every tool call produces a receipt containing:
- provider, version, command/task, workspace
- inputs, outputs (bounded), exit code
- timestamps, hashes of changed files
- risk score, verification result

### Security

Default to containerized execution with:
- workspace mount only
- optional read-only mounts
- network on/off per policy
- command allowlist
- time/memory limits

Planner/Verifier must gate risky steps before execution.

### Portability

- Must work on Mac/Windows/Linux host via Docker
- Provider installs should be bundled binaries OR pinned container images OR pinned pip/npm dependencies

---

## 4. Capability Definitions

### 4.1 Core Capabilities

| Capability | Description |
|------------|-------------|
| **ShellExec** | Run commands, stream logs |
| **RepoOps** | git clone/status/branch/commit/diff/apply patch |
| **FileOps** | read/write/list, apply unified diff safely |
| **WebOps** | fetch, screenshot, download, (optional) browser automation |
| **UIBuilder** | scaffold UI from deterministic templates + spec |
| **DeployOps** | build/test/package/deploy inside sandbox |
| **VisionControl** | screen perception + action (Antigravity required) |

### 4.2 Provider Model

**Required Provider:**
- `LocalSandboxProvider` (Docker-based Tool Runner) → must cover ShellExec/RepoOps/FileOps/DeployOps baseline

**Optional Providers:**
- `AgentCliProvider.Aider`
- `AgentCliProvider.OpenCode`
- `AgentCliProvider.ContinueHeadless`
- `AgentCliProvider.OpenInterpreter`
- `UIBuilderProvider.Antigravity` (optional "legacy" backend)
- `UIBuilderProvider.TemplateScaffold` (deterministic default)
- `AntigravityVisionProvider` (for VisionControl)

---

## 5. Architecture

### 5.1 Module Structure

```
src/tools/
  contracts.py            # capability interfaces + types
  router.py               # provider routing + failover
  receipts.py             # receipt schema + builders for tool calls
  policies.py             # risk policies, allowlists, network rules
  health.py               # provider discovery + probes
  fabric.py               # main Tool Fabric orchestration
  providers/
    local_sandbox.py      # Docker runner implementation (required)
    cli_aider.py          # optional
    cli_opencode.py       # optional
    cli_continue.py       # optional
    cli_open_interpreter.py # optional
    ui_templates.py       # deterministic scaffolder (required)
    ui_antigravity.py     # optional adapter
    vision_antigravity.py # optional VisionControl
```

### 5.2 Router Logic

Selects providers based on:
- requested capability
- task profile (interactive vs headless)
- workspace state
- risk level
- provider health
- user/system preferences
- feature flags

---

## 6. Security & Risk Policies

### 6.1 Risk Classes

| Risk | Examples |
|------|----------|
| **LOW** | read/list/status, safe scaffolding |
| **MEDIUM** | apply patches, install deps inside container, run tests |
| **HIGH** | network enabled, deploy, delete operations, credential handling |

### 6.2 Default Policy Rules

- Network OFF by default
- `rm -rf`, disk formatting, privileged docker flags: **deny**
- write outside workspace: **deny**
- secrets in stdout: **redact**
- any HIGH action requires verifier confirmation path

### 6.3 VisionControl Policies

- Allowed apps/domains list
- Max action count
- Confirmation gates for destructive UI actions

---

## 7. Feature Flags

| Flag | Default | Description |
|------|---------|-------------|
| `FEATURE_TOOLS_FABRIC` | true | Global Tool Fabric enable |
| `FEATURE_TOOLS_CLI_PROVIDERS` | false | Enable optional CLI providers |
| `FEATURE_TOOLS_ANTIGRAVITY` | false | Enable Antigravity providers |
| `FEATURE_TOOLS_NETWORK` | false | Allow network access in sandbox |
| `FEATURE_TOOLS_HOST_EXECUTION` | false | Allow host execution (dangerous, default false) |

---

## 8. Receipt Extensions

Tool invocation receipts include:
- `receipt_id`, `timestamp`, `session_id`
- `capability`, `action`
- `provider_id`, `provider_version`
- `workspace_id/path` (redacted)
- `policy_snapshot` (network on/off, allowlist applied)
- `inputs_summary` (redacted)
- `stdout/stderr` (bounded, redacted)
- `exit_code`
- `changed_files`: list with hashes before/after
- `verification_steps` + results
- `risk_score` + decision trace

Vision receipts include:
- screenshots (hashed)
- detected UI elements
- actions performed
- timing + confidence scores

---

## 9. Acceptance Criteria

Lancelot can:
- [ ] Run commands inside sandbox without Gemini CLI / Antigravity
- [ ] Edit files and apply patches in sandbox
- [ ] Run tests in sandbox
- [ ] Scaffold a UI template without Antigravity
- [ ] Generate receipt for every tool call with file hashes
- [ ] Gracefully degrade when optional providers offline
- [ ] Block unsafe operations via denylist/allowlist

Provider health view exists in War Room.
Disabling all optional providers does not break core flows.

---

## 10. Antigravity Retention Note

This revision explicitly **retains Antigravity as a first-class provider** for:
- Vision-based UI control
- Generative application building

The architectural goal is **containment, not removal**:
Antigravity remains available, powerful, and enabled, while Lancelot's core is no longer *dependent* on it to function.

---

**End of Specification**
