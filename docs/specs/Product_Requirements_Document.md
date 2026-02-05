# Product Requirements Document: Project Lancelot v7.0

**Document Version:** 7.0
**Last Updated:** 2026-02-05
**Status:** Current — reflects v4 Multi-Provider + vNext2 Soul/Skills/Heartbeat/Scheduler + vNext3 Memory + Tool Fabric + Security Hardening

---

## 1. Product Vision

Lancelot is a self-hosted autonomous AI agent designed to operate as a secure, high-context operational partner within the owner's workspace. It replaces stateless chat and lossy retrieval-augmented generation (RAG) with a deterministic long-context architecture, multi-provider model routing, constitutional governance, and receipt-based accountability for every action.

### Core Philosophy

- **Context is King:** Long-context windows (128k+ tokens) provide deterministic, complete awareness of project state. RAG is lossy; direct context loading is not.
- **Autonomy requires Verification:** Agents that guess are dangerous. Agents that plan, execute, and verify are useful. Every autonomous action passes through a Planner/Verifier pipeline.
- **Receipts are Truth:** Every action produces a durable, auditable receipt. If there is no receipt, it did not happen.
- **Single-Owner Allegiance:** Lancelot serves one owner. Constitutional governance (Soul) ensures behavioral boundaries are immutable unless the owner explicitly amends them.
- **Cost-Optimized Intelligence:** A local GGUF model handles routine tasks (classification, summarization, redaction) while flagship providers handle complex reasoning, reducing costs by 75-90%.

---

## 2. Problem Statement

Existing AI agent tools suffer from several critical limitations:

1. **Retrieval Hallucination:** RAG-based agents miss critical context because they only see what keyword search retrieves, leading to incomplete or fabricated reasoning.
2. **No Verification:** Most agents execute plans without validating results, creating silent failures and compounding errors.
3. **No Accountability:** Actions lack auditable trails, making it impossible to understand what the agent did and why.
4. **Vendor Lock-In:** Single-provider architectures create dependency risk and prevent cost optimization.
5. **No Constitutional Governance:** Agent behavior is defined by prompt engineering that can drift, be overridden, or become inconsistent.
6. **Monolithic Capabilities:** Agent functionality is hardcoded, making it impossible to extend, restrict, or audit individual capabilities.

---

## 3. Target Users

| Persona | Description | Primary Use Cases |
|---------|-------------|-------------------|
| **DevOps Engineer** | Manages infrastructure, monitors systems, automates operations | System audits, automated health checks, credential rotation |
| **Software Developer** | Builds and maintains codebases | Code review, bug fixing, refactoring, test generation |
| **Technical Manager** | Oversees engineering teams and processes | Action auditing, compliance reporting, operational oversight |
| **Security Professional** | Manages access controls and security posture | PII redaction, access logging, security policy enforcement |

---

## 4. Product Features

### 4.1 Multi-Provider Model Routing

**Priority:** P0 (Core)

Route tasks to the most cost-effective and capable model based on task type, risk level, and provider availability.

| Requirement | Description |
|-------------|-------------|
| PR-4.1.1 | Support three flagship providers: Google Gemini, OpenAI, Anthropic |
| PR-4.1.2 | Route through four prioritized lanes: local_redaction, local_utility, flagship_fast, flagship_deep |
| PR-4.1.3 | Automatically escalate from fast to deep lane on risk detection or fast-lane failure |
| PR-4.1.4 | Track per-lane usage, token consumption, and cost savings in real time |
| PR-4.1.5 | Produce a RouterDecision record for every routing event |
| PR-4.1.6 | Support lane configuration via YAML (models.yaml, router.yaml) |

### 4.2 Local LLM Service

**Priority:** P0 (Core)

Run a local GGUF model for routine utility tasks to reduce cost and latency.

| Requirement | Description |
|-------------|-------------|
| PR-4.2.1 | Serve a local GGUF model via FastAPI HTTP endpoint |
| PR-4.2.2 | Support five utility task types: classify_intent, extract_json, summarize, redact, rag_rewrite |
| PR-4.2.3 | Provide health endpoint for liveness monitoring |
| PR-4.2.4 | Support configurable context window, thread count, and GPU layer offload |
| PR-4.2.5 | Pin model versions via lockfile (models.lock.yaml) |
| PR-4.2.6 | Download model weights on first run with timeout protection |

### 4.3 Constitutional Identity (Soul)

**Priority:** P0 (Core)

Define and enforce immutable behavioral boundaries through a versioned constitutional document.

