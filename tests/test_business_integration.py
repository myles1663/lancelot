"""
Tests for Prompt 64: Business Dashboard + End-to-End Integration.

Capstone test exercising: connectors, trust ledger, skill security pipeline,
risk classifier, receipt system, Soul enforcement, and War Room display.
"""

import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch

from src.core.governance.config import RiskClassificationConfig
from src.core.governance.models import RiskTier
from src.core.governance.risk_classifier import RiskClassifier
from src.core.governance.trust_models import (
    TrustGraduationConfig,
    TrustGraduationThresholds,
    TrustRevocationConfig,
)
from src.core.governance.trust_ledger import TrustLedger

from src.skills.security.static_analyzer import StaticAnalyzer
from src.skills.security.sandbox_tester import SandboxTester
from src.skills.security.capability_enforcer import CapabilityEnforcer
from src.skills.security.pipeline import SkillSecurityPipeline

from src.business.skills.content_intake import ContentIntakeSkill, CONTENT_INTAKE_MANIFEST
from src.business.skills.content_repurpose import ContentRepurposeSkill
from src.business.skills.quality_verify import QualityVerifySkill
from src.business.skills.delivery import DeliverySkill

from src.business.soul_config import BUSINESS_SOUL_CONFIG
from src.business.war_room_business import render_business_panel


SAMPLE_CONTENT = """# How AI is Transforming Content Creation

Artificial intelligence is rapidly changing how businesses create and distribute content.
From automated writing assistants to sophisticated content analysis tools, AI is becoming
an essential part of the modern content workflow.

The key benefits include faster production times, more consistent quality, and the ability
to personalize content at scale. Companies that adopt AI-powered tools are seeing significant
improvements in their content marketing metrics.

However, it's important to remember that AI works best when combined with human creativity
and editorial judgment. The most successful approaches use AI to handle repetitive tasks
while humans focus on strategy and storytelling.

Looking ahead, we can expect even more advanced AI capabilities in content creation. Natural
language processing continues to improve, and new tools are making it easier than ever to
repurpose content across multiple platforms and formats.

The future of content is collaborative — humans and AI working together to create better,
more engaging experiences for audiences everywhere.
"""


@pytest.fixture
def trust_config():
    return TrustGraduationConfig(
        thresholds=TrustGraduationThresholds(T3_to_T2=50, T2_to_T1=100, T1_to_T0=200),
        revocation=TrustRevocationConfig(
            on_failure="reset_to_default",
            on_rollback="reset_above_default",
            cooldown_after_denial=50,
            cooldown_after_revocation=25,
        ),
    )


@pytest.fixture
def trust_ledger(trust_config):
    return TrustLedger(trust_config)


@pytest.fixture
def risk_config():
    return RiskClassificationConfig(defaults={
        "connector.email.send_message": 3,
        "connector.email.list_messages": 1,
        "connector.slack.post_message": 2,
        "connector.stripe.create_charge": 3,
    })


@pytest.fixture
def pipeline(trust_ledger):
    return SkillSecurityPipeline(
        static_analyzer=StaticAnalyzer(),
        sandbox_tester=SandboxTester(),
        capability_enforcer=CapabilityEnforcer(),
        trust_ledger=trust_ledger,
    )


# ── Full Pipeline Test ───────────────────────────────────────────

class TestFullPipeline:
    def test_install_skill_through_pipeline(self, pipeline):
        """Install content intake skill through 6-stage pipeline."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "content_intake.py").write_text(
                "class ContentIntakeSkill:\n    pass\n"
            )
            result = pipeline.evaluate(Path(tmpdir), CONTENT_INTAKE_MANIFEST)
            assert result.passed is True
            ok = pipeline.approve_and_install(result, ["connector.read"])
            assert ok is True

    def test_full_content_pipeline(self):
        """Process sample content: intake → repurpose → verify → delivery."""
        intake = ContentIntakeSkill()
        repurposer = ContentRepurposeSkill()
        quality_checker = QualityVerifySkill()
        delivery = DeliverySkill()

        # Intake
        parsed = intake.parse_content(SAMPLE_CONTENT)
        valid, issues = intake.validate_content(parsed)
        assert valid is True

        # Repurpose
        repurposed = repurposer.repurpose_all(parsed)
        assert len(repurposed["tweets"]) > 0
        assert len(repurposed["linkedin"]) > 0

        # Quality
        quality = quality_checker.verify_all(repurposed)
        assert isinstance(quality.score, float)

        # Delivery
        email_pkg = delivery.format_email_package(
            "client@example.com", repurposed, quality
        )
        assert email_pkg["to"] == "client@example.com"
        assert len(email_pkg["body"]) > 0

    def test_trust_records_created(self, pipeline, trust_ledger):
        """Verify trust records created during install."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "main.py").write_text("x = 1\n")
            result = pipeline.evaluate(Path(tmpdir), CONTENT_INTAKE_MANIFEST)
            pipeline.approve_and_install(result, ["connector.read"])
            records = trust_ledger.list_records()
            assert len(records) >= 1


