"""
Client Repository — SQLite persistence for BAL clients.

All CRUD operations for the bal_clients table.  Uses the BALDatabase
transaction context manager for atomic operations.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from src.core.bal.clients.models import (
    Client,
    ClientBilling,
    ClientCreate,
    ClientPreferences,
    ClientStatus,
    ClientUpdate,
    ContentHistory,
    PlanTier,
)
from src.core.bal.database import BALDatabase

logger = logging.getLogger(__name__)


class ClientRepository:
    """SQLite-backed repository for BAL client records."""

    def __init__(self, db: BALDatabase):
        self._db = db

    def create(self, client_create: ClientCreate) -> Client:
        """Create a new client from a ClientCreate input model."""
        now = datetime.now(timezone.utc)
        client_id = str(uuid.uuid4())

        preferences = client_create.preferences or ClientPreferences()
        billing = ClientBilling()

        client = Client(
            id=client_id,
            name=client_create.name,
            email=client_create.email,
            status=ClientStatus.ONBOARDING,
            plan_tier=client_create.plan_tier,
            billing=billing,
            preferences=preferences,
            content_history=ContentHistory(),
            created_at=now,
            updated_at=now,
        )

        with self._db.transaction() as conn:
            conn.execute(
                """INSERT INTO bal_clients
                   (id, name, email, tier, status, preferences_json, billing_json,
                    metadata, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)""",
                (
                    client.id,
                    client.name,
                    client.email,
                    client.plan_tier.value,
                    client.status.value,
                    preferences.model_dump_json(),
                    billing.model_dump_json(),
                    client.created_at.isoformat(),
                    client.updated_at.isoformat(),
                ),
            )

        logger.info("Client created: id=%s, name=%s", client.id, client.name)
        return client

    def get_by_id(self, client_id: str) -> Optional[Client]:
        """Fetch a client by primary key."""
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM bal_clients WHERE id = ?", (client_id,)
            ).fetchone()

        if row is None:
            return None
        return self._row_to_client(row)

    def get_by_email(self, email: str) -> Optional[Client]:
        """Fetch a client by email address."""
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM bal_clients WHERE email = ?", (email.lower(),)
            ).fetchone()

        if row is None:
            return None
        return self._row_to_client(row)

    def list_all(self, status_filter: Optional[ClientStatus] = None) -> List[Client]:
        """List all clients, optionally filtered by status."""
        with self._db.transaction() as conn:
            if status_filter:
                rows = conn.execute(
                    "SELECT * FROM bal_clients WHERE status = ? ORDER BY created_at DESC",
                    (status_filter.value,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM bal_clients ORDER BY created_at DESC"
                ).fetchall()

        return [self._row_to_client(row) for row in rows]

    def update(self, client_id: str, update: ClientUpdate) -> Client:
        """Apply a partial update to a client."""
        client = self.get_by_id(client_id)
        if client is None:
            raise ValueError(f"Client not found: {client_id}")

        now = datetime.now(timezone.utc)
        sets = ["updated_at = ?"]
        params: list = [now.isoformat()]

        if update.name is not None:
            sets.append("name = ?")
            params.append(update.name)

        if update.email is not None:
            sets.append("email = ?")
            params.append(update.email)

        if update.preferences is not None:
            sets.append("preferences_json = ?")
            params.append(update.preferences.model_dump_json())

        params.append(client_id)

        with self._db.transaction() as conn:
            conn.execute(
                f"UPDATE bal_clients SET {', '.join(sets)} WHERE id = ?",
                params,
            )

        return self.get_by_id(client_id)  # type: ignore

    def update_status(self, client_id: str, new_status: ClientStatus) -> Client:
        """Update a client's status."""
        now = datetime.now(timezone.utc)
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE bal_clients SET status = ?, updated_at = ? WHERE id = ?",
                (new_status.value, now.isoformat(), client_id),
            )
        client = self.get_by_id(client_id)
        if client is None:
            raise ValueError(f"Client not found: {client_id}")
        return client

    def update_billing(self, client_id: str, billing: ClientBilling) -> Client:
        """Update a client's billing information."""
        now = datetime.now(timezone.utc)
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE bal_clients SET billing_json = ?, updated_at = ? WHERE id = ?",
                (billing.model_dump_json(), now.isoformat(), client_id),
            )
        client = self.get_by_id(client_id)
        if client is None:
            raise ValueError(f"Client not found: {client_id}")
        return client

    def update_content_history(
        self, client_id: str, history: ContentHistory
    ) -> Client:
        """Update a client's content delivery history."""
        now = datetime.now(timezone.utc)
        # Store content_history in metadata JSON since it's not a dedicated column
        client = self.get_by_id(client_id)
        if client is None:
            raise ValueError(f"Client not found: {client_id}")

        import json

        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT metadata FROM bal_clients WHERE id = ?", (client_id,)
            ).fetchone()
            metadata = json.loads(row["metadata"]) if row else {}
            metadata["content_history"] = json.loads(history.model_dump_json())
            conn.execute(
                "UPDATE bal_clients SET metadata = ?, updated_at = ? WHERE id = ?",
                (json.dumps(metadata), now.isoformat(), client_id),
            )
        return self.get_by_id(client_id)  # type: ignore

    def update_memory_block_id(
        self, client_id: str, memory_block_id: str
    ) -> Client:
        """Store the memory block ID for a client."""
        now = datetime.now(timezone.utc)
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE bal_clients SET memory_block_id = ?, updated_at = ? WHERE id = ?",
                (memory_block_id, now.isoformat(), client_id),
            )
        client = self.get_by_id(client_id)
        if client is None:
            raise ValueError(f"Client not found: {client_id}")
        return client

    def delete(self, client_id: str) -> bool:
        """Soft delete — sets status to CHURNED."""
        client = self.get_by_id(client_id)
        if client is None:
            return False
        self.update_status(client_id, ClientStatus.CHURNED)
        logger.info("Client soft-deleted (churned): id=%s", client_id)
        return True

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _row_to_client(row) -> Client:
        """Convert a sqlite3.Row to a Client model."""
        import json

        preferences = ClientPreferences.model_validate_json(
            row["preferences_json"]
        )
        billing = ClientBilling.model_validate_json(row["billing_json"])

        # Content history may be stored in metadata
        metadata = json.loads(row["metadata"])
        content_history_data = metadata.get("content_history")
        content_history = (
            ContentHistory(**content_history_data)
            if content_history_data
            else ContentHistory()
        )

        return Client(
            id=row["id"],
            name=row["name"],
            email=row["email"],
            status=ClientStatus(row["status"]),
            plan_tier=PlanTier(row["tier"]),
            billing=billing,
            preferences=preferences,
            content_history=content_history,
            memory_block_id=row["memory_block_id"] if "memory_block_id" in row.keys() else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
