from __future__ import annotations

import logging

import pytest

from src.core.subsystem_manager import SubsystemManager


def test_subsystem_manager_registers_starts_and_reports_status() -> None:
    started = []
    manager = SubsystemManager()

    manager.register(
        "memory",
        "FEATURE_MEMORY",
        lambda: started.append("memory") or {"store": object()},
        lambda objects: None,
        route_prefixes=["/api/memory"],
    )

    objects = manager.start("memory")
    second_start = manager.start("memory")

    assert started == ["memory"]
    assert second_start is objects
    assert manager.is_running("memory") is True
    assert manager.get_by_flag("FEATURE_MEMORY").name == "memory"
    assert manager.status()["memory"] == {
        "flag": "FEATURE_MEMORY",
        "running": True,
        "route_prefixes": ["/api/memory"],
    }


def test_subsystem_manager_stop_clears_objects() -> None:
    stopped = []
    manager = SubsystemManager()
    manager.register(
        "scheduler",
        "FEATURE_SCHEDULER",
        lambda: {"runner": "active"},
        lambda objects: stopped.append(objects["runner"]),
    )

    manager.start("scheduler")
    manager.stop("scheduler")
    manager.stop("scheduler")

    entry = manager.get("scheduler")
    assert stopped == ["active"]
    assert entry.running is False
    assert entry.objects == {}


def test_subsystem_manager_handles_shutdown_failure(caplog) -> None:
    manager = SubsystemManager()
    manager.register(
        "hive",
        "FEATURE_HIVE",
        lambda: {"mesh": "active"},
        lambda objects: (_ for _ in ()).throw(RuntimeError("stop failed")),
    )
    manager.start("hive")

    with caplog.at_level(logging.ERROR):
        manager.stop("hive")

    assert manager.is_running("hive") is False
    assert "Failed to stop subsystem 'hive'" in caplog.text


def test_subsystem_manager_rejects_unknown_subsystems() -> None:
    manager = SubsystemManager()

    with pytest.raises(ValueError, match="Unknown subsystem"):
        manager.start("missing")
    with pytest.raises(ValueError, match="Unknown subsystem"):
        manager.stop("missing")


def test_subsystem_manager_duplicate_registration_keeps_original(caplog) -> None:
    manager = SubsystemManager()
    manager.register("soul", "FEATURE_SOUL", lambda: {"version": 1}, lambda objects: None)

    with caplog.at_level(logging.WARNING):
        manager.register("soul", "FEATURE_OTHER", lambda: {"version": 2}, lambda objects: None)

    assert manager.start("soul") == {"version": 1}
    assert manager.get_by_flag("FEATURE_OTHER") is None
    assert "already registered" in caplog.text


def test_shutdown_manifest_does_not_reference_removed_bal() -> None:
    from src.core.shutdown import SUBSYSTEM_SHUTDOWN_MANIFEST

    assert "bal" not in {task.name for task in SUBSYSTEM_SHUTDOWN_MANIFEST}
