# Lancelot Changelog

## v7.0.3 — File/Image Sharing + Shared Workspace (2026-02-11)

### Summary

Added multimodal file and image sharing across all interfaces (War Room, Telegram, Gateway
API) plus a shared Desktop workspace folder. Users can now upload images for Gemini vision
analysis, share documents for context, and exchange project files through a shared folder.

### Shared Workspace

A new Docker volume mount maps `C:\Users\...\Desktop\Lancelot Workspace` to
`/home/lancelot/workspace` inside the container. Both the user and Lancelot can read/write
files in this folder for seamless project collaboration.

- **`docker-compose.yml`**: Added workspace bind-mount volume

### Multimodal Chat Pipeline

Threaded file/image attachments through all layers: UI/Telegram -> Gateway -> Orchestrator -> Gemini.

- **`src/core/orchestrator.py`**:
  - Added `ChatAttachment` dataclass (`filename`, `mime_type`, `data`)
  - Extended `chat()` with `attachments` parameter
  - Images and PDFs sent to Gemini as `types.Part(inline_data=...)` for native vision
  - Text documents decoded and appended to context
  - `_text_only_generate()` and `_agentic_generate()` accept `image_parts` parameter
  - Vision input forces Gemini routing (skips local model which has no vision)
  - System instruction updated with workspace awareness

- **`src/core/gateway.py`**:
  - New `POST /chat/upload` endpoint (multipart/form-data)
  - Accepts text + files, creates `ChatAttachment` objects
  - Optional `save_to_workspace` flag copies uploads to shared folder
  - Request size limit increased to 20MB
  - Imports: `File`, `UploadFile`, `Form` from FastAPI

### War Room File Upload

- **`src/ui/war_room.py`**:
  - Added `st.file_uploader()` widget in Command Center (images, PDFs, code, text)
  - "Save to Workspace folder" checkbox
  - `_chat_with_files_via_gateway()` helper posts multipart to `/chat/upload`
  - Attachment names shown in chat history

### Telegram Photo/Document Support

- **`src/integrations/telegram_bot.py`**:
  - `_handle_photo()`: Downloads photo via existing `_download_file()`, sends to Gemini vision
  - `_handle_document()`: Downloads document, creates `ChatAttachment`, routes to orchestrator
  - `_handle_update()` extended to detect `photo` and `document` message types

### Supported File Types

| Category | Types | Processing |
|---|---|---|
| Images | PNG, JPG, JPEG, GIF, WebP | Gemini vision (inline_data) |
| Documents | PDF | Gemini native PDF processing |
| Text | TXT, MD, PY, JSON, CSV, YAML, XML, HTML, CSS, JS, TS | UTF-8 decode into context |

---

## v7.0.2 — War Room Cost Tracker Panel (2026-02-10)

### Summary

Added a real-time token and cost tracking panel to the War Room. Users can now monitor
monthly API costs, per-model token usage, and estimated savings from local model routing
without needing to visit provider dashboards.

### New Files

- **`src/core/usage_persistence.py`**: Monthly usage persistence layer. Stores per-model,
  per-day usage data to `lancelot_data/usage_history.json` so cost data survives container
  restarts. Thread-safe with periodic flush to disk.

- **`src/ui/panels/cost_panel.py`**: War Room Cost Tracker panel. Displays monthly cost
  KPIs, per-model breakdown table, daily cost trend bar chart, month selector, and reset
  controls. Fetches data from `/usage/*` control-plane endpoints.

### Modified Files

- **`src/core/usage_tracker.py`**: Enhanced with per-model tracking (`_models` dict),
  `record_simple()` method for direct LLM calls, `model_breakdown()` query, persistence
  hook via `set_persistence()`, and updated `summary()` to include `by_model`.

- **`src/core/control_plane.py`**: Added `set_usage_tracker()` / `get_usage_tracker()`
  as standalone alternative to model router wiring. Updated all `/usage/*` endpoints to
  use the standalone tracker. Added `/usage/models` and `/usage/monthly` endpoints.

