# Changelog

All notable changes to Project Lancelot will be documented in this file.

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
