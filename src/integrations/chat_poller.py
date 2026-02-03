import os
import time
import threading
import logging
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
        self.last_poll_time = datetime.now(timezone.utc).isoformat()
        
        # Load config
        self._load_config()
        
        # Initialize Service
        self._init_service()

    def _load_config(self):
        """Loads space name from .env if available."""
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        if os.path.exists(env_path):
            with open(env_path, "r") as f:
                for line in f:
                    if line.startswith("LANCELOT_CHAT_SPACE_NAME="):
                        self.space_name = line.split("=", 1)[1].strip()

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
            self.service.spaces().messages().create(
                parent=target,
                body={'text': text}
            ).execute()
        except HttpError as e:
            logger.error(f"ChatPoller: Send failed: {e}")

    def start_polling(self):
        """Starts the background polling loop."""
        if self.running or not self.service or not self.space_name:
            return
            
        self.running = True
        threading.Thread(target=self._poll_loop, daemon=True).start()
        logger.info(f"ChatPoller: Started polling {self.space_name}")

    def stop_polling(self):
        self.running = False

    def _poll_loop(self):
        """Main polling loop."""
        while self.running:
            try:
                # Poll for messages created AFTER last_poll_time
                # Note: filter syntax for user credentials might be limited.
                # If filter is not supported for users, we get last N and check timestamp manually.
                
                # 'spaces.messages.list' with filter is often supported. 
                # Otherwise standard list defaults to recent.
                
                resp = self.service.spaces().messages().list(
                    parent=self.space_name,
                    pageSize=10  # grab recent
                ).execute()
                
                messages = resp.get('messages', [])
                
                # Sort by time
                # Process only new ones (simple dedup by timestamp > last_poll_time)
                # ISO format comparison
                
                latest_time_str = self.last_poll_time
                
                for msg in messages:
                    # Skip messages from the bot/user itself? 
                    # sender.type == 'HUMAN' vs 'BOT'? 
                    # If functioning as User, self-sent messages appear too.
                    # We assume we only want to process *other* people's messages or handle 'self' carefully.
                    
                    # msg['createTime'] e.g. '2025-01-01T12:00:00.000Z'
                    create_time = msg.get('createTime')
                    if create_time > self.last_poll_time:
                        sender = msg.get('sender', {})
                        # If we have Orchestrator, feed it
                        if self.orchestrator:
                            # Avoid infinite loops: check if sender is Lancelot? 
                            # Since we use User Creds, Lancelot "IS" the user. 
                            # This is tricky. 
                            # We need a way to distinguish "User Input via Mobile" vs "Lancelot Output".
                            # Lancelot outputs trigger via send_message.
                            # We can simple ignore messages that match exactly what Lancelot just sent?
                            # Or rely on threadKey?
                            
                            # For v1: Process everything. If Lancelot replies, we might re-process it?
                            # Orchestrator checks for 'ACTIONS' or replies.
                            # If the message comes from the "User" (us), it might be a command from mobile.
                            # So we SHOULD process it.
                            
                            text = msg.get('text', '')
                            # Simple loop breaker: If text starts with "Lancelot:" or similar?
                            # Or if Orchestrator just replied, we might see our own reply.
                            
                            # Let's assume we process it.
                            logger.info(f"ChatPoller: Received {text[:20]}...")
                            response = self.orchestrator.chat(text)
                            
                            # If Orchestrator gives a response, WE send it back.
                            # This creates a message in the chat.
                            # Next poll, we see that message.
                            # We need to IGNORE messages that we just sent.
                            # But since we are the same user, sender.name is identical.
                            # We can use a "sent_cache" of IDs?
                            pass
                            
                        # Update high-water mark
                        latest_time_str = max(latest_time_str, create_time)
                
                self.last_poll_time = latest_time_str
                
            except Exception as e:
                logger.error(f"ChatPoller: Poll error: {e}")
                
            time.sleep(3) # Poll interval
