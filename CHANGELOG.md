# Changelog

All notable changes to Project Lancelot will be documented in this file.

> **Note:** Internal development used version numbers v8.x. The first public release is v0.1.0.
> All entries below represent the cumulative development history leading to public launch.

## [0.2.4] - 2026-02-18

### Added
- **Multi-Provider Onboarding** (`src/ui/onboarding.py`): In-app War Room onboarding now supports
  all 4 LLM providers (Gemini, OpenAI, Anthropic, xAI) with a dedicated provider selection step
  (`FLAGSHIP_SELECTION`). Previously only Gemini was supported. Each provider shows its key prefix,
  signup URL, and format validation.
- **Live API Key Validation**: Onboarding now validates API keys with a live HTTP probe to the
  provider's API (ported from the CLI installer's `validate.mjs`). Bad keys are rejected immediately
  with a clear error instead of silently failing at runtime. Network errors are non-blocking.
- **All Communications Connectors in Onboarding**: Comms selection step now covers all 8 messaging
  connectors: Telegram, Google Chat, Slack, Discord, Teams, WhatsApp, Email (SMTP), and SMS (Twilio).
  Each connector has a guided multi-step credential setup flow with provider-specific prompts.
- **Security Token Auto-Generation** (`FINAL_CHECKS` step): Onboarding automatically generates
  `LANCELOT_OWNER_TOKEN`, `LANCELOT_API_TOKEN`, and `LANCELOT_VAULT_KEY` using `secrets.token_urlsafe(32)`.
  Previously these were only set by the CLI installer, leaving War Room onboarders with no auth tokens.
- **Feature Flag Defaults in Onboarding**: `FINAL_CHECKS` writes default feature flags to `.env`
  (`FEATURE_SOUL`, `FEATURE_SKILLS`, `FEATURE_HEALTH_MONITOR`, `FEATURE_SCHEDULER`,
  `FEATURE_AGENTIC_LOOP`, `FEATURE_LOCAL_AGENTIC`) so new installs have a working configuration.
- **`LANCELOT_PROVIDER` env var**: Onboarding now writes this to `.env` so the model router knows
  which provider is configured. Includes backward-compatible inference from existing API key env vars.
- **Launch Scripts** (`launch.sh`, `launch.ps1`): New launcher scripts that wrap `docker compose up -d`,
  poll the health endpoint every 2 seconds, and auto-open the War Room in the default browser when
  Lancelot becomes healthy. 120-second timeout with helpful fallback message.
- **Auto-Open War Room in CLI Installer** (`installer/src/index.mjs`): The `npx create-lancelot`
  installer now automatically opens `http://localhost:8501` in the default browser after install
  completes. Cross-platform support (Windows `start`, macOS `open`, Linux `xdg-open`).

### Fixed
- **Telegram Duplicate Messages** (V15): Fixed `_handle_updates()` using `time.time()` debounce
  instead of proper Telegram `offset` parameter, causing duplicate processing on every poll cycle.
  Now tracks `last_update_id` and passes `offset=last_update_id + 1` to `getUpdates`.
- **Telegram Context Loss** (V15): Fixed conversation history not being injected into LLM context.
  `TelegramBot` now maintains per-chat `conversation_histories` dict and passes the last 6 messages
  to the orchestrator's Gemini call via `contents` parameter.
- **Telegram Model Routing** (V15): Fixed Telegram always using Gemini regardless of message type.
  Simple/conversational messages now route through the local model first (matching War Room behavior),
  with Gemini fallback on empty responses.
- **Telegram Offset Tracking** (V15b): Further hardened offset-based deduplication — `last_update_id`
  is updated per-message (not per-batch) to prevent gaps on partial batch failures.
- **Telegram Long Message Chunking** (V15b): Messages exceeding Telegram's 4096-character limit are
  now split at paragraph boundaries and sent as sequential chunks instead of being silently truncated.
- **Telegram Voice/Audio Stubs** (V15b): Voice and audio messages now return a friendly
  "voice notes not yet supported" message instead of crashing with an unhandled content type error.
- **Telegram Empty File Downloads** (V15b): Added size validation after `getFile` downloads — empty
  or failed downloads are caught before being passed to processing pipelines.
- **Onboarding `LOCAL_UTILITY_SETUP` Dead Code** (V16): The local model setup handler existed but
  `_determine_state()` never routed to it. Now properly wired between credentials and comms steps.
- **Onboarding State Map** (V16): `_sync_snapshot()` was missing `FLAGSHIP_SELECTION`,
  `LOCAL_UTILITY_SETUP`, and `FINAL_CHECKS` from its state map, causing snapshot persistence failures
  for those states.

### Changed
- **Onboarding Flow**: New sequence is WELCOME → FLAGSHIP_SELECTION → HANDSHAKE →
  LOCAL_UTILITY_SETUP → COMMS_SELECTION → [guided setup] → FINAL_CHECKS → READY. Previously
  skipped provider selection, local model setup, and final validation entirely.
- **Comms Skip**: Choosing to skip comms now writes `LANCELOT_COMMS_TYPE=none` to `.env` so
  `_determine_state()` knows the step was intentionally skipped (prevents re-prompting on restart).
- **README**: Updated manual installation section with launch scripts as primary start method.
  Fixed War Room URL from `localhost:8000` to `localhost:8501`.
- **Quickstart Guide**: Updated with launch scripts, auto-open behavior, all 8 connectors, and
  correct War Room URL.
- **Installation Guide**: Added port 8501, xAI provider, all connector env vars, security tokens,
  feature flags to .env example, and launch scripts throughout.

## [0.2.3] - 2026-02-17

### Added
- **Host Write Commands** (`FEATURE_HOST_WRITE_COMMANDS`): Toggleable dangerous commands list
  for the host OS. Unlocks destructive commands (`rm`, `del`, `kill`, `shutdown`, etc.) when
  enabled. OFF by default with extreme danger confirmation dialog. Editable via inline editor
  in Kill Switches. All write commands still require Sentry approval before execution.
