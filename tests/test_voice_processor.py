"""
Tests for VoiceProcessor (Fix Pack V1 PR7) and Telegram voice note integration.
"""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock, PropertyMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.integrations.voice_processor import (
    VoiceProcessor,
    VoiceResult,
    SUPPORTED_AUDIO_TYPES,
    _mime_to_encoding,
)


# =========================================================================
# VoiceProcessor — Stub Mode (no API key)
# =========================================================================


class TestVoiceProcessorStubMode:
    """Tests for VoiceProcessor when no API key is configured."""

    def test_stub_mode_when_no_api_key(self):
        vp = VoiceProcessor(api_key="")
        assert not vp.available

    def test_available_when_api_key_set(self):
        vp = VoiceProcessor(api_key="test-key-123")
        assert vp.available

    def test_stt_stub_returns_placeholder(self):
        vp = VoiceProcessor(api_key="")
        result = vp.process_voice_note(b"\x00\x01\x02", "audio/ogg")
        assert isinstance(result, VoiceResult)
        assert "not configured" in result.text.lower() or "voice note received" in result.text.lower()
        assert result.confidence == 0.0

    def test_tts_stub_returns_empty_bytes(self):
        vp = VoiceProcessor(api_key="")
        audio = vp.synthesize_reply("Hello world")
        assert audio == b""


# =========================================================================
# VoiceProcessor — Input Validation
# =========================================================================


class TestVoiceProcessorValidation:
    def test_empty_audio_raises(self):
        vp = VoiceProcessor(api_key="")
        with pytest.raises(ValueError, match="Empty audio"):
            vp.process_voice_note(b"", "audio/ogg")

    def test_unsupported_mime_raises(self):
        vp = VoiceProcessor(api_key="")
        with pytest.raises(ValueError, match="Unsupported audio type"):
            vp.process_voice_note(b"\x00\x01", "video/mp4")

    def test_empty_text_synthesis_raises(self):
        vp = VoiceProcessor(api_key="")
        with pytest.raises(ValueError, match="Empty text"):
            vp.synthesize_reply("")

    def test_supported_mime_types(self):
        """All listed MIME types should be accepted."""
        vp = VoiceProcessor(api_key="")
        for mime in SUPPORTED_AUDIO_TYPES:
            result = vp.process_voice_note(b"\x00\x01\x02", mime)
            assert isinstance(result, VoiceResult)


# =========================================================================
# VoiceProcessor — Google STT (Mocked)
# =========================================================================


class TestVoiceProcessorSTT:
    @patch("src.integrations.voice_processor.requests")
    def test_stt_google_success(self, mock_requests):
        """Successful STT call returns transcribed text."""
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "results": [
                {
                    "alternatives": [
                        {"transcript": "Hello Lancelot", "confidence": 0.95}
                    ]
                }
            ]
        }
        mock_requests.post.return_value = mock_resp

        vp = VoiceProcessor(api_key="test-key")
        result = vp.process_voice_note(b"\x00\x01\x02\x03", "audio/ogg")

        assert result.text == "Hello Lancelot"
        assert result.confidence == pytest.approx(0.95, abs=0.01)
        assert result.duration_ms >= 0

    @patch("src.integrations.voice_processor.requests")
    def test_stt_google_empty_results(self, mock_requests):
        """STT with no results returns empty text."""
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"results": []}
        mock_requests.post.return_value = mock_resp

        vp = VoiceProcessor(api_key="test-key")
        result = vp.process_voice_note(b"\x00\x01\x02\x03", "audio/ogg")

        assert result.text == ""
        assert result.confidence == 0.0

    @patch("src.integrations.voice_processor.requests")
    def test_stt_google_multiple_results(self, mock_requests):
        """STT with multiple result chunks concatenates them."""
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "results": [
                {"alternatives": [{"transcript": "Hello", "confidence": 0.9}]},
                {"alternatives": [{"transcript": "world", "confidence": 0.85}]},
            ]
        }
        mock_requests.post.return_value = mock_resp

        vp = VoiceProcessor(api_key="test-key")
        result = vp.process_voice_note(b"\x00\x01\x02\x03", "audio/ogg")

        assert result.text == "Hello world"
        assert result.confidence == pytest.approx(0.875, abs=0.01)

    @patch("src.integrations.voice_processor.requests")
    def test_stt_google_api_error(self, mock_requests):
        """STT API error raises RuntimeError."""
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 403
        mock_resp.text = "Forbidden"
        mock_requests.post.return_value = mock_resp

        vp = VoiceProcessor(api_key="test-key")
        with pytest.raises(RuntimeError, match="STT API error 403"):
            vp.process_voice_note(b"\x00\x01\x02\x03", "audio/ogg")

    @patch("src.integrations.voice_processor.requests")
    def test_stt_google_network_error(self, mock_requests):
        """STT network failure raises RuntimeError."""
        import requests as real_requests
        # Set up the mock's exceptions attribute to reference real exception classes
        mock_requests.exceptions = real_requests.exceptions
        mock_requests.post.side_effect = real_requests.exceptions.ConnectionError("offline")

        vp = VoiceProcessor(api_key="test-key")
        with pytest.raises(RuntimeError, match="STT request failed"):
            vp.process_voice_note(b"\x00\x01\x02\x03", "audio/ogg")


