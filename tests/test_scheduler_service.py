"""
Tests for src.core.scheduler.service — Scheduler service (Prompt 12 / D2-D3).
"""

import pytest
import yaml
from pathlib import Path

from src.core.scheduler.schema import SchedulerError
from src.core.scheduler.service import SchedulerService, JobRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_config(tmp_path, jobs=None):
    """Write scheduler.yaml with valid jobs."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    if jobs is None:
        jobs = [
            {
                "id": "health_sweep",
                "name": "Health Sweep",
                "trigger": {"type": "interval", "seconds": 60},
                "enabled": True,
                "requires_ready": True,
                "requires_approvals": [],
                "timeout_s": 30,
                "skill": "health_check",
            },
            {
                "id": "memory_cleanup",
                "name": "Memory Cleanup",
                "trigger": {"type": "cron", "expression": "0 3 * * *"},
                "enabled": True,
                "timeout_s": 120,
                "skill": "memory_cleanup",
            },
        ]

    (config_dir / "scheduler.yaml").write_text(
        yaml.dump({"jobs": jobs}), encoding="utf-8",
    )
    return str(config_dir)


@pytest.fixture
def service(tmp_path):
    """Create a SchedulerService with test directories."""
    config_dir = _write_config(tmp_path)
    data_dir = str(tmp_path / "data")
    return SchedulerService(data_dir=data_dir, config_dir=config_dir)


# ===================================================================
# Job registration and persistence
# ===================================================================

class TestJobRegistration:

    def test_register_from_config(self, service):
        """Blueprint requirement: unit test job registration."""
        count = service.register_from_config()
        assert count == 2
        jobs = service.list_jobs()
        assert len(jobs) == 2

    def test_idempotent_registration(self, service):
        service.register_from_config()
        count = service.register_from_config()
        assert count == 0  # No new jobs
        assert len(service.list_jobs()) == 2

    def test_persistence_across_instances(self, tmp_path):
        """Blueprint requirement: persistence behavior."""
        config_dir = _write_config(tmp_path)
        data_dir = str(tmp_path / "data")

        s1 = SchedulerService(data_dir=data_dir, config_dir=config_dir)
        s1.register_from_config()
        assert len(s1.list_jobs()) == 2

        # New instance reads from same SQLite
        s2 = SchedulerService(data_dir=data_dir, config_dir=config_dir)
        assert len(s2.list_jobs()) == 2

    def test_job_fields_populated(self, service):
        service.register_from_config()
        job = service.get_job("health_sweep")
        assert job is not None
        assert job.name == "Health Sweep"
        assert job.skill == "health_check"
        assert job.trigger_type == "interval"
        assert job.trigger_value == "60"
        assert job.timeout_s == 30
        assert job.enabled is True


# ===================================================================
# list_jobs / get_job
# ===================================================================

class TestListAndGet:

    def test_empty_list(self, tmp_path):
        config_dir = _write_config(tmp_path, jobs=[])
        data_dir = str(tmp_path / "data")
        s = SchedulerService(data_dir=data_dir, config_dir=config_dir)
        assert s.list_jobs() == []

    def test_get_not_found(self, service):
        assert service.get_job("nonexistent") is None

    def test_get_existing_job(self, service):
        service.register_from_config()
        job = service.get_job("health_sweep")
        assert isinstance(job, JobRecord)
        assert job.id == "health_sweep"


# ===================================================================
# enable_job / disable_job
# ===================================================================

class TestEnableDisable:

    def test_disable_job(self, service):
        service.register_from_config()
        service.disable_job("health_sweep")
        job = service.get_job("health_sweep")
        assert job.enabled is False

    def test_enable_job(self, service):
        service.register_from_config()
        service.disable_job("health_sweep")
        service.enable_job("health_sweep")
        job = service.get_job("health_sweep")
        assert job.enabled is True

    def test_disable_not_found_raises(self, service):
        with pytest.raises(SchedulerError, match="not found"):
            service.disable_job("nonexistent")

    def test_enable_not_found_raises(self, service):
        with pytest.raises(SchedulerError, match="not found"):
            service.enable_job("nonexistent")

    def test_disable_persists(self, tmp_path):
        config_dir = _write_config(tmp_path)
        data_dir = str(tmp_path / "data")

        s1 = SchedulerService(data_dir=data_dir, config_dir=config_dir)
        s1.register_from_config()
        s1.disable_job("health_sweep")

        s2 = SchedulerService(data_dir=data_dir, config_dir=config_dir)
        job = s2.get_job("health_sweep")
        assert job.enabled is False


# ===================================================================
# run_now
# ===================================================================

class TestRunNow:

    def test_run_now_updates_record(self, service):
        service.register_from_config()
        result = service.run_now("health_sweep")
        assert result.last_run_at is not None
        assert result.last_run_status == "triggered"
        assert result.run_count == 1

    def test_run_now_increments_count(self, service):
        service.register_from_config()
        service.run_now("health_sweep")
        service.run_now("health_sweep")
        job = service.get_job("health_sweep")
        assert job.run_count == 2

    def test_run_now_not_found_raises(self, service):
        with pytest.raises(SchedulerError, match="not found"):
            service.run_now("nonexistent")


# ===================================================================
# Heartbeat field
# ===================================================================

class TestHeartbeat:

    def test_last_tick_none_initially(self, service):
        assert service.last_scheduler_tick_at is None

    def test_last_tick_set_after_register(self, service):
        service.register_from_config()
        assert service.last_scheduler_tick_at is not None

    def test_last_tick_updated_on_run_now(self, service):
        service.register_from_config()
        tick1 = service.last_scheduler_tick_at
        import time
        time.sleep(0.01)
        service.run_now("health_sweep")
        tick2 = service.last_scheduler_tick_at
        assert tick2 >= tick1
