# Lancelot

Lancelot is a governance-native runtime for AI agents.

Most agent frameworks start with capability: tool calls, sub-agents, memory, app control, model routing, and interoperability. Governance is usually added later around the edges as prompts, policies, approvals, logs, or monitoring.

Lancelot starts with governance. Policy, scoped authority, approval gates, kill switches, operator visibility, and receipt-chain auditability are part of the execution path itself. Standard agent capabilities are routed through that path instead of bypassing it.

The model can propose actions. The runtime decides what is allowed, what requires approval, what gets blocked, and what must be recorded.

The goal is not less capability for more safety. The goal is capable by design, governed by default.

Lancelot can call tools, control desktop applications through a governed bridge, spawn scoped sub-agents, maintain structured memory across long-running work, route model calls, expose operator controls, and write immutable receipts for governed actions. These are not escape hatches around governance. They are governed execution surfaces.

<p align="center">
  <img src="docs/images/war-room-command-center.png" alt="Lancelot War Room command center" width="900">
</p>

## Quickstart

1. Install Docker Desktop and Node.js 18+.
2. Run `npx create-lancelot`.
3. The installer will collect one provider credential, pull the prebuilt core and local-model images, and start the stack.
4. Open http://localhost:8000.
5. Verify liveness: `curl http://localhost:8000/health/live`
6. Send a smoke test: `curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" -d '{"text":"hello"}'`

Manual setup:

```bash
git clone <repo-url>
cd lancelot
# Optional: create .env only when you need deployment overrides.
# cp .env.example .env
docker compose pull lancelot-core
docker compose pull local-llm
docker compose up -d
```

For installation details and provider-specific setup, see [docs/installation.md](docs/installation.md).

Lancelot is built for developers, operators, and technical teams who need governed automation rather than a lightweight agent demo or consumer chatbot.

## Walkthroughs

If you want to evaluate the system before reading every subsystem guide, start here:

- [Guided Walkthrough](docs/guided-walkthrough.md): screenshot flow showing a governed workspace file action moving from command, to approval, to execution, to receipt detail.
- [Proof Walkthrough](docs/proof-walkthrough.md): command-by-command verification path for health checks, Docker runtime behavior, receipt-chain validation, and governance proof points.

## Why Not Just Use An Existing Agent Framework?

Existing agent frameworks are useful. They proved that teams want agents that can plan, use tools, delegate work, maintain memory, control apps, and interoperate with other systems.

Lancelot is aimed at a different failure mode: capability and authority are often too tightly coupled. The same model that reasons about a task can also trigger actions that change files, call tools, control apps, or spend resources.

Lancelot separates proposal from authority. The model proposes. The runtime governs. Actions pass through policy, scoped permissions, approval rules, kill switches, and receipt logging before execution.

Governance-only wrappers fail if teams have to give up the capabilities they already need. Capability-first frameworks create risk when powerful actions run through soft boundaries. Lancelot is designed so teams do not have to choose between capable agents and governable agents.

## Verify the Governance Loop

You can inspect the core guarantees without trusting a demo transcript. From a clone with the Python and frontend dependencies installed, these checks do not require a live frontier model account:

```bash
# Receipt integrity, immutability, and tamper detection.
python -m pytest -q tests/test_receipts.py

# Scoped HIVE task execution and boundary enforcement.
python -m pytest -q tests/hive/test_runtime.py

# Kill switch propagation and fail-closed behavior.
python -m pytest -q tests/test_kill_switch_contract.py

# War Room frontend typecheck and production build.
(cd src/warroom && npm ci && npm run type-check && npm run build)
```

For release-candidate checks, dependency lockfiles, and Docker image pinning guidance, see [Release Verification](docs/release-verification.md).

The latest release-readiness pass recorded `7,216 passed`, `24 skipped`, `31 deselected`, and `90.5085%` Python line coverage with:

