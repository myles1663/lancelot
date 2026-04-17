# MCP (Model Context Protocol) Governance

**Feature Flag:** `FEATURE_MCP` (default: `false`)
**Codebase:** `src/mcp/` (12 modules)

Lancelot's MCP subsystem provides governed access to external MCP-compliant tool servers. Every invocation passes through an 8-gate governance pipeline — fail-closed at every gate, with mandatory receipt persistence as the final gate.

---

## Design Decisions

1. **HTTP+SSE only** — Stdio process spawning is excluded as an attack surface in a containerized governance system. This is a deliberate security decision documented for future reviewers and patent examination.
2. **Receipts before proxy** — The proxy constructor requires a receipt manager. It is impossible to construct a proxy that can complete invocations without generating receipts.
3. **Fourth fail-closed gate** — If receipt persistence fails, the tool invocation result is discarded. A governance system whose audit trail is broken is not governed.
4. **Federation ceiling reuse** — MCP permission narrowing uses the same monotonic contract as HIVE scoped Souls, not a separate mechanism.
5. **Guard stack required** — The governed proxy now refuses to construct unless the argument screener, response guard, and network policy are all present. The live gateway already wires those dependencies; constructor-level fail-closed behavior now matches the runtime contract.

---

## Governance Pipeline

Every MCP tool invocation passes through all 8 gates sequentially. No shortcutting, no fallbacks.

| Gate | Check | Module | Block Reason |
|------|-------|--------|--------------|
| **1. Soul Permission** | Is server+tool permitted by active Soul? | `permissions.py` | `soul_permission` |
| **2. Kill Switch** | Is `FEATURE_MCP` on? Is per-server switch on? | `kill_switches.py` | `kill_switch` |
| **3. Server Status** | Is server registered and not suspended/error? | `registry.py` | `server_status` |
| **4. Network Allowlist** | Is endpoint domain in network allowlist? | `network_policy.py` | `network` |
| **5. Argument Screening** | Do arguments contain injection patterns? | `argument_screen.py` | `argument_screen` |
| **6. Credential Resolution** | Can we resolve vault credential for this server? | `registry.py` | `credential` |
| **7. MCP Execution** | Call server via HTTP+SSE JSON-RPC 2.0 | `client.py` | `mcp_execution` |
| **7b. Response Guard** | Scrub credentials and injection markers from result | `response_guard.py` | *(remedial — does not block)* |
| **8. Receipt Persistence** | Write audit receipt. **If this fails, result is discarded.** | `receipts.py` | `receipt_failure` |

Gates 1–7 can block. Gate 7b only redacts (never blocks). Gate 8 is **mandatory** — receipt write failure causes the invocation result to be discarded.

---

## Module Reference

| Module | Purpose |
|--------|---------|
| `proxy.py` | GovernedMCPProxy — single entry point, orchestrates all 8 gates |
| `permissions.py` | MCPPermissionEvaluator — Soul-gated access control |
| `kill_switches.py` | Master (`FEATURE_MCP`) + per-server kill switch checks |
| `registry.py` | MCPServerRegistry — vault-backed server config store |
| `network_policy.py` | Endpoint validation, HTTPS enforcement, SSRF protection |
| `argument_screen.py` | Deep argument inspection with 6 injection pattern categories |
| `response_guard.py` | Credential leak scrubbing + prompt injection removal |
| `client.py` | HTTP+SSE MCP protocol client (JSON-RPC 2.0) |
| `receipts.py` | MCPReceiptManager — mandatory audit trail |
| `federation_ceiling.py` | Monotonic permission narrowing for federation |
| `api.py` | FastAPI router for War Room MCP management |
| `__init__.py` | Package documentation |

---

## Risk Tiers

MCP servers are assigned risk tiers that determine governance overhead:

| Tier | Name | Description | Examples |
|------|------|-------------|----------|
| **T0** | Read-only | No side effects | query, list, describe |
| **T1** | Reversible | Side effects, reversible | update, patch, add |
| **T2** | Controlled | Partially reversible (default) | delete, modify, batch |
| **T3** | Irreversible | Cannot be undone | financial transactions, permanent resources |

---

## Soul Permission Model

MCP permissions are declared in the Soul document under `mcp_permissions`:

```yaml
mcp_permissions:
  - server_id: "github"
    allowed_tools: ["list_repos", "get_issues", "search_code"]
    risk_tier: "T1"
  - server_id: "database"
    allowed_tools: ["*"]  # Wildcard — all tools allowed
    risk_tier: "T2"
```

The `MCPPermissionEvaluator` loads these at startup and checks every invocation against:
- Is the server_id in the permitted list?
- Is the tool_name in the server's `allowed_tools`?
- What risk tier applies?

---

## Argument Screening

The `MCPArgumentScreener` inspects tool arguments for 6 categories of injection:

