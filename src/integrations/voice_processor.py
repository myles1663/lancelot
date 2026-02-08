"""
Voice Processor — STT/TTS pipeline for Telegram voice notes (Fix Pack V1 PR7).

Converts audio → text (STT) and text → audio (TTS) using Google Cloud
Speech-to-Text / Text-to-Speech REST APIs, with fallback stubs when
API keys are not configured.

Public API:
    VoiceProcessor(api_key)   — create processor
    process_voice_note(audio_bytes, mime_type) → VoiceResult
    synthesize_reply(text) → bytes (OGG/OPUS)
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests
from requests.exceptions import RequestException

logger = logging.getLogger("lancelot.voice_processor")

# Supported input MIME types for STT
SUPPORTED_AUDIO_TYPES = {
    "audio/ogg",
    "audio/oga",
    "audio/opus",
    "audio/ogg; codecs=opus",
    "audio/mp3",
    "audio/mpeg",
    "audio/wav",
    "audio/x-wav",
    "audio/flac",
}

# Google Cloud Speech-to-Text v1 REST endpoint
STT_URL = "https://speech.googleapis.com/v1/speech:recognize"
# Google Cloud Text-to-Speech v1 REST endpoint
TTS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"


@dataclass
class VoiceResult:
    """Result from voice-note processing."""
    text: str
    confidence: float = 0.0
    language: str = "en"
    duration_ms: float = 0.0


def _mime_to_encoding(mime_type: str) -> str:
    """Map MIME type to Google Speech encoding enum."""
    mime = mime_type.lower().split(";")[0].strip()
    mapping = {
        "audio/ogg": "OGG_OPUS",
        "audio/oga": "OGG_OPUS",
        "audio/opus": "OGG_OPUS",
        "audio/mp3": "MP3",
        "audio/mpeg": "MP3",
        "audio/wav": "LINEAR16",
        "audio/x-wav": "LINEAR16",
        "audio/flac": "FLAC",
    }
    return mapping.get(mime, "OGG_OPUS")


class VoiceProcessor:
    """STT/TTS pipeline for Telegram voice notes."""

    def __init__(self, api_key: str = None, language: str = "en-US"):
        self.api_key = api_key or os.getenv("GOOGLE_CLOUD_API_KEY", "")
        self.language = language
        self._available = bool(self.api_key)

        if not self._available:
            logger.warning(
                "VoiceProcessor: No GOOGLE_CLOUD_API_KEY set. "
                "STT/TTS will use stub mode (no real transcription)."
            )

    @property
    def available(self) -> bool:
        """Whether real STT/TTS is available (API key configured)."""
        return self._available

    def process_voice_note(
        self, audio_bytes: bytes, mime_type: str = "audio/ogg"
    ) -> VoiceResult:
        """Transcribe audio bytes to text via STT.

        Args:
            audio_bytes: Raw audio data.
            mime_type: MIME type of the audio (default: audio/ogg for Telegram).

        Returns:
            VoiceResult with transcribed text and confidence.

        Raises:
            ValueError: If audio_bytes is empty or mime_type unsupported.
            RuntimeError: If STT API call fails.
        """
        if not audio_bytes:
            raise ValueError("Empty audio data")

        base_mime = mime_type.lower().split(";")[0].strip()
        if base_mime not in SUPPORTED_AUDIO_TYPES:
            raise ValueError(
                f"Unsupported audio type: '{mime_type}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_AUDIO_TYPES))}"
            )

        start = time.monotonic()

        if not self._available:
            # Stub mode — return placeholder
            duration_ms = (time.monotonic() - start) * 1000
            logger.info("VoiceProcessor: STT stub mode — returning placeholder text")
            return VoiceResult(
                text="[Voice note received — STT not configured]",
                confidence=0.0,
                language=self.language,
                duration_ms=duration_ms,
            )

        return self._stt_google(audio_bytes, mime_type, start)

    def synthesize_reply(self, text: str) -> bytes:
        """Convert text to audio bytes (OGG/OPUS for Telegram).

        Args:
            text: Text to synthesize.

        Returns:
            Audio bytes in OGG/OPUS format.

        Raises:
            ValueError: If text is empty.
            RuntimeError: If TTS API call fails.
        """
        if not text:
            raise ValueError("Empty text for synthesis")

        if not self._available:
            logger.info("VoiceProcessor: TTS stub mode — returning empty audio")
            return b""

        return self._tts_google(text)

    # ------------------------------------------------------------------
    # Google Cloud STT
    # ------------------------------------------------------------------

    def _stt_google(
        self, audio_bytes: bytes, mime_type: str, start: float
    ) -> VoiceResult:
        """Call Google Cloud Speech-to-Text REST API."""
        encoding = _mime_to_encoding(mime_type)
        audio_b64 = base64.b64encode(audio_bytes).decode("ascii")

        payload = {
            "config": {
                "encoding": encoding,
                "languageCode": self.language,
                "enableAutomaticPunctuation": True,
                "model": "latest_long",
            },
            "audio": {
                "content": audio_b64,
            },
        }

        try:
            resp = requests.post(
                STT_URL,
                params={"key": self.api_key},
                json=payload,
                timeout=30,
            )
            duration_ms = (time.monotonic() - start) * 1000

            if not resp.ok:
                raise RuntimeError(
                    f"STT API error {resp.status_code}: {resp.text[:500]}"
                )

            data = resp.json()
            results = data.get("results", [])

            if not results:
                return VoiceResult(
                    text="",
                    confidence=0.0,
                    language=self.language,
                    duration_ms=duration_ms,
                )

            # Concatenate all result transcripts
            transcript_parts = []
            total_confidence = 0.0
            count = 0
            for result in results:
                alternatives = result.get("alternatives", [])
                if alternatives:
                    best = alternatives[0]
                    transcript_parts.append(best.get("transcript", ""))
                    total_confidence += best.get("confidence", 0.0)
                    count += 1

            text = " ".join(transcript_parts).strip()
            avg_confidence = total_confidence / max(count, 1)

            logger.info(
                "VoiceProcessor: STT transcribed %d chars (confidence=%.2f, %.1fms)",
                len(text), avg_confidence, duration_ms,
            )

            return VoiceResult(
                text=text,
                confidence=avg_confidence,
                language=self.language,
                duration_ms=duration_ms,
            )

        except RequestException as e:
            duration_ms = (time.monotonic() - start) * 1000
            raise RuntimeError(f"STT request failed: {e}") from e

    # ------------------------------------------------------------------
    # Google Cloud TTS
    # ------------------------------------------------------------------

    def _tts_google(self, text: str) -> bytes:
        """Call Google Cloud Text-to-Speech REST API."""
        # Truncate to TTS limits (5000 bytes max)
        if len(text) > 4500:
            text = text[:4500] + "..."

        payload = {
            "input": {"text": text},
            "voice": {
                "languageCode": self.language,
                "ssmlGender": "NEUTRAL",
            },
            "audioConfig": {
                "audioEncoding": "OGG_OPUS",
                "speakingRate": 1.0,
            },
        }

        try:
            resp = requests.post(
                TTS_URL,
                params={"key": self.api_key},
                json=payload,
                timeout=30,
            )

            if not resp.ok:
                raise RuntimeError(
                    f"TTS API error {resp.status_code}: {resp.text[:500]}"
                )

            data = resp.json()
            audio_content = data.get("audioContent", "")
            if not audio_content:
                raise RuntimeError("TTS response contained no audioContent")

            audio_bytes = base64.b64decode(audio_content)
            logger.info(
                "VoiceProcessor: TTS synthesized %d bytes from %d chars",
                len(audio_bytes), len(text),
            )
            return audio_bytes

        except RequestException as e:
            raise RuntimeError(f"TTS request failed: {e}") from e
