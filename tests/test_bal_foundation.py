"""
Tests for BAL Phase 1 Foundation.

Covers: feature flags, BALConfig, BAL gates, BALDatabase, BAL receipts,
Soul overlay loading and merging, composable Soul linter checks.
"""

import os
import pytest
import yaml
from pathlib import Path


# ===================================================================
# Helpers
# ===================================================================

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
            {
                "name": "destructive_actions_require_approval",
                "description": "Destructive actions need approval",
                "enforced": True,
            },
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


def _write_soul_with_overlay(tmp_path, overlay_data=None):
    """Create a soul directory with v1 soul and optional BAL overlay."""
    soul_dir = tmp_path / "soul"
    versions_dir = soul_dir / "soul_versions"
    versions_dir.mkdir(parents=True)

    # Write base soul
    (versions_dir / "soul_v1.yaml").write_text(
        yaml.dump(_minimal_soul_dict()), encoding="utf-8"
    )
    (soul_dir / "ACTIVE").write_text("v1", encoding="utf-8")

    # Write overlay
    if overlay_data:
        overlays_dir = soul_dir / "overlays"
        overlays_dir.mkdir()
        (overlays_dir / "bal.yaml").write_text(
            yaml.dump(overlay_data), encoding="utf-8"
        )

    return str(soul_dir)


def _bal_overlay_dict() -> dict:
    return {
        "overlay_name": "bal",
        "feature_flag": "FEATURE_BAL",
        "description": "BAL governance overlay",
        "risk_rules": [
            {
                "name": "no_unauthorized_billing",
                "description": "No billing without authorization",
                "enforced": True,
            },
            {
                "name": "no_spam",
                "description": "No spam delivery",
                "enforced": True,
            },
            {
                "name": "content_verification_mandatory",
                "description": "Content must pass QA",
                "enforced": True,
            },
        ],
        "tone_invariants": [
            "Never promise beyond tier",
            "Disclose AI if requested",
        ],
        "memory_ethics": [
            "Client data isolation",
            "Export/archive on request",
        ],
        "autonomy_posture": {
            "allowed_autonomous": ["bal_content_intake", "bal_quality_check"],
            "requires_approval": ["bal_billing_charge", "bal_delivery_send"],
        },
        "scheduling_boundaries": "BAL jobs respect client tier limits.",
    }


# ===================================================================
# Feature Flags
# ===================================================================


class TestBALFeatureFlag:

    def test_feature_bal_default_false(self, monkeypatch):
        monkeypatch.delenv("FEATURE_BAL", raising=False)
        from src.core.feature_flags import reload_flags
        reload_flags()
        import src.core.feature_flags as ff
        assert ff.FEATURE_BAL is False

    def test_feature_bal_enabled(self, monkeypatch):
        monkeypatch.setenv("FEATURE_BAL", "true")
        from src.core.feature_flags import reload_flags
        reload_flags()
        import src.core.feature_flags as ff
        assert ff.FEATURE_BAL is True

    def test_feature_bal_in_restart_required(self):
        from src.core.feature_flags import RESTART_REQUIRED_FLAGS
        assert "FEATURE_BAL" in RESTART_REQUIRED_FLAGS

    def test_feature_bal_in_get_all_flags(self, monkeypatch):
        monkeypatch.setenv("FEATURE_BAL", "true")
        from src.core.feature_flags import reload_flags, get_all_flags
        reload_flags()
        flags = get_all_flags()
        assert "FEATURE_BAL" in flags

    def teardown_method(self):
        os.environ.pop("FEATURE_BAL", None)
        from src.core.feature_flags import reload_flags
        reload_flags()


# ===================================================================
# BAL Config
# ===================================================================


