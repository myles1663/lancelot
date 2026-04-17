"""
Runtime Pause Manager.

Persists an instance-wide pause state so the War Room can stop new work
across execution ingress points without relying on chat interpretation.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class RuntimePauseSnapshot:
    paused: bool
    reason: Optional[str]
    source: Optional[str]
    paused_at: Optional[str]
    paused_by_operator_id: Optional[str]
    paused_by_display_name: Optional[str]
    paused_by_session_id: Optional[str]
    resumed_at: Optional[str]
    resumed_by_operator_id: Optional[str]
    resumed_by_display_name: Optional[str]
    resumed_by_session_id: Optional[str]
    updated_at: Optional[str]

    def to_dict(self) -> dict:
        return {
            "paused": self.paused,
            "reason": self.reason,
            "source": self.source,
            "paused_at": self.paused_at,
            "paused_by_operator_id": self.paused_by_operator_id,
            "paused_by_display_name": self.paused_by_display_name,
            "paused_by_session_id": self.paused_by_session_id,
            "resumed_at": self.resumed_at,
            "resumed_by_operator_id": self.resumed_by_operator_id,
            "resumed_by_display_name": self.resumed_by_display_name,
            "resumed_by_session_id": self.resumed_by_session_id,
            "updated_at": self.updated_at,
        }


class RuntimePausedError(RuntimeError):
    """Raised when a runtime-wide pause blocks new work."""


class RuntimePauseManager:
    def __init__(self, state_file: Path):
        self._state_file = state_file
        self._lock = threading.RLock()
        self._snapshot = RuntimePauseSnapshot(
            paused=False,
            reason=None,
            source=None,
            paused_at=None,
            paused_by_operator_id=None,
            paused_by_display_name=None,
            paused_by_session_id=None,
            resumed_at=None,
            resumed_by_operator_id=None,
            resumed_by_display_name=None,
            resumed_by_session_id=None,
            updated_at=None,
        )
        self._load()

    def _load(self) -> None:
        with self._lock:
            try:
                if not self._state_file.exists():
                    return
                payload = json.loads(self._state_file.read_text(encoding="utf-8"))
                self._snapshot = RuntimePauseSnapshot(
                    paused=bool(payload.get("paused", False)),
                    reason=payload.get("reason"),
                    source=payload.get("source"),
                    paused_at=payload.get("paused_at"),
                    paused_by_operator_id=payload.get("paused_by_operator_id"),
                    paused_by_display_name=payload.get("paused_by_display_name"),
                    paused_by_session_id=payload.get("paused_by_session_id"),
                    resumed_at=payload.get("resumed_at"),
                    resumed_by_operator_id=payload.get("resumed_by_operator_id"),
                    resumed_by_display_name=payload.get("resumed_by_display_name"),
                    resumed_by_session_id=payload.get("resumed_by_session_id"),
                    updated_at=payload.get("updated_at"),
                )
            except Exception as exc:
                logger.warning("Failed to load runtime pause state: %s", exc)

    def _save(self) -> None:
        with self._lock:
            try:
                self._state_file.parent.mkdir(parents=True, exist_ok=True)
                self._state_file.write_text(
                    json.dumps(self._snapshot.to_dict(), indent=2, sort_keys=True),
                    encoding="utf-8",
                )
            except Exception as exc:
                logger.warning("Failed to persist runtime pause state: %s", exc)

    def snapshot(self) -> RuntimePauseSnapshot:
        with self._lock:
            return self._snapshot

    def is_paused(self) -> bool:
        with self._lock:
            return self._snapshot.paused

    def pause(
        self,
        reason: str,
        *,
        operator_id: str = "",
        operator_name: str = "",
        session_id: str = "",
        source: str = "warroom",
    ) -> RuntimePauseSnapshot:
        with self._lock:
            now = _utcnow()
            self._snapshot = RuntimePauseSnapshot(
                paused=True,
                reason=reason.strip() or "Paused by operator",
                source=source,
                paused_at=self._snapshot.paused_at or now,
                paused_by_operator_id=operator_id or None,
                paused_by_display_name=operator_name or None,
                paused_by_session_id=session_id or None,
                resumed_at=None,
                resumed_by_operator_id=None,
                resumed_by_display_name=None,
                resumed_by_session_id=None,
                updated_at=now,
            )
            self._save()
            return self._snapshot

    def resume(
        self,
        *,
        operator_id: str = "",
        operator_name: str = "",
        session_id: str = "",
        source: str = "warroom",
    ) -> RuntimePauseSnapshot:
        with self._lock:
            now = _utcnow()
            self._snapshot = RuntimePauseSnapshot(
                paused=False,
                reason=None,
                source=source,
                paused_at=self._snapshot.paused_at,
                paused_by_operator_id=self._snapshot.paused_by_operator_id,
                paused_by_display_name=self._snapshot.paused_by_display_name,
                paused_by_session_id=self._snapshot.paused_by_session_id,
                resumed_at=now,
                resumed_by_operator_id=operator_id or None,
                resumed_by_display_name=operator_name or None,
                resumed_by_session_id=session_id or None,
                updated_at=now,
            )
            self._save()
            return self._snapshot


_manager: Optional[RuntimePauseManager] = None
_manager_lock = threading.Lock()


def init_runtime_pause(data_dir: str = "/home/lancelot/data") -> RuntimePauseManager:
    global _manager
    with _manager_lock:
        _manager = RuntimePauseManager(Path(data_dir) / "runtime_pause.json")
        return _manager


def get_runtime_pause_manager() -> RuntimePauseManager:
    global _manager
    if _manager is None:
        return init_runtime_pause()
    return _manager


def get_runtime_pause_status() -> dict:
    return get_runtime_pause_manager().snapshot().to_dict()


def is_runtime_paused() -> bool:
    return get_runtime_pause_manager().is_paused()


def pause_runtime(
    reason: str,
    *,
    operator_id: str = "",
    operator_name: str = "",
    session_id: str = "",
    source: str = "warroom",
) -> dict:
    return get_runtime_pause_manager().pause(
        reason,
        operator_id=operator_id,
        operator_name=operator_name,
        session_id=session_id,
        source=source,
    ).to_dict()


def resume_runtime(
    *,
    operator_id: str = "",
    operator_name: str = "",
    session_id: str = "",
    source: str = "warroom",
) -> dict:
    return get_runtime_pause_manager().resume(
        operator_id=operator_id,
        operator_name=operator_name,
        session_id=session_id,
        source=source,
    ).to_dict()


def assert_runtime_not_paused(context: str = "runtime") -> None:
    snapshot = get_runtime_pause_manager().snapshot()
    if snapshot.paused:
        reason = snapshot.reason or "Paused by operator"
        raise RuntimePausedError(f"{context} is paused: {reason}")
