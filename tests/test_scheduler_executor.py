"""
Tests for src.core.scheduler.executor — Job execution pipeline (Prompt 13 / D4-D6).
"""

import pytest
import time
import yaml
from pathlib import Path

from src.core.scheduler.service import SchedulerService
from src.core.scheduler.executor import JobExecutor, Gate, JobExecutionResult
from src.core.runtime_pause import init_runtime_pause, pause_runtime, resume_runtime


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
def runtime_pause_state(tmp_path):
    init_runtime_pause(str(tmp_path / "runtime"))
    yield
    resume_runtime(
        operator_id="test",
        operator_name="Test",
        session_id="test",
        source="test",
    )


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

    def test_skipped_when_not_ready(self, service, runtime_pause_state):
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

    def test_skipped_emits_receipt(self, service, runtime_pause_state):
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

    def test_runs_when_ready(self, service, runtime_pause_state):
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

    def test_emits_scheduled_job_run_receipt(self, service, runtime_pause_state):
        executor = JobExecutor(
            service,
            skill_execute_fn=_noop_skill,
            gates=[_ready_gate()],
        )
        result = executor.execute_job("test_job")
        assert result.receipt["event"] == "scheduled_job_run"
        assert result.receipt["job_id"] == "test_job"

    def test_run_updates_scheduler_record(self, service, runtime_pause_state):
        executor = JobExecutor(
            service,
            skill_execute_fn=_noop_skill,
            gates=[_ready_gate()],
        )
        executor.execute_job("test_job")
        job = service.get_job("test_job")
        assert job.run_count == 1
        assert job.last_run_at is not None

    def test_skipped_when_runtime_paused(self, service, tmp_path, runtime_pause_state):
        init_runtime_pause(str(tmp_path))
        pause_runtime("Maintenance window", operator_id="op-1", operator_name="Arthur", session_id="session-1")
        try:
            executor = JobExecutor(
                service,
                skill_execute_fn=_noop_skill,
                gates=[_ready_gate()],
            )
            result = executor.execute_job("test_job")
            assert result.skipped is True
            assert "Maintenance window" in (result.skip_reason or "")
        finally:
            resume_runtime(operator_id="op-1", operator_name="Arthur", session_id="session-1")


# ===================================================================
# Multiple gates
# ===================================================================

class TestMultipleGates:

    def test_all_gates_pass(self, service, runtime_pause_state):
        gates = [_ready_gate(), _healthy_gate()]
        executor = JobExecutor(service, _noop_skill, gates)
        result = executor.execute_job("test_job")
        assert result.executed is True

    def test_second_gate_fails(self, service, runtime_pause_state):
        gates = [
            _ready_gate(),
            Gate("local_llm", lambda: False, "LLM not healthy"),
        ]
        executor = JobExecutor(service, _noop_skill, gates)
        result = executor.execute_job("test_job")
        assert result.skipped is True
        assert "LLM" in result.skip_reason

    def test_gate_exception_skips(self, service, runtime_pause_state):
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

    def test_disabled_job_skipped(self, service, runtime_pause_state):
        service.disable_job("test_job")
        executor = JobExecutor(service, _noop_skill, [_ready_gate()])
        result = executor.execute_job("test_job")
        assert result.skipped is True
        assert "disabled" in result.skip_reason.lower()


# ===================================================================
# Tick loop lifecycle
# ===================================================================

class TestTickLoopLifecycle:

    def test_start_is_idempotent(self, service, runtime_pause_state):
        executor = JobExecutor(service, _noop_skill, [_ready_gate()])
        executor.start_tick_loop()
        first_thread = executor._tick_thread
        executor.start_tick_loop()
        try:
            assert executor._tick_thread is first_thread
        finally:
            executor.stop()

    def test_stop_does_not_wait_for_full_tick_interval(self, service, runtime_pause_state):
        executor = JobExecutor(service, _noop_skill, [_ready_gate()])
        executor.start_tick_loop()
        assert executor.is_running is True

        started_at = time.perf_counter()
        executor.stop()
        elapsed_s = time.perf_counter() - started_at

        assert elapsed_s < 0.5
        assert executor.is_running is False
        assert executor._tick_thread is None