```bash
python -m pytest -q --cov=src --cov-report=term-missing --cov-report=json:coverage-full.json
```

## What Lancelot Is Not

- Not a consumer chatbot or companion app.
- Not a generic agent SDK where governance is optional.
- Not unrestricted computer control. Desktop and tool actions are intended to be scoped, classified, approved when needed, and receipt-traced.
- Not a managed SaaS. The default posture is local-first and self-hosted.

## Current Maturity

| Area | Status |
| --- | --- |
| Governance checks, approvals, kill switches, and receipts | Core path to inspect first |
| Structured memory, context compilation, quarantine, and memory receipts | Core path to inspect first |
| Health/readiness, async chat runs, and War Room build | Covered by public proof tests and CI |
| UAB routing, fallback behavior, and action-risk taxonomy | Implemented with focused test coverage |
| HIVE scoped sub-agents | Implemented and feature-gated |
| Federation, A2A, MCP governance, Time Travel, Observability | Implemented as runtime-toggleable governed capabilities |
| Broad desktop automation coverage | Strongest on supported host setups and frameworks; not universal app control |

## Evaluation FAQ

### Is Lancelot open source?

No. Lancelot is source-available under BSL 1.1. You can use, copy, modify, and redistribute it for non-production evaluation, development, and testing. Production use requires a commercial license. See [LICENSE](LICENSE) for the exact terms.

### What should I evaluate first?

Start with governance, receipts, health/readiness, the core tool bridge, and the War Room. Those are the default paths the README proof commands are intended to exercise.

### How do optional subsystems work?

The default install exercises Lancelot's governed operator path: governance, structured memory, receipts, health/readiness, the core tool bridge, and the War Room. HIVE, Federation, A2A, MCP governance, Time Travel, Observability, and parts of UAB are runtime-toggleable so operators can match Lancelot to their deployment. They are not separate products; they are optional governed capabilities behind kill switches.

### Is UAB universal desktop automation?

No. UAB is strongest on supported host setups and desktop frameworks. It is a governed route for application control, not permissionless "let the model click anything" automation.

### Was this built with AI assistance?

Yes. Lancelot was built through AI-assisted development. The project should be evaluated by its contracts, tests, failure modes, and reviewable boundaries rather than by trusting the generation process.

### Should I expose this directly to the internet?

No. The recommended posture is local-first and self-hosted. For production-style use, put it behind private network or VPN access and follow the [Production Hardening Guide](docs/production-hardening.md).

## Core Components

### Governance

The governance layer evaluates every agent action against policy before execution. It combines Soul-defined constraints, risk classification, approval requirements, and kill switches so the model cannot bypass the system with prompt text alone.

### UAB

The Universal Application Bridge gives Lancelot a governed path to desktop application control. It routes operations through framework-aware hooks and fallbacks so app automation stays explicit, inspectable, and policy-bound instead of being treated like a generic tool call.

### HIVE

HIVE handles task decomposition into bounded sub-agents. Each spawned task gets explicit scope, app/category limits, and runtime enforcement so delegation remains constrained instead of turning into open-ended autonomous execution.

### Receipts

The receipt system is the audit trail. Actions are staged, finalized into an immutable log, and linked into an integrity chain so operators can reconstruct what happened and verify that records were not silently rewritten.

## Governed Action Flow

1. A model or operator request proposes an action.
2. Lancelot classifies the action risk and checks the active Soul policy.
3. Kill switches, approval rules, and scoped runtime boundaries decide whether execution can continue.
4. The approved action runs through a governed tool provider or UAB route.
5. The outcome is written as a finalized receipt linked into the integrity chain.

## War Room

The War Room is the local operator console for governed execution, receipt review, kill-switch control, and runtime health.

