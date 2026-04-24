import os
import time

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
    def __init__(self, messages_api):
        self._messages_api = messages_api

    def messages(self):
        return self._messages_api


class _Service:
    def __init__(self, messages_api):
        self._spaces = _SpacesAPI(messages_api)

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


def test_stop_polling_does_not_wait_for_full_poll_interval(monkeypatch, tmp_path):
    monkeypatch.setenv("LANCELOT_CHAT_SPACE_NAME", "spaces/AAA123")
    monkeypatch.setattr(ChatPoller, "_init_service", lambda self: None)
    poller = ChatPoller(str(tmp_path))
    poller.service = _Service(_MessagesAPI())

    poller.start_polling()
    assert poller._poll_thread is not None
    assert poller._poll_thread.is_alive()

    started_at = time.perf_counter()
    poller.stop_polling()
    elapsed_s = time.perf_counter() - started_at

    assert elapsed_s < 0.5
    assert poller.running is False
    assert poller._poll_thread is None
