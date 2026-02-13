# Changelog

All notable changes to Project Lancelot will be documented in this file.

## [8.2.0] - 2026-02-13

### Added
- **Business Automation Layer (BAL) — Phase 1 Foundation:** Core infrastructure for autonomous content
  repurposing. BAL is a 10-phase system that adds client management, content intake, repurposing,
  delivery, and billing capabilities to Lancelot
  - **`FEATURE_BAL` feature flag:** Master toggle for the entire BAL subsystem. Added to
    `RESTART_REQUIRED_FLAGS`, `reload_flags()`, and `log_feature_flags()`. Default: disabled
  - **BAL Config** (`src/core/bal/config.py`): Pydantic configuration model with sub-system flags
    (`BAL_INTAKE`, `BAL_REPURPOSE`, `BAL_DELIVERY`, `BAL_BILLING`), data directory, SMTP/Stripe
    placeholders, and client/content limits. Loaded from environment variables
  - **BAL Gates** (`src/core/bal/gates.py`): `bal_gate(subsystem)` helper checks both `FEATURE_BAL`
    master flag AND the per-subsystem flag before allowing operations
  - **BAL Database** (`src/core/bal/database.py`): SQLite database with WAL mode, thread-local
    connections, foreign keys, and schema migration runner. V1 schema creates 6 tables:
    `bal_schema_version`, `bal_clients`, `bal_intake`, `bal_content`, `bal_deliveries`,
    `bal_financial_receipts`. Stored at `data/bal/bal.sqlite`
  - **BAL Receipts** (`src/core/bal/receipts.py`): `emit_bal_receipt()` helper integrating with the
    shared receipt system. 5 new ActionType values: `BAL_CLIENT_EVENT`, `BAL_INTAKE_EVENT`,
    `BAL_REPURPOSE_EVENT`, `BAL_DELIVERY_EVENT`, `BAL_BILLING_EVENT`
  - **Composable Soul Layer System** (`src/core/soul/layers.py`): New architectural pattern for
    domain-specific governance overlays. Overlays are YAML files in `soul/overlays/` that stack
    additively on top of the base Soul. Overlays can ONLY append rules — they can NEVER remove,
    weaken, or override base Soul fields (mission, allegiance, version, scheduling limits).
    Functions: `load_overlays()`, `merge_soul()`, `load_active_soul_with_overlays()`
  - **BAL Soul Overlay** (`soul/overlays/bal.yaml`): Domain-specific governance for BAL — 3 risk
    rules (no_unauthorized_billing, no_spam, content_verification_mandatory), 2 tone invariants,
    3 memory ethics rules, 6 allowed autonomous actions, 7 requires-approval actions
  - **BAL Soul Linter Checks** (`src/core/soul/linter.py`): 2 conditional checks that only fire
    when BAL overlay is loaded — `_check_bal_billing_requires_approval` (CRITICAL) and
    `_check_bal_no_spam` (CRITICAL). No-ops for non-BAL configurations
  - **Gateway BAL Integration** (`src/core/gateway.py`): Soul overlay merge in Phase 3 (base Soul
    + active overlays), BAL initialization block after connectors (config, database, startup receipt)
  - **38 tests** across 9 test classes covering feature flags, config, gates, database, receipts,
    overlay loading, soul merge, linter checks, and full integration

## [8.1.3] - 2026-02-12

### Added
- **Chat History Persistence:** New `GET /api/chat/history` endpoint returns conversation history
  from the backend. Chat Interface now loads previous messages on mount — conversations survive
  page navigation and browser refresh
- **Recent Activity Feed (WR-10):** Command Center now shows live activity from the receipts
  system. Displays last 8 actions with status dots, action names, types, duration, and timestamps.
  Auto-refreshes every 15 seconds
- **Quick Stats — Live Data:** Actions Today and Pending counts now pull from the receipts stats
  API instead of showing placeholder dashes

### Fixed
- **Chat history recall:** Lancelot can now recall conversation history. Root cause: the librarian
  was moving `chat_log.json` out of the data directory into classification subdirectories. Fixed by
  storing in a dedicated `data/chat/` subdirectory (same pattern used for `scheduler.sqlite`)

