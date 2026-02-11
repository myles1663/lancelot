# Lancelot Changelog

## v7.6.0 — Approval Pattern Learning (APL) (2026-02-11)

Approval Pattern Learning system: 10 prompts (P65-P74), 151 tests passing.
Observes owner approve/deny decisions, detects repeating patterns, and proposes
automation rules. Owner confirms rules once — matching actions skip the approval
gate. Complementary to Trust Ledger: Trust answers "did the action succeed?",
APL answers "does the owner always say yes?"

### New Module: `src/core/governance/approval_learning/`

- **`config.py`** — APLConfig (Pydantic v2)
  - DetectionConfig: min_observations, confidence_threshold, max_pattern_dimensions
  - RulesConfig: max_active_rules, daily/total limits, cooldown_after_decline
  - PersistenceConfig: JSONL decision log, JSON rules/patterns storage
  - `is_never_automate()`: wildcard matching against Soul-protected capabilities

- **`models.py`** — All APL data models
  - DecisionContext: full action context (capability, target, temporal, payload hash)
  - DecisionRecord: immutable decision entry with timing and auto/manual flag
  - ApprovalPattern: multi-dimensional pattern with confidence scoring
  - AutomationRule: lifecycle (proposed → active → paused → revoked) with circuit breakers
  - RuleCheckResult: auto_approve / auto_deny / ask_owner

- **`decision_log.py`** — DecisionLog (append-only JSONL journal)
  - Thread-safe recording with persistence on every write
  - Filtered queries: by capability, target domain, time window
  - Analysis trigger tracking (count since last analysis)

- **`pattern_detector.py`** — PatternDetector
  - Single-dimension detection (capability, target_domain, target_category, scope, time, day)
  - Multi-dimensional extension (up to max_pattern_dimensions)
  - Specificity-first: proposes narrowest rule the data supports
  - Confidence = consistency rate x observation factor
  - Score = confidence x (1 + 0.2 x specificity)
  - Proposal generation with never_automate filtering

- **`rule_engine.py`** — RuleEngine
  - Rule lifecycle: add_proposal, activate, decline, pause, resume, revoke
  - Runtime matching: deny wins over approve, most specific wins
  - Circuit breakers: daily limit per rule, total limit with re-confirmation
  - Cooldown tracking for declined patterns
  - JSON persistence with full roundtrip

- **`analyzer.py`** — APLAnalyzer (periodic analysis trigger)
  - Runs when decisions since last analysis >= analysis_trigger_interval
  - Full pipeline: detect_all → generate_proposals → filter declined → add to engine

- **`orchestrator_wiring.py`** — Orchestrator integration helpers
  - build_decision_context: extracts capability, target, tier from plan steps
  - ApprovalRecorder: records manual and auto decisions to DecisionLog

- **`war_room_panel.py`** — APL dashboard data
  - Summary: active/proposed/paused/revoked rules, automation rate
  - Active rules with usage stats, pending proposals
  - Circuit breaker and re-confirmation alerts
  - Recent decisions with auto/manual flag

### Configuration

- **`config/approval_learning.yaml`** — APL defaults
  - Detection: 20 min observations, 0.85 confidence threshold, 30-day window
  - Rules: 50 max active, 50/day limit, 500 total before re-confirmation
  - Never-automate: Stripe charges/refunds/invoices, all delete operations

### Safety Architecture (7 properties)

1. Owner controls everything — rules only activate after explicit confirmation
2. Deny wins over approve — conservative by default
3. Circuit breakers — daily limits prevent runaway automation
4. Rules expire — total limit requires periodic re-confirmation
5. Full receipt trail — auto-approved actions still emit receipts
6. Soul overrides APL — never_automate list cannot be bypassed
7. Instant revocation — any rule can be revoked immediately

### Feature Flag

- `FEATURE_APPROVAL_LEARNING` — default: false

### Tests (151 total)

- test_apl_config.py (14) — Feature flag, YAML loading, never_automate wildcards
- test_apl_models.py (33) — All data models, confidence, specificity, matching
- test_decision_log.py (15) — JSONL persistence, queries, restart survival
- test_apl_orchestrator_wiring.py (12) — Context building, approval recording
- test_pattern_detector.py (22) — Single/multi-dim detection, scoring, analysis trigger
- test_rule_proposals.py (11) — Proposal generation, never_automate filtering
- test_rule_engine.py (20) — Rule lifecycle, matching, circuit breakers, persistence
- test_apl_integration.py (11) — Full integration, analyzer, lifecycle
- test_apl_hardening.py (13) — Deny-wins, circuit breaker, re-confirmation, never-automate, cooldown, specificity preference, concurrent safety, persistence survival, time boundaries, full E2E lifecycle

