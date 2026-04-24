# Lancelot

Lancelot is a self-hosted AI operator for technical users who want model-driven automation with hard policy boundaries. It can call tools, control desktop applications through a governed bridge, keep structured memory, and write immutable receipts for every action. The design goal is simple: treat the model as untrusted planning logic inside a system that can say no, require approval, and leave an audit trail.

## Quickstart

1. Install Docker Desktop and Node.js 18+.
2. Run `npx create-lancelot@latest`.
3. The installer will collect one provider credential, pull the prebuilt core and local-model images, and start the stack.
4. Open http://localhost:8000.
5. Verify readiness: `curl http://localhost:8000/health/ready`
6. In the War Room, send `hello`.
7. For direct API smoke tests, run this from the installed repo directory and use the generated `LANCELOT_API_TOKEN` from `.env`:

```bash
TOKEN="$(grep '^LANCELOT_API_TOKEN=' .env | cut -d= -f2-)"
curl -X POST http://localhost:8000/chat \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"text":"hello"}'
```

Manual setup:

```bash
git clone https://github.com/myles1663/lancelot.git
cd lancelot
cp .env.example .env
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
pytest tests/test_receipts.py tests/hive/test_runtime.py tests/test_kill_switch_contract.py
cd packages/uab
npm run build
node --test tests/router-methods.test.mjs tests/connector-fallbacks.test.mjs tests/permissions-risk.test.mjs
node --test tests/server-readiness.test.mjs
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