# ── Trust Graduation Simulation ──────────────────────────────────

class TestTrustGraduation:
    @patch("src.core.feature_flags.FEATURE_TRUST_LEDGER", True)
    def test_email_graduation_and_revocation(self, trust_ledger, risk_config):
        """Simulate 50 email sends → graduation → failure → revocation."""
        cap = "connector.email.send_message"
        scope = "external"
        trust_ledger.get_or_create_record(cap, scope, RiskTier.T3_IRREVERSIBLE)

        # 50 successes → T3→T2 proposal
        for _ in range(50):
            trust_ledger.record_success(cap, scope)
        proposals = trust_ledger.pending_proposals()
        assert len(proposals) == 1

        # Approve graduation
        trust_ledger.apply_graduation(proposals[0].id, approved=True)
        rec = trust_ledger.get_record(cap, scope)
        assert rec.current_tier == RiskTier.T2_CONTROLLED

        # Classifier should now return T2
        classifier = RiskClassifier(risk_config, trust_ledger=trust_ledger)
        profile = classifier.classify(cap, scope=scope)
        assert profile.tier == RiskTier.T2_CONTROLLED

        # Failure → revocation back to T3
        trust_ledger.record_failure(cap, scope)
        assert rec.current_tier == RiskTier.T3_IRREVERSIBLE


# ── Soul Enforcement ─────────────────────────────────────────────

class TestSoulEnforcement:
    def test_stripe_always_t3(self, risk_config):
        """Stripe write operations stay at T3 regardless of trust."""
        classifier = RiskClassifier(risk_config)
        profile = classifier.classify("connector.stripe.create_charge")
        assert profile.tier == RiskTier.T3_IRREVERSIBLE

    @patch("src.core.feature_flags.FEATURE_TRUST_LEDGER", True)
    def test_email_non_verified_t3(self, trust_ledger, risk_config):
        """Email to non-verified recipient stays at T3."""
        cap = "connector.email.send_message"
        scope = "external"
        trust_ledger.get_or_create_record(cap, scope, RiskTier.T3_IRREVERSIBLE)

        # Default is T3, trust record at T3 — should stay T3
        classifier = RiskClassifier(risk_config, trust_ledger=trust_ledger)
        profile = classifier.classify(cap, scope=scope)
        assert profile.tier == RiskTier.T3_IRREVERSIBLE


# ── Business Panel ───────────────────────────────────────────────

class TestBusinessPanel:
    def test_returns_correct_structure(self, trust_ledger):
        result = render_business_panel(trust_ledger=trust_ledger)
        assert "pipeline_status" in result
        assert "trust_status" in result
        assert "connector_health" in result
        assert "governance_efficiency" in result

    def test_governance_efficiency_tier_distribution(self, trust_ledger):
        """Efficiency shows correct tier distribution."""
        trust_ledger.get_or_create_record("connector.email.send", "s", RiskTier.T3_IRREVERSIBLE)
        trust_ledger.get_or_create_record("connector.email.read", "s", RiskTier.T0_INERT)
        trust_ledger.get_or_create_record("connector.slack.post", "s", RiskTier.T2_CONTROLLED)

        result = render_business_panel(trust_ledger=trust_ledger)
        eff = result["governance_efficiency"]
        # 3 records: T0, T2, T3
        assert eff["pct_at_T0"] > 0
        assert eff["pct_at_T2"] > 0
        assert eff["pct_at_T3"] > 0
        # Total should be ~1.0
        total = sum(eff.values())
        assert abs(total - 1.0) < 0.01

    def test_empty_panel(self):
        result = render_business_panel()
        assert result["pipeline_status"]["intake"] == 0
        assert result["governance_efficiency"]["pct_at_T0"] == 0.0
