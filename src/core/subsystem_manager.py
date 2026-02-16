# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under AGPL-3.0. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
SubsystemManager — hot-toggle lifecycle registry for feature-gated subsystems.

Tracks init/shutdown functions for each subsystem so that feature flags can
start and stop subsystems at runtime without a container restart.

Public API:
    subsystem_manager              → singleton instance
    SubsystemManager.register()    → register a subsystem
    SubsystemManager.start()       → lazily initialize a subsystem
    SubsystemManager.stop()        → gracefully shut down a subsystem
    SubsystemManager.stop_all()    → shut down all running subsystems
    SubsystemManager.is_running()  → check if a subsystem is active
    SubsystemManager.get_by_flag() → look up subsystem by flag name
    SubsystemManager.status()      → snapshot of all subsystem states
"""

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class _SubsystemEntry:
    """Internal record for a registered subsystem."""
    name: str
    flag_name: str
    init_fn: Callable[[], dict]
    shutdown_fn: Callable[[dict], None]
    route_prefixes: list[str] = field(default_factory=list)
    running: bool = False
    objects: dict = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


class SubsystemManager:
    """Registry for hot-toggleable subsystems.

    Each subsystem is registered with:
    - name:            short identifier (e.g. "bal", "scheduler")
    - flag_name:       the FEATURE_* flag that controls it
    - init_fn:         callable that initializes the subsystem, returns dict of objects
    - shutdown_fn:     callable that tears down the subsystem, receives the objects dict
    - route_prefixes:  URL prefixes gated by middleware when subsystem is disabled
    """

    def __init__(self) -> None:
        self._subsystems: dict[str, _SubsystemEntry] = {}
        self._flag_index: dict[str, str] = {}  # flag_name → subsystem name

    def register(
        self,
        name: str,
        flag_name: str,
        init_fn: Callable[[], dict],
        shutdown_fn: Callable[[dict], None],
        route_prefixes: Optional[list[str]] = None,
    ) -> None:
        """Register a subsystem with its lifecycle functions."""
        if name in self._subsystems:
            logger.warning("Subsystem '%s' already registered — skipping", name)
            return
        entry = _SubsystemEntry(
            name=name,
            flag_name=flag_name,
            init_fn=init_fn,
            shutdown_fn=shutdown_fn,
            route_prefixes=route_prefixes or [],
        )
        self._subsystems[name] = entry
        self._flag_index[flag_name] = name
        logger.info("Subsystem registered: %s (flag=%s)", name, flag_name)

    def start(self, name: str) -> dict:
        """Initialize a subsystem. Returns the dict of created objects."""
        entry = self._subsystems.get(name)
        if not entry:
            raise ValueError(f"Unknown subsystem: '{name}'")

        with entry.lock:
            if entry.running:
                logger.info("Subsystem '%s' already running — skipping start", name)
                return entry.objects
            try:
                logger.info("Starting subsystem: %s", name)
                objects = entry.init_fn()
                entry.objects = objects or {}
                entry.running = True
                logger.info("Subsystem started: %s", name)
                return entry.objects
            except Exception as e:
                logger.error("Failed to start subsystem '%s': %s", name, e, exc_info=True)
                raise

    def stop(self, name: str) -> None:
        """Gracefully shut down a subsystem."""
        entry = self._subsystems.get(name)
        if not entry:
            raise ValueError(f"Unknown subsystem: '{name}'")

        with entry.lock:
            if not entry.running:
                logger.info("Subsystem '%s' not running — skipping stop", name)
                return
            try:
                logger.info("Stopping subsystem: %s", name)
                entry.shutdown_fn(entry.objects)
                entry.objects = {}
                entry.running = False
                logger.info("Subsystem stopped: %s", name)
            except Exception as e:
                logger.error("Failed to stop subsystem '%s': %s", name, e, exc_info=True)
                entry.running = False

    def stop_all(self) -> None:
        """Stop all running subsystems (used during gateway shutdown)."""
        for name, entry in self._subsystems.items():
            if entry.running:
                try:
                    self.stop(name)
                except Exception as e:
                    logger.error("Error stopping subsystem '%s' during shutdown: %s", name, e)

    def is_running(self, name: str) -> bool:
        """Check if a subsystem is currently initialized and running."""
        entry = self._subsystems.get(name)
        return entry.running if entry else False

    def get_by_flag(self, flag_name: str) -> Optional[_SubsystemEntry]:
        """Look up a subsystem entry by its feature flag name."""
        name = self._flag_index.get(flag_name)
        if name:
            return self._subsystems.get(name)
        return None

    def status(self) -> dict:
        """Return a snapshot of all registered subsystems and their states."""
        return {
            name: {
                "flag": entry.flag_name,
                "running": entry.running,
                "route_prefixes": entry.route_prefixes,
            }
            for name, entry in self._subsystems.items()
        }


# Singleton instance — imported by gateway.py and flags_api.py
subsystem_manager = SubsystemManager()
