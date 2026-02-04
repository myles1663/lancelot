"""
Tests for src.core.health.monitor — Health monitor loop (Prompt 10 / C3-C5).
"""

import pytest
from src.core.health.types import HealthSnapshot
from src.core.health.monitor import HealthMonitor, HealthCheck


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _always_true():
    return True

def _always_false():
    return False

def _raises():
    raise RuntimeError("check exploded")


def _make_monitor(checks=None):
    """Create a HealthMonitor with injected check functions."""
    if checks is None:
        checks = [
            HealthCheck("local_llm", _always_true, "Local LLM not responding"),
            HealthCheck("scheduler", _always_true, "Scheduler not running"),
            HealthCheck("onboarding_ready", _always_true, "Onboarding not ready"),
        ]
    return HealthMonitor(checks=checks, interval_s=0.1)


# ===================================================================
# compute_snapshot with injected checks
# ===================================================================

class TestComputeSnapshot:

    def test_all_healthy(self):
        """Blueprint requirement: unit test compute_snapshot using injected check functions."""
        monitor = _make_monitor()
        snapshot = monitor.compute_snapshot()
        assert snapshot.ready is True
        assert snapshot.degraded_reasons == []
        assert snapshot.local_llm_ready is True
        assert snapshot.scheduler_running is True

    def test_llm_down_degrades(self):
        checks = [
            HealthCheck("local_llm", _always_false, "Local LLM not responding"),
            HealthCheck("scheduler", _always_true, "Scheduler not running"),
            HealthCheck("onboarding_ready", _always_true, "Onboarding not ready"),
        ]
        monitor = _make_monitor(checks)
        snapshot = monitor.compute_snapshot()
        assert snapshot.ready is False
        assert "Local LLM" in snapshot.degraded_reasons[0]
        assert snapshot.local_llm_ready is False

    def test_scheduler_down_degrades(self):
        checks = [
            HealthCheck("local_llm", _always_true, "Local LLM not responding"),
            HealthCheck("scheduler", _always_false, "Scheduler not running"),
            HealthCheck("onboarding_ready", _always_true, "Onboarding not ready"),
        ]
        monitor = _make_monitor(checks)
        snapshot = monitor.compute_snapshot()
        assert snapshot.ready is False
        assert any("Scheduler" in r for r in snapshot.degraded_reasons)

    def test_onboarding_not_ready_degrades(self):
        checks = [
            HealthCheck("local_llm", _always_true),
            HealthCheck("scheduler", _always_true),
            HealthCheck("onboarding_ready", _always_false, "Onboarding not ready"),
        ]
        monitor = _make_monitor(checks)
        snapshot = monitor.compute_snapshot()
        assert snapshot.ready is False
        assert snapshot.onboarding_state == "NOT_READY"

    def test_all_down(self):
        checks = [
            HealthCheck("local_llm", _always_false, "LLM down"),
            HealthCheck("scheduler", _always_false, "Scheduler down"),
            HealthCheck("onboarding_ready", _always_false, "Not ready"),
        ]
        monitor = _make_monitor(checks)
        snapshot = monitor.compute_snapshot()
        assert snapshot.ready is False
        assert len(snapshot.degraded_reasons) == 3

    def test_check_exception_degrades(self):
        checks = [
            HealthCheck("local_llm", _raises, "LLM error"),
            HealthCheck("scheduler", _always_true),
        ]
        monitor = _make_monitor(checks)
        snapshot = monitor.compute_snapshot()
        assert snapshot.ready is False
        assert any("exploded" in r for r in snapshot.degraded_reasons)

    def test_last_health_tick_set(self):
        monitor = _make_monitor()
        snapshot = monitor.compute_snapshot()
        assert snapshot.last_health_tick_at is not None

    def test_no_checks_is_ready(self):
        monitor = _make_monitor(checks=[])
        snapshot = monitor.compute_snapshot()
        assert snapshot.ready is True


# ===================================================================
# Receipts on state transitions
# ===================================================================

class TestReceiptEmission:

    def test_initial_healthy_emits_health_ok(self):
        monitor = _make_monitor()
        monitor.compute_snapshot()
        assert any(r["event"] == "health_ok" for r in monitor.receipts)

    def test_initial_degraded_emits_health_degraded(self):
        checks = [HealthCheck("local_llm", _always_false, "LLM down")]
        monitor = _make_monitor(checks)
        monitor.compute_snapshot()
        assert any(r["event"] == "health_degraded" for r in monitor.receipts)

    def test_transition_to_degraded(self):
        call_count = [0]
        def toggle():
            call_count[0] += 1
            return call_count[0] <= 1  # True first, False second

        checks = [HealthCheck("local_llm", toggle, "LLM down")]
        monitor = _make_monitor(checks)
        monitor.compute_snapshot()  # healthy
        monitor.compute_snapshot()  # degraded
        events = [r["event"] for r in monitor.receipts]
        assert "health_degraded" in events

    def test_transition_to_recovered(self):
        call_count = [0]
        def toggle():
            call_count[0] += 1
            return call_count[0] != 1  # False first, True after

        checks = [HealthCheck("local_llm", toggle, "LLM down")]
        monitor = _make_monitor(checks)
        monitor.compute_snapshot()  # degraded
        monitor.compute_snapshot()  # recovered
        events = [r["event"] for r in monitor.receipts]
        assert "health_recovered" in events


# ===================================================================
# Background loop
# ===================================================================

class TestMonitorLoop:

    def test_start_and_stop(self):
        monitor = _make_monitor()
        monitor.start_monitor()
        import time
        time.sleep(0.3)  # Let it tick a few times
        monitor.stop_monitor()
        # Should have computed at least one snapshot
        assert monitor.latest_snapshot.last_health_tick_at is not None

    def test_start_is_idempotent(self):
        monitor = _make_monitor()
        monitor.start_monitor()
        monitor.start_monitor()  # Should not start second thread
        monitor.stop_monitor()


# ===================================================================
# Cached snapshot
# ===================================================================

class TestCachedSnapshot:

    def test_latest_snapshot_updated(self):
        monitor = _make_monitor()
        before = monitor.latest_snapshot
        monitor.compute_snapshot()
        after = monitor.latest_snapshot
        assert after.last_health_tick_at is not None
        assert after.timestamp != before.timestamp or after.last_health_tick_at is not None
