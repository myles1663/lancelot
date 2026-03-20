# Observability Integration

Enterprise observability layer that makes Lancelot's governance data visible in every major monitoring stack without requiring operators to replace their existing tooling.

---

## Architecture

The observability layer **translates** existing governance data — it does not modify the receipt system. Three complementary mechanisms:

| Mechanism | Purpose | Protocol |
|-----------|---------|----------|
| **OpenTelemetry Export** | Traces + metrics to Datadog, Grafana, Splunk, etc. | OTLP/HTTP |
| **Webhooks** | Real-time event push to SIEM, PagerDuty, Slack | HTTPS + HMAC-SHA256 |
| **Metrics API** | Read-only HTTP API for custom dashboards | REST + cursor pagination |

All three are gated behind `FEATURE_OBSERVABILITY` (default: false) with individual enable/disable toggles per mechanism.

---

## OpenTelemetry Export

### Receipt-to-Span Mapping

| Lancelot Concept | OTel Concept |
|------------------|--------------|
| `quest_id` | `trace_id` — one trace per governed workflow |
| `receipt_id` | `span_id` — each receipt is one span |
| `parent_id` | `parent_span_id` — preserves receipt DAG hierarchy |
| `receipt_type` | `span.name` — `lancelot.{type_lowercase}` |
| `timestamp` | `span start_time` |
| `duration_ms` | `span duration` (0 for instantaneous events) |
| `risk_tier` | `lancelot.risk_tier` attribute (T0–T3) |
| `operator_id` | `lancelot.operator_id` attribute |
| `soul_version` | `lancelot.soul_version` attribute |

### Sampling

- **T2 and T3 spans**: Always exported at 100% — governance events are never sampled out
- **T0 and T1 spans**: Configurable sampling rate (default: 10%)
- **Governance events** (kill switches, T3 approvals, Soul changes): Always 100% regardless of tier

### OTel Metrics (12 instruments)

| Metric | Type | Description |
|--------|------|-------------|
| `lancelot.actions.total` | Counter | Total governed actions. Labels: `risk_tier`, `receipt_type` |
| `lancelot.actions.blocked` | Counter | Blocked actions. Labels: `block_reason` |
| `lancelot.kill_switches.active` | UpDownCounter | Currently active kill switches |
| `lancelot.t3_approvals.pending` | UpDownCounter | T3 actions awaiting approval |
| `lancelot.t3_approvals.response_time_ms` | Histogram | T3 approval response latency |
| `lancelot.soul.version_changes` | Counter | Soul version changes since startup |
| `lancelot.trust_ledger.tier_distribution` | UpDownCounter | Capabilities per trust tier |
| `lancelot.cost.usd_total` | Counter | Total AI model cost. Labels: `provider`, `model` |
| `lancelot.cost.usd_rate` | Gauge | Current spend rate USD/hr (15-min window) |
| `lancelot.mcp.tool_calls` | Counter | MCP invocations. Labels: `server_id`, `tool_name`, `status` |
| `lancelot.hive.active_agents` | UpDownCounter | Active HIVE sub-agents |
| `lancelot.receipts.chain_lag_ms` | Gauge | Receipt write latency |

### Configuration

Configured via War Room at `/api/observability/config/otel`:

- **Endpoint**: OTLP/HTTP endpoint URL (e.g., `https://otel-collector:4318`)
- **Auth**: Bearer token or header — stored in Credential Vault
- **Export interval**: 1–60 seconds (default: 5s)
- **Sampling rate**: T0/T1 percentage (default: 10%)
- **Resource attributes**: `deployment_id`, `lancelot_version`, etc.

### Exporter Protocol

Phase 1 supports **OTLP/HTTP only**. gRPC support available on request (requires `grpcio` dependency). The HTTP exporter reaches every major platform: Datadog, Grafana Cloud, Honeycomb, Splunk, New Relic, Dynatrace.

---

## Webhooks

### Event Categories

| Category | Events |
|----------|--------|
| `GOVERNANCE_CRITICAL` | Kill switch issued/lifted, T3 approved/rejected, Soul updated, Crusader Mode, agent stopped |
| `GOVERNANCE_APPROVAL` | T3 approval requested/approved/rejected, APL rule proposed/approved/rejected |
| `SECURITY` | MCP tool blocked, injection detected, credential revoked, allowlist modified, governance write error |
| `COST_THRESHOLD` | Cost threshold crossings (configurable per-endpoint) |
| `SOUL_CHANGES` | Soul update, Soul version pinned, Crusader Mode changes |
| `TASK_LIFECYCLE` | Agent deployed, agent stopped, quest started/completed |
| `ALL` | Every event category |

### Payload Schema

```json
{
  "webhook_id": "<uuid>",
  "delivery_attempt": 1,
  "event_category": "GOVERNANCE_CRITICAL",
  "event_type": "KILL_SWITCH_ISSUED",
  "timestamp": "<ISO 8601>",
  "deployment_id": "<uuid>",
  "receipt_id": "<uuid>",
  "quest_id": "<uuid or null>",
  "operator_id": "<uuid or SYSTEM>",
  "operator_name": "<display name>",
  "payload": { /* event-specific fields */ },
  "signature": "<HMAC-SHA256>"
}
```

