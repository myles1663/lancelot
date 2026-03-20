# A2A Protocol Support

Lancelot implements Google's **Agent-to-Agent (A2A) v0.2** protocol as a governed transport layer for cross-agent communication. Every inbound and outbound A2A interaction passes through the full governance stack — Soul evaluation, risk classification, trust tracking, and T3 approval gates.

**Feature-gated:** `FEATURE_A2A=false` by default. Enable via environment variable or War Room Kill Switches panel.

For the architectural context, see [Architecture](architecture.md). For the trust model, see [Trust Ledger](trust-ledger.md).

---

## A2A vs Federation

| | A2A | Federation |
|---|-----|-----------|
| **Purpose** | Lancelot ↔ non-Lancelot agents | Lancelot ↔ Lancelot instances |
| **Protocol** | Google A2A v0.2 | Custom federation protocol |
| **Governance** | Per-agent trust tiers, Soul-filtered | Soul propagation, shared governance |
| **Detection** | `governance_framework: "lancelot"` in Agent Card | Instance ID in federation handshake |

**Lancelot-to-Lancelot A2A is rejected.** If an inbound A2A request comes from a Lancelot instance (detected via `governance_framework: "lancelot"` in the Agent Card), the inbound pipeline rejects it with a clear error directing to the Federation API.

---

## Soul Configuration

### Inbound Permissions

```yaml
inbound_a2a_permissions:
  allow_inbound: true
  default_trust_tier: 3
  allowed_callers:
    - agent_id_pattern: "crewai-*"
      trust_tier: 2
  blocked_callers:
    - agent_id_pattern: "untrusted-*"
  require_agent_card: true
  skill_filter:
    - "research"
    - "summarize"
  require_preregistration: false
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `allow_inbound` | bool | `false` | Master switch for accepting inbound A2A tasks |
| `default_trust_tier` | int | `3` | Trust tier assigned to unknown callers |
| `allowed_callers` | list | `[]` | Pattern-matched caller rules with specific trust tiers |
| `blocked_callers` | list | `[]` | Pattern-matched caller blocklist (checked before allowed) |
| `require_agent_card` | bool | `true` | Require callers to present a valid Agent Card |
| `skill_filter` | list | `[]` | Skills advertised to A2A callers (empty = all) |
| `require_preregistration` | bool | `false` | Require agents to be pre-registered before accepting tasks |

### Outbound Permissions

```yaml
outbound_a2a_permissions:
  allow_outbound: true
  allowed_targets:
    - agent_id_pattern: "partner-*"
      trust_tier: 1
  max_delegation_depth: 3
  require_agent_card_verification: true
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `allow_outbound` | bool | `false` | Master switch for delegating tasks to remote agents |
| `allowed_targets` | list | `[]` | Pattern-matched target rules with specific trust tiers |
| `max_delegation_depth` | int | `3` | Maximum delegation chain depth |
| `require_agent_card_verification` | bool | `true` | Verify remote agent card before delegating |

---

## Receipt Types

17 receipt types track all A2A activity:

| Receipt Type | When Emitted |
|-------------|-------------|
| `A2A_TASK_RECEIVED` | Inbound task accepted at Stage 1 |
| `A2A_INBOUND_BLOCKED` | Inbound task rejected at any pipeline stage |
| `A2A_TASK_EXECUTING` | Task enters governed execution (Stage 7) |
| `A2A_TASK_COMPLETED` | Task completes successfully (Stage 8) |
| `A2A_DELEGATION_SENT` | Outbound delegation dispatched (Stage 8) |
| `A2A_OUTBOUND_BLOCKED` | Outbound delegation rejected at any stage |
| `A2A_DELEGATION_COMPLETED` | Remote agent returned success (Stage 9) |
| `A2A_DELEGATION_FAILED` | Remote agent returned failure (Stage 9) |
| `T3_A2A_INBOUND_APPROVAL_REQUEST` | Inbound T3 approval needed (Stage 6) |
| `T3_A2A_INBOUND_APPROVED` | Operator approved inbound T3 request |
| `T3_A2A_INBOUND_REJECTED` | Operator rejected inbound T3 request |
| `T3_A2A_OUTBOUND_APPROVAL_REQUEST` | Outbound T3 approval needed (Stage 6) |
| `T3_A2A_OUTBOUND_APPROVED` | Operator approved outbound T3 request |
| `T3_A2A_OUTBOUND_REJECTED` | Operator rejected outbound T3 request |
| `A2A_AGENT_REGISTERED` | Agent registered (auto or manual) |
| `A2A_AGENT_CARD_UPDATED` | Agent Card regenerated |
| `A2A_AGENT_CARD_FETCHED` | Remote Agent Card fetched/verified |

---

## Inbound Pipeline (8 Stages)

The inbound pipeline processes tasks from external agents:

```
External Agent → POST /a2a/tasks/send
  Stage 1: Authentication (bearer token, API key, or open)
  Stage 2: Caller Identity Resolution (framework detection, Lancelot rejection)
  Stage 3: Skill Security Pipeline (injection pattern detection)
  Stage 4: Soul Evaluation (allowed/blocked callers, preregistration)
  Stage 5: Risk Classification (financial ops → T3)
  Stage 6: T3 Approval Gate (operator confirmation if at T3)
  Stage 7: Governed Execution (skill dispatch)
  Stage 8: Response + Trust Update (graduation/demotion)
```

**Key behaviors:**
- Lancelot-to-Lancelot rejected at Stage 2 with redirect to Federation API
- `require_preregistration: false` (default) allows unknown agents; `true` requires pre-registration
- Single failure resets agent trust to T3
- Sustained success graduates trust (configurable threshold)

