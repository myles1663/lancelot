# Changelog

All notable changes to Project Lancelot will be documented in this file.

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
