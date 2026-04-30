"""
Client Events — receipt emission for client lifecycle events.

Each function wraps emit_bal_receipt() with the appropriate payload
for a specific client lifecycle event.
"""

from __future__ import annotations

import logging
from typing import List

from src.core.bal.clients.models import Client, ClientStatus, PlanTier
from src.core.bal.receipts import emit_bal_receipt

logger = logging.getLogger(__name__)


def emit_client_onboarded(client: Client) -> None:
    """Emit receipt when a new client is created / onboarded."""
    emit_bal_receipt(
        event_type="client",
        action_name="client_onboarded",
        inputs={
            "client_id": client.id,
            "name": client.name,
            "email": client.email,
            "plan_tier": client.plan_tier.value,
        },
    )


def emit_client_preferences_updated(
    client: Client, changed_fields: List[str]
) -> None:
    """Emit receipt when client preferences are updated."""
    emit_bal_receipt(
        event_type="client",
        action_name="client_preferences_updated",
        inputs={
            "client_id": client.id,
            "changed_fields": changed_fields,
        },
    )


def emit_client_status_changed(
    client: Client,
    old_status: ClientStatus,
    new_status: ClientStatus,
    reason: str,
) -> None:
    """Emit receipt for any status transition."""
    emit_bal_receipt(
        event_type="client",
        action_name="client_status_changed",
        inputs={
            "client_id": client.id,
            "old_status": old_status.value,
            "new_status": new_status.value,
            "reason": reason,
        },
    )


def emit_client_plan_changed(
    client: Client, old_tier: PlanTier, new_tier: PlanTier
) -> None:
    """Emit receipt when a client's plan tier changes."""
    emit_bal_receipt(
        event_type="client",
        action_name="client_plan_changed",
        inputs={
            "client_id": client.id,
            "old_tier": old_tier.value,
            "new_tier": new_tier.value,
        },
    )


def emit_client_paused(client: Client, reason: str) -> None:
    """Emit receipt when a client is paused."""
    emit_bal_receipt(
        event_type="client",
        action_name="client_paused",
        inputs={
            "client_id": client.id,
            "reason": reason,
        },
    )


def emit_client_churned(client: Client, reason: str) -> None:
    """Emit receipt when a client churns (terminal state)."""
    emit_bal_receipt(
        event_type="client",
        action_name="client_churned",
        inputs={
            "client_id": client.id,
            "reason": reason,
        },
    )
