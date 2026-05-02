import json
import sys
import types
from types import SimpleNamespace

from fastapi.responses import JSONResponse

from src.core import gateway_runtime_routes as routes


class FakeRequest:
    def __init__(self, payload=None):
        self._payload = payload or {}
        self.client = SimpleNamespace(host="127.0.0.1")
        self.state = SimpleNamespace()
        self.headers = {}

    async def json(self):
        return self._payload


class FakeUpload:
    def __init__(self, filename, content, content_type="text/plain"):
        self.filename = filename
        self._content = content
        self.content_type = content_type

    async def read(self):
        return self._content


def _body(response):
    if isinstance(response, JSONResponse):
        return json.loads(response.body.decode("utf-8"))
    return response


def _error_response(status, message, request_id=None):
    return JSONResponse(
        status_code=status,
        content={"error": message, "request_id": request_id},
    )


def _bind_base(**overrides):
    globals_payload = {
        "make_request_id": lambda: "req-1",
        "verify_token": lambda request: True,
        "rate_limiter": SimpleNamespace(check=lambda ip: True),
        "error_response": _error_response,
        "logger": SimpleNamespace(
            info=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
            exception=lambda *args, **kwargs: None,
        ),
        "_resolve_audit_user": lambda request: "Arthur",
        "_require_request_capability": lambda request, capability: None,
    }
    globals_payload.update(overrides)
    routes.bind_gateway_globals(**globals_payload)


def test_chat_with_files_auth_rate_pause_and_success(monkeypatch, tmp_path):
    class ChatAttachment:
        def __init__(self, filename, mime_type, data):
            self.filename = filename
            self.mime_type = mime_type
            self.data = data

    monkeypatch.setitem(sys.modules, "orchestrator", SimpleNamespace(ChatAttachment=ChatAttachment))
    monkeypatch.setattr("src.core.auth_api.resolve_authenticated_identity", lambda request: SimpleNamespace(operator_id="op-1"))
    monkeypatch.setattr("src.core.runtime_pause.is_runtime_paused", lambda: False)
    monkeypatch.setattr("src.core.runtime_pause.get_runtime_pause_status", lambda: {"reason": "paused"})
    workspace = tmp_path / "workspace"
    saved_paths = []
    monkeypatch.setattr(routes.os, "makedirs", lambda path, exist_ok=True: workspace.mkdir(exist_ok=True))
    monkeypatch.setattr(routes.os.path, "join", lambda folder, name: str(workspace / name) if folder == "/home/lancelot/workspace" else str(tmp_path / name))

    async def execute_chat(text, **kwargs):
        saved_paths.append([att.filename for att in kwargs["attachments"]])
        return f"reply to {text}"

    _bind_base(
        crusader_mode=SimpleNamespace(is_active=False),
        _execute_chat_turn=execute_chat,
    )

    response = routes.asyncio.run(
        routes.chat_with_files(
            FakeRequest(),
            text="hello",
            files=[FakeUpload("../note.txt", b"content")],
            save_to_workspace=True,
        )
    )
    body = _body(response)
    assert body["response"] == "reply to hello"
    assert body["files_received"] == 1
    assert saved_paths == [["../note.txt"]]
    assert (workspace / "note.txt").read_bytes() == b"content"

    _bind_base(verify_token=lambda request: False)
    assert routes.asyncio.run(routes.chat_with_files(FakeRequest())).status_code == 401

    _bind_base(rate_limiter=SimpleNamespace(check=lambda ip: False))
    assert routes.asyncio.run(routes.chat_with_files(FakeRequest())).status_code == 429

    monkeypatch.setattr("src.core.runtime_pause.is_runtime_paused", lambda: True)
    _bind_base()
    assert routes.asyncio.run(routes.chat_with_files(FakeRequest())).status_code == 423