| Requirement | Description |
|-------------|-------------|
| PR-4.3.1 | Load Soul from versioned YAML files in `soul/soul_versions/` |
| PR-4.3.2 | Validate Soul against Pydantic schema with field validators |
| PR-4.3.3 | Enforce 5 constitutional invariants at load time via linter |
| PR-4.3.4 | Support versioned soul documents with ACTIVE pointer switching |
| PR-4.3.5 | Provide REST API for soul status, amendment approval, and activation |
| PR-4.3.6 | Require owner authentication (Bearer token) for soul modifications |
| PR-4.3.7 | Persist amendment proposals as JSON with full audit trail |

### 4.4 Modular Skills

**Priority:** P1 (Important)

Provide an extensible skill system for adding, managing, and governing discrete capabilities.

| Requirement | Description |
|-------------|-------------|
| PR-4.4.1 | Define skills via declarative YAML manifests (skill.yaml) |
| PR-4.4.2 | Validate skill names (snake_case), versions (non-empty), and permissions (required) |
| PR-4.4.3 | Persist skill registry as JSON with ownership and signature tracking |
| PR-4.4.4 | Support skill lifecycle: install, enable, disable, uninstall |
| PR-4.4.5 | Generate skill skeletons via factory pipeline with owner-gated approval |
| PR-4.4.6 | Restrict marketplace skill permissions to read_input, write_output, read_config |
| PR-4.4.7 | Package skills as distributable .zip archives |
| PR-4.4.8 | Emit receipts for every skill execution |

### 4.5 Health Monitoring (Heartbeat)

**Priority:** P0 (Core)

Provide continuous health monitoring with dependency tracking and state transition receipts.

| Requirement | Description |
|-------------|-------------|
| PR-4.5.1 | Expose /health/live (liveness) and /health/ready (readiness) endpoints |
| PR-4.5.2 | Return HealthSnapshot with ready, onboarding_state, local_llm_ready, scheduler_running, degraded_reasons |
| PR-4.5.3 | Run background monitor loop with configurable check interval |
| PR-4.5.4 | Emit receipts on state transitions: health_ok, health_degraded, health_recovered |
| PR-4.5.5 | Never leak stack traces or internal errors through health endpoints |

### 4.6 Job Scheduling (Chron)

**Priority:** P1 (Important)

Automate periodic tasks through a configurable, gated job scheduler.

| Requirement | Description |
|-------------|-------------|
| PR-4.6.1 | Support interval (every N seconds) and cron (5-field expression) triggers |
| PR-4.6.2 | Persist jobs in SQLite with run history and execution counts |
| PR-4.6.3 | Enforce gating pipeline: onboarding READY, LLM health, owner approvals |
| PR-4.6.4 | Emit typed receipts: scheduled_job_run, scheduled_job_failed, scheduled_job_skipped |
| PR-4.6.5 | Support manual job triggering (run_now) and enable/disable toggles |
| PR-4.6.6 | Prevent autonomous irreversible actions per Soul scheduling_boundaries |
| PR-4.6.7 | Limit concurrent jobs (default: 5) and job duration (default: 300s) |

### 4.7 Receipt-Based Accountability

**Priority:** P0 (Core)

Every discrete action must produce an auditable receipt for traceability and compliance.

| Requirement | Description |
|-------------|-------------|
| PR-4.7.1 | Generate receipts for: LLM calls, file operations, tool calls, plan steps, verifications |
| PR-4.7.2 | Include cognition tier classification (DETERMINISTIC through SYNTHESIS) |
| PR-4.7.3 | Link parent-child receipts for multi-step operation tracing |
| PR-4.7.4 | Persist receipts with timestamps, durations, token counts, and error messages |
| PR-4.7.5 | Support JSON serialization for all receipt fields |

### 4.8 War Room Dashboard

**Priority:** P1 (Important)

Provide a centralized control interface for monitoring and managing all subsystems.

| Requirement | Description |
|-------------|-------------|
| PR-4.8.1 | Display Soul status: active version, pending proposals |
| PR-4.8.2 | Display Skills: installed skills, enable/disable controls |
| PR-4.8.3 | Display Health: real-time snapshot, degraded reasons |
| PR-4.8.4 | Display Scheduler: job listing, manual triggers, status |
| PR-4.8.5 | Display routing decisions and usage telemetry |
| PR-4.8.6 | Handle backend-down gracefully with safe fallback displays |
| PR-4.8.7 | Display Memory: tier contents, recent edits, quarantined items |
| PR-4.8.8 | Display Tool Fabric: provider health, capability routing, recent receipts, safe mode toggle |