## [8.1.2] - 2026-02-12

### Added
- **War Room Scheduler Management (WR-25):** The Scheduler page now shows live job data instead of
  "Coming Soon". Full job management from the browser:
  - **Job Cards:** Expandable cards for each registered job showing name, trigger type/schedule,
    description, skill, timeout, approval requirements, and run history
  - **Enable/Disable Toggles:** Toggle switches to enable or disable individual jobs, persisted
    to SQLite
  - **Run Now:** Manual trigger button with confirmation dialog. Executes through the full gating
    pipeline (gates, approvals, skill execution) with result feedback banner
  - **Summary Metrics:** MetricCard row showing total jobs, enabled count, and scheduler status
  - **Auto-Refresh:** 10-second polling keeps job status current
- **Scheduler Management API** (`src/core/scheduler_api.py`):
  - `GET /api/scheduler/jobs` — list all jobs with enabled count
  - `GET /api/scheduler/jobs/{id}` — get single job details
  - `POST /api/scheduler/jobs/{id}/enable` — enable a job
  - `POST /api/scheduler/jobs/{id}/disable` — disable a job
  - `POST /api/scheduler/jobs/{id}/trigger` — manually execute a job through JobExecutor

### Fixed
- **Scheduler config path:** Fixed `config_dir` from absolute `/home/lancelot/config` (didn't exist
  in container) to relative `config` (resolves to `/home/lancelot/app/config/`)
- **Scheduler database persistence:** Moved SQLite to dedicated `data/scheduler/` subdirectory to
  prevent the librarian from relocating the database file

## [8.1.1] - 2026-02-12

### Added
- **War Room Connector Management UI:** New dedicated Connectors page in the War Room for configuring
  all 8 communication connectors from the browser:
  - **Connector Cards:** Each connector displayed with name, status indicator, operation count, and
    expandable details showing target domains and data access summaries (reads/writes/does_not_access)
  - **Enable/Disable Toggles:** Toggle switches to enable or disable individual connectors, with
    confirmation dialog for disabling. State persisted to `connectors.yaml`
  - **Backend Selector:** Dropdown for multi-backend connectors (Email: Gmail/Outlook/SMTP) to switch
    between provider backends
  - **Credential Management:** Inline expandable forms for entering, saving, and deleting credentials
    per connector. Credentials stored in the encrypted vault. Masked password inputs with type badges
    and required indicators
  - **Connection Testing:** "Test Connection" button per connector that validates stored credentials
    against the live service
  - **Auto-Refresh:** 10-second polling interval keeps connector status current
  - **Summary Bar:** At-a-glance counts of total, enabled, and configured connectors
- **Connectors Management API** (`src/core/connectors_api.py`):
  - `GET /api/connectors` — list all connectors with manifest info, enabled state, credential status,
    and backend selection
  - `POST /api/connectors/{id}/enable` — enable a connector
  - `POST /api/connectors/{id}/disable` — disable a connector
  - `POST /api/connectors/{id}/backend` — set backend for multi-backend connectors
- **Credential API mounted:** Pre-existing `credential_api.py` (store/status/delete/validate) now
  wired into the gateway and accessible from the War Room
- **`apiDelete` utility:** New DELETE method added to the War Room API client

### Changed
- **Gateway:** Connector management and credential APIs always mounted regardless of FEATURE_CONNECTORS
  flag — the flag now only gates runtime connector registration, not configuration access
- **Sidebar navigation:** Connectors page added to SYSTEM group in War Room sidebar

## [8.1.0] - 2026-02-12

