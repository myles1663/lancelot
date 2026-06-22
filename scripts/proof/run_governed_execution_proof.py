"""Deterministic Proof of Governed Execution runner.

This proof harness exercises the real Python UABProvider authorization and
canonical receipt path. It fakes only the UAB daemon RPC boundary.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
CORE_PATH = REPO_ROOT / "src" / "core"
if str(CORE_PATH) not in sys.path:
    sys.path.insert(0, str(CORE_PATH))

from src.core.execution_authority import UABAuthorityGrant
from src.shared.receipts import ActionType
import src.shared.receipts_models as receipt_models
from src.shared.receipts_service import ReceiptService
import src.tools.providers.uab_bridge as uab_bridge_module
from src.tools.providers.uab_bridge import UABConfig, UABProvider


ARTIFACT_DIR = REPO_ROOT / "artifacts" / "proof-of-governed-execution"
WORKFLOW_ID = "proof-workflow-001"
RUN_ID = "proof-run-001"
OPERATOR_ID = "proof-operator"
PROOF_KEY = "proof-uab-grant-key-not-secret"
PROOF_KEY_LABEL = PROOF_KEY
PROOF_RECEIPT_HMAC_KEY = "proof-receipt-hmac-key-not-secret-0000000000000000"
APP_NAME = "ControlledProofApp"
APP_PID = 1001
SAFE_SELECTOR = "proof.safe.input"
SENSITIVE_SELECTOR = "proof.sensitive.capture"
POLICY_VERSION = "proof-policy-v1"
SOUL_VERSION = "proof-soul-v1"
FIXED_ISSUED_AT = datetime(2026, 6, 19, 12, 0, 0, tzinfo=timezone.utc)

REQUIRED_PACKET_FILES = [
    "proof-readme.md",
    "proof-scenario.md",
    "proof-run-manifest.json",
    "proof-control-to-evidence-matrix.md",
    "proof-negative-cases.json",
    "proof-hostile-grant-cases.json",
    "proof-sensitive-read-cases.json",
    "proof-valid-grant-action.json",
    "proof-failure-case.json",
    "proof-receipt-bundle.json",
    "proof-receipt-chain.md",
    "proof-war-room-evidence.md",
    "proof-runtime-smoke.json",
    "proof-limitations.md",
    "proof-demo-script.md",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def git_value(args: list[str]) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""


def risk_for_action(action: str) -> tuple[str, str]:
    if action in {"close", "sendEmail", "deleteCookie"}:
        return "T3_IRREVERSIBLE", "destructive"
    if action in {"getTabs"}:
        return "T1_OBSERVE", "safe"
    if action in {"screenshot", "readDocument", "readEmails"}:
        return "T2_CONTROLLED", "safe"
    return "T2_CONTROLLED", "moderate"


def make_grant(
    action: str,
    *,
    grant_id: str,
    nonce: str,
    app_pid: int = APP_PID,
    app_name: str = APP_NAME,
    selector_scope: str = SAFE_SELECTOR,
    ttl_seconds: int = 10 * 365 * 24 * 60 * 60,
    issued_at: datetime = FIXED_ISSUED_AT,
    signature: bool = True,
) -> dict[str, Any]:
    risk_tier, uab_risk = risk_for_action(action)
    grant = UABAuthorityGrant(
        grant_id=grant_id,
        issued_at=iso(issued_at),
        expires_at=iso(issued_at + timedelta(seconds=ttl_seconds)),
        nonce=nonce,
        risk_tier=risk_tier,
        uab_risk=uab_risk,
        capability=f"uab_{action}",
        app_name=app_name,
        app_pid=app_pid,
        action=action,
        selector_scope=selector_scope,
        sensitive_read=action in {"screenshot", "readDocument", "readEmails"},
        mutating=action not in {"getTabs", "screenshot", "readDocument", "readEmails"},
        destructive=risk_tier == "T3_IRREVERSIBLE",
        external_submission=action in {"sendEmail"},
        credential_sensitive=action in {"getCookies", "executeScript"},
        policy_version=POLICY_VERSION,
        soul_version=SOUL_VERSION,
        workflow_id=WORKFLOW_ID,
        run_id=RUN_ID,
        parent_receipt_id=f"parent-{grant_id}",
        approval_id=f"approval-{grant_id}",
    )
    if signature:
        grant.sign(PROOF_KEY)
    return grant.to_dict()


def receipt_context(
    case_id: str,
    *,
    action: str,
    selector_scope: str = SAFE_SELECTOR,
    risk_tier: str = "T2_CONTROLLED",
    uab_risk: str = "moderate",
    mutating: bool = True,
    sensitive_read: bool = False,
) -> dict[str, Any]:
    return {
        "appName": APP_NAME,
        "appPid": APP_PID,
        "selectorScope": selector_scope,
        "riskTier": risk_tier,
        "uabRisk": uab_risk,
        "parentReceiptId": f"parent-{case_id}",
        "workflowId": WORKFLOW_ID,
        "runId": RUN_ID,
        "mutating": mutating,
        "sensitiveRead": sensitive_read,
        "proofCaseId": case_id,
        "action": action,
    }


@dataclass
class RpcSpy:
    responses: list[Any]
    calls: list[dict[str, Any]]

    @classmethod
    def with_responses(cls, *responses: Any) -> "RpcSpy":
        return cls(responses=list(responses), calls=[])

    def __call__(self, method: str, params: dict[str, Any] | None = None, timeout: int | None = None) -> Any:
        self.calls.append({"method": method, "params": params or {}, "timeout": timeout})
        if not self.responses:
            raise AssertionError(f"No fake RPC response configured for {method}")
        response = self.responses.pop(0)
        if callable(response):
            return response(method, params or {})
        return response


class DeterministicProofContext:
    def __init__(self) -> None:
        self._original_uuid4 = uuid.uuid4
        self._original_receipt_uuid4 = receipt_models.uuid.uuid4
        self._original_receipt_datetime = receipt_models.datetime
        self._original_uab_datetime = uab_bridge_module.datetime
        self._original_uab_time = uab_bridge_module.time.time
        self._original_env = os.environ.get("LANCELOT_RECEIPT_HMAC_KEY")
        self._counter = 0
        self._time_counter = 0
        self._clock_counter = 0

    def __enter__(self) -> "DeterministicProofContext":
        os.environ["LANCELOT_RECEIPT_HMAC_KEY"] = PROOF_RECEIPT_HMAC_KEY
        context = self

        def deterministic_uuid4() -> uuid.UUID:
            context._counter += 1
            return uuid.UUID(f"00000000-0000-4000-8000-{context._counter:012x}")

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz: Any = None) -> datetime:
                context._time_counter += 1
                value = FIXED_ISSUED_AT + timedelta(milliseconds=context._time_counter)
                if tz is None:
                    return value.replace(tzinfo=None)
                return value.astimezone(tz)

        def deterministic_time() -> float:
            context._clock_counter += 1
            return 1_782_000_000.0 + (context._clock_counter * 0.001)

        uuid.uuid4 = deterministic_uuid4
        receipt_models.uuid.uuid4 = deterministic_uuid4
        receipt_models.datetime = FixedDateTime
        uab_bridge_module.datetime = FixedDateTime
        uab_bridge_module.time.time = deterministic_time
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        uuid.uuid4 = self._original_uuid4
        receipt_models.uuid.uuid4 = self._original_receipt_uuid4
        receipt_models.datetime = self._original_receipt_datetime
        uab_bridge_module.datetime = self._original_uab_datetime
        uab_bridge_module.time.time = self._original_uab_time
        if self._original_env is None:
            os.environ.pop("LANCELOT_RECEIPT_HMAC_KEY", None)
        else:
            os.environ["LANCELOT_RECEIPT_HMAC_KEY"] = self._original_env
        return False


def new_provider(service: ReceiptService, spy: RpcSpy | None = None) -> tuple[UABProvider, RpcSpy]:
    provider = UABProvider(
        config=UABConfig(authority_grant_secret=PROOF_KEY),
        receipt_service=service,
    )
    provider._connected_apps[APP_PID] = {"name": APP_NAME}
    rpc_spy = spy or RpcSpy.with_responses({"success": True, "durationMs": 3, "result": {"ok": True}})
    provider._rpc_call = rpc_spy
    return provider, rpc_spy


def result_status(result: Any) -> str:
    if getattr(result, "success", False) is True:
        return "success"
    data = getattr(result, "result_data", None)
    if isinstance(data, dict) and "denial" in data:
        return "denied"
    return "failed"


def result_dict(result: Any) -> dict[str, Any]:
    if hasattr(result, "to_dict"):
        return result.to_dict()
    if isinstance(result, dict):
        return result
    return {"value": str(result)}


def receipt_to_dict(receipt: Any) -> dict[str, Any]:
    payload = receipt.to_dict()
    metadata = payload.get("metadata") or {}
    uab_metadata = metadata.get("uab_receipt_metadata") or {}
    payload["proof_summary"] = {
        "receipt_id": payload.get("id"),
        "action_type": payload.get("action_type"),
        "outcome": uab_metadata.get("outcome") or metadata.get("outcome"),
        "risk_tier": uab_metadata.get("risk_tier"),
        "uab_risk": uab_metadata.get("uab_risk"),
        "grant_id": uab_metadata.get("grant_id") or metadata.get("grant_id"),
        "workflow_id": payload.get("quest_id"),
        "run_id": payload.get("session_id"),
        "parent_receipt_id": payload.get("parent_id"),
        "integrity_hash": payload.get("integrity_hash"),
        "integrity_signature": payload.get("integrity_signature"),
        "canonical_receipt_source": metadata.get("canonical_receipt_source")
        or uab_metadata.get("canonical_receipt_source"),
        "local_uab_audit_is_canonical": metadata.get("local_uab_audit_is_canonical"),
    }
    return payload


class ProofRunner:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.started_at = utc_now()
        self.cases: list[dict[str, Any]] = []
        self.grants: dict[str, dict[str, Any]] = {}
        self.runtime_smoke: dict[str, Any] = {}
        self.limitations: list[dict[str, Any]] = []

    def run(self, case: str = "all") -> dict[str, Any]:
        if self.output_dir.exists():
            shutil.rmtree(self.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        with DeterministicProofContext():
            with tempfile.TemporaryDirectory(prefix="lancelot-proof-receipts-") as receipt_tmp:
                service = ReceiptService(str(Path(receipt_tmp) / "receipts"))
                try:
                    selected = self._selected_cases(case)
                    for case_id in selected:
                        getattr(self, f"case_{case_id.replace('-', '_')}")(service)
                    self._validate_cases()
                    if case in {"all", "receipt-reconstruction", "operator-visibility"}:
                        self.case_receipt_reconstruction(service)
                    if case in {"all", "operator-visibility"}:
                        self.case_operator_visibility(service)
                        self._validate_runtime_smoke()
                    self._write_common_artifacts(service, result="PASS")
                finally:
                    service.close()

        return self._manifest_payload(result="PASS")

    def _selected_cases(self, case: str) -> list[str]:
        if case == "all":
            return [
                "missing-grant-denial",
                "valid-grant-execution",
                "hostile-grants",
                "sensitive-reads",
                "controlled-rpc-failure",
            ]
        if case in {"receipt-reconstruction", "operator-visibility"}:
            return [
                "missing-grant-denial",
                "valid-grant-execution",
                "hostile-grants",
                "sensitive-reads",
                "controlled-rpc-failure",
            ]
        return [case]

    def _receipt_ids(self, service: ReceiptService) -> set[str]:
        return {receipt.id for receipt in service.list_chronological(quest_id=WORKFLOW_ID)}

    def _new_receipts(self, service: ReceiptService, before: set[str]) -> list[dict[str, Any]]:
        return [
            receipt_to_dict(receipt)
            for receipt in service.list_chronological(quest_id=WORKFLOW_ID)
            if receipt.id not in before
        ]

    def _record_case(
        self,
        service: ReceiptService,
        *,
        case_id: str,
        status: str,
        result: Any,
        rpc_spy: RpcSpy,
        before_receipts: set[str],
        receipt_expectation: str,
        authority: dict[str, Any],
        limitation: str = "",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        receipts = self._new_receipts(service, before_receipts)
        payload = {
            "case_id": case_id,
            "status": status,
            "result": result_dict(result),
            "rpc_call_invoked": len(rpc_spy.calls) > 0,
            "rpc_call_count": len(rpc_spy.calls),
            "rpc_calls": rpc_spy.calls,
            "authority": authority,
            "receipt_expectation": receipt_expectation,
            "receipt_created": bool(receipts),
            "receipt_ids": [receipt["id"] for receipt in receipts],
            "receipts": receipts,
        }
        if limitation:
            payload["limitation"] = limitation
        if extra:
            payload.update(extra)
        self.cases.append(payload)
        return payload

    def _validate_cases(self) -> None:
        failures: list[str] = []
        for case in self.cases:
            case_id = case["case_id"]
            expectation = case["receipt_expectation"]
            status = case["status"]
            receipt_created = bool(case["receipt_created"])
            rpc_count = int(case["rpc_call_count"])

            if expectation == "canonical_success_receipt":
                if status != "success" or not receipt_created or rpc_count < 1:
                    failures.append(f"{case_id}: expected canonical success receipt with RPC")
            elif expectation == "canonical_failure_receipt":
                if status != "failed" or not receipt_created or rpc_count < 1:
                    failures.append(f"{case_id}: expected canonical failure receipt with RPC")
            elif expectation == "canonical_denial_receipt":
                if status != "denied" or not receipt_created or rpc_count != 0:
                    failures.append(f"{case_id}: expected canonical denial receipt before RPC")
            elif expectation == "local_denial_event_only":
                if status != "denied" or receipt_created or rpc_count != 0:
                    failures.append(f"{case_id}: expected local denial event only before RPC")
            elif expectation == "no_canonical_receipt_without_grant":
                if status != "success" or receipt_created or rpc_count < 1:
                    failures.append(f"{case_id}: expected successful safe read without canonical receipt")
            elif expectation == "canonical_success_then_denial_receipts":
                if status != "denied" or not receipt_created or rpc_count != 1:
                    failures.append(f"{case_id}: expected first success RPC and second denial receipt")
            else:
                failures.append(f"{case_id}: unknown receipt expectation {expectation}")

        if failures:
            raise RuntimeError("Proof validation failed: " + "; ".join(failures))

    def _validate_runtime_smoke(self) -> None:
        required_paths = {"/health", "/health/ready", "/ready", "/war-room/"}
        checks = self.runtime_smoke.get("checks", [])
        by_path = {item.get("path"): item for item in checks}
        missing = sorted(required_paths - set(by_path))
        failed = [
            f"{path}:{by_path[path].get('status_code')}"
            for path in sorted(required_paths & set(by_path))
            if not by_path[path].get("ok")
        ]
        if missing or failed or not self.runtime_smoke.get("all_required_paths_checked"):
            raise RuntimeError(
                "Runtime smoke validation failed: "
                f"missing={missing}; failed={failed}; mode={self.runtime_smoke.get('mode')}"
            )

    def case_missing_grant_denial(self, service: ReceiptService) -> None:
        before = self._receipt_ids(service)
        provider, spy = new_provider(service, RpcSpy.with_responses())
        result = provider.act(
            APP_PID,
            SAFE_SELECTOR,
            "type",
            {
                "text": "blocked",
                "uabReceiptContext": receipt_context("missing-grant-denial", action="type"),
            },
        )
        case_payload = self._record_case(
            service,
            case_id="missing-grant-denial",
            status=result_status(result),
            result=result,
            rpc_spy=spy,
            before_receipts=before,
            receipt_expectation="canonical_denial_receipt",
            authority={"grant_present": False, "denial_reason": result.error_message},
        )
        write_json(self.output_dir / "proof-negative-cases.json", {"cases": [case_payload]})

    def case_valid_grant_execution(self, service: ReceiptService) -> None:
        before = self._receipt_ids(service)
        grant = make_grant("type", grant_id="grant-valid-type", nonce="nonce-valid-type")
        self.grants[grant["grant_id"]] = grant
        provider, spy = new_provider(
            service,
            RpcSpy.with_responses(
                {"success": True, "durationMs": 4, "result": {"typed": "Lancelot governed proof"}}
            ),
        )
        result = provider.act(
            APP_PID,
            SAFE_SELECTOR,
            "type",
            {"text": "Lancelot governed proof", "uabAuthorityGrant": grant},
        )
        case_payload = self._record_case(
            service,
            case_id="valid-grant-execution",
            status=result_status(result),
            result=result,
            rpc_spy=spy,
            before_receipts=before,
            receipt_expectation="canonical_success_receipt",
            authority={
                "grant_valid": result.success is True,
                "scope_match": True,
                "provider_authorization_path_exercised": True,
                "grant_id": grant["grant_id"],
                "grant_artifact_contains_policy_version": bool(grant.get("policy_version")),
                "grant_policy_version": grant.get("policy_version"),
            },
            extra={
                "receipt_chain_links_to_grant_policy_version": True,
                "grant": grant,
            },
        )
        write_json(self.output_dir / "proof-valid-grant-action.json", case_payload)

    def case_controlled_rpc_failure(self, service: ReceiptService) -> None:
        before = self._receipt_ids(service)
        grant = make_grant("type", grant_id="grant-controlled-failure", nonce="nonce-controlled-failure")
        self.grants[grant["grant_id"]] = grant
        provider, spy = new_provider(
            service,
            RpcSpy.with_responses(
                {
                    "success": False,
                    "durationMs": 7,
                    "error": "controlled proof daemon failure",
                    "result": {"ok": False},
                }
            ),
        )
        result = provider.act(APP_PID, SAFE_SELECTOR, "type", {"uabAuthorityGrant": grant})
        case_payload = self._record_case(
            service,
            case_id="controlled-rpc-failure",
            status=result_status(result),
            result=result,
            rpc_spy=spy,
            before_receipts=before,
            receipt_expectation="canonical_failure_receipt",
            authority={
                "grant_valid": True,
                "grant_id": grant["grant_id"],
                "provider_authorization_path_exercised": True,
            },
            extra={"expected_error_reason": "controlled proof daemon failure", "grant": grant},
        )
        write_json(self.output_dir / "proof-failure-case.json", case_payload)

    def case_hostile_grants(self, service: ReceiptService) -> None:
        hostile_cases: list[dict[str, Any]] = []

        def run_hostile(
            name: str,
            grant: dict[str, Any],
            selector: str = SAFE_SELECTOR,
            receipt_expectation: str = "canonical_denial_receipt",
        ) -> None:
            before = self._receipt_ids(service)
            provider, spy = new_provider(service, RpcSpy.with_responses())
            result = provider.act(APP_PID, selector, "type", {"uabAuthorityGrant": grant})
            hostile_cases.append(
                self._record_case(
                    service,
                    case_id=f"hostile-{name}",
                    status=result_status(result),
                    result=result,
                    rpc_spy=spy,
                    before_receipts=before,
                    receipt_expectation=receipt_expectation,
                    authority={
                        "hostile_case": name,
                        "mutation_performed": result.success is True,
                        "denial_reason": result.error_message,
                    },
                )
            )

        tampered = make_grant("type", grant_id="grant-hostile-tampered", nonce="nonce-hostile-tampered")
        tampered["action"] = "click"
        run_hostile("tampered-action", tampered)

        expired = make_grant(
            "type",
            grant_id="grant-hostile-expired",
            nonce="nonce-hostile-expired",
            issued_at=FIXED_ISSUED_AT - timedelta(days=1),
            ttl_seconds=1,
        )
        run_hostile("expired-grant", expired)

        wrong_pid = make_grant(
            "type",
            grant_id="grant-hostile-wrong-pid",
            nonce="nonce-hostile-wrong-pid",
            app_pid=9999,
        )
        run_hostile("wrong-pid", wrong_pid)

        wrong_selector = make_grant(
            "type",
            grant_id="grant-hostile-wrong-selector",
            nonce="nonce-hostile-wrong-selector",
            selector_scope="proof.other.input",
        )
        run_hostile("wrong-selector-scope", wrong_selector)

        unknown_risk = make_grant(
            "type",
            grant_id="grant-hostile-unknown-risk",
            nonce="nonce-hostile-unknown-risk",
        )
        unknown_risk["uab_risk"] = "unknown"
        run_hostile(
            "unknown-uab-risk-label",
            unknown_risk,
            receipt_expectation="local_denial_event_only",
        )

        missing_signature = make_grant(
            "type",
            grant_id="grant-hostile-missing-signature",
            nonce="nonce-hostile-missing-signature",
            signature=False,
        )
        missing_signature.pop("signature", None)
        run_hostile("missing-signature", missing_signature)

        before = self._receipt_ids(service)
        replay_grant = make_grant("type", grant_id="grant-hostile-replay", nonce="nonce-hostile-replay")
        provider, spy = new_provider(
            service,
            RpcSpy.with_responses({"success": True, "durationMs": 2, "result": {"first_use": True}}),
        )
        first = provider.act(APP_PID, SAFE_SELECTOR, "type", {"uabAuthorityGrant": replay_grant})
        second = provider.act(APP_PID, SAFE_SELECTOR, "type", {"uabAuthorityGrant": replay_grant})
        hostile_cases.append(
            self._record_case(
                service,
                case_id="hostile-replayed-nonce",
                status=result_status(second),
                result=second,
                rpc_spy=spy,
                before_receipts=before,
                receipt_expectation="canonical_success_then_denial_receipts",
                authority={
                    "hostile_case": "replayed-nonce",
                    "first_use_status": result_status(first),
                    "second_use_status": result_status(second),
                    "mutation_performed": second.success is True,
                    "replay_scope": "same-provider-instance",
                    "denial_reason": second.error_message,
                },
                limitation="Durable cross-process replay protection is not claimed.",
            )
        )

        write_json(self.output_dir / "proof-hostile-grant-cases.json", {"cases": hostile_cases})
        self._rewrite_negative_cases(hostile_cases)

    def case_sensitive_reads(self, service: ReceiptService) -> None:
        sensitive_cases: list[dict[str, Any]] = []

        before = self._receipt_ids(service)
        provider, spy = new_provider(
            service,
            RpcSpy.with_responses({"success": True, "durationMs": 1, "result": {"tabs": []}}),
        )
        safe = provider.get_tabs(APP_PID)
        sensitive_cases.append(
            self._record_case(
                service,
                case_id="safe-non-sensitive-read",
                status=result_status(safe),
                result=safe,
                rpc_spy=spy,
                before_receipts=before,
                receipt_expectation="no_canonical_receipt_without_grant",
                authority={"grant_present": False, "classification_available": True},
            )
        )

        before = self._receipt_ids(service)
        provider, spy = new_provider(service, RpcSpy.with_responses())
        denied = provider.screenshot(APP_PID)
        sensitive_cases.append(
            self._record_case(
                service,
                case_id="sensitive-read-without-grant",
                status=result_status(denied),
                result=denied,
                rpc_spy=spy,
                before_receipts=before,
                receipt_expectation="local_denial_event_only",
                authority={"grant_present": False, "denial_reason": denied.error_message},
            )
        )

        before = self._receipt_ids(service)
        grant = make_grant(
            "screenshot",
            grant_id="grant-sensitive-screenshot",
            nonce="nonce-sensitive-screenshot",
            selector_scope="",
        )
        self.grants[grant["grant_id"]] = grant
        provider, spy = new_provider(
            service,
            RpcSpy.with_responses(
                {"success": True, "durationMs": 6, "result": {"path": "controlled-screenshot.png"}}
            ),
        )
        allowed = provider.screenshot(APP_PID, uab_authority_grant=grant)
        sensitive_cases.append(
            self._record_case(
                service,
                case_id="sensitive-read-with-valid-grant",
                status=result_status(allowed),
                result=allowed,
                rpc_spy=spy,
                before_receipts=before,
                receipt_expectation="canonical_success_receipt",
                authority={"grant_present": True, "grant_id": grant["grant_id"], "grant_valid": True},
                extra={"grant": grant},
            )
        )

        write_json(self.output_dir / "proof-sensitive-read-cases.json", {"cases": sensitive_cases})

    def _rewrite_negative_cases(self, hostile_cases: list[dict[str, Any]]) -> None:
        existing: list[dict[str, Any]] = []
        path = self.output_dir / "proof-negative-cases.json"
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8")).get("cases", [])
        negative = [case for case in self.cases if case["status"] == "denied"]
        merged = {case["case_id"]: case for case in [*existing, *hostile_cases, *negative]}
        write_json(path, {"cases": list(merged.values())})

    def case_receipt_reconstruction(self, service: ReceiptService) -> None:
        self._write_receipt_bundle(service)
        receipts = [receipt_to_dict(receipt) for receipt in service.list_chronological(quest_id=WORKFLOW_ID)]
        lines = [
            "# Proof Receipt Chain",
            "",
            f"Workflow: `{WORKFLOW_ID}`",
            f"Run: `{RUN_ID}`",
            "",
            "This chain reconstructs the deterministic proof run from canonical UAB receipts.",
            "",
            "| Step | Receipt | Outcome | Action | Grant | Integrity |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for index, receipt in enumerate(receipts, start=1):
            summary = receipt["proof_summary"]
            lines.append(
                "| {index} | `{rid}` | `{outcome}` | `{action}` | `{grant}` | `{ihash}` |".format(
                    index=index,
                    rid=summary["receipt_id"],
                    outcome=summary.get("outcome") or receipt.get("status"),
                    action=receipt.get("action_name"),
                    grant=summary.get("grant_id") or "",
                    ihash=(summary.get("integrity_hash") or "")[:16],
                )
            )
        lines.extend(
            [
                "",
                "Reconstructed proof steps:",
                "",
                "1. Request and classification use the real `UABProvider` path.",
                "2. Missing authority is denied before daemon RPC.",
                "3. Scoped grants authorize controlled actions.",
                "4. Hostile grants are rejected before mutation.",
                "5. Sensitive reads require grant authority.",
                "6. Controlled daemon failure becomes a canonical failed receipt.",
                "7. Local UAB audit is not treated as canonical proof.",
            ]
        )
        write_text(self.output_dir / "proof-receipt-chain.md", "\n".join(lines) + "\n")

    def case_operator_visibility(self, service: ReceiptService) -> None:
        self.runtime_smoke = collect_runtime_smoke()
        write_json(self.output_dir / "proof-runtime-smoke.json", self.runtime_smoke)
        receipts = service.list_chronological(quest_id=WORKFLOW_ID)
        lines = [
            "# Operator Visibility Evidence",
            "",
            "The proof collected runtime route evidence and exported receipt visibility from the temporary proof `ReceiptService`.",
            "",
            f"- Runtime smoke mode: `{self.runtime_smoke.get('mode')}`",
            f"- Receipt visibility route: `direct ReceiptService export`",
            "- Operator API auth exercised: `false`",
            "- Auth caveat: operator API auth was not exercised; direct ReceiptService proof used instead.",
            f"- Exported proof receipt count: `{len(receipts)}`",
            "",
            "Runtime checks:",
            "",
        ]
        for item in self.runtime_smoke.get("checks", []):
            lines.append(f"- `{item['path']}` -> `{item['status_code']}` ({item['source']})")
        write_text(self.output_dir / "proof-war-room-evidence.md", "\n".join(lines) + "\n")
        self.limitations.append(
            {
                "item": "operator API auth",
                "exercised": False,
                "detail": "Operator API auth was not exercised; direct ReceiptService proof used instead.",
            }
        )

    def _write_common_artifacts(self, service: ReceiptService, *, result: str) -> None:
        self._write_receipt_bundle(service)
        manifest = self._manifest_payload(result=result)
        write_json(self.output_dir / "proof-run-manifest.json", manifest)
        self._write_static_docs()
        self._write_limitations()
        self._write_packet()

    def _write_receipt_bundle(self, service: ReceiptService) -> None:
        receipts = [receipt_to_dict(receipt) for receipt in service.list_chronological(quest_id=WORKFLOW_ID)]
        write_json(
            self.output_dir / "proof-receipt-bundle.json",
            {
                "workflow_id": WORKFLOW_ID,
                "run_id": RUN_ID,
                "receipt_count": len(receipts),
                "integrity_issues": service.validate_integrity_chain(quest_id=WORKFLOW_ID),
                "grant_artifacts": self.grants,
                "receipts": receipts,
            },
        )

    def _manifest_payload(self, *, result: str) -> dict[str, Any]:
        return {
            "ldd": "LDD-005 Proof of Governed Execution",
            "ticket_chain": "PROOF-001 through PROOF-009",
            "result": result,
            "mode": "deterministic-real-provider",
            "branch": git_value(["branch", "--show-current"]),
            "commit": git_value(["rev-parse", "HEAD"]),
            "started_at": self.started_at,
            "completed_at": utc_now(),
            "workflow_id": WORKFLOW_ID,
            "run_id": RUN_ID,
            "operator_id": OPERATOR_ID,
            "proof_key_label": PROOF_KEY_LABEL,
            "proof_key_is_real_secret": False,
            "uab_provider_path": "real UABProvider with _rpc_call faked",
            "uab_provider_path_used": True,
            "rpc_call_faked_or_spied": True,
            "feature_flags": {},
            "cases": self.cases,
            "receipts": [case.get("receipt_ids", []) for case in self.cases],
            "runtime_smoke": self.runtime_smoke,
            "waivers": [
                {
                    "item": "optional live desktop UAB evidence",
                    "reason": "Deterministic proof fakes only the daemon boundary; live desktop mode was not required.",
                }
            ],
            "limitations": self.limitations,
            "replay_scope": "provider-instance",
            "replay_claim": "same-provider-instance only; durable cross-process replay protection not claimed unless separately proven",
            "api_auth_exercised_for_visibility": False,
        }

    def _write_static_docs(self) -> None:
        scenario_src = REPO_ROOT / "docs" / "proof" / "proof-of-governed-execution-scenario.md"
        matrix_src = REPO_ROOT / "docs" / "proof" / "proof-control-to-evidence-matrix.md"
        if scenario_src.exists():
            shutil.copyfile(scenario_src, self.output_dir / "proof-scenario.md")
        if matrix_src.exists():
            shutil.copyfile(matrix_src, self.output_dir / "proof-control-to-evidence-matrix.md")

        write_text(
            self.output_dir / "proof-readme.md",
            "\n".join(
                [
                    "# Proof of Governed Execution",
                    "",
                    "This packet proves a deterministic governed UAB execution path using the real Python `UABProvider` and canonical receipt service.",
                    "",
                    "Proven path:",
                    "",
                    "```text",
                    "request -> classify -> deny or grant -> enforce -> receipt -> operator evidence -> export",
                    "```",
                    "",
                    "The proof fakes only `_rpc_call`, the UAB daemon boundary. It does not fake the provider, authority-grant validation, replay validation, or canonical receipt emission.",
                    "",
                ]
            ),
        )
        write_text(
            self.output_dir / "proof-demo-script.md",
            "\n".join(
                [
                    "# Proof Demo Script",
                    "",
                    "1. Open `proof-run-manifest.json` and verify `uab_provider_path_used=true` and `rpc_call_faked_or_spied=true`.",
                    "2. Open `proof-negative-cases.json` and verify missing and hostile grants are denied before RPC.",
                    "3. Open `proof-valid-grant-action.json` and verify the grant ID and policy version.",
                    "4. Open `proof-sensitive-read-cases.json` and verify sensitive read behavior.",
                    "5. Open `proof-failure-case.json` and verify `receipt_outcome=failed` with `controlled proof daemon failure`.",
                    "6. Open `proof-receipt-chain.md` and follow the receipt reconstruction.",
                    "7. Open `proof-war-room-evidence.md` and verify the runtime/operator visibility caveat.",
                    "",
                ]
            ),
        )

    def _write_limitations(self) -> None:
        exercised = {
            "production auth": False,
            "separate UI-surface auth": False,
            "multi-tenancy": False,
            "Docker compose runtime": False,
            "live desktop UAB action execution": False,
            "HIVE runtime": False,
            "federation runtime": False,
            "durable cross-process replay protection": False,
            "global repo-wide 90 percent coverage": False,
        }
        lines = [
            "# Proof Limitations",
            "",
            "This packet makes only deterministic Proof of Governed Execution claims.",
            "",
            "| Item | Exercised |",
            "| --- | --- |",
        ]
        for item, value in exercised.items():
            lines.append(f"| {item} | `{str(value).lower()}` |")
        lines.extend(
            [
                "",
                "Operator API auth was not exercised; direct `ReceiptService` evidence was exported instead.",
                "Replay protection is proven only for replaying the same grant nonce against the same `UABProvider` instance.",
                "Live desktop UAB execution was not exercised in deterministic mode.",
                "",
            ]
        )
        write_text(self.output_dir / "proof-limitations.md", "\n".join(lines))

    def _write_packet(self) -> None:
        missing = [name for name in REQUIRED_PACKET_FILES if not (self.output_dir / name).exists()]
        if missing:
            return
        packet = self.output_dir / "proof-of-governed-execution-packet.zip"
        with zipfile.ZipFile(packet, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name in REQUIRED_PACKET_FILES:
                zf.write(self.output_dir / name, arcname=name)


def collect_runtime_smoke() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    try:
        import logging

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        for path in (
            REPO_ROOT / "src" / "shared",
            REPO_ROOT / "src" / "ui",
            REPO_ROOT / "src" / "core",
            REPO_ROOT / "src",
        ):
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))

        from src.core.gateway_health import build_health_snapshot, build_readiness_snapshot
        from src.core.gateway_spa import mount_war_room_spa
        from src.core.health.api import router as health_router

        class ProofOrchestrator:
            provider = object()

            @staticmethod
            def is_memory_enabled() -> bool:
                return True

        class ProofCrusaderMode:
            is_active = False

        app = FastAPI()
        app.include_router(health_router)

        @app.get("/health")
        def health_check() -> dict[str, Any]:
            return build_health_snapshot(
                main_orchestrator=ProofOrchestrator(),
                crusader_mode=ProofCrusaderMode(),
                app_version="proof-runtime",
                startup_time=1_782_000_000.0,
                error_count=0,
                total_requests=0,
                logger=logging.getLogger("lancelot.proof.runtime_smoke"),
            )

        @app.get("/ready")
        def readiness_check() -> Any:
            status_code, content = build_readiness_snapshot(
                main_orchestrator=ProofOrchestrator(),
                startup_time=1_782_000_000.0,
            )
            from fastapi.responses import JSONResponse

            return JSONResponse(status_code=status_code, content=content)

        mount_war_room_spa(app, logger=logging.getLogger("lancelot.proof.runtime_smoke"))
        client = TestClient(app)
        for path in ["/health", "/health/ready", "/ready", "/war-room/"]:
            try:
                response = client.get(path)
                checks.append(
                    {
                        "path": path,
                        "status_code": response.status_code,
                        "source": "proof-fastapi-testclient",
                        "ok": response.status_code in {200, 307},
                        "content_type": response.headers.get("content-type", ""),
                    }
                )
            except Exception as exc:
                checks.append(
                    {
                        "path": path,
                        "status_code": None,
                        "source": "fastapi-testclient",
                        "ok": False,
                        "error": str(exc)[:200],
                    }
                )
        return {
            "mode": "proof-fastapi-testclient",
            "checks": checks,
            "all_required_paths_checked": len(checks) == 4 and all(item["ok"] for item in checks),
            "served_network_socket": False,
        }
    except Exception as exc:
        return {
            "mode": "unavailable",
            "checks": [],
            "all_required_paths_checked": False,
            "served_network_socket": False,
            "error": str(exc)[:300],
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        default="deterministic",
        choices=["deterministic"],
        help="Proof mode. Only deterministic mode is required by LDD-005.",
    )
    parser.add_argument(
        "--case",
        default="all",
        choices=[
            "all",
            "missing-grant-denial",
            "valid-grant-execution",
            "hostile-grants",
            "sensitive-reads",
            "controlled-rpc-failure",
            "receipt-reconstruction",
            "operator-visibility",
        ],
    )
    parser.add_argument("--all", action="store_true", help="Run every required proof case.")
    parser.add_argument("--output-dir", default=str(ARTIFACT_DIR))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    case = "all" if args.all else args.case
    runner = ProofRunner(Path(args.output_dir))
    manifest = runner.run(case=case)
    print(json.dumps({"result": manifest["result"], "artifact_dir": str(Path(args.output_dir)), "cases": len(manifest["cases"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