# =========================================================================
# VoiceProcessor — Google TTS (Mocked)
# =========================================================================


class TestVoiceProcessorTTS:
    @patch("src.integrations.voice_processor.requests")
    def test_tts_google_success(self, mock_requests):
        """Successful TTS call returns audio bytes."""
        import base64
        fake_audio = b"\x00\x01\x02\x03\x04\x05"
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "audioContent": base64.b64encode(fake_audio).decode()
        }
        mock_requests.post.return_value = mock_resp

        vp = VoiceProcessor(api_key="test-key")
        result = vp.synthesize_reply("Hello Lancelot")

        assert result == fake_audio

    @patch("src.integrations.voice_processor.requests")
    def test_tts_google_api_error(self, mock_requests):
        """TTS API error raises RuntimeError."""
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 400
        mock_resp.text = "Bad Request"
        mock_requests.post.return_value = mock_resp

        vp = VoiceProcessor(api_key="test-key")
        with pytest.raises(RuntimeError, match="TTS API error 400"):
            vp.synthesize_reply("Hello")

    @patch("src.integrations.voice_processor.requests")
    def test_tts_google_empty_audio_content(self, mock_requests):
        """TTS with empty audioContent raises RuntimeError."""
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"audioContent": ""}
        mock_requests.post.return_value = mock_resp

        vp = VoiceProcessor(api_key="test-key")
        with pytest.raises(RuntimeError, match="no audioContent"):
            vp.synthesize_reply("Hello")

    @patch("src.integrations.voice_processor.requests")
    def test_tts_truncates_long_text(self, mock_requests):
        """TTS truncates text longer than 4500 chars."""
        import base64
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "audioContent": base64.b64encode(b"audio").decode()
        }
        mock_requests.post.return_value = mock_resp

        vp = VoiceProcessor(api_key="test-key")
        long_text = "x" * 5000
        vp.synthesize_reply(long_text)

        # Verify the payload text was truncated
        call_args = mock_requests.post.call_args
        sent_text = call_args[1]["json"]["input"]["text"]
        assert len(sent_text) <= 4504  # 4500 + "..."


# =========================================================================
# MIME type encoding mapping
# =========================================================================


class TestMimeEncoding:
    def test_ogg_maps_to_ogg_opus(self):
        assert _mime_to_encoding("audio/ogg") == "OGG_OPUS"

    def test_mp3_maps_to_mp3(self):
        assert _mime_to_encoding("audio/mp3") == "MP3"
        assert _mime_to_encoding("audio/mpeg") == "MP3"

    def test_wav_maps_to_linear16(self):
        assert _mime_to_encoding("audio/wav") == "LINEAR16"

    def test_flac_maps_to_flac(self):
        assert _mime_to_encoding("audio/flac") == "FLAC"

    def test_unknown_defaults_to_ogg_opus(self):
        assert _mime_to_encoding("audio/unknown") == "OGG_OPUS"

    def test_mime_with_codec_param(self):
        assert _mime_to_encoding("audio/ogg; codecs=opus") == "OGG_OPUS"


