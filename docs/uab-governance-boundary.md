# UAB Governance Boundary

UAB is Lancelot's desktop automation enforcement surface. It is not the policy
source of truth.

## Authority Model

| Layer | Role |
|---|---|
| Python governance core | Policy decision point. Issues scoped authority grants for governed UAB actions. |
| UAB TypeScript runtime | Policy enforcement point. Validates grants and fails closed for governed actions. |
| Canonical receipt service | Proof system. Records success, denial, and failure outcomes after CORE-B3 wiring. |
| War Room | Operator visibility surface. It may display state but does not define authority. |

The Python grant shape is `UABAuthorityGrant` in
`src/core/execution_authority/uab_grant.py`. It is signed with deterministic
canonical JSON and includes grant ID, issue/expiration time, nonce, governance
risk tier, UAB risk label, capability, app/action target, selector scope,
sensitivity flags, policy/Soul version, workflow/run context, parent receipt ID,
approval ID, and signature.

The Python UAB provider in `src/tools/providers/uab_bridge.py` is also an
enforcement boundary. It classifies actions through the shared UAB action-risk
manifest before daemon RPCs, denies governed raw provider calls without a valid
`UABAuthorityGrant`, and records provider-local denial events for the interim
receipt/event path.

The TypeScript verifier lives in `packages/uab/src/governance/grants.ts`. UAB-A3
validates any supplied `ActionParams.uabAuthorityGrant` before a connector or
service action reaches the underlying desktop connection. UAB-A4 makes the local
daemon enforcement-only for governed actions: mutating, destructive,
external-submission, credential-sensitive, and sensitive-read actions without a
valid central grant fail closed before reaching the underlying connection.

## Action Policy

| Action class | Target behavior |
|---|---|
| Read-only non-sensitive | Allowed when classified safe and non-sensitive. |
| Sensitive read | Requires central classification or authority grant; otherwise deny or escalate. |
| Mutating | Requires valid `UABAuthorityGrant`. |
| Destructive | Requires valid `UABAuthorityGrant` and approval metadata where policy requires it. |
| External submission | Requires valid `UABAuthorityGrant`. |
| Credential-sensitive | Requires valid `UABAuthorityGrant`. |

## Standalone Product Boundary

Lancelot embeds UAB but should not make Lancelot gateway or orchestration modules
depend on UAB internals. Core integration belongs behind a narrow adapter/service
boundary that can pass health, registration, grant, request, response, and
receipt metadata without coupling to daemon implementation details.

The approved embedded-product facade is `src/core/uab_runtime_adapter.py`.
Gateway boot and HIVE support modules may import this facade, but must not import
`src.tools.providers.uab_bridge`, `tools.providers.uab_bridge`, or `packages.uab`
directly. `tests/test_uab_import_boundaries.py` locks this rule so UAB can remain
standalone-maintainable and future standalone UAB changes can be merged back into
the embedded Lancelot version through one audited boundary.

The facade also supplies the canonical receipt service when Core creates the
embedded provider, and Tool Fabric UAB feature-flag registration must create the
provider through that facade. Direct provider tests and standalone UAB
development may inject a receipt service explicitly or run without canonical
receipt persistence.

## HIVE Bridge Rule

HIVE UAB mutating actions are governed actions. `src/hive/integration/uab_bridge.py`
and `src/hive/integration/uab_executor.py` must fail closed when the governance
bridge is missing, when governance denies, or when scoped Soul requires approval
or rejects the UAB capability. Approved governance decisions issue a signed
`UABAuthorityGrant` using the shared UAB action-risk manifest and pass it in
provider action params as `uabAuthorityGrant` where the provider API supports
params. Governed provider methods without a grant-carrying API fail closed until
the Python UAB provider hardening ticket exposes that authority path.

## Receipt Rule

UAB local audit is useful runtime telemetry, but it is not canonical proof. The
hardening plan defines UAB receipt metadata in UAB-A7 and wires final canonical
success, denial, and failure receipts through the Core receipt facade in CORE-B3.
The UAB-A7 compatibility adapter may carry `UABReceiptMetadata` inside the
shared receipt shape, but it must mark `local_uab_audit_is_canonical=false` and
`canonical_receipt_deferred_to=CORE-B3`. UAB must not grow its own receipt store
or import pre-split receipt internals to prove governed outcomes.

CORE-B3 adds the canonical path: `src/tools/receipts_uab.py` creates UAB action
receipts and persists them with `ReceiptService.create`. The Python UAB provider
emits canonical receipts for successful, failed, and denied governed action
outcomes when a grant or receipt context provides workflow/run/parent proof
fields. This includes the generic `act()` path and non-`act()` helper/dict paths
such as keypresses and chains. Those canonical receipts use
`canonical_receipt_source=core-receipt-service`, preserve
`local_uab_audit_is_canonical=false`, include grant ID when a grant was used,
link to parent workflow/run fields, omit the compatibility deferral marker, and
do not rely on local UAB audit files. Failed daemon outcomes that omit an error
string use a deterministic fallback reason so failure receipts are not skipped.
CORE-B3 proof covers direct provider injection, adapter-created providers, and
Tool Fabric's enabled `uab_bridge` provider registration path, plus non-`act()`
success, failure, and denial paths.