class TestBALConfig:

    def test_default_config(self):
        from src.core.bal.config import load_bal_config
        config = load_bal_config()
        assert config.bal_enabled is False
        assert config.bal_intake is False
        assert config.bal_data_dir == "/home/lancelot/data/bal"
        assert config.bal_max_clients == 100

    def test_config_from_env(self, monkeypatch):
        monkeypatch.setenv("FEATURE_BAL", "true")
        monkeypatch.setenv("BAL_INTAKE", "true")
        monkeypatch.setenv("BAL_DATA_DIR", "/tmp/bal_test")
        from src.core.bal.config import load_bal_config
        config = load_bal_config()
        assert config.bal_enabled is True
        assert config.bal_intake is True
        assert config.bal_data_dir == "/tmp/bal_test"

    def test_stripe_key_placeholder(self):
        from src.core.bal.config import load_bal_config
        config = load_bal_config()
        assert config.bal_stripe_secret_key == ""
        assert config.bal_stripe_webhook_secret == ""


# ===================================================================
# BAL Database
# ===================================================================


class TestBALDatabase:

    def test_database_created(self, tmp_path):
        from src.core.bal.database import BALDatabase
        db = BALDatabase(data_dir=str(tmp_path))
        assert Path(db.db_path).exists()
        db.close()

    def test_schema_version_recorded(self, tmp_path):
        from src.core.bal.database import BALDatabase
        db = BALDatabase(data_dir=str(tmp_path))
        conn = db._get_connection()
        cursor = conn.execute("SELECT MAX(version) FROM bal_schema_version")
        version = cursor.fetchone()[0]
        assert version == db.CURRENT_SCHEMA_VERSION
        db.close()

    def test_all_tables_created(self, tmp_path):
        from src.core.bal.database import BALDatabase
        db = BALDatabase(data_dir=str(tmp_path))
        conn = db._get_connection()
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}
        expected = {
            "bal_schema_version", "bal_clients", "bal_intake",
            "bal_content", "bal_deliveries", "bal_financial_receipts",
        }
        assert expected.issubset(tables)
        db.close()

    def test_wal_mode_enabled(self, tmp_path):
        from src.core.bal.database import BALDatabase
        db = BALDatabase(data_dir=str(tmp_path))
        conn = db._get_connection()
        cursor = conn.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        assert mode == "wal"
        db.close()

    def test_foreign_keys_enabled(self, tmp_path):
        from src.core.bal.database import BALDatabase
        db = BALDatabase(data_dir=str(tmp_path))
        conn = db._get_connection()
        cursor = conn.execute("PRAGMA foreign_keys")
        fk = cursor.fetchone()[0]
        assert fk == 1
        db.close()

    def test_idempotent_init(self, tmp_path):
        from src.core.bal.database import BALDatabase
        db1 = BALDatabase(data_dir=str(tmp_path))
        db1.close()
        # Second init should not fail or double-create
        db2 = BALDatabase(data_dir=str(tmp_path))
        conn = db2._get_connection()
        cursor = conn.execute("SELECT COUNT(*) FROM bal_schema_version")
        count = cursor.fetchone()[0]
        assert count == db2.CURRENT_SCHEMA_VERSION  # One record per migration
        db2.close()

    def test_transaction_rollback(self, tmp_path):
        from src.core.bal.database import BALDatabase
        db = BALDatabase(data_dir=str(tmp_path))
        try:
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO bal_clients (id, name, email, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    ("test-1", "Test", "t@t.com", "2024-01-01", "2024-01-01"),
                )
                raise RuntimeError("Force rollback")
        except RuntimeError:
            pass
        conn = db._get_connection()
        cursor = conn.execute("SELECT COUNT(*) FROM bal_clients")
        assert cursor.fetchone()[0] == 0
        db.close()


# ===================================================================
# BAL Receipts
# ===================================================================


class TestBALReceipts:

    def test_receipt_types_exist(self):
        from src.shared.receipts import ActionType
        assert ActionType.BAL_CLIENT_EVENT.value == "bal_client_event"
        assert ActionType.BAL_INTAKE_EVENT.value == "bal_intake_event"
        assert ActionType.BAL_REPURPOSE_EVENT.value == "bal_repurpose_event"
        assert ActionType.BAL_DELIVERY_EVENT.value == "bal_delivery_event"
        assert ActionType.BAL_BILLING_EVENT.value == "bal_billing_event"

    def test_emit_bal_receipt(self, tmp_path):
        from src.core.bal.receipts import emit_bal_receipt
        receipt = emit_bal_receipt(
            event_type="client",
            action_name="client_created",
            inputs={"name": "Test Client"},
            data_dir=str(tmp_path),
        )
        assert receipt.action_type == "bal_client_event"
        assert receipt.action_name == "client_created"
        assert receipt.metadata["bal_subsystem"] == "client"

    def test_emit_bad_event_type_raises(self):
        from src.core.bal.receipts import emit_bal_receipt
        with pytest.raises(ValueError, match="Unknown BAL event type"):
            emit_bal_receipt(
                event_type="nonexistent",
                action_name="test",
                inputs={},
            )


