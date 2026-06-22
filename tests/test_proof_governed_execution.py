import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "proof" / "run_governed_execution_proof.py"


def run_proof(tmp_path: Path, *args: str) -> Path:
    output_dir = tmp_path / "proof"
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--output-dir", str(output_dir)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert '"result": "PASS"' in completed.stdout
    return output_dir


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_proof_harness_uses_real_provider_path_and_emits_manifest(tmp_path):
    output_dir = run_proof(tmp_path, "--mode", "deterministic", "--case", "valid-grant-execution")

    manifest = load_json(output_dir / "proof-run-manifest.json")
    valid = load_json(output_dir / "proof-valid-grant-action.json")

    assert manifest["uab_provider_path_used"] is True
    assert manifest["rpc_call_faked_or_spied"] is True
    assert manifest["proof_key_is_real_secret"] is False
    assert valid["authority"]["grant_valid"] is True
    assert valid["rpc_call_count"] == 1
    assert valid["receipt_created"] is True
    assert valid["authority"]["grant_artifact_contains_policy_version"] is True


def test_proof_all_packet_contains_required_cases_and_receipts(tmp_path):
    output_dir = run_proof(tmp_path, "--all")

    packet = output_dir / "proof-of-governed-execution-packet.zip"
    bundle = load_json(output_dir / "proof-receipt-bundle.json")
    manifest = load_json(output_dir / "proof-run-manifest.json")
    negative = load_json(output_dir / "proof-negative-cases.json")
    sensitive = load_json(output_dir / "proof-sensitive-read-cases.json")
    failure = load_json(output_dir / "proof-failure-case.json")

    assert packet.exists()
    assert {case["case_id"] for case in manifest["cases"]} >= {
        "missing-grant-denial",
        "valid-grant-execution",
        "hostile-replayed-nonce",
        "safe-non-sensitive-read",
        "sensitive-read-without-grant",
        "sensitive-read-with-valid-grant",
        "controlled-rpc-failure",
    }
    assert any(case["case_id"] == "missing-grant-denial" for case in negative["cases"])
    assert any(case["case_id"] == "sensitive-read-with-valid-grant" for case in sensitive["cases"])
    assert failure["status"] == "failed"
    assert bundle["receipt_count"] >= 5
    assert bundle["integrity_issues"] == []
    outcomes = {
        receipt["proof_summary"]["outcome"]
        for receipt in bundle["receipts"]
        if receipt["proof_summary"]["outcome"]
    }
    assert {"success", "denied", "failed"}.issubset(outcomes)


def test_proof_artifacts_match_declared_case_expectations(tmp_path):
    output_dir = run_proof(tmp_path, "--all")

    manifest = load_json(output_dir / "proof-run-manifest.json")
    sensitive = load_json(output_dir / "proof-sensitive-read-cases.json")
    hostile = load_json(output_dir / "proof-hostile-grant-cases.json")

    for case in manifest["cases"]:
        expectation = case["receipt_expectation"]
        if expectation == "canonical_success_receipt":
            assert case["status"] == "success"
            assert case["receipt_created"] is True
            assert case["rpc_call_count"] >= 1
        elif expectation == "canonical_failure_receipt":
            assert case["status"] == "failed"
            assert case["receipt_created"] is True
            assert case["rpc_call_count"] >= 1
        elif expectation == "canonical_denial_receipt":
            assert case["status"] == "denied"
            assert case["receipt_created"] is True
            assert case["rpc_call_count"] == 0
        elif expectation == "local_denial_event_only":
            assert case["status"] == "denied"
            assert case["receipt_created"] is False
            assert case["rpc_call_count"] == 0
        elif expectation == "no_canonical_receipt_without_grant":
            assert case["status"] == "success"
            assert case["receipt_created"] is False
            assert case["rpc_call_count"] >= 1
        elif expectation == "canonical_success_then_denial_receipts":
            assert case["status"] == "denied"
            assert case["receipt_created"] is True
            assert case["rpc_call_count"] == 1
        else:
            raise AssertionError(expectation)

    valid_sensitive = next(
        case for case in sensitive["cases"] if case["case_id"] == "sensitive-read-with-valid-grant"
    )
    assert valid_sensitive["status"] == "success"
    assert valid_sensitive["rpc_call_count"] == 1

    unknown_risk = next(
        case for case in hostile["cases"] if case["case_id"] == "hostile-unknown-uab-risk-label"
    )
    assert unknown_risk["receipt_expectation"] == "local_denial_event_only"


def test_proof_runtime_smoke_checks_required_operator_paths(tmp_path):
    output_dir = run_proof(tmp_path, "--all")

    runtime = load_json(output_dir / "proof-runtime-smoke.json")

    assert runtime["all_required_paths_checked"] is True
    checks = {item["path"]: item for item in runtime["checks"]}
    assert set(checks) == {"/health", "/health/ready", "/ready", "/war-room/"}
    assert all(item["ok"] is True for item in checks.values())
