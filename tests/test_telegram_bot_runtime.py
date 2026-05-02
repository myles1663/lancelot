import asyncio
import json
import sys
import types

import pytest

from src.integrations.telegram_bot import TelegramBot


class FakeResponse:
    def __init__(self, ok=True, payload=None, text="OK", status_code=200, content=b""):
        self.ok = ok
        self._payload = payload or {}
        self.text = text
        self.status_code = status_code
        self.content = content

    def json(self):
        return self._payload


@pytest.fixture
def telegram_bot(monkeypatch, tmp_path):
    monkeypatch.setattr(
        TelegramBot,
        "_OFFSET_FILE",
        str(tmp_path / "chat" / "telegram_offset.txt"),
    )
    bot = TelegramBot(orchestrator=None)
    bot.token = "123456:TEST"
    bot.chat_id = "chat-1"
    return bot


def test_offset_persistence_and_debug_dump(monkeypatch, tmp_path, telegram_bot):
    telegram_bot._offset = 42
    telegram_bot._save_offset()

    assert TelegramBot._load_offset() == 42

    monkeypatch.setenv("LANCELOT_TELEGRAM_DEBUG_DUMP", "true")
    monkeypatch.setenv("LANCELOT_DATA_DIR", str(tmp_path))

    telegram_bot._write_outbound_debug_artifacts("hello", "chat-1", 2)

    text_path = tmp_path / "chat" / "debug" / "telegram_last_outbound_message.txt"
    meta_path = tmp_path / "chat" / "debug" / "telegram_last_outbound_message.json"
    assert text_path.read_text(encoding="utf-8") == "hello"
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    assert metadata["target_chat_id"] == "chat-1"
    assert metadata["chunk_count"] == 2


def test_formatting_helpers_preserve_mobile_readability():
    assert TelegramBot.display_width("ok") == 2
    assert TelegramBot.display_width("ok ✅") == 5
    assert TelegramBot._pad_to_width("A", 4) == "A   "
    assert TelegramBot.strip_emoji("✅ Ready  速度") == "Ready 速度"
    assert TelegramBot.is_separator_row("| --- | :---: |")
    assert not TelegramBot.is_separator_row("| name | status |")

    widths, kept = TelegramBot.fit_columns([20, 18, 16, 10], 24)

    assert kept < 4
    assert sum(widths) + kept - 1 <= 24


def test_table_and_markdown_sanitization_strips_scaffolding_and_json():
    table = (
        "### **Status**\n"
        "| Capability | Result |\n"
        "| --- | --- |\n"
        "| ✅ governance | **active** |\n"
        "\n"
        "Action: internal tool call\n"
        "```json\n"
        + json.dumps({"items": list(range(200))})
        + "\n```"
    )

    output = TelegramBot.sanitize_for_telegram(table)

    assert "<b>Status</b>" in output
    assert "<pre>" in output
    assert "governance" in output
    assert "Action:" not in output
    assert "[data omitted]" in output


def test_chunk_by_lines_prefers_line_boundaries_and_splits_long_lines():
    chunks = TelegramBot.chunk_by_lines("alpha\nbeta\ngamma", max_size=11)

    assert chunks == ["alpha\nbeta", "gamma"]

    long_line_chunks = TelegramBot.chunk_by_lines("x" * 9, max_size=4)

    assert long_line_chunks == ["xxxx", "xxxx", "x"]