### 4.9 Security

**Priority:** P0 (Core)

Protect against prompt injection, unauthorized access, and data leakage.

| Requirement | Description |
|-------------|-------------|
| PR-4.9.1 | Detect and block 16 common prompt injection patterns |
| PR-4.9.2 | Normalize Cyrillic homoglyphs and strip obfuscation characters |
| PR-4.9.3 | Apply regex-based suspicious pattern detection (10 patterns) |
| PR-4.9.4 | Enforce rate limiting (60 requests/60 seconds per IP) |
| PR-4.9.5 | Enforce request size limits (1 MB maximum) |
| PR-4.9.6 | Log all security-relevant events with audit trails |
| PR-4.9.7 | Execute file commands via SafeREPL (Python stdlib, no shell) |
| PR-4.9.8 | Sanitize all API error responses (no stack traces, no internal paths) |
| PR-4.9.9 | Enforce symlink-safe workspace boundary via `os.path.realpath()` + `os.sep` suffix matching |
| PR-4.9.10 | Enforce command denylist with shlex-based token matching (not substring) |
| PR-4.9.11 | Sanitize Docker env var values via `shlex.quote()` to prevent shell injection |
| PR-4.9.12 | Use atomic file writes (temp + `os.replace()`) for crash-safe registry and config persistence |
| PR-4.9.13 | Employ double-checked locking for thread-safe singleton initialization |
| PR-4.9.14 | Sanitize skill factory code generation to prevent docstring breakout injection |
| PR-4.9.15 | Validate workspace paths in all file operation providers before execution |

### 4.10 Onboarding

**Priority:** P1 (Important)

Guide new users through identity setup, authentication, and communications configuration.

| Requirement | Description |
|-------------|-------------|
| PR-4.10.1 | Create persistent user profile (USER.md) with identity bond |
| PR-4.10.2 | Support API key and OAuth (ADC) authentication modes |
| PR-4.10.3 | Configure Google Chat and/or Telegram webhook endpoints |
| PR-4.10.4 | Track onboarding state persistently with recovery commands |
| PR-4.10.5 | Expose onboarding status and recovery via REST API |

### 4.11 Feature Flags

**Priority:** P1 (Important)

Enable selective subsystem activation for deployment flexibility and fault isolation.

| Requirement | Description |
|-------------|-------------|
| PR-4.11.1 | Provide kill switches for Soul, Skills, Health Monitor, and Scheduler |
| PR-4.11.2 | Read flags from environment variables at startup (default: all enabled) |
| PR-4.11.3 | Support runtime flag reload without process restart |
| PR-4.11.4 | Log flag state at startup for operational visibility |
| PR-4.11.5 | Provide kill switches for Memory vNext and Tool Fabric subsystems |
| PR-4.11.6 | Support granular Tool Fabric flags: CLI providers, Antigravity, network, host execution |

### 4.12 Memory vNext (Tiered Memory)

**Priority:** P0 (Core)

Provide a commit-based, tiered memory system with governed self-edits and context compilation.

| Requirement | Description |
|-------------|-------------|
| PR-4.12.1 | Store core memory blocks (persona, human, mission, operating_rules, workspace_state) with schema validation |
| PR-4.12.2 | Support three memory tiers: working (short-term), episodic (session-based), archival (long-term) |
| PR-4.12.3 | Implement commit-based editing with begin/finish/rollback semantics and snapshot isolation |
| PR-4.12.4 | Compile context from memory tiers with configurable token budgets per block type |
| PR-4.12.5 | Provide governed self-edit operations: insert, update, delete, rethink with provenance tracking |
| PR-4.12.6 | Persist memory in SQLite with thread-safe connection management |
| PR-4.12.7 | Support full-text search across memory tiers with relevance scoring |
| PR-4.12.8 | Quarantine suspicious memory edits for owner review before application |
| PR-4.12.9 | Expose REST API for memory operations with authentication |
| PR-4.12.10 | Schedule automatic memory maintenance jobs (cleanup, archival promotion) |

### 4.13 Tool Fabric (Provider-Agnostic Tool Execution)

**Priority:** P0 (Core)

Provide a capability-based tool execution layer with provider routing, Docker sandboxing, and policy enforcement.

