# Release Notes

## Current Public Release: 0.3.1

Lancelot 0.3.1 is the first public-facing release line focused on making the
governance contract inspectable from source. The core path is self-hosted:
requests enter through the gateway, policy and Soul constraints are checked
before execution, approved work runs through governed tools or UAB routes, and
outcomes are written as immutable receipts.

### What To Inspect First

- `README.md` for the quickstart and architecture summary.
- `tests/test_receipts.py` for immutable receipt and tamper-detection behavior.
- `tests/hive/test_runtime.py` and `tests/hive/test_integration.py` for scoped
  HIVE execution and governance denial before mutation.
- `tests/test_kill_switch_contract.py` and `tests/federation/test_kill_switch.py`
  for fail-closed kill-switch behavior.
- `packages/uab/tests/router-methods.test.mjs` and
  `packages/uab/tests/permissions-risk.test.mjs` for UAB route fallback and
  risk classification.

### Known Limits

- The default install is intentionally narrow: health checks, chat ingress,
  governance configuration, tool routing, and receipts.
- HIVE, Federation, MCP governance, A2A, Time Travel, Observability, and many
  UAB desktop-control paths are feature-gated and off by default.
- UAB depends on supported desktop frameworks and host setup. It is not universal
  automation for every application.
- Receipt storage is local SQLite with a signed integrity chain. It is not a
  multi-tenant audit warehouse.
- Source is available under BSL 1.1. Non-production use is free; production use
  requires a commercial license.
