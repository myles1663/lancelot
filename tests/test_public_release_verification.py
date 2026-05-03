from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_release_verifier():
    root = Path(__file__).resolve().parents[1]
    path = root / "scripts" / "verify-public-release.py"
    spec = importlib.util.spec_from_file_location("verify_public_release", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_runtime_data_patterns_are_public_release_blocked():
    verifier = _load_release_verifier()

    assert "lancelot_data/" in verifier.RUNTIME_DATA_PREFIXES
    assert "data/" in verifier.RUNTIME_DATA_PREFIXES
    assert "secrets/" in verifier.RUNTIME_DATA_PREFIXES
    assert ".sqlite" in verifier.RUNTIME_DATA_SUFFIXES
    assert "coverage.xml" in verifier.RUNTIME_DATA_FILES


def test_internal_spec_and_blueprint_docs_are_public_release_blocked():
    root = Path(__file__).resolve().parents[1]
    verifier = _load_release_verifier()
    exclude_file = root / "scripts" / "public-release-exclude.txt"
    if exclude_file.exists():
        exclude_text = exclude_file.read_text(encoding="utf-8")
        assert "docs/specs/" in exclude_text
        assert "docs/blueprints/" in exclude_text

    forbidden = {
        "docs/specs/",
        "docs/blueprints/",
    }
    tracked_examples = {
        "docs/specs/Technical_Specifications.md",
        "docs/blueprints/Lancelot_ToolFabric_Blueprint.md",
    }
    for path in tracked_examples:
        assert any(path.startswith(prefix) for prefix in forbidden)

    # Keep the public artifact verifier in sync with the release exclusion file.
    source = (root / "scripts" / "verify-public-release.py").read_text(encoding="utf-8")
    assert '"docs/specs/"' in source
    assert '"docs/blueprints/"' in source
    assert callable(verifier.check_no_local_only_files)


def test_dockerfile_does_not_copy_operator_runtime_state():
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (root / ".dockerignore").read_text(encoding="utf-8")

    assert "cp -r lancelot_data" not in dockerfile
    assert "onboarding_snapshot.json" not in dockerfile
    assert "USER.md" not in dockerfile
    assert "/lancelot_data/" in dockerignore
    assert "/data/" in dockerignore
    assert "/secrets/" in dockerignore
