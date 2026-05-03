# ADR 0003: Runtime-Toggleable Governed Subsystems

## Status

Accepted.

## Context

Lancelot includes capabilities with different operational weight and blast radius: core governance, receipts, HIVE, Federation, MCP governance, A2A, Time Travel, Observability, and UAB. Operators need one system that can run as a lighter local developer tool or as a higher-governance operator environment without changing products or forking deployments.

## Decision

Major capabilities are runtime-toggleable governed subsystems. Operators can enable or disable them through configuration or the War Room kill-switch controls, and the runtime reports disabled, degraded, and failed states distinctly.

## Consequences

- Operators can choose a deployment posture without recompiling, reinstalling, or switching products.
- High-blast-radius capabilities remain governed by explicit runtime controls.
- The default install can be inspected through governance, receipts, health checks, structured memory, and the core tool bridge first.
- Documentation, health APIs, and War Room views must clearly distinguish disabled, degraded, and failed states.
