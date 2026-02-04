"""
Tests for src.core.skills.factory — Skill Factory proposals (Prompt 15 / F1-F4).
"""

import pytest
from pathlib import Path

from src.core.skills.schema import SkillError
from src.core.skills.registry import SkillRegistry
from src.core.skills.factory import (
    SkillFactory,
    SkillProposal,
    ProposalStatus,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def factory(tmp_path):
    return SkillFactory(data_dir=str(tmp_path / "data"))


@pytest.fixture
def registry(tmp_path):
    return SkillRegistry(str(tmp_path / "registry_data"))


# ===================================================================
# Proposal cannot auto-enable itself
# ===================================================================

class TestProposalCannotAutoEnable:

    def test_new_proposal_is_pending(self, factory):
        """Blueprint requirement: proposal cannot auto-enable itself."""
        proposal = factory.generate_skeleton("my_skill", "A test skill")
        assert proposal.status == ProposalStatus.PENDING

    def test_install_pending_raises(self, factory, registry):
        """Cannot install a proposal that hasn't been approved."""
        proposal = factory.generate_skeleton("my_skill")
        with pytest.raises(SkillError, match="approved"):
            factory.install_proposal(proposal.id, registry)

    def test_install_rejected_raises(self, factory, registry):
        proposal = factory.generate_skeleton("my_skill")
        factory.reject_proposal(proposal.id)
        with pytest.raises(SkillError, match="approved"):
            factory.install_proposal(proposal.id, registry)


# ===================================================================
# Approval required for installation
# ===================================================================

class TestApprovalRequired:

    def test_approve_then_install(self, factory, registry, tmp_path):
        """Blueprint requirement: approval required for installation."""
        proposal = factory.generate_skeleton("echo_v2", "Echo skill v2",
                                              permissions=["read_input"])
        factory.approve_proposal(proposal.id, approved_by="owner")

        install_dir = str(tmp_path / "skills")
        entry = factory.install_proposal(proposal.id, registry, install_dir)
        assert entry.name == "echo_v2"
        assert entry.enabled is True

    def test_proposal_marked_installed_after(self, factory, registry, tmp_path):
        proposal = factory.generate_skeleton("my_skill", permissions=["read_input"])
        factory.approve_proposal(proposal.id)
        factory.install_proposal(proposal.id, registry, str(tmp_path / "skills"))

        loaded = factory.get_proposal(proposal.id)
        assert loaded.status == ProposalStatus.INSTALLED


# ===================================================================
# Skeleton generation
# ===================================================================

class TestSkeletonGeneration:

    def test_generates_manifest_yaml(self, factory):
        proposal = factory.generate_skeleton("test_skill", "Test")
        assert "name: test_skill" in proposal.manifest_yaml

    def test_generates_execute_code(self, factory):
        proposal = factory.generate_skeleton("test_skill")
        assert "def execute" in proposal.execute_code

    def test_generates_test_code(self, factory):
        proposal = factory.generate_skeleton("test_skill")
        assert "def test_" in proposal.test_code

    def test_permissions_in_manifest(self, factory):
        proposal = factory.generate_skeleton("test_skill",
                                              permissions=["read_input", "write_output"])
        assert "read_input" in proposal.manifest_yaml
        assert "write_output" in proposal.manifest_yaml

    def test_proposal_has_id(self, factory):
        proposal = factory.generate_skeleton("test_skill")
        assert proposal.id
        assert len(proposal.id) == 12

    def test_tests_status_not_run(self, factory):
        proposal = factory.generate_skeleton("test_skill")
        assert proposal.tests_status == "not_run"


# ===================================================================
# Persistence
# ===================================================================

class TestPersistence:

    def test_proposals_persisted(self, factory):
        factory.generate_skeleton("skill_one")
        factory.generate_skeleton("skill_two")
        proposals = factory.list_proposals()
        assert len(proposals) == 2

    def test_proposal_retrieved_by_id(self, factory):
        p = factory.generate_skeleton("test_skill")
        loaded = factory.get_proposal(p.id)
        assert loaded is not None
        assert loaded.name == "test_skill"

    def test_get_nonexistent_returns_none(self, factory):
        assert factory.get_proposal("nonexistent") is None


# ===================================================================
# Approve / Reject
# ===================================================================

class TestApproveReject:

    def test_approve_sets_status(self, factory):
        p = factory.generate_skeleton("test_skill")
        factory.approve_proposal(p.id, approved_by="owner")
        loaded = factory.get_proposal(p.id)
        assert loaded.status == ProposalStatus.APPROVED
        assert loaded.approved_by == "owner"

    def test_approve_nonexistent_raises(self, factory):
        with pytest.raises(SkillError, match="not found"):
            factory.approve_proposal("fake_id")

    def test_approve_already_approved_raises(self, factory):
        p = factory.generate_skeleton("test_skill")
        factory.approve_proposal(p.id)
        with pytest.raises(SkillError, match="pending"):
            factory.approve_proposal(p.id)

    def test_reject_sets_status(self, factory):
        p = factory.generate_skeleton("test_skill")
        factory.reject_proposal(p.id)
        loaded = factory.get_proposal(p.id)
        assert loaded.status == ProposalStatus.REJECTED
