# Governance Spine Coverage Gate

This gate applies to the Governance Spine Hardening epic and both current LDDs:

- LDD-001: UAB Unified Authority Boundary
- LDD-002: Core Runtime and Receipt Spine Hardening
- LDD-003: Governance Spine Closeout Quality Gates

The gate measures current coverage in Phase 0, prevents silent regression during
implementation, and sets an epic completion bar. The 90 percent target is a
readiness gate for the completed epic, not a blocker for the first hardening
ticket.

## Phase 0 Baseline Commands

Python:

```powershell
python -m pytest tests --cov=src --cov-branch --cov-report=term-missing --cov-report="xml:artifacts\governance-spine-hardening\phase0\python-coverage.xml" --cov-report="json:artifacts\governance-spine-hardening\phase0\python-coverage.json" --cov-fail-under=0
```

UAB:

```powershell
npm --prefix packages/uab run build
node --experimental-test-coverage --test packages/uab/tests/*.test.mjs packages/uab/tests/daemon.test.js
```

## Recorded Baseline

| Area | Line coverage | Branch coverage | Test result | Evidence |
|---|---:|---:|---|---|
| Python `src` | `86.90481502967558%` | `77.26873107355671%` | `18 failed, 7948 passed` | `artifacts/governance-spine-hardening/phase0/python-coverage.json` |
| UAB dist | `32.71%` | `64.77%` | `2 failed, 43 passed` | `artifacts/governance-spine-hardening/phase0/uab-coverage-baseline.log` |

## Gates

1. Every hardening ticket must preserve or improve coverage on touched critical
   paths unless the ticket records a specific exception.
2. Overall Python and UAB coverage must be measured after meaningful ticket
   groups and before epic completion.
3. Epic completion requires 90 percent overall line coverage and 90 percent
   branch coverage for the relevant Python and UAB scopes unless an explicitly
   non-critical legacy exception is accepted.
4. Governance-critical touched paths target 95 percent coverage where practical.
5. Import-boundary rules must have 100 percent rule coverage: every declared
   boundary rule needs an executable check.
6. Coverage evidence must include branch coverage for approval, denial,
   fail-closed, receipt finalization, and risk translation paths.
7. Closeout coverage must be scoped to meaningful critical modules when broad
   package coverage would include unrelated legacy surface area. The command,
   scoped modules, line/branch result, and limitation must be recorded in the
   evidence manifest.

## Critical Paths

The higher proof bar applies to:

- UAB grant issuing, signing, validation, expiry, target matching, and replay
  protection.
- UAB `PermissionManager` enforcement behavior.
- Python UAB provider mutating and sensitive-read gates.
- HIVE-to-UAB governance bridge paths.
- Receipt metadata preparation and canonical receipt emission.
- Receipt integrity and finalization.
- Risk terminology translation.
- Gateway, receipt, memory, orchestrator, and UAB adapter import-boundary checks.

## Non-Fluff Rule

Coverage does not count as hardening proof when tests only import modules,
instantiate models, assert non-null results, snapshot implementation details, or
mock away the security decision. A counted test should prove an approval, denial,
receipt, boundary, drift, fail-closed, or behavior-preservation outcome.

## Closeout Evidence

LDD-003 closeout uses focused branch coverage over the UAB receipt path, receipt
service/integrity/migration path, Tool Fabric UAB provider path, and UAB runtime
adapter. These reports are local artifacts because they include generated
coverage JSON:

```text
artifacts/governance-spine-hardening-closeout/close-002/uab-receipts-coverage.json
artifacts/governance-spine-hardening-closeout/close-003/receipt-service-coverage.json
artifacts/governance-spine-hardening-closeout/close-004/tool-fabric-uab-coverage.json
artifacts/governance-spine-hardening-closeout/close-006/branch-coverage.json
```

The closeout disposition table records which enterprise claims these reports
support and what limitations remain.