---

## v7.5.0 — Capability Upgrade Phase 4: Business Automation PoC (2026-02-11)

End-to-end content repurposing business automation: 6 prompts (P59-P64), 41 tests passing.
Complete pipeline from content intake through multi-format repurposing, quality verification,
delivery packaging, and capstone integration test proving all systems work together.

### New Modules

- **`src/business/skills/content_intake.py`** — ContentIntakeSkill
  - Parses raw text into structured format (title, body, word count, type, topics)
  - Content type identification: blog_post, transcript, newsletter, article
  - Keyword extraction via word frequency (stop word filtering)
  - Quality validation: minimum word count, title detection

- **`src/business/skills/content_repurpose.py`** — ContentRepurposeSkill
  - generate_tweets: <=280 chars, configurable count
  - generate_linkedin_posts: 200-500 words, professional format
  - generate_email_snippets: newsletter-style with CTA
  - generate_instagram_caption: <=2200 chars with hashtags
  - repurpose_all: all formats in one call

- **`src/business/skills/quality_verify.py`** — QualityVerifySkill
  - Per-format verification: length limits, no placeholders, word count ranges
  - Aggregate QualityResult with score (0.0-1.0) and per-format breakdown

- **`src/business/skills/delivery.py`** — DeliverySkill
  - format_email_package: EmailConnector-compatible params
  - create_delivery_schedule: time-spaced posting schedule
  - prepare_social_posts: platform-specific formatting

- **`src/business/soul_config.py`** — Business Soul Configuration
  - Stripe operations locked at T3 (financial ops always require approval)
  - Email to non-verified recipients locked at T3
  - Trust graduation ceilings per capability

- **`src/business/war_room_business.py`** — Business War Room Dashboard
  - Pipeline status, trust status, connector health, governance efficiency

### Capstone Integration Test

End-to-end test exercising: skill security pipeline install, full content
pipeline (intake→repurpose→verify→deliver), trust graduation simulation
(50 successes→proposal→approve→revoke on failure), Soul enforcement
(Stripe always T3, email non-verified always T3), and War Room display
with tier distribution metrics.

---

## v7.4.0 — Capability Upgrade Phase 3: Skill Security Pipeline (2026-02-11)

6-stage skill security pipeline: 11 prompts (P48-P58), 82 tests passing.
Every skill must pass manifest validation, static analysis, and sandbox testing
before installation. Runtime enforcement blocks undeclared capabilities.

### New Modules

- **`src/skills/security/manifest.py`** — SkillManifest schema (Pydantic v2)
  - Capability, credential, and domain declarations
  - Cross-field validation: credentials need domains, community needs does_not_access
  - audit() method for warnings/errors/info

- **`src/skills/security/static_analyzer.py`** — Source code scanner
  - 14 built-in patterns (8 CRITICAL, 4 WARNING, 2 INFO)
  - Detects: network imports, subprocess, eval/exec, ctypes, signal handlers
  - Custom pattern support via add_custom_pattern()
  - Directory and single-source scanning

- **`src/skills/security/sandbox_tester.py`** — Docker sandbox testing
  - Sibling containers via mounted Docker socket
  - --network=none, --read-only, memory/CPU limits, non-root user
  - Violation monitoring: network, filesystem, process, resource
  - Graceful skip if Docker unavailable

- **`src/skills/security/capability_enforcer.py`** — Runtime enforcement
  - Per-skill approved capabilities, domains, and vault keys
  - Enforcement hooks for blocking undeclared actions (PermissionError)
  - Active skill tracking for execution context

- **`src/skills/security/pipeline.py`** — 6-stage orchestrator
  - Stage 1: Manifest validation
  - Stage 2: Static analysis (CRITICAL findings block)
  - Stage 3: Sandbox testing (violations block)
  - Stage 4: Owner review (external)
  - Stage 5: Capability enforcement registration
  - Stage 6: Trust ledger initialization