- **HostWriteCommandsEditor**: Red-themed inline editor in Kill Switches page for managing
  the allowed write commands list. Shows bold danger warnings and command count.
- **Config file** (`config/host_write_commands.yaml`): Editable list of write command binaries.
  Changes take effect immediately — no restart required.
- **API routes**: `GET/PUT /api/flags/host-write-commands` for reading and updating the
  write commands list from the War Room UI.

### Fixed
- **HTTP 500 from local LLM**: Fixed crash when assistant messages with `content: null`
  (standard OpenAI tool-call format) were sent to llama-cpp-python. Added message sanitization
  in `local_models/server.py` and defensive None-to-empty-string conversion in orchestrator.
- **Task runner COMMAND steps**: Fixed `Missing required input: 'command'` error when the plan
  compiler created COMMAND steps with `{"description": "..."}` instead of `{"command": "..."}`.
  Runner now extracts the command from step params or description.
- **Windows command classification**: Added Windows read-only commands (`ver`, `systeminfo`,
  `ipconfig`, `hostname`, etc.) to the safety classifier's auto-approve list, preventing
  infinite escalation loops.
- **System prompt Windows awareness**: Host bridge prompt now explicitly tells the LLM the
  host is Windows and suggests Windows commands instead of Linux commands like `cat /etc/os-release`.

## [0.2.2] - 2026-02-17

### Added
- **Host Agent control panel**: Inline panel in Kill Switches page (under HOST_BRIDGE toggle)
  shows agent status (Running/Offline), platform info, hostname, and a "Stop Agent" button.
  When offline, displays install instructions.
- **Host Agent `/shutdown` endpoint**: Graceful remote shutdown via `POST /shutdown`.
- **Host Agent service installer** (`host_agent/install_service.bat`): One-time script that
  registers the agent as a Windows Scheduled Task using `pythonw.exe` — runs silently in the
  background with no console window, auto-starts on user login.
- **Host Agent uninstaller** (`host_agent/uninstall_service.bat`): Removes scheduled task and
  stops the running agent.
- **Backend proxy routes**: `GET /api/flags/host-agent-status` and
  `POST /api/flags/host-agent-shutdown` for container-to-host-agent communication.
- **Auto-shutdown**: Toggling `FEATURE_TOOLS_HOST_BRIDGE` OFF automatically sends shutdown
  signal to the host agent (best-effort).

## [0.2.1] - 2026-02-16

### Added
- **Host OS Bridge** (`FEATURE_TOOLS_HOST_BRIDGE`): New `HostBridgeProvider` that executes
  commands on the actual host operating system (Windows, macOS, Linux) via a lightweight HTTP
  bridge. The Lancelot Host Agent (`host_agent/agent.py`) runs on the host machine and accepts
  command execution requests from the Docker container. Supports ShellExec, RepoOps, FileOps,
  and DeployOps capabilities. Authenticated via shared token, localhost-only binding.
- **Host Agent** (`host_agent/`): Standalone Python HTTP server (stdlib only, no dependencies)
  that runs on the host machine. Includes `start_agent.bat` for Windows quick-start. Endpoints:
  `/health` (no auth), `/info` (auth), `/execute` (auth). Command denylist, output bounding,
  and timeouts enforced.
- **Risk acceptance dialog**: Flags with `confirm_enable` metadata now show a confirmation
  dialog before enabling. `FEATURE_TOOLS_HOST_BRIDGE` requires explicit risk acceptance.

