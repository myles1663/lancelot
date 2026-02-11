"""
UsagePersistence — Monthly usage history stored to disk.

Persists per-model, per-day usage data to ``usage_history.json`` so the
War Room Cost Tracker panel survives container restarts.

Data is accumulated in memory and flushed to disk periodically (every
``_FLUSH_INTERVAL`` records) or on explicit ``flush()`` calls.

Public API:
    UsagePersistence(data_dir)
    persistence.record(model, tokens, cost)
    persistence.get_current_month() -> dict
    persistence.get_month(month_key)  -> dict
    persistence.get_available_months() -> list[str]
    persistence.flush()
"""

import json
import logging
import os
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_FLUSH_INTERVAL = 10  # flush to disk every N records


class UsagePersistence:
    """Persists monthly usage data to JSON for the Cost Tracker panel."""

    def __init__(self, data_dir: str) -> None:
        self._path = os.path.join(data_dir, "usage_history.json")
        self._lock = threading.Lock()
        self._dirty = 0  # records since last flush
        self._data: dict = self._load()

    # ------------------------------------------------------------------
    # Loading / saving
    # ------------------------------------------------------------------

    def _load(self) -> dict:
        if os.path.exists(self._path):
            try:
                with open(self._path, "r") as fh:
                    data = json.load(fh)
                    logger.info("UsagePersistence: loaded %s", self._path)
                    return data
            except Exception as exc:
                logger.warning("UsagePersistence: failed to load %s: %s", self._path, exc)
        return {"current_month": "", "months": {}}

    def _save(self) -> None:
        tmp = self._path + ".tmp"
        try:
            with open(tmp, "w") as fh:
                json.dump(self._data, fh, indent=2)
            os.replace(tmp, self._path)
        except Exception as exc:
            logger.error("UsagePersistence: failed to save: %s", exc)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(self, model: str, tokens: int, cost: float) -> None:
        """Accumulate a single LLM call into the current month bucket."""
        now = datetime.now(timezone.utc)
        month_key = now.strftime("%Y-%m")
        day_key = now.strftime("%Y-%m-%d")

        with self._lock:
            self._data["current_month"] = month_key

            if month_key not in self._data["months"]:
                self._data["months"][month_key] = {
                    "total_requests": 0,
                    "total_tokens": 0,
                    "total_cost": 0.0,
                    "by_model": {},
                    "by_day": {},
                }

            month = self._data["months"][month_key]
            month["total_requests"] += 1
            month["total_tokens"] += tokens
            month["total_cost"] = round(month["total_cost"] + cost, 6)

            # Per-model
            if model not in month["by_model"]:
                month["by_model"][model] = {"requests": 0, "tokens": 0, "cost": 0.0}
            m = month["by_model"][model]
            m["requests"] += 1
            m["tokens"] += tokens
            m["cost"] = round(m["cost"] + cost, 6)

            # Per-day
            if day_key not in month["by_day"]:
                month["by_day"][day_key] = {"requests": 0, "tokens": 0, "cost": 0.0}
            d = month["by_day"][day_key]
            d["requests"] += 1
            d["tokens"] += tokens
            d["cost"] = round(d["cost"] + cost, 6)

            self._dirty += 1
            # Always flush the first record (ensures file creation) and
            # periodically after that.
            if self._dirty >= _FLUSH_INTERVAL or self._dirty == 1:
                self._save()
                self._dirty = 0

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_current_month(self) -> dict:
        """Return the current month's data (or empty structure)."""
        with self._lock:
            key = self._data.get("current_month", "")
            if key and key in self._data["months"]:
                return {"month": key, **self._data["months"][key]}
        return {
            "month": datetime.now(timezone.utc).strftime("%Y-%m"),
            "total_requests": 0,
            "total_tokens": 0,
            "total_cost": 0.0,
            "by_model": {},
            "by_day": {},
        }

    def get_month(self, month_key: str) -> dict:
        """Return data for a specific month (or empty)."""
        with self._lock:
            if month_key in self._data["months"]:
                return {"month": month_key, **self._data["months"][month_key]}
        return {
            "month": month_key,
            "total_requests": 0,
            "total_tokens": 0,
            "total_cost": 0.0,
            "by_model": {},
            "by_day": {},
        }

    def get_available_months(self) -> list:
        """Return sorted list of month keys with data."""
        with self._lock:
            return sorted(self._data.get("months", {}).keys(), reverse=True)

    # ------------------------------------------------------------------
    # Flush
    # ------------------------------------------------------------------

    def flush(self) -> None:
        """Force-write current state to disk."""
        with self._lock:
            self._save()
            self._dirty = 0
            logger.info("UsagePersistence: flushed to disk")
