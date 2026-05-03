# Release Notes

## Current Public Release: 0.4.1

Lancelot 0.4.1 is the release-readiness line focused on governed memory,
context continuity, runtime hot toggles, and public verification. The core path
is self-hosted: requests enter through the gateway, policy and Soul constraints
are checked before execution, approved work runs through governed tools or UAB
routes, and outcomes are written as immutable receipts.

### What To Inspect First

- `README.md` for the quickstart and architecture summary.
- `docs/context-continuity.md` and `docs/memory.md` for structured memory,
  session briefs, compaction, retrieval, and context-efficiency telemetry.
- `tests/test_memory_receipts.py`, `tests/test_memory_commits.py`, and
  `tests/test_context_compiler.py` for governed memory proof paths.
- `tests/test_flags_api_dependencies.py` and
  `tests/test_subsystem_runtime_contract.py` for hot-toggle and route-gating
  behavior.
- `packages/uab/tests/*.test.mjs` for UAB route fallback, permission-risk
  taxonomy, and bridge behavior.

### Known Limits

- The default install emphasizes governance, receipts, health checks,
  structured memory, and the core tool bridge.
- HIVE, Federation, MCP governance, A2A, Time Travel, Observability, and parts
  of UAB are implemented behind runtime kill switches and should be evaluated
  as separate subsystem paths.
- UAB depends on supported desktop frameworks and host setup. It is not universal
  automation for every application.
- Receipt storage is local SQLite with a signed integrity chain. It is not a
  multi-tenant audit warehouse.
- Source is available under BSL 1.1. Non-production use is free; production use
  requires a commercial license.