- **`src/core/gateway.py`**: Phase 6b wires `UsageTracker` + `UsagePersistence` at
  startup, registers with control plane, injects tracker into orchestrator. Shutdown
  handler flushes persistence to disk.

- **`src/core/orchestrator.py`**: Added `usage_tracker` attribute. Instrumented all
  three LLM call paths (V8 local agentic, V6 Gemini agentic, main chat) to call
  `usage_tracker.record_simple()` alongside existing governor logging.

- **`src/ui/war_room.py`**: Added 5th "Cost Tracker" tab importing and rendering
  `render_cost_panel`.

- **`src/ui/panels/__init__.py`**: Exported `render_cost_panel`.

### API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/usage/summary` | GET | Full usage summary (now works without ModelRouter) |
| `/usage/lanes` | GET | Per-lane breakdown |
| `/usage/models` | GET | Per-model breakdown |
| `/usage/savings` | GET | Local model savings estimate |
| `/usage/monthly` | GET | Persistent monthly data (survives restarts) |
| `/usage/reset` | POST | Reset in-memory counters |

### Verification

- `curl http://localhost:8000/usage/summary` returns real data
- `curl http://localhost:8000/usage/monthly` returns monthly breakdown
- War Room "Cost Tracker" tab shows KPIs, model table, daily chart
- Data persists across container restarts via `usage_history.json`

---

## v7.0.1 — Memory vNext Activation + Identity Fix (2026-02-10)

### Summary

Configuration fix enabling the Memory vNext subsystem and correcting the owner identity
in core memory. Memory vNext was disabled by feature flag (`FEATURE_MEMORY_VNEXT=false`),
preventing the CoreBlockStore, ContextCompiler, and Memory API from initializing. This
caused Lancelot to report it could not access memory and prevented the `human` core block
from being bootstrapped from USER.md.

### Root Cause

1. `FEATURE_MEMORY_VNEXT=false` in `.env` disabled the entire memory subsystem
2. The gateway bootstrap (which syncs USER.md → human core block) is gated behind
   `FEATURE_MEMORY_VNEXT`, so it never ran
3. The `human` core block in `core_blocks.json` was stale and out of sync with USER.md
4. The librarian-filed copy `Personal/USER.md` was out of sync with the canonical USER.md

### Changes

- **`.env`**: Set `FEATURE_MEMORY_VNEXT=true` (P0 subsystem, should be enabled per architecture)
- **`lancelot_data/Personal/USER.md`**: Synced with canonical USER.md
- **`lancelot_data/memory/core_blocks.json`**: Auto-fixed by bootstrap on restart —
  `human` block re-synced from USER.md (v22)

### Fix: Setup & Recovery Panel Stuck at WELCOME

The War Room "Setup & Recovery" panel showed onboarding state stuck at `WELCOME` even
when the system was fully operational. Root cause: `OnboardingOrchestrator` determines
state dynamically from files/env, but `OnboardingSnapshot` (used by recovery panel and
API) reads from `onboarding_snapshot.json` which was never written during normal
onboarding flow.

**Fix:** Added `_sync_snapshot()` to `OnboardingOrchestrator.__init__()` that writes
the dynamically determined state back to the snapshot file at startup. This ensures the
recovery panel, control plane API, and health system all reflect the actual system state.

- **`src/ui/onboarding.py`**: Added `_sync_snapshot()` method, called from `__init__`

### Verification

- Gateway logs confirm: `Memory vNext initialized and wired.`
- Bootstrap log: `Updated core block human (v22, 22 tokens, by system)`
- Feature flags: `MEMORY_VNEXT=True`
- Onboarding API returns `state: READY` with provider and credential info

---

## v7.0.0 — Memory vNext + Tool Fabric + Security Hardening

**Specs:**
- [Tool Fabric Spec](specs/Lancelot_ToolFabric_Spec.md)
- [Memory vNext Spec](specs/Lancelot_vNext3_Spec_Memory_BlockMemory_ContextCompiler.md)

