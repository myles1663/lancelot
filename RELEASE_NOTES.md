# Release Notes

## Current Public Release: 0.4.6

Lancelot 0.4.6 expands the War Room into a more complete operator console. It
adds broad UX polish across governance, operations, federation, receipts, cost,
connectors, kill switches, and time-travel views; preserves governed installed
skill inspection with source proposal and artifact evidence; adds Soul version
rollback and template provenance; introduces Microsoft Graph workspace
connectors; and adds DeepSeek plus operator-managed OpenAI-compatible local
model provider configuration. Provider and connector changes now emit durable
governance receipts, and provider configuration persistence fails closed when
durable writes fail. The release also includes a standalone visual War Room
walkthrough built from fresh screenshots.

### What To Inspect First

- `README.md` for the quickstart and architecture summary.
- `docs/security-overview.md` for the current dependency audit and security
  posture snapshot.
- `CHANGELOG.md` for the 0.4.6 War Room readiness verification summary.
- `docs/soul-templates.md` for the built-in template library and structured
  governance fields.
- The War Room Skills panel for installed-skill inspection, source proposal
  review, runtime contract review, and governed enable/disable controls.
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
- `tests/test_skills_api.py`, `tests/test_skill_factory.py`,
  `tests/test_skill_registry.py`, and `tests/test_skill_executor.py` for skill
  inspection, installation, registry, execution, and governed toggle paths.
- `tests/test_connector_proxy_governance.py` for connector policy enforcement,
  denial receipts, trust failure recording, and durable daily send caps.
- `packages/uab/tests/*.test.mjs` for UAB route fallback, permission-risk
  taxonomy, and bridge behavior.

### Known Limits

- The default install emphasizes governance, receipts, health checks,
  skill inspection, Soul template governance, procedural recommendations,
  structured memory, and the core tool bridge.
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