def test_mfa_submit_accepts_admin_context_and_reports_failures(monkeypatch):
    identity = SimpleNamespace(operator_id="op-1", session_id="session-1", display_name="Arthur")
    monkeypatch.setattr("src.core.auth_api.resolve_authenticated_identity", lambda request: identity)
    monkeypatch.setattr("src.core.auth_api.request_has_capability", lambda request, cap: cap == "platform.admin")
    calls = []
    guard = SimpleNamespace(
        submit_code=lambda task_id, code, **kwargs: calls.append((task_id, code, kwargs)) or (True, "")
    )
    _bind_base(mfa_guard=guard)

    response = routes.asyncio.run(routes.mfa_submit(FakeRequest({"task_id": "task-1", "code": "123456"})))
    assert _body(response)["status"] == "Code Accepted. Bridge Released."
    assert calls[0][2]["is_admin"] is True

    guard.submit_code = lambda *args, **kwargs: (False, "forbidden")
    assert routes.asyncio.run(routes.mfa_submit(FakeRequest({"task_id": "task-1", "code": "123456"}))).status_code == 403

    guard.submit_code = lambda *args, **kwargs: (False, "missing")
    assert routes.asyncio.run(routes.mfa_submit(FakeRequest({"task_id": "task-1", "code": "123456"}))).status_code == 404

    assert routes.asyncio.run(routes.mfa_submit(FakeRequest({"task_id": "task-1"}))).status_code == 400


def test_reload_secrets_rotates_api_token_and_emits_receipt(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "shared.receipts",
        SimpleNamespace(
            ReceiptService=lambda data_dir: SimpleNamespace(
                create_receipt=lambda **kwargs: setattr(routes, "_receipt_created", kwargs)
            )
        ),
    )
    set_calls = []
    secret_cache = SimpleNamespace(
        is_bootstrapped=lambda: True,
        reload=lambda vault: {"LANCELOT_API_TOKEN": True, "OTHER": False},
        get=lambda key: "new-token",
    )
    _bind_base(
        secret_cache=secret_cache,
        _boot_vault=object(),
        set_api_token=lambda token: set_calls.append(token),
        API_TOKEN="old-token",
    )

    response = routes.asyncio.run(routes.reload_secrets(FakeRequest()))

    assert _body(response) == {"status": "ok", "changed_count": 1}
    assert set_calls == ["new-token"]
    assert routes.API_TOKEN == "new-token"
    assert routes._receipt_created["result"] == {"changed_count": 1}

    _bind_base(secret_cache=SimpleNamespace(is_bootstrapped=lambda: False), _boot_vault=None)
    assert routes.asyncio.run(routes.reload_secrets(FakeRequest())).status_code == 503

    _bind_base(_require_request_capability=lambda request, cap: JSONResponse(status_code=403, content={"error": "denied"}))
    assert routes.asyncio.run(routes.reload_secrets(FakeRequest())).status_code == 403


def test_crusader_routes_transition_and_audit():
    audit_events = []
    crusader = SimpleNamespace(
        is_active=False,
        get_status=lambda: {"active": crusader.is_active},
    )
    orchestrator = SimpleNamespace(
        audit_logger=SimpleNamespace(log_event=lambda *args: audit_events.append(args))
    )
    transitions = []

    def transition(action):
        transitions.append(action)
        crusader.is_active = action == "activate"
        return True, f"{action} ok"

    _bind_base(
        crusader_mode=crusader,
        _transition_crusader_mode=transition,
        main_orchestrator=orchestrator,
    )

    assert routes.crusader_status(FakeRequest()) == {"active": False}
    activated = routes.api_crusader_activate(FakeRequest())
    assert activated["status"] == "activated"
    assert routes.api_crusader_activate(FakeRequest())["status"] == "already_active"
    deactivated = routes.api_crusader_deactivate(FakeRequest())
    assert deactivated["status"] == "deactivated"
    assert routes.api_crusader_deactivate(FakeRequest())["status"] == "already_inactive"
    assert transitions == ["activate", "deactivate"]
    assert len(audit_events) == 2

    _bind_base(verify_token=lambda request: False)
    assert routes.crusader_status(FakeRequest()).status_code == 401

    _bind_base(_require_request_capability=lambda request, cap: JSONResponse(status_code=403, content={"error": "denied"}))
    assert routes.api_crusader_activate(FakeRequest()).status_code == 403


