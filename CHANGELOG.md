# Changelog

Public-facing changes to Lancelot are documented here.

This file is intentionally concise and limited to product changes relevant to
external users and reviewers.

## [Unreleased]

No public-facing product changes yet.

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
