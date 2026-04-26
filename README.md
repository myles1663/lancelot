# Lancelot

Lancelot is a self-hosted AI operator for technical users who want model-driven automation with hard policy boundaries. It can call tools, control desktop applications through a governed bridge, keep structured memory, and write immutable receipts for every action. The design goal is simple: treat the model as untrusted planning logic inside a system that can say no, require approval, and leave an audit trail.

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

## Architecture

<p align="center">
  <img src="docs/images/fig1_system_architecture.svg" alt="Lancelot system architecture" width="900">
</p>

The diagram shows the system boundary clearly: governance sits in front of execution, receipts capture outcomes, and feature-gated subsystems can be disabled without collapsing the entire stack.

## Proof Points

Key guarantees are backed by contract tests you can run directly:

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
  tests/test_gateway_control_routes.py \
  tests/test_gateway_live_routes.py \
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

- Several major subsystems, including HIVE, Federation, MCP Governance, A2A, Time Travel, Observability, and parts of UAB, are feature-gated and disabled by default. The default path to inspect first is governance, receipts, health checks, and the core tool bridge.
- UAB is strongest on supported desktop frameworks and host setups. It is not universal automation for every application.
- The system is local-first and self-hosted, not a managed SaaS.
- The configuration surface is large. Operators should treat `config/*.yaml` and `.env` as deployment artifacts, not casual defaults.
- Some modules still need refactoring and logging cleanup to match the maturity of the core governance and receipt systems.

## Deep Dives

For the full documentation index, architecture notes, subsystem guides, and operational references, see [docs/INDEX.md](docs/INDEX.md).

Short architecture decision records:

- [Local-first, self-hosted control plane](docs/adr/0001-local-first-self-hosted.md)
- [SQLite-backed receipts](docs/adr/0002-sqlite-receipts.md)
- [Feature-gated subsystems](docs/adr/0003-feature-gated-subsystems.md)

## License

Business Source License 1.1 (BSL 1.1, source-available). Non-production use is free. Production use requires a commercial license; see [LICENSE](LICENSE) for the exact terms.
