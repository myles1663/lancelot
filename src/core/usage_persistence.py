"""
UsagePersistence — Monthly usage history stored to disk.

Persists per-model, per-lane, and per-day usage data to ``usage_history.json``
so the War Room Cost Tracker panel survives container restarts.

Data is accumulated in memory and flushed to disk periodically (every
``_FLUSH_INTERVAL`` records) or on explicit ``flush()`` calls.

Public API:
    UsagePersistence(data_dir)
    persistence.record(model, tokens, cost, lane=None)
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

    @staticmethod
    def _empty_month() -> dict:
        return {
            "total_requests": 0,
            "total_tokens": 0,
            "total_cost": 0.0,
            "by_model": {},
            "by_lane": {},
            "by_day": {},
        }

    @staticmethod
    def _legacy_lane_from_totals(month: dict) -> dict:
        requests = int(month.get("total_requests") or 0)
        if requests <= 0:
            return {}
        return {
            "legacy_unclassified": {
                "requests": requests,
                "successes": requests,
                "failures": 0,
                "total_tokens_est": int(month.get("total_tokens") or 0),
                "total_cost_est": round(float(month.get("total_cost") or 0.0), 6),
                "total_elapsed_ms": 0.0,
                "avg_elapsed_ms": 0.0,
            }
        }

    def _month_view(self, month_key: str, month: dict) -> dict:
        view = {"month": month_key, **self._empty_month(), **month}
        if not view.get("by_lane"):
            view["by_lane"] = self._legacy_lane_from_totals(view)
        return view

    def record(
        self,
        model: str,
        tokens: int,
        cost: float,
        *,
        lane: str | None = None,
        success: bool = True,
        elapsed_ms: float = 0.0,
    ) -> None:
        """Accumulate a single LLM call into the current month bucket."""
        now = datetime.now(timezone.utc)
        month_key = now.strftime("%Y-%m")
        day_key = now.strftime("%Y-%m-%d")

        with self._lock:
            self._data["current_month"] = month_key

            if month_key not in self._data["months"]:
                self._data["months"][month_key] = self._empty_month()

            month = self._data["months"][month_key]
            month.setdefault("by_model", {})
            month.setdefault("by_lane", {})
            month.setdefault("by_day", {})
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

            # Per-lane. Older call sites may not provide lane context; keep those
            # visible as an explicit unclassified bucket instead of dropping them.
            lane_name = lane or "unclassified"
            if lane_name not in month["by_lane"]:
                month["by_lane"][lane_name] = {
                    "requests": 0,
                    "successes": 0,
                    "failures": 0,
                    "total_tokens_est": 0,
                    "total_cost_est": 0.0,
                    "total_elapsed_ms": 0.0,
                    "avg_elapsed_ms": 0.0,
                }
            l = month["by_lane"][lane_name]
            l["requests"] += 1
            if success:
                l["successes"] += 1
            else:
                l["failures"] += 1
            l["total_tokens_est"] += tokens
            l["total_cost_est"] = round(l["total_cost_est"] + cost, 6)
            l["total_elapsed_ms"] = round(l["total_elapsed_ms"] + elapsed_ms, 2)
            l["avg_elapsed_ms"] = round(l["total_elapsed_ms"] / l["requests"], 2)

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
                return self._month_view(key, self._data["months"][key])
        return {"month": datetime.now(timezone.utc).strftime("%Y-%m"), **self._empty_month()}

    def get_month(self, month_key: str) -> dict:
        """Return data for a specific month (or empty)."""
        with self._lock:
            if month_key in self._data["months"]:
                return self._month_view(month_key, self._data["months"][month_key])
        return {"month": month_key, **self._empty_month()}

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
