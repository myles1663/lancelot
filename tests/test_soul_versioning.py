"""
Tests for Soul versioning and activation pointer (Prompt 3 / A3).
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
    set_active_version,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _soul_dict(version="v1", **overrides) -> dict:
    """Return a valid soul dictionary that passes the linter."""
    base = {
        "version": version,
        "mission": "Serve the owner faithfully.",
        "allegiance": "Single owner loyalty.",
        "autonomy_posture": {
            "level": "supervised",
            "description": "Supervised autonomy.",
            "allowed_autonomous": ["classify_intent"],
            "requires_approval": ["deploy", "delete"],
        },
        "risk_rules": [
            {"name": "destructive_actions_require_approval",
             "description": "Destructive actions need approval", "enforced": True},
        ],
        "approval_rules": {
            "default_timeout_seconds": 3600,
            "escalation_on_timeout": "skip_and_log",
            "channels": ["war_room"],
        },
        "tone_invariants": [
            "Never mislead the owner",
            "Never suppress errors or degrade silently",
        ],
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
        versions = {"v1": _soul_dict("v1")}

    for ver, data in versions.items():
        path = versions_dir / f"soul_{ver}.yaml"
        path.write_text(yaml.dump(data), encoding="utf-8")

    if active is not None:
        (soul_dir / "ACTIVE").write_text(active, encoding="utf-8")

    return str(soul_dir)


# ===================================================================
# set_active_version
# ===================================================================

class TestSetActiveVersion:

    def test_set_writes_active_file(self, tmp_path):
        versions = {
            "v1": _soul_dict("v1"),
            "v2": _soul_dict("v2", mission="Updated mission."),
        }
        soul_dir = _write_soul_dir(tmp_path, versions=versions, active="v1")
        set_active_version("v2", soul_dir)
        assert get_active_version(soul_dir) == "v2"

    def test_set_to_nonexistent_version_raises(self, tmp_path):
        soul_dir = _write_soul_dir(tmp_path, active="v1")
        with pytest.raises(SoulStoreError, match="not found"):
            set_active_version("v99", soul_dir)

    def test_set_does_not_corrupt_directory(self, tmp_path):
        versions = {
            "v1": _soul_dict("v1"),
            "v2": _soul_dict("v2"),
        }
        soul_dir = _write_soul_dir(tmp_path, versions=versions, active="v1")
        set_active_version("v2", soul_dir)
        # Versions list should be unchanged
        assert list_versions(soul_dir) == ["v1", "v2"]


# ===================================================================
# Switching ACTIVE changes loaded soul
# ===================================================================

class TestActiveSwitching:

    def test_switching_active_changes_loaded_soul(self, tmp_path):
        """Core requirement: switching ACTIVE changes which soul loads."""
        versions = {
            "v1": _soul_dict("v1", mission="Original mission."),
            "v2": _soul_dict("v2", mission="Updated mission for v2."),
        }
        soul_dir = _write_soul_dir(tmp_path, versions=versions, active="v1")

        soul_v1 = load_active_soul(soul_dir)
        assert soul_v1.version == "v1"
        assert "Original" in soul_v1.mission

        set_active_version("v2", soul_dir)

        soul_v2 = load_active_soul(soul_dir)
        assert soul_v2.version == "v2"
        assert "Updated" in soul_v2.mission

    def test_switching_back_to_v1(self, tmp_path):
        versions = {
            "v1": _soul_dict("v1"),
            "v2": _soul_dict("v2"),
        }
        soul_dir = _write_soul_dir(tmp_path, versions=versions, active="v2")
        set_active_version("v1", soul_dir)
        soul = load_active_soul(soul_dir)
        assert soul.version == "v1"


# ===================================================================
# Missing ACTIVE defaults to latest
# ===================================================================

class TestMissingActiveDefaults:

    def test_no_active_defaults_to_latest(self, tmp_path):
        """Core requirement: missing ACTIVE defaults to latest valid version."""
        versions = {
            "v1": _soul_dict("v1"),
            "v2": _soul_dict("v2"),
            "v3": _soul_dict("v3"),
        }
        soul_dir = _write_soul_dir(tmp_path, versions=versions)
        # No ACTIVE file written
        assert get_active_version(soul_dir) == "v3"

    def test_no_active_loads_latest_soul(self, tmp_path):
        versions = {
            "v1": _soul_dict("v1"),
            "v2": _soul_dict("v2", mission="Latest soul mission."),
        }
        soul_dir = _write_soul_dir(tmp_path, versions=versions)
        soul = load_active_soul(soul_dir)
        assert soul.version == "v2"
        assert "Latest" in soul.mission


# ===================================================================
# Multi-version scenarios
# ===================================================================

class TestMultiVersion:

    def test_three_versions_list_sorted(self, tmp_path):
        versions = {
            "v3": _soul_dict("v3"),
            "v1": _soul_dict("v1"),
            "v2": _soul_dict("v2"),
        }
        soul_dir = _write_soul_dir(tmp_path, versions=versions, active="v1")
        assert list_versions(soul_dir) == ["v1", "v2", "v3"]

    def test_load_each_version_by_switching(self, tmp_path):
        versions = {
            "v1": _soul_dict("v1", mission="Mission alpha."),
            "v2": _soul_dict("v2", mission="Mission beta."),
            "v3": _soul_dict("v3", mission="Mission gamma."),
        }
        soul_dir = _write_soul_dir(tmp_path, versions=versions, active="v1")

        for ver, expected_word in [("v1", "alpha"), ("v2", "beta"), ("v3", "gamma")]:
            set_active_version(ver, soul_dir)
            soul = load_active_soul(soul_dir)
            assert soul.version == ver
            assert expected_word in soul.mission

    def test_active_pointer_persists_across_loads(self, tmp_path):
        versions = {"v1": _soul_dict("v1"), "v2": _soul_dict("v2")}
        soul_dir = _write_soul_dir(tmp_path, versions=versions, active="v2")
        # Load twice — should both return v2
        s1 = load_active_soul(soul_dir)
        s2 = load_active_soul(soul_dir)
        assert s1.version == s2.version == "v2"


# ===================================================================
# soul_loaded log receipt
# ===================================================================

class TestSoulLoadedReceipt:

    def test_soul_loaded_logged(self, tmp_path, caplog):
        """load_active_soul emits a soul_loaded log event."""
        import logging
        soul_dir = _write_soul_dir(tmp_path, active="v1")
        with caplog.at_level(logging.INFO, logger="src.core.soul.store"):
            load_active_soul(soul_dir)
        assert any("soul_loaded" in r.message for r in caplog.records)
