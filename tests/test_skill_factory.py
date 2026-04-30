"""Tests for the governed SkillFactory proposal pipeline."""

from pathlib import Path

import pytest

from src.core.skills.factory import ProposalStatus, SkillFactory
from src.core.skills.registry import SkillRegistry
from src.core.skills.schema import SkillError


@pytest.fixture
def factory(tmp_path):
    return SkillFactory(data_dir=str(tmp_path / "data"))


@pytest.fixture
def registry(tmp_path):
    return SkillRegistry(str(tmp_path / "registry"))


class TestGovernedProposalCreation:
    def test_new_proposal_creates_artifact_package(self, factory):
        proposal = factory.generate_skeleton("demo_skill", "Demo skill", permissions=["read_input", "write_output"])

        assert proposal.status == ProposalStatus.PENDING
        assert proposal.pipeline_passed is True
        assert proposal.review_ready is True
        assert "tool.read_input" in proposal.approved_capabilities
        assert "tool.write_output" in proposal.approved_capabilities
        assert "capabilities_required:" in proposal.security_manifest_yaml

        artifact_dir = Path(proposal.artifact_dir)
        assert artifact_dir.exists()
        assert (artifact_dir / "skill.yaml").exists()
        assert (artifact_dir / "security_manifest.yaml").exists()
        assert (artifact_dir / "execute.py").exists()
        assert (artifact_dir / "README.md").exists()
        assert proposal.artifact_hashes["skill.yaml"]

    def test_create_proposal_with_real_code_persists_metadata(self, factory):
        proposal = factory.create_proposal(
            name="trim_input",
            description="Normalize whitespace",
            permissions=["read_input", "write_output"],
            execute_code=(
                'def execute(context, inputs):\n'
                '    value = str(inputs.get("input_data", "")).strip()\n'
                '    return {"result": value}\n'
            ),
            target_domains=["api.example.com"],
            credentials=[{"vault_key": "service.token", "type": "bearer", "purpose": "API access"}],
            author="Arthur",
        )

        loaded = factory.get_proposal(proposal.id)
        assert loaded is not None
        assert loaded.author == "Arthur"
        assert loaded.target_domains == ["api.example.com"]
        assert loaded.credential_keys == ["service.token"]
        assert loaded.pipeline_stage_results["owner_review"]["status"] == "pending"

    def test_dangerous_code_is_blocked_by_real_pipeline(self, factory):
        proposal = factory.create_proposal(
            name="network_probe",
            permissions=["network_fetch"],
            execute_code=(
                "import requests\n\n"
                "def execute(context, inputs):\n"
                '    return {"result": requests.get("https://example.com").text}\n'
            ),
        )

        assert proposal.status == ProposalStatus.REVIEW_FAILED
        assert proposal.pipeline_passed is False
        assert proposal.pipeline_failed_at_stage == "static_analysis"


class TestApprovalAndInstallation:
    def test_pending_proposal_cannot_install(self, factory, registry):
        proposal = factory.generate_skeleton("pending_skill")
        with pytest.raises(SkillError, match="approved"):
            factory.install_proposal(proposal.id, registry)

    def test_review_failed_proposal_cannot_approve(self, factory):
        proposal = factory.create_proposal(
            name="bad_skill",
            permissions=["network_fetch"],
            execute_code="import requests\n",
        )

        with pytest.raises(SkillError, match="expected 'pending'"):
            factory.approve_proposal(proposal.id)

    def test_approve_then_install_uses_governed_package(self, factory, registry, tmp_path):
        proposal = factory.create_proposal(
            name="echo_v2",
            description="Echo input for validation",
            permissions=["read_input", "write_output"],
            execute_code=(
                'def execute(context, inputs):\n'
                '    value = inputs.get("input_data", "")\n'
                '    return {"result": value, "skill": "echo_v2"}\n'
            ),
        )

        approved = factory.approve_proposal(proposal.id, approved_by="owner")
        entry = factory.install_proposal(approved.id, registry, install_dir=str(tmp_path / "skills"))

        assert entry.name == "echo_v2"
        assert entry.enabled is True

        loaded = factory.get_proposal(proposal.id)
        assert loaded is not None
        assert loaded.status == ProposalStatus.INSTALLED
        assert loaded.approved_by == "owner"
        assert loaded.approved_at is not None
        assert loaded.installed_at is not None

    def test_artifact_tamper_blocks_install(self, factory, registry):
        proposal = factory.generate_skeleton("tamper_check")
        factory.approve_proposal(proposal.id, approved_by="owner")

        artifact_dir = Path(proposal.artifact_dir)
        (artifact_dir / "execute.py").write_text("def execute(context, inputs):\n    return {'result': 'tampered'}\n", encoding="utf-8")

        with pytest.raises(SkillError, match="changed after review"):
            factory.install_proposal(proposal.id, registry)


class TestPersistenceAndReviewMetadata:
    def test_reject_records_reason(self, factory):
        proposal = factory.generate_skeleton("reject_me")
        rejected = factory.reject_proposal(proposal.id, reason="Too much privilege")

        assert rejected.status == ProposalStatus.REJECTED
        assert rejected.rejected_reason == "Too much privilege"
        assert rejected.rejected_at is not None

    def test_get_nonexistent_returns_none(self, factory):
        assert factory.get_proposal("missing") is None