def test_receipt_lookup_direct_search_missing_and_error(monkeypatch):
    direct = SimpleNamespace(to_dict=lambda: {"id": "receipt-1"})
    searched = SimpleNamespace(to_dict=lambda: {"id": "receipt-2"})
    service = SimpleNamespace(
        get=lambda task_id: direct if task_id == "direct" else None,
        search=lambda task_id, limit=25: [searched] if task_id == "search" else [],
    )
    monkeypatch.setitem(
        sys.modules,
        "shared.receipts",
        SimpleNamespace(get_receipt_service=lambda data_dir: service),
    )
    _bind_base()

    assert routes.get_receipt("direct", FakeRequest())["receipt"] == {"id": "receipt-1"}
    assert routes.get_receipt("search", FakeRequest())["matches"] == [{"id": "receipt-2"}]
    assert routes.get_receipt("missing", FakeRequest()).status_code == 404

    service.get = lambda task_id: (_ for _ in ()).throw(RuntimeError("db down"))
    assert routes.get_receipt("direct", FakeRequest()).status_code == 500

    _bind_base(verify_token=lambda request: False)
    assert routes.get_receipt("direct", FakeRequest()).status_code == 401


def test_chat_upload_and_mfa_submit_return_safe_500s(monkeypatch):
    class ChatAttachment:
        def __init__(self, filename, mime_type, data):
            self.filename = filename
            self.mime_type = mime_type
            self.data = data

    monkeypatch.setitem(sys.modules, "orchestrator", SimpleNamespace(ChatAttachment=ChatAttachment))
    monkeypatch.setattr("src.core.auth_api.resolve_authenticated_identity", lambda request: SimpleNamespace(operator_id="op-1"))
    monkeypatch.setattr("src.core.runtime_pause.is_runtime_paused", lambda: False)
    monkeypatch.setattr("src.core.runtime_pause.get_runtime_pause_status", lambda: {"reason": "paused"})

    async def failing_turn(*args, **kwargs):
        raise RuntimeError("provider failed")

    _bind_base(
        crusader_mode=SimpleNamespace(is_active=False),
        _execute_chat_turn=failing_turn,
    )
    assert routes.asyncio.run(routes.chat_with_files(FakeRequest(), text="hello", files=[])).status_code == 500

    monkeypatch.setattr(
        "src.core.auth_api.resolve_authenticated_identity",
        lambda request: (_ for _ in ()).throw(RuntimeError("identity failed")),
    )
    _bind_base(mfa_guard=SimpleNamespace(submit_code=lambda *args, **kwargs: (True, "")))
    assert routes.asyncio.run(routes.mfa_submit(FakeRequest({"task_id": "task-1", "code": "123456"}))).status_code == 500


def test_reload_secrets_and_crusader_transition_failure_paths(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "shared.receipts",
        SimpleNamespace(
            ReceiptService=lambda data_dir: SimpleNamespace(
                create_receipt=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("receipt down"))
            )
        ),
    )
    secret_cache = SimpleNamespace(
        is_bootstrapped=lambda: True,
        reload=lambda vault: {"OTHER": True},
        get=lambda key: "ignored",
    )
    _bind_base(secret_cache=secret_cache, _boot_vault=object(), set_api_token=lambda token: None)
    assert _body(routes.asyncio.run(routes.reload_secrets(FakeRequest()))) == {
        "status": "ok",
        "changed_count": 1,
    }

    _bind_base(
        secret_cache=SimpleNamespace(
            is_bootstrapped=lambda: True,
            reload=lambda vault: (_ for _ in ()).throw(RuntimeError("vault down")),
        ),
        _boot_vault=object(),
    )
    assert routes.asyncio.run(routes.reload_secrets(FakeRequest())).status_code == 500

    crusader = SimpleNamespace(is_active=False, get_status=lambda: {"active": False})
    _bind_base(
        crusader_mode=crusader,
        _transition_crusader_mode=lambda action: (False, "transition denied"),
    )
    assert routes.api_crusader_activate(FakeRequest()).status_code == 500

    crusader.is_active = True
    crusader.get_status = lambda: {"active": True}
    _bind_base(
        crusader_mode=crusader,
        _transition_crusader_mode=lambda action: (False, "transition denied"),
    )
    assert routes.api_crusader_deactivate(FakeRequest()).status_code == 500


