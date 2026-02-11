"""
Tests for Prompt 56: SkillSecurityPipeline Orchestrator.
"""

import tempfile
import pytest
from pathlib import Path

from src.skills.security.static_analyzer import StaticAnalyzer
from src.skills.security.sandbox_tester import SandboxTester
from src.skills.security.capability_enforcer import CapabilityEnforcer
from src.skills.security.pipeline import SkillSecurityPipeline, PipelineResult


def _valid_manifest_dict(**overrides):
    defaults = {
        "id": "test-skill",
        "name": "Test Skill",
        "version": "1.0.0",
        "author": "test",
        "source": "first-party",
        "capabilities_required": [
            {"capability": "connector.read", "description": "Read data"},
        ],
        "target_domains": ["api.example.com"],
        "credentials": [
            {"vault_key": "test.token", "type": "bearer", "purpose": "API"},
        ],
    }
    defaults.update(overrides)
    return defaults


@pytest.fixture
def pipeline():
    return SkillSecurityPipeline(
        static_analyzer=StaticAnalyzer(),
        sandbox_tester=SandboxTester(),
        capability_enforcer=CapabilityEnforcer(),
    )


@pytest.fixture
def pipeline_with_trust():
    from src.core.governance.trust_models import TrustGraduationConfig
    from src.core.governance.trust_ledger import TrustLedger
    ledger = TrustLedger(TrustGraduationConfig())
    return SkillSecurityPipeline(
        static_analyzer=StaticAnalyzer(),
        sandbox_tester=SandboxTester(),
        capability_enforcer=CapabilityEnforcer(),
        trust_ledger=ledger,
    ), ledger


class TestCleanSkill:
    def test_passes_all_stages(self, pipeline):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "main.py").write_text("x = 1\nprint(x)\n")
            result = pipeline.evaluate(Path(tmpdir), _valid_manifest_dict())
            assert result.passed is True
            assert "manifest" in result.stage_results
            assert "static_analysis" in result.stage_results
            assert "sandbox_test" in result.stage_results


class TestInvalidManifest:
    def test_fails_at_stage_1(self, pipeline):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "main.py").write_text("x = 1\n")
            result = pipeline.evaluate(Path(tmpdir), {"id": "", "name": "x"})
            assert result.passed is False
            assert result.failed_at_stage == "manifest"


class TestCriticalStaticFinding:
    def test_fails_at_stage_2(self, pipeline):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "main.py").write_text("import requests\n")
            result = pipeline.evaluate(Path(tmpdir), _valid_manifest_dict())
            assert result.passed is False
            assert result.failed_at_stage == "static_analysis"


class TestStageResults:
    def test_has_all_stage_outputs(self, pipeline):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "main.py").write_text("x = 1\n")
            result = pipeline.evaluate(Path(tmpdir), _valid_manifest_dict())
            assert "manifest" in result.stage_results
            assert "static_analysis" in result.stage_results
            assert "sandbox_test" in result.stage_results
            assert "owner_review" in result.stage_results


class TestApproveAndInstall:
    def test_registers_in_enforcer(self, pipeline):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "main.py").write_text("x = 1\n")
            result = pipeline.evaluate(Path(tmpdir), _valid_manifest_dict())
            assert result.passed is True

            ok = pipeline.approve_and_install(result, ["connector.read"])
            assert ok is True

    def test_enforcer_allows_after_install(self, pipeline):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "main.py").write_text("x = 1\n")
            result = pipeline.evaluate(Path(tmpdir), _valid_manifest_dict())
            pipeline.approve_and_install(result, ["connector.read"])

            check = pipeline._capability_enforcer.enforce("test-skill", "connector.read")
            assert check.allowed is True

    def test_enforcer_blocks_unapproved(self, pipeline):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "main.py").write_text("x = 1\n")
            result = pipeline.evaluate(Path(tmpdir), _valid_manifest_dict())
            pipeline.approve_and_install(result, ["connector.read"])

            check = pipeline._capability_enforcer.enforce("test-skill", "connector.write")
            assert check.allowed is False


class TestUninstall:
    def test_removes_from_enforcer(self, pipeline):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "main.py").write_text("x = 1\n")
            result = pipeline.evaluate(Path(tmpdir), _valid_manifest_dict())
            pipeline.approve_and_install(result, ["connector.read"])
            pipeline.uninstall("test-skill")

            check = pipeline._capability_enforcer.enforce("test-skill", "connector.read")
            assert check.allowed is False


class TestTrustInitialization:
    def test_trust_records_created(self, pipeline_with_trust):
        pipeline, ledger = pipeline_with_trust
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "main.py").write_text("x = 1\n")
            result = pipeline.evaluate(Path(tmpdir), _valid_manifest_dict())
            pipeline.approve_and_install(result, ["connector.read"])

            records = ledger.list_records()
            assert len(records) >= 1
            assert any("test-skill" in r.capability for r in records)
