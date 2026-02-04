"""
Tests for src.core.soul.store — Soul schema, loader, and versioning (Prompt 1 / A1).
"""

import pytest
import yaml
from pathlib import Path

from src.core.soul.store import (
    Soul,
    SoulStoreError,
    load_active_soul,
    list_versions,
    get_active_version,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_soul_dict(**overrides) -> dict:
    """Return a minimal valid soul dictionary."""
    base = {
        "version": "v1",
        "mission": "Serve the owner faithfully.",
        "allegiance": "Single owner loyalty.",
        "autonomy_posture": {
            "level": "supervised",
            "description": "Supervised autonomy.",
            "allowed_autonomous": ["classify_intent"],
            "requires_approval": ["deploy"],
        },
        "risk_rules": [
            {"name": "approval_required", "description": "Destructive actions need approval", "enforced": True},
        ],
        "approval_rules": {
            "default_timeout_seconds": 3600,
            "escalation_on_timeout": "skip_and_log",
            "channels": ["war_room"],
        },
        "tone_invariants": ["Never mislead the owner"],
        "memory_ethics": ["Do not store PII without consent"],
        "scheduling_boundaries": {
            "max_concurrent_jobs": 5,
            "max_job_duration_seconds": 300,
            "no_autonomous_irreversible": True,
            "require_ready_state": True,
            "description": "Safe scheduling.",
        },
    }
    base.update(overrides)
    return base


def _write_soul_dir(tmp_path, versions=None, active=None):
    """Create a soul directory with version files and optional ACTIVE pointer."""
    soul_dir = tmp_path / "soul"
    versions_dir = soul_dir / "soul_versions"
    versions_dir.mkdir(parents=True)

    if versions is None:
        versions = {"v1": _minimal_soul_dict(version="v1")}

    for ver, data in versions.items():
        path = versions_dir / f"soul_{ver}.yaml"
        path.write_text(yaml.dump(data), encoding="utf-8")

    if active is not None:
        (soul_dir / "ACTIVE").write_text(active, encoding="utf-8")

    return str(soul_dir)


# ===================================================================
# Pydantic Soul model validation
# ===================================================================

class TestSoulModel:

    def test_valid_soul(self):
        soul = Soul(**_minimal_soul_dict())
        assert soul.version == "v1"
        assert "owner" in soul.mission.lower()

    def test_version_format_enforced(self):
        with pytest.raises(Exception, match="vN"):
            Soul(**_minimal_soul_dict(version="1.0"))

    def test_version_v2_valid(self):
        soul = Soul(**_minimal_soul_dict(version="v2"))
        assert soul.version == "v2"

    def test_empty_mission_rejected(self):
        with pytest.raises(Exception, match="[Mm]ission"):
            Soul(**_minimal_soul_dict(mission=""))

    def test_empty_allegiance_rejected(self):
        with pytest.raises(Exception, match="[Aa]llegiance"):
            Soul(**_minimal_soul_dict(allegiance=""))

    def test_missing_autonomy_posture_rejected(self):
        d = _minimal_soul_dict()
        del d["autonomy_posture"]
        with pytest.raises(Exception):
            Soul(**d)

    def test_risk_rules_parsed(self):
        soul = Soul(**_minimal_soul_dict())
        assert len(soul.risk_rules) == 1
        assert soul.risk_rules[0].enforced is True

    def test_scheduling_boundaries_parsed(self):
        soul = Soul(**_minimal_soul_dict())
        assert soul.scheduling_boundaries.max_concurrent_jobs == 5
        assert soul.scheduling_boundaries.no_autonomous_irreversible is True

    def test_tone_invariants_parsed(self):
        soul = Soul(**_minimal_soul_dict())
        assert len(soul.tone_invariants) >= 1

    def test_memory_ethics_parsed(self):
        soul = Soul(**_minimal_soul_dict())
        assert len(soul.memory_ethics) >= 1


# ===================================================================
# Loading from real soul/ directory
# ===================================================================

class TestRealSoulDirectory:

    def test_load_real_soul_v1(self):
        """Load the actual soul.yaml shipped with the repo."""
        real_soul_dir = str(Path(__file__).parent.parent / "soul")
        soul = load_active_soul(real_soul_dir)
        assert soul.version == "v1"
        assert soul.mission
        assert soul.allegiance

    def test_real_soul_has_risk_rules(self):
        real_soul_dir = str(Path(__file__).parent.parent / "soul")
        soul = load_active_soul(real_soul_dir)
        assert len(soul.risk_rules) >= 1

    def test_real_active_pointer(self):
        real_soul_dir = str(Path(__file__).parent.parent / "soul")
        version = get_active_version(real_soul_dir)
        assert version == "v1"

    def test_real_versions_list(self):
        real_soul_dir = str(Path(__file__).parent.parent / "soul")
        versions = list_versions(real_soul_dir)
        assert "v1" in versions


# ===================================================================
# get_active_version
# ===================================================================

class TestGetActiveVersion:

    def test_reads_active_pointer(self, tmp_path):
        soul_dir = _write_soul_dir(tmp_path, active="v1\n")
        assert get_active_version(soul_dir) == "v1"

    def test_falls_back_to_latest_when_no_pointer(self, tmp_path):
        versions = {
            "v1": _minimal_soul_dict(version="v1"),
            "v2": _minimal_soul_dict(version="v2"),
        }
        soul_dir = _write_soul_dir(tmp_path, versions=versions, active=None)
        # Remove ACTIVE file if created
        active_file = Path(soul_dir) / "ACTIVE"
        if active_file.exists():
            active_file.unlink()
        assert get_active_version(soul_dir) == "v2"

    def test_empty_active_file_falls_back(self, tmp_path):
        soul_dir = _write_soul_dir(tmp_path, active="")
        version = get_active_version(soul_dir)
        assert version == "v1"

    def test_no_versions_raises(self, tmp_path):
        soul_dir = tmp_path / "soul"
        versions_dir = soul_dir / "soul_versions"
        versions_dir.mkdir(parents=True)
        with pytest.raises(SoulStoreError, match="No ACTIVE"):
            get_active_version(str(soul_dir))


# ===================================================================
# list_versions
# ===================================================================

class TestListVersions:

    def test_empty_directory(self, tmp_path):
        soul_dir = tmp_path / "soul"
        (soul_dir / "soul_versions").mkdir(parents=True)
        assert list_versions(str(soul_dir)) == []

    def test_no_versions_directory(self, tmp_path):
        soul_dir = tmp_path / "soul"
        soul_dir.mkdir()
        assert list_versions(str(soul_dir)) == []

    def test_single_version(self, tmp_path):
        soul_dir = _write_soul_dir(tmp_path)
        assert list_versions(soul_dir) == ["v1"]

    def test_multiple_versions_sorted(self, tmp_path):
        versions = {
            "v1": _minimal_soul_dict(version="v1"),
            "v3": _minimal_soul_dict(version="v3"),
            "v2": _minimal_soul_dict(version="v2"),
        }
        soul_dir = _write_soul_dir(tmp_path, versions=versions, active="v1\n")
        result = list_versions(soul_dir)
        assert result == ["v1", "v2", "v3"]

    def test_ignores_non_soul_files(self, tmp_path):
        soul_dir = _write_soul_dir(tmp_path)
        # Add a non-matching file
        (Path(soul_dir) / "soul_versions" / "notes.txt").write_text("ignore me")
        assert list_versions(soul_dir) == ["v1"]


# ===================================================================
# load_active_soul
# ===================================================================

class TestLoadActiveSoul:

    def test_loads_v1(self, tmp_path):
        soul_dir = _write_soul_dir(tmp_path, active="v1\n")
        soul = load_active_soul(soul_dir)
        assert soul.version == "v1"

    def test_loads_specific_version(self, tmp_path):
        versions = {
            "v1": _minimal_soul_dict(version="v1"),
            "v2": _minimal_soul_dict(version="v2", mission="Updated mission."),
        }
        soul_dir = _write_soul_dir(tmp_path, versions=versions, active="v2\n")
        soul = load_active_soul(soul_dir)
        assert soul.version == "v2"
        assert "Updated" in soul.mission

    def test_missing_version_file_raises(self, tmp_path):
        soul_dir = _write_soul_dir(tmp_path, active="v99\n")
        with pytest.raises(SoulStoreError, match="not found"):
            load_active_soul(soul_dir)

    def test_invalid_yaml_raises(self, tmp_path):
        soul_dir = _write_soul_dir(tmp_path, active="v1\n")
        # Corrupt the file
        version_file = Path(soul_dir) / "soul_versions" / "soul_v1.yaml"
        version_file.write_text("{{invalid: yaml: [", encoding="utf-8")
        with pytest.raises(SoulStoreError, match="Invalid YAML"):
            load_active_soul(soul_dir)

    def test_invalid_schema_raises(self, tmp_path):
        bad_soul = {"version": "v1", "mission": "", "allegiance": "ok",
                     "autonomy_posture": {"level": "x", "description": "x"}}
        soul_dir = _write_soul_dir(
            tmp_path,
            versions={"v1": bad_soul},
            active="v1\n",
        )
        with pytest.raises(SoulStoreError, match="validation failed"):
            load_active_soul(soul_dir)

    def test_soul_fields_populated(self, tmp_path):
        soul_dir = _write_soul_dir(tmp_path, active="v1\n")
        soul = load_active_soul(soul_dir)
        assert soul.autonomy_posture.level == "supervised"
        assert len(soul.approval_rules.channels) >= 1
        assert soul.scheduling_boundaries.require_ready_state is True