def test_live_stream_auth_and_service_unavailable_paths(monkeypatch):
    sent = []
    closed = []

    class FakeWebSocket:
        cookies = {}

        def __init__(self, messages):
            self.messages = list(messages)

        async def accept(self):
            sent.append("accepted")

        async def receive_text(self):
            if not self.messages:
                raise routes.WebSocketDisconnect()
            item = self.messages.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        async def send_text(self, text):
            sent.append(text)

        async def close(self, code=None, reason=None):
            closed.append((code, reason))

    monkeypatch.setattr("src.core.auth_api.get_warroom_session_cookie_name", lambda: "session")
    monkeypatch.setattr("src.core.auth_api.verify_warroom_session_token", lambda token: False)

    _bind_base(API_TOKEN="api-token", main_orchestrator=SimpleNamespace(client=None))
    routes.asyncio.run(routes.live_stream(FakeWebSocket([])))
    assert closed[-1] == (4001, "Unauthorized")

    routes.asyncio.run(routes.live_stream(FakeWebSocket([json.dumps({"type": "message", "token": "api-token"})])))
    assert closed[-1] == (4001, "Unauthorized")

    routes.asyncio.run(routes.live_stream(FakeWebSocket([json.dumps({"type": "auth", "token": "wrong"})])))
    assert closed[-1] == (4001, "Unauthorized")

    routes.asyncio.run(routes.live_stream(FakeWebSocket([json.dumps({"type": "auth", "token": "api-token"})])))
    assert "Error: Gemini client not initialized." in sent
    assert closed[-1] == (4002, "Service unavailable")


def test_live_stream_authenticated_session_streams_chunks_and_closes_manager(monkeypatch):
    sent = []
    closed = []
    manager_closed = []

    class FakeWebSocket:
        cookies = {"session": "cookie-token"}

        def __init__(self):
            self.messages = ["hello", routes.WebSocketDisconnect()]

        async def accept(self):
            sent.append("accepted")

        async def receive_text(self):
            item = self.messages.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        async def send_text(self, text):
            sent.append(text)

        async def close(self, code=None, reason=None):
            closed.append((code, reason))

    class Manager:
        def __init__(self, client, model_name, system_instruction):
            assert client == "client"
            assert model_name == "gemini-live"
            assert system_instruction == "system"

        async def connect(self):
            sent.append("connected")

        async def send_text(self, data):
            assert data == "hello"
            yield "chunk-1"
            yield "chunk-2"

        async def close(self):
            manager_closed.append(True)

    monkeypatch.setitem(sys.modules, "live_session", SimpleNamespace(LiveSessionManager=Manager))
    monkeypatch.setattr("src.core.auth_api.get_warroom_session_cookie_name", lambda: "session")
    monkeypatch.setattr("src.core.auth_api.verify_warroom_session_token", lambda token: token == "cookie-token")
    _bind_base(
        main_orchestrator=SimpleNamespace(
            client="client",
            model_name="gemini-live",
            build_system_instruction=lambda: "system",
        )
    )

    routes.asyncio.run(routes.live_stream(FakeWebSocket()))

    assert sent == ["accepted", "connected", "chunk-1", "chunk-2"]
    assert manager_closed == [True]
    assert closed == []
