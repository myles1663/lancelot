# Proof Walkthrough

This walkthrough gives skeptical reviewers a short path from "interesting claim" to "I can inspect the controls myself." It focuses on the default paths that are most mature today: receipts, scoped execution, kill switches, health/readiness, UAB routing tests, and the War Room build.

## Fast Path

If you want the shortest copy-paste proof path from a normal clone, run:

```bash
python scripts/verify-public-release.py --skip-pytest --skip-uab --skip-docker
python -m pytest -q tests/test_receipts.py tests/hive/test_runtime.py tests/test_kill_switch_contract.py
npm --prefix src/warroom ci
npm --prefix src/warroom run type-check
npm --prefix src/warroom run build
npm --prefix packages/uab ci
npm --prefix packages/uab test
```

The sections below explain what each check proves and what it does not prove.

## 1. Verify The Checkout

From a normal development clone:

```bash
python scripts/verify-public-release.py --skip-pytest --skip-uab --skip-docker
```

Expected result:

```text
Public release verification completed successfully.
```

In a development checkout, this check verifies release-readiness invariants that should hold before public prep, including runtime data exclusions, Docker build-context exclusions, source-file size limits, and production Python `print()` statements.

From a prepared public release artifact, run the stricter public-artifact guard:

```bash
python scripts/verify-public-release.py --skip-pytest --skip-uab --skip-docker --public-artifact
```

That stricter check is intended for the public release tree after maintainer-only docs and release-prep files have been excluded.

## 2. Verify Receipt Integrity

Receipts are staged, finalized into SQLite, hash-linked, and HMAC-signed. The focused contract suite exercises immutability, chain construction, tamper detection, signature persistence, and scoped chain validation:

```bash
python -m pytest -q tests/test_receipts.py
```

Useful tests to inspect:

- `test_finalized_receipt_cannot_be_mutated`
- `test_finalized_receipt_gets_integrity_chain_fields`
- `test_validate_integrity_chain_detects_tampering`
- `test_validate_integrity_chain_detects_signature_tampering`
- `test_local_signing_key_persists_across_service_restart`

After a Docker-backed instance has created receipts, you can also validate the live receipt chain inside the core container:

```bash
docker compose exec lancelot-core python -c "from shared.receipts import get_receipt_service; issues = get_receipt_service().validate_integrity_chain(); print('receipt chain OK' if not issues else issues)"
```

Expected clean result:

```text
receipt chain OK
```

## 3. Inspect A Receipt

The War Room Receipt Explorer shows recent receipts, risk tiers, action type, status, duration, token counts, sanitized inputs/outputs, operator/session metadata, and quest lineage.

A simplified receipt has this shape:

```json
{
  "id": "4f50fc88-1b4e-40c3-bd6f-29c2cbc6034c",
  "timestamp": "2026-04-30T15:56:00Z",
  "action_type": "llm_call",
  "action_name": "chat_generation",
  "status": "success",
  "duration_ms": 18103,
  "token_count": 39,
  "tier": 1,
  "inputs": {
    "channel": "warroom",
    "model": "gpt-5.4-mini"
  },
  "outputs": {
    "response": "The active-work status is active in the execution phase."
  },
  "metadata": {
    "provider": "openai-codex",
    "quest_id": "043c801b-43e6-490e-8c35-bbae0025c178"
  },
  "integrity_prev_hash": "3ab...",
  "integrity_hash": "a18...",
  "integrity_key_id": "local-default",
  "integrity_signature": "c12..."
}
```

Actual runtime receipts include more fields and sanitized request/output data. Secrets and high-risk private data are not meant to be stored in plaintext receipt bodies.

## 4. Verify Scoped HIVE Execution

HIVE decomposes work into bounded sub-agents. The scoped execution tests prove spawned tasks cannot widen authority through mutated payloads or injected scope fields:

```bash
python -m pytest -q tests/hive/test_runtime.py
```

Useful tests to inspect:

- `test_runtime_overwrites_injected_scope_fields_before_executor`
- `test_runtime_uses_spawn_time_scope_boundary_not_later_raw_soul_mutation`
- `test_exact_category_matching_blocks_fuzzy_substring_capabilities`
- `test_scoped_soul_violation_before_governance_emits_action_receipt`

## 5. Verify Kill Switch Behavior

Kill switches are part of the fail-closed control path:

```bash
python -m pytest -q tests/test_kill_switch_contract.py
```

This contract verifies propagation and failure behavior around operator stop controls.

## 6. Verify War Room And UAB

The War Room frontend should typecheck and build:

```bash
npm --prefix src/warroom ci
npm --prefix src/warroom run type-check
npm --prefix src/warroom run build
```

The Universal Application Bridge test suite covers route selection, fallback behavior, readiness polling, and action-risk taxonomy:

```bash
npm --prefix packages/uab ci
npm --prefix packages/uab test
```

## 7. Verify Compose Configuration

The Docker Compose file should resolve without requiring a local `.env` file:

Bash:

```bash
if [ ! -f .env ]; then cp .env.example .env && cleanup_env=1; fi
docker compose config --quiet
compose_status=$?
if [ "${cleanup_env:-0}" = "1" ]; then rm .env; fi
exit "$compose_status"
```

PowerShell:

```powershell
$createdEnv = $false
if (-not (Test-Path .env)) {
  Copy-Item .env.example .env
  $createdEnv = $true
}
docker compose config --quiet
$composeStatus = $LASTEXITCODE
if ($createdEnv) {
  Remove-Item .env
}
exit $composeStatus
```

No output means Compose accepted the configuration.

## Optional Full Coverage Run

For a full release-readiness view, run:

```bash
python -m pytest -q --cov=src --cov-report=term-missing --cov-report=json:coverage-full.json
```

The latest release-readiness pass recorded `7,361 passed`, `24 skipped`, `31 deselected`, and `90.2644%` Python line coverage.

## What This Does Not Prove

This walkthrough does not prove that every feature-gated subsystem is production-ready. Federation, A2A, MCP governance, Time Travel, Observability, and broad desktop automation coverage should be evaluated separately. The default path to inspect first is governance, receipts, health/readiness, the core tool bridge, and the War Room.
