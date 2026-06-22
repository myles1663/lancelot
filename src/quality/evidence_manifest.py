"""Validation helpers for closeout evidence manifests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


REQUIRED_FIELDS = (
    "package",
    "commit_sha",
    "branch",
    "timestamp_utc",
    "python_version",
    "node_version",
    "os",
    "ldds_closed",
    "tickets_closed",
    "commands_run",
    "coverage_reports",
    "docs_updated",
    "evidence_artifacts",
    "waivers",
    "deferred_scope",
    "operator_smoke",
    "final_gate_status",
)

FORBIDDEN_COMPLETED_SCOPE = (
    "admin auth",
    "presentation auth",
    "multi-tenancy",
    "federation expansion",
    "war room features",
    "new uab plugins",
)


@dataclass(frozen=True)
class ManifestValidationResult:
    """Result returned by manifest validation."""

    ok: bool
    errors: tuple[str, ...]


class EvidenceManifestError(ValueError):
    """Raised when evidence manifest validation fails."""


def load_manifest(path: str | Path) -> dict[str, Any]:
    """Load a JSON evidence manifest."""

    manifest_path = Path(path)
    with manifest_path.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise EvidenceManifestError("Manifest root must be a JSON object")
    return data


def validate_manifest(
    manifest: dict[str, Any],
    *,
    repo_root: str | Path | None = None,
    require_paths: bool = False,
) -> ManifestValidationResult:
    """Validate a closeout evidence manifest.

    Path validation is optional so default unit tests can run in a clean
    checkout without generated artifacts.
    """

    errors: list[str] = []
    _validate_required_fields(manifest, errors)
    _validate_collection_shapes(manifest, errors)
    _validate_waivers(manifest, errors)
    _validate_forbidden_completed_scope(manifest, errors)
    if require_paths:
        _validate_paths(manifest, repo_root=repo_root, errors=errors)
    return ManifestValidationResult(ok=not errors, errors=tuple(errors))


def validate_manifest_or_raise(
    manifest: dict[str, Any],
    *,
    repo_root: str | Path | None = None,
    require_paths: bool = False,
) -> None:
    """Validate a manifest and raise a useful error on failure."""

    result = validate_manifest(manifest, repo_root=repo_root, require_paths=require_paths)
    if not result.ok:
        raise EvidenceManifestError("\n".join(result.errors))


def _validate_required_fields(manifest: dict[str, Any], errors: list[str]) -> None:
    for field in REQUIRED_FIELDS:
        if field not in manifest:
            errors.append(f"Missing required field: {field}")


def _validate_collection_shapes(manifest: dict[str, Any], errors: list[str]) -> None:
    list_fields = (
        "ldds_closed",
        "tickets_closed",
        "commands_run",
        "coverage_reports",
        "docs_updated",
        "evidence_artifacts",
        "waivers",
        "deferred_scope",
    )
    for field in list_fields:
        if field in manifest and not isinstance(manifest[field], list):
            errors.append(f"Field must be a list: {field}")
    if "operator_smoke" in manifest and not isinstance(manifest["operator_smoke"], dict):
        errors.append("Field must be an object: operator_smoke")


def _validate_waivers(manifest: dict[str, Any], errors: list[str]) -> None:
    waivers = manifest.get("waivers", [])
    if not isinstance(waivers, list):
        return
    for index, waiver in enumerate(waivers):
        if not isinstance(waiver, dict):
            errors.append(f"Waiver {index} must be an object")
            continue
        for field in ("reason", "risk", "owner"):
            if not waiver.get(field):
                errors.append(f"Waiver {index} missing required field: {field}")
        if not (waiver.get("follow_up") or waiver.get("follow_up_ticket")):
            errors.append(
                f"Waiver {index} missing required field: follow_up or follow_up_ticket"
            )


def _validate_forbidden_completed_scope(
    manifest: dict[str, Any], errors: list[str]
) -> None:
    completed_text = " ".join(
        _iter_text_values(
            manifest.get("ldds_closed", []),
            manifest.get("tickets_closed", []),
            manifest.get("docs_updated", []),
            manifest.get("completed_scope", []),
        )
    ).lower()
    for forbidden in FORBIDDEN_COMPLETED_SCOPE:
        if forbidden in completed_text:
            errors.append(f"Forbidden completed scope appears in manifest: {forbidden}")


def _validate_paths(
    manifest: dict[str, Any],
    *,
    repo_root: str | Path | None,
    errors: list[str],
) -> None:
    root = Path(repo_root or ".").resolve()
    path_sources = (
        ("coverage_reports", manifest.get("coverage_reports", [])),
        ("evidence_artifacts", manifest.get("evidence_artifacts", [])),
        ("commands_run", manifest.get("commands_run", [])),
        ("docs_updated", manifest.get("docs_updated", [])),
    )
    for field, values in path_sources:
        for path_value in _iter_declared_paths(values):
            candidate = Path(path_value)
            if not candidate.is_absolute():
                candidate = root / candidate
            if not candidate.exists():
                errors.append(f"Declared path does not exist in {field}: {path_value}")


def _iter_declared_paths(values: Any) -> Iterable[str]:
    if not isinstance(values, list):
        return
    for value in values:
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for key in ("path", "log_path", "artifact", "report"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate:
                    yield candidate


def _iter_text_values(*values: Any) -> Iterable[str]:
    for value in values:
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            for item in value:
                yield from _iter_text_values(item)
        elif isinstance(value, dict):
            for item in value.values():
                yield from _iter_text_values(item)
