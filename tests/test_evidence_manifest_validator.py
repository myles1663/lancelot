from __future__ import annotations

import json
from pathlib import Path

from src.quality.evidence_manifest import load_manifest, validate_manifest


FIXTURE_ROOT = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def test_valid_manifest_fixture_passes_without_path_validation():
    manifest = _load_fixture("evidence_manifest_valid.json")

    result = validate_manifest(manifest)

    assert result.ok is True
    assert result.errors == ()


def test_missing_required_fields_fail_with_clear_errors():
    manifest = _load_fixture("evidence_manifest_invalid_missing_fields.json")

    result = validate_manifest(manifest)

    assert result.ok is False
    assert "Missing required field: branch" in result.errors
    assert "Missing required field: operator_smoke" in result.errors


def test_path_validation_can_be_enabled_against_repo_root():
    manifest = _load_fixture("evidence_manifest_valid.json")
    repo_root = Path(__file__).resolve().parents[1]

    result = validate_manifest(manifest, repo_root=repo_root, require_paths=True)

    assert result.ok is True


def test_path_validation_reports_missing_artifacts_when_required():
    manifest = _load_fixture("evidence_manifest_valid.json")
    manifest["coverage_reports"] = ["artifacts/missing-coverage.json"]

    result = validate_manifest(
        manifest,
        repo_root=Path(__file__).resolve().parents[1],
        require_paths=True,
    )

    assert result.ok is False
    assert "Declared path does not exist in coverage_reports: artifacts/missing-coverage.json" in result.errors


def test_waivers_require_reason_risk_owner_and_follow_up():
    manifest = _load_fixture("evidence_manifest_valid.json")
    manifest["waivers"] = [{"reason": "covered"}]

    result = validate_manifest(manifest)

    assert result.ok is False
    assert "Waiver 0 missing required field: risk" in result.errors
    assert "Waiver 0 missing required field: owner" in result.errors
    assert "Waiver 0 missing required field: follow_up or follow_up_ticket" in result.errors


def test_forbidden_completed_scope_is_rejected():
    manifest = _load_fixture("evidence_manifest_valid.json")
    manifest["tickets_closed"] = ["Implemented admin auth"]

    result = validate_manifest(manifest)

    assert result.ok is False
    assert "Forbidden completed scope appears in manifest: admin auth" in result.errors


def test_load_manifest_rejects_non_object_json(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("[]", encoding="utf-8")

    try:
        load_manifest(manifest_path)
    except ValueError as exc:
        assert "Manifest root must be a JSON object" in str(exc)
    else:
        raise AssertionError("expected non-object manifest to fail")
