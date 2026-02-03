"""
Security Bridge - Production Authentication & MFA
-------------------------------------------------
Handles stateful MFA challenges and webhook security.

Features:
1. **MFA Bridge**: Async event loop that pauses automation until a user submits a code via /mfa_submit.
2. **Webhook Auth**: Validates JWT headers from Google Chat (Stubbed for now, waiting for key material).
"""

import asyncio
import logging
import os
from security import AuditLogger

logger = logging.getLogger("lancelot.security_bridge")

class MFAListener:
    """
    Coordinates Blocking MFA Challenges.
    
    Flow:
    1. Automation calls `request_mfa(task_id)`.
    2. Automation calls `await wait_for_code(task_id)`.
    3. User receives alert and POSTs to `/mfa_submit`.
    4. `submit_code(task_id, code)` is called, releasing the waiter.
    """
    def __init__(self):
        self._pending_challenges = {} # task_id -> {"event": asyncio.Event, "code": str}
        self.audit_logger = AuditLogger()

    async def request_mfa(self, task_id: str, context: str):
        """Registers a new challenge and logs it."""
        if task_id in self._pending_challenges:
            logger.warning(f"MFA Challenge for {task_id} already exists.")
            return

        self._pending_challenges[task_id] = {
            "event": asyncio.Event(),
            "code": None,
            "created_at": str(asyncio.get_running_loop().time())
        }
        
        self.audit_logger.log_event(
            "MFA_REQUESTED",
            f"Challenge needed for: {context}",
            user="System"
        )
        logger.info(f"MFA Challenge created for Task [{task_id}]")

    async def wait_for_code(self, task_id: str, timeout=300) -> str:
        """Blocks until code is submitted or timeout."""
        challenge = self._pending_challenges.get(task_id)
        if not challenge:
            raise ValueError(f"No MFA challenge found for {task_id}")
        
        logger.info(f"Task [{task_id}] Waiting for Code...")
        try:
            await asyncio.wait_for(challenge["event"].wait(), timeout=timeout)
            code = challenge["code"]
            logger.info(f"Task [{task_id}] Resumed with Code.")
            
            # Cleanup
            del self._pending_challenges[task_id]
            return code
            
        except asyncio.TimeoutError:
            logger.error(f"Task [{task_id}] MFA Timed Out.")
            del self._pending_challenges[task_id]
            raise TimeoutError("MFA Code not received in time.")

    def submit_code(self, task_id: str, code: str) -> bool:
        """Called by API to release the block."""
        challenge = self._pending_challenges.get(task_id)
        if not challenge:
            logger.warning(f"Received code for unknown task {task_id}")
            return False
        
        challenge["code"] = code
        challenge["event"].set()
        
        self.audit_logger.log_event(
            "MFA_SUBMITTED",
            f"Code received for Task [{task_id}]",
            user="User"
        )
        return True


class WebhookAuthenticator:
    """Validates incoming Webhook requests."""
    
    GOOGLE_Issuer = "chat@system.gserviceaccount.com"
    
    def verify_remote_header(self, auth_header: str) -> bool:
        """
        Validates Bearer token from Google Chat.
        
        TODO: Implement real JWT signature validation using Google's public keys.
        Current V1: Simple Token Match from Env.
        """
        expected_token = os.getenv("LANCELOT_API_TOKEN")
        if not expected_token:
            return True # Dev mode
            
        if not auth_header.startswith("Bearer "):
            return False
            
        token = auth_header.split(" ")[1]
        return token == expected_token

class CommsBridge:
    """
    Handles Outbound Secure Communication.
    Uses the channel verified during Onboarding.
    """
    def __init__(self):
        self.comms_type = os.getenv("LANCELOT_COMMS_TYPE", "google_chat")
        
        # Google Chat Config
        self.webhook_url = os.getenv("LANCELOT_COMMS_WEBHOOK")
        
        # Telegram Config
        self.telegram_token = os.getenv("LANCELOT_TELEGRAM_TOKEN")
        self.telegram_chat_id = os.getenv("LANCELOT_TELEGRAM_CHAT_ID")

    async def send_alert(self, message: str):
        """Sends an alert to the bonded channel."""
        import aiohttp
        
        if self.comms_type == "google_chat":
             if not self.webhook_url:
                 logger.warning("Google Chat Webhook missing.")
                 return
             target_url = self.webhook_url
             payload = {"text": message}

        elif self.comms_type == "telegram":
             if not self.telegram_token or not self.telegram_chat_id:
                 logger.warning("Telegram settings missing.")
                 return
             target_url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
             payload = {"chat_id": self.telegram_chat_id, "text": message, "parse_mode": "Markdown"}
        
        else:
             logger.warning(f"Unknown comms type: {self.comms_type}")
             return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(target_url, json=payload) as resp:
                    if resp.status != 200:
                        logger.error(f"Comms Alert Failed ({self.comms_type}): {resp.status}")
        except Exception as e:
            logger.error(f"Comms Error: {e}")