# ===================================================================
# Approvals
# ===================================================================

class TestApprovals:

    def test_job_with_approvals_skipped(self, tmp_path, runtime_pause_state):
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

    def test_pending_approval_survives_executor_restart(self, tmp_path, runtime_pause_state):
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

        first = JobExecutor(svc, _noop_skill, [_ready_gate()])
        result = first.execute_job("approval_job")
        assert result.skipped is True
        assert "approval_job" in first.pending_approvals

        reloaded = JobExecutor(svc, _noop_skill, [_ready_gate()])
        assert "approval_job" in reloaded.pending_approvals

    def test_granted_approval_survives_executor_restart(self, tmp_path, runtime_pause_state):
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

        first = JobExecutor(svc, _noop_skill, [_ready_gate()])
        first.execute_job("approval_job")
        assert first.approve_job("approval_job") is True

        reloaded = JobExecutor(svc, _noop_skill, [_ready_gate()])
        result = reloaded.execute_job("approval_job")
        assert result.executed is True
        assert result.success is True

    def test_clear_approval_state_clears_and_persists(self, tmp_path, runtime_pause_state):
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
        executor.execute_job("approval_job")
        assert executor.approve_job("approval_job") is True

        cleared = executor.clear_approval_state(
            reason="Federation full stop",
            operator_id="federation-peer",
            session_id="federation-peer",
            actor="Federation Peer",
        )

        assert cleared == {
            "pending_cleared": 1,
            "granted_cleared": 1,
        }
        assert executor.pending_approvals == {}

        reloaded = JobExecutor(svc, _noop_skill, [_ready_gate()])
        assert reloaded.pending_approvals == {}
        result = reloaded.execute_job("approval_job")
        assert result.skipped is True
        assert "approval" in (result.skip_reason or "").lower()


# ===================================================================
# Skill execution failure
# ===================================================================

class TestSkillFailure:

    def test_skill_failure_emits_receipt(self, service, runtime_pause_state):
        def failing_skill(name, inputs):
            raise ValueError("skill error")

        executor = JobExecutor(service, failing_skill, [_ready_gate()])
        result = executor.execute_job("test_job")
        assert result.executed is True
        assert result.success is False
        assert result.receipt["event"] == "scheduled_job_failed"

    def test_missing_skill_executor_fails_closed(self, service, runtime_pause_state):
        executor = JobExecutor(service, None, [_ready_gate()])
        result = executor.execute_job("test_job")
        assert result.executed is True
        assert result.success is False
        assert result.receipt["event"] == "scheduled_job_failed"
        assert "not configured" in result.error

    def test_skill_result_success_false_fails_closed(self, service, runtime_pause_state):
        def failed_result(name, inputs):
            return type("SkillResult", (), {"success": False, "error": "skill returned false"})()

        executor = JobExecutor(service, failed_result, [_ready_gate()])
        result = executor.execute_job("test_job")

        assert result.success is False
        assert result.receipt["event"] == "scheduled_job_failed"
        assert "skill returned false" in result.error

    def test_command_return_code_failure_fails_closed(self, service, runtime_pause_state):
        def failed_command(name, inputs):
            return type(
                "SkillResult",
                (),
                {
                    "success": True,
                    "outputs": {
                        "return_code": 2,
                        "stderr": "script failed",
                        "command": "python sync.py",
                    },
                },
            )()

        executor = JobExecutor(service, failed_command, [_ready_gate()])
        result = executor.execute_job("test_job")

        assert result.success is False
        assert result.receipt["event"] == "scheduled_job_failed"
        assert "return_code=2" in result.error


# ===================================================================
# Job not found
# ===================================================================

class TestJobNotFound:

    def test_nonexistent_job_skipped(self, service, runtime_pause_state):
        executor = JobExecutor(service, _noop_skill, [_ready_gate()])
        result = executor.execute_job("nonexistent")
        assert result.skipped is True
        assert "not found" in result.skip_reason.lower()
