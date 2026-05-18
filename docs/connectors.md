# Connectors

**Feature Flag:** `FEATURE_CONNECTORS` (default: `true`, requires `FEATURE_TOOLS_FABRIC`)
**Codebase:** `src/connectors/`

Connectors are governed integrations with external services. Every connector produces an HTTP request specification; the `ConnectorProxy` is the only component that makes actual HTTP calls. `GovernedConnectorProxy` adds risk classification, policy evaluation, trust tracking, and receipt emission.

---

## Architecture

```
Connector.execute()          → ConnectorResult (HTTP request spec)
  → ConnectorProxy.execute() → ConnectorResponse (actual HTTP response)
    → GovernedConnectorProxy  → Risk classification + receipt
```

Connectors never make network calls directly. The proxy pattern ensures all external communication is governed, audited, and credential-isolated.

---

## Built-In First-Party Connectors

The repo currently ships the following first-party connector implementations under `src/connectors/connectors/`:

| Connector ID | Runtime Target | Notes |
|--------------|----------------|-------|
| `calendar` | Google Calendar / Microsoft Graph / CalDAV | Multi-backend calendar connector selected by config; Google is feature-gated, Microsoft and CalDAV remain available separately |
| `discord` | Discord REST API | Bot-token auth for guild, channel, message, and reaction operations |
| `email` | Gmail / Outlook / SMTP / IMAP | Multi-backend email connector selected by config rather than a single hardcoded provider |
| `generic_rest` | Operator-defined REST target | Config-driven manifest for custom governed HTTP endpoints |
| `onedrive` | Microsoft Graph API | OneDrive file and folder request specs using a shared Microsoft Graph vault token |
| `shared_workspace` | Localhost shared workspace | Local-only connector for governed shared-workspace operations |
| `sharepoint` | Microsoft Graph API | SharePoint site, document library, and list-item request specs using a shared Microsoft Graph vault token |
| `slack` | Slack Web API | Bot-token auth for channels, threads, reactions, uploads, and posting |
| `sms` | Twilio SMS/MMS | Twilio REST API with composed Basic auth |
| `teams` | Microsoft Graph API | Teams chat/channel operations via Graph bearer tokens |
| `telegram` | Telegram Bot API | URL-token auth for bot messaging and file operations |
| `whatsapp` | WhatsApp Business Cloud API | Meta Graph-based business messaging connector |
| `x` | X API v2 | OAuth 1.0a-signed tweet/account operations |

`test_echo` also exists in-tree as an integration-test connector targeting `httpbin.org`, but it is test-only and not part of the normal operator-facing connector surface.

---

## Connector Manifest

Every connector declares an immutable manifest:

| Field | Description |
|-------|-------------|
| `id` | Connector ID (e.g., "slack", "email") |
| `name` | Display name |
| `version` | Semantic version |
| `source` | "first-party", "community", or "user" |
| `target_domains` | Domains this connector communicates with (required) |
| `required_credentials` | List of CredentialSpec (vault key, type, scopes) |
| `data_reads` | Data types read (e.g., ["emails", "contacts"]) |
| `data_writes` | Data types written (e.g., ["sent_emails"]) |
| `does_not_access` | Explicit negative declarations |

---

## ConnectorProxy

The core HTTP execution layer:

1. Look up connector in registry
2. Check rate limit
3. Route protocol:// URLs to ProtocolAdapter (SMTP/IMAP)
4. Validate URL domain against manifest's `target_domains` (exact match)
5. Inject credentials from Vault based on auth type
6. Execute HTTP request
7. Parse response (JSON if possible)
8. Return `ConnectorResponse`

### Credential Injection Modes

| Mode | Header |
|------|--------|
| Bearer/OAuth | `Authorization: Bearer {token}` |
| API Key | `X-API-Key: {value}` |
| Basic Auth | `Authorization: Basic {base64}` |
| Bot Token | `Authorization: Bot {token}` (Discord) |
| URL Token | `{token}` substituted in URL path (Telegram) |
| OAuth 1.0a | RFC 5849 HMAC-SHA1 signature (X/Twitter) |

### Domain Validation

`DomainValidator.is_domain_allowed()` performs exact hostname matching against the manifest's `target_domains`. No wildcards — a connector can only reach the domains it declares.

---

## GovernedConnectorProxy

Wraps ConnectorProxy with full governance:

1. **Risk classification** — `RiskClassifier` assigns T0–T3 tier
2. **Policy evaluation** — `PolicyEngine` checks against Soul constraints
3. **Execution** — Delegates to ConnectorProxy
4. **Trust tracking** — `record_success()` or `record_failure()` on TrustLedger
5. **Receipt emission** — T0 receipts batched, T1+ immediate

---

## Connector Operations

Each connector declares its operations with:

- `id` — Operation ID (e.g., "send_message")
- `capability` — Human-readable name
- `full_capability_id` — e.g., "connector.email.send_message"
- `default_tier` — Default RiskTier for this operation

---

## Rate Limiting

Per-connector rate limiters prevent abuse. Check is performed before credential injection and HTTP execution.

---

## Relationship to MCP

Connectors and MCP servers coexist. Connectors are lighter-weight (direct HTTP specs), while MCP servers use the full JSON-RPC 2.0 protocol with the 8-gate governance pipeline. MCP is more expensive token-wise but provides richer tool discovery and schema validation.
