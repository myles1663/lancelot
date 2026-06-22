# Release Notes

## Current Public Release: 0.4.8

Lancelot 0.4.8 is scoped to the governed runtime spine, UAB hardening, and
proof-of-governed-execution evidence. It does not include other in-flight
private development projects.

The spine hardening work separates boot, gateway, orchestration, memory, and
receipt responsibilities into smaller runtime modules with boundary tests and
public architecture notes. The UAB work keeps UAB usable as a standalone package
while aligning the embedded Lancelot integration around canonical authority
grants, permission-risk terminology, receipt metadata, and bridge fallback
coverage.

The proof package adds a deterministic governed-execution runner, scenario
documentation, a control-to-evidence matrix, focused tests, generated receipt
evidence, and packet validation so reviewers can inspect governed denial,
approval, receipt, and evidence-manifest behavior without relying on prose-only
claims.

### Verification

- Full Python suite: 7,537 passed, 24 skipped, 31 deselected.
- Full Python line coverage: 90%.
- Public artifact guard: passed.
- Governed-execution proof runner: 13 cases passed.
- Proof packet zip integrity validation: passed.
- Universal Application Bridge clean install, audit, build, and tests: passed.
- War Room clean install, audit, type-check, and production build: passed.
- Installer clean install, audit, and tests: passed.
- Docker Compose config validation: passed.

## Previous Public Release: 0.4.7

Lancelot 0.4.7 adds a disabled-by-default governed UCP commerce connector
foundation on top of the 0.4.6 War Room readiness release. UCP is documented as
a high-authority commerce execution rail: quote and discovery operations can be
modeled as governed commerce intents, while spend-committing actions default to
T3 and require approval evidence through Lancelot's existing governance queue.

Lancelot 0.4.6 expanded the War Room into a more complete operator console. It
added broad UX polish across governance, operations, federation, receipts, cost,
connectors, kill switches, and time-travel views; preserved governed installed
skill inspection with source proposal and artifact evidence; added Soul version
rollback and template provenance; introduced Microsoft Graph workspace
connectors; and added DeepSeek plus operator-managed OpenAI-compatible local
model provider configuration. Provider and connector changes now emit durable
governance receipts, and provider configuration persistence fails closed when
durable writes fail. The release also includes a standalone visual War Room
walkthrough built from fresh screenshots.

### What To Inspect First

- `README.md` for the quickstart and architecture summary.
- `docs/security-overview.md` for the current dependency audit and security
  posture snapshot.
- `CHANGELOG.md` for the 0.4.8 governance spine, UAB, proof, and dependency
  audit verification summary.
- `docs/proof/README.md` for the proof-of-governed-execution package.
- `docs/engineering/runtime-spine-boundaries.md` and
  `docs/engineering/uab-governance-boundary.md` for the hardened runtime and
  UAB boundaries.
- `docs/security/governed-execution-threat-model.md` and
  `docs/security/uab-threat-model.md` for the release threat models.
- `docs/ucp-governed-commerce.md` for the governed commerce connector
  authority boundary, approval contract, and rollout plan.
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
