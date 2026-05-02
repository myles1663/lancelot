import asyncio
import importlib
import logging
import sqlite3
import tempfile
import types
import urllib.error

import pytest
from src.a2a import server as a2a_server
from src.agents.antigravity_engine import AntigravityEngine, EngineMode
from src.core.flagship_client import FlagshipClient, FlagshipError
from src.core.provider_profile import LaneConfig, ProviderProfile
from src.core.scheduler.service import SchedulerService


def test_antigravity_engine_stop_logs_browser_session_close_failure(caplog):
    class _BrokenSession:
        async def close(self):
            raise RuntimeError("session close exploded")

    engine = AntigravityEngine.__new__(AntigravityEngine)
    engine._save_session = lambda: asyncio.sleep(0)
    engine._browser_use_session = _BrokenSession()
    engine.mode = EngineMode.ISOLATED
    engine.playwright = None
    engine.context = None
    engine.browser = None

    with caplog.at_level(logging.WARNING):
        asyncio.run(engine.stop())

    assert "Failed to close browser-use session cleanly" in caplog.text


def test_a2a_kill_switch_logs_flag_lookup_failure(caplog, monkeypatch):
    def _raise_flags():
        raise RuntimeError("flags exploded")

    monkeypatch.setattr(a2a_server, "logger", a2a_server.logger)
    feature_flags = types.ModuleType("src.core.feature_flags")
    feature_flags.get_all_flags = _raise_flags
    monkeypatch.setitem(importlib.import_module("sys").modules, "src.core.feature_flags", feature_flags)

    with caplog.at_level(logging.WARNING):
        assert a2a_server._check_a2a_kill_switch() is True

    assert "Failed to inspect A2A feature flags; leaving A2A enabled" in caplog.text


def test_vault_logs_permission_hardening_failure(caplog, monkeypatch, tmp_path):
    vault_module = importlib.import_module("src.memory.vault")
    monkeypatch.setattr(vault_module.sys, "platform", "linux")
    monkeypatch.setattr(vault_module.os, "chmod", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("chmod exploded")))

    with caplog.at_level(logging.WARNING):
        vault = vault_module.SecretVault(data_dir=str(tmp_path))

    assert vault is not None
    assert "Failed to restrict vault key permissions" in caplog.text


def test_librarian_logs_memory_summary_write_failure(caplog, monkeypatch, tmp_path):
    librarian_module = importlib.import_module("src.memory.librarian")
    librarian = librarian_module.Librarian(data_dir=str(tmp_path))

    def _broken_open(*_args, **_kwargs):
        raise RuntimeError("write exploded")

    monkeypatch.setattr(librarian_module, "open", _broken_open, raising=False)

    with caplog.at_level(logging.WARNING):
        librarian._update_memory_summary("note.txt", "summary", "notes")

    assert "Failed to update memory summary for note.txt" in caplog.text


def test_playbook_loader_logs_malformed_variant_step(caplog, tmp_path):
    playbooks_module = importlib.import_module("src.incidents.playbooks")
    playbook_path = tmp_path / "playbook.yaml"
    playbook_path.write_text(
        """
_playbook_metadata:
  name: test-playbook
  display_name: Test Playbook
steps:
  - step: 1
    title: Contain
    description: Primary response
variant_steps:
  - step: 2
    action_type: MANUAL
        """.strip(),
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING):
        loaded = playbooks_module._load_single(str(playbook_path))

    assert loaded is not None
    assert "Skipping malformed incident playbook variant step" in caplog.text


def test_scheduler_row_to_record_logs_missing_timezone(caplog, tmp_path):
    service = SchedulerService(data_dir=str(tmp_path / "data"), config_dir=str(tmp_path / "config"))
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT
            'job-1' AS id,
            'Job' AS name,
            'skill.name' AS skill,
            '{}' AS inputs,
            1 AS enabled,
            'cron' AS trigger_type,
            '* * * * *' AS trigger_value,
            1 AS requires_ready,
            '[]' AS requires_approvals,
            300 AS timeout_s,
            'desc' AS description,
            NULL AS last_run_at,
            NULL AS last_run_status,
            0 AS run_count,
            '2026-04-17T00:00:00+00:00' AS registered_at
        """
    ).fetchone()

    try:
        with caplog.at_level(logging.DEBUG):
            record = service._row_to_record(row)
    finally:
        conn.close()

    assert record.timezone == "UTC"
    assert "Scheduler job row missing timezone; defaulting to UTC" in caplog.text


def test_flagship_client_logs_unreadable_http_error_body(caplog, monkeypatch):
    profile = ProviderProfile(
        name="gemini",
        display_name="Gemini",
        fast=LaneConfig(model="gemini-fast", max_tokens=256, temperature=0.2),
        deep=LaneConfig(model="gemini-deep", max_tokens=1024, temperature=0.1),
    )
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr("src.core.flagship_client.assert_url_allowed", lambda url, **kwargs: url)
    client = FlagshipClient("gemini", profile)

    class _BrokenHttpError(urllib.error.HTTPError):
        def read(self):
            raise RuntimeError("body read exploded")

    def _raise_http_error(*_args, **_kwargs):
        raise _BrokenHttpError(
            url="https://example.test",
            code=500,
            msg="boom",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr("src.core.flagship_client.urllib.request.urlopen", _raise_http_error)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(FlagshipError):
            client._http_post("https://example.test", {"prompt": "hi"}, timeout=1.0)

    assert "Failed to read HTTP error body from gemini provider response" in caplog.text