**Blueprints:**
- [Tool Fabric Blueprint](blueprints/Lancelot_ToolFabric_Blueprint.md)
- [Memory vNext Blueprint](blueprints/Lancelot_vNext3_Blueprint_Memory_BlockMemory_ContextCompiler.md)

### Summary

Major release combining three upgrades: (1) Tool Fabric — a capability-based abstraction
layer decoupling Lancelot from vendor-specific tooling with stable capability interfaces,
Docker sandboxing, and policy enforcement; (2) Memory vNext — tiered commit-based memory
with working/episodic/archival storage, context compiler, and governed self-edits;
(3) Security Hardening — 96 vulnerabilities remediated across two comprehensive passes.

### Prompts Completed

#### Prompt 1 — Contracts + Receipts (Foundation)
- Capability interfaces (Protocol classes): ShellExec, RepoOps, FileOps, WebOps, UIBuilder, DeployOps, VisionControl
- Result types: ExecResult, FileChange, PatchResult, ScaffoldResult, VisionResult
- Provider types: ProviderHealth, ProviderState, BaseProvider
- Intent and policy: ToolIntent, PolicySnapshot, RiskLevel
- Tool receipts: ToolReceipt, VisionReceipt with redaction and bounding
- Feature flags: FEATURE_TOOLS_FABRIC, FEATURE_TOOLS_CLI_PROVIDERS, FEATURE_TOOLS_ANTIGRAVITY, FEATURE_TOOLS_NETWORK, FEATURE_TOOLS_HOST_EXECUTION
- 63 unit tests for schema validation and JSON serialization

#### Prompt 2 — LocalSandboxProvider MVP
- Docker-based tool runner implementation
- ShellExec capability: run commands with stdout/stderr capture, timeout, output bounding
- RepoOps capability: git status, diff, apply_patch, commit, branch, checkout
- FileOps capability: read, write (atomic), list, delete with file hashing
- Security: command denylist (rm -rf, mkfs, fork bomb), configurable allowlist
- Health checks: Docker availability, image status, provider state tracking
- 39 unit tests + 7 integration tests (Docker required)

#### Prompt 3 — Policies
- PolicyEngine for centralized security enforcement
- Command policies: allowlist/denylist evaluation, case-insensitive matching
- Risk assessment: LOW (read), MEDIUM (modify), HIGH (network/delete/deploy)
- Path security: traversal detection (encoded/double-encoded), workspace boundary
- Sensitive path patterns: .env, .ssh, .aws, credentials, secrets.yaml
- Network policy: disabled by default, capability-based exceptions
- Redaction: passwords, API keys, tokens, paths
- PolicySnapshot for audit trail
- 63 unit tests covering all security gates

#### Prompt 4 — Router + Health
- HealthMonitor for provider discovery and health tracking
- Health probes with caching, TTL, and retry logic
- ProviderRouter for capability-based provider selection
- Priority-based selection with failover to healthy providers
- Policy engine integration for intent-based routing
- RouteDecision captures selection rationale and alternatives tried
- Global singleton instances with thread safety
- 43 unit tests for routing and health monitoring

#### Prompt 5 — Orchestrator Wiring
- ToolFabric main orchestration class coordinating all components
- Provider registration and management
- Command execution through policy→router→provider pipeline
- Repository operations: git_status, git_diff, git_apply_patch, git_commit
- File operations: read_file, write_file, list_files with policy enforcement
- Health status and probing API
- Safe mode toggle for restricted provider selection
- Global singleton with thread-safe initialization
- Receipt generation with exec results and policy snapshots
- 36 integration tests for full Tool Fabric workflow

#### Prompt 6 — RepoOps + FileOps Integration Tests
- Comprehensive RepoOps tests: status, diff, apply_patch, commit, branch, checkout
- Comprehensive FileOps tests: read, write, list, delete, apply_diff
- File hash tracking verification: hash_before and hash_after in FileChange
- Apply patch + commit workflow with complete hash verification
- Receipt integration tests with file change serialization
- Path traversal blocking tests through ToolFabric
- Error handling tests for edge cases
- 49 integration tests for repository and file operations