| Category | Pattern Count | Examples |
|----------|--------------|---------|
| **SQL Injection** | 11 | `OR '1'='1'`, `UNION SELECT`, `SLEEP()`, block comments |
| **Path Traversal** | 8 | `../`, `..\`, URL-encoded, `/etc/passwd`, null bytes |
| **Command Injection** | 7 | `;ls`, `\| cat`, backticks, `$()`, reverse shell, `eval/exec` |
| **Prompt Injection** | 8 | `<\|system\|>`, `[SYSTEM]`, `IMPORTANT: ignore`, override markers |
| **NoSQL Injection** | 3 | MongoDB `$` operators, `$where`, `this.field ==` |
| **Size Limits** | — | Max 50KB per string, 200KB total |

### Compound Attack Detection

If **2 or more** categories trigger in the same invocation, severity is elevated to **critical** and the invocation is **hard-blocked**. This catches sophisticated multi-vector attacks.

### Severity Levels

| Level | Meaning |
|-------|---------|
| `none` | Clean arguments |
| `low` | Single minor match, logged |
| `medium` | Clear pattern match, logged |
| `high` | Strong injection signal, blocked |
| `critical` | Compound attack or dangerous pattern, hard-blocked |

---

## Response Guard

The `MCPResponseGuard` scrubs MCP server responses before they enter the agent context.

### Credential Patterns Scrubbed (13 patterns)

OpenAI keys (`sk-*`), GitHub PATs (`ghp_*`), Slack tokens (`xoxb-`/`xoxp-`), AWS access keys (`AKIA*`), JWTs, Bearer tokens, Basic auth, connection strings, private keys, and more.

### Prompt Injection Markers Removed (6 patterns)

System role markers, `im_start`/`im_end` tags, `IMPORTANT:`/`NEW INSTRUCTIONS` directives.

### Size Enforcement

- Max response: 500KB (truncated with warning)
- Max list items: 1000 (truncated with warning)

Gate 7b is **remedial** — it sanitizes but does not block. The sanitized result proceeds to Gate 8.

---

## Network Policy

The `MCPNetworkPolicy` validates endpoints at registration time and invocation time.

### Registration-Time Checks

| Check | Description |
|-------|-------------|
| HTTPS enforcement | HTTP only allowed for localhost development |
| URL well-formed | Must have scheme + host |
| Blocked metadata | AWS/GCP metadata endpoints blocked (`169.254.169.254`) |
| Private IP ranges | `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` blocked |
| Embedded credentials | Username/password in URL rejected |
| Domain resolution | Warning if domain unresolvable |

### Invocation-Time Check

`check_invocation_allowed(endpoint)` delegates to the global `NetworkInterceptor` for runtime domain allowlist enforcement.

---

## Kill Switches

### Two Levels

1. **Master switch:** `FEATURE_MCP` — if OFF, all MCP invocations blocked
2. **Per-server switches:** `MCP_SERVER_<SERVER_ID>` — if OFF, only that server blocked

Master is checked first (fail-closed). Per-server flags that don't exist fail-open (master switch covers).

---

## Server Registry

`MCPServerRegistry` stores server configurations encrypted in the Credential Vault.

### Server Configuration

| Field | Description |
|-------|-------------|
| `server_id` | Unique identifier |
| `name` | Display name |
| `endpoint` | HTTP+SSE URL |
| `transport` | Always `HTTP_SSE` (stdio excluded) |
| `auth_type` | NONE, API_KEY, OAUTH2, BASIC, CUSTOM_HEADER |
| `vault_key` | Vault reference for credential |
| `default_risk_tier` | T0–T3 |
| `network_domains` | Allowed domains for this server |
| `kill_switch_id` | Auto-generated feature flag ID |

### Server Status Lifecycle

```
REGISTERED → VALIDATED → ACTIVE
                       → SUSPENDED
                       → ERROR
```

### Credential Resolution

Credentials are resolved from the Vault using scoped access: `vault.retrieve(vault_key, accessor_id=f"mcp:{server_id}")`. Each server can only access its own credential.

---

## Receipt System

### Receipt Types

| Type | When |
|------|------|
| `MCP_TOOL_CALL` | Successful invocation |
| `MCP_TOOL_BLOCKED` | Blocked by any gate |

### Block Gates

`soul_permission`, `kill_switch`, `server_status`, `network`, `argument_screen`, `credential`, `mcp_execution`, `receipt_failure`

### Sensitive Key Redaction

Keys matching `token`, `key`, `secret`, `password`, `credential`, `auth`, `api_key`, `access_token`, `refresh_token` are redacted in receipt storage.

---

## Federation Ceiling

The `federation_ceiling.py` module enforces **monotonic permission narrowing** — child/peer instances can only have equal or more restrictive MCP permissions than the root Soul.

### Four Rules

1. **Server subset** — Child servers ⊆ root servers
2. **Tool subset** — Child tools ⊆ root tools per server
3. **Tier elevation** — Child tier ≥ root tier (more restrictive)
4. **Wildcard restriction** — Child wildcard only if root has wildcard

### Violation Types

| Violation | Description |
|-----------|-------------|
| `server_removed` | Server not in root's permitted set |
| `tools_narrowed` | Tools removed by intersection |
| `wildcard_downgraded` | Child wildcard → root's explicit tool set |
| `tier_elevated` | Child tier less restrictive → elevated to root's tier |

### Enforcement Points

1. **Soul propagation** — When root Soul is pushed to peer
2. **Peer registration** — New peer's permissions validated against root's ceiling
3. **HIVE scoped Soul** — Sub-agent MCP permissions intersected with parent's

---

## API Endpoints

**Prefix:** `/api/mcp/`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/servers` | List all registered servers + feature status |
| POST | `/servers` | Register new server (validates endpoint) |
| GET | `/servers/{id}` | Server detail + governance status |
| DELETE | `/servers/{id}` | Unregister server |
| POST | `/servers/{id}/status` | Update server status |
| POST | `/servers/{id}/test` | Test connection + discover tools |
| POST | `/servers/{id}/credential` | Store credential in Vault |
| GET | `/receipts/summary` | MCP receipt statistics |

---

## War Room Integration

The **MCP Section** appears in the Connectors page (not a separate panel) and provides:

- Server list with status dots, risk tier badges, tool counts
- Expandable server cards with auth type, credentials, kill switch ID
- Register form for new servers
- Test Connection button (discovers tools, shows latency)
- Credential management (masked input, Vault-backed storage)
- Suspend/Activate/Unregister controls
- Governance pipeline description footer

When `FEATURE_MCP` is disabled, the section shows an "Enable FEATURE_MCP in Kill Switches" message.
