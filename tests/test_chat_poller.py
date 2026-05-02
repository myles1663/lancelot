import os
from src.integrations import chat_poller
from src.integrations.chat_poller import ChatPoller


class _CreateCall:
    def __init__(self, response):
        self._response = response

    def execute(self):
        return self._response


class _MessagesAPI:
    def __init__(self, create_response=None, list_response=None):
        self.create_response = create_response or {}
        self.list_response = list_response or {"messages": []}
        self.created = []

    def create(self, parent, body):
        self.created.append((parent, body))
        return _CreateCall(self.create_response)

    def list(self, parent, pageSize):
        return _CreateCall(self.list_response)


class _SpacesAPI:
    def __init__(self, messages_api, list_response=None):
        self._messages_api = messages_api
        self._list_response = list_response or {"spaces": []}

    def messages(self):
        return self._messages_api

    def list(self):
        return _CreateCall(self._list_response)


class _Service:
    def __init__(self, messages_api, list_response=None):
        self._spaces = _SpacesAPI(messages_api, list_response=list_response)

    def spaces(self):
        return self._spaces


def test_loads_space_name_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("LANCELOT_CHAT_SPACE_NAME", "spaces/AAA123")
    monkeypatch.setattr(ChatPoller, "_init_service", lambda self: None)

    poller = ChatPoller(str(tmp_path))

    assert poller.space_name == "spaces/AAA123"


def test_send_message_records_sent_message_id(monkeypatch, tmp_path):
    monkeypatch.setenv("LANCELOT_CHAT_SPACE_NAME", "spaces/AAA123")
    monkeypatch.setattr(ChatPoller, "_init_service", lambda self: None)
    poller = ChatPoller(str(tmp_path))
    messages_api = _MessagesAPI(create_response={"name": "spaces/AAA123/messages/1"})
    poller.service = _Service(messages_api)

    poller.send_message("hello")

    assert messages_api.created == [("spaces/AAA123", {"text": "hello"})]
    assert "spaces/AAA123/messages/1" in poller._sent_message_set


def test_process_messages_sends_response_and_skips_self_sent(monkeypatch, tmp_path):
    monkeypatch.setenv("LANCELOT_CHAT_SPACE_NAME", "spaces/AAA123")
    monkeypatch.setattr(ChatPoller, "_init_service", lambda self: None)

    class _Orchestrator:
        def __init__(self):
            self.calls = []

        def chat(self, text, channel="warroom"):
            self.calls.append((text, channel))
            return f"echo:{text}"

    orchestrator = _Orchestrator()
    poller = ChatPoller(str(tmp_path), orchestrator=orchestrator)
    messages_api = _MessagesAPI(create_response={"name": "spaces/AAA123/messages/outbound"})
    poller.service = _Service(messages_api)
    poller.last_poll_time = "2026-01-01T00:00:00Z"
    poller._remember_sent_message_id("spaces/AAA123/messages/self")

    poller._process_messages(
        [
            {
                "name": "spaces/AAA123/messages/self",
                "text": "ignore me",
                "createTime": "2026-01-01T00:00:01Z",
            },
            {
                "name": "spaces/AAA123/messages/inbound",
                "text": "status",
                "createTime": "2026-01-01T00:00:02Z",
            },
        ]
    )

    assert orchestrator.calls == [("status", "google_chat")]
    assert messages_api.created == [("spaces/AAA123", {"text": "echo:status"})]
    assert poller.last_poll_time == "2026-01-01T00:00:02Z"


def test_init_service_success_and_failure(monkeypatch, tmp_path):
    built = []
    monkeypatch.setattr(chat_poller.google.auth, "default", lambda scopes: ("creds", "project"))
    monkeypatch.setattr(chat_poller, "build", lambda *args, **kwargs: built.append((args, kwargs)) or "service")

    poller = ChatPoller(str(tmp_path))

    assert poller.creds == "creds"
    assert poller.service == "service"
    assert built[0][0] == ("chat", "v1")

    monkeypatch.setattr(chat_poller.google.auth, "default", lambda scopes: (_ for _ in ()).throw(RuntimeError("no auth")))
    failed = ChatPoller(str(tmp_path))
    assert failed.service is None