<table>
  <tr>
    <td width="50%">
      <img src="docs/images/war-room-receipt-explorer.png" alt="Receipt Explorer showing governed action receipts">
    </td>
    <td width="50%">
      <img src="docs/images/war-room-kill-switches.png" alt="Kill Switches panel showing runtime-toggleable governed capabilities">
    </td>
  </tr>
  <tr>
    <td align="center"><sub>Receipt Explorer</sub></td>
    <td align="center"><sub>Runtime Kill Switches</sub></td>
  </tr>
  <tr>
    <td colspan="2">
      <img src="docs/images/war-room-health-dashboard.png" alt="Health Dashboard showing runtime component readiness">
    </td>
  </tr>
  <tr>
    <td colspan="2" align="center"><sub>Runtime Health</sub></td>
  </tr>
</table>

## Architecture

<p align="center">
  <img src="docs/images/fig1_system_architecture.svg" alt="Lancelot system architecture" width="900">
</p>

The diagram shows the runtime boundary clearly: operator channels enter through the governed core, execution flows through policy and capability checks, memory/context is durable and auditable, and runtime-toggleable subsystems can be enabled or disabled without collapsing the stack.

## Proof Points

Key guarantees are backed by contract tests you can run directly:

- Guided approval flow: [docs/guided-walkthrough.md](docs/guided-walkthrough.md)
- Live runtime proof path: [docs/proof-walkthrough.md](docs/proof-walkthrough.md)
- Release verification: `python scripts/verify-public-release.py`
- Full Python suite coverage baseline: `90.5085%` line coverage in the latest release-readiness pass
- Receipt immutability and integrity-chain validation: [tests/test_receipts.py](tests/test_receipts.py)
- HIVE scoped execution and boundary enforcement: [tests/hive/test_runtime.py](tests/hive/test_runtime.py)
- Kill switch propagation and fail-closed behavior: [tests/test_kill_switch_contract.py](tests/test_kill_switch_contract.py)
- UAB route selection and fallback behavior: [packages/uab/tests/router-methods.test.mjs](packages/uab/tests/router-methods.test.mjs), [packages/uab/tests/connector-fallbacks.test.mjs](packages/uab/tests/connector-fallbacks.test.mjs)
- UAB action-risk taxonomy: [packages/uab/tests/permissions-risk.test.mjs](packages/uab/tests/permissions-risk.test.mjs), [tests/test_uab_bridge_provider.py](tests/test_uab_bridge_provider.py)
- UAB readiness polling: [packages/uab/tests/server-readiness.test.mjs](packages/uab/tests/server-readiness.test.mjs)

