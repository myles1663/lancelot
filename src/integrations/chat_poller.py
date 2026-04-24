import os
import time
import threading
import logging
from collections import deque
import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from datetime import datetime, timezone

logger = logging.getLogger("lancelot.chat_poller")

class ChatPoller:
    """
    Polls Google Chat for new messages using User Credentials (ADC).
    Acts as the bridge for 2-way communication.
    """
    def __init__(self, data_dir: str, orchestrator=None):
        self.data_dir = data_dir
        self.orchestrator = orchestrator
        self.creds = None
        self.service = None
        self.space_name = None
        self.running = False
        self._stop_event = threading.Event()
        self._poll_thread = None
        self.last_poll_time = datetime.now(timezone.utc).isoformat()
        self._sent_message_ids = deque(maxlen=100)
        self._sent_message_set = set()
        
        # Load config
        self._load_config()
        
        # Initialize Service
        self._init_service()

    def _load_config(self):
        """Loads the configured Google Chat space from the live environment."""
        self.space_name = (
            os.getenv("LANCELOT_CHAT_SPACE_NAME")
            or os.getenv("GOOGLE_CHAT_SPACE_NAME")
            or self.space_name
        )

    def _init_service(self):
        """Initializes the Authenticated Chat Service."""
        try:
            # Scopes for reading and writing messages
            SCOPES = ['https://www.googleapis.com/auth/chat.messages', 
                      'https://www.googleapis.com/auth/chat.spaces.readonly']
            
            self.creds, project = google.auth.default(scopes=SCOPES)
            self.service = build('chat', 'v1', credentials=self.creds)
            logger.info("ChatPoller: Service initialized successfully.")
        except Exception as e:
            logger.warning(f"ChatPoller: Failed to init service (Auth missing?): {e}")

    def list_spaces(self):
        """Lists available spaces (DMs and Rooms) for the user."""
        if not self.service:
            logger.warning("ChatPoller: No service available (auth failed?)")
            return []
        try:
            # User credentials can list spaces they are in
            result = self.service.spaces().list().execute()
            spaces = result.get('spaces', [])
            logger.info(f"ChatPoller: Found {len(spaces)} spaces.")
            return spaces
        except HttpError as e:
            logger.error(f"ChatPoller: HTTP Error listing spaces: {e.status_code} - {e.reason}")
            logger.error(f"ChatPoller: Details: {e.error_details}")
            return []
        except Exception as e:
            logger.error(f"ChatPoller: Unexpected error listing spaces: {e}")
            return []

    def send_message(self, text: str, space_name: str = None):
        """Sends a message to the defined space."""
        target = space_name or self.space_name
        if not self.service or not target:
            logger.warning("ChatPoller: Cannot send (Service or Space missing).")
            return
            
        try:
            created = self.service.spaces().messages().create(
                parent=target,
                body={'text': text}
            ).execute()
            self._remember_sent_message_id(created.get("name"))
        except HttpError as e:
            logger.error(f"ChatPoller: Send failed: {e}")

    def _remember_sent_message_id(self, message_id: str | None):
        """Track sent message IDs so they are not reprocessed on the next poll."""
        if not message_id or message_id in self._sent_message_set:
            return
        if len(self._sent_message_ids) == self._sent_message_ids.maxlen:
            oldest = self._sent_message_ids.popleft()
            self._sent_message_set.discard(oldest)
        self._sent_message_ids.append(message_id)
        self._sent_message_set.add(message_id)

    def _is_self_sent_message(self, msg: dict) -> bool:
        """Return True when the message was previously sent by this poller."""
        msg_id = msg.get("name")
        return bool(msg_id and msg_id in self._sent_message_set)

    def _process_messages(self, messages):
        """Process a batch of polled messages and update the high-water mark."""
        latest_time_str = self.last_poll_time
        ordered = sorted(messages, key=lambda m: m.get("createTime", ""))

        for msg in ordered:
            create_time = msg.get("createTime")
            if not create_time or create_time <= self.last_poll_time:
                continue
            if self._is_self_sent_message(msg):
                latest_time_str = max(latest_time_str, create_time)
                continue

            text = (msg.get("text") or "").strip()
            if self.orchestrator and text:
                logger.info("ChatPoller: Received %s...", text[:20])
                response = self.orchestrator.chat(text, channel="google_chat")
                if response:
                    self.send_message(response)

            latest_time_str = max(latest_time_str, create_time)

        self.last_poll_time = latest_time_str

    def start_polling(self):
        """Starts the background polling loop."""
        if self.running or not self.service or not self.space_name:
            return
            
        self.running = True
        self._stop_event.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="google-chat-poller",
        )
        self._poll_thread.start()
        logger.info(f"ChatPoller: Started polling {self.space_name}")

    def stop_polling(self):
        was_running = self.running or (
            self._poll_thread is not None and self._poll_thread.is_alive()
        )
        self.running = False
        self._stop_event.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=5)
            if self._poll_thread.is_alive():
                logger.warning("ChatPoller: Polling thread did not stop within 5s")
                return
            self._poll_thread = None
        if was_running:
            logger.info("ChatPoller: Polling stopped")
        else:
            logger.debug("ChatPoller: Stop skipped; polling was not running")

    def _poll_loop(self):
        """Main polling loop."""
        try:
            while not self._stop_event.is_set():
                try:
                    resp = self.service.spaces().messages().list(
                        parent=self.space_name,
                        pageSize=10
                    ).execute()
                    self._process_messages(resp.get('messages', []))
                except Exception as e:
                    logger.error(f"ChatPoller: Poll error: {e}")

                if self._stop_event.wait(timeout=3):
                    return
        finally:
            self.running = False