- **`src/skills/marketplace/source_tiers.py`** — Source tier policies
  - First-party: auto-approve reads, skip community review
  - Community: requires community review
  - User: skip community review, no auto-approve

- **`src/skills/marketplace/reputation.py`** — Reputation scoring
  - Weighted score: stars(+2x), installs(+0.5x), issues(-1x), security(-3x)
  - Version-based rescan tracking
  - Security issue flagging

### Security Hardening

- End-to-end malicious skill defense test: skill with import requests,
  subprocess, os.system, and eval is caught at Stage 2 (static analysis)
- Unregistered skills blocked at enforcer even if somehow bypassing pipeline

---

## v7.3.0 — Capability Upgrade Phase 2C: Trust Ledger (2026-02-11)

Progressive tier relaxation system: 7 prompts (P41-P47), 69 tests passing.
Capabilities earn lower risk tiers through consecutive successes, with snap-back on failure.

### New Modules

- **`config/trust_graduation.yaml`** — Graduation thresholds and revocation policy
  - T3→T2: 50 successes, T2→T1: 100, T1→T0: 200
  - Failure revocation: reset to default tier, rollback: reset above default
  - Cooldown: 50 after denial, 25 after revocation

- **`src/core/governance/trust_models.py`** — Trust data models
  - TrustRecord: per-capability success/failure tracking with graduation history
  - GraduationProposal: pending tier transition requests
  - GraduationEvent: audit trail for tier changes
  - Pydantic config: TrustGraduationConfig, TrustGraduationThresholds, TrustRevocationConfig

- **`src/core/governance/trust_ledger.py`** — Trust Ledger engine
  - record_success/record_failure: increment counts, check graduation thresholds
  - check_graduation: propose tier lowering when threshold met
  - apply_graduation: owner approve/deny with cooldown on denial
  - revoke_trust: snap-back on failure (reset_to_default) or rollback (reset_above_default)
  - simulate_timeline: preview graduation events without modifying state
  - initialize_from_connector: bulk-create trust records from connector operations

### Modified Modules

- **`src/core/governance/risk_classifier.py`** — Added Layer 4 (Trust Adjustment)
  - After Soul escalation, checks TrustLedger for graduated tiers
  - Trust can only LOWER tiers, never raise — Soul floor always wins
  - Gated behind FEATURE_TRUST_LEDGER feature flag

- **`src/connectors/governed_proxy.py`** — Trust Ledger integration
  - execute_governed records success/failure in trust ledger after HTTP execution
  - handle_rollback method for recording rollback failures with is_rollback=True

- **`src/core/governance/war_room_panel.py`** — Trust visualization
  - render_trust_panel: summary, per-connector breakdown, proposals, recent events
  - format_graduation_proposal: human-readable graduation proposal text

### Security Hardening (P47)

- Tier floor enforcement: soul_minimum prevents graduation past floor
- Cross-scope isolation: successes in one scope don't affect another
- Proposal replay protection: denied proposals set cooldown
- Cooldown enforcement: graduation blocked during cooldown period
- Rollback severity: rollback failures snap to default+1 (capped at T3)
- Thread safety: concurrent record_success calls produce correct counts
- Trust never raises: effective tier only applied when lower than config default
- No tier skipping: graduation must proceed T3→T2→T1→T0 sequentially

---

## v7.2.0 — Capability Upgrade Phase 2B: First-Party Connectors (2026-02-11)

Four first-party connectors: 6 prompts (P35-P40), 72 tests passing.
Each connector produces HTTP request specs — never touches the network directly.

### New Connectors

- **EmailConnector** (`connectors/email.py`) — Gmail API: 7 operations
  - Read: list_messages, get_message, search_messages (T1)
  - Write: send_message (T3), reply_message (T3), move_to_folder (T2)
  - Delete: delete_message (T3)
  - RFC 2822 + base64url encoding for send operations

- **SlackConnector** (`connectors/slack.py`) — Slack Web API: 7 operations
  - Read: read_channels (T0), read_messages (T1), read_threads (T1)
  - Write: post_message (T2), add_reaction (T1), upload_file (T2)
  - Delete: delete_message (T3)

- **CalendarConnector** (`connectors/calendar.py`) — Google Calendar API: 6 operations
  - Read: read_events (T0), read_availability (T0)
  - Write: create_event (T2), update_event (T2), send_invite (T3)
  - Delete: delete_event (T3)

