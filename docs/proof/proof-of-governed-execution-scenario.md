# Proof of Governed Execution Scenario

Status: fixed for `LDD-005 Proof of Governed Execution`.

This proof is a deterministic, evaluator-facing run of the governed UAB execution path:

```text
request -> classify -> deny or grant -> enforce -> receipt -> operator evidence -> export
```

The proof uses the real Python `UABProvider` authorization, classification, replay validation, receipt-context handling, and canonical UAB receipt path. The only allowed deterministic seam is the daemon boundary: `_rpc_call` may be faked or spied so the run does not require a live desktop automation target.

## Scenario

The controlled proof run must include:

- missing-grant mutation denial before `_rpc_call`
- valid scoped grant execution in controlled mode
- hostile grant rejections for tampered action, expired grant, wrong PID, wrong selector scope, unknown UAB risk label, replayed nonce on the same provider instance, and missing signature
- same-provider-instance replay rejection after the first valid execution
- safe non-sensitive read without a grant
- sensitive read without a grant
- sensitive read with a valid grant in controlled mode
- controlled RPC failure after a valid grant
- canonical receipt reconstruction
- served-runtime smoke and operator or receipt visibility evidence

## Deterministic Inputs

- `workflow_id`: `proof-workflow-001`
- `run_id`: `proof-run-001`
- `operator_id`: `proof-operator`
- proof key label: `proof-uab-grant-key-not-secret`
- app: `ControlledProofApp`
- PID: `1001`
- safe selector scope: `proof.safe.input`
- sensitive selector scope: `proof.sensitive.capture`

The proof key is a public deterministic fixture key for repeatable proof generation. It is not a credential and must not be described as a production secret.

## Receipt Rules

Canonical receipt claims are valid only when the case supplies enough `uabReceiptContext` to build canonical UAB receipt metadata and persist it through `ReceiptService`.

Every denial case must classify receipt expectation as one of:

- `canonical_denial_receipt`
- `local_denial_event_only`

The final packet may claim canonical UAB success, denial, and failure receipts only for cases where canonical receipt context is supplied and the persisted receipt exists.

## Replay Claim

The replay proof is intentionally narrow. The final packet may claim only:

```text
Replaying the same authority grant against the same UABProvider instance is denied before the second RPC call.
```

The proof must not claim durable cross-process replay protection.

## Runtime And Operator Visibility

The proof must collect served runtime evidence for:

- `/health`
- `/health/ready`
- `/ready`
- `/war-room/`

Operator visibility must be shown through authenticated API/TestClient/session evidence when available. If the proof uses direct `ReceiptService` lookup instead, the packet must explicitly say that operator API auth was not exercised.

## Non-Goals

This proof does not implement or claim:

- production auth completion
- separate UI-surface auth completion
- multi-tenancy
- Docker customer deployment completion
- live desktop UAB action execution unless optional live mode is separately exercised
- HIVE production readiness
- federation production readiness
- durable cross-process replay protection
- broad repo-wide 90 percent coverage