#### Prompt 7 — UIBuilder Templates
- TemplateScaffolder provider implementing UIBuilderCapability
- Template packs: nextjs_shadcn_dashboard, fastapi_service, streamlit_dashboard, flask_api
- DETERMINISTIC mode scaffolding with spec substitution
- list_templates() returning all available templates with metadata
- verify_build() checking Python syntax and package.json validity
- Template content generation with project name, title, description interpolation
- 45 unit tests for template scaffolding and verification

#### Prompt 8 — Antigravity UIBuilder
- AntigravityUIProvider for generative UI scaffolding
- GENERATIVE mode with AI-powered project generation
- Graceful fallback to templates when Antigravity unavailable
- Feature flag integration (FEATURE_TOOLS_ANTIGRAVITY)
- GenerationReceipt for audit trail with prompt/spec hashes
- Prompt-to-template mapping for intelligent fallback
- Health checks with availability and fallback status
- 38 tests for generative scaffolding and fallback

#### Prompt 9 — VisionControl
- AntigravityVisionProvider for vision-based UI control
- VisionControlCapability: capture_screen, locate_element, perform_action, verify_state
- Explicit failure when Antigravity unavailable (no silent downgrade)
- AntigravityUnavailableError and VisionOperationError exceptions
- VisionReceipt with screenshot hashes (not raw bytes)
- CSS selector and natural language element location
- Click, type, drag, scroll action support
- State verification with expected/actual comparison
- 35 tests for vision control operations

#### Prompt 10 — War Room Panel
- ToolsPanel data provider for War Room integration
- Provider health display with state icons (healthy/degraded/offline)
- Health summary with counts and overall status
- Routing policy summary with capability→provider mapping
- Safe Mode toggle (disables optional providers)
- Receipt management with capability/provider filtering
- Receipt callbacks for real-time updates
- render_tools_panel() Streamlit render function
- War Room integration with new "Tool Fabric" tab
- Global singleton with thread-safe initialization
- 50 tests for panel functionality

#### Prompt 11 — Hardening
- Command denylist regression tests (25+ dangerous patterns)
- Path traversal tests (obvious, encoded, double-encoded)
- Network policy enforcement tests
- Sensitive data redaction tests (passwords, API keys, tokens, AWS keys)
- Provider offline degradation tests (failover, fallback)
- All-providers-offline scenario tests (graceful error handling)
- Malformed provider output tests (receipts remain valid)
- Intent-based policy tests (VisionControl requires approval)
- Policy snapshot serialization tests
- Vulnerability regression tests (shell injection, null bytes, unicode)
- 105 security regression tests

### New Files

- `docs/specs/Lancelot_ToolFabric_Spec.md` — Tool Fabric specification
- `docs/blueprints/Lancelot_ToolFabric_Blueprint.md` — Tool Fabric blueprint
- `src/tools/__init__.py` — Tool Fabric module exports
- `src/tools/contracts.py` — Capability interfaces and type definitions
- `src/tools/receipts.py` — Tool-specific receipt extensions
- `src/tools/providers/__init__.py` — Provider module placeholder
- `src/tools/providers/local_sandbox.py` — Docker-based tool runner (Prompt 2)
- `src/tools/policies.py` — Policy engine with security gates (Prompt 3)
- `src/tools/health.py` — Health monitoring and probing (Prompt 4)
- `src/tools/router.py` — Provider routing and failover (Prompt 4)
- `tests/test_tool_contracts.py` — 63 unit tests for contracts and receipts
- `tests/test_local_sandbox.py` — 46 tests for LocalSandboxProvider
- `tests/test_tool_policies.py` — 63 tests for policy engine
- `tests/test_tool_router.py` — 43 tests for router and health
- `src/tools/fabric.py` — Main Tool Fabric orchestrator (Prompt 5)
- `tests/test_tool_fabric_integration.py` — 36 integration tests (Prompt 5)
- `tests/test_repo_file_ops.py` — 49 integration tests for RepoOps + FileOps (Prompt 6)
- `src/tools/providers/ui_templates.py` — Template-based UI scaffolder (Prompt 7)
- `tests/test_ui_templates.py` — 45 tests for UIBuilder templates (Prompt 7)
- `src/tools/providers/ui_antigravity.py` — Antigravity generative UI provider (Prompt 8)
- `tests/test_ui_antigravity.py` — 38 tests for Antigravity UIBuilder (Prompt 8)
- `src/tools/providers/vision_antigravity.py` — Antigravity vision control provider (Prompt 9)
- `tests/test_vision_control.py` — 35 tests for VisionControl (Prompt 9)
- `src/ui/panels/tools_panel.py` — Tool Fabric panel for War Room (Prompt 10)
- `tests/test_tools_panel.py` — 50 tests for Tools Panel (Prompt 10)
- `tests/test_tool_fabric_hardening.py` — 105 security regression tests (Prompt 11)

