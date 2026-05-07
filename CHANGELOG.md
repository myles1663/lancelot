# Changelog

Public-facing changes to Lancelot are documented here.

This file is intentionally concise and limited to product changes relevant to
external users and reviewers.

## [Unreleased]

No unreleased changes.

## [0.4.2] - 2026-05-07

### Added

- War Room Command Center now surfaces procedural recommendations that can be
  accepted, dismissed, snoozed, or converted into governed ActionCards.
- Procedural recommendations are operator-scoped, sensitivity-aware, and
  respect runtime kill-switch controls.
- Recommendation persistence now restores pending visibility when snoozes
  expire, so temporary deferral does not permanently hide useful suggestions.
- Release verification now includes explicit fresh-clone installer checks and
  prebuilt-image smoke gates before a release is considered install-ready.

### Changed

- Public release notes now call out the recommendation proof tests and the
  prebuilt-image verification path for release operators.

## [0.4.1] - 2026-05-03

### Added

- Command Center chat now uses persisted asynchronous run records so long
  governed turns surface queued, running, blocked, failed, and completed states
  instead of holding a browser request open.
- Async Command Center run cards now support cooperative cancellation and
  failed/cancelled run retry without reusing prior approvals.
- Async Command Center runs now persist progress events, phase timing, total
  elapsed time, and startup cleanup for stale queued/running records.
- Command Center active run cards now surface degraded privacy/scrub fallback
  progress while the run is still executing, rather than leaving the signal
  only in receipts and logs.
- Command Center active run cards now surface explicit wait reasons for
  governed worker-slot queueing, provider calls, approval pauses, and response
  proof finalization.
- Command Center active run cards now flag long quiet phases as slow progress
  with the last observed phase, so operators can distinguish a slow provider or
  tool wait from a frozen interface.
- Command Center now reconciles persisted async runs from the backend, so active
  governed work remains visible after a browser refresh or transient WebSocket
  event loss.
- Exact runtime status commands now use a deterministic fast path instead of
  entering the full classifier/model/tool loop.
- Read-only operational smoke report requests now use deterministic gateway,
  local-model role, and scheduler checks instead of relying on a model to infer
  which runtime surfaces to inspect.
- Approval cards now lead with operator-readable intent, bounded scope, and
  explicit non-scope before showing raw technical parameters.
- Approved governance ActionCards now resume the exact blocked async run they
  belong to instead of sending a generic `continue` chat turn, so grouped
  approvals resume deterministically and preserve the original request scope.
- Local model routing now supports named scrub and utility roles, optional
  Bonsai/Prism container profiles, and role-level readiness in Setup &
  Recovery.
- Local-model health now treats ready role-specific endpoints as a healthy
  local model lane, even when the legacy fallback `LOCAL_LLM_URL` endpoint is
  unavailable.

### Changed

- Frontier-bound scrubbing now uses deterministic pre-scrub and residual
  validation around local role verification so larger payloads can be protected
  without routing every byte through a frontier model.
- The local scrub region-finder now uses deterministic candidate filtering plus
  a Bonsai-sized default input budget, reducing unnecessary deterministic
  fallback on normal Command Center model payloads while preserving bounded
  latency guards.
- The supported install/update path now pulls prebuilt `lancelot-core` and
  local-model images by default, keeps local model builds as an explicit
  fallback, and documents the one-step
  `docker compose up -d --no-deps --build lancelot-core` command for live core
  rebuilds so refreshed frontend bundles actually reach the running container.
- Large frontier-bound payloads with no deterministic PII and no privacy
  semantic cues now take a deterministic-clean scrub fast path instead of
  invoking the local model scanner.
- Async Command Center retry now supports approved blocked runs and replays the
  retained original message through the governed path, allowing ActionCard
  resumes to target the correct blocked run without broadening approval scope.
- Deterministic frontier scrubbing now treats approver and lead/reviewer names
  as private names, protecting approval-chain and ownership context even if the
  local model verifier times out.
- Oversized local scrub region-finder inputs are now split into bounded
  numbered windows, with a deterministic fallback only when the window count
  would exceed the configured latency guard.
- Approval/proceed follow-ups and short acknowledgements now resolve before
  classifier/model routing, reducing latency for `continue`, `ok`, and related
  control replies.
- Docker startup now allows the War Room API to come up while a slow local
  model lane remains degraded rather than blocking the whole stack.

## [0.3.1] - 2026-04-21

### Highlights

- Hardened the core governance path around request validation, policy checks,
  feature-flag kill switches, and bounded error responses.
- Strengthened immutable receipt storage with staged finalization, integrity
  hashes, HMAC signing, and tamper-detection coverage.
- Expanded HIVE scoped-execution coverage so spawned tasks cannot widen their
  runtime authority through mutated payloads or injected scope fields.
- Expanded UAB routing and permission-risk coverage for governed desktop bridge
  operations, route fallback, daemon readiness, and action-risk taxonomy.
- Refined the README, architecture overview, and proof-point test list for
  skeptical technical review.

### Status

- Default installation exercises the core path: health checks, chat ingress,
  governance configuration, tool routing, and receipts.
- HIVE, Federation, MCP governance, A2A, Time Travel, Observability, and many
  UAB desktop-control paths remain feature-gated and should be evaluated as
  separate subsystems.
