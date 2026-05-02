from __future__ import annotations

import asyncio
import logging
import sys
import types

import pytest

from src.core import shutdown as shutdown_mod


class _Entry:
    def __init__(self, running=True, objects=None) -> None:
        self.running = running
        self.objects = objects or {}


class _SubsystemManager:
    def __init__(self) -> None:
        self.entries = {}
        self.stopped = []

    def get(self, name):
        return self.entries.get(name)

    def stop(self, name):
        self.stopped.append(name)
        entry = self.entries[name]
        if entry.objects.get("fail_stop"):
            raise RuntimeError("stop failed")
        entry.running = False


class _AsyncStopper:
    def __init__(self, fail=False) -> None:
        self.stopped = False
        self.fail = fail

    async def stop(self):
        if self.fail:
            raise RuntimeError("async stop failed")
        self.stopped = True


@pytest.mark.asyncio
async def test_run_sync_shutdown_step_logs_failures(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        await shutdown_mod._run_sync_shutdown_step(
            "failing",
            1.0,
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )

    assert "Shutdown step 'failing' failed: boom" in caplog.text


@pytest.mark.asyncio
async def test_federation_transport_pre_stop_stops_components(monkeypatch, caplog) -> None:
    manager = _SubsystemManager()
    transport = _AsyncStopper()
    heartbeat_mesh = _AsyncStopper(fail=True)
    cost_reporter = _AsyncStopper()
    manager.entries["federation"] = _Entry(
        objects={
            "transport": transport,
            "heartbeat_mesh": heartbeat_mesh,
            "cost_reporter": cost_reporter,
        }
    )
    monkeypatch.setattr(shutdown_mod, "subsystem_manager", manager, raising=False)

    with caplog.at_level(logging.WARNING):
        await shutdown_mod._shutdown_federation_transport()

    assert transport.stopped is True
    assert cost_reporter.stopped is True
    assert "Federation component heartbeat_mesh stop failed" in caplog.text


@pytest.mark.asyncio
async def test_federation_transport_pre_stop_noops_when_not_running(monkeypatch) -> None:
    manager = _SubsystemManager()
    manager.entries["federation"] = _Entry(running=False)
    monkeypatch.setattr(shutdown_mod, "subsystem_manager", manager, raising=False)

    await shutdown_mod._shutdown_federation_transport()


@pytest.mark.asyncio
async def test_shutdown_stops_registered_runtime_components(monkeypatch) -> None:
    manager = _SubsystemManager()
    for task in shutdown_mod.SUBSYSTEM_SHUTDOWN_MANIFEST:
        manager.entries[task.name] = _Entry()
    monkeypatch.setattr(shutdown_mod, "subsystem_manager", manager, raising=False)

    librarian = types.SimpleNamespace(stopped=False)
    librarian.stop = lambda: setattr(librarian, "stopped", True)
    antigravity = types.SimpleNamespace(stopped=False)
    antigravity.stop = lambda: asyncio.sleep(0)
    telegram_bot = types.SimpleNamespace(stopped=False)
    telegram_bot.stop_polling = lambda: setattr(telegram_bot, "stopped", True)
    chat_poller = types.SimpleNamespace(stopped=False)
    chat_poller.stop_polling = lambda: setattr(chat_poller, "stopped", True)
    usage_persistence = types.SimpleNamespace(flushed=False)
    usage_persistence.flush = lambda: setattr(usage_persistence, "flushed", True)
    orchestrator = types.SimpleNamespace(
        usage_tracker=types.SimpleNamespace(_persistence=usage_persistence),
        audit_logger=types.SimpleNamespace(events=[], log_event=lambda *args: orchestrator.audit_logger.events.append(args)),
    )
    oauth_manager = types.SimpleNamespace(stopped=False)
    google_manager = types.SimpleNamespace(stopped=False)
    monkeypatch.setitem(
        sys.modules,
        "oauth_token_manager",
        types.SimpleNamespace(
            get_oauth_manager=lambda: oauth_manager,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "google_oauth_manager",
        types.SimpleNamespace(
            get_google_oauth_manager=lambda: google_manager,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "observability.otel_provider",
        types.SimpleNamespace(shutdown_otel=lambda: setattr(google_manager, "otel_shutdown", True)),
    )
    oauth_manager.stop_background_refresh = lambda: setattr(oauth_manager, "stopped", True)
    google_manager.stop_background_refresh = lambda: setattr(google_manager, "stopped", True)

    monkeypatch.setattr(shutdown_mod, "librarian", librarian, raising=False)
    monkeypatch.setattr(shutdown_mod, "antigravity", antigravity, raising=False)
    monkeypatch.setattr(shutdown_mod, "telegram_bot", telegram_bot, raising=False)
    monkeypatch.setattr(shutdown_mod, "chat_poller", chat_poller, raising=False)
    monkeypatch.setattr(shutdown_mod, "main_orchestrator", orchestrator, raising=False)

    await shutdown_mod.shutdown(app=object(), boot_result=object())

    assert set(manager.stopped) == {task.name for task in shutdown_mod.SUBSYSTEM_SHUTDOWN_MANIFEST}
    assert librarian.stopped is True
    assert telegram_bot.stopped is True
    assert chat_poller.stopped is True
    assert usage_persistence.flushed is True
    assert oauth_manager.stopped is True
    assert google_manager.stopped is True
    assert orchestrator.audit_logger.events[0][0] == "GATEWAY_SHUTDOWN"


@pytest.mark.asyncio
async def test_shutdown_swallows_top_level_errors(monkeypatch, caplog) -> None:
    monkeypatch.setattr(
        shutdown_mod,
        "_run_sync_shutdown_step",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("outer boom")),
    )

    with caplog.at_level(logging.ERROR):
        await shutdown_mod.shutdown(app=object(), boot_result=object())

    assert "Shutdown error: outer boom" in caplog.text