### Memory vNext Prompts Completed

#### Prompt 12 — Core Block Store + Schemas
- CoreBlockStore with in-memory block storage
- CoreBlock, CoreBlockType, MemoryItem, CompiledContext schemas
- MemoryStoreManager for tiered SQLite persistence
- Full-text search index with position-based relevance scoring

#### Prompt 13 — Commit Pipeline + Rollback
- CommitManager with begin/finish/rollback semantics
- Snapshot isolation for concurrent edit safety
- MemoryEditOp: insert, update, delete, rethink operations
- Item-level undo log for rollback of tiered edits
- MAX_RETAINED_SNAPSHOTS (50) with LRU eviction

#### Prompt 14 — Context Compiler
- ContextCompilerService assembling memory into token-budgeted context
- Per-block-type token budgets with priority ordering
- Tier-based item inclusion (working > episodic > archival)
- Shared store instance support to prevent duplicate singletons

#### Prompt 15 — Memory API + Panel
- FastAPI router for memory operations (status, edit, compile, search, quarantine, rollback)
- Thread-safe singleton initialization with double-checked locking
- Memory panel in War Room for tier browsing and quarantine management
- Scheduled maintenance jobs for cleanup and archival promotion

#### Prompt 16 — Security Hardening (Pass 1)
- 16 issues fixed: 4 critical, 5 high, 7 medium
- Skill factory code injection via description sanitization
- Unsigned skill execution warning receipts
- Duplicate store instance consolidation
- Incomplete rollback for item edits

#### Prompt 17 — Security Hardening (Pass 2)
- 80 issues fixed: 13 security, 40 bugs, 27 dead code
- Symlink-safe workspace boundary enforcement
- Provider file ops workspace validation
- Race condition fixes (memory service, health monitor, soul proposals)
- Atomic file writes for registry persistence
- Command denylist with shlex token matching
- Docker env var value sanitization
- Vision provider page reuse and element detection fixes
- Error message sanitization across all API endpoints
- Dead code cleanup: 27 unused imports, classes, and functions removed

### Modified Files

- `src/core/feature_flags.py` — Added Tool Fabric and Memory vNext feature flags
- `src/ui/panels/__init__.py` — Added ToolsPanel exports (Prompt 10)
- `src/ui/war_room.py` — Added Tool Fabric and Memory tabs
- `src/core/memory/*.py` — Memory vNext subsystem (10 modules)
- `src/core/skills/factory.py` — Code injection fix
- `src/core/skills/executor.py` — Unsigned skill warning
- `src/core/skills/registry.py` — Atomic file writes
- `src/core/soul/api.py` — Thread-safe proposals
- `src/core/soul/amendments.py` — Error logging for corrupted files
- `src/core/health/monitor.py` — Thread-safe snapshots
- `src/tools/policies.py` — Symlink-safe paths, shlex denylist
- `src/tools/providers/local_sandbox.py` — Workspace validation, shlex denylist, Docker sanitization
- `src/tools/providers/vision_antigravity.py` — Page reuse, element detection, asyncio fixes