def test_send_message_retries_plain_text_and_writes_debug(monkeypatch, tmp_path, telegram_bot):
    monkeypatch.setenv("LANCELOT_TELEGRAM_DEBUG_DUMP", "true")
    monkeypatch.setenv("LANCELOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("src.integrations.telegram_bot.time.sleep", lambda _: None)
    calls = []

    def fake_post(url, *, component, json=None, **kwargs):
        calls.append({"url": url, "component": component, "payload": dict(json)})
        if len(calls) == 1:
            return FakeResponse(ok=False, text="bad markdown", status_code=400)
        return FakeResponse(ok=True, payload={"result": {"message_id": 99}})

    telegram_bot._post = fake_post

    telegram_bot.send_message("<b>Hello</b> &amp; <code>world</code>")

    assert len(calls) == 2
    assert calls[0]["payload"]["parse_mode"] == "HTML"
    assert "parse_mode" not in calls[1]["payload"]
    assert calls[1]["payload"]["text"] == "Hello & world"
    assert (tmp_path / "chat" / "debug" / "telegram_last_outbound_message.txt").exists()


def test_send_message_blocks_large_raw_json(telegram_bot):
    calls = []
    telegram_bot._post = lambda *args, **kwargs: calls.append(kwargs) or FakeResponse()

    telegram_bot.send_message(json.dumps({"items": list(range(300))}))

    assert calls == []


def test_keyboard_edit_and_callback_methods_handle_success_fallback_and_errors(monkeypatch, telegram_bot):
    monkeypatch.setattr("src.integrations.telegram_bot.time.sleep", lambda _: None)
    calls = []

    def fake_post(url, *, component, json=None, **kwargs):
        calls.append((component, dict(json)))
        if component == "Telegram sendMessage" and len([c for c in calls if c[0] == component]) == 1:
            return FakeResponse(ok=False, text="parse failed", status_code=400)
        return FakeResponse(ok=True, payload={"result": {"message_id": 321}})

    telegram_bot._post = fake_post

    sent_id = telegram_bot.send_message_with_keyboard(
        "<b>Approve?</b>",
        keyboard={"inline_keyboard": [[{"text": "Approve", "callback_data": "ac:abc:approve"}]]},
    )
    edited = telegram_bot.edit_message(321, "<b>Done</b>", keyboard={"inline_keyboard": []})
    answered = telegram_bot.answer_callback_query("query-1", "x" * 300)

    assert sent_id == 321
    assert edited is True
    assert answered is True
    assert calls[-1][1]["text"] == "x" * 200
    assert "parse_mode" not in calls[1][1]

    telegram_bot._post = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down"))
    assert telegram_bot.answer_callback_query("query-2") is False


def test_edit_message_treats_not_modified_as_success(telegram_bot):
    telegram_bot._post = lambda *args, **kwargs: FakeResponse(
        ok=False,
        status_code=400,
        text="Bad Request: message is not modified",
    )

    assert telegram_bot.edit_message(1, "same") is True


def test_document_voice_and_download_helpers(telegram_bot):
    posts = []
    gets = []

    def fake_post(url, *, component, **kwargs):
        posts.append((component, kwargs))
        return FakeResponse(ok=True)

    def fake_get(url, *, component, **kwargs):
        gets.append((component, kwargs))
        if component == "Telegram getFile":
            return FakeResponse(ok=True, payload={"result": {"file_path": "voice/file.ogg"}})
        return FakeResponse(ok=True, content=b"audio-bytes")

    telegram_bot._post = fake_post
    telegram_bot._get = fake_get

    assert telegram_bot.send_document(b"report", "report.txt", caption="caption") is True
    assert telegram_bot.send_document(b"{" + b"x" * 1001, "payload.json") is False
    telegram_bot.send_voice(b"voice")
    assert telegram_bot._download_file("file-1") == b"audio-bytes"
    assert [component for component, _ in posts] == [
        "Telegram sendDocument",
        "Telegram sendVoice",
    ]
    assert [component for component, _ in gets] == [
        "Telegram getFile",
        "Telegram file download",
    ]


def test_download_file_surfaces_telegram_failures(telegram_bot):
    telegram_bot._get = lambda *args, **kwargs: FakeResponse(ok=False, text="denied", status_code=403)

    with pytest.raises(RuntimeError, match="getFile failed"):
        telegram_bot._download_file("missing")

    telegram_bot._get = lambda *args, **kwargs: FakeResponse(ok=True, payload={"result": {}})
    with pytest.raises(RuntimeError, match="No file_path"):
        telegram_bot._download_file("missing")


class FakeOrchestrator:
    def __init__(self, response="ok"):
        self.response = response
        self.calls = []
        self.delivery_handled = False
        self.cleared = False

    def chat(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def was_telegram_delivery_handled(self):
        return self.delivery_handled

    def clear_telegram_delivery_handled(self):
        self.cleared = True


def test_handle_update_routes_text_messages_and_respects_delivery_flag(telegram_bot):
    sent = []
    orchestrator = FakeOrchestrator("<b>already delivered</b>")
    orchestrator.delivery_handled = True
    telegram_bot.orchestrator = orchestrator
    telegram_bot.send_message = lambda text, chat_id=None: sent.append((text, chat_id))

    telegram_bot._handle_update(
        {
            "update_id": 7,
            "message": {
                "chat": {"id": "chat-1"},
                "from": {"first_name": "Myles"},
                "text": "status",
            },
        }
    )

    assert telegram_bot._offset == 8
    assert orchestrator.calls[0][0] == ("status",)
    assert orchestrator.calls[0][1]["channel"] == "telegram"
    assert orchestrator.cleared is True
    assert sent == []


def test_handle_update_security_empty_and_error_paths(telegram_bot):
    sent = []
    telegram_bot.send_message = lambda text, chat_id=None: sent.append((text, chat_id))

    telegram_bot._handle_update({"update_id": 1, "edited_message": {"text": "ignored"}})
    assert telegram_bot._offset == 2

    telegram_bot._handle_update(
        {"update_id": 2, "message": {"chat": {"id": "other"}, "text": "blocked"}}
    )
    assert telegram_bot._offset == 3
    assert sent == []

    telegram_bot.chat_id = ""
    telegram_bot._handle_update(
        {"update_id": 3, "message": {"chat": {"id": "open"}, "text": ""}}
    )
    assert telegram_bot._offset == 4

    telegram_bot.orchestrator = FakeOrchestrator(RuntimeError("boom"))
    telegram_bot._handle_update(
        {"update_id": 4, "message": {"chat": {"id": "open"}, "text": "explode"}}
    )
    assert telegram_bot._offset == 5
    assert sent[-1] == ("Error processing request: boom", "open")


def test_handle_update_dispatches_callback_voice_photo_and_document(telegram_bot):
    calls = []
    telegram_bot._handle_callback_query = lambda callback: calls.append(("callback", callback["id"]))
    telegram_bot._handle_voice = lambda voice, chat_id, sender: calls.append(("voice", voice["file_id"], chat_id, sender))
    telegram_bot._handle_photo = lambda file_id, caption, chat_id, sender: calls.append(("photo", file_id, caption, chat_id, sender))
    telegram_bot._handle_document = lambda document, caption, chat_id, sender: calls.append(("document", document["file_id"], caption, chat_id, sender))

    telegram_bot._handle_update({"update_id": 10, "callback_query": {"id": "cb-1"}})
    telegram_bot._handle_update(
        {
            "update_id": 11,
            "message": {
                "chat": {"id": "chat-1"},
                "from": {"first_name": "Op"},
                "voice": {"file_id": "v1"},
            },
        }
    )
    telegram_bot._handle_update(
        {
            "update_id": 12,
            "message": {
                "chat": {"id": "chat-1"},
                "from": {"first_name": "Op"},
                "photo": [{"file_id": "small"}, {"file_id": "large"}],
                "caption": "look",
            },
        }
    )
    telegram_bot._handle_update(
        {
            "update_id": 13,
            "message": {
                "chat": {"id": "chat-1"},
                "from": {"first_name": "Op"},
                "document": {"file_id": "doc-1"},
            },
        }
    )

    assert calls == [
        ("callback", "cb-1"),
        ("voice", "v1", "chat-1", "Op"),
        ("photo", "large", "look", "chat-1", "Op"),
        ("document", "doc-1", "Please analyze this document.", "chat-1", "Op"),
    ]
    assert telegram_bot._offset == 14


class SttResult:
    def __init__(self, text, confidence=0.91):
        self.text = text
        self.confidence = confidence


class FakeVoiceProcessor:
    def __init__(self, text="turn on governance", available=True, reply=b"voice-reply"):
        self.text = text
        self.available = available
        self.reply = reply
        self.processed = []
        self.synthesized = []

    def process_voice_note(self, audio_bytes, mime_type):
        self.processed.append((audio_bytes, mime_type))
        return SttResult(self.text)

    def synthesize_reply(self, response):
        self.synthesized.append(response)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_handle_voice_covers_disabled_stub_success_and_fallback(telegram_bot):
    sent = []
    voices = []
    telegram_bot.send_message = lambda text, chat_id=None: sent.append((text, chat_id))
    telegram_bot.send_voice = lambda audio, chat_id=None: voices.append((audio, chat_id))

    telegram_bot.voice_processor = None
    telegram_bot._handle_voice({"file_id": "v1"}, "chat-1", "Op")
    assert sent[-1][0] == "Voice notes are not enabled. Please send a text message."

    telegram_bot._download_file = lambda file_id: b"audio"
    telegram_bot.voice_processor = FakeVoiceProcessor("")
    telegram_bot._handle_voice({"file_id": "v2", "mime_type": "audio/ogg"}, "chat-1", "Op")
    assert "couldn't understand" in sent[-1][0]

    telegram_bot.voice_processor = FakeVoiceProcessor("[voice not configured]")
    telegram_bot._handle_voice({"file_id": "v3"}, "chat-1", "Op")
    assert "not currently configured" in sent[-1][0]

    telegram_bot.orchestrator = FakeOrchestrator("Acknowledged **operator**")
    telegram_bot.voice_processor = FakeVoiceProcessor()
    telegram_bot._handle_voice({"file_id": "v4"}, "chat-1", "Op")
    assert voices == [(b"voice-reply", "chat-1")]
    assert sent[-1][0] == "Acknowledged <b>operator</b>"

    telegram_bot.voice_processor = FakeVoiceProcessor(reply=RuntimeError("tts down"))
    telegram_bot._handle_voice({"file_id": "v5"}, "chat-1", "Op")
    assert sent[-1][0] == "Acknowledged <b>operator</b>"


def test_handle_voice_without_orchestrator_and_download_error(telegram_bot):
    sent = []
    telegram_bot.send_message = lambda text, chat_id=None: sent.append((text, chat_id))
    telegram_bot._download_file = lambda file_id: b"audio"
    telegram_bot.voice_processor = FakeVoiceProcessor("what is the status")

    telegram_bot._handle_voice({"file_id": "v1"}, "chat-1", "Op")
    assert "Orchestrator not available" in sent[-1][0]

    telegram_bot._download_file = lambda file_id: (_ for _ in ()).throw(RuntimeError("download failed"))
    telegram_bot._handle_voice({"file_id": "v2"}, "chat-1", "Op")
    assert sent[-1] == ("Error processing voice note: download failed", "chat-1")


class FakeAttachment:
    def __init__(self, filename, mime_type, data):
        self.filename = filename
        self.mime_type = mime_type
        self.data = data


def test_photo_and_document_handlers_send_attachments(monkeypatch, telegram_bot):
    monkeypatch.setitem(
        sys.modules,
        "orchestrator",
        types.SimpleNamespace(ChatAttachment=FakeAttachment),
    )
    sent = []
    telegram_bot.send_message = lambda text, chat_id=None: sent.append((text, chat_id))
    telegram_bot._download_file = lambda file_id: b"file-bytes"
    telegram_bot.orchestrator = FakeOrchestrator("analysis **done**")

    telegram_bot._handle_photo("photo-1", "describe", "chat-1", "Op")
    telegram_bot._handle_document(
        {"file_id": "doc-1", "file_name": "report.pdf", "mime_type": "application/pdf"},
        "analyze",
        "chat-1",
        "Op",
    )

    photo_call, document_call = telegram_bot.orchestrator.calls
    photo_attachment = photo_call[1]["attachments"][0]
    document_attachment = document_call[1]["attachments"][0]
    assert photo_attachment.filename == "telegram_photo.jpg"
    assert document_attachment.filename == "report.pdf"
    assert sent == [
        ("analysis <b>done</b>", "chat-1"),
        ("analysis <b>done</b>", "chat-1"),
    ]


def test_photo_and_document_handlers_report_unavailable_empty_and_errors(telegram_bot):
    sent = []
    telegram_bot.send_message = lambda text, chat_id=None: sent.append((text, chat_id))

    telegram_bot._handle_photo("photo", "caption", "chat-1", "Op")
    telegram_bot._handle_document({"file_id": "doc"}, "caption", "chat-1", "Op")
    assert sent[-2:] == [
        ("Lancelot orchestrator is not available.", "chat-1"),
        ("Lancelot orchestrator is not available.", "chat-1"),
    ]

    telegram_bot.orchestrator = FakeOrchestrator("unused")
    telegram_bot._download_file = lambda file_id: b""
    telegram_bot._handle_photo("photo", "caption", "chat-1", "Op")
    telegram_bot._handle_document({"file_id": "doc"}, "caption", "chat-1", "Op")
    assert sent[-2:] == [
        ("Failed to download the photo. Please try sending it again.", "chat-1"),
        ("Failed to download the document. Please try sending it again.", "chat-1"),
    ]

    telegram_bot._download_file = lambda file_id: (_ for _ in ()).throw(RuntimeError("download failed"))
    telegram_bot._handle_photo("photo", "caption", "chat-1", "Op")
    telegram_bot._handle_document({"file_id": "doc"}, "caption", "chat-1", "Op")
    assert sent[-2:] == [
        ("Error processing photo: download failed", "chat-1"),
        ("Error processing document: download failed", "chat-1"),
    ]


class FakeResolver:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def resolve(self, card_id_prefix, button_id, channel):
        self.calls.append((card_id_prefix, button_id, channel))
        return self.result


def test_callback_query_security_parsing_and_resolution(telegram_bot):
    answered = []
    edited = []
    telegram_bot.answer_callback_query = lambda query_id, text="": answered.append((query_id, text)) or True
    telegram_bot.edit_message = lambda message_id, text, chat_id=None, keyboard=None: edited.append((message_id, text, chat_id, keyboard)) or True

    telegram_bot._handle_callback_query(
        {"id": "unauth", "data": "ac:abc:approve", "message": {"chat": {"id": "other"}}}
    )
    telegram_bot._handle_callback_query(
        {"id": "other", "data": "noop", "message": {"chat": {"id": "chat-1"}}}
    )
    telegram_bot._handle_callback_query(
        {"id": "bad", "data": "ac:missing", "message": {"chat": {"id": "chat-1"}}}
    )
    telegram_bot._handle_callback_query(
        {"id": "noresolver", "data": "ac:abc:approve", "message": {"chat": {"id": "chat-1"}}}
    )

    resolver = FakeResolver({"status": "approved", "message": "Granted"})
    telegram_bot.attach_actioncard_runtime(resolver=resolver, store=None)
    telegram_bot._handle_callback_query(
        {
            "id": "good",
            "data": "ac:abcdef12:approve",
            "message": {"chat": {"id": "chat-1"}, "message_id": 44, "text": "Request"},
        }
    )

    assert answered == [
        ("unauth", "Unauthorized"),
        ("other", ""),
        ("bad", "Invalid callback data"),
        ("noresolver", "ActionCards not available"),
        ("good", "Approve: Granted"),
    ]
    assert resolver.calls == [("abcdef12", "approve", "telegram")]
    assert edited == [
        (44, "Request\n\n[APPROVED] Granted", "chat-1", {"inline_keyboard": []})
    ]


class FakeStore:
    def __init__(self):
        self.message_ids = []

    def set_telegram_message_id(self, card_id, message_id):
        self.message_ids.append((card_id, message_id))


def test_actioncard_events_send_and_update_telegram_message(telegram_bot):
    sent = []
    edited = []
    store = FakeStore()
    telegram_bot.attach_actioncard_runtime(store=store)
    telegram_bot.send_message_with_keyboard = lambda text, keyboard=None: sent.append((text, keyboard)) or 77
    telegram_bot.edit_message = lambda message_id, text, chat_id=None, keyboard=None: edited.append((message_id, text, keyboard)) or True

    event = types.SimpleNamespace(
        payload={
            "card_id": "abcdef123456",
            "title": "Approve deployment",
            "description": "Ship the guarded release",
            "source_system": "tests",
            "buttons": [{"id": "approve", "label": "Approve"}],
        }
    )
    asyncio.run(telegram_bot.handle_actioncard_event(event))

    assert store.message_ids == [("abcdef123456", 77)]
    assert "Approve deployment" in sent[0][0]
    assert sent[0][1]["inline_keyboard"][0][0]["callback_data"] == "ac:abcdef12:approve"

    asyncio.run(
        telegram_bot.handle_actioncard_resolved_event(
            types.SimpleNamespace(
                payload={
                    "channel": "warroom",
                    "telegram_message_id": 77,
                    "button_id": "approve",
                    "result": {"status": "approved", "message": "Done"},
                }
            )
        )
    )
    assert edited == [(77, "[APPROVED] Approve: Done", {"inline_keyboard": []})]


def test_actioncard_events_ignore_uneditable_or_invalid_payloads(telegram_bot):
    sent = []
    telegram_bot.send_message_with_keyboard = lambda text, keyboard=None: sent.append((text, keyboard)) or None

    asyncio.run(
        telegram_bot.handle_actioncard_event(
            types.SimpleNamespace(payload={"card_id": "bad", "buttons": [{"id": "missing-label"}]})
        )
    )
    asyncio.run(
        telegram_bot.handle_actioncard_resolved_event(
            types.SimpleNamespace(payload={"channel": "telegram", "telegram_message_id": 1})
        )
    )
    asyncio.run(
        telegram_bot.handle_actioncard_resolved_event(
            types.SimpleNamespace(payload={"channel": "warroom"})
        )
    )

    assert sent == []


def test_poll_loop_processes_updates_and_persists_offset(monkeypatch, telegram_bot):
    handled = []

    def fake_post(url, *, component, json=None, **kwargs):
        telegram_bot.running = False
        return FakeResponse(ok=True, payload={"ok": True, "result": [{"update_id": 30}]})

    telegram_bot._post = fake_post
    telegram_bot._handle_update = lambda update: handled.append(update) or setattr(telegram_bot, "_offset", 31)
    telegram_bot.running = True

    telegram_bot._poll_loop()

    assert handled == [{"update_id": 30}]
    assert TelegramBot._load_offset() == 31


def test_poll_loop_handles_http_api_timeout_and_generic_errors(monkeypatch, telegram_bot):
    sleeps = []
    monkeypatch.setattr(
        "src.integrations.telegram_bot.time.sleep",
        lambda seconds: sleeps.append(seconds) or setattr(telegram_bot, "running", False),
    )

    telegram_bot._post = lambda *args, **kwargs: FakeResponse(ok=False, status_code=500, text="down")
    telegram_bot.running = True
    telegram_bot._poll_loop()
    assert sleeps == [5]

    sleeps.clear()
    telegram_bot._post = lambda *args, **kwargs: FakeResponse(ok=True, payload={"ok": False, "description": "bad"})
    telegram_bot.running = True
    telegram_bot._poll_loop()
    assert sleeps == [5]

    sleeps.clear()
    telegram_bot._post = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("network"))
    telegram_bot.running = True
    telegram_bot._poll_loop()
    assert sleeps == [5]
