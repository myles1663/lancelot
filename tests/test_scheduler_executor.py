"""
Tests for src.core.scheduler.executor — Job execution pipeline (Prompt 13 / D4-D6).
"""

import pytest
import yaml
from pathlib import Path

from src.core.scheduler.service import SchedulerService
from src.core.scheduler.executor import JobExecutor, Gate, JobExecutionResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_config(tmp_path, jobs=None):
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
    (config_dir / "scheduler.yaml").write_text(
        yaml.dump({"jobs": jobs}), encoding="utf-8",
    )
    return str(config_dir)


@pytest.fixture
def service(tmp_path):
    config_dir = _write_config(tmp_path)
    data_dir = str(tmp_path / "data")
    svc = SchedulerService(data_dir=data_dir, config_dir=config_dir)
    svc.register_from_config()
    return svc


def _ready_gate():
    return Gate("onboarding_ready", lambda: True, "Not READY")


def _not_ready_gate():
    return Gate("onboarding_ready", lambda: False, "System not READY")


def _healthy_gate():
    return Gate("local_llm", lambda: True, "LLM not healthy")


def _noop_skill(name, inputs):
    return {"status": "ok"}


# ===================================================================
# Job skipped when not READY
# ===================================================================

class TestJobSkippedWhenNotReady:

    def test_skipped_when_not_ready(self, service):
        """Blueprint requirement: job skipped when not READY."""
        executor = JobExecutor(
            service,
            skill_execute_fn=_noop_skill,
            gates=[_not_ready_gate()],
        )
        result = executor.execute_job("test_job")
        assert result.skipped is True
        assert result.executed is False
        assert "READY" in result.skip_reason

    def test_skipped_emits_receipt(self, service):
        executor = JobExecutor(
            service,
            skill_execute_fn=_noop_skill,
            gates=[_not_ready_gate()],
        )
        result = executor.execute_job("test_job")
        assert result.receipt is not None
        assert result.receipt["event"] == "scheduled_job_skipped"


# ===================================================================
# Job runs when READY
# ===================================================================

class TestJobRunsWhenReady:

    def test_runs_when_ready(self, service):
        """Blueprint requirement: job runs when READY and emits receipt."""
        executor = JobExecutor(
            service,
            skill_execute_fn=_noop_skill,
            gates=[_ready_gate(), _healthy_gate()],
        )
        result = executor.execute_job("test_job")
        assert result.executed is True
        assert result.success is True
        assert result.skipped is False

    def test_emits_scheduled_job_run_receipt(self, service):
        executor = JobExecutor(
            service,
            skill_execute_fn=_noop_skill,
            gates=[_ready_gate()],
        )
        result = executor.execute_job("test_job")
        assert result.receipt["event"] == "scheduled_job_run"
        assert result.receipt["job_id"] == "test_job"

    def test_run_updates_scheduler_record(self, service):
        executor = JobExecutor(
            service,
            skill_execute_fn=_noop_skill,
            gates=[_ready_gate()],
        )
        executor.execute_job("test_job")
        job = service.get_job("test_job")
        assert job.run_count == 1
        assert job.last_run_at is not None


# ===================================================================
# Multiple gates
# ===================================================================

class TestMultipleGates:

    def test_all_gates_pass(self, service):
        gates = [_ready_gate(), _healthy_gate()]
        executor = JobExecutor(service, _noop_skill, gates)
        result = executor.execute_job("test_job")
        assert result.executed is True

    def test_second_gate_fails(self, service):
        gates = [
            _ready_gate(),
            Gate("local_llm", lambda: False, "LLM not healthy"),
        ]
        executor = JobExecutor(service, _noop_skill, gates)
        result = executor.execute_job("test_job")
        assert result.skipped is True
        assert "LLM" in result.skip_reason

    def test_gate_exception_skips(self, service):
        def error_gate():
            raise RuntimeError("gate error")

        gates = [Gate("broken", error_gate, "Gate broken")]
        executor = JobExecutor(service, _noop_skill, gates)
        result = executor.execute_job("test_job")
        assert result.skipped is True


# ===================================================================
# Disabled jobs
# ===================================================================

class TestDisabledJobs:

    def test_disabled_job_skipped(self, service):
        service.disable_job("test_job")
        executor = JobExecutor(service, _noop_skill, [_ready_gate()])
        result = executor.execute_job("test_job")
        assert result.skipped is True
        assert "disabled" in result.skip_reason.lower()


# ===================================================================
# Approvals
# ===================================================================

class TestApprovals:

    def test_job_with_approvals_skipped(self, tmp_path):
        config_dir = _write_config(tmp_path, jobs=[{
            "id": "approval_job",
            "name": "Approval Job",
            "trigger": {"type": "interval", "seconds": 60},
            "enabled": True,
            "requires_approvals": ["owner"],
            "timeout_s": 30,
            "skill": "echo",
        }])
        data_dir = str(tmp_path / "data")
        svc = SchedulerService(data_dir=data_dir, config_dir=config_dir)
        svc.register_from_config()

        executor = JobExecutor(svc, _noop_skill, [_ready_gate()])
        result = executor.execute_job("approval_job")
        assert result.skipped is True
        assert "approval" in result.skip_reason.lower()


# ===================================================================
# Skill execution failure
# ===================================================================

class TestSkillFailure:

    def test_skill_failure_emits_receipt(self, service):
        def failing_skill(name, inputs):
            raise ValueError("skill error")

        executor = JobExecutor(service, failing_skill, [_ready_gate()])
        result = executor.execute_job("test_job")
        assert result.executed is True
        assert result.success is False
        assert result.receipt["event"] == "scheduled_job_failed"


# ===================================================================
# Job not found
# ===================================================================

class TestJobNotFound:

    def test_nonexistent_job_skipped(self, service):
        executor = JobExecutor(service, _noop_skill, [_ready_gate()])
        result = executor.execute_job("nonexistent")
        assert result.skipped is True
        assert "not found" in result.skip_reason.lower()