| Requirement | Description |
|-------------|-------------|
| PR-4.13.1 | Define 7 capability interfaces: ShellExec, RepoOps, FileOps, WebOps, UIBuilder, DeployOps, VisionControl |
| PR-4.13.2 | Route tool invocations to providers based on capability, health, and priority |
| PR-4.13.3 | Execute shell commands in Docker sandbox with output bounding and timeout enforcement |
| PR-4.13.4 | Enforce security policies: command denylist, path traversal detection, sensitive path blocking, network control |
| PR-4.13.5 | Generate ToolReceipt and VisionReceipt for every tool invocation with full audit trail |
| PR-4.13.6 | Support provider health monitoring with caching, TTL, and automatic failover |
| PR-4.13.7 | Provide template-based and AI-generative UI scaffolding via UIBuilder capability |
| PR-4.13.8 | Support vision-based UI control via VisionControl capability (Antigravity integration) |
| PR-4.13.9 | Enforce workspace boundary for all file operations with symlink resolution |
| PR-4.13.10 | Support safe mode toggle restricting providers to local sandbox only |

---

## 5. User Stories

| ID | As a... | I want to... | So that... |
|----|---------|--------------|------------|
| US-01 | DevOps Engineer | Ask Lancelot to audit a repository | It reads all relevant files into context and finds issues with full project awareness |
| US-02 | Developer | Have Lancelot fix a bug | It plans the fix, writes the code, verifies the change, and produces an audit trail |
| US-03 | Manager | Review Lancelot's actions | I can inspect receipts to see exactly what it did, which models it used, and why |
| US-04 | Developer | Add a new capability | I create a skill manifest and the factory generates a complete skeleton for approval |
| US-05 | Ops Engineer | Monitor system health | The health dashboard shows real-time status of all subsystems with degraded reasons |
| US-06 | Security Admin | Ensure PII is redacted | All sensitive data routes through the local redaction lane before reaching external APIs |
| US-07 | Owner | Modify agent behavior | I propose a soul amendment, review the diff, and activate it through the approval workflow |
| US-08 | DevOps Engineer | Automate recurring tasks | I define jobs in scheduler.yaml with cron expressions and approval requirements |
| US-09 | Developer | Reduce LLM costs | Routine tasks (classification, summarization) route to the local model, saving 75-90% on tokens |
| US-10 | Owner | Disable a subsystem | I set a feature flag to false and the system boots cleanly without that subsystem |
| US-11 | Developer | Switch LLM providers | I update models.yaml and the router uses the new provider without code changes |
| US-12 | Ops Engineer | Debug a failed scheduled job | I check the job receipt for skip_reason and gate details to identify the root cause |
| US-13 | Developer | Inspect Lancelot's memory | I view working/episodic/archival tiers and see what context the agent is operating with |
| US-14 | Owner | Review memory self-edits | I approve or quarantine proposed memory changes before they take effect |
| US-15 | Developer | Execute code in sandbox | I run commands through Tool Fabric which enforces security policies and produces audit receipts |
| US-16 | Ops Engineer | Add a new tool provider | I register a new provider that implements a capability interface and it auto-routes via priority |

---

## 6. Non-Functional Requirements

### 6.1 Performance

| ID | Requirement |
|----|-------------|
| NFR-01 | Planning steps complete within the provider's standard response time |
| NFR-02 | Local model utility tasks respond within 2 seconds for standard inputs |
| NFR-03 | Health endpoints respond within 100ms |
| NFR-04 | War Room dashboard loads within 3 seconds |

### 6.2 Reliability

| ID | Requirement |
|----|-------------|
| NFR-05 | System starts cleanly with any combination of feature flags disabled |
| NFR-06 | Failed skills do not crash the scheduler or orchestrator |
| NFR-07 | Health monitor continues operating when individual checks fail |
| NFR-08 | Gate exceptions produce skip receipts rather than unhandled errors |
| NFR-09 | SQLite database handles concurrent job state updates safely |

### 6.3 Security

| ID | Requirement |
|----|-------------|
| NFR-10 | No API endpoint leaks stack traces or internal file paths |
| NFR-11 | All secrets stored in .env or encrypted vault (never committed) |
| NFR-12 | Input sanitization blocks prompt injection before LLM processing |
| NFR-13 | Bearer token authentication required for soul modifications |
| NFR-14 | Marketplace skills restricted to safe permission set by default |

### 6.4 Maintainability

| ID | Requirement |
|----|-------------|
| NFR-15 | All subsystems follow single-owner module pattern (one module, one responsibility) |
| NFR-16 | Configuration changes via YAML files without code modifications |
| NFR-17 | Operational runbooks available for each subsystem |
| NFR-18 | Test coverage for all public APIs with regression tests for hardening |