### Added
- **Connector Expansion — 6 New Connectors:** Lancelot now supports 8 communication connectors
  covering every major enterprise and consumer channel:
  - **TeamsConnector** (10 operations): Microsoft Teams via Graph API v1.0 — list teams/channels,
    read/post channel messages, read/send chat messages, reply, delete
  - **DiscordConnector** (9 operations): Discord via REST API v10 — list guilds/channels,
    read/post/edit/delete messages, add/remove reactions. Includes rate limit group metadata
  - **WhatsAppConnector** (8 operations): WhatsApp Business via Meta Cloud API — send text/template/
    media/interactive messages, mark read, get/upload media, get business profile. Template message
    support with language codes and component parameters
  - **SMSConnector** (6 operations): Twilio REST API — send SMS/MMS, get/list/delete messages,
    get media. Supports both From number and MessagingServiceSid routing
  - **Email Outlook Backend:** EmailConnector now supports `backend="outlook"` via Microsoft Graph
    API — same 7 operations as Gmail with Outlook-specific URLs and `$search` OData queries
  - **Email SMTP/IMAP Backend:** EmailConnector now supports `backend="smtp"` for direct SMTP/IMAP
    using Python stdlib only — produces `protocol://` URL markers for ProtocolAdapter routing
- **ProtocolAdapter:** New SMTP/IMAP translation layer (`src/connectors/protocol_adapter.py`) that
  executes `protocol://smtp` and `protocol://imap` ConnectorResults using Python's `smtplib` and
  `imaplib`. Zero external dependencies. Supports send, reply, list, fetch, search, delete, move
- **Bot Token Auth:** ConnectorProxy now supports `bot_token` credential type for Discord
  (`Authorization: Bot {token}`)
- **Form-Encoded Body Support:** ConnectorProxy detects `Content-Type: application/x-www-form-urlencoded`
  and sends body as `data=` instead of `json=` (required for Twilio)
- **Protocol Routing:** ConnectorProxy routes `protocol://` URLs to ProtocolAdapter instead of HTTP,
  incrementing request count for audit consistency
- **232 connector tests** across 10 test files covering manifests, operations, execution, auth types,
  form encoding, protocol routing, credential propagation, and cross-connector integration

### Changed
- **connectors.yaml** bumped to version 2.0 with entries for teams, discord, whatsapp, and sms
  connectors including per-connector rate limits
- **ConnectorProxy** docstring updated to document all 4 auth types and 3 body encodings

## [8.0.5] - 2026-02-12

### Added
- **Crusader Mode — Auto Flag Configuration:** Activating Crusader Mode now automatically toggles
  feature flags to maximize capabilities (AGENTIC_LOOP, TASK_GRAPH_EXECUTION, TOOLS_CLI_PROVIDERS,
  TOOLS_NETWORK, CONNECTORS, LOCAL_AGENTIC on; RISK_TIERED_GOVERNANCE, APPROVAL_LEARNING off).
  All flags are snapshotted and restored on deactivation
- **Crusader Mode — Soul Switching:** Crusader Mode activation switches to a dedicated Crusader soul
  constitution with autonomous posture, expanded allowed actions, and reduced approval gates. Original
  soul version is restored on deactivation
- **Crusader Soul:** New `soul_crusader.yaml` — autonomous constitution with elevated scheduling limits
  (10 concurrent jobs, 600s max), relaxed risk rules, and auto-approve escalation. Only
  credential_rotation and financial_transaction still require approval
- **Crusader API Endpoints:** `POST /api/crusader/activate` and `POST /api/crusader/deactivate` for
  direct Crusader Mode control with detailed status response (flag overrides count, overridden flag
  names, soul override version, activation timestamp)
- **Enhanced Crusader Status:** `GET /crusader_status` now returns full state including flag_overrides,
  soul_override, overridden_flags list, and activated_at timestamp

### Changed
- **Controls Panel:** Now calls dedicated API endpoints instead of sending chat messages for
  Crusader Mode. Shows activation summary (flags changed, soul switched) and live status while active
- **Kill Switches — Crusader Banner:** Shows alert banner when Crusader Mode is active with list of
  overridden flags. Overridden toggles are locked with "crusader" badge and cursor-not-allowed state
- **Soul Inspector — Crusader Banner:** Shows override banner when Crusader soul is active with
  original version info. YAML editor disabled during Crusader Mode to prevent confusion
- **CrusaderStatusResponse type** extended with activated_at, flag_overrides, soul_override, and
  overridden_flags fields. New CrusaderActionResponse type for activate/deactivate endpoints