# =========================================================================
# TelegramBot — Voice Note Handling
# =========================================================================


class TestTelegramBotVoice:
    def _make_bot(self, orchestrator=None, voice_processor=None):
        from src.integrations.telegram_bot import TelegramBot
        bot = TelegramBot(orchestrator=orchestrator, voice_processor=voice_processor)
        bot.token = "fake-token"
        bot.chat_id = "12345"
        return bot

    def test_voice_note_without_processor_sends_text(self):
        """Voice note with no processor sends a fallback text message."""
        bot = self._make_bot(voice_processor=None)
        sent = []
        bot.send_message = lambda text, chat_id=None: sent.append(text)

        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": 12345},
                "from": {"first_name": "Test"},
                "voice": {"file_id": "abc", "mime_type": "audio/ogg", "duration": 5},
            },
        }
        bot._handle_update(update)
        assert len(sent) == 1
        assert "not enabled" in sent[0].lower()

    @patch("src.integrations.telegram_bot.requests")
    def test_voice_note_end_to_end(self, mock_requests):
        """Voice note → STT → orchestrator → text reply."""
        # Mock file download
        mock_get_resp = MagicMock()
        mock_get_resp.ok = True
        mock_get_resp.json.return_value = {"result": {"file_path": "voice/file.ogg"}}
        mock_dl_resp = MagicMock()
        mock_dl_resp.ok = True
        mock_dl_resp.content = b"\x00\x01\x02\x03"
        mock_requests.get.side_effect = [mock_get_resp, mock_dl_resp]

        # Mock voice processor
        vp = MagicMock()
        vp.available = False  # No TTS, text fallback
        vp.process_voice_note.return_value = VoiceResult(
            text="Hello Lancelot", confidence=0.9
        )

        # Mock orchestrator
        orch = MagicMock()
        orch.chat.return_value = "I am Lancelot, at your service."

        bot = self._make_bot(orchestrator=orch, voice_processor=vp)
        sent = []
        bot.send_message = lambda text, chat_id=None: sent.append(text)

        update = {
            "update_id": 2,
            "message": {
                "chat": {"id": 12345},
                "from": {"first_name": "Test"},
                "voice": {"file_id": "abc123", "mime_type": "audio/ogg", "duration": 3},
            },
        }
        bot._handle_update(update)

        # Verify STT was called
        vp.process_voice_note.assert_called_once()
        # Verify orchestrator received transcribed text
        orch.chat.assert_called_once_with("Hello Lancelot")
        # Verify response was sent
        assert len(sent) == 1
        assert "at your service" in sent[0]

    @patch("src.integrations.telegram_bot.requests")
    def test_voice_note_with_tts_reply(self, mock_requests):
        """Voice note with TTS available sends voice + text reply."""
        import base64

        # Mock file download
        mock_get_resp = MagicMock()
        mock_get_resp.ok = True
        mock_get_resp.json.return_value = {"result": {"file_path": "voice/file.ogg"}}
        mock_dl_resp = MagicMock()
        mock_dl_resp.ok = True
        mock_dl_resp.content = b"\x00\x01\x02\x03"

        # Mock send voice
        mock_send_resp = MagicMock()
        mock_send_resp.ok = True

        mock_requests.get.side_effect = [mock_get_resp, mock_dl_resp]
        mock_requests.post.return_value = mock_send_resp

        # Mock voice processor with TTS
        vp = MagicMock()
        vp.available = True
        vp.process_voice_note.return_value = VoiceResult(
            text="Status report", confidence=0.92
        )
        vp.synthesize_reply.return_value = b"\xff\xfe\xfd"  # Fake audio

        # Mock orchestrator
        orch = MagicMock()
        orch.chat.return_value = "All systems nominal."

        bot = self._make_bot(orchestrator=orch, voice_processor=vp)
        text_sent = []
        voice_sent = []
        bot.send_message = lambda text, chat_id=None: text_sent.append(text)
        bot.send_voice = lambda audio, chat_id=None: voice_sent.append(audio)

        update = {
            "update_id": 3,
            "message": {
                "chat": {"id": 12345},
                "from": {"first_name": "Test"},
                "voice": {"file_id": "xyz", "mime_type": "audio/ogg", "duration": 2},
            },
        }
        bot._handle_update(update)

        # TTS was called with orchestrator response
        vp.synthesize_reply.assert_called_once_with("All systems nominal.")
        # Voice reply sent
        assert len(voice_sent) == 1
        assert voice_sent[0] == b"\xff\xfe\xfd"
        # Text also sent for accessibility
        assert len(text_sent) == 1
        assert "nominal" in text_sent[0]

    def test_text_message_still_works(self):
        """Regular text messages still route through orchestrator."""
        orch = MagicMock()
        orch.chat.return_value = "Text response"

        bot = self._make_bot(orchestrator=orch, voice_processor=MagicMock())
        sent = []
        bot.send_message = lambda text, chat_id=None: sent.append(text)

        update = {
            "update_id": 4,
            "message": {
                "chat": {"id": 12345},
                "from": {"first_name": "Test"},
                "text": "Hello",
            },
        }
        bot._handle_update(update)
        assert len(sent) == 1
        assert sent[0] == "Text response"

    @patch("src.integrations.telegram_bot.requests")
    def test_empty_transcription_sends_fallback(self, mock_requests):
        """Empty STT result sends a helpful message."""
        mock_get_resp = MagicMock()
        mock_get_resp.ok = True
        mock_get_resp.json.return_value = {"result": {"file_path": "voice/file.ogg"}}
        mock_dl_resp = MagicMock()
        mock_dl_resp.ok = True
        mock_dl_resp.content = b"\x00\x01\x02\x03"
        mock_requests.get.side_effect = [mock_get_resp, mock_dl_resp]

        vp = MagicMock()
        vp.available = False
        vp.process_voice_note.return_value = VoiceResult(text="", confidence=0.0)

        bot = self._make_bot(orchestrator=MagicMock(), voice_processor=vp)
        sent = []
        bot.send_message = lambda text, chat_id=None: sent.append(text)

        update = {
            "update_id": 5,
            "message": {
                "chat": {"id": 12345},
                "from": {"first_name": "Test"},
                "voice": {"file_id": "abc", "mime_type": "audio/ogg", "duration": 1},
            },
        }
        bot._handle_update(update)
        assert len(sent) == 1
        assert "couldn't understand" in sent[0].lower()

    def test_unauthorized_chat_ignored(self):
        """Messages from unauthorized chats are ignored."""
        bot = self._make_bot()
        sent = []
        bot.send_message = lambda text, chat_id=None: sent.append(text)

        update = {
            "update_id": 6,
            "message": {
                "chat": {"id": 99999},  # Wrong chat
                "from": {"first_name": "Evil"},
                "voice": {"file_id": "abc", "mime_type": "audio/ogg", "duration": 1},
            },
        }
        bot._handle_update(update)
        assert len(sent) == 0


# =========================================================================
# Feature Flag
# =========================================================================


class TestVoiceFeatureFlag:
    def test_voice_notes_flag_exists(self):
        from src.core.feature_flags import FEATURE_VOICE_NOTES
        assert isinstance(FEATURE_VOICE_NOTES, bool)


# =========================================================================
# Receipt Types
# =========================================================================


class TestVoiceReceiptTypes:
    def test_voice_receipt_types_exist(self):
        from src.shared.receipts import ActionType
        assert ActionType.VOICE_STT == "voice_stt"
        assert ActionType.VOICE_TTS == "voice_tts"
