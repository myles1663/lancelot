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
    assert "coverage*.json" in verifier.RUNTIME_DATA_GLOBS


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
    assert "coverage*.json" in dockerignore