### 6.5 Deployment

| ID | Requirement |
|----|-------------|
| NFR-19 | Deployable via Docker Compose with two services (core + local-llm) |
| NFR-20 | Persistent data survives container restarts via volume mounts |
| NFR-21 | Local LLM service includes health check for dependency management |
| NFR-22 | Environment-variable-based configuration for all deployment parameters |

### 6.6 Privacy

| ID | Requirement |
|----|-------------|
| NFR-23 | All data stays local (Docker volume) or is sent only to configured LLM endpoints |
| NFR-24 | PII redaction via local model before data reaches external providers |
| NFR-25 | Soul memory ethics enforce PII consent, data redaction, and soul exclusion from recursive memory |

---

## 7. System Constraints

| Constraint | Description |
|------------|-------------|
| Single Owner | System serves exactly one owner. Multi-tenancy is not supported. |
| Docker Required | Production deployment requires Docker and Docker Compose. |
| Python 3.11+ | Runtime requires Python 3.11 or later. |
| Context Window | Maximum context budget is 128k tokens (configurable). |
| Local Model | Local GGUF model required for redaction and utility tasks. Flagship providers alone are insufficient. |
| Soul Immutability | Soul document cannot be modified without the full proposal/approval/activation workflow. |

---

## 8. Dependencies

| Dependency | Purpose | Version |
|------------|---------|---------|
| google-genai | Gemini 2.0 API integration | >= 1.0.0 |
| fastapi | REST API framework | latest |
| uvicorn | ASGI server | latest |
| streamlit | War Room dashboard | latest |
| pydantic | Data validation and settings | v2 |
| pyyaml | Configuration parsing | >= 6.0 |
| llama-cpp-python | Local GGUF model inference | >= 0.2.0 |
| chromadb | Vector database (legacy, being phased out) | latest |
| cryptography | Vault encryption | latest |
| pywebview | Native desktop launcher | latest |
| plyer | Desktop notifications | latest |
| pytest | Test framework | >= 7.0 |

---

## 9. Release History

| Version | Release | Scope |
|---------|---------|-------|
| v4.0 | Multi-Provider Upgrade | Lane-based routing, 3 flagship providers, control plane, usage tracking |
| v5.0 (vNext2) | Soul + Skills + Heartbeat + Scheduler | Constitutional governance, modular skills, health monitoring, job scheduling, feature flags |
| v6.0 | Current | All v4 + vNext2 features integrated and hardened with regression tests |
| v7.0 | Memory vNext + Tool Fabric + Security Hardening | Tiered memory with commit-based editing, provider-agnostic tool execution, 96 security vulnerabilities remediated |

---

## 10. Glossary

| Term | Definition |
|------|-----------|
| **Soul** | Versioned YAML document defining Lancelot's constitutional identity, behavioral boundaries, and ethical constraints |
| **Skill** | A modular, permissioned capability defined by a declarative manifest and executable module |
| **Lane** | A prioritized routing path that maps task types to specific model providers |
| **Receipt** | An immutable audit record generated for every discrete action taken by the system |
| **Gate** | A configurable check function that must pass before a scheduled job can execute |
| **Heartbeat** | The health monitoring subsystem providing liveness and readiness probes |
| **War Room** | The Streamlit-based control dashboard for system monitoring and management |
| **Crusader Mode** | A high-agency autonomy mode that increases tool autonomy while respecting Soul constraints |
| **GGUF** | A quantized model format used by llama-cpp-python for local inference |
| **HealthSnapshot** | A point-in-time data structure reporting the health state of all subsystems |
| **Memory vNext** | The tiered memory subsystem providing working, episodic, and archival storage with commit-based governed self-edits |
| **Tool Fabric** | The provider-agnostic tool execution subsystem with capability routing, Docker sandboxing, and policy enforcement |
| **Core Block** | A fundamental memory element (persona, human, mission, operating_rules, workspace_state) stored in the block store |
| **Context Compiler** | Component that assembles memory tiers into a token-budgeted context window for LLM prompts |
| **PolicySnapshot** | A frozen record of the security policy state at the time of a tool invocation, included in receipts |
| **Capability** | A typed interface (protocol) defining a category of tool operations (e.g., ShellExec, FileOps) |
| **Provider** | An implementation of one or more capabilities that executes tool operations (e.g., LocalSandboxProvider) |
