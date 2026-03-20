# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Incident Store — JSON file persistence for incident records.

Each incident is a single JSON file in {data_dir}/incidents/{incident_id}.json.
An index file (_index.json) enables fast listing without reading every file.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional

from src.incidents.models import (
    IncidentRecord,
    IncidentStatus,
    IncidentCategory,
    IncidentSeverity,
)

logger = logging.getLogger("lancelot.incidents.store")

# Singleton
_store_instance: Optional[IncidentStore] = None
_store_lock = threading.Lock()


class IncidentStore:
    """Thread-safe JSON file store for incident records."""

    def __init__(self, data_dir: str):
        self._data_dir = data_dir
        self._incidents_dir = os.path.join(data_dir, "incidents")
        self._index_path = os.path.join(self._incidents_dir, "_index.json")
        self._lock = threading.Lock()
        os.makedirs(self._incidents_dir, exist_ok=True)
        self._index: Dict[str, Dict] = self._load_index()

    def _load_index(self) -> Dict[str, Dict]:
        """Load the index file, or rebuild from individual files."""
        if os.path.exists(self._index_path):
            try:
                with open(self._index_path, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Index file corrupt, rebuilding: %s", exc)

        # Rebuild from individual files
        index = {}
        if os.path.isdir(self._incidents_dir):
            for fname in os.listdir(self._incidents_dir):
                if fname.startswith("_") or not fname.endswith(".json"):
                    continue
                try:
                    path = os.path.join(self._incidents_dir, fname)
                    with open(path, "r") as f:
                        data = json.load(f)
                    iid = data.get("incident_id", fname.replace(".json", ""))
                    index[iid] = {
                        "category": data.get("category"),
                        "severity": data.get("severity"),
                        "status": data.get("status"),
                        "playbook_name": data.get("playbook_name"),
                        "opened_at": data.get("opened_at"),
                        "dedup_key": data.get("dedup_key"),
                    }
                except Exception as exc:
                    logger.warning("Failed to index %s: %s", fname, exc)

        self._save_index(index)
        return index

    def _save_index(self, index: Optional[Dict] = None) -> None:
        idx = index if index is not None else self._index
        try:
            with open(self._index_path, "w") as f:
                json.dump(idx, f, indent=2)
        except OSError as exc:
            logger.error("Failed to write index: %s", exc)

    def _incident_path(self, incident_id: str) -> str:
        return os.path.join(self._incidents_dir, f"{incident_id}.json")

    def create(self, incident: IncidentRecord) -> IncidentRecord:
        """Persist a new incident record."""
        with self._lock:
            path = self._incident_path(incident.incident_id)
            with open(path, "w") as f:
                json.dump(incident.to_dict(), f, indent=2)

            self._index[incident.incident_id] = {
                "category": incident.category,
                "severity": incident.severity,
                "status": incident.status,
                "playbook_name": incident.playbook_name,
                "opened_at": incident.opened_at,
                "dedup_key": incident.dedup_key,
            }
            self._save_index()

        logger.info("Incident created: %s [%s/%s] playbook=%s",
                     incident.incident_id, incident.category,
                     incident.severity, incident.playbook_name)
        return incident

    def get(self, incident_id: str) -> Optional[IncidentRecord]:
        """Load a single incident by ID."""
        path = self._incident_path(incident_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r") as f:
                data = json.load(f)
            return IncidentRecord.from_dict(data)
        except Exception as exc:
            logger.error("Failed to load incident %s: %s", incident_id, exc)
            return None

    def update(self, incident: IncidentRecord) -> IncidentRecord:
        """Persist an updated incident record."""
        with self._lock:
            path = self._incident_path(incident.incident_id)
            with open(path, "w") as f:
                json.dump(incident.to_dict(), f, indent=2)

            # Update index
            if incident.incident_id in self._index:
                self._index[incident.incident_id].update({
                    "severity": incident.severity,
                    "status": incident.status,
                })
            self._save_index()

        return incident

    def list_incidents(
        self,
        status: Optional[str] = None,
        category: Optional[str] = None,
        severity: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict]:
        """List incidents from the index with optional filters."""
        results = []
        for iid, meta in self._index.items():
            if status and meta.get("status") != status:
                continue
            if category and meta.get("category") != category:
                continue
            if severity and meta.get("severity") != severity:
                continue
            results.append({"incident_id": iid, **meta})

        # Sort by opened_at descending (most recent first)
        results.sort(key=lambda x: x.get("opened_at", ""), reverse=True)
        return results[offset:offset + limit]

    def find_by_dedup_key(
        self,
        dedup_key: str,
        window_seconds: int = 300,
    ) -> Optional[str]:
        """Find an open/investigating incident with the same dedup key within window.

        Returns the incident_id if found, None otherwise.
        """
        now = datetime.now(timezone.utc)
        for iid, meta in self._index.items():
            if meta.get("dedup_key") != dedup_key:
                continue
            if meta.get("status") in (
                IncidentStatus.CLOSED.value,
                IncidentStatus.FALSE_POSITIVE.value,
            ):
                continue
            # Check time window
            opened_at = meta.get("opened_at", "")
            if opened_at:
                try:
                    opened = datetime.fromisoformat(opened_at)
                    elapsed = (now - opened).total_seconds()
                    if elapsed <= window_seconds:
                        return iid
                except (ValueError, TypeError):
                    pass
        return None

    def find_by_trigger_receipt(self, trigger_receipt_id: str) -> Optional[str]:
        """Find an incident opened by a specific trigger receipt."""
        for iid in self._index:
            incident = self.get(iid)
            if incident and incident.trigger_receipt_id == trigger_receipt_id:
                return iid
        return None

    def count_open(self) -> Dict[str, int]:
        """Count open incidents by severity."""
        counts: Dict[str, int] = {s.value: 0 for s in IncidentSeverity}
        for meta in self._index.values():
            if meta.get("status") not in (
                IncidentStatus.CLOSED.value,
                IncidentStatus.FALSE_POSITIVE.value,
            ):
                sev = meta.get("severity", "MEDIUM")
                counts[sev] = counts.get(sev, 0) + 1
        return counts

    def close(self) -> None:
        """Flush index and release resources."""
        with self._lock:
            self._save_index()


def get_incident_store(data_dir: Optional[str] = None) -> Optional[IncidentStore]:
    """Singleton factory for IncidentStore."""
    global _store_instance
    if _store_instance is not None:
        return _store_instance

    if data_dir is None:
        return None

    with _store_lock:
        if _store_instance is None:
            _store_instance = IncidentStore(data_dir)
    return _store_instance
