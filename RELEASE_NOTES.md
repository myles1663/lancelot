# Release Notes

## Current Public Release: 0.4.4

Lancelot 0.4.4 adds the governed Soul template library and the War Room
operator controls needed to inspect, adapt, and test Soul behavior before
runtime use. The release includes structured governance fields for domain
templates, a read-only Soul behavior evaluator, operator-managed behavior
contracts, connector-policy enforcement, and admin-gated Soul diagnostics. The
core path remains self-hosted: requests enter through the gateway, policy and
Soul constraints are checked before execution, approved work runs through
governed tools or UAB routes, and outcomes are written as immutable receipts.

### What To Inspect First

- `README.md` for the quickstart and architecture summary.
- `docs/security-overview.md` for the current dependency audit and security
  posture snapshot.
- `CHANGELOG.md` for the 0.4.4 Soul Governance Library verification summary.
- `docs/soul-templates.md` for the built-in template library and structured
  governance fields.
- The War Room Soul viewer for template browsing, editable governance controls,
  behavior evaluation, and behavior-contract runs.
- The Command Center recommendations panel for low-interruption procedural
  suggestions that can be accepted, dismissed, snoozed, or converted into
  governed ActionCards.
- `docs/context-continuity.md` and `docs/memory.md` for structured memory,
  session briefs, compaction, retrieval, and context-efficiency telemetry.
- `docs/release-verification.md` for the fresh-clone and prebuilt-image smoke
  gates that must pass before treating a tag as install-ready.
- `tests/test_procedural_recommendations.py` and
  `tests/test_procedural_recommendations_api.py` for recommendation scoring,
  persistence, snooze behavior, sensitivity, operator scoping, and ActionCard
  conversion.
- `tests/test_memory_receipts.py`, `tests/test_memory_commits.py`, and
  `tests/test_context_compiler.py` for governed memory proof paths.
- `tests/test_flags_api_dependencies.py` and
  `tests/test_subsystem_runtime_contract.py` for hot-toggle and route-gating
  behavior.
- `tests/test_soul_template_library.py`, `tests/test_soul_behavior.py`, and
  `tests/test_soul_api.py` for Soul template loading, evaluator decisions,
  behavior contracts, and admin authorization.
- `tests/test_connector_proxy_governance.py` for connector policy enforcement,
  denial receipts, trust failure recording, and durable daily send caps.
- `packages/uab/tests/*.test.mjs` for UAB route fallback, permission-risk
  taxonomy, and bridge behavior.

### Known Limits

- The default install emphasizes governance, receipts, health checks,
  Soul template governance, procedural recommendations, structured memory, and
  the core tool bridge.
- HIVE, Federation, MCP governance, A2A, Time Travel, Observability, and parts
  of UAB are implemented behind runtime kill switches and should be evaluated
  as separate subsystem paths.
- Procedural recommendations are advisory. They surface bounded next steps for
  the operator and do not execute work without the existing governed action
  path.
- UAB depends on supported desktop frameworks and host setup. It is not universal
  automation for every application.
- Receipt storage is local SQLite with a signed integrity chain. It is not a
  multi-tenant audit warehouse.
- Source is available under BSL 1.1. Non-production use is free; production use
  requires a commercial license.