def test_list_spaces_success_and_error_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(ChatPoller, "_init_service", lambda self: None)
    poller = ChatPoller(str(tmp_path))

    assert poller.list_spaces() == []

    poller.service = _Service(_MessagesAPI(), list_response={"spaces": [{"name": "spaces/1"}]})
    assert poller.list_spaces() == [{"name": "spaces/1"}]

    class BrokenSpaces:
        def list(self):
            raise RuntimeError("list failed")

    poller.service = type("BrokenService", (), {"spaces": lambda self: BrokenSpaces()})()
    assert poller.list_spaces() == []


def test_send_message_missing_target_and_http_error(monkeypatch, tmp_path):
    monkeypatch.setattr(ChatPoller, "_init_service", lambda self: None)
    poller = ChatPoller(str(tmp_path))

    assert poller.send_message("no service") is None

    class BrokenMessages:
        def create(self, parent, body):
            raise chat_poller.HttpError(resp=type("Resp", (), {"status": 500, "reason": "bad"})(), content=b"{}")

    poller.service = type("BrokenService", (), {"spaces": lambda self: type("Spaces", (), {"messages": lambda self: BrokenMessages()})()})()
    poller.space_name = "spaces/AAA123"

    assert poller.send_message("hello") is None


def test_sent_message_id_dedupes_and_evicts(monkeypatch, tmp_path):
    monkeypatch.setattr(ChatPoller, "_init_service", lambda self: None)
    poller = ChatPoller(str(tmp_path))
    poller._sent_message_ids = chat_poller.deque(maxlen=2)

    poller._remember_sent_message_id(None)
    poller._remember_sent_message_id("m1")
    poller._remember_sent_message_id("m1")
    poller._remember_sent_message_id("m2")
    poller._remember_sent_message_id("m3")

    assert "m1" not in poller._sent_message_set
    assert list(poller._sent_message_ids) == ["m2", "m3"]
    assert poller._is_self_sent_message({"name": "m2"}) is True
    assert poller._is_self_sent_message({"name": "missing"}) is False


def test_start_stop_and_poll_loop(monkeypatch, tmp_path):
    monkeypatch.setenv("LANCELOT_CHAT_SPACE_NAME", "spaces/AAA123")
    monkeypatch.setattr(ChatPoller, "_init_service", lambda self: None)
    started = []

    class Thread:
        def __init__(self, target, daemon):
            self.target = target
            self.daemon = daemon

        def start(self):
            started.append((self.target, self.daemon))

    monkeypatch.setattr(chat_poller.threading, "Thread", Thread)
    poller = ChatPoller(str(tmp_path))
    poller.service = _Service(_MessagesAPI())

    poller.start_polling()
    poller.start_polling()
    poller.stop_polling()

    assert len(started) == 1
    assert poller.running is False

    messages_api = _MessagesAPI(list_response={"messages": [{"text": "", "createTime": "2026-01-01T00:00:01Z"}]})
    poller.service = _Service(messages_api)
    poller.last_poll_time = "2026-01-01T00:00:00Z"
    poller.running = True
    monkeypatch.setattr(chat_poller.time, "sleep", lambda _seconds: setattr(poller, "running", False))

    poller._poll_loop()

    assert poller.last_poll_time == "2026-01-01T00:00:01Z"


def test_poll_loop_logs_and_continues_after_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(ChatPoller, "_init_service", lambda self: None)
    poller = ChatPoller(str(tmp_path))
    poller.space_name = "spaces/AAA123"

    class BrokenMessages:
        def list(self, parent, pageSize):
            raise RuntimeError("poll failed")

    poller.service = type("BrokenService", (), {"spaces": lambda self: type("Spaces", (), {"messages": lambda self: BrokenMessages()})()})()
    poller.running = True
    monkeypatch.setattr(chat_poller.time, "sleep", lambda _seconds: setattr(poller, "running", False))

    poller._poll_loop()

    assert poller.running is False
