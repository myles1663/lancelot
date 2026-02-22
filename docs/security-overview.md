# Project Lancelot: Security Overview

## Security Architecture of a Governed Autonomous AI System

**Document Version:** 1.1
**System Version:** v7.4 (v0.2.25)
**Classification:** Public
**Author:** Myles Russell Hamilton

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [System Architecture Overview](#2-system-architecture-overview)
3. [Security Controls Inventory](#3-security-controls-inventory)
4. [Compliance Alignment](#4-compliance-alignment)
5. [Glossary](#5-glossary)

---

## 1. Executive Summary

Project Lancelot is a **Governed Autonomous System (GAS)** — an AI agent that executes real-world actions (shell commands, network requests, file operations, message delivery) under constitutional governance constraints. Unlike a chatbot, Lancelot operates autonomously within a framework of policy enforcement, risk-tiered approval gates, and cryptographic audit trails. This distinction is critical: the security surface area extends far beyond typical web application concerns into autonomous code execution, secret management, and LLM prompt integrity.

### 1.1 Scope

This overview covers the security architecture and controls implemented in Lancelot v7.4 (v0.2.25), including:

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

A comprehensive internal security assessment identified 15 findings across all severity levels. As of v0.2.25, **all 15 findings have been remediated** across three releases, covering authentication hardening, CORS restrictions, security headers, WebSocket authentication, OAuth token handling, scheduler approval workflows, network configuration, audit integrity, rate limiter memory management, file I/O safety, Docker socket isolation, encryption at rest guidance, skill runtime sandboxing, vault key management, and host execution documentation.

### 1.3 Key Strengths

- Constitutional governance model (Soul) with versioned, linted, owner-gated amendments
- Receipt-based audit trail covering all action types with SHA-256 hashing
- Fernet-encrypted secret vault with access policies and audit logging
- SSRF protection with private IP blocking and fail-closed DNS resolution
- Docker sandbox execution with memory limits, network isolation, and output bounding
- 6-stage skill security pipeline with owner review gate
- Quarantine-by-default memory editing with provenance tracking
- Non-root container execution with gosu privilege dropping
- Hash-chained tamper-evident audit log
- Thread-safe resource governance with atomic file writes
- Docker socket proxy for restricted Docker API access
- Non-builtin skill execution sandboxed in Docker containers

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

Both services run on the `lancelot_net` Docker bridge network. External access is via port 8000 (FastAPI gateway) only. The local LLM service on port 8080 is internal to the Docker network and not exposed externally.

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
|  Controls: Auth, CORS, rate limiting, subsystem gates            |
+-------------------------------+----------------------------------+
                                |
                    [InputSanitizer: 16 phrases, 10 patterns]
                    [NetworkInterceptor: allowlist + SSRF block]
                    [CognitionGovernor: 2M tokens/day, 1K calls/day]
                                |
+-------------------------------+----------------------------------+
|  ZONE 3: APPLICATION                                             |
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
| Docker API access | Via TCP socket proxy (restricted to container lifecycle operations) |
| Exposed ports | 8000 (gateway) |

---

## 3. Security Controls Inventory

### 3.1 Authentication and Authorization

**Primary mechanism:** Bearer token authentication via HMAC-SHA256 constant-time comparison.

- **API Token:** All protected endpoints require a valid bearer token validated with `hmac.compare_digest()` (timing-safe comparison)
- **Owner Token:** Separate administrative token for privileged operations including Soul amendments, memory quarantine approval, and file access
- **Dev Mode:** Fail-closed by default. Development mode bypass requires explicit opt-in via environment variable. If no token is configured and dev mode is not enabled, all requests are rejected except health endpoints
- **WebSocket Authentication:** First-message auth handshake protocol — clients must send an auth message with bearer token immediately after connection. 10-second timeout prevents hanging unauthenticated connections

**OAuth 2.0 PKCE:** For external provider integration:
- Code verifier (128-byte random) + SHA-256 code challenge
- State parameter with 600-second TTL
- Automatic token refresh 10 minutes before expiry
- Token storage in Fernet-encrypted vault (not in process environment)

**Rate Limiting:** Sliding-window per IP address:
- Default: 60 requests per 60 seconds
- Periodic stale IP cleanup prevents unbounded memory growth
- Applied to chat and upload endpoints

### 3.2 Input Validation and Sanitization

**InputSanitizer** — multi-layered prompt injection defense:

| Defense Layer | Implementation |
|---------------|----------------|
| Banned phrases | 16 phrases covering common injection patterns. Case-insensitive replacement with `[REDACTED]` |
| Regex patterns | 10 patterns: instruction override, role injection, prompt delimiter detection |
| Zero-width stripping | Removes U+200B, U+200C, U+200D, U+FEFF invisible characters |
| Homoglyph normalization | Cyrillic-to-Latin character mapping to defeat visual spoofing |
| URL decoding | Decodes percent-encoded sequences to defeat encoding-based obfuscation |
| Space normalization | Collapses multiple spaces to defeat spacing-based evasion |

**Processing order:** Normalize first (defeat obfuscation), then banned phrase replacement, then suspicious pattern flagging.

**CognitionGovernor** — resource governance:

| Limit | Value | Purpose |
|-------|-------|---------|
| Daily tokens | 2,000,000 | Prevent runaway LLM costs |
| Daily tool calls | 1,000 | Prevent infinite loops |
| Actions per minute | 60 | Throttle burst activity |

Counters are persisted to disk, reset daily, and protected by thread-safe file I/O with atomic writes.

**Request size limit:** 20 MB maximum on chat and upload endpoints.

### 3.3 Network Security

**NetworkInterceptor** — SSRF protection via multi-layer validation:

1. **URL credential stripping:** Removes `user:pass@` credentials from URLs before processing
2. **Domain allowlist:** Core infrastructure domains plus configurable domains from YAML configuration. Auto-reloads every 5 minutes for configuration changes without restart.
3. **Private IP blocking:** RFC 1918 and reserved ranges blocked:
   - `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` (private)
   - `127.0.0.0/8` (loopback), `169.254.0.0/16` (link-local), `0.0.0.0/8` (reserved)
4. **DNS resolution check:** Resolves hostnames and validates resolved IPs against blocked ranges
5. **Fail-closed:** If DNS resolution fails, the request is blocked

**Subdomain matching:** Allows `api.example.com` when `example.com` is in the allowlist.

**CORS:**
- Origins: configurable via environment variable (defaults to localhost development origins)
- Methods: restricted to `GET`, `POST`, `DELETE`, `PATCH`, `OPTIONS`
- Headers: restricted to `Authorization` and `Content-Type`

**HTTP Security Headers** (applied to all responses):
- `X-Frame-Options: DENY` — prevents clickjacking
- `X-Content-Type-Options: nosniff` — prevents MIME type sniffing
- `Referrer-Policy: strict-origin-when-cross-origin` — limits referrer leakage
- `Permissions-Policy: camera=(), microphone=(), geolocation=()` — restricts browser APIs

**Docker Network Isolation:**
- Services communicate via private bridge network
- Sandbox containers run with `--network=none` by default
- Local LLM accessible only from within the Docker network

### 3.4 Secret Management

**SecretVault:**

| Property | Implementation |
|----------|----------------|
| Algorithm | Fernet (AES-128-CBC + HMAC-SHA256) via `cryptography` library |
| Key priority | 1. Environment variable, 2. Existing key file, 3. Auto-generate |
| Storage | Encrypted credential file with backup |
| Access control | Per-connector access policies |
| Audit | Every store/retrieve/delete logged with timestamp, accessor, action |
| List safety | `list_secrets()` returns names only; values never exposed |

**Environment variables:** API keys and tokens stored in `.env` file:
- Loaded via `env_file` directive in Docker Compose
- `.env` is `.gitignored` and has never been committed to the repository
- Installer generates unique tokens during initial setup

**Sensitive data redaction:**

Patterns redacted from tool receipts and logs:
- `password`, `api_key`, `secret`, `token`, `bearer`
- Provider-specific API key patterns (OpenAI, GitHub, AWS)

### 3.5 Tool Execution Sandboxing

**PolicyEngine** — command evaluation pipeline:

1. **Denylist check** (shlex tokenization): destructive commands (`rm -rf /`, `mkfs`, `dd`), fork bombs, privilege escalation (`sudo`, `su -`), reverse shells (`nc -l`), credential access, kernel modules
2. **Allowlist check**: 50+ safe commands including `git`, `python`, `pip`, `npm`, `ls`, `grep`, `curl`, etc.
3. **Risk classification**: LOW (read/list/status), MEDIUM (modify/install/test), HIGH (network/deploy/delete/credentials)
4. **Path traversal detection**: normalized `..` depth tracking, encoded `%2e%2e` and double-encoded `%252e` detection, symlink rejection, workspace boundary enforcement via `os.path.realpath()`
5. **Sensitive path blocking**: `.env`, `.ssh`, `.aws`, `.gnupg`, `credentials`, `secrets.yaml`

**Docker Sandbox:**

| Parameter | Value |
|-----------|-------|
| Base image | `python:3.11-slim` |
| Memory limit | 512 MB |
| CPU limit | 1 core |
| Timeout | 300 seconds |
| Network | Disabled by default (`--network=none`) |
| Max stdout | 100,000 characters |
| Max stderr | 50,000 characters |
| User | Non-root |

### 3.6 Skill Security Pipeline

Six-stage security gate for skill installation:

```
Stage 1: Manifest Validation
    |-- Schema validation: name, version, description, risk, permissions
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
    |-- Marketplace skills restricted to low-risk permissions
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

### 3.7 Memory Protection

**WriteGateValidator:**

| Gate | Purpose |
|------|---------|
| Block allowlist | Agents restricted to specific memory blocks |
| Owner-only blocks | Critical identity blocks require owner provenance |
| Secret scrubbing | Regex patterns detect API keys, tokens, credentials before storage |
| Confidence scoring | Per-edit confidence tracked; low-confidence edits flagged |
| Quarantine | Agent edits quarantined by default; require owner approval |

**CommitManager:**

| Feature | Implementation |
|---------|----------------|
| Atomic transactions | Snapshot isolation with rollback support |
| Snapshot retention | 50 snapshots with LRU eviction |
| Rollback | Per-item undo log for insert/update/delete recovery |
| Provenance | Every edit records origin type, timestamp, and approval chain |

### 3.8 Audit and Observability

**AuditLogger:**
- Commands logged with SHA-256 hash, user attribution, and ISO 8601 timestamp
- Events logged with type, user, detail hash, and structured details
- Hash-chained entries: each entry includes the SHA-256 hash of the previous entry, creating a tamper-evident chain
- Thread-safe writes via locking

**Receipt System:**

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

**ToolReceipt:**
- Extended with: capability, provider ID, policy snapshot, risk level, file change hashes (before/after)
- Enables post-hoc verification of tool execution claims

**Sentry:**
- Human-in-the-loop permission system with persistent approval whitelist
- Approval signatures: SHA-256 hash of action type + metadata
- Persisted across restarts

---

## 4. Compliance Alignment

### 4.1 OWASP Top 10 (2021) Mapping

| OWASP Category | Status | Evidence |
|----------------|--------|----------|
| **A01: Broken Access Control** | Strong | Bearer token auth with HMAC-SHA256 comparison. Dev mode fail-closed. WebSocket first-message auth handshake. |
| **A02: Cryptographic Failures** | Strong | Fernet vault encryption. Volume-level encryption documented as deployment requirement. |
| **A03: Injection** | Strong | InputSanitizer with anti-obfuscation normalization, PolicyEngine command denylist with shlex tokenization, path traversal detection with encoded variant handling. |
| **A04: Insecure Design** | Strong | Constitutional governance (Soul), policy engine with risk tiers, receipt-based accountability, defense-in-depth architecture. |
| **A05: Security Misconfiguration** | Strong | CORS restricted to explicit methods/headers. Security headers middleware. Feature flags and subsystem gates properly configured. |
| **A06: Vulnerable Components** | Not Assessed | Dependencies use minimum version bounds. Recommendation: enable automated vulnerability scanning in CI. |
| **A07: Auth Failures** | Partial | HMAC-SHA256 constant-time comparison is correct. Single static tokens; no MFA. |
| **A08: Software/Data Integrity** | Strong | Fernet encryption, atomic writes, file change hashing in receipts. |
| **A09: Logging/Monitoring** | Strong | Comprehensive receipt system, structured audit logging with hash-chained tamper-evident entries. Thread-safe governance counters. |
| **A10: SSRF** | Strong | NetworkInterceptor with private IP blocking across 6 CIDR ranges, fail-closed DNS resolution, URL credential stripping. |

### 4.2 NIST AI Risk Management Framework

Lancelot's governance architecture aligns with several NIST AI RMF practices:

| NIST AI RMF Function | Lancelot Alignment |
|-----------------------|-------------------|
| **GOVERN** | Soul constitution with versioned amendments, owner-gated approval, critical invariant checks via linter |
| **MAP** | Risk tiers (T0-T3) for skill permissions, CognitionGovernor daily limits, feature flags for subsystem enable/disable |
| **MEASURE** | Receipt system tracks all actions with cognition tier classification, usage telemetry (tokens, tool calls, costs) |
| **MANAGE** | Quarantine-by-default for agent memory edits, Sentry approval whitelist, Crusader Mode with auto-pause gates |

### 4.3 CIS Docker Benchmark Alignment

| CIS Recommendation | Status |
|--------------------|--------|
| 4.1: Create user for container | Pass |
| 4.6: Add HEALTHCHECK | Pass |
| 5.10: Limit memory | Pass |
| 5.12: Mount propagation | Pass |
| 5.15: Do not share host PID | Pass |

### 4.4 SOC 2 Readiness

| Trust Service Criteria | Readiness | Notes |
|------------------------|-----------|-------|
| **Security (CC6)** | Good | Strong auth with fail-closed dev mode, HMAC-SHA256 tokens, WebSocket first-message auth, restricted CORS, security headers. |
| **Availability (CC7)** | Good | Health monitoring, auto-restart, CognitionGovernor limits. |
| **Processing Integrity (CC8)** | Good | Receipt system, atomic memory commits, file change hashing. |
| **Confidentiality (CC9)** | Strong | Vault encryption strong; volume-level encryption documented as deployment requirement. |
| **Privacy** | N/A | Single-owner system; no multi-tenant data handling. |

---

## 5. Glossary

| Term | Definition |
|------|------------|
| **GAS** | Governed Autonomous System — an AI agent that executes real actions under policy constraints |
| **Soul** | Lancelot's constitutional identity document, versioned and linter-validated |
| **Receipt** | Structured audit record of an action, including hash, timing, inputs/outputs, and cognition tier |
| **War Room** | Web-based command interface (React SPA) for interacting with Lancelot |
| **Fernet** | Symmetric encryption scheme (AES-128-CBC + HMAC-SHA256) from the `cryptography` library |
| **PKCE** | Proof Key for Code Exchange — OAuth 2.0 extension preventing authorization code interception |
| **SSRF** | Server-Side Request Forgery — attack where the server is tricked into making requests to internal resources |
| **HMAC** | Hash-based Message Authentication Code — used for constant-time token comparison |
| **gosu** | Lightweight privilege-dropping tool for Docker entrypoints |
| **Sentry** | Lancelot's human-in-the-loop permission system with persistent approval whitelist |
| **Crusader Mode** | Temporary escalated autonomy mode for complex multi-step operations |
| **CognitionGovernor** | Rate limiter for LLM token consumption and tool call frequency |

---

*This public security overview is derived from the internal security whitepaper for Project Lancelot v7.4 (v0.2.25). All 15 security findings have been resolved. For detailed findings, remediation history, and threat model analysis, refer to the internal security whitepaper.*

**Author:** Myles Russell Hamilton
**Date:** February 21, 2026
