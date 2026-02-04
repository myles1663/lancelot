"""
Tests for src.core.scheduler.schema — Scheduler config + schema (Prompt 11 / D1).
"""

import pytest
import yaml
from pathlib import Path

from src.core.scheduler.schema import (
    JobSpec,
    TriggerSpec,
    TriggerType,
    SchedulerConfig,
    SchedulerError,
    load_scheduler_config,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_job(**overrides) -> dict:
    """Return a minimal valid job spec dict."""
    base = {
        "id": "health_sweep",
        "name": "Health Sweep",
        "trigger": {"type": "interval", "seconds": 60},
        "enabled": True,
        "requires_ready": True,
        "requires_approvals": [],
        "timeout_s": 30,
        "skill": "health_check",
    }
    base.update(overrides)
    return base


def _write_config(tmp_path, jobs=None, as_example=False):
    """Write a scheduler.yaml or example config."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    data = {"jobs": jobs or [_valid_job()]}
    filename = "scheduler.example.yaml" if as_example else "scheduler.yaml"
    (config_dir / filename).write_text(yaml.dump(data), encoding="utf-8")
    return str(config_dir)


# ===================================================================
# Valid config loads
# ===================================================================

class TestValidConfig:

    def test_valid_config_loads(self, tmp_path):
        """Blueprint requirement: valid config loads."""
        config_dir = _write_config(tmp_path)
        config = load_scheduler_config(config_dir)
        assert len(config.jobs) == 1
        assert config.jobs[0].id == "health_sweep"

    def test_interval_trigger(self, tmp_path):
        config_dir = _write_config(tmp_path)
        config = load_scheduler_config(config_dir)
        assert config.jobs[0].trigger.type == TriggerType.INTERVAL
        assert config.jobs[0].trigger.seconds == 60

    def test_cron_trigger(self, tmp_path):
        job = _valid_job(
            id="daily_job",
            trigger={"type": "cron", "expression": "0 3 * * *"},
        )
        config_dir = _write_config(tmp_path, jobs=[job])
        config = load_scheduler_config(config_dir)
        assert config.jobs[0].trigger.type == TriggerType.CRON
        assert config.jobs[0].trigger.expression == "0 3 * * *"

    def test_multiple_jobs(self, tmp_path):
        jobs = [
            _valid_job(id="job_one"),
            _valid_job(id="job_two"),
        ]
        config_dir = _write_config(tmp_path, jobs=jobs)
        config = load_scheduler_config(config_dir)
        assert len(config.jobs) == 2

    def test_disabled_job(self, tmp_path):
        job = _valid_job(enabled=False)
        config_dir = _write_config(tmp_path, jobs=[job])
        config = load_scheduler_config(config_dir)
        assert config.jobs[0].enabled is False

    def test_requires_approvals(self, tmp_path):
        job = _valid_job(requires_approvals=["owner"])
        config_dir = _write_config(tmp_path, jobs=[job])
        config = load_scheduler_config(config_dir)
        assert "owner" in config.jobs[0].requires_approvals


# ===================================================================
# Invalid triggers fail validation
# ===================================================================

class TestInvalidTriggers:

    def test_invalid_trigger_type_fails(self):
        """Blueprint requirement: invalid triggers fail validation."""
        with pytest.raises(Exception):
            TriggerSpec(type="weekly", seconds=60)

    def test_zero_seconds_fails(self):
        with pytest.raises(Exception, match="positive"):
            TriggerSpec(type="interval", seconds=0)

    def test_negative_seconds_fails(self):
        with pytest.raises(Exception, match="positive"):
            TriggerSpec(type="interval", seconds=-10)

    def test_bad_cron_expression_fails(self):
        with pytest.raises(Exception, match="5 fields"):
            TriggerSpec(type="cron", expression="* * *")

    def test_cron_with_6_fields_fails(self):
        with pytest.raises(Exception, match="5 fields"):
            TriggerSpec(type="cron", expression="0 0 * * * *")


# ===================================================================
# JobSpec validation
# ===================================================================

class TestJobSpecValidation:

    def test_empty_id_fails(self):
        with pytest.raises(Exception, match="empty"):
            JobSpec(**_valid_job(id=""))

    def test_uppercase_id_fails(self):
        with pytest.raises(Exception, match="lowercase"):
            JobSpec(**_valid_job(id="HealthSweep"))

    def test_zero_timeout_fails(self):
        with pytest.raises(Exception, match="positive"):
            JobSpec(**_valid_job(timeout_s=0))


# ===================================================================
# Config file creation from example
# ===================================================================

class TestConfigCreation:

    def test_copies_example_on_first_run(self, tmp_path):
        config_dir = _write_config(tmp_path, as_example=True)
        # No scheduler.yaml exists yet
        config = load_scheduler_config(config_dir)
        assert len(config.jobs) >= 1
        # scheduler.yaml should now exist
        assert (Path(config_dir) / "scheduler.yaml").exists()

    def test_no_config_or_example_raises(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        with pytest.raises(SchedulerError, match="No"):
            load_scheduler_config(str(config_dir))

    def test_invalid_yaml_raises(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "scheduler.yaml").write_text("{{bad yaml", encoding="utf-8")
        with pytest.raises(SchedulerError, match="Invalid YAML"):
            load_scheduler_config(str(config_dir))


# ===================================================================
# Real example config
# ===================================================================

class TestRealExampleConfig:

    def test_example_config_loads(self):
        """The shipped example config must be valid."""
        real_config_dir = str(Path(__file__).parent.parent / "config")
        config = load_scheduler_config(real_config_dir)
        assert len(config.jobs) >= 1
