# Enterprise Repo SOP for Lancelot

This reference expands the working SOP for changes in the Lancelot repository. Use it when the task touches runtime behavior, governance, security, production readiness, release processes, or cross-module contracts.

## 1. Intake and Scope

Before editing:

- Identify the affected surface: core gateway, orchestrator, governance, memory, skills, connectors, MCP, scheduler, HIVE, federation, A2A, UAB, War Room, compliance, installer, docs, or tests.
- Identify user-visible, operator-visible, and audit-visible behavior.
- Read the nearest implementation, tests, docs, and config examples.
- Confirm whether the task changes a public contract, runtime default, API schema, receipt shape, config key, feature flag, database layout, installer flow, or UI workflow.
- Check for existing dirty worktree changes and avoid overwriting unrelated edits.

Escalate design scrutiny when the change:

- Adds or broadens autonomous action.
- Adds external network, filesystem, host, desktop, connector, A2A, federation, or MCP behavior.
- Changes risk classification, policy decisions, approval flow, receipt emission, auth, vault behavior, memory persistence, or rollback.
- Changes production setup, release gates, or deployment assumptions.

## 2. Architectural Standard

Lancelot is a governed autonomous system. Changes must preserve these architecture properties:

- Governance is enforced outside the model.
- Actions are risk-tiered and policy-checked before execution.
- Receipts are the audit ground truth.
- Subsystems are independently disableable through feature flags.
- Runtime status surfaces must distinguish healthy, disabled, unavailable, and degraded states.
- Connectors and external peers are untrusted unless explicitly governed.
- Human operator controls, including pause, emergency stop, approvals, and kill switches, must remain effective.
- Memory writes are reversible, attributable, and safe to roll back.

Do not introduce hidden side effects, implicit external calls, silent fallbacks, or "best effort" security behavior that masks failure.

## 3. Security and Compliance Standard

For every change, check whether it affects:

- Authentication and session boundaries.
- Authorization and capability checks.
- Secrets, vault keys, OAuth tokens, or credential recovery.
- Network allowlists and outbound policy.
- Sandbox and host execution boundaries.
- Receipt integrity, redaction, and operator attribution.
- Compliance exports, signed manifests, framework mappings, or audit bundles.
- Data retention, persistence, rollback, and backup expectations.

Required practices:

- Fail closed for auth, vault, policy, local-only URL, redaction, and capability-boundary failures.
- Keep credentials out of code, logs, docs, receipts, memory, and generated artifacts.
- Treat model output and tool output as untrusted input.
- Validate inbound payloads with Pydantic or established schemas.
- Add tests for malformed input, missing auth, disabled features, and degraded dependencies when those paths are touched.

## 4. Development Workflow

1. Reproduce or understand the current behavior with focused reads and tests.
2. Add or update a failing test first when fixing a bug or locking a contract.
3. Implement the smallest coherent change using existing abstractions.
4. Update docs, runbooks, config examples, and changelog entries when the change affects operators or users.
5. Run focused tests, then broader gates as risk increases.
6. Review the diff for unrelated churn, accidental generated files, secrets, noisy formatting, and stale assumptions.

Avoid broad rewrites unless the request is explicitly a refactor and the tests cover the blast radius.

## 5. Testing Matrix

Choose tests by affected surface:

| Surface | Minimum verification |
| --- | --- |
| Core Python behavior | Focused `pytest tests/<file>.py -x -v`, then `pytest tests/ -x` when shared paths are affected |
| Governance, receipts, risk, memory, auth | Focused tests plus negative/security cases |
| API contracts | API tests plus schema/payload edge cases |
| War Room UI | `npm run type-check` and `npm run build` from `src/warroom`; use browser verification for visual/interaction changes |
| UAB package | `npm test` from `packages/uab` |
| Docker/runtime setup | `docker compose up -d --build`, health checks, and containerized pytest |
| Production hardening | Relevant health, auth, vault, pause, emergency stop, allowlist, compliance, or subsystem status checks |
| Docs-only change | Link/path sanity and command accuracy review |

Default pytest excludes `integration`, `slow`, `docker`, and `local_model`. Run those lanes only when the task depends on them or the change affects that boundary.

## 6. Production Readiness Gate

A production-impacting change is not done until it has evidence for:

- Governance: policy/risk behavior is tested and documented.
- Receipts: success, failure, skip, and degraded paths emit or preserve audit records where applicable.
- Kill switch: the subsystem can be disabled without breaking unrelated runtime paths.
- Status: War Room/API surfaces report truthful health and degradation.
- Auth: management surfaces remain protected.
- Persistence: restart behavior is understood and tested when state changes.
- Rollback: operator can recover or disable the change.
- Compliance: audit exports or evidence chains are not weakened.

For deployment or release readiness, also verify the production-hardening expectations in the repo docs: intentional auth mode, stable vault key, pause/resume, emergency stop, local-only host execution policy, network egress review, and staged rollout.

## 7. PR and Release Checklist

Before presenting a change as ready:

- Branch, PR, merge, and publishing steps follow `development-publishing-flow.md`.
- Tests cover the main path and important failure paths.
- Public release PRs preserve at least 90.00% Python line coverage before merge unless the owner explicitly approves a documented exception.
- User-facing or operator-facing changes are documented.
- `CHANGELOG.md` is updated for behavior changes.
- Config examples are updated for new or changed settings.
- New feature flags default to the conservative behavior.
- New connectors declare capabilities, target domains, vault keys, and risk honestly.
- New skills follow the governed skill pipeline and do not bypass sandbox enforcement.
- No secrets, local logs, coverage dumps, build outputs, or unrelated generated artifacts are included.
- The final response lists exact verification performed and any gates not run.

## 8. Common Commands

Use PowerShell syntax in this workspace unless the user asks otherwise.

```powershell
git status --short
rg -n "pattern" src tests docs
pytest tests/<path> -x -v
pytest tests/ -x
pytest tests/ --cov=src --cov-report=term-missing
docker compose up -d --build
docker exec lancelot_core pytest tests/ -x
```

War Room:

```powershell
Set-Location src\warroom
npm run type-check
npm run build
```

UAB:

```powershell
Set-Location packages\uab
npm test
```

Health checks after deployment:

```powershell
curl.exe -s http://localhost:8000/health
curl.exe -s http://localhost:8000/health/ready
curl.exe -s -o NUL -w "%{http_code}" http://localhost:8000/war-room/
```

## 9. Closeout Template

Use a concise closeout:

- Summary: what changed and why.
- Files: key files touched.
- Verification: commands run and outcomes.
- Not run: any relevant gates skipped and why.
- Risk: residual production, security, migration, or rollout concerns.
