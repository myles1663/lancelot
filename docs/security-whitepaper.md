# Project Lancelot: Security Whitepaper

## Comprehensive Security Assessment of a Governed Autonomous AI System

**Document Version:** 1.2
**Assessment Date:** February 21, 2026
**System Version:** v7.4 (v0.2.24)
**Classification:** Internal -- Stakeholder Distribution
**Author:** Myles Russell Hamilton

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [System Architecture Overview](#2-system-architecture-overview)
3. [Threat Model (STRIDE Analysis)](#3-threat-model-stride-analysis)
4. [Security Controls Inventory](#4-security-controls-inventory)
5. [Risk Assessment](#5-risk-assessment)
6. [Remediation Roadmap](#6-remediation-roadmap)
7. [Compliance Considerations](#7-compliance-considerations)
8. [Appendices](#8-appendices)

---

## 1. Executive Summary

Project Lancelot is a **Governed Autonomous System (GAS)** -- an AI agent that executes real-world actions (shell commands, network requests, file operations, message delivery) under constitutional governance constraints. Unlike a chatbot, Lancelot operates autonomously within a framework of policy enforcement, risk-tiered approval gates, and cryptographic audit trails. This distinction is critical: the security surface area extends far beyond typical web application concerns into autonomous code execution, secret management, and LLM prompt integrity.

### 1.1 Scope

This assessment covers all security-relevant code paths in Lancelot v7.4 (v0.2.24), including:

- **Authentication and authorization** across all API, WebSocket, and Telegram interfaces
- **Input validation and prompt injection defense** at the gateway and orchestrator layers
- **Network security** including SSRF protection, CORS configuration, and container networking
- **Secret management** including vault encryption, API key storage, and OAuth token handling
- **Tool execution sandboxing** via Docker containers and the PolicyEngine
- **Skill security pipeline** including the 6-stage installation gate and runtime loading
- **Memory protection** including write gates, quarantine, and provenance tracking
- **Container and infrastructure hardening** including Docker configuration and dependency management
- **Audit and observability** including receipt-based accountability and structured logging

### 1.2 Overall Security Posture

Lancelot exhibits a **mature defense-in-depth architecture** with strong containment primitives. The system implements multiple overlapping security layers: bearer token authentication, input sanitization with anti-obfuscation normalization, SSRF-aware network interception, policy-gated tool execution with Docker sandboxing, a 6-stage skill security pipeline, quarantine-by-default memory editing, and a comprehensive receipt-based audit trail.

The initial assessment identified 15 findings. Since publication, 10 findings have been remediated across v0.2.23 and v0.2.24. v0.2.23 addressed 5 high/medium findings (F-002, F-003, F-004, F-005, F-009). v0.2.24 addressed 5 additional findings (F-008, F-010, F-011, F-012, F-015) covering scheduler approval, network config, audit integrity, rate limiter memory, and file locking. The 5 remaining open findings (1 Critical, 2 Medium, 2 Low) relate to container isolation, encryption at rest, and skill sandboxing -- areas that should be addressed before any multi-user or public-facing deployment.

### 1.3 Findings Summary

| Severity | Open | Resolved | Description |
|----------|------|----------|-------------|
| **Critical** | 1 | 0 | Container escape vector via Docker socket access |
| **High** | 0 | 3 | ~~Auth bypass (F-002), WebSocket URL token (F-003), CORS wildcards (F-004)~~ — resolved v0.2.23 |
| **Medium** | 2 | 3 | Open: unencrypted data at rest (F-006), skill code loading (F-007). ~~Resolved: security headers (F-005), scheduler approval (F-008), OAuth env (F-009)~~ |
| **Low** | 0 | 3 | ~~Network allowlist (F-010), audit log integrity (F-011), rate limiter memory (F-012)~~ — resolved v0.2.24 |
| **Informational** | 2 | 1 | Open: vault key auto-generation (F-013), host execution flag (F-014). ~~Resolved: usage file locking (F-015)~~ — v0.2.24 |
| **Total** | **5** | **10** | |

### 1.4 Key Strengths

- Constitutional governance model (Soul) with versioned, linted, owner-gated amendments
- Receipt-based audit trail covering all action types with SHA-256 hashing
- Fernet-encrypted secret vault with access policies and audit logging
- SSRF protection with private IP blocking and fail-closed DNS resolution
- Docker sandbox execution with memory limits, network isolation, and output bounding
- 6-stage skill security pipeline with owner review gate
- Quarantine-by-default memory editing with provenance tracking
- Non-root container execution with gosu privilege dropping

---

## 2. System Architecture Overview

### 2.1 Service Topology

```
                    Internet / User
                         |
                    [Port 8000]
                         |
              +---------------------+
              |   lancelot-core     |
              |  (FastAPI + Uvicorn)|
              |                     |       +-------------------+
              |  Gateway Layer      |       |   local-llm       |
              |  Orchestrator       |<----->|  (llama-cpp-python)|
              |  Soul / Memory      |  :8080|  [Port 8080]      |
              |  Skills / Scheduler |       +-------------------+
              |  Tool Fabric        |
              +---------------------+
                    |           |
            [lancelot_data] [lancelot_workspace]
             (named vol)     (named vol)
```

Both services run on the `lancelot_net` Docker bridge network. External access is via port 8000 (FastAPI gateway) only. The local LLM service on port 8080 is internal to the Docker network.

### 2.2 Security Boundary Model

The system operates across four trust zones, each with distinct security controls:

```
+------------------------------------------------------------------+
|  ZONE 1: UNTRUSTED                                               |
|  - User input (Telegram, War Room, API)                          |
|  - LLM provider responses                                       |
|  - External RSS/API data                                         |
+-------------------------------+----------------------------------+
                                |
                    [Rate Limiter: 60 req/min per IP]
                    [Request Size: 20 MB max]
                    [Bearer Token HMAC-SHA256]
                                |
+-------------------------------+----------------------------------+
|  ZONE 2: GATEWAY                                                 |
|  File: src/core/gateway.py                                       |
|  Controls: Auth, CORS, rate limiting, subsystem gates            |
+-------------------------------+----------------------------------+
                                |
                    [InputSanitizer: 16 phrases, 10 patterns]
                    [NetworkInterceptor: allowlist + SSRF block]
                    [CognitionGovernor: 2M tokens/day, 1K calls/day]
                                |
+-------------------------------+----------------------------------+
|  ZONE 3: APPLICATION                                             |
|  Files: orchestrator.py, security.py, soul/, memory/, skills/    |
|  Controls: Prompt sanitization, governance, memory gates,        |
|            skill pipeline, receipt logging                        |
+-------------------------------+----------------------------------+
                                |
                    [PolicyEngine: command denylist, path checks]
                    [Docker sandbox: 512MB, no network, 300s timeout]
                    [Workspace boundary enforcement]
                                |
+-------------------------------+----------------------------------+
|  ZONE 4: EXECUTION                                               |
|  Files: tools/providers/local_sandbox.py, tools/policies.py      |
|  Controls: Sandboxed containers, output bounding, non-root user  |
+------------------------------------------------------------------+
```

### 2.3 Docker Configuration

| Component | Value |
|-----------|-------|
| Base image | `python:3.11-slim` |
| Application user | `lancelot` (non-root, UID auto-assigned) |
| Privilege model | Root entrypoint with `gosu` drop to `lancelot` |
| Data volumes | `lancelot_data` (named), `lancelot_workspace` (named) |
| Network | `lancelot_net` bridge (inter-service only) |
| Exposed ports | 8000 (gateway) |
| Docker socket | Mounted at `/var/run/docker.sock` for sandbox provider |

---

## 3. Threat Model (STRIDE Analysis)

### 3.1 Spoofing

| Attack Surface | Threat | Existing Control | Residual Risk |
|----------------|--------|------------------|---------------|
| API Gateway | Attacker impersonates valid user | HMAC-SHA256 bearer token via `hmac.compare_digest()` (`gateway.py:129`). Fail-closed: requires explicit `LANCELOT_DEV_MODE=true` for dev bypass (F-002 resolved in v0.2.23) | Single static token; no rotation or MFA |
| Soul API | Unauthorized soul amendment | Owner token validation (`LANCELOT_OWNER_TOKEN`) | Single static token; no rotation or MFA |
| WebSocket `/ws/warroom` | Session hijacking | First-message auth handshake with HMAC token validation (F-003 resolved in v0.2.23). Legacy query param deprecated. | 10-second auth timeout window |
| Telegram Bot | Impersonation | Bot token + configured chat_id | No cryptographic verification on long-polling responses |
| OAuth Flow | Authorization code interception | PKCE with code_verifier/challenge (`oauth_token_manager.py`) | State parameter validated but no browser session binding |

### 3.2 Tampering

| Attack Surface | Threat | Existing Control | Residual Risk |
|----------------|--------|------------------|---------------|
| Memory system | Agent self-modifies core blocks | WriteGateValidator with block allowlist, quarantine-by-default | Provenance tracks origin but doesn't cryptographically verify it |
| Soul constitution | Unauthorized rule change | Versioned YAML, lint-or-raise validation, owner-only API | Relies on single bearer token |
| Skill code | Malicious code injection | 6-stage pipeline: manifest validation, static analysis, sandbox test, owner review, capability enforcement, trust initialization | `exec_module()` runs in main process without runtime sandboxing (`executor.py:196`) |
| Scheduler jobs | Unauthorized job creation | Parameterized SQL, per-job locking | No authentication on `create_job()` internal API |

### 3.3 Repudiation

| Attack Surface | Threat | Existing Control | Residual Risk |
|----------------|--------|------------------|---------------|
| All actions | Deny performing action | Receipt system with UUID, timestamp, SHA-256 hash, cognition tier. Audit logs are hash-chained (F-011 resolved in v0.2.24) | Receipts stored in file-based JSON; hash chain only covers audit.log, not receipt files |
| Memory edits | Deny modifying memory | Commit-based transactions with provenance | Provenance relies on self-reported identity |
| Tool execution | Deny running command | ToolReceipt with command hash, policy snapshot, file change hashes | Receipts stored in file-based JSON; no integrity guarantee |

### 3.4 Information Disclosure

| Attack Surface | Threat | Existing Control | Residual Risk |
|----------------|--------|------------------|---------------|
| API responses | Stack trace / path leak | Structured error responses with generic messages (`gateway.py:46-52`) | Consistent across all endpoints |
| Secret vault | Key material exposure | Fernet encryption (AES-128-CBC + HMAC-SHA256) | SQLite databases, chat logs, audit logs are NOT encrypted at rest |
| Health endpoints | System reconnaissance | `/health` and `/health/ready` return component status | Limited to version, uptime, and component state -- acceptable for monitoring |
| OAuth tokens | Token exposure | Stored in vault with Fernet encryption. In-memory cache replaces `os.environ` storage (F-009 resolved in v0.2.23) | Token accessible to in-process code via getter function |
| LLM API calls | PII exfiltration to providers | Sensitive pattern redaction in policies | Redaction is heuristic-based; may miss novel patterns |

### 3.5 Denial of Service

| Attack Surface | Threat | Existing Control | Residual Risk |
|----------------|--------|------------------|---------------|
| API Gateway | Request flooding | RateLimiter: 60/60s per IP with periodic stale IP cleanup (F-012 resolved in v0.2.24) | In-memory state; no distributed limiting |
| LLM costs | Runaway token consumption | CognitionGovernor: 2M tokens/day, 1000 tool calls/day, thread-safe file I/O with atomic writes (F-015 resolved in v0.2.24) | Single-process only; no multi-instance coordination |
| Docker sandbox | Resource exhaustion | 512MB memory, 1 CPU, 300s timeout | No disk I/O limits on sandbox containers |
| Scheduler | Job spam | Per-job locking prevents concurrent execution | No rate limit on job creation |

### 3.6 Elevation of Privilege

| Attack Surface | Threat | Existing Control | Residual Risk |
|----------------|--------|------------------|---------------|
| Container escape | Breakout via Docker socket | Non-root user, gosu privilege drop | Docker socket mount + docker group membership enables sibling container creation with arbitrary capabilities |
| Prompt injection | Override governance via LLM | InputSanitizer: 16 banned phrases, 10 regex patterns, homoglyph normalization, zero-width stripping | Semantic prompt injection (context manipulation without keyword triggers) may bypass pattern detection |
| Skill escalation | Skill acquires elevated permissions | Marketplace restriction to low-risk permissions; owner review gate | User-installed skills run via `exec_module()` in main process |
| Feature flags | Enable dangerous subsystems | `FEATURE_TOOLS_HOST_EXECUTION` default `false`, clearly documented as dangerous | If enabled, allows host-level command execution |

---

## 4. Security Controls Inventory

### 4.1 Authentication and Authorization

**Primary mechanism:** Bearer token authentication via HMAC-SHA256 constant-time comparison.

```
File: src/core/gateway.py (lines 116-130)

Token source: LANCELOT_API_TOKEN environment variable
Comparison: hmac.compare_digest() — timing-safe
Protected: /chat, /chat/upload, /mfa_submit, /receipt/*, /forge/*, /ucp/*,
           /crusader_status, /api/crusader/*
```

**Owner token:** Separate `LANCELOT_OWNER_TOKEN` for administrative operations:
- Soul amendments (`src/core/soul/api.py`)
- Memory quarantine approval (`src/core/memory/api.py`)
- File download endpoint (`/api/files/`)

**OAuth 2.0 PKCE:** For Anthropic provider integration (`src/core/oauth_token_manager.py`):
- Code verifier (128-byte random) + SHA-256 code challenge
- State parameter with 600-second TTL
- Automatic token refresh 10 minutes before expiry
- Token storage in Fernet-encrypted vault

**Rate limiting:** Sliding-window per IP address (`gateway.py:55-72`):
- Default: 60 requests per 60 seconds
- In-memory dictionary of timestamps per IP
- Applied to `/chat` and `/chat/upload` endpoints

### 4.2 Input Validation and Sanitization

**InputSanitizer** (`src/core/security.py:9-97`):

| Defense Layer | Implementation |
|---------------|----------------|
| Banned phrases | 16 phrases: "ignore previous rules", "system prompt", "bypass security", "jailbreak", "DAN", etc. Case-insensitive replacement with `[REDACTED]` |
| Regex patterns | 10 patterns: instruction override (`ignore\s+previous\s+instructions`), role injection (`you\s+are\s+now`), prompt delimiters (`[INST]`, `<\|im_start\|>`) |
| Zero-width stripping | Removes U+200B, U+200C, U+200D, U+FEFF |
| Homoglyph normalization | Cyrillic to Latin mapping: a(U+0430), e(U+0435), o(U+043E), c(U+0441), p(U+0440) |
| URL decoding | `urllib.parse.unquote()` to defeat percent-encoding |
| Space normalization | Collapses multiple spaces to single |

**Processing order:** Normalize first, then banned phrase replacement, then suspicious pattern flagging with `[SUSPICIOUS INPUT DETECTED]` prefix.

**CognitionGovernor** (`src/core/security.py:238-294`):

| Limit | Value | Purpose |
|-------|-------|---------|
| Daily tokens | 2,000,000 | Prevent runaway LLM costs |
| Daily tool calls | 1,000 | Prevent infinite loops |
| Actions per minute | 60 | Throttle burst activity |

Counters are persisted to `data/usage_stats.json` and reset daily. Check and log operations are atomic per call but the file lacks OS-level locking.

**Request size limit:** 20 MB maximum (`gateway.py:35`), enforced on `/chat` and `/chat/upload` endpoints.

### 4.3 Network Security

**NetworkInterceptor** (`src/core/security.py:129-236`):

SSRF protection via multi-layer validation:

1. **URL credential stripping:** Removes `user:pass@` from URLs before processing (`lines 193-204`)
2. **Domain allowlist:** Core domains (localhost, 127.0.0.1, api.projectlancelot.dev, ghcr.io) plus configurable domains from `config/network_allowlist.yaml`
3. **Private IP blocking:** RFC 1918 and reserved ranges:
   - `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` (private)
   - `127.0.0.0/8` (loopback), `169.254.0.0/16` (link-local), `0.0.0.0/8` (reserved)
4. **DNS resolution:** Resolves hostnames and checks resolved IP against blocked ranges
5. **Fail-closed:** If DNS resolution fails, the request is blocked

**Subdomain matching:** `hostname.endswith("." + allowed)` allows `api.telegram.org` when `telegram.org` is allowed.

**CORS** (`gateway.py:77-84`):
- Origins: configurable via `ALLOWED_ORIGINS` env var (default: `localhost:8501`, `localhost:5173`)
- Methods: `["GET", "POST", "DELETE", "PATCH", "OPTIONS"]` (restricted in v0.2.23, F-004 resolved)
- Headers: `["Authorization", "Content-Type"]` (restricted in v0.2.23, F-004 resolved)

**Docker network isolation:**
- Services communicate via `lancelot_net` bridge network
- Sandbox containers run with `--network=none` by default
- Local LLM accessible only from within the Docker network

### 4.4 Secret Management

**SecretVault** (`src/connectors/vault.py`):

| Property | Implementation |
|----------|----------------|
| Algorithm | Fernet (AES-128-CBC + HMAC-SHA256) via `cryptography` library |
| Key priority | 1. `LANCELOT_VAULT_KEY` env var, 2. Existing `vault.key` file, 3. Auto-generate |
| Storage | `data/vault/credentials.enc` (encrypted), with `.bak` backup |
| Access control | `VaultAccessPolicy` with per-connector grants |
| Audit | Every store/retrieve/delete logged with timestamp, accessor, action |
| List safety | `list_secrets()` returns names only; values never exposed |

**Environment variables:** API keys and tokens stored in `.env` file:
- Loaded via `env_file` directive in `docker-compose.yml`
- `.env` is `.gitignored` and has never been committed to the repository
- Installer generates unique tokens during initial setup

**Sensitive data redaction** (`src/tools/policies.py`):

Patterns redacted from tool receipts and logs:
- `password`, `api_key`, `secret`, `token`, `bearer`
- `sk-[a-zA-Z0-9]+` (OpenAI keys)
- `ghp_*`, `gho_*` (GitHub tokens)
- `AKIA[A-Z0-9]{16}` (AWS access keys)

### 4.5 Tool Execution Sandboxing

**PolicyEngine** (`src/tools/policies.py`):

Command evaluation pipeline:
1. **Denylist check** (shlex tokenization, not substring matching):
   - `rm -rf /`, `rm -rf /*`, `rm -rf .`, `rm -rf ..`
   - `mkfs`, `fdisk`, `dd if=/dev/zero`
   - Fork bomb: `:(){:|:&};:`
   - `chmod -R 777 /`, `chown -R`, `sudo`, `su -`
   - `nc -l`, `ncat -l` (reverse shells)
   - Credential access: `cat /etc/passwd`, `cat ~/.ssh/id_rsa`
   - `insmod`, `rmmod`, `modprobe`

2. **Allowlist check**: 50+ safe commands including `git`, `python`, `pip`, `npm`, `ls`, `grep`, `curl`, `docker`, etc.

3. **Risk classification**:
   - **LOW:** Read, list, status operations
   - **MEDIUM:** Modify, install, test operations
   - **HIGH:** Network-enabled, deploy, delete, credential operations

4. **Path traversal detection**:
   - Normalized `..` depth tracking
   - Encoded `%2e%2e` and double-encoded `%252e` detection
   - Symlink rejection outside workspace boundary
   - Workspace boundary: `os.path.realpath()` + `os.sep` suffix matching

5. **Sensitive path blocking**: `.env`, `.ssh`, `.aws`, `.gnupg`, `credentials`, `secrets.yaml` patterns

**Docker Sandbox** (`src/tools/providers/local_sandbox.py`):

| Parameter | Value |
|-----------|-------|
| Base image | `python:3.11-slim` |
| Memory limit | 512 MB |
| CPU limit | 1 core |
| Timeout | 300 seconds (+ 5s Docker overhead) |
| Network | Disabled by default (`--network=none`) |
| Max stdout | 100,000 characters |
| Max stderr | 50,000 characters |
| User | Non-root |

### 4.6 Skill Security Pipeline

Six-stage security gate for skill installation:

```
Stage 1: Manifest Validation
    |-- Pydantic schema: name (snake_case), version, description, risk, permissions
    |-- Required fields enforced
    |
Stage 2: Static Analysis
    |-- Scans for dangerous patterns: eval(), exec(), __import__(), os.system(), ctypes
    |-- Checks for hardcoded credentials
    |-- Flags file operations outside workspace
    |
Stage 3: Sandbox Test Execution
    |-- Runs skill in isolated Docker context
    |-- Monitors for policy violations
    |-- Tracks file I/O, network, shell operations
    |
Stage 4: Owner Review
    |-- Proposal workflow with approval gate
    |-- Owner must explicitly approve installation
    |
Stage 5: Capability Enforcement
    |-- Registers skill with approved capability set
    |-- Marketplace skills restricted to: read_input, write_output, read_config
    |
Stage 6: Trust Initialization
    |-- Signature state tracking: UNSIGNED, SIGNED, VERIFIED
    |-- Trust ledger entry created
```

**Risk tiers** for skill governance:

| Tier | Level | Approval Required | Example Permissions |
|------|-------|-------------------|---------------------|
| T0 | INERT | None | `fs.read` |
| T1 | REVERSIBLE | Single approval | `fs.write` |
| T2 | CONTROLLED | Approval + verification | `shell.exec` |
| T3 | IRREVERSIBLE | Approval + dual verification | `net.post` |

**Runtime concern:** After passing the 6-stage gate, skills are loaded via `importlib.util.spec_from_file_location()` + `exec_module()` (`executor.py:196`). This runs skill code in the main application process. Path escape is prevented (`executor.py:161-169`), and unsigned skills generate a security warning, but there is no runtime sandboxing of the executed code. See Finding F-007.

### 4.7 Memory Protection

**WriteGateValidator** (`src/core/memory/gates.py`):

| Gate | Purpose |
|------|---------|
| Block allowlist | Agents restricted to `mission` and `workspace_state` blocks |
| Owner-only blocks | `persona`, `human`, `operating_rules` require owner provenance |
| Secret scrubbing | Regex patterns detect API keys, tokens, credentials before storage |
| Confidence scoring | Per-edit confidence tracked; low-confidence edits flagged |
| Quarantine | Agent edits quarantined by default; require owner approval via `/memory/quarantine/{id}/approve` (Bearer auth) |

**CommitManager** (`src/core/memory/commits.py`):

| Feature | Implementation |
|---------|----------------|
| Atomic transactions | Snapshot isolation with rollback support |
| Snapshot retention | MAX_RETAINED_SNAPSHOTS = 50 with LRU eviction |
| Rollback | Per-item undo log for insert/update/delete recovery |
| Provenance | Every edit records origin type, timestamp, and approval chain |

### 4.8 Audit and Observability

**AuditLogger** (`src/core/security.py:99-127`):
- Commands logged with SHA-256 hash, user attribution, and ISO 8601 timestamp
- Events logged with type, user, detail hash, and structured details
- Log path: `/home/lancelot/data/audit.log`

**Receipt System** (`src/shared/receipts.py`):

| Field | Purpose |
|-------|---------|
| `id` | UUID for unique identification |
| `timestamp` | ISO 8601 with timezone |
| `action_type` | TOOL_CALL, LLM_CALL, FILE_OP, ENV_QUERY, PLAN_STEP, VERIFICATION, USER_INTERACTION, SYSTEM |
| `action_name` | Specific action identifier |
| `inputs` / `outputs` | Structured data (redacted of secrets) |
| `status` | PENDING, SUCCESS, FAILURE, CANCELLED |
| `duration_ms` | Execution time tracking |
| `token_count` | LLM token usage |
| `tier` | Cognition tier: DETERMINISTIC, CLASSIFICATION, PLANNING, SYNTHESIS |
| `parent_id` / `quest_id` | Hierarchical action linking |

**ToolReceipt** (`src/tools/receipts.py`):
- Extended with: capability, provider ID, policy snapshot, risk level, file change hashes (before/after)
- Enables post-hoc verification of tool execution claims

**Sentry** (`src/core/security.py:297-348`):
- Human-in-the-loop permission system with persistent approval whitelist
- Approval signatures: SHA-256 hash of action type + metadata
- Persisted to `data/sentry_whitelist.json`

---

## 5. Risk Assessment

### F-001: Docker Socket Access Enables Container Escape

| Property | Value |
|----------|-------|
| **Severity** | Critical |
| **CVSS Vector** | AV:L/AC:L/PR:L/UI:N/S:C/C:H/I:H/A:H |
| **CVSS Score** | 8.8 |

**Description:** The `docker-compose.yml` (line 10) mounts the Docker socket at `/var/run/docker.sock`, and the `lancelot` user is added to the `docker` group (`Dockerfile:62`). The sandbox provider (`src/tools/providers/local_sandbox.py`) uses this to spawn sibling containers via `docker run`. However, Docker socket access grants the ability to create containers with arbitrary capabilities, including mounting the host filesystem.

**Evidence:**
- `docker-compose.yml:10`: `- /var/run/docker.sock:/var/run/docker.sock`
- `docker-compose.yml:13-14`: `group_add: - "0"` (root group for socket access)
- `Dockerfile:62`: `RUN groupadd docker 2>/dev/null; usermod -aG docker lancelot`

**Recommendation:** Evaluate alternatives: (a) Sysbox runtime for rootless nested containers, (b) gVisor runsc for sandboxing, (c) a dedicated Docker-in-Docker sidecar with a restricted API proxy that only allows specific `docker run` invocations.

---

### F-002: Dev Mode Authentication Bypass -- RESOLVED (v0.2.23)

| Property | Value |
|----------|-------|
| **Severity** | ~~High~~ Resolved |
| **CVSS Vector** | AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:N |
| **CVSS Score** | 7.5 |
| **Status** | **RESOLVED in v0.2.23** |

**Description:** When `LANCELOT_API_TOKEN` is not set, `verify_token()` previously returned `True` for all requests. This was intended for local development but created a risk if the system was deployed without configuring a token.

**Remediation (v0.2.23):** `verify_token()` now fails closed. Dev mode bypass requires an explicit `LANCELOT_DEV_MODE=true` environment variable. If neither `LANCELOT_API_TOKEN` nor `LANCELOT_DEV_MODE` is set, all requests are rejected except health endpoints. The function now reads both `LANCELOT_API_TOKEN` and `LANCELOT_DEV_MODE` at module load and logs an error when no token is configured and dev mode is not explicitly enabled.

---

### F-003: WebSocket Authentication via URL Query Parameter -- RESOLVED (v0.2.23)

| Property | Value |
|----------|-------|
| **Severity** | ~~High~~ Resolved |
| **CVSS Vector** | AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:N/A:N |
| **CVSS Score** | 5.7 |
| **Status** | **RESOLVED in v0.2.23** |

**Description:** The WebSocket endpoint previously accepted authentication tokens via URL query parameter. Tokens in URLs are visible in server access logs, reverse proxy logs, browser history, and HTTP Referer headers.

**Remediation (v0.2.23):** Implemented first-message auth handshake protocol in `warroom_ws.py`. The client must send `{"type": "auth", "token": "<bearer_token>"}` as the first message after connection. The server validates with HMAC-SHA256 and responds with `{"type": "auth_ok"}` or closes the connection with code 4401. A 10-second auth timeout prevents hanging unauthenticated connections. The React `useWebSocket.ts` hook was updated to send the auth message on `ws.onopen`. Legacy query parameter auth is still accepted but logged as deprecated with a security warning.

---

### F-004: CORS Configuration Too Permissive -- RESOLVED (v0.2.23)

| Property | Value |
|----------|-------|
| **Severity** | ~~High~~ Resolved |
| **CVSS Vector** | AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:L/A:N |
| **CVSS Score** | 6.1 |
| **Status** | **RESOLVED in v0.2.23** |

**Description:** CORS middleware previously used `allow_methods=["*"]` and `allow_headers=["*"]`. While origins were restricted, the wildcard methods and headers allowed any HTTP method and any custom headers from allowed origins, expanding the attack surface.

**Remediation (v0.2.23):** CORS middleware now uses explicit lists:
- `allow_methods=["GET", "POST", "DELETE", "PATCH", "OPTIONS"]`
- `allow_headers=["Authorization", "Content-Type"]`

This restricts cross-origin requests to only the methods and headers actually used by the application.

---

### F-005: No HTTP Security Headers -- RESOLVED (v0.2.23)

| Property | Value |
|----------|-------|
| **Severity** | ~~Medium~~ Resolved |
| **CVSS Vector** | AV:N/AC:L/PR:N/UI:R/S:U/C:L/I:L/A:N |
| **CVSS Score** | 4.3 |
| **Status** | **RESOLVED in v0.2.23** |

**Description:** The FastAPI application previously did not set standard security headers.

**Remediation (v0.2.23):** Added `security_headers_middleware` to the FastAPI application in `gateway.py`. All HTTP responses now include:
- `X-Frame-Options: DENY` -- prevents clickjacking
- `X-Content-Type-Options: nosniff` -- prevents MIME type sniffing
- `Referrer-Policy: strict-origin-when-cross-origin` -- limits referrer leakage
- `Permissions-Policy: camera=(), microphone=(), geolocation=()` -- restricts browser APIs

Note: `Content-Security-Policy` and `Strict-Transport-Security` are deferred until HTTPS reverse proxy deployment.

---

### F-006: Unencrypted Data at Rest

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **CVSS Vector** | AV:L/AC:L/PR:L/UI:N/S:U/C:H/I:N/A:N |
| **CVSS Score** | 5.5 |

**Description:** While the SecretVault encrypts credentials with Fernet, the following data stores are unencrypted:
- `scheduler.sqlite` -- Job definitions and execution history
- `memory.sqlite` -- Tiered memory items
- `chat_log.json` -- Conversation history
- `audit.log` -- Security event log
- `receipts/` -- Action receipts with input/output data

Docker volumes (`lancelot_data`, `lancelot_workspace`) use the default unencrypted driver.

**Evidence:** `docker-compose.yml:78-82` defines named volumes without encryption configuration. SQLite files are created without encryption extensions.

**Recommendation:** For sensitive deployments: (a) use SQLCipher for SQLite databases, (b) enable LUKS or similar filesystem encryption on the Docker volume host, (c) evaluate encrypted Docker volume plugins.

---

### F-007: Dynamic Skill Code Loading Without Runtime Sandboxing

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **CVSS Vector** | AV:L/AC:H/PR:H/UI:R/S:U/C:H/I:H/A:H |
| **CVSS Score** | 6.1 |

**Description:** The skill executor uses `importlib.util.spec_from_file_location()` + `spec.loader.exec_module()` to load and execute arbitrary Python code in the main application process. While the 6-stage security pipeline gates installation, once installed, skills run with full process privileges -- access to the filesystem, network, and all application state.

**Evidence:**
```python
# executor.py:192-198
def _load_module_execute(self, path: Path, skill_name: str) -> SkillExecuteFunc:
    module_name = f"skill_{skill_name}"
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    ...
```

Path escape is validated (`executor.py:161-169`), and unsigned skills generate a security warning (`executor.py:172-184`), but there is no runtime isolation.

**Recommendation:** Execute non-builtin skills in a subprocess or Docker sandbox container. At minimum, consider `RestrictedPython` or AST-level restrictions for loaded skill code. Built-in skills (trusted, part of the codebase) can continue running in-process.

---

### F-008: Scheduler Approval Flow Auto-Skipped -- RESOLVED (v0.2.24)

| Property | Value |
|----------|-------|
| **Severity** | ~~Medium~~ Resolved |
| **CVSS Vector** | AV:L/AC:L/PR:L/UI:N/S:U/C:N/I:L/A:N |
| **CVSS Score** | 3.3 |
| **Status** | **RESOLVED in v0.2.24** |

**Description:** The scheduler executor had a `requires_approvals` field in its job schema, but the approval mechanism was not wired up. Jobs with approval requirements were skipped with no way to grant approval.

**Remediation (v0.2.24):** Implemented full approval workflow in `scheduler/executor.py`. Jobs with `requires_approvals` now emit a `scheduler_approval_required` event to the War Room WebSocket and are tracked in a pending approvals dict. Added `approve_job(job_id)` method that grants approval — the job executes on the next scheduler tick. New API endpoints: `POST /api/scheduler/jobs/{id}/approve` and `GET /api/scheduler/approvals/pending`.

---

### F-009: OAuth Token Stored in Process Environment -- RESOLVED (v0.2.23)

| Property | Value |
|----------|-------|
| **Severity** | ~~Medium~~ Resolved |
| **CVSS Vector** | AV:L/AC:L/PR:H/UI:N/S:U/C:H/I:N/A:N |
| **CVSS Score** | 4.4 |
| **Status** | **RESOLVED in v0.2.23** |

**Description:** After OAuth token acquisition, the access token was previously stored in `os.environ` for use by downstream SDK clients. On Linux, environment variables are readable via `/proc/PID/environ` by processes with the same UID.

**Remediation (v0.2.23):** Replaced `os.environ` storage with a module-level `_oauth_token_cache` dictionary in `oauth_token_manager.py`. Added a `get_oauth_token()` function for just-in-time retrieval. Updated `flagship_client.py` and `gateway.py` to use the getter. The `revoke()` function clears both the new cache and any legacy env var. Tokens are no longer exposed via `/proc/PID/environ`.

---

### F-010: Network Allowlist Configuration -- RESOLVED (v0.2.24)

| Property | Value |
|----------|-------|
| **Severity** | ~~Low~~ Resolved |
| **CVSS Vector** | AV:L/AC:L/PR:H/UI:N/S:U/C:N/I:L/A:N |
| **CVSS Score** | 2.3 |
| **Status** | **RESOLVED in v0.2.24** |

**Description:** The `NetworkInterceptor` previously loaded domains from `config/network_allowlist.yaml` silently, with no feedback when the file was missing. The reload mechanism was only triggered manually.

**Remediation (v0.2.24):** `NetworkInterceptor` now logs a warning with guidance when the config file is missing. Added automatic reload via `threading.Timer` every 300 seconds (configurable via `_RELOAD_INTERVAL_S`), so config changes take effect without restart. The config file already includes documented YAML format with categorized domain sections.

---

### F-011: Audit Log Integrity Not Cryptographically Guaranteed -- RESOLVED (v0.2.24)

| Property | Value |
|----------|-------|
| **Severity** | ~~Low~~ Resolved |
| **CVSS Vector** | AV:L/AC:L/PR:H/UI:N/S:U/C:N/I:L/A:N |
| **CVSS Score** | 2.3 |
| **Status** | **RESOLVED in v0.2.24** |

**Description:** The AuditLogger previously hashed individual commands with SHA-256, but entries were not chained. An attacker with filesystem access could modify, delete, or reorder log entries without detection.

**Remediation (v0.2.24):** Implemented hash chaining in `AuditLogger`. Each entry now includes a `PrevHash:` field containing the SHA-256 hash of the previous entry. The chain starts with a zero hash and recovers the last hash from the existing log file on startup for continuity across restarts. All writes are thread-safe via `threading.Lock`. Modifying, deleting, or reordering any entry invalidates all subsequent entries in the chain.

---

### F-012: Rate Limiter State is In-Memory Only -- RESOLVED (v0.2.24)

| Property | Value |
|----------|-------|
| **Severity** | ~~Low~~ Resolved |
| **CVSS Vector** | AV:N/AC:H/PR:N/UI:N/S:U/C:N/I:N/A:L |
| **CVSS Score** | 3.1 |
| **Status** | **RESOLVED in v0.2.24** |

**Description:** The `RateLimiter` stored request timestamps in a Python dictionary that grew unboundedly as new IPs were seen.

**Remediation (v0.2.24):** Added `_cleanup_stale()` method that periodically (every 5 minutes) removes IPs with no requests within the active window. Cleanup is triggered automatically during the `check()` call cycle, preventing unbounded dictionary growth. Note: state is still in-memory (lost on restart); Redis-backed limiting is recommended for future multi-instance deployment.

---

### F-013 and F-014 (Informational -- Open)

| ID | Finding | Notes |
|----|---------|-------|
| F-013 | Vault key auto-generated on disk when env var not set | Acceptable for development. Operational guidance should require `LANCELOT_VAULT_KEY` in production. |
| F-014 | `FEATURE_TOOLS_HOST_EXECUTION` flag exists (default: false) | Clearly documented as dangerous. Acceptable if never enabled in production. |

### F-015: CognitionGovernor File Locking -- RESOLVED (v0.2.24)

| Property | Value |
|----------|-------|
| **Severity** | ~~Informational~~ Resolved |
| **Status** | **RESOLVED in v0.2.24** |

**Remediation (v0.2.24):** Added `threading.Lock` (`_file_lock`) to all file I/O operations in `CognitionGovernor`. Reads and writes are now mutually exclusive. Additionally, writes use atomic `os.replace()` via a temporary file to prevent partial writes from corrupting `usage_stats.json`.

---

## 6. Remediation Roadmap

### Completed (v0.2.23)

| Finding | Remediation | Resolved |
|---------|-------------|----------|
| F-002 | Added explicit `LANCELOT_DEV_MODE` env var; fail closed when token not set | v0.2.23 |
| F-003 | Implemented first-message auth handshake for WebSocket; deprecated query param | v0.2.23 |
| F-004 | Restricted CORS `allow_methods` and `allow_headers` to explicit lists | v0.2.23 |
| F-005 | Added security headers middleware to FastAPI app | v0.2.23 |
| F-009 | Moved OAuth token to in-memory cache; added `get_oauth_token()` getter; removed `os.environ` | v0.2.23 |

### Completed (v0.2.24)

| Finding | Remediation | Resolved |
|---------|-------------|----------|
| F-008 | Scheduler approval workflow: WebSocket event + approve endpoint + pending tracking | v0.2.24 |
| F-010 | Network allowlist: warning log when missing, auto-reload every 300s | v0.2.24 |
| F-011 | Audit log hash chaining: PrevHash field, tamper-evident chain, startup recovery | v0.2.24 |
| F-012 | Rate limiter: periodic stale IP cleanup every 5 minutes | v0.2.24 |
| F-015 | CognitionGovernor: threading.Lock + atomic os.replace() writes | v0.2.24 |

### Priority 0 -- High (Next Sprint)

| Finding | Remediation | Effort |
|---------|-------------|--------|
| F-001 | Evaluate Sysbox/gVisor; implement restricted Docker API proxy | 2-3 weeks |
| F-007 | Execute non-builtin skills in subprocess with restricted imports | 1-2 weeks |

### Priority 1 -- Medium (Sprint 2-3)

| Finding | Remediation | Effort |
|---------|-------------|--------|
| F-006 | SQLCipher integration + documentation for volume encryption | 1 week |

---

## 7. Compliance Considerations

### 7.1 OWASP Top 10 (2021) Mapping

| OWASP Category | Status | Evidence |
|----------------|--------|----------|
| **A01: Broken Access Control** | Strong | Bearer token auth with HMAC-SHA256 comparison. Dev mode now fail-closed (F-002 resolved). WebSocket uses first-message auth handshake (F-003 resolved). |
| **A02: Cryptographic Failures** | Partial | Fernet vault encryption is strong. Gap: unencrypted data at rest for SQLite, logs, chat history (F-006) |
| **A03: Injection** | Strong | InputSanitizer with anti-obfuscation normalization, PolicyEngine command denylist with shlex tokenization, path traversal detection with encoded variant handling |
| **A04: Insecure Design** | Strong | Constitutional governance (Soul), policy engine with risk tiers, receipt-based accountability, defense-in-depth architecture |
| **A05: Security Misconfiguration** | Strong | CORS restricted to explicit methods/headers (F-004 resolved). Security headers middleware added (F-005 resolved). Feature flags and subsystem gates properly configured. |
| **A06: Vulnerable Components** | Not Assessed | Dependencies use minimum version bounds (e.g., `cryptography`, `pyyaml>=6.0`). Recommendation: enable `pip-audit` in CI |
| **A07: Auth Failures** | Partial | HMAC-SHA256 constant-time comparison is correct. No session management, no MFA, single static tokens |
| **A08: Software/Data Integrity** | Strong | Fernet encryption, atomic writes, file change hashing in receipts. Gap: skill code runs without runtime verification (F-007) |
| **A09: Logging/Monitoring** | Strong | Comprehensive receipt system, structured audit logging with hash-chained tamper-evident entries (F-011 resolved). Thread-safe CognitionGovernor counters (F-015 resolved). |
| **A10: SSRF** | Strong | NetworkInterceptor with private IP blocking across 6 CIDR ranges, fail-closed DNS resolution, URL credential stripping |

### 7.2 NIST AI Risk Management Framework

Lancelot's governance architecture aligns with several NIST AI RMF practices:

| NIST AI RMF Function | Lancelot Alignment |
|-----------------------|-------------------|
| **GOVERN** | Soul constitution with versioned amendments, owner-gated approval, 5 critical invariant checks via linter |
| **MAP** | Risk tiers (T0-T3) for skill permissions, CognitionGovernor daily limits, feature flags for subsystem enable/disable |
| **MEASURE** | Receipt system tracks all actions with cognition tier classification, usage telemetry (tokens, tool calls, costs) |
| **MANAGE** | Quarantine-by-default for agent memory edits, Sentry approval whitelist, Crusader Mode with auto-pause gates |

### 7.3 CIS Docker Benchmark Alignment

| CIS Recommendation | Status | Notes |
|--------------------|--------|-------|
| 4.1: Create user for container | Pass | `lancelot` user created in Dockerfile, privilege dropped via `gosu` |
| 4.6: Add HEALTHCHECK | Pass | Health check on both services (`curl -f http://localhost:PORT/health`) |
| 5.2: Verify SELinux/AppArmor | Gap | No seccomp or AppArmor profile configured |
| 5.4: Restrict Linux capabilities | Gap | No `--cap-drop` in docker-compose |
| 5.10: Limit memory | Pass | Sandbox containers limited to 512MB |
| 5.12: Mount propagation | Pass | Named volumes with default propagation |
| 5.15: Do not share host PID | Pass | No `pid: host` in docker-compose |
| 5.31: Do not mount Docker socket | Fail | Socket mounted for sandbox provider (F-001) |

### 7.4 SOC 2 Readiness

| Trust Service Criteria | Readiness | Notes |
|------------------------|-----------|-------|
| **Security (CC6)** | Good | Strong auth with fail-closed dev mode, HMAC-SHA256 tokens, WebSocket first-message auth, restricted CORS, security headers. Remaining gap: encryption at rest (F-006) |
| **Availability (CC7)** | Good | Health monitoring, auto-restart, CognitionGovernor limits |
| **Processing Integrity (CC8)** | Good | Receipt system, atomic memory commits, file change hashing |
| **Confidentiality (CC9)** | Partial | Vault encryption strong; gaps in data-at-rest encryption |
| **Privacy** | N/A | Single-owner system; no multi-tenant data handling |

---

## 8. Appendices

### Appendix A: Security-Relevant File Inventory

| File | Purpose | Security Relevance |
|------|---------|-------------------|
| `src/core/gateway.py` | FastAPI gateway | Auth, rate limiting, CORS, WebSocket, request validation |
| `src/core/security.py` | Security primitives | InputSanitizer, AuditLogger, NetworkInterceptor, CognitionGovernor, Sentry |
| `src/tools/policies.py` | Policy engine | Command/path/network evaluation, risk classification, redaction |
| `src/tools/providers/local_sandbox.py` | Docker sandbox | Container execution, resource limits, output bounding |
| `src/connectors/vault.py` | Connector vault | Fernet encryption, access policies, audit logging |
| `src/memory/vault.py` | OAuth vault | Token encryption, key management |
| `src/core/soul/api.py` | Soul API | Owner authentication, amendment workflow |
| `src/core/soul/linter.py` | Soul linter | 5 critical invariant checks |
| `src/core/skills/executor.py` | Skill execution | Dynamic code loading, path validation |
| `src/core/skills/governance.py` | Skill governance | Risk tiers, marketplace permissions |
| `src/core/memory/gates.py` | Memory write gates | Block allowlist, quarantine, provenance |
| `src/core/memory/commits.py` | Memory commits | Atomic transactions, snapshot isolation |
| `src/core/scheduler/executor.py` | Scheduler execution | Gate-based checks, per-job locking |
| `src/core/oauth_token_manager.py` | OAuth PKCE | Token lifecycle, refresh, vault storage |
| `docker-compose.yml` | Deployment | Service topology, volumes, network, socket mount |
| `Dockerfile` | Container build | Non-root user, image hardening, gosu |
| `config/network_allowlist.yaml` | Network policy | Domain allowlist for outbound requests |

### Appendix B: Security Test Coverage

| Subsystem | Test Files | Approximate Count |
|-----------|-----------|-------------------|
| Security primitives | `test_security_s*.py` (11 files) | ~60 tests |
| Tool policies | `test_tool_policies.py` | 63 tests |
| Tool router | `test_tool_router.py` | 43 tests |
| Tool fabric integration | `test_tool_fabric_integration.py` | 36 tests |
| Tool hardening | `test_tool_fabric_hardening.py` | 105 tests |
| Repo/file ops | `test_repo_file_ops.py` | 49 tests |
| vNext2 hardening | `test_vnext2_hardening.py` | 42 tests |
| vNext3 hardening | `test_vnext3_hardening.py` | ~50 tests |
| Memory API | `test_memory_api.py` | ~40 tests |
| Memory commits | `test_context_compiler.py` | ~30 tests |
| Soul store/linter/versioning | Multiple files | ~62 tests |
| Soul amendments/API | Multiple files | ~39 tests |
| Skill schema/registry/executor | Multiple files | ~51 tests |
| Scheduler | Multiple files | ~48 tests |
| **Total** | **~40 files** | **1900+ tests** |

### Appendix C: Dependency Security Notes

| Package | Version Constraint | Security Notes |
|---------|-------------------|----------------|
| `cryptography` | Latest | Well-audited; used for Fernet vault. Actively maintained. |
| `fastapi` | Latest | Active security advisory tracking. No known critical CVEs. |
| `pyyaml` | `>=6.0` | v6.0+ mitigates YAML deserialization attacks. `safe_load()` used throughout. |
| `anthropic` | `>=0.20.0` | Official SDK. Token handling delegated to SDK. |
| `openai` | `>=1.0.0` | Official SDK. Structured API key management. |
| `google-genai` | `>=1.0.0` | Official Google AI SDK. |
| `requests` | Latest | Widely audited. Used for HTTP in Telegram and RSS fetching. |
| `playwright` | Latest | Browser automation. Chromium installed with system deps. |
| `llama-cpp-python` | `>=0.2.0` | Local inference. Runs within container; no network exposure. |
| `python-docx`, `openpyxl`, `reportlab` | Various | Document generation. No known critical CVEs. |

**Recommendation:** Pin all dependencies to exact versions in `requirements.txt` and enable automated vulnerability scanning via `pip-audit` or GitHub Dependabot.

### Appendix D: Glossary

| Term | Definition |
|------|------------|
| **GAS** | Governed Autonomous System -- an AI agent that executes real actions under policy constraints |
| **Soul** | Lancelot's constitutional identity document, versioned and linter-validated |
| **Receipt** | Structured audit record of an action, including hash, timing, inputs/outputs, and cognition tier |
| **War Room** | Web-based command interface (React SPA) for interacting with Lancelot |
| **STRIDE** | Threat modeling framework: Spoofing, Tampering, Repudiation, Information Disclosure, Denial of Service, Elevation of Privilege |
| **CVSS** | Common Vulnerability Scoring System -- standardized vulnerability severity rating |
| **Fernet** | Symmetric encryption scheme (AES-128-CBC + HMAC-SHA256) from the `cryptography` library |
| **PKCE** | Proof Key for Code Exchange -- OAuth 2.0 extension preventing authorization code interception |
| **SSRF** | Server-Side Request Forgery -- attack where the server is tricked into making requests to internal resources |
| **HMAC** | Hash-based Message Authentication Code -- used for constant-time token comparison |
| **gosu** | Lightweight privilege-dropping tool for Docker entrypoints |
| **Sentry** | Lancelot's human-in-the-loop permission system with persistent approval whitelist |
| **Crusader Mode** | Temporary escalated autonomy mode for complex multi-step operations |
| **CognitionGovernor** | Rate limiter for LLM token consumption and tool call frequency |

---

*This whitepaper was generated through automated security analysis of the Lancelot codebase with manual verification of findings. Updated to reflect remediations in v0.2.23 and v0.2.24. All file paths and line references are accurate as of the latest revision date. Findings should be re-validated after any significant code changes.*

**Document Revision History:**

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-21 | Myles Russell Hamilton | Initial comprehensive security assessment (v0.2.22) |
| 1.1 | 2026-02-21 | Myles Russell Hamilton | Updated for v0.2.23: marked F-002, F-003, F-004, F-005, F-009 as resolved; updated STRIDE tables, OWASP mapping, remediation roadmap, and SOC 2 readiness |
| 1.2 | 2026-02-21 | Myles Russell Hamilton | Updated for v0.2.24: marked F-008, F-010, F-011, F-012, F-015 as resolved; 10 of 15 findings now remediated; updated STRIDE DoS/Repudiation tables, OWASP A09 mapping |