---

## Outbound Pipeline (10 Stages)

The outbound pipeline governs delegation to remote agents:

```
Internal Request → OutboundPipeline.delegate()
  Stage 1: Remote Agent Resolution (registry lookup)
  Stage 2: Soul Evaluation (allowed_targets check)
  Stage 3: Network Allowlist (URL validation)
  Stage 4: Skill Security — PII Scrub (SSN, credit card, email patterns)
  Stage 5: Risk Classification (financial ops → T3)
  Stage 6: T3 Approval Gate (operator confirmation if at T3)
  Stage 7: Credential Injection (auth headers for remote agent)
  Stage 8: Delegation (HTTP POST to remote /a2a/tasks/send)
  Stage 9: Response Inspection (validate response structure)
  Stage 10: Receipt + Trust Update (record outcome, adjust trust)
```

**Key behaviors:**
- PII scrubbing at Stage 4 removes SSNs, credit card numbers, email addresses
- `max_delegation_depth` prevents unbounded delegation chains
- Agent Card verification required by default before first delegation

---

## Agent Card

Lancelot generates a dynamic Agent Card at `GET /.well-known/agent.json` per the A2A spec:

```json
{
  "name": "Lancelot",
  "version": "0.3.0",
  "a2a_protocol_version": "0.2",
  "url": "https://your-instance/a2a/",
  "skills": [...],
  "authentication": { "type": "bearer" },
  "governance_declaration": {
    "governed": true,
    "governance_framework": "lancelot",
    "soul_version_hash": "abc123..."
  }
}
```

- Skills are dynamically filtered by Soul's `skill_filter`
- Card is cached and invalidated when Soul changes
- `POST /api/a2a/card/regenerate` forces regeneration

---

## Remote Agent Registry

SQLite-backed registry tracks all known remote agents:

| Field | Description |
|-------|-------------|
| `agent_id` | Unique identifier |
| `display_name` | Human-readable name |
| `agent_card_url` | URL to fetch Agent Card |
| `agent_framework` | Framework type (crewai, langchain, google_adk, unknown) |
| `direction` | Communication direction (inbound, outbound, both) |
| `inbound_trust_tier` | Trust tier for inbound tasks from this agent |
| `outbound_trust_tier` | Trust tier for outbound delegation to this agent |
| `status` | active or revoked |
| `card_status` | verified (< 24h), stale (> 24h), unverified |
| `interaction_count` | Total interactions |
| `success_count` | Successful interactions |
| `kill_switch_id` | Per-agent kill switch: `A2A_[AGENT_ID]` |

**Trust dynamics:**
- Directional trust — inbound and outbound tracked independently
- Graduation: sustained success over configurable threshold
- Demotion: single failure resets to T3

---

## Kill Switch Hierarchy

```
FEATURE_A2A (boot gate — controls subsystem initialization)
  └── A2A_ALL (runtime kill switch — disables all A2A at runtime)
       └── A2A_[AGENT_ID] (per-agent kill switch — auto-created on registration)
```

- `FEATURE_A2A=false`: A2A subsystem not loaded, all endpoints return 503
- `A2A_ALL` issued: All A2A traffic blocked, agents and registry preserved
- `A2A_[AGENT_ID]` issued: Specific agent blocked, others unaffected

---

## API Endpoints

### Protocol Endpoints (root-mounted, for external agents)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/.well-known/agent.json` | Lancelot's Agent Card |
| POST | `/a2a/tasks/send` | Submit task to Lancelot |
| GET | `/a2a/tasks/{task_id}` | Get task status |
| GET | `/a2a/tasks/{task_id}/subscribe` | SSE stream for task updates |

### Management Endpoints (under /api/a2a/, for War Room)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/a2a/status` | A2A subsystem status |
| GET | `/api/a2a/agents` | List registered agents |
| GET | `/api/a2a/agents/{id}` | Get agent details |
| POST | `/api/a2a/agents` | Register remote agent |
| DELETE | `/api/a2a/agents/{id}` | Revoke remote agent |
| POST | `/api/a2a/agents/{id}/verify` | Re-verify agent card |
| GET | `/api/a2a/card` | Get own Agent Card |
| POST | `/api/a2a/card/regenerate` | Regenerate Agent Card |
| POST | `/api/a2a/delegate` | Delegate task to remote agent |
| GET | `/api/a2a/receipts` | Query A2A receipts |

---

## War Room UI

The A2A section appears in the **Connectors** page (below MCP):

- **Status badges** — INBOUND/OUTBOUND enabled indicators, A2A v0.2 protocol badge
- **Agent list** — Expandable rows with direction, framework, card status, trust tiers
- **Agent detail** — Card URL, auth type, interaction counts, last outcome, kill switch ID
- **Actions** — Register agent, re-verify card, revoke agent
- **Own Agent Card** — Lancelot's advertised card with skills, governance declaration, regenerate button

---

## Source Files

```
src/a2a/
├── __init__.py              # Package init
├── types.py                 # A2ATask, AgentCard, RemoteAgent dataclasses + enums
├── registry.py              # SQLite-backed agent registry with trust tracking
├── agent_card.py            # Dynamic Agent Card generator with Soul filtering
├── inbound_pipeline.py      # 8-stage inbound governance pipeline
├── outbound_pipeline.py     # 10-stage outbound governance pipeline
├── client.py                # HTTP client for remote A2A communication
├── server.py                # Protocol-standard endpoints (root-mounted)
└── api.py                   # Management API endpoints (/api/a2a/)
```

War Room components:
- `src/warroom/src/api/a2a.ts` — API client functions
- `src/warroom/src/components/A2ASection.tsx` — Connectors page A2A section
