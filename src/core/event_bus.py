"""
War Room Event Bus — in-memory pub/sub for backend modules to emit events.

Modules publish events (approval_requested, task_started, health_change, etc.)
and the WebSocket connection manager subscribes to broadcast them to connected
War Room clients.

Thread-safe: publishers may run on background threads.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)


@dataclass
class Event:
    """A single event emitted by a backend module."""
    type: str
    payload: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }


# Type alias for async subscriber callbacks
Subscriber = Callable[[Event], Awaitable[None]]


class EventBus:
    """Simple in-memory pub/sub event bus."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Subscriber]] = {}
        self._global_subscribers: list[Subscriber] = []

    def subscribe(self, event_type: str, callback: Subscriber) -> None:
        """Subscribe to a specific event type."""
        self._subscribers.setdefault(event_type, []).append(callback)

    def subscribe_all(self, callback: Subscriber) -> None:
        """Subscribe to all events."""
        self._global_subscribers.append(callback)

    async def publish(self, event: Event) -> None:
        """Publish an event to all matching subscribers."""
        callbacks = list(self._global_subscribers)
        callbacks.extend(self._subscribers.get(event.type, []))

        for cb in callbacks:
            try:
                await cb(event)
            except Exception as exc:
                logger.error("Event subscriber error for %s: %s", event.type, exc)

    def publish_sync(self, event: Event) -> None:
        """Publish from a synchronous context (creates a task on the running loop)."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.publish(event))
        except RuntimeError:
            # No running loop — log and skip
            logger.debug("No event loop for sync publish of %s", event.type)


# Global singleton
event_bus = EventBus()
