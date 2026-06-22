# ADR 0004: UAB Central Authority Boundary

## Status

Accepted.

## Context

Lancelot embeds UAB for local desktop automation and also maintains UAB as a
standalone product. UAB has local safety logic, but Lancelot's governance claim
requires the Python governance core to remain the authority source for mutating,
destructive, external, credential-sensitive, and sensitive-read actions.

## Decision

The Python governance core is the policy decision point for governed UAB actions.
The UAB TypeScript runtime is a policy enforcement point that validates scoped
authority grants and fails closed when authority is missing, invalid, expired,
tampered, replayed, or mismatched.

Lancelot Core integrates with UAB through a narrow adapter/service boundary so
the embedded UAB package remains compatible with standalone UAB development.

## Consequences

- UAB may enforce grants but must not become an independent policy decision
  system for Lancelot.
- Gateway and orchestration modules must not import UAB daemon, connector,
  router, transport, permission, or provider internals directly.
- HIVE and other callers must receive UAB authority through central governance.
- UAB local audit remains telemetry; canonical receipts remain the proof system.
