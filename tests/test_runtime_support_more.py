import asyncio
import json
import socket
import sys
import types
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def test_update_checker_success_dismissal_and_error_classification(monkeypatch, tmp_path):
    from src.core import update_checker

    version_file = tmp_path / "VERSION"
    version_file.write_text("1.0.0", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps({
                "latest": "1.1.0",
                "severity": "recommended",
                "message": "upgrade available",
                "changelog_url": "https://example.com/changelog",
                "released_at": "2026-05-01",
            }).encode("utf-8")

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: Response())
    checker = update_checker.UpdateChecker(check_interval=60)
    status = checker.force_check()
    assert status["current_version"] == "1.0.0"
    assert status["update_available"] is True
    assert status["show_banner"] is True
    assert checker.dismiss() is True
    assert checker.get_update_status()["show_banner"] is False

    checker._dismissed_at = update_checker.time.time() - update_checker.DISMISS_REAPPEAR_SECONDS - 1
    assert checker.get_update_status()["show_banner"] is True
    checker._severity = "critical"
    assert checker.dismiss() is False

    assert update_checker._classify_check_error(update_checker.OutboundNetworkError("blocked")) == "blocked_by_policy"
    assert update_checker._classify_check_error(ValueError("bad json")) == "manifest_parse_error"
    assert update_checker._is_expected_network_failure(urllib.error.URLError(OSError(111, "refused"))) is True
    assert update_checker._is_expected_network_failure(socket.gaierror(-2, "dns")) is True
    assert update_checker._is_expected_network_failure(RuntimeError("other")) is False


def test_librarian_v2_trash_organize_and_handler_paths(tmp_path, monkeypatch):
    from src.memory import librarian_v2

    trash = librarian_v2.TrashService(str(tmp_path))
    source = tmp_path / "operator-note.txt"
    source.write_text("review this", encoding="utf-8")
    assert trash.soft_delete(str(source), "cleanup") is True
    metadata = list((tmp_path / ".trash").glob("*.metadata"))[0]
    meta = json.loads(metadata.read_text(encoding="utf-8"))
    assert meta["reason"] == "cleanup"
    meta["expires_at"] = (datetime.utcnow() - timedelta(seconds=1)).isoformat()
    metadata.write_text(json.dumps(meta), encoding="utf-8")
    trash.cleanup()
    assert not metadata.exists()

    failed_source = tmp_path / "missing.txt"
    assert trash.soft_delete(str(failed_source), "missing") is False

    librarian = librarian_v2.LibrarianV2(str(tmp_path))
    protected = tmp_path / "USER.md"
    protected.write_text("do not move", encoding="utf-8")
    tmp_file = tmp_path / "scratch.tmp"
    tmp_file.write_text("ignore", encoding="utf-8")
    work = tmp_path / "receipt.txt"
    work.write_text("file me", encoding="utf-8")
    collision_dir = tmp_path / "Unsorted"
    collision_dir.mkdir()
    (collision_dir / "receipt.txt").write_text("old", encoding="utf-8")
    monkeypatch.setattr(librarian_v2.time, "time", lambda: 123)

    asyncio.run(librarian._organize_file(str(protected)))
    asyncio.run(librarian._organize_file(str(tmp_file)))
    asyncio.run(librarian._organize_file(str(work)))

    assert protected.exists()
    assert tmp_file.exists()
    assert (collision_dir / "receipt_123.txt").read_text(encoding="utf-8") == "file me"
    assert "Filed receipt.txt" in (tmp_path / "librarian.log").read_text(encoding="utf-8")

    queued = []
    handler = librarian_v2.LibrarianHandler(
        queue=types.SimpleNamespace(put_nowait=lambda path: queued.append(path)),
        loop=types.SimpleNamespace(call_soon_threadsafe=lambda fn, arg: fn(arg)),
    )
    handler.on_created(types.SimpleNamespace(is_directory=False, src_path="new.txt"))
    handler.on_created(types.SimpleNamespace(is_directory=True, src_path="dir"))
    assert queued == ["new.txt"]