### Changed
- **Relabeled `FEATURE_TOOLS_HOST_EXECUTION`**: Now clearly described as "Docker Linux Access"
  (runs in the container's Linux environment) to distinguish from the new Host OS Bridge.
- **Router priority chain**: `host_bridge` > `host_execution` > `local_sandbox`. The highest
  available provider in the chain is selected for shell_exec, repo_ops, file_ops, deploy_ops.
- **Docker Compose**: Added `extra_hosts: host.docker.internal:host-gateway` and
  `HOST_AGENT_URL`/`HOST_AGENT_TOKEN` environment variables for the host bridge.

## [0.2.0] - 2026-02-16

### Added
- **Host Execution Provider** (`FEATURE_TOOLS_HOST_EXECUTION`): New `HostExecutionProvider` that
  runs commands directly on the host OS instead of inside the Docker sandbox. Implements
  ShellExec, RepoOps, FileOps, and DeployOps capabilities. Still enforces command denylist,
  output bounding, and timeouts. Registered as preferred provider when enabled.

### Fixed
- **Feature flag audit — 6 broken flags now functional**:
  - `FEATURE_TOOLS_NETWORK`: Now wired to `SandboxConfig.network_enabled` so the flag actually
    controls whether Docker sandbox commands can access the network.
  - `FEATURE_TOOLS_CLI_PROVIDERS`: Gate added in Tool Fabric setup; logs when enabled, blocks
    future CLI adapter registration when disabled.
  - `FEATURE_NETWORK_ALLOWLIST`: Enforcement added in `PolicyEngine.evaluate_network()` — when
    enabled, only domains listed in `config/network_allowlist.yaml` are permitted. Supports
    suffix matching (e.g., `api.github.com` matches allowlisted `github.com`).
  - `FEATURE_VOICE_NOTES`: Now wired in `gateway.py` — `VoiceProcessor` is only created and
    passed to `TelegramBot` when the flag is enabled.
  - `FEATURE_SKILL_SECURITY_PIPELINE`: Now gates skill installation in `SkillFactory` with a
    4-stage pipeline: manifest validation, dangerous code scanning (eval, exec, os.system, etc.),
    ownership verification, and audit logging.
  - `FEATURE_RESPONSE_ASSEMBLER`: Updated description to note it's informational-only since
    Fix Pack V2 (assembler is always active for output hygiene).

## [0.1.9] - 2026-02-16

### Added
- **Shared Workspace Connector**: Configurable shared folder path from the War Room Connectors page:
  - New `shared_workspace` connector with "Host Folder Path" config field
  - Hot-swap: changing the path updates `docker-compose.yml` and auto-restarts the container
  - Current path auto-seeded into vault on startup from docker-compose.yml
  - Config-type credentials now show as visible text inputs (not masked passwords)

## [0.1.8] - 2026-02-16

### Added
- **Skill Builder Pipeline**: Wired up the complete skill proposal → approval → installation pipeline:
  - **SkillFactory initialization** in `gateway.py`: Creates the factory at startup, registers all
    builtin skills (including `document_creator` and `skill_manager`) in the registry.
  - **skill_manager builtin skill**: New tool that Lancelot can call to `propose` new skills (with real
    Python code), `list_proposals`, `list_skills`, and `run_skill`. Proposals require owner approval.
  - **Skills REST API** (`/api/skills/*`): Six endpoints for War Room proposal management — list
    proposals, get detail (with code preview), approve, reject, install, and list installed skills.
  - **War Room Skills Panel**: New "Skills" page under Operations with proposal cards, installed skill
    list, and a detail modal for reviewing code, approving/rejecting, and installing proposals.
  - **Tool declarations**: Both Normalized and OpenAI-format declarations for `skill_manager`, with
    safety classification (auto for list/propose, escalate for run_skill).

### Fixed
- **Workspace path bug**: `repo_writer` and `document_creator` defaulted to `/home/lancelot/data`
  instead of the shared workspace. Set `LANCELOT_WORKSPACE=/home/lancelot/workspace` in
  docker-compose.yml so files land in the shared desktop folder.

## [0.1.7] - 2026-02-16

### Added
- **Update Mechanism — Phase 1**: Version discovery, periodic update checks, and War Room update
  banner with severity-colored notifications:
  - **VERSION file**: Single source of truth for the running version (replaces hardcoded `"8.0"`).
    Health endpoint now reads from `VERSION`.
  - **Update Checker Service** (`update_checker.py`): Background daemon thread that checks a version
    manifest URL every 6 hours (configurable via `LANCELOT_VERSION_URL`). Graceful fallback if
    unreachable — no crash, no spam. Stores latest check result and banner dismissal state in memory.
  - **Update API** (`/api/updates/*`): Three endpoints — `GET /status` (cached, cheap poll),
    `POST /check` (force immediate check), `POST /dismiss` (dismiss banner for info/recommended;
    important/critical are non-dismissible).
  - **War Room Update Banner**: Severity-colored banner at top of main content area. Shows current
    version → available version, severity badge, changelog summary. "View Changelog" link, "Check Now"
    button, and "Dismiss" button (info/recommended only). Polls backend every 5 minutes.
  - **Network Allowlist**: Added `api.projectlancelot.dev` and `ghcr.io` to the NetworkInterceptor
    allow list so version checks aren't blocked.
- **GitHub Actions CI/CD** (`.github/workflows/release.yml`): Automated release pipeline triggered on
  `v*` tag push — builds multi-arch Docker image (amd64 + arm64), pushes to GHCR, creates GitHub
  Release with auto-generated notes. VERSION file is verified against tag before build.

## [0.1.6] - 2026-02-16

### Added
- **Comprehensive Setup & Recovery Page**: Complete overhaul of the Setup & Recovery page with
  4-tab navigation (System, Data, Logs & Config, Danger Zone):
  - **System Tab**: System info metrics (version, uptime, Python version, disk usage), container
    controls (restart/shutdown with confirmation dialogs), onboarding status, and recovery commands.
  - **Data Tab**: Vault credential management (list keys with metadata, delete individual keys —
    values never shown), execution token list with revoke, receipt management with clear all,
    and usage counter reset.
  - **Logs & Config Tab**: Terminal-style audit log viewer (last 200 lines, auto-scroll, switchable
    between audit.log and vault access.log), configuration reload (feature flags, scheduler,
    connectors), and export/backup download (ZIP of configs, soul, memory, flags, scheduler data).
  - **Danger Zone Tab**: Red-bordered destructive operations — factory reset (requires typed "RESET"
    confirmation), memory purge, feature flag reset, and onboarding reset. All with confirmation
    dialogs.
- **Setup API Backend** (`/api/setup/*`): New API module with 12 endpoints — system-info, restart,
  shutdown, logs, vault key listing/deletion, receipt clearing, config reload, export backup (ZIP),
  factory reset, memory purge, and flag reset. All destructive operations are audit-logged and
  require `{"confirm": true}` in the request body.

## [0.1.5] - 2026-02-16

### Added
- **Health Dashboard**: New War Room page at `/health` showing full system health detail —
  component status (gateway, orchestrator, sentry, vault, memory), readiness state (LLM, scheduler,
  onboarding), degraded reasons list, uptime, version, and Crusader mode status. All data polls
  live every 5 seconds.
- **VitalsBar Armor Popover**: Hovering over the Armor vital in the header now shows a popover
  listing each degraded reason (or "All systems operational" if healthy). Clicking the Armor vital
  navigates to the Health Dashboard. Users can now immediately see what's wrong instead of just
  a percentage.
- **Health link in sidebar**: Added "Health" as first item in the SYSTEM navigation group.

## [0.1.4] - 2026-02-16

### Added
- **X (Twitter) Connector**: First-party connector for X API v2. Operations: `post_tweet` (T1,
  reversible via delete), `delete_tweet` (T3, irreversible), `get_me` (T0, read account info).
  Auth via OAuth 1.0a (4 credentials: API Key, API Key Secret, Access Token, Access Token Secret).
  Free tier supports ~1,500 tweets/month. Domain `api.x.com` added to network allowlist.
- **Connector Proxy Multi-Auth**: ConnectorProxy now supports 4 credential injection modes:
  `url_token` (Telegram URL template substitution), `oauth1` (X/Twitter HMAC-SHA1 signing),
  `basic_auth_composed` (Twilio two-key Basic auth), and `bot_token` (Discord). Previously only
  supported single-credential bearer/api_key injection.
- **Flag Dependency Enforcement**: Toggle and set endpoints now validate `requires` and `conflicts`
  metadata before changing flags. Enabling a flag that requires a disabled dependency returns a 400
  error. Disabling a flag that other enabled flags depend on is also blocked.

### Fixed
- **SMTP Email Connector Credentials**: The SMTP backend previously had a single `smtp_credentials`
  field, making it impossible to configure SMTP from the War Room. Now provides individual fields:
  `smtp_host`, `smtp_port`, `smtp_username`, `smtp_password`, `smtp_from_address`, `smtp_use_tls`,
  `imap_host`, and `imap_port`. Each field appears as a separate input in the War Room Connectors page.
- **SMS (Twilio) Connector Credentials**: Replaced single `twilio_credentials` basic_auth field with
  individual fields: `sms.account_sid`, `sms.auth_token`, `sms.from_number`, and
  `sms.messaging_service_sid`. Account SID and Auth Token are required; From Number and Messaging
  Service SID are optional (one of the two is needed for sending).
- **WhatsApp Connector Credentials**: Added missing `whatsapp.phone_number_id` credential field.
  Previously only configurable via YAML — now manageable from the War Room Connectors page.
- **Receipt Explorer Status Mismatch**: UI used `completed`/`failed` filter values but backend uses
  `success`/`failure`. Success rate metric always showed 0%. Fixed filter values and calculation.
- **Network Allowlist Missing Domains**: 7 of 9 connector domains were missing from the allowlist,
  which would have blocked all traffic. Added: `api.twilio.com`, `slack.com`, `discord.com`,
  `graph.microsoft.com`, `graph.facebook.com`, `gmail.googleapis.com`, `www.googleapis.com`,
  `api.openai.com`, `api.x.ai`.
- **Telegram Connector Auth**: URLs contained literal `{token}` placeholder that was never
  substituted. Added `url_token` auth type metadata so the proxy substitutes the token correctly.
- **Discord Connector Auth**: Credential type was `api_key`, causing the proxy to inject
  `X-API-Key` instead of `Authorization: Bot`. Changed to `bot_token` type.
- **SMS Connector Auth**: Account SID was always empty because `_instantiate_connector` never
  populated it. Switched to `basic_auth_composed` where the proxy composes Basic auth from two
  vault keys at request time.
- **Health Monitor Slow Shutdown**: `stop_monitor()` could block for up to 30 seconds (the full
  monitoring interval). Changed from monolithic `time.sleep()` to 1-second increments with a stop
  event, matching the scheduler pattern. Shutdown now completes within ~1 second.
- **Scheduler Job Concurrency**: `execute_job()` could run the same job simultaneously from the
  tick loop and the `run_now` API. Added per-job locking — concurrent attempts are skipped with a
  receipt.
- **Soul Init Crash**: `load_active_soul()` returning None caused an AttributeError. Added null
  guard so Soul subsystem starts gracefully without a soul document.
- **set_flag() Type Safety**: `set_flag()` could overwrite non-boolean module attributes. Added
  `isinstance(current, bool)` guard.
- **Vite Dev Proxy**: Missing proxy entries for `/soul`, `/memory`, `/system`, `/usage`,
  `/onboarding`, `/crusader_status` caused 404s in development mode.
- **Tailwind Missing Colors**: Added `state.warning`, `surface.bg`, and `surface.base` color tokens
  referenced by War Room components but not defined.
- **generic_rest Connector Registration**: `GenericRESTConnector` was not in `_CONNECTOR_CLASSES`,
  preventing it from being instantiated.

## [0.1.3] - 2026-02-16

### Added
- **Hot-Toggle Feature Flags**: All feature flags are now hot-toggleable at runtime without a
  container restart. Core subsystems (Soul, Skills, Scheduler, Health Monitor, Memory vNext, BAL)
  are lazily initialized when toggled ON and gracefully shut down when toggled OFF.
- **SubsystemManager**: New lifecycle registry (`subsystem_manager.py`) tracks init/shutdown
  functions for each feature-gated subsystem. Enables start/stop/status operations.
- **Request-Gating Middleware**: FastAPI middleware gates disabled subsystem routes with HTTP 503
  responses. Routes are always mounted but only accessible when their flag is ON.
- **BAL Flag Metadata**: Added `FEATURE_BAL` to the flags API metadata registry so it appears
  in the War Room Kill Switches page with proper description, category, and warnings.

### Changed
- `RESTART_REQUIRED_FLAGS` emptied — no flags require restart anymore.
- Gateway startup refactored: subsystem init/shutdown extracted into standalone functions,
  all subsystem routers always mounted (gated by middleware).
- Gateway shutdown uses `subsystem_manager.stop_all()` for clean teardown of all subsystems
  including the previously missing `job_executor.stop()`.
- Toggle and set API endpoints now call SubsystemManager to hot-toggle subsystems.

### Fixed
- **Missing job_executor.stop()**: The scheduler's job executor background thread was never
  stopped during gateway shutdown. Now handled by SubsystemManager.

## [0.1.2] - 2026-02-15

### Added
- **xAI (Grok) Provider**: Full fourth LLM provider integration. `XAIProviderClient` uses the
  OpenAI-compatible API at `https://api.x.ai/v1` with the `openai` SDK. Supports all Grok models
  (grok-3-mini, grok-3, grok-4-0709) with tool calling, model discovery, and lane auto-assignment.
  Set `LANCELOT_PROVIDER=xai` and `XAI_API_KEY` to use.
- **API Key Rotation UI**: New "Provider API Keys" section on the Cost Tracker page lets users
  rotate API keys for any provider from the War Room. Keys are validated against the provider API
  before being accepted, then hot-swapped (if active provider) and persisted to `.env`.
  `GET /api/v1/providers/keys` returns masked key previews (last 4 chars only).
  `POST /api/v1/providers/keys/rotate` validates, applies, and persists a new key.

### Changed
- Provider factory, gateway, flagship client, antigravity engine, and installer all updated to
  support 4 providers (Gemini, OpenAI, Anthropic, xAI).
- Model profiles and lane configs updated with xAI Grok models.
- All documentation updated to reference 4-provider support.

## [0.1.1] - 2026-02-15

### Fixed
- **Governance Dashboard — T3 Approvals Invisible**: MCP Sentry T3 action approvals were stored
  in a separate in-memory dict that the Governance API never queried. Wired the same MCPSentry
  instance into `governance_api.py` so `/api/governance/approvals` now includes pending T3 actions
  alongside graduation proposals and APL rule proposals. Approve/deny endpoints updated to handle
  all three approval types.
- **Agentic Loop — Escalated Ops Bypassed Sentry**: The V6/V8 agentic loops only checked
  `if safety == "escalate" and not allow_writes`, meaning the action request path (`allow_writes=True`)
  never blocked anything. Now all escalated tool calls go through MCP Sentry regardless of
  `allow_writes`, creating proper pending approval requests for the War Room.
- **Trust Records Not Counting**: The `_record_governance_event()` method was only called from
  the plan execution pipeline, not from V6/V8 agentic loops (the primary execution path). Added
  governance event recording after tool execution in both loops with proper `RiskTier` enum values.
- **Kill Switch State Lost on Restart**: Feature flag toggles via the War Room only updated
  `os.environ` (in-memory). On container restart, `.env` values would overwrite all runtime
  changes. Added persistent JSON state file (`.flag_state.json`) in the Docker-mounted data
  volume. Priority order: persisted state > env vars > code defaults. Toggles now survive
  container restarts.
- **Missing `health_check` Skill**: Scheduler's `health_sweep` job referenced a `health_check`
  skill that was never registered as a builtin, causing recurring error logs every 60 seconds.
  Created `health_check.py` builtin skill that reads the latest `HealthSnapshot` from the
  health monitor and returns system status. Registered in the skill executor.

### Added
- **Sentry Decision Logging**: Approving or denying a T3 action via the War Room now records
  the decision in the `DecisionLog`, so it appears in the Recent Decisions panel with the
  correct risk tier and timestamp.
- **T3 ACTION Badge**: War Room Governance Dashboard now shows a distinct yellow "T3 ACTION"
  badge for MCP Sentry approval items, with warning-colored border and parameter display.

## [0.1.0] - 2026-02-14 (Public Launch)

Initial public release of the Lancelot Governed Autonomous System.

### Highlights
- Soul constitutional governance with runtime-switchable postures (Crusader Mode)
- Risk-tiered governance pipeline (T0-T3)
- Progressive Trust Ledger with graduation and revocation
- 6-stage Skill Security Pipeline
- Governed Connector Proxy with Credential Vault
- Receipt-based audit trails
- Approval Pattern Learning (APL)
- Dependency-resolved Kill Switch management
- War Room operator dashboard (React 18 + TypeScript)
- One-command CLI installer (`npx create-lancelot`)
- Multi-provider LLM support (Gemini, OpenAI, Anthropic)
- Telegram file attachment delivery
- 1900+ tests

License: AGPL-3.0 | Patent Pending: US Provisional Application #63/982,183

---

## [8.3.4] - 2026-02-14

### Fixed
- **Duplicate Telegram Messages**: Fixed two duplication paths: (1) voice handler fallback
  in `_handle_voice()` could send text twice when TTS returned falsy; (2) system prompt
  told the LLM to use `telegram_send` to reply when the handler already sends the response
  automatically. Channel note now explicitly warns against double-sending.
- **Workspace File Writes Blocked by Governance**: `_classify_tool_call_safety()` returned
  `"escalate"` for ALL `repo_writer` calls regardless of scope, bypassing the risk-tier
  system that correctly classifies workspace writes as T1 (auto-approved). Now workspace
  create/edit/patch operations are auto-approved; only deletes and sensitive files (.env,
  credentials) require explicit approval.

### Added
- **Telegram File/Document Delivery**: New `send_document()` method on `TelegramBot` class
  enables file attachments via Telegram's `sendDocument` API. The `telegram_send` skill
  (now v2.0.0) accepts a `file_path` parameter to deliver workspace files as Telegram
  document attachments with optional captions. Path traversal protection enforced.

## [8.3.3] - 2026-02-14

### Fixed
- **Governance Dashboard + Trust Ledger: Wired to backend**: `TrustLedger`, `DecisionLog`, and
  `RuleEngine` instances were never created in the orchestrator — all three API endpoint groups
  (`/api/governance/*`, `/api/trust/*`, `/api/apl/*`) were receiving `None` and returning empty data.
  Now properly initialized in `_init_governance()` when feature flags are enabled.
- **Governance Execution Pipeline: Data flow wired**: Governance subsystems were initialized but
  never called during action execution. Added `_record_governance_event()` helper and wired it
  into all 5 execution paths (legacy, T0, T1, T2, T3). Every tool execution now records
  success/failure to the Trust Ledger and decisions to the Decision Log.
- **Trust Ledger Seed Data**: Added `_seed_trust_records()` to populate 10 baseline capability
  records on startup (fs.read, fs.write, shell.exec, chat.send, memory.write, scheduler.create,
  skill.install) so the War Room pages display meaningful data from first boot.
- **Feature Flags Enabled**: `FEATURE_TRUST_LEDGER`, `FEATURE_APPROVAL_LEARNING`, and
  `FEATURE_RISK_TIERED_GOVERNANCE` now enabled by default in `.env` and the installer.

### Changed
- **Antigravity Engine: Provider-Agnostic**: Browser automation agent (`run_agent_task`) now
  respects `LANCELOT_PROVIDER` and works with Gemini, OpenAI, or Anthropic. Previously hardcoded
  to Gemini via `langchain-google-genai`. Falls back to any provider with an available API key.
- **New Dependencies**: Added `langchain-openai>=0.3.0` and `langchain-anthropic>=0.3.0` to
  `requirements.txt` for full provider coverage in browser automation tasks.
- **Feature Flag Description**: Updated `FEATURE_TOOLS_ANTIGRAVITY` description to reflect
  provider-agnostic support.

## [8.3.2] - 2026-02-14

### Added
- **`create-lancelot` CLI Installer**: New NPM package (`npx create-lancelot`) provides a guided,
  single-command installation experience. The installer:
  - Checks all prerequisites (Node.js, Git, Docker Desktop, Compose, disk space, RAM, GPU)
  - Prompts for install location, LLM provider, API key (with live validation), and comms channel
  - Clones the repository, generates `.env`, patches `docker-compose.yml` for the target system
  - Downloads the local AI model (5GB) with progress bar and resume support
  - Builds Docker images, starts services, and verifies health
  - Writes onboarding snapshot to skip the War Room setup wizard
  - Supports `--resume` for interrupted installations and `--skip-model` for faster setup
  - Cross-platform: Windows, macOS, and Linux
- **Installer state persistence**: Interrupted installs save progress to `~/.create-lancelot-state.json`
  (API keys are never persisted). Resume with `npx create-lancelot --resume`.

## [8.3.1] - 2026-02-14

### Added
- **Provider Switching UI**: Provider selector dropdown on the Cost Tracker page lets users
  hot-swap the active LLM provider (Gemini/OpenAI/Anthropic) without restarting the container.
  Providers without API keys are shown as disabled.
- **Lane Model Override Controls**: Each lane (Fast/Deep/Cache) now has a model selector dropdown
  populated from discovered models. Users can override which model is assigned to each lane at
  runtime. Fast/Deep lanes filter to models with tool support.
- **Reset to Auto**: Button to clear all lane overrides and re-run automatic model assignment
  based on capability scoring.
- **Runtime Config Persistence**: Provider and lane override choices are persisted to
  `lancelot_data/provider_config.json` and automatically restored on container restart.
- **4 New API Endpoints**:
  - `GET /api/v1/providers/available` — list providers with API key availability
  - `POST /api/v1/providers/switch` — hot-swap active provider
  - `POST /api/v1/providers/lanes/override` — override a lane's model assignment
  - `POST /api/v1/providers/lanes/reset` — reset lanes to auto-assignment
- **Orchestrator Methods**: `switch_provider()` and `set_lane_model()` for runtime provider/model
  hot-swap. Invalidates context caching and deep model validation on switch.
- **ModelDiscovery Methods**: `set_lane_override()`, `reset_overrides()`, `replace_provider()`
  for runtime lane management.

### Changed
- **Gateway Phase 7**: Now loads persisted provider config at startup, applies saved provider and
  lane overrides before model discovery runs.
- **Provider API init**: `init_provider_api()` now accepts orchestrator reference for runtime
  switching coordination.
- **Readiness probe**: Fixed remaining `main_orchestrator.client` references to use `.provider`.

## [8.3.0] - 2026-02-13

### Added
- **Multi-Provider Support**: Lancelot now supports Google Gemini, OpenAI, and Anthropic as LLM
  backends. Set `LANCELOT_PROVIDER=openai` or `LANCELOT_PROVIDER=anthropic` to switch providers.
  Gemini remains the default.
- **ProviderClient Abstraction Layer**: New `providers/` package with abstract `ProviderClient`
  base class and concrete implementations:
  - `GeminiProviderClient` — wraps google-genai SDK
  - `OpenAIProviderClient` — wraps openai SDK
  - `AnthropicProviderClient` — wraps anthropic SDK
  - `NormalizedToolDeclaration` — provider-agnostic tool definitions with per-provider converters
  - Factory function `create_provider()` for provider instantiation
- **Dynamic Model Discovery**: `ModelDiscovery` service queries the active provider's API at
  startup to discover available models. Cross-references with `config/model_profiles.yaml` for
  cost rates, context windows, and capability tiers. Auto-assigns models to lanes (fast/deep/cache).
- **Model Profiles Database**: New `config/model_profiles.yaml` with static capability data for
  known models across all three providers (context windows, cost rates, tool support, capability tiers).
- **Provider Stack API**: Three new REST endpoints:
  - `GET /api/v1/providers/stack` — current provider, lane assignments, discovered models
  - `GET /api/v1/providers/models` — all discovered models from provider API
  - `POST /api/v1/providers/refresh` — re-run model discovery
  - `GET /api/v1/providers/profiles` — static model profile data
- **Model Stack UI**: New "Model Stack" section at the top of the Cost Tracker page showing:
  - Active provider with connection status indicator
  - Lane-to-model assignments table (Fast/Deep/Cache with context window, cost, tool support)
  - Collapsible list of all discovered models with capability tiers
  - Refresh button to re-run model discovery
  - Last discovery timestamp

### Changed
- **Orchestrator refactored to provider-agnostic**: All 7 `generate_content()` call sites and the
  entire agentic loop now use the `ProviderClient` abstraction instead of direct Gemini SDK calls.
  `_init_gemini()` → `_init_provider()`, `_gemini_call_with_retry()` → `_llm_call_with_retry()`,
  `_build_tool_declarations()` now returns `NormalizedToolDeclaration` objects.
- **Dynamic cost rates**: `UsageTracker` now loads cost rates from `config/model_profiles.yaml`
  with fallback to hardcoded values. Rates update when the profiles file changes.
- **Health check updated**: Gateway health check now monitors `provider` instead of `client`.
- **Startup validation**: Gateway startup checks the correct API key env var based on
  `LANCELOT_PROVIDER` setting (not just `GEMINI_API_KEY`).
- **Context caching**: Now correctly guarded behind Gemini-only check — other providers skip
  cache initialization gracefully.
- **Attachment handling**: Multimodal attachments (images, PDFs) now use provider-agnostic
  `(bytes, mime_type)` tuple format instead of Gemini-specific `types.Part` objects.
- **Deep model validation**: `_get_deep_model()` now uses `provider.validate_model()` instead of
  direct Gemini SDK `client.models.get()`.
- `CAPABILITIES.md` updated with multi-provider support docs, complete skills list.

### Dependencies
- Added `openai>=1.0.0` and `anthropic>=0.20.0` to requirements.txt.

## [8.2.11] - 2026-02-13

### Removed
- **Dead Streamlit UI process**: Removed `streamlit run src/ui/war_room.py` from docker-compose command.
  The old Streamlit War Room on port 8501 was still running alongside the React War Room (served by
  FastAPI on port 8000). Now only uvicorn runs, eliminating wasted resources and port 8501 exposure.
- **PlaceholderPage component**: Unused React component with no route pointing to it.
- **14 unused frontend API functions**: fetchRouterDecisions, fetchRouterStats, fetchTokens, fetchToken,
  revokeToken, fetchArtifacts, fetchArtifact, storeArtifact, fetchReady, fetchHealthLive,
  fetchToolsRouting, fetchUsageSavings, resetUsage, fetchBalClientsByStatus. Removed from API modules
  and barrel exports.
- **Unused `json` import** in scheduler executor.

### Fixed
- **Toast animation missing**: Added `animate-slide-in` keyframe animation to tailwind.config.ts.
  Toast notifications now properly slide in from the right.
- **No health check for lancelot-core**: Added Docker health check (`/health` endpoint, 30s interval)
  so Docker Desktop can monitor container health.
- **No restart policy**: Added `restart: unless-stopped` to lancelot-core service so it recovers
  from crashes automatically.

## [8.2.10] - 2026-02-13

### Added
- **Per-Job Timezone Support**: Each scheduled job now has an IANA timezone field (e.g.,
  `America/New_York`, `UTC`). Cron expressions are evaluated in the job's configured timezone,
  so "45 5 * * *" with timezone `America/New_York` fires at 5:45 AM Eastern regardless of
  server timezone. Uses Python's built-in `zoneinfo` module (no external dependencies).
- **Timezone Selector in War Room**: The Scheduler dashboard expanded job details now include a
  timezone dropdown selector. Changes are saved immediately via a new PATCH endpoint
  (`/api/scheduler/jobs/{id}/timezone`). The job row also displays a compact timezone badge.
- **`apiPatch` HTTP client**: Added PATCH method to the War Room API client for partial updates.
- Default timezone for new chat-created jobs is `America/New_York` (Commander's timezone).

### Changed
- `JobRecord` and `JobSpec` models now include `timezone` field (defaults to `UTC`).
- `_tick()` in `JobExecutor` converts UTC time to each job's timezone before cron evaluation.
- `schedule_job` skill accepts optional `timezone` parameter on create action.
- Gemini and OpenAI tool declarations updated with `timezone` parameter.
- SQLite schema includes `timezone TEXT DEFAULT 'UTC'` column with automatic migration.
- `SchedulerService.create_job()` accepts `timezone_str` parameter.
- New `update_job_timezone()` method on `SchedulerService` for dashboard edits.

## [8.2.9] - 2026-02-13

### Added
- **Dynamic Job Scheduling** (`schedule_job` skill): New builtin skill that allows creating, listing,
  and deleting scheduled jobs from chat. Supports cron expressions for recurring tasks like wake-up
  calls, daily reminders, and automated health checks. Jobs are persisted in SQLite and survive
  container restarts.
- **Cron Tick Loop**: Background thread in `JobExecutor` that evaluates cron expressions every 60
  seconds and fires matching jobs. Supports `*`, specific values, comma-separated lists, and ranges.
  Includes double-fire prevention (same-minute dedup) and interval-based triggers.
- **Job Inputs**: Scheduled jobs can now pass inputs to their target skills (e.g., a wake-up call job
  passes `{"message": "Good morning Commander"}` to `telegram_send`). Added `inputs` column to the
  scheduler SQLite schema with automatic migration for existing databases.
- Scheduling keywords added to intent routing (`_needs_research`, `_wants_action`) so "schedule a
  wake-up call" correctly triggers the agentic loop with `force_tool_use=True`.

## [8.2.8] - 2026-02-13

### Added
- **War Room Send Skill** (`warroom_send`): New built-in skill that pushes notifications to the War
  Room dashboard via EventBus → WebSocket broadcast. Messages appear as slide-in toast notifications
  and persist in the notification tray. Supports `message` (required) and `priority` ("normal"|"high")
  parameters. Registered in executor, orchestrator (Gemini + OpenAI declarations), gateway, and
  skills registry.
- **Toast Notifications** (`Toast.tsx`): Auto-dismissing slide-in notification component for the War
  Room. High-priority messages show a red accent border; normal messages use the theme accent color.
  Toasts auto-dismiss after 5 seconds with manual dismiss option.
- **Live Notification Tray**: NotificationTray now displays real-time notifications received via
  WebSocket. Shows unread count badge, expandable notification list with timestamps, and "Clear all"
  button. High-priority notifications are visually distinguished with red borders.
- **WebSocket Integration in WarRoomShell**: Shell now connects to `/ws/warroom` via the `useWebSocket`
  hook, listens for `warroom_notification` events, and manages notification + toast state.

### Changed
- **Channel-Aware Messaging**: System instruction now tells Lancelot which cross-channel tools are
  available depending on the current channel — e.g., when on Telegram, it knows to use `warroom_send`
  for the War Room; when on the War Room, it knows to use `telegram_send` for Telegram.
- CAPABILITIES.md updated to list `warroom_send` as an available skill and document War Room push
  notification capability.

## [8.2.7] - 2026-02-13

### Fixed
- **Channel Isolation**: Telegram and War Room no longer act as disconnected agents. All channels
  share one orchestrator brain, one chat history, and one user profile — the user is correctly
  identified as "Myles" across all interfaces.
- **Telegram Send Blocked**: The `telegram_send` tool was classified as "escalate" which caused it
  to be BLOCKED in all non-write chat paths. Changed to "auto" since it only sends to the
  pre-configured owner chat_id. Also added "send"/"telegram"/"notify"/"message" to the routing
  triggers (`_needs_research`, `_wants_action`) so the agentic loop uses `force_tool_use=True`
  when telegram_send is clearly intended — Gemini no longer ignores the tool.
- **User Profile**: Updated USER.md from "Arthur" to "Myles" so Lancelot addresses the owner
  correctly across all channels.
- **Guardrail Conflict**: Removed the system instruction that told Gemini "call me X is NOT a
  system modification" — this was preventing name persistence. Now handled programmatically.

### Added
- **Channel Context**: `orchestrator.chat()` now accepts a `channel` parameter ("telegram",
  "warroom", or "api"). Channel info is included in the system instruction so Lancelot knows
  which interface the user is communicating through. Chat history entries are tagged with
  `[via telegram]` or `[via warroom]` for context continuity.
- **Persistent Name Updates** (V18): When the user says "call me X" or "my name is X",
  the orchestrator detects the pattern and updates USER.md automatically. The change takes
  effect immediately (context reloaded) and persists across restarts.
- Telegram bot passes `channel="telegram"` for all message types (text, voice, photo, document)
- War Room chat endpoints pass `channel="warroom"` for both standard and file upload chats
- CAPABILITIES.md updated to list `telegram_send` as an available skill

## [8.2.6] - 2026-02-13

### Added
- **Telegram Send Skill** (`src/core/skills/builtins/telegram_send.py`): New built-in skill enabling
  the orchestrator to send Telegram messages on demand. Registered as a Gemini and OpenAI function
  declaration, added to `_DECLARED_TOOL_NAMES`, and classified for auto-execution.
  Uses the gateway's TelegramBot instance or falls back to direct API calls via env vars.

## [8.2.5] - 2026-02-13

### Added
- **Soul Inspector Overlay Visibility**: Soul Inspector now shows active governance overlays as a
  banner below the header — displays overlay name, feature flag, description, and counts of added
  rules (risk rules, tone invariants, memory ethics, autonomy entries). The `/soul/content` endpoint
  now returns the fully merged soul (base + overlays) so the Constitution Viewer shows all active
  governance rules including overlay additions.

### Changed
- `/soul/status` and `/soul/content` endpoints now include `active_overlays` field with overlay metadata

## [8.2.4] - 2026-02-13

### Fixed
- **Soul Overlay Merge**: Fixed Pydantic class-identity mismatch when gateway imports models via
  short paths (`soul.store.RiskRule`) vs full paths (`src.core.soul.store.RiskRule`). Serialize
  models to dicts before constructing merged Soul. BAL governance overlay now loads successfully.

## [8.2.3] - 2026-02-13

### Added
- **Business Dashboard** (`src/warroom/src/pages/BusinessDashboard.tsx`): Live War Room page replacing
  the "Coming Soon" placeholder — shows client metrics (total, active, onboarding, paused), filterable
  client table with status/tier/actions, plan tier breakdown, status overview, and content delivery stats.
  Includes inline Pause/Resume/Activate actions. Falls back to empty state when BAL is disabled.
- **Business API Client** (`src/warroom/src/api/business.ts`): TypeScript API module with full client
  types and fetcher functions for the Business Dashboard

## [8.2.2] - 2026-02-13

### Added
- **Telegram Connector** (`src/connectors/connectors/telegram.py`): Full Telegram Bot API connector
  following the governed connector framework pattern — 8 operations (get_updates, get_me, get_chat,
  get_file, send_message, send_voice, send_photo, delete_message) with proper ConnectorResult specs,
  credential vault integration, and risk tier classification
- **Connector Registry Entry**: Telegram added to `_CONNECTOR_CLASSES` in `connectors_api.py` for
  automatic discovery and instantiation
- **Connector Config**: Telegram entry in `config/connectors.yaml` (enabled: true) with rate limits
  (30 req/min, burst 5)

### Fixed
- **Credential Validation**: Inject vault reference into connector instances during validation so
  `validate_credentials()` works correctly — previously all connectors returned `valid: false` because
  the vault was never passed to connector instances created by `_instantiate_connector()`

## [8.2.1] - 2026-02-13

### Added
- **BAL Phase 2 — Client Manager:** Full client lifecycle management with CRUD API, state machine,
  and receipt emission
  - **Client Models** (`src/core/bal/clients/models.py`): 6 enums (ClientStatus, PlanTier,
    PaymentStatus, TonePreference, HashtagPolicy, EmojiPolicy) and 6 Pydantic models (Client,
    ClientCreate, ClientUpdate, ClientBilling, ClientPreferences, ContentHistory) with email
    validation, UUID generation, and JSON round-trip support
  - **Client Repository** (`src/core/bal/clients/repository.py`): Full CRUD against `bal_clients`
    table — create, get_by_id, get_by_email, list_all (with status filter), update (partial),
    update_status, update_billing, update_content_history, soft delete (sets status to CHURNED)
  - **Client State Machine** (`src/core/bal/clients/state_machine.py`): Deterministic lifecycle
    transitions — ONBOARDING->[ACTIVE,CHURNED], ACTIVE->[PAUSED,CHURNED], PAUSED->[ACTIVE,CHURNED],
    CHURNED->[] (terminal). Raises `InvalidTransitionError` for invalid transitions
  - **Client Events** (`src/core/bal/clients/events.py`): Receipt emission for all lifecycle events
    (onboarded, preferences_updated, status_changed, plan_changed, paused, churned) via BAL receipt
    system
  - **Client REST API** (`src/core/bal/clients/api.py`): 7 endpoints at `/api/v1/clients` — POST
    create (201), GET list, GET by id, PATCH update, POST pause/resume/activate. Feature-flag gated,
    409 on duplicate email, 422 on invalid transitions
  - **Schema V2 Migration**: Added `memory_block_id` column and unique email constraint to
    `bal_clients` table
  - **97 new tests** (25 models + 28 repository + 20 state machine + 6 events + 18 API) all passing
    with real SQLite databases

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