---

## v4.0.0 — Multi-Provider Upgrade (2026-02-03)

**Spec:** [docs/specs/Lancelot_v4Next_Spec_MultiProvider_Upgrade.md](specs/Lancelot_v4Next_Spec_MultiProvider_Upgrade.md)
**Blueprint:** [docs/blueprints/Lancelot_v4Next_Blueprint_MultiProvider_Upgrade.md](../docs/blueprints/Lancelot_v4Next_Blueprint_MultiProvider_Upgrade.md)

### Summary

Complete v4 upgrade transforming Lancelot from a Gemini-only system into a
multi-provider AI platform with mandatory local utility models, unbrickable
onboarding, and a War Room control plane.

### Phases Completed

#### Phase 1 — Unbrickable Onboarding (Prompts 0-7)
- Test harness baseline with pytest markers and conftest
- 11-state OnboardingSnapshot with atomic disk persistence
- Recovery commands: STATUS, BACK, RESTART STEP, RESEND CODE, RESET ONBOARDING
- COOLDOWN state replacing legacy LOCKDOWN with exponential backoff
- Control-plane API endpoints mounted as FastAPI APIRouter
- War Room recovery panel in Streamlit UI

#### Phase 2 — Local Model Package (Prompts 8-12)
- Local model scaffold: lockfile, fetch, smoke test modules
- models.lock.yaml with Hermes 2 Pro Mistral 7B Q4_K_M (Apache-2.0)
- local-llm Docker service with FastAPI /health and /v1/completions
- Mandatory LOCAL_UTILITY_SETUP onboarding state

#### Phase 3 — Model Router & Provider Lanes (Prompts 13-16)
- LocalModelClient HTTP client with 5 utility methods
- Runtime models.yaml and router.yaml with ProfileRegistry
- ModelRouter v1 with local utility routing and receipt logging
- ModelRouter v2 with fast/deep flagship lanes and escalation
- FlagshipClient: provider-agnostic HTTP client for Gemini, OpenAI, Anthropic
- Risk-based escalation: task types, keywords, failure retry (fast to deep)

#### Phase 4 — Cost Telemetry & Hardening (Prompts 17-18)
- UsageTracker with per-lane cost estimation and local savings calculation
- /usage/summary, /usage/lanes, /usage/savings, /usage/reset endpoints
- Error leakage prevention across all API endpoints
- Health check hardening with try/except safety
- Download timeout protection for model fetching
- Inference error sanitisation in local-llm server
- 26 regression tests covering all hardening fixes

### Test Suite

721 passed, 18 skipped, 0 failures across 18 v4 test files.

### New Files

- `config/models.yaml` — Lane-based model configuration
- `config/router.yaml` — Routing order and escalation config
- `local_models/` — Docker service, lockfile, fetch, smoke test, prompts
- `src/core/onboarding_snapshot.py` — Disk-backed state persistence
- `src/core/recovery_commands.py` — Recovery command handlers
- `src/core/control_plane.py` — War Room API endpoints
- `src/core/local_utility_setup.py` — Onboarding setup orchestration
- `src/core/local_model_client.py` — Local model HTTP client
- `src/core/provider_profile.py` — Typed config loader and registry
- `src/core/model_router.py` — Lane-based routing with receipts
- `src/core/flagship_client.py` — Multi-provider flagship client
- `src/core/usage_tracker.py` — Per-lane cost telemetry
- `src/ui/recovery_panel.py` — Streamlit recovery panel
- `pytest.ini` — Test configuration with markers
- `tests/conftest.py` — Shared fixtures
- 18 test files with 721 tests

### Modified Files

- `docker-compose.yml` — Added local-llm service
- `requirements.txt` — Added pyyaml, pytest dependencies
- `src/core/gateway.py` — Error leakage fixes, health check hardening
- `src/integrations/api_discovery.py` — Error sanitisation
- `src/ui/onboarding.py` — LOCAL_UTILITY_SETUP step integration
- `src/ui/war_room.py` — Router and usage panels
