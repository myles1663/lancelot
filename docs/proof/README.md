# Governed Execution Proof

Artifact Status: public-source-ready

Source of truth:

- `scripts/proof/run_governed_execution_proof.py`
- `tests/test_proof_governed_execution.py`
- `docs/proof/proof-of-governed-execution-scenario.md`
- `docs/proof/proof-control-to-evidence-matrix.md`

Generated packet: `artifacts/proof-of-governed-execution/proof-of-governed-execution-packet.zip`

The generated packet is a local or release artifact. Do not commit it to the public source tree. Regenerate it from a clean release candidate or CI run and attach it to the release, evaluator packet, or internal evidence store after the public-artifact guard passes.

## Regenerate The Proof Packet

From the repository root:

```bash
npm --prefix src/warroom ci
npm --prefix src/warroom run type-check
npm --prefix src/warroom run build
python scripts/proof/run_governed_execution_proof.py --all
python -m pytest -q tests/test_proof_governed_execution.py
python -m zipfile -t artifacts/proof-of-governed-execution/proof-of-governed-execution-packet.zip
```

The War Room build is a prerequisite because the proof validates `/war-room/`
through the same SPA mount path used by the gateway. A clean source checkout
without `src/warroom/dist/` should build the SPA before generating the proof
packet.

The runner writes deterministic proof artifacts under `artifacts/proof-of-governed-execution/`, including negative cases, hostile grant cases, sensitive-read cases, receipt bundle, receipt chain, runtime smoke evidence, War Room evidence, and the packet zip.

## What The Proof Covers

The proof exercises the real Python `UABProvider` authorization, classification, replay validation, receipt-context handling, and canonical UAB receipt path. The only deterministic test double is the daemon boundary: `_rpc_call` may be faked or spied so the proof does not require a live desktop automation target.

The controlled proof run covers:

- missing-grant mutation denial before `_rpc_call`
- valid scoped authority grant execution in controlled mode
- hostile grant rejection for tampered action, expired grant, wrong PID, wrong selector scope, unknown UAB risk label, replayed nonce on the same provider instance, and missing signature
- same-provider-instance replay rejection after first valid execution
- safe non-sensitive read without a grant
- sensitive read denial without a grant
- grant-backed sensitive read in controlled mode
- controlled RPC failure after a valid grant
- canonical success, denial, and failure receipt reconstruction when `uabReceiptContext` is supplied
- runtime smoke evidence for `/health`, `/health/ready`, `/ready`, and `/war-room/`

## Claim Limits

This proof does not claim:

- production auth completion
- separate UI-surface auth completion
- multi-tenant enforcement completion
- live desktop UAB execution unless optional live mode is separately exercised
- durable cross-process replay protection
- HIVE or federation production readiness
- broad repository-wide coverage

Operator visibility is valid only for the evidence path exercised by the run. When the packet uses direct `ReceiptService` lookup instead of authenticated operator API/TestClient/session evidence, the packet must keep that caveat visible.

## Public Release Rule

Public source should include reproducible proof machinery and public-safe documentation. Generated proof outputs should stay out of source control unless a maintainer explicitly promotes a specific artifact as public release evidence.

Before publishing a public release candidate, run the normal release guard in the development checkout:

```bash
python scripts/verify-public-release.py --skip-pytest --skip-uab --skip-docker
```

After release prep creates the public artifact tree, run the stricter public-artifact guard there:

```bash
python scripts/verify-public-release.py --skip-pytest --skip-uab --skip-docker --public-artifact
```