---

## [8.0.4] - 2026-02-12

### Added
- **Soul Inspector — Constitution Viewer:** Full structured view of the active soul document with
  collapsible sections for Mission & Identity, Autonomy Posture (level badge, allowed/requires lists),
  Risk Rules (enforced/disabled status), Approval Rules, Tone Invariants, Memory Ethics, and
  Scheduling Boundaries
- **Soul Inspector — YAML Editor:** Tab-based view/edit interface. YAML editor creates amendment
  proposals validated by the soul linter. Proposals must be approved then activated (two-stage workflow)
- **Soul Inspector — Proposal Actions:** Approve and Activate buttons on pending proposals with
  confirmation dialogs, diff summary badges (added/changed/removed), author/timestamp display
- **Soul API — content endpoint:** `GET /soul/content` returns full parsed soul document as JSON
  plus raw YAML text
- **Soul API — propose endpoint:** `POST /soul/propose` creates amendment proposals from edited YAML
  with linter validation (rejects critical issues, surfaces warnings)

---

## [8.0.3] - 2026-02-12

### Added
- **Network Allowlist Editor:** Inline domain editor in Kill Switches page under FEATURE_NETWORK_ALLOWLIST.
  Loads/saves `config/network_allowlist.yaml` via new API endpoints. One domain per line, exact match,
  comment support, save button with dirty detection
- **Allowlist API:** `GET /api/flags/network-allowlist` and `PUT /api/flags/network-allowlist` for
  reading/updating the global default domain allowlist
- **Default allowlist config:** `config/network_allowlist.yaml` with 6 default domains
  (GitHub, Anthropic, Google AI, Telegram)
- **apiPut** HTTP method added to the API client layer

### Fixed
- **False conflict removed:** FEATURE_TOOLS_NETWORK no longer shows conflict with FEATURE_NETWORK_ALLOWLIST.
  These flags are complementary — TOOLS_NETWORK enables sandbox network access while NETWORK_ALLOWLIST
  restricts it to safe domains
- **Route ordering:** Allowlist routes registered before `/{name}/*` pattern routes to prevent
  FastAPI parameter capture

### Changed
- FEATURE_NETWORK_ALLOWLIST description updated to reference the inline editor
- FEATURE_TOOLS_NETWORK warning updated to recommend enabling allowlist alongside it

---

## [8.0.2] - 2026-02-12

### Added
- **Kill Switches — Expandable flag details:** Each feature flag now has click-to-expand rows showing
  full description, warning box, dependency status badges (met/unmet with checkmark/X), and conflict
  indicators. Flags grouped by category (Core Subsystem, Tool Fabric, Runtime, Governance,
  Capabilities, Intelligence) with sorted display order
- **Kill Switches — Toggle switches:** Interactive toggle buttons for all feature flags with
  confirmation dialog for restart-required flags, restart banner notification, loading states
- **FLAG_META registry:** Comprehensive metadata for all 26 feature flags in `flags_api.py` —
  descriptions, categories, dependency lists, conflict lists, and safety warnings
- **Flags API — toggle endpoint:** `POST /api/flags/{name}/toggle` and `POST /api/flags/{name}/set`
  for runtime flag manipulation
- **feature_flags.py — runtime toggle:** `toggle_flag()`, `set_flag()`, and `RESTART_REQUIRED_FLAGS`
  for safe runtime flag changes with env var sync

### Changed
- Flags API response now includes description, category, requires, conflicts, and warning metadata
- FlagInfo TypeScript interface updated with all metadata fields

---

## [8.0.1] - 2026-02-12

### Fixed
- **Health endpoint crash:** `/health` and `/ready` no longer crash on missing `memory_collection`
  attribute — uses `getattr()` with `_memory_enabled` flag, treats "disabled" as non-degraded
- **Cost Tracker field mismatch:** Fixed summary metric cards to match backend field names
  (`total_requests`, `total_tokens_est`, `total_cost_est`, `avg_elapsed_ms`)