### Signature Verification

The `signature` field is `HMAC-SHA256(shared_secret, canonical_payload_string)`. The canonical string is the JSON-serialized `payload` field only (sorted keys, no whitespace). Recipients verify the signature before processing.

### Delivery Guarantees

- **At-least-once**: `webhook_id` + `delivery_attempt` for deduplication
- **Retry schedule**: immediate → 30s → 2m → 10m → 30m → 2h (6 attempts)
- **Timeout**: 10 seconds per attempt
- **GOVERNANCE_CRITICAL**: Always retried to exhaustion
- **WEBHOOK_DELIVERY_FAILED receipt**: Generated after all retries exhausted

### Secret Rotation

Phase 1: immediate rotation. In-flight deliveries signed with the old secret will fail verification at the recipient. Document this in integration guides. Dual-secret overlap is a Phase 2 item.

---

## Metrics API

Read-only HTTP API at `/api/metrics/*` for custom dashboard integration. Same FastAPI app, same port.

### Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/metrics/summary` | Governance health summary (kill switches, T3 pending, cost rate, agents, Soul version) |
| `GET /api/metrics/receipts` | Paginated receipt query with filters (start, end, type, quest, operator, tier) |
| `GET /api/metrics/receipts/{id}` | Full receipt payload |
| `GET /api/metrics/actions` | Aggregated action counts (group_by: risk_tier, receipt_type, operator_id, quest_id) |
| `GET /api/metrics/cost` | Cost aggregation (group_by: provider, model, quest_id) |
| `GET /api/metrics/trust-ledger` | Current Trust Ledger state |
| `GET /api/metrics/soul` | Soul document summary (version, capability count, constraint count) |
| `GET /api/metrics/kill-switches` | All kill switches with current state |
| `GET /api/metrics/hive` | Active HIVE agents and quests |
| `GET /api/metrics/webhooks/status` | Per-endpoint delivery health |

### Response Envelope

Every response includes `soul_version` for detecting governance posture changes between queries:

```json
{
  "api_version": "1.0",
  "generated_at": "<ISO 8601>",
  "deployment_id": "<uuid>",
  "soul_version": "<hash>",
  "data": { /* endpoint-specific */ },
  "pagination": { "cursor": "<opaque>", "has_more": true, "limit": 100 }
}
```

### Receipt Query Receipting

Polling endpoints (`/summary`, `/actions`) do not generate receipts by default. Specific receipt lookups (`/receipts/{id}`) generate `METRICS_API_QUERY` receipts when `OBSERVABILITY_RECEIPT_QUERIES=true`. Default: false.

---

## Dashboard Templates

Pre-built dashboard templates in `docs/dashboards/`:

| File | Platform | Dashboard |
|------|----------|-----------|
| `grafana-governance-ops.json` | Grafana 10.x | Governance Operations (10 panels) |
| `grafana-security-ops.json` | Grafana 10.x | Security Operations (6 panels) |
| `datadog-governance-ops.json` | Datadog | Governance Operations (11 widgets) |
| `datadog-security-ops.json` | Datadog | Security Operations (6 widgets) |

Templates reference the OTel metric names defined above. Import directly into your monitoring platform.

---

## SIEM Integration

### Splunk

- **Webhook path**: Point a Lancelot webhook endpoint at Splunk HEC. Subscribe to `ALL` or `SECURITY` + `GOVERNANCE_CRITICAL`.
- **OTel path**: Deploy an OTel collector with a Splunk exporter.
- **Index recommendation**: Route `lancelot.*` spans to a dedicated index.

### Microsoft Sentinel

- **Webhook path**: Logic App with HTTP trigger → custom `Lancelot_Governance_CL` table.
- **OTel path**: Azure Monitor OTLP endpoint → Application Insights.

---

## Configuration

### Feature Flag

```env
FEATURE_OBSERVABILITY=true   # Master kill switch (default: false)
```

### War Room Toggles

Individual subsystem toggles via `/api/observability/config`:
- OTel export: enabled/disabled with endpoint, auth, interval, sampling
- Webhooks: enabled/disabled with per-endpoint management
- Metrics API: enabled/disabled with rate limiting

### Files

| File | Purpose |
|------|---------|
| `src/observability/config.py` | Configuration model and persistence |
| `src/observability/otel_provider.py` | TracerProvider + MeterProvider setup |
| `src/observability/span_mapper.py` | Receipt-to-span translation |
| `src/observability/metrics.py` | 12 OTel metric instruments |
| `src/observability/receipt_bridge.py` | Receipt write path hook |
| `src/observability/webhook_engine.py` | Webhook delivery with HMAC + retry |
| `src/observability/webhook_categories.py` | Event category routing |
| `src/observability/api.py` | Configuration API endpoints |
| `src/observability/metrics_api.py` | Metrics API endpoints |