- **GenericRESTConnector** (`connectors/generic_rest.py`) — User-configurable
  - Dynamic operation generation from config
  - Input validation: HTTPS-only, SSRF prevention, path traversal, injection prevention
  - Max 50 endpoints, param name sanitization, wildcard domain rejection

---

## v7.1.0 — Capability Upgrade Phase 2A: Connector Foundation (2026-02-11)

Complete connector infrastructure: 10 prompts (P25-P34), 149 tests passing.
This phase builds the foundation for external integrations with full governance.

### New Modules

- **`src/connectors/`** — Connector subsystem package
  - `base.py` — ConnectorBase, ConnectorManifest, ConnectorStatus, CredentialSpec
  - `models.py` — ConnectorOperation, ConnectorResult, ConnectorResponse, HTTPMethod, ParameterSpec
  - `registry.py` — ConnectorRegistry with YAML config, thread-safe registration
  - `vault.py` — CredentialVault (Fernet encryption), VaultAccessPolicy (scoped access)
  - `rate_limiter.py` — Token bucket RateLimiter, RateLimiterRegistry (per-connector)
  - `proxy.py` — ConnectorProxy (sync HTTP via requests), DomainValidator
  - `governed_proxy.py` — GovernedConnectorProxy (risk classification + policy + receipts)
  - `credential_api.py` — FastAPI endpoints for credential onboarding
  - `connectors/test_echo.py` — EchoConnector (httpbin.org integration test connector)

### New Config Files

- **`config/connectors.yaml`** — Connector settings, rate limits, per-connector config
- **`config/vault.yaml`** — Credential vault encryption and audit config

### Modified Files

- **`src/core/feature_flags.py`** — Added FEATURE_CONNECTORS, FEATURE_TRUST_LEDGER, FEATURE_SKILL_SECURITY_PIPELINE
- **`src/tools/contracts.py`** — Extended Capability enum: CONNECTOR_READ, CONNECTOR_WRITE, CONNECTOR_DELETE, CREDENTIAL_READ, CREDENTIAL_WRITE

### Key Architecture Decisions

- **Connectors never touch the network** — they produce HTTP request specs; ConnectorProxy executes them
- **Synchronous execution** — all backend uses `requests` library, matching existing orchestrator pattern
- **Scoped vault access** — connectors can only retrieve credentials declared in their manifest
- **Token bucket rate limiting** — per-connector, thread-safe, configurable via YAML
- **Domain allowlisting** — ConnectorProxy validates URLs against manifest.target_domains

---

## v7.0.6 — Conversational Intelligence Upgrade (2026-02-11)

Five targeted changes to unblock the recursive memory pipeline and make Lancelot
maintain multi-turn conversational intelligence — remembering prior context,
distinguishing related concepts, and proactively offering options across messages.

### 1. History Depth Increase (10 → 50 messages)
Previous: only 10 messages (1000 chars each) passed to the model, losing context
after a few turns. Now 50 messages at up to 4000 chars each are included.

- **`src/core/context_env.py`**:
  - `get_history_string()` default limit: `10` → `50`
  - Message truncation threshold: `1000` → `4000` chars
  - History storage cap: `100` → `200` entries
  - `get_context_string()` explicit `limit=50` for history

### 2. Episodic Memory Retrieval Budget (8K → 16K tokens)
Doubled the token budget for episodic memory retrieval, allowing more relevant
past conversations to surface when user references prior discussions.

- **`src/core/memory/config.py`**:
  - `MAX_RETRIEVAL_TOKENS`: `8000` → `16000`

### 3. Slim System Instruction (V16 → V17)
Replaced ~4,500 char detailed architecture block with a ~750 char identity core.
Detailed architecture stays in CAPABILITIES.md (loaded into file context at boot).
Frees ~940 tokens of model attention for actual user context.

- **`src/core/orchestrator.py`**:
  - `_build_self_awareness()` rewritten: V16 (verbose) → V17 (identity core only)
  - Architecture details delegated to CAPABILITIES.md reference

### 4. Continuation Detection
Short follow-up messages ("what about that?", "yes do it", "the spec") now stay
in the agentic loop instead of being fragmented into PlanningPipeline. This keeps
multi-turn conversations coherent.