- **Cost Tracker monthly rendering:** Replaced raw `JSON.stringify` dump with proper formatted tables
  (monthly totals, by-model breakdown, by-day breakdown)
- **Cost Tracker fallback data:** Per-lane and per-model sections now fall back to summary/monthly
  data when dedicated endpoints return empty
- **Memory Panel disabled state:** Shows clear "Memory vNext Disabled" message with enable
  instructions when `FEATURE_MEMORY_VNEXT=false`
- **KillSwitches null safety:** Added optional chaining for onboarding/cooldown data access

### Added
- **Tools API** (`/api/tools/*`): New backend router exposing Tool Fabric provider health,
  routing summary, and configuration status — 2 providers discovered (local_sandbox, ui_templates)
- **Flags API** (`/api/flags`): New endpoint returning all feature flag values from environment
- **Tool Fabric page:** Enhanced with provider health matrix, fabric configuration panel,
  and system component status (previously only showed broken health endpoint data)
- **Kill Switches page:** Now displays real-time feature flag values from `/api/flags` with
  enabled/disabled status indicators (previously showed static "env-configured" placeholders)
- TypeScript API clients for tools (`tools.ts`) and flags (`flags.ts`)

### Changed
- gateway.py: Health check version updated to 8.0, memory component uses `_memory_enabled` flag
- gateway.py: Tools API and Flags API routers mounted at startup

---

## [8.0.0] - 2026-02-11

### Added
- **War Room React SPA:** Migrated operator interface from Streamlit to React 18 + TypeScript + Tailwind CSS
  - **Phase 0 — Infrastructure:** Vite project scaffold, Tailwind design system with spec color palette,
    6 shared components (MetricCard, TierBadge, StatusDot, ProgressBar, ConfirmDialog, EmptyState),
    typed API client layer covering 40+ existing endpoints, shell layout with 240px collapsible sidebar,
    56px header, notification tray footer, React Router with 13 routes, keyboard shortcuts (Ctrl+1-9),
    FastAPI static mount at `/war-room/`, Dockerfile updated with Node.js 20 build step
  - **Phase 1 — Command Center:** Live VitalsBar polling /health, /health/ready, /soul/status every 5s
    (Identity Bonded, Armor Integrity, Connection, Defense Posture with Crusader violet pulse),
    chat interface with file upload and Crusader mode message styling,
    Controls panel (Crusader toggle, Pause, Emergency Stop) with confirmation dialogs,
    WebSocket infrastructure (/ws/warroom) with EventBus pub/sub and auto-reconnect hook
  - **Phase 2 — Governance Visibility:** 4 new backend API routers (receipts_api, governance_api,
    trust_api, apl_api), Governance Dashboard with metrics + approval queue + decision log,
    Receipt Explorer with searchable/filterable table and expandable rows,
    Soul Inspector with version viewer and proposal management,
    Trust Ledger with per-capability table, graduation proposals, and timeline,
    APL Panel with rules management (pause/resume/revoke), proposals, circuit breakers, decisions
  - **Phase 4 — Operational Completeness:** Tool Fabric (component health, provider status),
    Memory (core blocks, quarantine queue, full-text search), Scheduler (status and job placeholder),
    Setup & Recovery (onboarding status, 5 recovery commands), Cost Tracker (summary, lanes,
    models, monthly), Kill Switches (system state, feature flags), Business Dashboard (BAL placeholder)
  - All 12+ tabs render with live data or appropriate empty states
  - Build output: 234KB JS + 18KB CSS, 81 modules, TypeScript strict mode

### Changed
- gateway.py: CORS updated for Vite dev server (localhost:5173), WebSocket /ws/warroom endpoint added
- Dockerfile: Node.js 20 installed, React SPA built during Docker image build
- TierBadge component accepts both RiskTier string and numeric tier values

---

## [7.0.0] - 2026-02-05

