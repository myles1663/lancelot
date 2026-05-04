from pathlib import Path

import pytest

from scripts.prepare_guided_walkthrough import (
    REQUEST_TEXT,
    ensure_inside,
    prepare_walkthrough,
)


def test_prepare_walkthrough_creates_disposable_workspace(tmp_path: Path):
    paths = prepare_walkthrough(tmp_path)

    assert paths["directory"] == tmp_path / "guided-walkthrough"
    assert paths["candidate"].read_text(encoding="utf-8").startswith("Lancelot guided walkthrough")
    assert paths["request"].read_text(encoding="utf-8").strip() == REQUEST_TEXT


def test_prepare_walkthrough_reset_removes_prior_artifacts(tmp_path: Path):
    first = prepare_walkthrough(tmp_path)
    stale_artifact = first["directory"] / "stale.txt"
    stale_artifact.write_text("old artifact", encoding="utf-8")

    second = prepare_walkthrough(tmp_path)

    assert not stale_artifact.exists()
    assert second["candidate"].exists()


def test_ensure_inside_rejects_paths_outside_workspace(tmp_path: Path):
    outside = tmp_path.parent / "outside.txt"

    with pytest.raises(ValueError, match="Refusing to write outside workspace"):
        ensure_inside(tmp_path, outside)
