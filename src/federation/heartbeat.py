# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Federation Heartbeat — Background heartbeat emission and staleness tracking.

Each instance emits a heartbeat at a configurable interval (default 2s).
The heartbeat carries instance identity, Soul version hash, deployment
mode, and health summary. Peer staleness is computed from heartbeat age.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Heartbeat:
    """A single heartbeat emission."""
    instance_id: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    soul_version_hash: str = ""
    deployment_mode: str = "standalone"
    active_task_count: int = 0
    budget_utilization_pct: float = 0.0
    peer_count: int = 0
    signature: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class StalenessLevel:
    """Staleness threshold constants."""
    FRESH = "fresh"          # Within normal heartbeat window
    WARNING = "warning"      # 10-20s — delayed but possibly transient
    CRITICAL = "critical"    # 20-30s — likely connectivity issue
    LOST = "lost"            # >30s — instance unreachable


def compute_staleness(
    last_heartbeat_iso: Optional[str],
    warning_s: float = 10.0,
    critical_s: float = 20.0,
    lost_s: float = 30.0,
) -> tuple[str, float]:
    """Compute staleness level and age in seconds from last heartbeat.

    Returns:
        Tuple of (staleness_level, age_seconds). If no heartbeat
        has ever been received, returns (LOST, -1.0).
    """
    if not last_heartbeat_iso:
        return StalenessLevel.LOST, -1.0

    try:
        last_time = datetime.fromisoformat(last_heartbeat_iso)
        if last_time.tzinfo is None:
            last_time = last_time.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        age = (now - last_time).total_seconds()
    except (ValueError, TypeError):
        return StalenessLevel.LOST, -1.0

    if age < 0:
        age = 0.0

    if age >= lost_s:
        return StalenessLevel.LOST, age
    elif age >= critical_s:
        return StalenessLevel.CRITICAL, age
    elif age >= warning_s:
        return StalenessLevel.WARNING, age
    else:
        return StalenessLevel.FRESH, age


class HeartbeatEmitter:
    """Background heartbeat emitter following HealthMonitor pattern.

    Emits heartbeats at a configurable interval. Subscribers receive
    heartbeat dicts via registered callbacks. A ring buffer stores
    the last N heartbeats for late subscribers.
    """

    def __init__(
        self,
        instance_id: str,
        interval_s: float = 2.0,
        buffer_size: int = 100,
    ):
        self._instance_id = instance_id
        self._interval_s = interval_s
        self._buffer: Deque[Heartbeat] = deque(maxlen=buffer_size)
        self._subscribers: List[Callable[[Heartbeat], None]] = []
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # State providers — callables that return current values
        self._soul_hash_provider: Optional[Callable[[], str]] = None
        self._mode_provider: Optional[Callable[[], str]] = None
        self._task_count_provider: Optional[Callable[[], int]] = None
        self._budget_provider: Optional[Callable[[], float]] = None
        self._peer_count_provider: Optional[Callable[[], int]] = None
        self._sign_provider: Optional[Callable[[bytes], bytes]] = None

    def set_providers(
        self,
        soul_hash: Optional[Callable[[], str]] = None,
        mode: Optional[Callable[[], str]] = None,
        task_count: Optional[Callable[[], int]] = None,
        budget: Optional[Callable[[], float]] = None,
        peer_count: Optional[Callable[[], int]] = None,
        sign: Optional[Callable[[bytes], bytes]] = None,
    ) -> None:
        """Register state providers for heartbeat fields."""
        if soul_hash:
            self._soul_hash_provider = soul_hash
        if mode:
            self._mode_provider = mode
        if task_count:
            self._task_count_provider = task_count
        if budget:
            self._budget_provider = budget
        if peer_count:
            self._peer_count_provider = peer_count
        if sign:
            self._sign_provider = sign

    def subscribe(self, callback: Callable[[Heartbeat], None]) -> None:
        """Register a callback to receive heartbeat emissions."""
        with self._lock:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[Heartbeat], None]) -> None:
        """Remove a heartbeat subscriber."""
        with self._lock:
            self._subscribers = [s for s in self._subscribers if s is not callback]

    def get_latest(self) -> Optional[Heartbeat]:
        """Return the most recent heartbeat, or None."""
        with self._lock:
            return self._buffer[-1] if self._buffer else None

    def get_history(self, count: int = 10) -> List[Heartbeat]:
        """Return the last N heartbeats."""
        with self._lock:
            return list(self._buffer)[-count:]

    def emit_once(self) -> Heartbeat:
        """Emit a single heartbeat immediately. Useful for testing."""
        hb = Heartbeat(
            instance_id=self._instance_id,
            soul_version_hash=(
                self._soul_hash_provider() if self._soul_hash_provider else ""
            ),
            deployment_mode=(
                self._mode_provider() if self._mode_provider else "standalone"
            ),
            active_task_count=(
                self._task_count_provider() if self._task_count_provider else 0
            ),
            budget_utilization_pct=(
                self._budget_provider() if self._budget_provider else 0.0
            ),
            peer_count=(
                self._peer_count_provider() if self._peer_count_provider else 0
            ),
        )

        with self._lock:
            self._buffer.append(hb)
            subscribers = list(self._subscribers)

        for sub in subscribers:
            try:
                sub(hb)
            except Exception as exc:
                logger.warning("Heartbeat subscriber error: %s", exc)

        return hb

    def start(self) -> None:
        """Start the background heartbeat thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("HeartbeatEmitter already running")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="federation-heartbeat",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "HeartbeatEmitter started: interval=%.1fs, instance=%s",
            self._interval_s, self._instance_id,
        )

    def stop(self) -> None:
        """Stop the background heartbeat thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self._interval_s + 1)
            self._thread = None
        logger.info("HeartbeatEmitter stopped")

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        """Background loop — emit heartbeat at configured interval."""
        while not self._stop_event.is_set():
            try:
                self.emit_once()
            except Exception as exc:
                logger.error("Heartbeat emission error: %s", exc)
            self._stop_event.wait(timeout=self._interval_s)
