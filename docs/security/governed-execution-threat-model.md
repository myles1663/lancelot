# Governed Execution Threat Model

## Scope

This model covers governed execution paths that matter to the current hardening
epic: policy decisions, tool execution, UAB enforcement, receipts, runtime spine
boundaries, and proof evidence.

## Threats

| Threat | Risk | Control |
|---|---|---|
| Prompt injection | Model or user content attempts to override policy. | Soul and Policy Engine remain outside model context; suspicious input produces auditable refusal paths. |
| Tool output injection | Tool output attempts to become instructions or policy. | Tool output is untrusted input and must not modify policy or authority. |
| Approval bypass | Runtime action executes without required governance. | Central policy decision path, grants for governed UAB actions, fail-closed checks. |
| Receipt tampering | Proof artifacts are altered after execution. | Canonical receipt integrity, finalization, hash/HMAC verification. |
| Local audit substitution | Non-canonical logs are treated as proof. | UAB local audit cannot replace canonical receipts. |
| Runtime spine tangling | Gateway, orchestrator, receipts, memory, or UAB become mutually coupled. | Compatibility facades plus import-boundary tests. |
| Risk terminology drift | Different layers classify risk inconsistently. | Explicit mapping and drift tests. |
| Evidence overclaim | Documentation claims enterprise readiness without matching tests or runtime proof. | Evidence manifest validation plus documentation disposition table. |

## Evidence Standard

Tests alone are not sufficient for non-code-only governance-security work. Each
behavioral or proof change must also produce external evidence such as receipts,
API payloads, denial records, generated artifacts, coverage reports, import-
boundary results, or before/after hashes where applicable.

Closeout documentation must tie every enterprise claim to at least one doc
location, one behavior test or runtime proof, and one artifact or explicit
limitation. Served-runtime claims require operator smoke proof or a waiver with
blocker, risk, owner, and follow-up command.