# ===================================================================
# Soul Overlay Loading
# ===================================================================


class TestSoulOverlayLoading:

    def test_load_no_overlays_dir(self, tmp_path):
        from src.core.soul.layers import load_overlays
        soul_dir = _write_soul_with_overlay(tmp_path, overlay_data=None)
        overlays = load_overlays(soul_dir, active_features={"FEATURE_BAL"})
        assert overlays == []

    def test_load_overlay_with_active_flag(self, tmp_path):
        from src.core.soul.layers import load_overlays
        soul_dir = _write_soul_with_overlay(tmp_path, _bal_overlay_dict())
        overlays = load_overlays(soul_dir, active_features={"FEATURE_BAL"})
        assert len(overlays) == 1
        assert overlays[0].overlay_name == "bal"

    def test_load_overlay_skipped_when_flag_inactive(self, tmp_path):
        from src.core.soul.layers import load_overlays
        soul_dir = _write_soul_with_overlay(tmp_path, _bal_overlay_dict())
        overlays = load_overlays(soul_dir, active_features=set())
        assert overlays == []

    def test_load_overlay_parses_risk_rules(self, tmp_path):
        from src.core.soul.layers import load_overlays
        soul_dir = _write_soul_with_overlay(tmp_path, _bal_overlay_dict())
        overlays = load_overlays(soul_dir, active_features={"FEATURE_BAL"})
        rule_names = {r.name for r in overlays[0].risk_rules}
        assert "no_spam" in rule_names
        assert "no_unauthorized_billing" in rule_names


# ===================================================================
# Soul Merge
# ===================================================================


