"""
Security Bridge - Production Authentication & MFA
-------------------------------------------------
Handles stateful MFA challenges and webhook security.

Features:
1. **MFA Bridge**: Async event loop that pauses automation until a user submits a code via /mfa_submit.
2. **Webhook Auth**: Validates JWT headers from Google Chat (Stubbed for now, waiting for key material).
"""

import asyncio
import datetime
import json
import logging
import os
import time
from pathlib import Path
from security import AuditLogger

logger = logging.getLogger("lancelot.security_bridge")
MFA_CHALLENGE_TTL_SECONDS = 300

class MFAListener:
    """
    Coordinates Blocking MFA Challenges.
    
    Flow:
    1. Automation calls `request_mfa(task_id)`.
    2. Automation calls `await wait_for_code(task_id)`.
    3. User receives alert and POSTs to `/mfa_submit`.
    4. `submit_code(task_id, code)` is called, releasing the waiter.
    """
    def __init__(self, data_dir: str = "/home/lancelot/data"):
        self._pending_challenges = {} # task_id -> challenge metadata
        self._events = {}
        self._data_dir = Path(data_dir)
        self._challenge_file = self._data_dir / "mfa_challenges.json"
        self.audit_logger = AuditLogger()
        self._load_challenges()

    def _load_challenges(self):
        """Restore pending MFA challenges from disk."""
        self._pending_challenges = {}
        try:
            if not self._challenge_file.exists():
                return
            data = json.loads(self._challenge_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._pending_challenges = data
        except Exception as exc:
            logger.warning("Failed to load MFA challenges: %s", exc)
            self._pending_challenges = {}
        self._prune_expired(save=False)

    def _save_challenges(self):
        """Persist MFA challenges to disk."""
        try:
            self._challenge_file.parent.mkdir(parents=True, exist_ok=True)
            self._challenge_file.write_text(
                json.dumps(self._pending_challenges, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Failed to persist MFA challenges: %s", exc)

    def _prune_expired(self, *, save: bool = True):
        """Drop expired MFA challenges."""
        now = time.time()
        expired = [
            task_id
            for task_id, challenge in self._pending_challenges.items()
            if challenge.get("created_at_ts", 0) + MFA_CHALLENGE_TTL_SECONDS < now
        ]
        for task_id in expired:
            self._pending_challenges.pop(task_id, None)
            self._events.pop(task_id, None)
        if expired and save:
            self._save_challenges()

    async def request_mfa(
        self,
        task_id: str,
        context: str,
        *,
        operator_id: str = "",
        session_id: str = "",
        actor: str = "",
    ):
        """Registers a new challenge and logs it."""
        self._prune_expired()
        if task_id in self._pending_challenges:
            logger.warning(f"MFA Challenge for {task_id} already exists.")
            return

        self._pending_challenges[task_id] = {
            "code": None,
            "created_at": datetime.datetime.utcnow().isoformat(),
            "created_at_ts": time.time(),
            "operator_id": operator_id or "",
            "session_id": session_id or "",
            "actor": actor or operator_id or "System",
        }
        self._events[task_id] = asyncio.Event()
        self._save_challenges()
        
        self.audit_logger.log_event(
            "MFA_REQUESTED",
            f"Challenge needed for: {context}",
            user=actor or operator_id or "System",
        )
        logger.info(f"MFA Challenge created for Task [{task_id}]")

    async def wait_for_code(self, task_id: str, timeout=300) -> str:
        """Blocks until code is submitted or timeout."""
        self._load_challenges()
        challenge = self._pending_challenges.get(task_id)
        if not challenge:
            raise ValueError(f"No MFA challenge found for {task_id}")

        logger.info(f"Task [{task_id}] Waiting for Code...")
        start = time.monotonic()
        event = self._events.setdefault(task_id, asyncio.Event())

        while True:
            self._load_challenges()
            challenge = self._pending_challenges.get(task_id)
            if not challenge:
                raise TimeoutError("MFA challenge expired or was cleared.")

            code = challenge.get("code")
            if code:
                logger.info(f"Task [{task_id}] Resumed with Code.")
                self._pending_challenges.pop(task_id, None)
                self._events.pop(task_id, None)
                self._save_challenges()
                return code

            remaining = timeout - (time.monotonic() - start)
            if remaining <= 0:
                logger.error(f"Task [{task_id}] MFA Timed Out.")
                self._pending_challenges.pop(task_id, None)
                self._events.pop(task_id, None)
                self._save_challenges()
                raise TimeoutError("MFA Code not received in time.")

            try:
                await asyncio.wait_for(event.wait(), timeout=min(1.0, remaining))
                event.clear()
            except asyncio.TimeoutError:
                continue

    def submit_code(
        self,
        task_id: str,
        code: str,
        *,
        operator_id: str = "",
        session_id: str = "",
        actor: str = "",
        is_admin: bool = False,
    ) -> tuple[bool, str]:
        """Called by API to release the block.

        The submitting operator must match the original challenge owner
        unless an explicit admin override is used.
        """
        self._load_challenges()
        challenge = self._pending_challenges.get(task_id)
        if not challenge:
            logger.warning(f"Received code for unknown task {task_id}")
            return False, "unknown_task"

        bound_operator_id = challenge.get("operator_id", "")
        bound_session_id = challenge.get("session_id", "")

        if not is_admin:
            if bound_operator_id and operator_id and bound_operator_id != operator_id:
                logger.warning(
                    "Rejected MFA code submission for task %s: operator mismatch (%s != %s)",
                    task_id,
                    operator_id,
                    bound_operator_id,
                )
                return False, "forbidden"
            if bound_session_id and session_id and bound_session_id != session_id:
                logger.warning(
                    "Rejected MFA code submission for task %s: session mismatch (%s != %s)",
                    task_id,
                    session_id,
                    bound_session_id,
                )
                return False, "forbidden"

        challenge["code"] = code
        challenge["submitted_at"] = datetime.datetime.utcnow().isoformat()
        self._save_challenges()
        event = self._events.get(task_id)
        if event is not None:
            event.set()

        self.audit_logger.log_event(
            "MFA_SUBMITTED",
            f"Code received for Task [{task_id}]",
            user=actor or operator_id or challenge.get("actor") or "User",
        )
        return True, "accepted"


class WebhookAuthenticator:
    """Validates incoming Webhook requests."""
    
    GOOGLE_Issuer = "chat@system.gserviceaccount.com"

    def _expected_bearer(self) -> str:
        """Return the bonded webhook bearer secret.

        Prefer a dedicated webhook bearer when configured. Fall back to the
        general API token for backward compatibility with existing installs.
        """
        try:
            import secret_cache

            expected = secret_cache.get("LANCELOT_WEBHOOK_BEARER", "")
            if expected:
                return expected
            return secret_cache.get("LANCELOT_API_TOKEN", "")
        except Exception:
            return (
                os.getenv("LANCELOT_WEBHOOK_BEARER", "").strip()
                or os.getenv("LANCELOT_API_TOKEN", "").strip()
            )
    
    def verify_remote_header(self, auth_header: str) -> bool:
        """
        Validates Bearer token from Google Chat.
        
        TODO: Implement real JWT signature validation using Google's public keys.
        Current hardened fallback: require an explicit bonded webhook bearer.
        """
        expected_token = self._expected_bearer()
        if not expected_token:
            logger.error("Webhook auth attempted without a configured bearer secret")
            return False
            
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
        self.comms_type = os.getenv("LANCELOT_COMMS_TYPE", "none")

        # Google Chat Config
        self.webhook_url = os.getenv("LANCELOT_COMMS_WEBHOOK")

    def _telegram_token(self) -> str:
        """Read telegram token from secret_cache (supports hot rotation)."""
        try:
            import secret_cache
            return secret_cache.get("LANCELOT_TELEGRAM_TOKEN", "")
        except Exception:
            return os.getenv("LANCELOT_TELEGRAM_TOKEN", "")

    def _telegram_chat_id(self) -> str:
        """Read telegram chat ID from secret_cache (supports hot rotation)."""
        try:
            import secret_cache
            return secret_cache.get("LANCELOT_TELEGRAM_CHAT_ID", "")
        except Exception:
            return os.getenv("LANCELOT_TELEGRAM_CHAT_ID", "")

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
             tg_token = self._telegram_token()
             tg_chat = self._telegram_chat_id()
             if not tg_token or not tg_chat:
                 logger.warning("Telegram settings missing.")
                 return
             target_url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
             payload = {"chat_id": tg_chat, "text": message, "parse_mode": "Markdown"}
        
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