@pytest.mark.asyncio
async def test_live_session_connect_stream_and_close_paths(monkeypatch):
    from src.shared import live_session

    monkeypatch.setattr(live_session.types, "Part", lambda text: ("part", text))
    monkeypatch.setattr(live_session.types, "Content", lambda parts: ("content", parts))
    monkeypatch.setattr(live_session.types, "LiveConnectConfig", lambda **kwargs: kwargs)

    class Session:
        def __init__(self):
            self.sent = []
            self.closed = False

        async def send(self, **kwargs):
            self.sent.append(kwargs)

        async def receive(self):
            for text in ("one", "", "two"):
                yield types.SimpleNamespace(text=text)

        async def close(self):
            self.closed = True

    session = Session()
    client = types.SimpleNamespace(
        aio=types.SimpleNamespace(
            live=types.SimpleNamespace(connect=lambda **kwargs: asyncio.sleep(0, result=session))
        )
    )
    manager = live_session.LiveSessionManager(client, "gemini-live", "system")
    with pytest.raises(RuntimeError, match="not connected"):
        [chunk async for chunk in manager.send_text("hi")]

    assert await manager.connect() is session
    assert manager.is_connected is True
    assert [chunk async for chunk in manager.send_text("hi")] == ["one", "two"]
    assert session.sent[0] == {"input": "hi", "end_of_turn": True}
    await manager.close()
    assert session.closed is True
    assert manager.is_connected is False


def test_soul_panel_success_and_backend_down_paths(monkeypatch):
    from src.ui.panels.soul_panel import SoulPanel
    import src.ui.panels.soul_panel as soul_panel

    calls = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            calls.append("raise")

        def json(self):
            return self.payload

    monkeypatch.setattr(soul_panel.requests, "get", lambda url, headers, timeout: Response({
        "active_version": "v2",
        "available_versions": ["v1", "v2"],
        "pending_proposals": [{"id": "p1"}],
    }))
    monkeypatch.setattr(soul_panel.requests, "post", lambda url, headers, timeout: Response({"ok": url}))

    panel = SoulPanel(base_url="http://local/", token="token")
    assert panel._headers() == {"Authorization": "Bearer token"}
    rendered = panel.render_data()
    assert rendered["active_version"] == "v2"
    assert panel.approve_proposal("p1")["ok"].endswith("/soul/proposals/p1/approve")
    assert panel.activate_proposal("p1")["ok"].endswith("/soul/proposals/p1/activate")

    monkeypatch.setattr(soul_panel.requests, "get", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(soul_panel.requests, "post", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("down")))
    assert panel.get_status()["error"] == "Backend unavailable"
    assert "down" in panel.approve_proposal("p1")["error"]
    assert "down" in panel.activate_proposal("p1")["error"]