class TestSoulMerge:

    def test_merge_appends_risk_rules(self):
        from src.core.soul.store import Soul
        from src.core.soul.layers import SoulOverlay, merge_soul

        base = Soul(**_minimal_soul_dict())
        overlay = SoulOverlay(**_bal_overlay_dict())
        merged = merge_soul(base, [overlay])

        rule_names = {r.name for r in merged.risk_rules}
        assert "destructive_actions_require_approval" in rule_names
        assert "no_unauthorized_billing" in rule_names
        assert "no_spam" in rule_names

    def test_merge_appends_tone_invariants(self):
        from src.core.soul.store import Soul
        from src.core.soul.layers import SoulOverlay, merge_soul

        base = Soul(**_minimal_soul_dict())
        overlay = SoulOverlay(**_bal_overlay_dict())
        merged = merge_soul(base, [overlay])

        assert "Never promise beyond tier" in merged.tone_invariants
        assert "Never suppress errors or degrade silently" in merged.tone_invariants

    def test_merge_appends_memory_ethics(self):
        from src.core.soul.store import Soul
        from src.core.soul.layers import SoulOverlay, merge_soul

        base = Soul(**_minimal_soul_dict())
        overlay = SoulOverlay(**_bal_overlay_dict())
        merged = merge_soul(base, [overlay])

        assert "Client data isolation" in merged.memory_ethics
        assert "Do not store PII without consent" in merged.memory_ethics

    def test_merge_appends_autonomy_posture(self):
        from src.core.soul.store import Soul
        from src.core.soul.layers import SoulOverlay, merge_soul

        base = Soul(**_minimal_soul_dict())
        overlay = SoulOverlay(**_bal_overlay_dict())
        merged = merge_soul(base, [overlay])

        assert "bal_content_intake" in merged.autonomy_posture.allowed_autonomous
        assert "classify_intent" in merged.autonomy_posture.allowed_autonomous
        assert "bal_billing_charge" in merged.autonomy_posture.requires_approval
        assert "deploy" in merged.autonomy_posture.requires_approval

    def test_merge_preserves_immutable_fields(self):
        from src.core.soul.store import Soul
        from src.core.soul.layers import SoulOverlay, merge_soul

        base = Soul(**_minimal_soul_dict())
        overlay = SoulOverlay(**_bal_overlay_dict())
        merged = merge_soul(base, [overlay])

        assert merged.version == base.version
        assert merged.mission == base.mission
        assert merged.allegiance == base.allegiance
        assert merged.autonomy_posture.level == base.autonomy_posture.level

    def test_merge_cannot_weaken_base(self):
        """Overlays can only add, never remove base rules."""
        from src.core.soul.store import Soul
        from src.core.soul.layers import SoulOverlay, merge_soul

        base = Soul(**_minimal_soul_dict())
        base_rule_count = len(base.risk_rules)
        base_tone_count = len(base.tone_invariants)

        overlay = SoulOverlay(**_bal_overlay_dict())
        merged = merge_soul(base, [overlay])

        assert len(merged.risk_rules) >= base_rule_count
        assert len(merged.tone_invariants) >= base_tone_count

    def test_merge_appends_scheduling_description(self):
        from src.core.soul.store import Soul
        from src.core.soul.layers import SoulOverlay, merge_soul

        base = Soul(**_minimal_soul_dict())
        overlay = SoulOverlay(**_bal_overlay_dict())
        merged = merge_soul(base, [overlay])

        assert "Safe scheduling" in merged.scheduling_boundaries.description
        assert "BAL jobs" in merged.scheduling_boundaries.description

    def test_no_overlays_returns_base(self):
        from src.core.soul.store import Soul
        from src.core.soul.layers import merge_soul

        base = Soul(**_minimal_soul_dict())
        merged = merge_soul(base, [])
        assert merged == base

    def test_duplicate_rules_not_duplicated(self):
        from src.core.soul.store import Soul
        from src.core.soul.layers import SoulOverlay, merge_soul

        base = Soul(**_minimal_soul_dict())
        overlay = SoulOverlay(**_bal_overlay_dict())

        # Merge twice with same overlay
        merged = merge_soul(base, [overlay, overlay])

        rule_names = [r.name for r in merged.risk_rules]
        assert len(rule_names) == len(set(rule_names))

    def test_scheduling_numeric_limits_preserved(self):
        from src.core.soul.store import Soul
        from src.core.soul.layers import SoulOverlay, merge_soul

        base = Soul(**_minimal_soul_dict())
        overlay = SoulOverlay(**_bal_overlay_dict())
        merged = merge_soul(base, [overlay])

        assert merged.scheduling_boundaries.max_concurrent_jobs == base.scheduling_boundaries.max_concurrent_jobs
        assert merged.scheduling_boundaries.max_job_duration_seconds == base.scheduling_boundaries.max_job_duration_seconds
        assert merged.scheduling_boundaries.no_autonomous_irreversible == base.scheduling_boundaries.no_autonomous_irreversible


# ===================================================================
# BAL-Specific Linter Checks
# ===================================================================


