# ADR 0002: SQLite-Backed Receipts

## Status

Accepted.

## Context

Receipts need to be durable, inspectable, easy to back up, and usable in local development without requiring an external database. The first production constraint is auditability of a single deployment, not multi-tenant analytics.

## Decision

Lancelot stores receipts in SQLite. Pending actions are staged separately and finalized into an append-only receipt log with integrity fields.

## Consequences

- The audit trail works in a local install without additional infrastructure.
- Operators can copy and inspect the database with standard tools.
- Horizontal aggregation belongs in export, federation, or observability layers rather than the core receipt store.
