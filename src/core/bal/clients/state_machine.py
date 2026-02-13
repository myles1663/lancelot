"""
Client State Machine — validates and executes client lifecycle transitions.

Valid transitions:
    ONBOARDING -> [ACTIVE, CHURNED]
    ACTIVE     -> [PAUSED, CHURNED]
    PAUSED     -> [ACTIVE, CHURNED]
    CHURNED    -> []  (terminal state)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Set

from src.core.bal.clients.models import Client, ClientStatus

if TYPE_CHECKING:
    from src.core.bal.clients.repository import ClientRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Transition map
# ---------------------------------------------------------------------------

_VALID_TRANSITIONS: dict[ClientStatus, Set[ClientStatus]] = {
    ClientStatus.ONBOARDING: {ClientStatus.ACTIVE, ClientStatus.CHURNED},
    ClientStatus.ACTIVE: {ClientStatus.PAUSED, ClientStatus.CHURNED},
    ClientStatus.PAUSED: {ClientStatus.ACTIVE, ClientStatus.CHURNED},
    ClientStatus.CHURNED: set(),  # terminal
}


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class InvalidTransitionError(Exception):
    """Raised when an invalid client status transition is attempted."""

    def __init__(
        self,
        current_status: ClientStatus,
        target_status: ClientStatus,
        client_id: str,
    ):
        self.current_status = current_status
        self.target_status = target_status
        self.client_id = client_id
        super().__init__(
            f"Invalid transition for client {client_id}: "
            f"{current_status.value} -> {target_status.value}"
        )


# ---------------------------------------------------------------------------
# State Machine
# ---------------------------------------------------------------------------

class ClientStateMachine:
    """Deterministic state machine for client lifecycle transitions."""

    @staticmethod
    def validate_transition(
        current: ClientStatus, target: ClientStatus
    ) -> bool:
        """Check if a transition from current to target is valid."""
        return target in _VALID_TRANSITIONS.get(current, set())

    @staticmethod
    def transition(
        client_id: str,
        target: ClientStatus,
        repository: ClientRepository,
        reason: str = "",
    ) -> Client:
        """Execute a state transition, updating the repository.

        Args:
            client_id: The client to transition.
            target: The desired new status.
            repository: ClientRepository for persistence.
            reason: Optional human-readable reason.

        Returns:
            The updated Client.

        Raises:
            InvalidTransitionError: If the transition is not allowed.
            ValueError: If client_id is not found.
        """
        client = repository.get_by_id(client_id)
        if client is None:
            raise ValueError(f"Client not found: {client_id}")

        current = client.status

        if not ClientStateMachine.validate_transition(current, target):
            raise InvalidTransitionError(current, target, client_id)

        updated = repository.update_status(client_id, target)
        logger.info(
            "Client %s transitioned: %s -> %s (reason=%s)",
            client_id,
            current.value,
            target.value,
            reason or "none",
        )
        return updated