### Added
- **Memory vNext (vNext3):** Tiered memory system with working, episodic, and archival storage
  - Core block store (persona, human, mission, operating_rules, workspace_state)
  - Commit-based editing with begin/finish/rollback semantics and snapshot isolation
  - Context compiler with per-type token budgets
  - Governed self-edits with provenance tracking and quarantine flow
  - SQLite persistence with thread-safe connection management
  - Full-text search with position-based relevance scoring
  - REST API for memory operations
  - Memory panel in War Room dashboard
- **Tool Fabric:** Provider-agnostic tool execution subsystem
  - 7 capability interfaces (ShellExec, RepoOps, FileOps, WebOps, UIBuilder, DeployOps, VisionControl)
  - LocalSandboxProvider with Docker-based execution
  - TemplateScaffolder with 4 template packs (Next.js, FastAPI, Streamlit, Flask)
  - AntigravityUIProvider for generative UI scaffolding
  - AntigravityVisionProvider for vision-based UI control
  - PolicyEngine with command denylist, path traversal detection, workspace boundaries
  - ProviderRouter with capability-based selection and health-aware failover
  - ToolReceipt and VisionReceipt for audit trail
  - Tool Fabric panel in War Room dashboard
  - 6 feature flags for granular subsystem control

### Security
- 96 security vulnerabilities remediated across two hardening passes
- Symlink-safe workspace boundary enforcement via `os.path.realpath()`
- Command denylist with shlex-based token matching (not substring)
- Docker env var sanitization via `shlex.quote()`
- Atomic file writes (temp + `os.replace()`) for crash-safe persistence
- Thread-safe singletons with double-checked locking
- Skill factory code generation sanitized against docstring breakout injection
- Workspace path validation in all file operation providers
- Error message sanitization (no internal paths/stack traces in API responses)
- Race condition fixes in memory service, health monitor, and soul proposals

### Fixed
- Memory commit rollback now restores both core blocks and item-level edits
- Snapshot eviction bounded at 50 with LRU eviction (fixes unbounded memory growth)
- Vision provider reuses pages instead of creating blank pages per operation
- Search index returns position-based relevance scores (not all 1.0)
- Quarantine approval looks up items across all tiers (not hardcoded to working)
- SQLite connections cleaned up via `atexit` registration
- `rethink` operation raises explicit error instead of silent no-op
- Vision `detected_elements` properly populated from search results
- Silent provenance parsing failure now returns HTTP 400
- Corrupted soul proposals file logged at error level instead of silently dropped

### Removed
- Dead code cleanup: 27 unused imports, classes, and utility functions removed

---

## [6.0.0] - 2026-02-04

### Added
- **Soul (Constitutional Identity):** Versioned YAML governance with amendment workflow
- **Skills:** Modular capabilities with manifests, factory pipeline, marketplace governance
- **Heartbeat:** Liveness/readiness health monitoring with state transition receipts
- **Scheduler:** Cron/interval job scheduling with gating pipeline
- Feature flags for independent subsystem activation
- War Room panels for Soul, Skills, Health, and Scheduler
- 317 tests for vNext2 subsystems
- 42 hardening regression tests

---

## [5.0.0] - 2026-02-04

### Added
- Soul, Skills, Heartbeat, and Scheduler subsystems (vNext2 integration)
- Constitutional governance with 5 invariant checks
- Skill registry with ownership and signature tracking
- Background health monitor with configurable check functions
- SQLite-backed job persistence with run history

---

## [4.0.0] - 2026-02-03

### Added
- Multi-provider model routing (Gemini, OpenAI, Anthropic)
- Four prioritized routing lanes: local_redaction, local_utility, flagship_fast, flagship_deep
- Local GGUF model service for utility tasks (classify, extract, summarize, redact)
- Risk-based escalation from fast to deep lane
- UsageTracker with per-lane cost estimation
- Control plane REST API endpoints
- 721 tests across 18 v4 test files

---

## [1.0.0] - 2026-02-02

### Added
- Initial release
- Gemini 2.0 Flash integration
- War Room UI (Streamlit)
- Native launcher (pywebview)
- Telegram integration
- Google Chat integration (OAuth)
- Crusader Mode (autonomous execution)
- Planner and Verifier agents
- File management (Librarian)
- Action receipts and audit trail
- Sandboxed code execution
