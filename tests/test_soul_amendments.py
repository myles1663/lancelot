"""
Tests for src.core.soul.amendments — Soul amendment proposals (Prompt 4 / A4).
"""

import json
import pytest
import yaml
from pathlib import Path

from src.core.soul.store import SoulStoreError
from src.core.soul.amendments import (
    SoulAmendmentProposal,
    ProposalStatus,
    create_proposal,
    compute_yaml_diff,
    list_proposals,
    get_proposal,
    save_proposals,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _soul_dict(version="v1", **overrides) -> dict:
    """Return a valid soul dictionary."""
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
    """Create a soul directory with version files."""
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
# compute_yaml_diff
# ===================================================================

class TestComputeYamlDiff:

    def test_identical_dicts_no_changes(self):
        d = _soul_dict()
        assert compute_yaml_diff(d, d) == []

    def test_changed_field_detected(self):
        old = _soul_dict(mission="Old mission.")
        new = _soul_dict(mission="New mission.")
        diff = compute_yaml_diff(old, new)
        assert "changed: mission" in diff

    def test_added_field_detected(self):
        old = {"version": "v1"}
        new = {"version": "v1", "new_field": "value"}
        diff = compute_yaml_diff(old, new)
        assert "added: new_field" in diff

    def test_removed_field_detected(self):
        old = {"version": "v1", "old_field": "value"}
        new = {"version": "v1"}
        diff = compute_yaml_diff(old, new)
        assert "removed: old_field" in diff

    def test_nested_change_detected(self):
        old = _soul_dict()
        new = _soul_dict()
        new["autonomy_posture"]["level"] = "autonomous"
        diff = compute_yaml_diff(old, new)
        assert "changed: autonomy_posture.level" in diff

    def test_multiple_changes(self):
        old = _soul_dict(mission="Old.", allegiance="Old allegiance.")
        new = _soul_dict(mission="New.", allegiance="New allegiance.")
        diff = compute_yaml_diff(old, new)
        assert "changed: mission" in diff
        assert "changed: allegiance" in diff

    def test_diff_includes_expected_changed_keys(self):
        """Blueprint requirement: diff includes expected changed keys."""
        old = _soul_dict(version="v1")
        new = _soul_dict(version="v2", mission="Upgraded mission.")
        diff = compute_yaml_diff(old, new)
        assert any("version" in d for d in diff)
        assert any("mission" in d for d in diff)


# ===================================================================
# create_proposal
# ===================================================================

class TestCreateProposal:

    def test_proposal_created_and_persisted(self, tmp_path):
        """Blueprint requirement: proposal created and persisted."""
        soul_dir = _write_soul_dir(tmp_path, active="v1")

        proposed = _soul_dict("v2", mission="New mission for v2.")
        proposed_yaml = yaml.dump(proposed)

        proposal = create_proposal("v1", proposed_yaml, author="owner", soul_dir=soul_dir)

        assert proposal.proposed_version == "v2"
        assert proposal.author == "owner"
        assert proposal.status == ProposalStatus.PENDING
        assert len(proposal.diff_summary) > 0

        # Verify persistence
        loaded = list_proposals(soul_dir)
        assert len(loaded) == 1
        assert loaded[0].id == proposal.id

    def test_proposal_has_id(self, tmp_path):
        soul_dir = _write_soul_dir(tmp_path, active="v1")
        proposed_yaml = yaml.dump(_soul_dict("v2"))
        proposal = create_proposal("v1", proposed_yaml, soul_dir=soul_dir)
        assert proposal.id
        assert len(proposal.id) == 12

    def test_proposal_has_created_at(self, tmp_path):
        soul_dir = _write_soul_dir(tmp_path, active="v1")
        proposed_yaml = yaml.dump(_soul_dict("v2"))
        proposal = create_proposal("v1", proposed_yaml, soul_dir=soul_dir)
        assert proposal.created_at

    def test_proposal_stores_yaml(self, tmp_path):
        soul_dir = _write_soul_dir(tmp_path, active="v1")
        proposed_yaml = yaml.dump(_soul_dict("v2"))
        proposal = create_proposal("v1", proposed_yaml, soul_dir=soul_dir)
        assert proposal.proposed_yaml == proposed_yaml

    def test_invalid_yaml_raises(self, tmp_path):
        soul_dir = _write_soul_dir(tmp_path, active="v1")
        with pytest.raises(SoulStoreError, match="Invalid YAML"):
            create_proposal("v1", "{{bad yaml: [", soul_dir=soul_dir)

    def test_missing_base_version_raises(self, tmp_path):
        soul_dir = _write_soul_dir(tmp_path, active="v1")
        proposed_yaml = yaml.dump(_soul_dict("v99"))
        with pytest.raises(SoulStoreError, match="not found"):
            create_proposal("v99", proposed_yaml, soul_dir=soul_dir)

    def test_multiple_proposals_appended(self, tmp_path):
        soul_dir = _write_soul_dir(tmp_path, active="v1")
        p1 = create_proposal("v1", yaml.dump(_soul_dict("v2")), soul_dir=soul_dir)
        p2 = create_proposal("v1", yaml.dump(_soul_dict("v3")), soul_dir=soul_dir)
        loaded = list_proposals(soul_dir)
        assert len(loaded) == 2
        assert loaded[0].id == p1.id
        assert loaded[1].id == p2.id


# ===================================================================
# list_proposals / get_proposal
# ===================================================================

class TestListAndGetProposals:

    def test_empty_returns_empty_list(self, tmp_path):
        soul_dir = _write_soul_dir(tmp_path, active="v1")
        assert list_proposals(soul_dir) == []

    def test_get_proposal_found(self, tmp_path):
        soul_dir = _write_soul_dir(tmp_path, active="v1")
        p = create_proposal("v1", yaml.dump(_soul_dict("v2")), soul_dir=soul_dir)
        found = get_proposal(p.id, soul_dir)
        assert found is not None
        assert found.id == p.id

    def test_get_proposal_not_found(self, tmp_path):
        soul_dir = _write_soul_dir(tmp_path, active="v1")
        assert get_proposal("nonexistent", soul_dir) is None


# ===================================================================
# Persistence file
# ===================================================================

class TestPersistence:

    def test_proposals_stored_in_data_dir(self, tmp_path):
        soul_dir = _write_soul_dir(tmp_path, active="v1")
        create_proposal("v1", yaml.dump(_soul_dict("v2")), soul_dir=soul_dir)
        data_file = tmp_path / "data" / "soul_proposals.json"
        assert data_file.exists()

    def test_proposals_file_is_valid_json(self, tmp_path):
        soul_dir = _write_soul_dir(tmp_path, active="v1")
        create_proposal("v1", yaml.dump(_soul_dict("v2")), soul_dir=soul_dir)
        data_file = tmp_path / "data" / "soul_proposals.json"
        data = json.loads(data_file.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) == 1

    def test_corrupted_file_returns_empty(self, tmp_path):
        soul_dir = _write_soul_dir(tmp_path, active="v1")
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "soul_proposals.json").write_text("not json!", encoding="utf-8")
        assert list_proposals(soul_dir) == []


# ===================================================================
# SoulAmendmentProposal model
# ===================================================================

class TestProposalModel:

    def test_default_status_is_pending(self):
        p = SoulAmendmentProposal(proposed_version="v2")
        assert p.status == ProposalStatus.PENDING

    def test_status_values(self):
        assert ProposalStatus.PENDING == "pending"
        assert ProposalStatus.APPROVED == "approved"
        assert ProposalStatus.ACTIVATED == "activated"
        assert ProposalStatus.REJECTED == "rejected"
