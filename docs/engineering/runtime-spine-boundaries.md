# Runtime Spine Boundaries

This document defines the ownership boundaries for the Governance Spine
Hardening epic. It is an engineering map for refactors and import-boundary tests;
the current code remains the behavioral source of truth.

## Boundary Map

| Area | Responsibility | Must not own |
|---|---|---|
| Governance models | Policy concepts, risk tiers, approvals, authority decisions. | Gateway assembly, UAB daemon details, War Room UI. |
| Gateway modules `src/core/gateway*.py` | FastAPI assembly, route mounting, health/readiness wiring, subsystem support. | Direct UAB daemon/provider internals or policy decision logic. |
| Boot and boot phase modules | `boot.py` coordinates startup order; `boot_routes.py`, `boot_core_apis.py`, `boot_subsystems.py`, `boot_connectors.py`, and `boot_observability_support.py` own focused boot phases. | Business logic, receipt storage internals, direct desktop automation enforcement, or unrelated runtime behavior. |
| Orchestrator and orchestrator helper modules | `orchestrator.py` coordinates public runtime state and method compatibility; `orchestrator_planning.py` owns LLM-backed plan enrichment and execution-result summaries alongside the existing focused provider, governance, frontier, routing, response, and context helpers. | Receipt storage details, UAB daemon internals, memory persistence internals, or standalone UAB package internals. |
| Receipt facade | Stable public receipt API for staging, finalization, verification, and lookup. | Gateway route assembly or UI rendering. |
| Receipt store/integrity modules | Durable receipt persistence, hash/HMAC integrity, migrations. | Gateway, orchestrator, War Room, or UAB daemon routing. |
| Memory persistence | `sqlite_store.py` preserves the public SQLite memory store facade; `sqlite_schema.py` owns SQLite schema DDL/version recording; `sqlite_codec.py` owns row serialization for memory items. | Orchestrator control flow, gateway assembly, UAB internals, or receipt storage internals. |
| UAB adapter/service boundary | The only Lancelot-to-UAB integration path for provider registration, health, grant propagation, and receipt metadata handoff. | Independent policy decisions. |
| UAB TypeScript daemon | Local policy enforcement point for desktop actions. | Python governance policy decision logic. |

## Import-Boundary Intent

The hardening epic should leave compatibility facades in place while moving large
spine files into smaller modules. Import-boundary tests should reject new tangles
instead of only documenting them.

Required executable rules, enforced by `tests/test_uab_import_boundaries.py`:

- `src/core/gateway*.py`, `src/core/boot*.py`, and
  `src/core/orchestrator*.py` must not import UAB internals directly.
- Only the approved UAB adapter/service boundary may import UAB provider or
  daemon integration details.
- Receipt models and stores must not import gateway or UI modules.
- Memory persistence must not import orchestrator.
- Governance models in `src/core/governance/models.py`,
  `src/core/governance/trust_models.py`, and
  `src/core/governance/approval_learning/models.py` must not import gateway,
  UAB, orchestrator, War Room, or UI surfaces.
- UAB daemon code must not import Python policy decision logic.

The boundary tests also include hostile detector fixtures for UAB alias escapes,
relative import escapes, orchestrator sibling-prefix false positives, and UAB
daemon policy marker leaks.

## UAB Standalone Constraint

UAB is both embedded in Lancelot and maintained as a standalone product. Lancelot
Core should therefore depend on a narrow adapter/service contract instead of
reaching into UAB daemon, connector, router, transport, or permission internals
from gateway or orchestration surfaces. That keeps standalone UAB updates easier
to merge back into the embedded Lancelot version.

The adapter boundary is also the documentation boundary for authority grants and
canonical receipt handoff. See `docs/engineering/uab-governance-boundary.md` for
the policy-enforcement-point contract and denied-versus-failed outcome
semantics.
