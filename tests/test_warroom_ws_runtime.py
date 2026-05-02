import asyncio
import json
import sys
import types

import pytest
from fastapi import WebSocketDisconnect

from src.core import warroom_ws


class FakeWebSocket:
    def __init__(self, messages=None, cookies=None, fail_send=False):
        self.messages = list(messages or [])
        self.cookies = cookies or {}
        self.fail_send = fail_send
        self.accepted = False
        self.sent = []
        self.closed = None

    async def accept(self):
        self.accepted = True

    async def send_text(self, data):
        if self.fail_send:
            raise RuntimeError("socket closed")
        self.sent.append(json.loads(data))

    async def receive_text(self):
        if not self.messages:
            raise WebSocketDisconnect(code=1000)
        item = self.messages.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def close(self, code=None, reason=None):
        self.closed = {"code": code, "reason": reason}


@pytest.mark.asyncio
async def test_connection_manager_broadcasts_and_prunes_stale_sockets():
    manager = warroom_ws.ConnectionManager()
    good = FakeWebSocket()
    stale = FakeWebSocket(fail_send=True)

    await manager.connect(good)
    await manager.register_accepted(stale)
    await manager.broadcast({"type": "runtime_event", "payload": {"ok": True}})

    assert good.accepted is True
    assert good.sent == [{"type": "runtime_event", "payload": {"ok": True}}]
    assert manager.active_count == 1

    await manager.disconnect(good)
    assert manager.active_count == 0


def test_verify_ws_token_accepts_dev_mode_api_token_and_session(monkeypatch):
    monkeypatch.setenv("LANCELOT_DEV_MODE", "true")
    assert warroom_ws._verify_ws_token("") is True
    monkeypatch.delenv("LANCELOT_DEV_MODE")

    secret_cache = types.SimpleNamespace(get=lambda key, default="": "api-secret")
    monkeypatch.setitem(sys.modules, "secret_cache", secret_cache)
    assert warroom_ws._verify_ws_token("api-secret") is True

    auth_api = types.ModuleType("src.core.auth_api")
    auth_api.verify_warroom_session_token = lambda token: token == "session-secret"
    monkeypatch.setitem(sys.modules, "src.core.auth_api", auth_api)
    assert warroom_ws._verify_ws_token("session-secret") is True
    assert warroom_ws._verify_ws_token("wrong") is False


def test_verify_ws_cookie_uses_session_cookie(monkeypatch):
    auth_api = types.ModuleType("src.core.auth_api")
    auth_api.get_warroom_session_cookie_name = lambda: "wr_session"
    auth_api.verify_warroom_session_token = lambda token: token == "cookie-secret"
    monkeypatch.setitem(sys.modules, "src.core.auth_api", auth_api)

    assert warroom_ws._verify_ws_cookie(FakeWebSocket(cookies={"wr_session": "cookie-secret"})) is True
    assert warroom_ws._verify_ws_cookie(FakeWebSocket(cookies={"wr_session": "bad"})) is False


@pytest.mark.asyncio
async def test_warroom_websocket_authenticates_with_cookie_and_answers_ping(monkeypatch):
    manager = warroom_ws.ConnectionManager()
    monkeypatch.setattr(warroom_ws, "connection_manager", manager)
    monkeypatch.setattr(warroom_ws, "_verify_ws_cookie", lambda websocket: True)

    ws = FakeWebSocket(messages=[json.dumps({"type": "ping"})])

    await warroom_ws.warroom_websocket(ws)

    assert ws.accepted is True
    assert ws.sent == [{"type": "auth_ok"}, {"type": "pong"}]
    assert manager.active_count == 0


@pytest.mark.asyncio
async def test_warroom_websocket_token_handshake_and_unknown_message(monkeypatch, caplog):
    manager = warroom_ws.ConnectionManager()
    monkeypatch.setattr(warroom_ws, "connection_manager", manager)
    monkeypatch.setattr(warroom_ws, "_verify_ws_cookie", lambda websocket: False)
    monkeypatch.setattr(warroom_ws, "_verify_ws_token", lambda token: token == "bearer")

    ws = FakeWebSocket(
        messages=[
            json.dumps({"type": "auth", "token": "bearer"}),
            json.dumps({"type": "client_metric"}),
        ]
    )

    await warroom_ws.warroom_websocket(ws)

    assert ws.sent == [{"type": "auth_ok"}]
    assert manager.active_count == 0


@pytest.mark.asyncio
async def test_warroom_websocket_rejects_bad_handshakes(monkeypatch):
    monkeypatch.setattr(warroom_ws, "_verify_ws_cookie", lambda websocket: False)
    monkeypatch.setattr(warroom_ws, "_verify_ws_token", lambda token: False)

    ws = FakeWebSocket(messages=[json.dumps({"type": "auth", "token": "bad"})])

    await warroom_ws.warroom_websocket(ws)

    assert ws.sent[0]["type"] == "auth_error"
    assert ws.closed["code"] == 4401


@pytest.mark.asyncio
async def test_warroom_websocket_dev_ping_handshake_and_invalid_json(monkeypatch, caplog):
    manager = warroom_ws.ConnectionManager()
    monkeypatch.setattr(warroom_ws, "connection_manager", manager)
    monkeypatch.setattr(warroom_ws, "_verify_ws_cookie", lambda websocket: False)
    monkeypatch.setattr(warroom_ws, "_verify_ws_token", lambda token: token == "")

    ws = FakeWebSocket(messages=[json.dumps({"type": "ping"}), "{not-json"])

    await warroom_ws.warroom_websocket(ws)

    assert ws.sent == [{"type": "auth_ok"}]
    assert manager.active_count == 0
