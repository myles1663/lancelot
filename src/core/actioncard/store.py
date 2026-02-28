# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under AGPL-3.0. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
ActionCardStore — SQLite-backed persistence for ActionCards.

Tracks card lifecycle: presented -> resolved/expired.
Enables cross-channel sync (resolve in Telegram, reflected in War Room).
Supports prefix lookup for Telegram's 64-byte callback_data limit.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

from actioncard.models import ActionButton, ActionCard

logger = logging.getLogger(__name__)


class ActionCardStore:
    """SQLite-backed store for ActionCards."""

    CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS action_cards (
        card_id TEXT PRIMARY KEY,
        card_type TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        source_system TEXT NOT NULL DEFAULT '',
        source_item_id TEXT NOT NULL DEFAULT '',
        buttons TEXT NOT NULL DEFAULT '[]',
        metadata TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL,
        expires_at REAL,
        quest_id TEXT,
        resolved INTEGER NOT NULL DEFAULT 0,
        resolved_action TEXT,
        resolved_at REAL,
        resolved_channel TEXT,
        telegram_message_id INTEGER
    );

    CREATE INDEX IF NOT EXISTS idx_ac_resolved ON action_cards(resolved);
    CREATE INDEX IF NOT EXISTS idx_ac_source ON action_cards(source_system);
    CREATE INDEX IF NOT EXISTS idx_ac_created ON action_cards(created_at);
    CREATE INDEX IF NOT EXISTS idx_ac_quest ON action_cards(quest_id);
    """

    def __init__(self, data_dir: str = "/home/lancelot/data"):
        self.data_dir = data_dir
        self.db_path = os.path.join(data_dir, "actioncards.db")
        self._local = threading.local()
        self._lock = threading.Lock()
        os.makedirs(data_dir, exist_ok=True)
        self._init_database()

    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local database connection."""
        if not hasattr(self._local, "connection") or self._local.connection is None:
            self._local.connection = sqlite3.connect(
                self.db_path, check_same_thread=False, timeout=30.0
            )
            self._local.connection.row_factory = sqlite3.Row
            self._local.connection.execute("PRAGMA journal_mode=WAL")
            self._local.connection.execute("PRAGMA synchronous=NORMAL")
        return self._local.connection

    def _init_database(self) -> None:
        """Initialize database schema."""
        conn = self._get_connection()
        conn.executescript(self.CREATE_TABLE_SQL)
        conn.commit()

    def save(self, card: ActionCard) -> ActionCard:
        """Persist a new or updated ActionCard."""
        conn = self._get_connection()
        conn.execute(
            """INSERT OR REPLACE INTO action_cards (
                card_id, card_type, title, description,
                source_system, source_item_id, buttons, metadata,
                created_at, expires_at, quest_id,
                resolved, resolved_action, resolved_at, resolved_channel,
                telegram_message_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                card.card_id,
                card.card_type,
                card.title,
                card.description,
                card.source_system,
                card.source_item_id,
                json.dumps([b.to_dict() for b in card.buttons]),
                json.dumps(card.metadata),
                card.created_at,
                card.expires_at,
                card.quest_id,
                1 if card.resolved else 0,
                card.resolved_action,
                card.resolved_at,
                card.resolved_channel,
                card.telegram_message_id,
            ),
        )
        conn.commit()
        return card

    def get(self, card_id: str) -> Optional[ActionCard]:
        """Retrieve a card by full ID."""
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT * FROM action_cards WHERE card_id = ?", (card_id,)
        )
        row = cursor.fetchone()
        return self._row_to_card(row) if row else None

    def get_by_prefix(self, prefix: str) -> Optional[ActionCard]:
        """Retrieve a card by ID prefix (for Telegram callback_data lookup).

        Returns the most recent unresolved match, or most recent overall.
        """
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT * FROM action_cards WHERE card_id LIKE ? ORDER BY resolved ASC, created_at DESC LIMIT 1",
            (prefix + "%",),
        )
        row = cursor.fetchone()
        return self._row_to_card(row) if row else None

    def resolve(self, card_id: str, button_id: str, channel: str) -> bool:
        """Mark a card as resolved. Returns True if the card was found and unresolved."""
        conn = self._get_connection()
        cursor = conn.execute(
            """UPDATE action_cards
               SET resolved = 1, resolved_action = ?, resolved_at = ?, resolved_channel = ?
               WHERE card_id = ? AND resolved = 0""",
            (button_id, time.time(), channel, card_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    def set_telegram_message_id(self, card_id: str, message_id: int) -> None:
        """Record the Telegram message_id after sending an ActionCard."""
        conn = self._get_connection()
        conn.execute(
            "UPDATE action_cards SET telegram_message_id = ? WHERE card_id = ?",
            (message_id, card_id),
        )
        conn.commit()

    def list_pending(self, source_system: Optional[str] = None,
                     limit: int = 50) -> List[ActionCard]:
        """List unresolved, non-expired cards."""
        query = "SELECT * FROM action_cards WHERE resolved = 0"
        params: List[Any] = []

        if source_system:
            query += " AND source_system = ?"
            params.append(source_system)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        conn = self._get_connection()
        cursor = conn.execute(query, params)
        cards = [self._row_to_card(row) for row in cursor.fetchall()]
        # Filter out expired cards in Python (simpler than timestamp math in SQL)
        return [c for c in cards if not c.is_expired()]

    def list_all(self, limit: int = 100, include_resolved: bool = True) -> List[ActionCard]:
        """List cards with optional resolved filter."""
        if include_resolved:
            query = "SELECT * FROM action_cards ORDER BY created_at DESC LIMIT ?"
        else:
            query = "SELECT * FROM action_cards WHERE resolved = 0 ORDER BY created_at DESC LIMIT ?"
        conn = self._get_connection()
        cursor = conn.execute(query, (limit,))
        return [self._row_to_card(row) for row in cursor.fetchall()]

    def cleanup_expired(self) -> int:
        """Delete expired and resolved cards older than 24 hours."""
        cutoff = time.time() - 86400  # 24 hours
        conn = self._get_connection()
        cursor = conn.execute(
            "DELETE FROM action_cards WHERE resolved = 1 AND resolved_at < ?",
            (cutoff,),
        )
        # Also delete expired unresolved cards
        cursor2 = conn.execute(
            "DELETE FROM action_cards WHERE expires_at IS NOT NULL AND expires_at < ?",
            (time.time(),),
        )
        conn.commit()
        return cursor.rowcount + cursor2.rowcount

    def _row_to_card(self, row: sqlite3.Row) -> ActionCard:
        """Convert a database row to an ActionCard."""
        buttons_data = json.loads(row["buttons"])
        buttons = [ActionButton(**b) for b in buttons_data]

        return ActionCard(
            card_id=row["card_id"],
            card_type=row["card_type"],
            title=row["title"],
            description=row["description"],
            source_system=row["source_system"],
            source_item_id=row["source_item_id"],
            buttons=buttons,
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            quest_id=row["quest_id"],
            resolved=bool(row["resolved"]),
            resolved_action=row["resolved_action"],
            resolved_at=row["resolved_at"],
            resolved_channel=row["resolved_channel"],
            telegram_message_id=row["telegram_message_id"],
        )

    def close(self) -> None:
        """Close database connections."""
        if hasattr(self._local, "connection") and self._local.connection:
            self._local.connection.close()
            self._local.connection = None