def test_incident_receipt_hook_configure_dedup_emit_and_swallow(monkeypatch, tmp_path):
    from src.incidents import receipt_hook

    trigger_engine = types.SimpleNamespace(triggers=[types.SimpleNamespace(playbook="pb", dedup_window_seconds=42)])
    store = types.SimpleNamespace()
    monkeypatch.setitem(
        sys.modules,
        "src.incidents.trigger_engine",
        types.SimpleNamespace(TriggerEngine=lambda: trigger_engine),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.incidents.store",
        types.SimpleNamespace(get_incident_store=lambda data_dir: store),
    )

    receipt_hook.configure(True, str(tmp_path))
    assert receipt_hook._enabled is True
    assert receipt_hook._trigger_engine is trigger_engine
    assert receipt_hook._store is store
    assert receipt_hook._get_dedup_window("pb") == 42
    assert receipt_hook._get_dedup_window("missing") == 300

    receipt_hook._trigger_engine = types.SimpleNamespace(
        triggers=[],
        evaluate=lambda receipt: types.SimpleNamespace(
            dedup_key="same",
            playbook_name="missing",
            trigger_receipt_id="r1",
        ),
    )
    receipt_hook._store = types.SimpleNamespace(
        find_by_dedup_key=lambda key, window_seconds: None,
        find_by_trigger_receipt=lambda receipt_id: "inc-existing",
        create=lambda incident: (_ for _ in ()).throw(AssertionError("duplicate should not create")),
    )
    receipt_hook._evaluate_receipt({"action_type": "tool"})

    receipt_hook._store = types.SimpleNamespace(
        update=lambda incident: (_ for _ in ()).throw(RuntimeError("pager down")),
    )
    receipt_hook._page_responders(types.SimpleNamespace(incident_id="inc-1", severity="high"))

    created_receipts = []
    monkeypatch.setitem(
        sys.modules,
        "src.shared.receipts",
        types.SimpleNamespace(
            ActionType=lambda value: value,
            CognitionTier=types.SimpleNamespace(DETERMINISTIC="deterministic"),
            create_receipt=lambda *args, **kwargs: {"args": args, "kwargs": kwargs},
            get_receipt_service=lambda: types.SimpleNamespace(create=lambda receipt: created_receipts.append(receipt)),
        ),
    )
    receipt_hook._emit_incident_receipt("incident_opened", {"incident_id": "inc-1"})
    assert created_receipts[0]["args"][0] == "incident_opened"

    receipt_hook._trigger_engine = types.SimpleNamespace(evaluate=lambda receipt: (_ for _ in ()).throw(RuntimeError("boom")))
    receipt_hook._store = object()
    receipt_hook._enabled = True
    receipt_hook.on_receipt_for_incidents({"action_type": "tool"})


def test_mcp_federation_ceiling_validation_apply_and_raw_soul_narrowing():
    from src.mcp.federation_ceiling import (
        apply_ceiling_to_evaluator,
        enforce_mcp_ceiling,
        narrow_soul_mcp_permissions,
        validate_child_within_ceiling,
    )
    from src.mcp.permissions import MCPPermissionEvaluator, MCPRiskTier, MCPServerPermission

    root = [
        MCPServerPermission("github", frozenset({"read"}), MCPRiskTier.T2),
        MCPServerPermission("stripe", frozenset({"*"}), MCPRiskTier.T1, wildcard=True),
    ]
    child = [
        MCPServerPermission("github", frozenset({"read", "write"}), MCPRiskTier.T0),
        MCPServerPermission("stripe", frozenset({"charge"}), MCPRiskTier.T2),
        MCPServerPermission("slack", frozenset({"post"}), MCPRiskTier.T1),
    ]

    violations = validate_child_within_ceiling(child, root)
    assert {v.violation_type for v in violations} == {
        "tool_escalation",
        "tier_escalation",
        "server_not_permitted",
    }
    enforced = enforce_mcp_ceiling(child, root)
    assert enforced.enforced is True
    assert enforced.to_dict()["violation_count"] >= 3
    assert [p.server_id for p in enforced.resulting_permissions] == ["github", "stripe"]
    assert enforced.resulting_permissions[0].risk_tier == MCPRiskTier.T2

    evaluator = MCPPermissionEvaluator()
    evaluator.load_permissions(child, soul_version="child-v1")
    result = apply_ceiling_to_evaluator(evaluator, root)
    assert result.enforced is True
    assert evaluator.get_allowed_tools("github") == {"read"}
    assert evaluator.soul_version == "child-v1"

    raw = narrow_soul_mcp_permissions(
        child_soul_data={
            "mcp_permissions": [
                {"server_id": "github", "allowed_tools": ["read", "write"], "risk_tier": "T0"},
                {"server_id": "slack", "allowed_tools": ["post"], "risk_tier": "T1"},
            ]
        },
        root_soul_data={
            "mcp_permissions": [
                {"server_id": "github", "allowed_tools": ["read"], "risk_tier": "T2"},
            ]
        },
    )
    assert raw["ceiling_enforced"] is True
    assert raw["mcp_permissions"] == [{"server_id": "github", "allowed_tools": ["read"], "risk_tier": "T2"}]