class TestBALLinterChecks:

    def test_bal_billing_check_passes_with_overlay(self):
        from src.core.soul.store import Soul
        from src.core.soul.layers import SoulOverlay, merge_soul
        from src.core.soul.linter import lint, LintSeverity

        base = Soul(**_minimal_soul_dict())
        overlay = SoulOverlay(**_bal_overlay_dict())
        merged = merge_soul(base, [overlay])

        issues = lint(merged)
        billing_critical = [
            i for i in issues
            if i.rule == "bal_billing_requires_approval"
            and i.severity == LintSeverity.CRITICAL
        ]
        assert billing_critical == []

    def test_bal_billing_check_fails_without_approval(self):
        from src.core.soul.store import Soul
        from src.core.soul.linter import lint, LintSeverity

        d = _minimal_soul_dict()
        d["risk_rules"].append(
            {"name": "no_unauthorized_billing",
             "description": "test", "enforced": True}
        )
        # No billing-related entry in requires_approval
        soul = Soul(**d)

        issues = lint(soul)
        billing_issues = [
            i for i in issues
            if i.rule == "bal_billing_requires_approval"
        ]
        assert len(billing_issues) == 1
        assert billing_issues[0].severity == LintSeverity.CRITICAL

    def test_bal_no_spam_check_passes_with_overlay(self):
        from src.core.soul.store import Soul
        from src.core.soul.layers import SoulOverlay, merge_soul
        from src.core.soul.linter import lint, LintSeverity

        base = Soul(**_minimal_soul_dict())
        overlay = SoulOverlay(**_bal_overlay_dict())
        merged = merge_soul(base, [overlay])

        issues = lint(merged)
        spam_critical = [
            i for i in issues
            if i.rule == "bal_no_spam_rule_required"
            and i.severity == LintSeverity.CRITICAL
        ]
        assert spam_critical == []

    def test_bal_no_spam_check_fails_without_rule(self):
        from src.core.soul.store import Soul
        from src.core.soul.linter import lint, LintSeverity

        d = _minimal_soul_dict()
        d["autonomy_posture"]["requires_approval"].append("bal_delivery_send")
        # No "no_spam" risk rule
        soul = Soul(**d)

        issues = lint(soul)
        spam_issues = [
            i for i in issues
            if i.rule == "bal_no_spam_rule_required"
        ]
        assert len(spam_issues) == 1
        assert spam_issues[0].severity == LintSeverity.CRITICAL

    def test_bal_checks_skip_when_no_overlay(self):
        from src.core.soul.store import Soul
        from src.core.soul.linter import lint

        soul = Soul(**_minimal_soul_dict())
        issues = lint(soul)

        bal_issues = [i for i in issues if i.rule.startswith("bal_")]
        assert bal_issues == []


# ===================================================================
# Full Integration
# ===================================================================


class TestBALFullIntegration:

    def test_bal_enabled_all_components(self, tmp_path, monkeypatch):
        """When FEATURE_BAL=true: database created, config loaded, overlay merged."""
        monkeypatch.setenv("FEATURE_BAL", "true")
        from src.core.feature_flags import reload_flags
        reload_flags()

        # Config
        from src.core.bal.config import load_bal_config
        config = load_bal_config()
        assert config.bal_enabled is True

        # Database
        from src.core.bal.database import BALDatabase
        db = BALDatabase(data_dir=str(tmp_path / "bal"))
        assert Path(db.db_path).exists()
        db.close()

        # Overlay merge
        from src.core.soul.layers import load_active_soul_with_overlays
        soul_dir = _write_soul_with_overlay(tmp_path, _bal_overlay_dict())
        merged = load_active_soul_with_overlays(
            soul_dir, active_features={"FEATURE_BAL"}
        )
        assert any(r.name == "no_spam" for r in merged.risk_rules)
        assert any(r.name == "destructive_actions_require_approval" for r in merged.risk_rules)

        # Receipt
        from src.core.bal.receipts import emit_bal_receipt
        receipt = emit_bal_receipt(
            event_type="client",
            action_name="bal_startup",
            inputs={"phase": "1"},
            data_dir=str(tmp_path),
        )
        assert receipt.action_type == "bal_client_event"

    def teardown_method(self):
        os.environ.pop("FEATURE_BAL", None)
        from src.core.feature_flags import reload_flags
        reload_flags()


class TestBALDisabledIntegration:

    def test_bal_disabled_nothing_initializes(self, tmp_path, monkeypatch):
        """When FEATURE_BAL=false: nothing BAL-related activates."""
        monkeypatch.delenv("FEATURE_BAL", raising=False)
        from src.core.feature_flags import reload_flags
        reload_flags()

        import src.core.feature_flags as ff
        assert ff.FEATURE_BAL is False

        # Overlays should not load when no features active
        from src.core.soul.layers import load_overlays
        soul_dir = _write_soul_with_overlay(tmp_path, _bal_overlay_dict())
        overlays = load_overlays(soul_dir, active_features=set())
        assert overlays == []

    def teardown_method(self):
        os.environ.pop("FEATURE_BAL", None)
        from src.core.feature_flags import reload_flags
        reload_flags()