- **`src/core/orchestrator.py`**:
  - Added `_is_continuation()` method — detects short reference messages
  - Modified `chat()` routing: continuation messages reroute from PLAN/EXEC/MIXED
    intent to KNOWLEDGE_REQUEST (agentic loop with full history + tools)

### 5. Smart Model Routing + Auto-Escalation
Replaced the rudimentary `_route_model()` (always returned Flash) with intelligent
routing that escalates to the deep model for complex reasoning tasks.

- **`src/core/orchestrator.py`**:
  - `_route_model()` rewritten: detects deep task keywords (plan, architect, analyze,
    compare, debug, etc.), risk keywords (deploy, production, security), and
    complexity phrases (step by step, pros and cons, best approach)
  - Added `_get_deep_model()` — returns `GEMINI_DEEP_MODEL` env var with validation
    and caching, graceful fallback to Flash
  - Added auto-escalation: if Flash returns a thin response (<100 chars) for a
    non-trivial query (>200 chars), retries once with the deep model transparently
- **`config/models.yaml`**:
  - Gemini deep lane model: `gemini-2.0-pro` → `gemini-2.5-pro`
  - Deep lane max_tokens: `8192` → `16384`
- **`.env`**:
  - Added `GEMINI_DEEP_MODEL=gemini-2.5-pro`

### Verification
- 15+ message conversations maintain full context
- Complex queries (analyze, plan, debug) route to deep model (visible in Docker logs)
- Short follow-ups ("what about X?") stay in agentic loop, not PlanningPipeline
- Identity questions still accurate (CAPABILITIES.md in file context)
- Thin Flash responses auto-escalate to deep model transparently

---

## v7.0.5 — API Retry Logic + Self-Awareness V16 (2026-02-11)

### 429 Retry with Exponential Backoff
Added automatic retry with exponential backoff for transient Gemini API errors
(429 RESOURCE_EXHAUSTED, 503 Service Unavailable). Previously, any 429 error
immediately returned an error string to the user. Now retries up to 3 times
with 1s → 2s → 4s delays before giving up.

- **`src/core/orchestrator.py`**:
  - Added `_is_retryable_error()` static method — detects 429/503/RESOURCE_EXHAUSTED
  - Added `_gemini_call_with_retry()` — generic retry wrapper with configurable
    max_retries and base_delay
  - Wired retry into all 5 Gemini API call sites:
    - `_agentic_generate()` — main agentic loop
    - `_text_only_generate()` — text-only generation
    - `_enrich_plan_with_llm()` — plan enrichment
    - `_execute_plan_via_llm()` — LLM-backed plan execution
    - `_summarize_execution_results()` — result summarization

### Self-Awareness V16
Enhanced `_build_self_awareness()` to provide detailed, accurate descriptions of
Lancelot's architecture when asked about itself. Fixes issue where Lancelot gave
generic LLM answers ("as a language model, I don't have recursive memory...") when
asked about its memory system.

- **`src/core/orchestrator.py`**:
  - Rewrote `_build_self_awareness()` (V15 → V16):
    - Added "MANDATORY IDENTITY" directive at the top
    - Detailed recursive memory explanation (episodic → context → response → new memory loop)
    - All 5 memory tiers described (Core, Episodic, Working, Archival, File Context)
    - Added vNext4 Risk-Tiered Governance to architecture section
    - Explicit instruction for memory/identity questions
  - Added identity-related keywords to `_is_simple_for_local()` complex_keywords:
    "tell me about", "describe your", "how do you", "your memory", "your architecture"
    — ensures identity questions always route to Gemini (not local LLM)

- **`lancelot_data/CAPABILITIES.md`**:
  - Updated Memory section with recursive memory description
  - Added Risk-Tiered Governance (vNext4) entry

---

## vNext4 — Risk-Tiered Governance & Performance Pipeline (2026-02-11)

**Architecture:** docs/architecture/governance.md
**Runbook:** docs/operations/runbooks/governance.md

