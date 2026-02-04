"""
Tests for src.core.soul.linter — Soul invariant checks (Prompt 2 / A2).
"""

import pytest
from pathlib import Path

from src.core.soul.store import Soul, SoulStoreError, load_active_soul
from src.core.soul.linter import (
    LintIssue,
    LintSeverity,
    lint,
    lint_or_raise,
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
            "Never suppress errors or degrade silently",
            "Report failures immediately",
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


# ===================================================================
# Canonical v1 passes linter
# ===================================================================

class TestCanonicalSoulPasses:

    def test_canonical_soul_zero_critical_issues(self):
        soul = Soul(**_minimal_soul_dict())
        issues = lint(soul)
        critical = [i for i in issues if i.severity == LintSeverity.CRITICAL]
        assert critical == [], f"Unexpected critical issues: {critical}"

    def test_lint_or_raise_does_not_raise_on_valid(self):
        soul = Soul(**_minimal_soul_dict())
        issues = lint_or_raise(soul)
        assert isinstance(issues, list)

    def test_real_soul_passes_linter(self):
        """The actual shipped soul.yaml must pass the linter."""
        real_soul_dir = str(Path(__file__).parent.parent / "soul")
        soul = load_active_soul(real_soul_dir)
        issues = lint(soul)
        critical = [i for i in issues if i.severity == LintSeverity.CRITICAL]
        assert critical == [], f"Real soul has critical issues: {critical}"


# ===================================================================
# destructive_actions_require_approval
# ===================================================================

class TestDestructiveActionsInvariant:

    def test_missing_destructive_keywords_is_critical(self):
        d = _minimal_soul_dict()
        d["autonomy_posture"]["requires_approval"] = ["summarize"]
        soul = Soul(**d)
        issues = lint(soul)
        rule_issues = [i for i in issues if i.rule == "destructive_actions_require_approval"]
        assert len(rule_issues) >= 1
        assert rule_issues[0].severity == LintSeverity.CRITICAL

    def test_has_delete_passes(self):
        d = _minimal_soul_dict()
        d["autonomy_posture"]["requires_approval"] = ["delete"]
        soul = Soul(**d)
        issues = lint(soul)
        destructive_critical = [
            i for i in issues
            if i.rule == "destructive_actions_require_approval"
            and i.severity == LintSeverity.CRITICAL
        ]
        assert destructive_critical == []

    def test_has_deploy_passes(self):
        d = _minimal_soul_dict()
        d["autonomy_posture"]["requires_approval"] = ["deploy"]
        soul = Soul(**d)
        issues = lint(soul)
        destructive_critical = [
            i for i in issues
            if i.rule == "destructive_actions_require_approval"
            and i.severity == LintSeverity.CRITICAL
        ]
        assert destructive_critical == []

    def test_missing_risk_rule_gives_warning(self):
        d = _minimal_soul_dict()
        d["risk_rules"] = []
        soul = Soul(**d)
        issues = lint(soul)
        warnings = [
            i for i in issues
            if i.rule == "destructive_actions_require_approval"
            and i.severity == LintSeverity.WARNING
        ]
        assert len(warnings) == 1


# ===================================================================
# no_silent_degradation
# ===================================================================

class TestNoSilentDegradation:

    def test_missing_tone_invariants_is_critical(self):
        d = _minimal_soul_dict()
        d["tone_invariants"] = ["Be polite"]
        soul = Soul(**d)
        issues = lint(soul)
        rule_issues = [i for i in issues if i.rule == "no_silent_degradation"]
        assert len(rule_issues) == 1
        assert rule_issues[0].severity == LintSeverity.CRITICAL

    def test_suppress_and_error_passes(self):
        d = _minimal_soul_dict()
        d["tone_invariants"] = ["Never suppress errors"]
        soul = Soul(**d)
        issues = lint(soul)
        rule_issues = [
            i for i in issues
            if i.rule == "no_silent_degradation"
            and i.severity == LintSeverity.CRITICAL
        ]
        assert rule_issues == []

    def test_silent_and_degrade_passes(self):
        d = _minimal_soul_dict()
        d["tone_invariants"] = ["Never degrade silently"]
        soul = Soul(**d)
        issues = lint(soul)
        rule_issues = [
            i for i in issues
            if i.rule == "no_silent_degradation"
            and i.severity == LintSeverity.CRITICAL
        ]
        assert rule_issues == []

    def test_failure_and_error_passes(self):
        d = _minimal_soul_dict()
        d["tone_invariants"] = ["Report failures and errors immediately"]
        soul = Soul(**d)
        issues = lint(soul)
        rule_issues = [
            i for i in issues
            if i.rule == "no_silent_degradation"
            and i.severity == LintSeverity.CRITICAL
        ]
        assert rule_issues == []


# ===================================================================
# scheduling_no_autonomous_irreversible
# ===================================================================

class TestSchedulingIrreversible:

    def test_false_is_critical(self):
        d = _minimal_soul_dict()
        d["scheduling_boundaries"]["no_autonomous_irreversible"] = False
        soul = Soul(**d)
        issues = lint(soul)
        rule_issues = [i for i in issues if i.rule == "scheduling_no_autonomous_irreversible"]
        assert len(rule_issues) == 1
        assert rule_issues[0].severity == LintSeverity.CRITICAL

    def test_true_passes(self):
        soul = Soul(**_minimal_soul_dict())
        issues = lint(soul)
        rule_issues = [i for i in issues if i.rule == "scheduling_no_autonomous_irreversible"]
        assert rule_issues == []


# ===================================================================
# approval_channels_required
# ===================================================================

class TestApprovalChannels:

    def test_empty_channels_is_critical(self):
        d = _minimal_soul_dict()
        d["approval_rules"]["channels"] = []
        soul = Soul(**d)
        issues = lint(soul)
        rule_issues = [i for i in issues if i.rule == "approval_channels_required"]
        assert len(rule_issues) == 1
        assert rule_issues[0].severity == LintSeverity.CRITICAL

    def test_has_channels_passes(self):
        soul = Soul(**_minimal_soul_dict())
        issues = lint(soul)
        rule_issues = [i for i in issues if i.rule == "approval_channels_required"]
        assert rule_issues == []


# ===================================================================
# memory_ethics_required
# ===================================================================

class TestMemoryEthics:

    def test_empty_memory_ethics_gives_warning(self):
        d = _minimal_soul_dict()
        d["memory_ethics"] = []
        soul = Soul(**d)
        issues = lint(soul)
        rule_issues = [i for i in issues if i.rule == "memory_ethics_required"]
        assert len(rule_issues) == 1
        assert rule_issues[0].severity == LintSeverity.WARNING

    def test_has_memory_ethics_passes(self):
        soul = Soul(**_minimal_soul_dict())
        issues = lint(soul)
        rule_issues = [i for i in issues if i.rule == "memory_ethics_required"]
        assert rule_issues == []


# ===================================================================
# lint_or_raise behaviour
# ===================================================================

class TestLintOrRaise:

    def test_raises_on_critical(self):
        d = _minimal_soul_dict()
        d["scheduling_boundaries"]["no_autonomous_irreversible"] = False
        soul = Soul(**d)
        with pytest.raises(SoulStoreError, match="lint failed"):
            lint_or_raise(soul)

    def test_does_not_raise_on_warnings_only(self):
        d = _minimal_soul_dict()
        d["risk_rules"] = []  # produces warning, not critical
        soul = Soul(**d)
        issues = lint_or_raise(soul)
        warnings = [i for i in issues if i.severity == LintSeverity.WARNING]
        assert len(warnings) >= 1

    def test_multiple_critical_all_reported(self):
        d = _minimal_soul_dict()
        d["scheduling_boundaries"]["no_autonomous_irreversible"] = False
        d["approval_rules"]["channels"] = []
        soul = Soul(**d)
        with pytest.raises(SoulStoreError, match="2 critical") as exc_info:
            lint_or_raise(soul)
        msg = str(exc_info.value)
        assert "scheduling_no_autonomous_irreversible" in msg
        assert "approval_channels_required" in msg


# ===================================================================
# Wiring: load_active_soul runs linter
# ===================================================================

class TestLinterWiredIntoLoader:

    def test_load_rejects_soul_with_critical_lint_issue(self, tmp_path):
        """load_active_soul should fail when linter finds critical issues."""
        import yaml

        bad_soul = _minimal_soul_dict()
        bad_soul["scheduling_boundaries"]["no_autonomous_irreversible"] = False

        soul_dir = tmp_path / "soul"
        versions_dir = soul_dir / "soul_versions"
        versions_dir.mkdir(parents=True)
        (versions_dir / "soul_v1.yaml").write_text(
            yaml.dump(bad_soul), encoding="utf-8"
        )
        (soul_dir / "ACTIVE").write_text("v1", encoding="utf-8")

        with pytest.raises(SoulStoreError, match="lint failed"):
            load_active_soul(str(soul_dir))

    def test_load_succeeds_with_clean_soul(self, tmp_path):
        """load_active_soul should succeed when linter finds no critical issues."""
        import yaml

        good_soul = _minimal_soul_dict()
        soul_dir = tmp_path / "soul"
        versions_dir = soul_dir / "soul_versions"
        versions_dir.mkdir(parents=True)
        (versions_dir / "soul_v1.yaml").write_text(
            yaml.dump(good_soul), encoding="utf-8"
        )
        (soul_dir / "ACTIVE").write_text("v1", encoding="utf-8")

        soul = load_active_soul(str(soul_dir))
        assert soul.version == "v1"
