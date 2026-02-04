"""
vNext2 Regression tests — Prompt 17 / H1-H5.

Covers:
    - Feature flags (_env_bool, reload, defaults, log)
    - Soul linter invariant regressions
    - Scheduler gating regressions
    - Safe-error regressions (no stack traces leaked)
"""

from __future__ import annotations

import os
import logging
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------

from src.core.feature_flags import (
    _env_bool,
    reload_flags,
    log_feature_flags,
)


class TestEnvBool:
    """_env_bool parses boolean env vars correctly."""

    def test_default_true(self):
        assert _env_bool("UNSET_VAR_12345") is True

    def test_default_false(self):
        assert _env_bool("UNSET_VAR_12345", default=False) is False

    @pytest.mark.parametrize("val", ["true", "TRUE", "True", "1", "yes", "YES"])
    def test_truthy_values(self, val):
        with patch.dict(os.environ, {"TEST_FLAG": val}):
            assert _env_bool("TEST_FLAG") is True

    @pytest.mark.parametrize("val", ["false", "FALSE", "0", "no", "NO", "anything"])
    def test_falsy_values(self, val):
        with patch.dict(os.environ, {"TEST_FLAG": val}):
            assert _env_bool("TEST_FLAG") is False

    def test_whitespace_stripped(self):
        with patch.dict(os.environ, {"TEST_FLAG": "  true  "}):
            assert _env_bool("TEST_FLAG") is True

    def test_empty_string_uses_default(self):
        with patch.dict(os.environ, {"TEST_FLAG": ""}):
            assert _env_bool("TEST_FLAG", default=False) is False


class TestReloadFlags:
    """reload_flags re-reads from environment."""

    def test_reload_picks_up_change(self):
        import src.core.feature_flags as ff

        with patch.dict(os.environ, {"FEATURE_SOUL": "false"}):
            reload_flags()
            assert ff.FEATURE_SOUL is False

        # Restore
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FEATURE_SOUL", None)
            reload_flags()
            assert ff.FEATURE_SOUL is True

    def test_all_flags_default_true(self):
        import src.core.feature_flags as ff

        env_clear = {
            "FEATURE_SOUL": "",
            "FEATURE_SKILLS": "",
            "FEATURE_HEALTH_MONITOR": "",
            "FEATURE_SCHEDULER": "",
        }
        with patch.dict(os.environ, env_clear):
            reload_flags()
            assert ff.FEATURE_SOUL is True
            assert ff.FEATURE_SKILLS is True
            assert ff.FEATURE_HEALTH_MONITOR is True
            assert ff.FEATURE_SCHEDULER is True


class TestLogFeatureFlags:
    """log_feature_flags emits a log line."""

    def test_logs_at_info_level(self, caplog):
        with caplog.at_level(logging.INFO, logger="src.core.feature_flags"):
            log_feature_flags()
        assert "Feature flags:" in caplog.text
        assert "SOUL=" in caplog.text
        assert "SKILLS=" in caplog.text


class TestBootWithFlagsDisabled:
    """System can import each subsystem when its flag is False."""

    def test_boot_without_soul(self):
        import src.core.feature_flags as ff
        with patch.dict(os.environ, {"FEATURE_SOUL": "false"}):
            reload_flags()
            assert ff.FEATURE_SOUL is False
            from src.core.soul import store  # noqa: F401
        reload_flags()

    def test_boot_without_skills(self):
        import src.core.feature_flags as ff
        with patch.dict(os.environ, {"FEATURE_SKILLS": "false"}):
            reload_flags()
            assert ff.FEATURE_SKILLS is False
            from src.core.skills import schema  # noqa: F401
        reload_flags()

    def test_boot_without_health_monitor(self):
        import src.core.feature_flags as ff
        with patch.dict(os.environ, {"FEATURE_HEALTH_MONITOR": "false"}):
            reload_flags()
            assert ff.FEATURE_HEALTH_MONITOR is False
            from src.core.health import types  # noqa: F401
        reload_flags()

    def test_boot_without_scheduler(self):
        import src.core.feature_flags as ff
        with patch.dict(os.environ, {"FEATURE_SCHEDULER": "false"}):
            reload_flags()
            assert ff.FEATURE_SCHEDULER is False
            from src.core.scheduler import schema  # noqa: F401
        reload_flags()


# ---------------------------------------------------------------------------
# Soul linter invariant regressions
# ---------------------------------------------------------------------------

