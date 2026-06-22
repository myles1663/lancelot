# Governance Spine Test Strategy

The Governance Spine Hardening epic uses tests as proof of control behavior, not
as line-count padding. Each hardening ticket must identify its evidence class
before implementation and run the narrowest relevant verification after changes.

## Required Test Categories

| Category | Purpose |
|---|---|
| Baseline contract tests | Freeze behavior before receipt, gateway, boot, orchestrator, and memory refactors. |
| Hostile UAB tests | Prove unsafe UAB actions fail closed without central authority. |
| Receipt tests | Prove canonical receipt behavior, integrity, finalization, and UAB metadata wiring. |
| Receipt outcome tests | Prove denied and failed UAB outcomes remain distinguishable through canonical metadata. |
| Import-boundary tests | Prevent gateway, receipt, memory, governance, and UAB responsibilities from tangling again. |
| Drift tests | Fail on unknown risk labels, unclassified UAB actions, and unmapped terminology. |
| Documentation checks | Prove boundary, threat model, coverage, ADR, and readiness docs stayed aligned. |

## Hostile UAB Cases

Required hostile cases include:

- Mutating UAB action without a valid central grant is denied.
- Destructive UAB action without a valid central grant is denied.
- Sensitive read without classification or grant is denied or escalated.
- Read-only non-sensitive action remains available.
- Expired, tampered, wrong-app, wrong-PID, wrong-action, and replayed grants are
  denied.
- HIVE governance denial produces no grant.
- Missing HIVE governance fails closed for governed actions.
- Successful, denied, and failed UAB outcomes produce structured receipt
  metadata, then canonical receipts after the receipt facade exists.
- Denied and failed outcomes remain queryable as distinct UAB receipt outcomes
  even when both map to receipt failure status.

## Import-Boundary Rules

The executable boundary tests must cover these rules:

- Governance models do not import gateway, orchestrator, UAB, or War Room.
- Receipt models do not import gateway or orchestrator.
- Receipt store does not import UI or gateway.
- Gateway subsystem modules matching `src/core/gateway*.py` do not import UAB
  internals directly.
- Only the approved UAB adapter or service boundary imports UAB provider or
  daemon integration details.
- UAB daemon code does not import Python policy decision logic.
- Memory persistence does not import orchestrator.
- UAB receipt payload builders emit through the canonical receipt service once
  CORE-B3 is complete.

The gateway rule intentionally covers the current gateway layout with
`src/core/gateway*.py`, not only `src/core/gateway.py`. This keeps the rule
wide enough for split gateway helper modules while still preserving UAB as a
standalone-maintainable package behind the approved adapter boundary.

## Test Quality Checklist

A governance-spine test should usually prove one of these outcomes:

- unsafe action denied
- approved action allowed
- missing authority fails closed
- tampered or expired authority fails closed
- scoped Soul violation fails closed
- policy denial includes a stable reason
- canonical receipt emitted or receipt tampering detected
- local UAB audit does not substitute for canonical proof
- invalid import or dependency rejected
- risk label drift rejected
- public behavior preserved through a facade
