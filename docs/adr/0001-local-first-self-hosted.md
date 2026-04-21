# ADR 0001: Local-First, Self-Hosted Control Plane

## Status

Accepted.

## Context

Lancelot governs automation that may touch local files, desktop applications, credentials, and operator workflows. Sending that control path through a hosted service would make the model provider and the governance vendor part of the trust boundary.

## Decision

Lancelot runs as a self-hosted local control plane by default. The model may be remote or local, but policy evaluation, feature flags, UAB routing, and receipt storage remain under the operator's deployment.

## Consequences

- Operators can inspect policy, receipts, and subsystem state locally.
- Deployment has more configuration surface than a managed SaaS.
- Health checks, startup validation, and feature gates matter because the operator owns runtime behavior.
