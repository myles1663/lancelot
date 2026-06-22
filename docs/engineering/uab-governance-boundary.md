# UAB Governance Boundary

This document defines how Lancelot Core treats the embedded Universal
Application Bridge (UAB). UAB is a policy enforcement point for desktop actions;
it is not the policy source of truth. Governance decisions originate in Core and
are carried to UAB through signed authority grants and canonical receipt
metadata.

## Boundary Rules

| Subject | Core owns | UAB owns |
|---|---|---|
| Policy decision | Risk classification, approval state, grant issuance, receipt context. | Enforcing supplied authority before desktop action execution. |
| Authority grant | Deterministic payload, HMAC signature, expiry, target/action/scope, nonce. | Signature, expiry, scope, flag, and replay validation before action dispatch. |
| Risk terminology | Canonical translation between governance, Tool Fabric, and UAB labels. | Consuming mapped labels and failing closed on unknown or unmapped risk. |
| Receipts | Canonical outcome receipts through the Core receipt service. | Local audit telemetry and UAB outcome metadata only. |
| Runtime integration | `src/core/uab_runtime_adapter.py` and `src/tools/providers/uab_bridge.py`. | Standalone daemon, connectors, transport, permissions, and plugin internals. |

Gateway, boot, and orchestrator modules must not import UAB daemon/provider
internals directly. They should depend on the Core UAB runtime adapter so the
standalone UAB package can evolve independently and be merged back into
Lancelot's embedded version with less coupling.

## Outcome Semantics

Denied and failed outcomes are intentionally distinct even when both surface as
receipt failure status:

- `denied` means governance or authority validation rejected the action before
  execution.
- `failed` means an authorized action or provider path failed during execution.

Consumers must read `metadata.uab_receipt_metadata.outcome` and the stable
reason fields instead of inferring outcome semantics from receipt status alone.

## Proof Requirements

UAB hardening claims require both executable proof and external evidence:

- Grant and permission behavior is covered by hostile allow/deny tests.
- Canonical UAB receipts are covered by provider, adapter, Tool Fabric, and
  helper-path tests.
- Import boundaries are covered by `tests/test_uab_import_boundaries.py`.
- Closeout evidence is tracked in
  `docs/internal/planning/governance-spine-hardening-closeout-evidence.md` and
  the generated evidence manifest under `artifacts/`.
