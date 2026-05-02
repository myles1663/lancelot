"""
Tests for src.core.scheduler.executor — Job execution pipeline (Prompt 13 / D4-D6).
"""

import pytest
import yaml
import logging
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace

from src.core.scheduler.service import SchedulerService
from src.core.scheduler import executor as executor_module
from src.core.scheduler.executor import JobExecutor, Gate, JobExecutionResult, _cron_matches, _scheduled_skill_failure_reason
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
        job = service.get_job("test_job")
        assert job.last_run_status == "failed"
        assert "skill error" in job.last_run_error

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
        job = service.get_job("test_job")
        assert job.last_run_status == "failed"
        assert "return_code=2" in job.last_run_error


# ===================================================================
# Job not found
# ===================================================================

class TestJobNotFound:

    def test_nonexistent_job_skipped(self, service, runtime_pause_state):
        executor = JobExecutor(service, _noop_skill, [_ready_gate()])
        result = executor.execute_job("nonexistent")
        assert result.skipped is True
        assert "not found" in result.skip_reason.lower()


class TestSchedulerRuntimeInternals:
    def test_failure_reason_handles_none_status_error_and_bad_return_codes(self):
        assert _scheduled_skill_failure_reason(None) is None
        assert _scheduled_skill_failure_reason({"status": "error"}) == "Scheduled skill returned status=error"
        assert _scheduled_skill_failure_reason({"return_code": "bad", "stdout": "output"}) == (
            "Scheduled command exited with return_code=1: output"
        )
        assert _scheduled_skill_failure_reason({"return_code": 0}) is None

    def test_cron_matcher_supports_lists_ranges_wildcards_and_invalid_values(self):
        now = datetime(2026, 5, 3, 9, 30, tzinfo=timezone.utc)  # Sunday

        assert _cron_matches("30 9 3 5 0", now) is True
        assert _cron_matches("29,30 8-10 * * 0", now) is True
        assert _cron_matches("31 9 * * *", now) is False
        assert _cron_matches("* * * *", now) is False
        assert _cron_matches("bad * * * *", now) is False

    def test_load_and_save_approval_state_error_paths(self, tmp_path, caplog, runtime_pause_state):
        config_dir = _write_config(tmp_path)
        svc = SchedulerService(data_dir=str(tmp_path / "data"), config_dir=config_dir)
        svc.register_from_config()
        approvals = Path(svc.data_dir) / "scheduler_approvals.json"
        approvals.write_text("{bad json", encoding="utf-8")

        with caplog.at_level(logging.WARNING):
            executor = JobExecutor(svc, _noop_skill, [_ready_gate()])
        assert executor.pending_approvals == {}
        assert "Failed to load scheduler approval state" in caplog.text

        executor._approvals_file = tmp_path / "missing" / "dir" / "state.json"
        executor._approvals_file.parent.mkdir(parents=True)
        executor._approvals_file.parent.rmdir()
        with caplog.at_level(logging.WARNING):
            executor._save_approval_state()

    def test_tick_loop_start_stop_and_already_running(self, service, monkeypatch, runtime_pause_state, caplog):
        executor = JobExecutor(service, _noop_skill, [_ready_gate()])
        monkeypatch.setattr(executor, "_tick_loop", lambda: None)

        executor.start_tick_loop()
        executor.stop()

        assert executor.is_running is False

        class AliveThread:
            def is_alive(self):
                return True

            def join(self, timeout=None):
                self.timeout = timeout

        executor._tick_thread = AliveThread()
        with caplog.at_level(logging.WARNING):
            executor.start_tick_loop()
        assert "Tick loop already running" in caplog.text

    def test_tick_loop_catches_tick_errors_and_stops_responsively(self, service, monkeypatch, runtime_pause_state, caplog):
        executor = JobExecutor(service, _noop_skill, [_ready_gate()])
        calls = {"count": 0}

        def broken_tick():
            calls["count"] += 1
            executor._stop_event.set()
            raise RuntimeError("tick failed")

        monkeypatch.setattr(executor, "_tick", broken_tick)

        with caplog.at_level(logging.ERROR):
            executor._tick_loop()

        assert calls["count"] == 1
        assert "Scheduler tick error" in caplog.text

    def test_tick_skips_when_runtime_paused(self, service, tmp_path, runtime_pause_state):
        init_runtime_pause(str(tmp_path / "pause"))
        pause_runtime("paused", operator_id="op", operator_name="Operator", session_id="s")
        executor = JobExecutor(service, _noop_skill, [_ready_gate()])
        executor.execute_job = lambda job_id: pytest.fail("paused scheduler should not execute")

        try:
            executor._tick()
        finally:
            resume_runtime(operator_id="op", operator_name="Operator", session_id="s")

        assert service.last_scheduler_tick_at is not None

    def test_tick_fires_due_cron_and_interval_jobs(self, tmp_path, runtime_pause_state):
        now = datetime.now(timezone.utc)
        config_dir = _write_config(tmp_path)
        svc = SchedulerService(data_dir=str(tmp_path / "data"), config_dir=config_dir)
        svc.register_from_config()
        jobs = [
            SimpleNamespace(id="cron_due", enabled=True, skill="echo", trigger_type="cron", trigger_value=f"{now.minute} {now.hour} * * *", timezone="UTC", last_run_at=None),
            SimpleNamespace(id="interval_due", enabled=True, skill="echo", trigger_type="interval", trigger_value="1", timezone="UTC", last_run_at=(now - timedelta(seconds=5)).isoformat()),
            SimpleNamespace(id="interval_bad", enabled=True, skill="echo", trigger_type="interval", trigger_value="bad", timezone="UTC", last_run_at=None),
            SimpleNamespace(id="disabled", enabled=False, skill="echo", trigger_type="interval", trigger_value="1", timezone="UTC", last_run_at=None),
            SimpleNamespace(id="no_skill", enabled=True, skill="", trigger_type="interval", trigger_value="1", timezone="UTC", last_run_at=None),
        ]
        svc.list_jobs = lambda: jobs
        fired = []
        executor = JobExecutor(svc, _noop_skill, [_ready_gate()])
        executor.execute_job = lambda job_id: fired.append(job_id)

        executor._tick()

        assert fired == ["cron_due", "interval_due"]

    def test_tick_does_not_double_fire_same_cron_minute_and_ignores_malformed_last_run(self, tmp_path, runtime_pause_state):
        now = datetime.now(timezone.utc)
        config_dir = _write_config(tmp_path)
        svc = SchedulerService(data_dir=str(tmp_path / "data"), config_dir=config_dir)
        svc.register_from_config()
        jobs = [
            SimpleNamespace(id="already_ran", enabled=True, skill="echo", trigger_type="cron", trigger_value=f"{now.minute} {now.hour} * * *", timezone="UTC", last_run_at=now.isoformat()),
            SimpleNamespace(id="malformed_last_run", enabled=True, skill="echo", trigger_type="cron", trigger_value=f"{now.minute} {now.hour} * * *", timezone="UTC", last_run_at="not-a-date"),
            SimpleNamespace(id="interval_malformed", enabled=True, skill="echo", trigger_type="interval", trigger_value="1", timezone="UTC", last_run_at="not-a-date"),
        ]
        svc.list_jobs = lambda: jobs
        fired = []
        executor = JobExecutor(svc, _noop_skill, [_ready_gate()])
        executor.execute_job = lambda job_id: fired.append(job_id)

        executor._tick()

        assert fired == ["malformed_last_run", "interval_malformed"]

    def test_concurrent_execution_is_skipped_with_identity_receipt(self, service, runtime_pause_state):
        executor = JobExecutor(service, _noop_skill, [_ready_gate()])
        lock = executor._get_job_lock("test_job")
        lock.acquire()
        try:
            result = executor.execute_job_with_identity(
                "test_job",
                operator_id="op-1",
                session_id="s-1",
                actor="Operator",
            )
        finally:
            lock.release()

        assert result.skipped is True
        assert "Already running" in result.skip_reason
        assert result.receipt["operator_id"] == "op-1"
        assert result.receipt["actor"] == "Operator"

    def test_missing_skill_binding_and_record_failure_warning(self, tmp_path, monkeypatch, caplog, runtime_pause_state):
        config_dir = _write_config(tmp_path, jobs=[{
            "id": "missing_skill",
            "name": "Missing Skill",
            "trigger": {"type": "interval", "seconds": 60},
            "enabled": True,
        }])
        svc = SchedulerService(data_dir=str(tmp_path / "data"), config_dir=config_dir)
        svc.register_from_config()
        executor = JobExecutor(svc, _noop_skill, [_ready_gate()])
        monkeypatch.setattr(svc, "record_job_result", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("write failed")))

        with caplog.at_level(logging.WARNING):
            result = executor.execute_job("missing_skill")

        assert result.success is False
        assert "missing a skill binding" in result.error
        assert "Failed to persist scheduler failure" in caplog.text

    def test_approval_request_is_idempotent_and_clear_empty_noops(self, tmp_path, runtime_pause_state):
        config_dir = _write_config(tmp_path)
        svc = SchedulerService(data_dir=str(tmp_path / "data"), config_dir=config_dir)
        svc.register_from_config()
        executor = JobExecutor(svc, _noop_skill, [_ready_gate()])

        assert executor.clear_approval_state() == {"pending_cleared": 0, "granted_cleared": 0}
        executor._request_approval("job-1", "Job 1", ["owner"])
        first_requested = executor.pending_approvals["job-1"]["requested_at"]
        executor._request_approval("job-1", "Job 1", ["owner"])

        assert executor.pending_approvals["job-1"]["requested_at"] == first_requested
        assert executor.approve_job("missing") is False

    def test_approval_event_bus_success_and_failure_paths(self, service, monkeypatch, runtime_pause_state, caplog):
        emitted = []

        class Event:
            def __init__(self, type, payload):
                self.type = type
                self.payload = payload

        class Bus:
            async def emit(self, event):
                emitted.append(event)

        loop = asyncio_loop = __import__("asyncio").new_event_loop()
        monkeypatch.setattr(executor_module, "_HAS_EVENT_BUS", True)
        monkeypatch.setattr(executor_module, "Event", Event, raising=False)
        monkeypatch.setattr(executor_module, "event_bus", Bus(), raising=False)
        monkeypatch.setattr(__import__("asyncio"), "get_event_loop", lambda: loop)

        executor = JobExecutor(service, _noop_skill, [_ready_gate()])
        executor._request_approval("job-1", "Job 1", ["owner"])

        assert emitted[0].type == "scheduler_approval_required"
        assert emitted[0].payload["job_id"] == "job-1"

        class BrokenBus:
            async def emit(self, event):
                raise RuntimeError("event failed")

        monkeypatch.setattr(executor_module, "event_bus", BrokenBus(), raising=False)
        with caplog.at_level(logging.WARNING):
            executor._request_approval("job-2", "Job 2", ["owner"])

        assert "Failed to emit approval request event" in caplog.text
        asyncio_loop.close()
