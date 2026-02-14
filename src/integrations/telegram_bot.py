"""
Telegram Bot Integration for Lancelot
--------------------------------------
Long-polling bot that receives messages via Telegram Bot API
and routes them through the Lancelot orchestrator.

Supports text messages and voice notes (Fix Pack V1 PR7).
Uses only `requests` (no extra dependencies beyond voice_processor).
"""

import os
import re
import time
import threading
import logging
import requests

logger = logging.getLogger("lancelot.telegram_bot")

# Telegram Bot API base
TG_API = "https://api.telegram.org/bot{token}/{method}"


class TelegramBot:
    """
    Polls Telegram for new messages and replies via the orchestrator.
    Supports text messages and voice notes (STT → LLM → TTS → voice reply).
    """

    def __init__(self, orchestrator=None, voice_processor=None):
        self.token = os.getenv("LANCELOT_TELEGRAM_TOKEN", "")
        self.chat_id = os.getenv("LANCELOT_TELEGRAM_CHAT_ID", "")
        self.orchestrator = orchestrator
        self.voice_processor = voice_processor
        self.running = False
        self._offset = 0  # getUpdates offset for dedup
        self._receipt_service = None  # Set externally if available

        if not self.token:
            logger.warning("TelegramBot: No LANCELOT_TELEGRAM_TOKEN set.")
        if not self.chat_id:
            logger.warning("TelegramBot: No LANCELOT_TELEGRAM_CHAT_ID set.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_polling(self):
        """Starts the background polling thread."""
        if self.running or not self.token:
            return
        self.running = True
        threading.Thread(target=self._poll_loop, daemon=True).start()
        logger.info(f"TelegramBot: Polling started (chat_id={self.chat_id})")

    def stop_polling(self):
        self.running = False
        logger.info("TelegramBot: Polling stopped.")

    @staticmethod
    def _sanitize_for_telegram(text: str) -> str:
        """Last-resort filter: strip internal tool scaffolding before sending.

        Catches any (Tool: ..., Params: ...) syntax, model= references, and
        user_message= LLM parameter traces that slipped past the assembler.
        """
        # Strip tool call syntax: (Tool: search_workspace, Params: query=foo)
        text = re.sub(r"\s*\(Tool:\s*\w+,?\s*Params:\s*[^)]*\)", "", text)
        # Strip model references: model=gemini-2.0-flash
        text = re.sub(r",?\s*model=[\w.\-]+", "", text)
        # Strip user_message= LLM params
        text = re.sub(r",?\s*user_message=[^,\n)]+", "", text)
        # Strip Action: prefix lines (Gemini tool-call syntax, V3)
        text = re.sub(r"^Action:\s?.*$", "", text, flags=re.MULTILINE)
        # Strip Tool_Code fenced blocks
        text = re.sub(r"```(?:Tool_Code|tool_code)?\s*\n.*?```", "", text, flags=re.DOTALL)
        # Strip unfenced Tool_Code blocks
        text = re.sub(r"^Tool_Code\s*\n.*?(?=\n\n|\Z)", "", text, flags=re.MULTILINE | re.DOTALL)
        # Strip print() function calls
        text = re.sub(r"print\s*\([^)]*\)", "", text, flags=re.IGNORECASE)
        # Clean up empty parens left behind
        text = re.sub(r"\(\s*\)", "", text)
        # Clean up excess blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def send_message(self, text: str, chat_id: str = None):
        """Sends a message to the configured chat."""
        target = chat_id or self.chat_id
        if not self.token or not target:
            logger.warning("TelegramBot: Cannot send (token or chat_id missing).")
            return

        url = TG_API.format(token=self.token, method="sendMessage")

        # Telegram limit is 4096 chars per message; chunk if needed
        chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]
        for chunk in chunks:
            try:
                resp = requests.post(url, json={
                    "chat_id": target,
                    "text": chunk,
                    "parse_mode": "Markdown",
                }, timeout=15)
                if not resp.ok:
                    # Retry without Markdown if parse fails
                    resp2 = requests.post(url, json={
                        "chat_id": target,
                        "text": chunk,
                    }, timeout=15)
                    if not resp2.ok:
                        logger.error(f"TelegramBot: Send failed: {resp2.text}")
            except Exception as e:
                logger.error(f"TelegramBot: Send error: {e}")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _poll_loop(self):
        """Long-polling loop using getUpdates."""
        while self.running:
            try:
                url = TG_API.format(token=self.token, method="getUpdates")
                resp = requests.get(url, params={
                    "offset": self._offset,
                    "timeout": 30,  # long-poll 30s
                }, timeout=40)

                if not resp.ok:
                    logger.error(f"TelegramBot: Poll HTTP {resp.status_code}: {resp.text[:200]}")
                    time.sleep(5)
                    continue

                data = resp.json()
                if not data.get("ok"):
                    logger.error(f"TelegramBot: API error: {data}")
                    time.sleep(5)
                    continue

                for update in data.get("result", []):
                    self._handle_update(update)

            except requests.exceptions.Timeout:
                # Normal for long-polling — just loop again
                continue
            except Exception as e:
                logger.error(f"TelegramBot: Poll error: {e}")
                time.sleep(5)

    def send_document(self, file_bytes: bytes, filename: str, chat_id: str = None, caption: str = None):
        """Sends a document/file to the configured chat."""
        target = chat_id or self.chat_id
        if not self.token or not target:
            logger.warning("TelegramBot: Cannot send document (token or chat_id missing).")
            return False

        url = TG_API.format(token=self.token, method="sendDocument")
        try:
            data = {"chat_id": target}
            if caption:
                data["caption"] = caption[:1024]  # Telegram caption limit
            resp = requests.post(
                url,
                data=data,
                files={"document": (filename, file_bytes, "application/octet-stream")},
                timeout=60,
            )
            if not resp.ok:
                logger.error("TelegramBot: Send document failed: %s", resp.text[:200])
                return False
            logger.info("TelegramBot: Sent document '%s' (%d bytes)", filename, len(file_bytes))
            return True
        except Exception as e:
            logger.error("TelegramBot: Send document error: %s", e)
            return False

    def send_voice(self, audio_bytes: bytes, chat_id: str = None):
        """Sends a voice note (OGG/OPUS) to the configured chat."""
        target = chat_id or self.chat_id
        if not self.token or not target:
            logger.warning("TelegramBot: Cannot send voice (token or chat_id missing).")
            return

        url = TG_API.format(token=self.token, method="sendVoice")
        try:
            resp = requests.post(
                url,
                data={"chat_id": target},
                files={"voice": ("reply.ogg", audio_bytes, "audio/ogg")},
                timeout=30,
            )
            if not resp.ok:
                logger.error(f"TelegramBot: Send voice failed: {resp.text[:200]}")
        except Exception as e:
            logger.error(f"TelegramBot: Send voice error: {e}")

    def _download_file(self, file_id: str) -> bytes:
        """Download a file from Telegram by file_id."""
        # Step 1: Get file path
        url = TG_API.format(token=self.token, method="getFile")
        resp = requests.get(url, params={"file_id": file_id}, timeout=15)
        if not resp.ok:
            raise RuntimeError(f"getFile failed: {resp.text[:200]}")

        file_path = resp.json().get("result", {}).get("file_path", "")
        if not file_path:
            raise RuntimeError("No file_path in getFile response")

        # Step 2: Download file content
        download_url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
        dl_resp = requests.get(download_url, timeout=30)
        if not dl_resp.ok:
            raise RuntimeError(f"File download failed: {dl_resp.status_code}")

        return dl_resp.content

    def _handle_update(self, update: dict):
        """Processes a single Telegram update."""
        update_id = update.get("update_id", 0)
        self._offset = update_id + 1  # ack this update

        msg = update.get("message")
        if not msg:
            return

        sender_chat_id = str(msg.get("chat", {}).get("id", ""))
        sender_name = msg.get("from", {}).get("first_name", "User")

        # Only respond to the configured chat (security)
        if self.chat_id and sender_chat_id != self.chat_id:
            logger.warning(f"TelegramBot: Ignoring message from unauthorized chat {sender_chat_id}")
            return

        # Check for voice note / audio
        voice = msg.get("voice") or msg.get("audio")
        if voice:
            self._handle_voice(voice, sender_chat_id, sender_name)
            return

        # V14: Check for photo messages
        photo = msg.get("photo")
        if photo:
            caption = msg.get("caption", "What's in this image?")
            largest = photo[-1]  # Highest resolution
            self._handle_photo(largest["file_id"], caption, sender_chat_id, sender_name)
            return

        # V14: Check for document messages
        document = msg.get("document")
        if document:
            caption = msg.get("caption", "Please analyze this document.")
            self._handle_document(document, caption, sender_chat_id, sender_name)
            return

        text = msg.get("text", "")
        if not text:
            return

        logger.info(f"TelegramBot: [{sender_name}] {text[:50]}...")

        if not self.orchestrator:
            self.send_message("Lancelot orchestrator is not available.", sender_chat_id)
            return

        try:
            response = self.orchestrator.chat(text, channel="telegram")
            if response:
                response = self._sanitize_for_telegram(response)
                self.send_message(response, sender_chat_id)
        except Exception as e:
            logger.error(f"TelegramBot: Orchestrator error: {e}")
            self.send_message(f"Error processing request: {e}", sender_chat_id)

    def _handle_voice(self, voice: dict, chat_id: str, sender_name: str):
        """Handle a voice note: STT → orchestrator → TTS → voice reply."""
        if not self.voice_processor:
            self.send_message(
                "Voice notes are not enabled. Please send a text message.",
                chat_id,
            )
            return

        file_id = voice.get("file_id", "")
        mime_type = voice.get("mime_type", "audio/ogg")
        duration = voice.get("duration", 0)

        logger.info(
            "TelegramBot: Voice note from [%s] (duration=%ds, mime=%s)",
            sender_name, duration, mime_type,
        )

        try:
            # Step 1: Download voice file from Telegram
            audio_bytes = self._download_file(file_id)
            logger.info("TelegramBot: Downloaded voice file (%d bytes)", len(audio_bytes))

            # Step 2: STT — transcribe audio to text
            stt_result = self.voice_processor.process_voice_note(audio_bytes, mime_type)
            transcribed_text = stt_result.text

            if not transcribed_text:
                self.send_message(
                    "I couldn't understand the voice note. Please try again or send text.",
                    chat_id,
                )
                return

            logger.info(
                "TelegramBot: STT result: '%s' (confidence=%.2f)",
                transcribed_text[:80], stt_result.confidence,
            )

            # Step 3: Route through orchestrator
            if not self.orchestrator:
                self.send_message(
                    f"Transcribed: {transcribed_text}\n\n(Orchestrator not available)",
                    chat_id,
                )
                return

            response = self.orchestrator.chat(transcribed_text, channel="telegram")
            if not response:
                return

            # Step 4: TTS — synthesize response as voice
            response = self._sanitize_for_telegram(response)
            sent = False
            if self.voice_processor.available:
                try:
                    audio_reply = self.voice_processor.synthesize_reply(response)
                    if audio_reply:
                        self.send_voice(audio_reply, chat_id)
                        # Also send text version for accessibility
                        self.send_message(response, chat_id)
                        sent = True
                except Exception as tts_err:
                    logger.warning("TelegramBot: TTS failed, falling back to text: %s", tts_err)

            # Fallback: send text response (only if not already sent)
            if not sent:
                self.send_message(response, chat_id)

        except Exception as e:
            logger.error("TelegramBot: Voice processing error: %s", e)
            self.send_message(
                f"Error processing voice note: {e}",
                chat_id,
            )

    def _handle_photo(self, file_id: str, caption: str, chat_id: str, sender_name: str):
        """V14: Handle a photo — download → send to Gemini vision → respond."""
        logger.info("TelegramBot: Photo from [%s] caption='%s'", sender_name, caption[:50])

        if not self.orchestrator:
            self.send_message("Lancelot orchestrator is not available.", chat_id)
            return

        try:
            image_bytes = self._download_file(file_id)
            logger.info("TelegramBot: Downloaded photo (%d bytes)", len(image_bytes))

            from orchestrator import ChatAttachment
            attachment = ChatAttachment(
                filename="telegram_photo.jpg",
                mime_type="image/jpeg",
                data=image_bytes,
            )

            response = self.orchestrator.chat(caption, attachments=[attachment], channel="telegram")
            if response:
                response = self._sanitize_for_telegram(response)
                self.send_message(response, chat_id)

        except Exception as e:
            logger.error("TelegramBot: Photo processing error: %s", e)
            self.send_message(f"Error processing photo: {e}", chat_id)

    def _handle_document(self, document: dict, caption: str, chat_id: str, sender_name: str):
        """V14: Handle a document — download → read/analyze → respond."""
        file_id = document.get("file_id", "")
        file_name = document.get("file_name", "unknown")
        mime_type = document.get("mime_type", "application/octet-stream")

        logger.info("TelegramBot: Document from [%s]: %s (%s)", sender_name, file_name, mime_type)

        if not self.orchestrator:
            self.send_message("Lancelot orchestrator is not available.", chat_id)
            return

        try:
            file_bytes = self._download_file(file_id)
            logger.info("TelegramBot: Downloaded document (%d bytes)", len(file_bytes))

            from orchestrator import ChatAttachment
            attachment = ChatAttachment(
                filename=file_name,
                mime_type=mime_type,
                data=file_bytes,
            )

            response = self.orchestrator.chat(caption, attachments=[attachment], channel="telegram")
            if response:
                response = self._sanitize_for_telegram(response)
                self.send_message(response, chat_id)

        except Exception as e:
            logger.error("TelegramBot: Document processing error: %s", e)
            self.send_message(f"Error processing document: {e}", chat_id)
