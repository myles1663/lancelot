# UAB Threat Model

## Scope

This threat model covers Lancelot's embedded Universal Application Bridge (UAB)
and its governed integration with the Python runtime. It does not add admin auth,
separate UI-surface auth, multi-tenancy, federation expansion, HIVE expansion, War Room
features, or new UAB plugins.

## Assets

- Desktop application state and user data visible through UAB.
- Credentials, tokens, and credential-sensitive UI surfaces.
- Python-issued UAB authority grants.
- UAB action requests, responses, local audit entries, and canonical receipt
  metadata.
- Operator approval and governance decisions.

## Threats and Controls

| Threat | Control | Required proof |
|---|---|---|
| Mutating desktop action without central authority | Governed daemon and Python provider actions fail closed on missing or invalid `UABAuthorityGrant`. | Hostile UAB tests and denial metadata. |
| Sensitive read treated as safe because it is read-only | Central classification or grant required for sensitive reads, including Python provider helper calls. | Sensitive-read allow/deny tests. |
| Tampered, expired, replayed, or wrong-target grant | Python provider and TypeScript permission paths validate signatures and scope, then consume nonces so replayed grants fail before a second execution. | Grant verifier negative tests and UAB-A8 direct replay proof. |
| HIVE bridge bypasses governance | HIVE requests central authority, denies when governance is missing or rejects the action, and injects a signed `UABAuthorityGrant` only after approval. | HIVE approve, deny, missing-governance, scoped-Soul, and approval-required tests. |
| UAB local audit mistaken for canonical proof | UAB-A7 metadata and the compatibility adapter mark local audit as non-canonical; CORE-B3 persists canonical UAB outcome receipts through the Core receipt service. | Local-only audit rejection, metadata adapter tests, CORE-B3 receipt tests, and canonical receipt proof for direct, adapter, Tool Fabric, and non-`act()` provider paths. |
| Gateway couples directly to UAB internals | Import-boundary tests restrict UAB internals to `src/core/uab_runtime_adapter.py`; gateway, boot, and orchestrator entries must import only the facade. | Boundary test failure on direct import and UAB-A8 AST proof artifact. |
| Risk label drift causes under-classification | Explicit mapping between governance, Tool Fabric, and UAB labels. | Drift tests for unknown labels. |
| Denied UAB outcome is misread as provider failure | Canonical metadata preserves `outcome=denied` or `outcome=failed` plus stable reason fields. | Receipt query and metadata tests distinguish denied from failed outcomes. |

## Fail-Closed Defaults

Unknown action classes, unknown risk labels, invalid grants, missing governance,
and unclassified sensitive reads must fail closed or escalate through the central
governance path. UAB-A3 applies this to supplied invalid grants; UAB-A4 applies
it to missing grants for governed UAB actions; UAB-A5 applies it to HIVE UAB
mutating actions before provider execution; UAB-A6 applies it to direct Python
provider calls before daemon RPC. These paths must not silently degrade to local
UAB permission logic.

## Grant Proof

Python issues `UABAuthorityGrant` objects from the execution authority boundary.
The model is deterministic JSON plus HMAC-SHA256 signature, scoped to the target
app/action/selector and time-limited by `expires_at`. Missing required fields,
expired grants, tampered payloads, unknown UAB risk labels, and target mismatch
fail validation before downstream UAB enforcement is allowed to trust the grant.
The TypeScript verifier recomputes the Python canonical HMAC payload and returns
stable denial reason codes such as `missing_signature`, `invalid_signature`,
`grant_expired`, `wrong_pid`, `wrong_action`, `wrong_selector_scope`,
`unknown_uab_risk`, `flag_mismatch`, and `replayed_nonce`. The Python provider
records replay denial as `replayed_authority_grant` and prevents the replayed
request from reaching the UAB daemon RPC boundary.

## Authority Grants

Authority grants are scoped execution credentials, not policy records. Core
decides whether an action may proceed, then issues a grant for one target,
action, selector scope, risk label, and validity window. UAB validates the grant
and fails closed when the grant is missing, malformed, expired, replayed, or
out of scope. UAB must not silently substitute local permission logic for a
missing Core governance decision.

## Canonical UAB Receipt Proof

CORE-B3 stores UAB action outcomes through the split Core receipt service. A
canonical UAB receipt must use `ActionType.UAB_ACTION`, include the UAB metadata
payload, link to parent workflow/run context, include grant ID when a grant was
used, set `canonical_receipt_source=core-receipt-service`, and keep
`local_uab_audit_is_canonical=false`. Missing-grant denials may emit canonical
denial receipts when caller-provided receipt context supplies the workflow,
run, parent, risk, and selector fields required for proof. Local UAB audit files
remain telemetry only and must not be used as canonical proof. Canonical receipt
metadata must omit the compatibility deferral marker. Tool Fabric UAB
registration is part of this boundary: when `FEATURE_TOOLS_UAB` enables the
`uab_bridge` provider, Tool Fabric must create it through the Core UAB runtime
adapter so the canonical receipt service is present. Non-`act()` provider
helpers that authorize governed actions must also emit canonical receipts for
success, failure, and denial outcomes. If a daemon failure omits an error
string, the provider must synthesize a deterministic failure reason before
building canonical receipt metadata.
