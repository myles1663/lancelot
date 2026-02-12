"""
Protocol Adapter — SMTP/IMAP translation layer for email connectors.

Translates ConnectorResult protocol specs (``protocol://smtp`` and
``protocol://imap``) into actual SMTP/IMAP operations using Python
stdlib only (``smtplib``, ``imaplib``, ``email``).

The adapter is injected into ConnectorProxy and called when the URL
starts with ``protocol://``.  In production it holds a live connection;
in tests it can be replaced with a mock.

No external dependencies — Python stdlib only.
"""

from __future__ import annotations

import email.mime.multipart
import email.mime.text
import imaplib
import logging
import smtplib
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.connectors.models import ConnectorResponse, ConnectorResult

logger = logging.getLogger(__name__)


# ── Configuration ─────────────────────────────────────────────────

@dataclass
class SMTPConfig:
    """SMTP server connection parameters."""
    host: str = "localhost"
    port: int = 587
    use_tls: bool = True


@dataclass
class IMAPConfig:
    """IMAP server connection parameters."""
    host: str = "localhost"
    port: int = 993
    use_ssl: bool = True


# ── Protocol Adapter ─────────────────────────────────────────────

class ProtocolAdapter:
    """Translates ConnectorResult protocol specs into SMTP/IMAP operations.

    Call ``execute(result)`` with a ConnectorResult whose URL starts
    with ``protocol://smtp`` or ``protocol://imap``.  Returns a
    ConnectorResponse.
    """

    def __init__(
        self,
        smtp_config: Optional[SMTPConfig] = None,
        imap_config: Optional[IMAPConfig] = None,
        credentials: Optional[Dict[str, str]] = None,
    ) -> None:
        self._smtp_config = smtp_config or SMTPConfig()
        self._imap_config = imap_config or IMAPConfig()
        self._credentials = credentials or {}
        self._smtp_conn: Optional[smtplib.SMTP] = None
        self._imap_conn: Optional[imaplib.IMAP4_SSL] = None

    # ── Public API ────────────────────────────────────────────────

    def execute(self, result: ConnectorResult) -> ConnectorResponse:
        """Route a protocol:// ConnectorResult to the right handler."""
        start = time.time()
        protocol = result.url.replace("protocol://", "")

        try:
            if protocol == "smtp":
                resp_body = self._handle_smtp(result)
            elif protocol == "imap":
                resp_body = self._handle_imap(result)
            else:
                return ConnectorResponse(
                    operation_id=result.operation_id,
                    connector_id=result.connector_id,
                    status_code=0,
                    success=False,
                    error=f"Unknown protocol: {protocol}",
                    elapsed_ms=(time.time() - start) * 1000,
                )

            elapsed_ms = (time.time() - start) * 1000
            return ConnectorResponse(
                operation_id=result.operation_id,
                connector_id=result.connector_id,
                status_code=200,
                body=resp_body,
                elapsed_ms=elapsed_ms,
                success=True,
            )
        except Exception as e:
            elapsed_ms = (time.time() - start) * 1000
            logger.warning("ProtocolAdapter error: %s", e)
            return ConnectorResponse(
                operation_id=result.operation_id,
                connector_id=result.connector_id,
                status_code=0,
                success=False,
                error=str(e),
                elapsed_ms=elapsed_ms,
            )

    # ── SMTP Handlers ────────────────────────────────────────────

    def _handle_smtp(self, result: ConnectorResult) -> Dict[str, Any]:
        """Handle SMTP send/reply operations."""
        body = result.body or {}
        action = body.get("action", "")

        if action == "send":
            return self._smtp_send(body)
        elif action == "reply":
            return self._smtp_reply(body)
        else:
            raise ValueError(f"Unknown SMTP action: {action}")

    def _smtp_send(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Send an email via SMTP."""
        msg = self._build_message(body)
        self._get_smtp_connection().send_message(msg)
        return {"status": "sent", "to": body.get("to", ""), "subject": body.get("subject", "")}

    def _smtp_reply(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Send a reply email via SMTP."""
        msg = self._build_message(body)
        headers = body.get("headers", {})
        if "In-Reply-To" in headers:
            msg["In-Reply-To"] = headers["In-Reply-To"]
        if "References" in headers:
            msg["References"] = headers["References"]
        self._get_smtp_connection().send_message(msg)
        return {"status": "replied", "in_reply_to": headers.get("In-Reply-To", "")}

    def _build_message(self, body: Dict[str, Any]) -> email.mime.multipart.MIMEMultipart:
        """Build a MIME message from body fields."""
        msg = email.mime.multipart.MIMEMultipart()
        msg["To"] = body.get("to", "")
        msg["Subject"] = body.get("subject", "")
        if body.get("cc"):
            msg["Cc"] = body["cc"]

        mime_type = body.get("mime_type", "text/plain")
        subtype = mime_type.split("/")[-1] if "/" in mime_type else "plain"
        msg.attach(email.mime.text.MIMEText(body.get("body", ""), subtype))
        return msg

    def _get_smtp_connection(self) -> smtplib.SMTP:
        """Get or create SMTP connection."""
        if self._smtp_conn is None:
            cfg = self._smtp_config
            self._smtp_conn = smtplib.SMTP(cfg.host, cfg.port)
            if cfg.use_tls:
                self._smtp_conn.starttls()
            if self._credentials.get("username"):
                self._smtp_conn.login(
                    self._credentials["username"],
                    self._credentials.get("password", ""),
                )
        return self._smtp_conn

    # ── IMAP Handlers ────────────────────────────────────────────

    def _handle_imap(self, result: ConnectorResult) -> Dict[str, Any]:
        """Handle IMAP read/search/delete/move operations."""
        body = result.body or {}
        action = body.get("action", "")

        if action == "list":
            return self._imap_list(body)
        elif action == "fetch":
            return self._imap_fetch(body)
        elif action == "search":
            return self._imap_search(body)
        elif action == "delete":
            return self._imap_delete(body)
        elif action == "move":
            return self._imap_move(body)
        else:
            raise ValueError(f"Unknown IMAP action: {action}")

    def _imap_list(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """List messages in a folder."""
        conn = self._get_imap_connection()
        folder = body.get("folder", "INBOX")
        conn.select(folder)
        _, data = conn.search(None, "ALL")
        message_ids = data[0].split() if data[0] else []
        limit = body.get("max_results", 50)
        return {
            "folder": folder,
            "message_ids": [mid.decode() for mid in message_ids[-limit:]],
            "total": len(message_ids),
        }

    def _imap_fetch(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch a single message by ID."""
        conn = self._get_imap_connection()
        conn.select("INBOX")
        message_id = body.get("message_id", "")
        _, data = conn.fetch(message_id, "(RFC822)")
        if data and data[0]:
            raw = data[0][1] if isinstance(data[0], tuple) else data[0]
            return {"message_id": message_id, "raw": raw.decode("utf-8", errors="replace")}
        return {"message_id": message_id, "raw": ""}

    def _imap_search(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Search messages by query."""
        conn = self._get_imap_connection()
        conn.select("INBOX")
        query = body.get("query", "")
        _, data = conn.search(None, f'SUBJECT "{query}"')
        message_ids = data[0].split() if data[0] else []
        return {"query": query, "message_ids": [mid.decode() for mid in message_ids]}

    def _imap_delete(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Delete a message by marking it as \\Deleted and expunging."""
        conn = self._get_imap_connection()
        conn.select("INBOX")
        message_id = body.get("message_id", "")
        conn.store(message_id, "+FLAGS", "\\Deleted")
        conn.expunge()
        return {"deleted": message_id}

    def _imap_move(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Move a message to a different folder."""
        conn = self._get_imap_connection()
        conn.select("INBOX")
        message_id = body.get("message_id", "")
        destination = body.get("destination", "")
        conn.copy(message_id, destination)
        conn.store(message_id, "+FLAGS", "\\Deleted")
        conn.expunge()
        return {"moved": message_id, "destination": destination}

    def _get_imap_connection(self) -> imaplib.IMAP4_SSL:
        """Get or create IMAP connection."""
        if self._imap_conn is None:
            cfg = self._imap_config
            self._imap_conn = imaplib.IMAP4_SSL(cfg.host, cfg.port)
            if self._credentials.get("username"):
                self._imap_conn.login(
                    self._credentials["username"],
                    self._credentials.get("password", ""),
                )
        return self._imap_conn

    # ── Cleanup ───────────────────────────────────────────────────

    def close(self) -> None:
        """Close any open connections."""
        if self._smtp_conn:
            try:
                self._smtp_conn.quit()
            except Exception:
                pass
            self._smtp_conn = None
        if self._imap_conn:
            try:
                self._imap_conn.logout()
            except Exception:
                pass
            self._imap_conn = None