from src.core.soul.store import Soul, SoulStoreError
from src.core.soul.linter import lint, lint_or_raise, LintSeverity


def _valid_soul_dict(**overrides) -> dict:
    """Build a minimal dict that passes all linter checks."""
    base = {
        "version": "v1",
        "mission": "Serve the owner faithfully",
        "allegiance": "owner",
        "autonomy_posture": {
            "level": "supervised",
            "description": "Supervised autonomy",
            "allowed_autonomous": ["status_check"],
            "requires_approval": ["delete_data", "deploy_service"],
        },
        "risk_rules": [
            {
                "name": "destructive_actions_require_approval",
                "description": "Block destructive ops",
                "enforced": True,
            }
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
        "memory_ethics": ["Respect data ownership"],
        "scheduling_boundaries": {
            "max_concurrent_jobs": 5,
            "max_job_duration_seconds": 300,
            "no_autonomous_irreversible": True,
            "require_ready_state": True,
        },
    }
    base.update(overrides)
    return base


class TestSoulLinterRegressions:
    """Ensure linter catches all invariant violations."""

    def test_valid_soul_passes(self):
        soul = Soul(**_valid_soul_dict())
        issues = lint(soul)
        critical = [i for i in issues if i.severity == LintSeverity.CRITICAL]
        assert len(critical) == 0

    def test_missing_destructive_in_requires_approval(self):
        d = _valid_soul_dict()
        d["autonomy_posture"]["requires_approval"] = ["review_logs"]
        soul = Soul(**d)
        issues = lint(soul)
        rules = [i.rule for i in issues if i.severity == LintSeverity.CRITICAL]
        assert "destructive_actions_require_approval" in rules

    def test_missing_no_silent_degradation(self):
        d = _valid_soul_dict()
        d["tone_invariants"] = ["Be polite"]
        soul = Soul(**d)
        issues = lint(soul)
        rules = [i.rule for i in issues if i.severity == LintSeverity.CRITICAL]
        assert "no_silent_degradation" in rules

    def test_autonomous_irreversible_flag_false(self):
        d = _valid_soul_dict()
        d["scheduling_boundaries"]["no_autonomous_irreversible"] = False
        soul = Soul(**d)
        issues = lint(soul)
        rules = [i.rule for i in issues if i.severity == LintSeverity.CRITICAL]
        assert "scheduling_no_autonomous_irreversible" in rules

    def test_no_approval_channels(self):
        d = _valid_soul_dict()
        d["approval_rules"]["channels"] = []
        soul = Soul(**d)
        issues = lint(soul)
        rules = [i.rule for i in issues if i.severity == LintSeverity.CRITICAL]
        assert "approval_channels_required" in rules

    def test_lint_or_raise_on_critical(self):
        d = _valid_soul_dict()
        d["approval_rules"]["channels"] = []
        soul = Soul(**d)
        with pytest.raises(SoulStoreError, match="critical issue"):
            lint_or_raise(soul)

    def test_missing_memory_ethics_is_warning(self):
        d = _valid_soul_dict()
        d["memory_ethics"] = []
        soul = Soul(**d)
        issues = lint(soul)
        warnings = [i for i in issues if i.severity == LintSeverity.WARNING]
        assert any(i.rule == "memory_ethics_required" for i in warnings)
        # Should NOT raise
        lint_or_raise(soul)


# ---------------------------------------------------------------------------
# Scheduler gating regressions
# ---------------------------------------------------------------------------

from src.core.scheduler.executor import JobExecutor, Gate, JobExecutionResult
from src.core.scheduler.service import SchedulerService


def _write_sched_config(tmp_path, jobs=None):
    """Write a scheduler.yaml config and return the config dir path."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    if jobs is None:
        jobs = [
            {
                "id": "test_job",
                "name": "Test Job",
                "trigger": {"type": "interval", "seconds": 60},
                "enabled": True,
                "requires_ready": True,
                "requires_approvals": [],
                "timeout_s": 30,
                "skill": "echo",
            },
        ]
    import yaml
    (config_dir / "scheduler.yaml").write_text(
        yaml.dump({"jobs": jobs}), encoding="utf-8",
    )
    return str(config_dir)


class TestSchedulerGatingRegressions:
    """Gating pipeline blocks jobs correctly."""

    @pytest.fixture
    def svc(self, tmp_path):
        config_dir = _write_sched_config(tmp_path)
        data_dir = str(tmp_path / "data")
        svc = SchedulerService(data_dir=data_dir, config_dir=config_dir)
        svc.register_from_config()
        return svc

    def test_gate_failure_skips_job(self, svc):
        gate = Gate(name="ready_check", check_fn=lambda: False,
                    skip_reason="System not READY")
        executor = JobExecutor(svc, gates=[gate])
        result = executor.execute_job("test_job")
        assert result.skipped is True
        assert "not READY" in (result.skip_reason or "")

    def test_gate_passes_allows_execution(self, svc):
        gate = Gate(name="ready_check", check_fn=lambda: True)
        executor = JobExecutor(svc, gates=[gate])
        result = executor.execute_job("test_job")
        assert result.executed is True
        assert result.success is True

    def test_disabled_job_is_skipped(self, svc):
        svc.disable_job("test_job")
        executor = JobExecutor(svc)
        result = executor.execute_job("test_job")
        assert result.skipped is True
        assert "disabled" in (result.skip_reason or "").lower()

    def test_missing_job_is_skipped(self, svc):
        executor = JobExecutor(svc)
        result = executor.execute_job("nonexistent")
        assert result.skipped is True

    def test_approval_required_skips(self, tmp_path):
        config_dir = _write_sched_config(tmp_path / "appr", jobs=[{
            "id": "approval_job",
            "name": "Approval Job",
            "trigger": {"type": "interval", "seconds": 60},
            "enabled": True,
            "requires_approvals": ["owner_approval"],
            "timeout_s": 30,
            "skill": "echo",
        }])
        data_dir = str(tmp_path / "appr" / "data")
        svc = SchedulerService(data_dir=data_dir, config_dir=config_dir)
        svc.register_from_config()
        executor = JobExecutor(svc)
        result = executor.execute_job("approval_job")
        assert result.skipped is True
        assert "approv" in (result.skip_reason or "").lower()

    def test_gate_exception_skips_safely(self, svc):
        def boom():
            raise RuntimeError("boom")

        gate = Gate(name="broken_gate", check_fn=boom, skip_reason="Gate error")
        executor = JobExecutor(svc, gates=[gate])
        result = executor.execute_job("test_job")
        assert result.skipped is True

    def test_receipt_emitted_on_execution(self, svc):
        executor = JobExecutor(svc)
        result = executor.execute_job("test_job")
        assert result.receipt is not None
        assert result.receipt["event"] == "scheduled_job_run"

    def test_receipt_emitted_on_skip(self, svc):
        svc.disable_job("test_job")
        executor = JobExecutor(svc)
        result = executor.execute_job("test_job")
        assert result.receipt is not None
        assert result.receipt["event"] == "scheduled_job_skipped"


# ---------------------------------------------------------------------------
# Safe-error regressions (no stack traces leaked)
# ---------------------------------------------------------------------------

from src.core.health.api import router as health_router, set_snapshot_provider
from src.core.health.types import HealthSnapshot


class TestSafeErrorRegressions:
    """Health and soul endpoints never leak stack traces."""

    @pytest.fixture
    def health_client(self):
        app = FastAPI()
        app.include_router(health_router)
        return TestClient(app)

    def test_health_live_always_200(self, health_client):
        resp = health_client.get("/health/live")
        assert resp.status_code == 200
        assert resp.json()["status"] == "alive"

    def test_health_ready_no_stacktrace_on_provider_error(self, health_client):
        def bad_provider():
            raise RuntimeError("internal kaboom")

        set_snapshot_provider(bad_provider)
        try:
            resp = health_client.get("/health/ready")
            assert resp.status_code == 200
            body = resp.json()
            assert body["ready"] is False
            raw = str(body)
            assert "Traceback" not in raw
            assert "kaboom" not in raw
        finally:
            set_snapshot_provider(None)

    def test_health_ready_no_provider_returns_safe_body(self, health_client):
        set_snapshot_provider(None)
        resp = health_client.get("/health/ready")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ready"] is False
        assert "No health snapshot provider" in str(body.get("degraded_reasons", []))

    def test_health_ready_happy_path(self, health_client):
        def ok_provider():
            return HealthSnapshot(ready=True, onboarding_state="READY")

        set_snapshot_provider(ok_provider)
        try:
            resp = health_client.get("/health/ready")
            assert resp.status_code == 200
            assert resp.json()["ready"] is True
        finally:
            set_snapshot_provider(None)
