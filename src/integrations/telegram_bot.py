"""
Telegram Bot Integration for Lancelot
--------------------------------------
Long-polling bot that receives messages via Telegram Bot API
and routes them through the Lancelot orchestrator.

Uses only `requests` (no extra dependencies).
"""

import os
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
    """

    def __init__(self, orchestrator=None):
        self.token = os.getenv("LANCELOT_TELEGRAM_TOKEN", "")
        self.chat_id = os.getenv("LANCELOT_TELEGRAM_CHAT_ID", "")
        self.orchestrator = orchestrator
        self.running = False
        self._offset = 0  # getUpdates offset for dedup

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

    def _handle_update(self, update: dict):
        """Processes a single Telegram update."""
        update_id = update.get("update_id", 0)
        self._offset = update_id + 1  # ack this update

        msg = update.get("message")
        if not msg:
            return

        text = msg.get("text", "")
        sender_chat_id = str(msg.get("chat", {}).get("id", ""))
        sender_name = msg.get("from", {}).get("first_name", "User")

        if not text:
            return

        # Only respond to the configured chat (security)
        if self.chat_id and sender_chat_id != self.chat_id:
            logger.warning(f"TelegramBot: Ignoring message from unauthorized chat {sender_chat_id}")
            return

        logger.info(f"TelegramBot: [{sender_name}] {text[:50]}...")

        if not self.orchestrator:
            self.send_message("Lancelot orchestrator is not available.", sender_chat_id)
            return

        try:
            response = self.orchestrator.chat(text)
            if response:
                self.send_message(response, sender_chat_id)
        except Exception as e:
            logger.error(f"TelegramBot: Orchestrator error: {e}")
            self.send_message(f"Error processing request: {e}", sender_chat_id)