```bash
# Python governance contract tests
pytest \
  tests/test_receipts.py \
  tests/hive/test_runtime.py \
  tests/test_kill_switch_contract.py \
  tests/test_feature_f1_f8.py::TestHealthCheckEnhanced \
  tests/test_feature_f1_f8.py::TestReadinessEndpoint \
  tests/test_chat_runs.py::test_local_model_health_summary_accepts_ready_role_lane \
  tests/test_chat_runs.py::test_fast_runtime_status_command_formats_health_snapshot \
  tests/test_chat_runs.py::test_chat_run_status_classifier_identifies_operator_blocking \
  tests/test_chat_runs.py::test_execute_async_chat_run_marks_completion_and_emits_events \
  tests/test_chat_runs.py::test_execute_async_chat_run_does_not_overwrite_operator_cancel \
  tests/test_chat_runs.py::test_execute_async_chat_run_keeps_fast_runtime_commands_outside_worker_slot \
  tests/test_chat_runs.py::test_chat_progress_event_updates_async_run \
  tests/test_chat_runs.py::test_chat_progress_event_preserves_degraded_disclosure \
  tests/test_chat_runs.py::test_chat_run_payload_includes_receipt_proof_from_retry_lineage \
  tests/test_chat_runs.py::test_chat_run_payload_omits_receipt_proof_for_active_runs \
  tests/test_chat_runs.py::test_chat_async_endpoint_queues_run_without_waiting_for_result \
  tests/test_chat_runs.py::test_chat_run_cancel_endpoint_marks_run_cancelled \
  tests/test_chat_runs.py::test_chat_run_retry_endpoint_queues_new_run \
  tests/test_chat_runs.py::test_chat_run_retry_endpoint_queues_blocked_run \
  tests/test_control_plane.py \
  tests/test_control_plane_auth_hardening.py \
  tests/test_token_url_hardening.py::test_live_websocket_rejects_query_param_tokens \
  tests/test_orchestrator_approval.py \
  tests/test_orchestrator_context.py \
  tests/test_orchestrator_frontier.py \
  tests/test_orchestrator_generation.py \
  tests/test_orchestrator_governance.py \
  tests/test_orchestrator_identity.py \
  tests/test_orchestrator_provider.py \
  tests/test_orchestrator_routing.py \
  tests/test_orchestrator_response_delivery.py \
  tests/test_tool_loop_approval.py \
  tests/test_tool_loop_receipts.py \
  tests/test_tool_loop_frontier.py \
  tests/test_tool_loop_governance.py \
  tests/test_tool_loop_completion.py \
  tests/test_tool_loop_results.py \
  tests/test_tool_loop_structured.py \
  tests/test_tool_loop_local.py \
  tests/test_composition_coverage.py::test_agentic_generate_blocks_escalated_tool_without_write_permission \
  tests/test_composition_coverage.py::test_agentic_generate_groups_multiple_escalated_tool_calls \
  tests/test_composition_coverage.py::test_agentic_generate_rejects_missing_tool_inputs_before_approval

# UAB build and test suite
(cd packages/uab && npm ci && npm test)

# War Room frontend typecheck and production build
(cd src/warroom && npm ci && npm run type-check && npm run build)

# Docker Compose configuration validation
if [ ! -f .env ]; then cp .env.example .env && cleanup_env=1; fi
docker compose config --quiet
if [ "${cleanup_env:-0}" = "1" ]; then rm .env; fi
```

## Development Note

Lancelot was built through AI-assisted development. The engineering bar for the repo is not whether generated code is detectable; it is whether the system has explicit contracts, failure-mode tests, and reviewable boundaries. The files above are the best starting point for evaluating that claim.

## Known Limitations

- Several major subsystems, including HIVE, Federation, MCP Governance, A2A, Time Travel, Observability, and parts of UAB, are runtime-toggleable governed capabilities. The default path to inspect first is governance, receipts, health checks, structured memory, and the core tool bridge.
- UAB is strongest on supported desktop frameworks and host setups. It is not universal automation for every application.
- The system is local-first and self-hosted, not a managed SaaS.
- The configuration surface is large. Operators should treat `config/*.yaml` and `.env` as deployment artifacts, not casual defaults.
- Some modules still need refactoring and logging cleanup to match the maturity of the core governance and receipt systems.

## Deep Dives

For the full documentation index, architecture notes, subsystem guides, and operational references, see [docs/INDEX.md](docs/INDEX.md).

Start with the subsystem guides for implementation detail:

- [War Room](docs/war-room.md)
- [Governance](docs/governance.md)
- [Receipts](docs/receipts.md)
- [Memory](docs/memory.md)
- [UAB](docs/uab.md)
- [HIVE](docs/hive.md)
- [Federation](docs/federation.md)
- [A2A](docs/a2a.md)
- [MCP Governance](docs/mcp.md)
- [Kill Switches](docs/kill-switches.md)

Architecture decision records are intentionally short design notes:

- [Local-first, self-hosted control plane](docs/adr/0001-local-first-self-hosted.md)
- [SQLite-backed receipts](docs/adr/0002-sqlite-receipts.md)
- [Runtime-toggleable governed subsystems](docs/adr/0003-runtime-toggleable-governed-subsystems.md)

## License

Business Source License 1.1 (BSL 1.1, source-available). Non-production use is free. Production use requires a commercial license; see [LICENSE](LICENSE) for the exact terms.