### Added
- Risk-tiered governance system (T0-T3) with proportional verification overhead
- `src/core/governance/` module with 9 submodules:
  - `models.py` — RiskTier enum, VerificationStatus, ActionRiskProfile data types
  - `config.py` — Pydantic v2 config loader for governance.yaml
  - `risk_classifier.py` — Classifier with scope, pattern, and Soul escalation
  - `policy_cache.py` — Boot-time precomputed policy decisions for T0/T1 (O(1) lookup)
  - `async_verifier.py` — Background verification queue for T1 actions
  - `rollback.py` — Pre-execution snapshot system with automatic file rollback
  - `intent_templates.py` — Cached plan skeleton registry with promotion lifecycle
  - `batch_receipts.py` — Batched receipt emission with tier boundary flush
  - `war_room_panel.py` — Streamlit governance metrics panel
- `config/governance.yaml` — Risk classification defaults (14 capabilities), 3 scope escalations
- 5 new feature flags (all default false, gated behind master switch):
  - `FEATURE_RISK_TIERED_GOVERNANCE` (master), `FEATURE_POLICY_CACHE`,
    `FEATURE_ASYNC_VERIFICATION`, `FEATURE_INTENT_TEMPLATES`, `FEATURE_BATCH_RECEIPTS`
- 231 governance tests across 15 test files covering:
  - Unit tests for all modules
  - T1 pipeline integration tests
  - Full tiered execution tests
  - T2/T3 boundary enforcement tests
  - Security hardening tests (tier downgrade attacks, cache poisoning, template injection)
  - High-volume stress tests and graceful shutdown

### Changed
- `src/core/orchestrator.py`:
  - `execute_plan()` rewritten with full risk-tiered pipeline (T0/T1/T2/T3)
  - Added `_init_governance()` for subsystem initialization
  - Added `_execute_step_tool()` helper and `_request_approval()` for T3 gate
  - Added governance imports with conditional loading
  - Legacy path preserved when `FEATURE_RISK_TIERED_GOVERNANCE=false`
- `src/core/feature_flags.py` — 5 new governance flags with reload and logging support

### Security
- Soul escalation overrides prevent tier downgrade (only escalates up, never down)
- Policy cache validates Soul version on every lookup
- Intent templates cannot contain T2+ actions (enforced at creation)
- T2/T3 boundary enforcement flushes all pending T0/T1 work before execution
- Unknown capabilities default to T3 (fail-safe)
- Rollback is idempotent (double rollback is no-op)
- SHA-256 integrity hashing on all batch receipt entries

---

## v7.0.4 — Self-Awareness Fix (2026-02-11)

### Summary

Fixed Lancelot's inability to describe its own architecture. Previously, when asked about
its memory system or capabilities, Lancelot gave generic LLM answers ("As a large language
model, I don't have recursive memory...") instead of describing its actual Memory vNext,
receipt system, cognition governor, and other architectural components.

### Root Cause

Five issues prevented self-awareness:
1. **Persona core block was empty** — no identity or architecture description in memory
2. **Operating rules block was empty** — no behavioral principles loaded
3. **Mission block was "staged"** — never activated, contained placeholder text
4. **CAPABILITIES.md and RULES.md missing from data root** — files existed in `Technical/`
   subdirectory but `context_env.py` loads from root `lancelot_data/`
5. **`_build_self_awareness()` was minimal** — mentioned deployment and skills but nothing
   about Memory vNext, receipts, governance, model routing, or architecture

### Changes

- **`lancelot_data/memory/core_blocks.json`**:
  - Populated `persona` block with full architectural identity (Memory vNext, receipts,
    cognition governor, model routing, tool fabric, soul contract, multimodal, workspace)
  - Populated `operating_rules` block with core principles (honesty, tool-forward, self-aware
    responses, governed operation, workspace rules)
  - Activated `mission` block with real mission statement (was "staged" with placeholder)
  - Activated `workspace_state` block with current system state

- **`lancelot_data/CAPABILITIES.md`** (CREATED):
  - Full capabilities document at data root where `context_env.py` expects it
  - Covers architecture, deployment, communication, skills, agentic execution, limits

- **`lancelot_data/RULES.md`** (CREATED):
  - Operating rules at data root where `context_env.py` expects it
  - Core principles, identity rules, security rules, communication rules, workspace rules

- **`src/core/orchestrator.py`**:
  - Expanded `_build_self_awareness()` with full architecture description
  - Includes Memory vNext tiers, receipt system, cognition governor, model routing,
    soul contract, cost tracking, agentic execution, multimodal, workspace
  - Explicit instruction to never give generic "as an AI" answers about itself

---

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
