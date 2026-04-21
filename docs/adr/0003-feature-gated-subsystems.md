# ADR 0003: Feature-Gated Subsystems

## Status

Accepted.

## Context

Lancelot includes subsystems with very different blast radii: core governance, receipts, HIVE, Federation, MCP governance, A2A, Time Travel, Observability, and UAB. Treating all of them as always-on would make the default deployment harder to reason about.

## Decision

Major subsystems are feature-gated. High-risk or integration-heavy capabilities default off unless explicitly enabled by configuration or operator action.

## Consequences

- The default install can be inspected through the core governance and receipt path first.
- Experimental or environment-dependent subsystems do not have to block the base stack.
- Documentation and readiness reporting must clearly distinguish disabled, degraded, and failed states.
