# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Graph Persistence — save, load, and version topology documents.

Provides JSON-file-backed persistence for the Graph Builder topology
editor, including version history and deployment snapshots.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.federation.graph_models import TopologyDocument

logger = logging.getLogger(__name__)


class TopologyStore:
    """Persistent storage for federation topology documents.

    Stores the active topology and version history as JSON files.
    Thread-safe for concurrent access from API and background tasks.

    Directory layout:
        data_dir/
            active_topology.json     — current working topology
            topology_versions/
                v1.json              — version snapshot
                v2.json
                ...
            deployed_topology.json   — last deployed snapshot
    """

    def __init__(self, data_dir: str):
        self._data_dir = data_dir
        self._versions_dir = os.path.join(data_dir, "topology_versions")
        self._active_path = os.path.join(data_dir, "active_topology.json")
        self._deployed_path = os.path.join(data_dir, "deployed_topology.json")
        self._lock = threading.Lock()

        os.makedirs(self._versions_dir, exist_ok=True)

    def save(self, topology: TopologyDocument) -> TopologyDocument:
        """Save topology as the active working document.

        Bumps version, recomputes hash, and updates timestamp.
        Returns the updated topology.
        """
        with self._lock:
            topology.updated_at = datetime.now(timezone.utc).isoformat()
            topology.version_hash = topology.compute_version_hash()

            data = topology.model_dump(mode="json")
            with open(self._active_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)

            logger.info(
                "Saved active topology v%d (hash=%s, nodes=%d, edges=%d)",
                topology.version, topology.version_hash,
                len(topology.nodes), len(topology.edges),
            )
            return topology

    def load(self) -> Optional[TopologyDocument]:
        """Load the active working topology. Returns None if not found."""
        with self._lock:
            return self._load_file(self._active_path)

    def save_version(self, topology: TopologyDocument) -> int:
        """Save a versioned snapshot. Returns the version number."""
        with self._lock:
            topology.version_hash = topology.compute_version_hash()
            version = topology.version

            path = os.path.join(self._versions_dir, f"v{version}.json")
            data = topology.model_dump(mode="json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)

            logger.info("Saved topology version v%d", version)
            return version

    def load_version(self, version: int) -> Optional[TopologyDocument]:
        """Load a specific version snapshot."""
        with self._lock:
            path = os.path.join(self._versions_dir, f"v{version}.json")
            return self._load_file(path)

    def list_versions(self) -> List[Dict[str, Any]]:
        """List all saved versions with metadata."""
        with self._lock:
            versions = []
            if not os.path.exists(self._versions_dir):
                return versions

            for fname in sorted(os.listdir(self._versions_dir)):
                if not fname.startswith("v") or not fname.endswith(".json"):
                    continue
                path = os.path.join(self._versions_dir, fname)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    versions.append({
                        "version": data.get("version", 0),
                        "version_hash": data.get("version_hash", ""),
                        "topology_name": data.get("topology_name", ""),
                        "node_count": len(data.get("nodes", [])),
                        "edge_count": len(data.get("edges", [])),
                        "created_at": data.get("created_at", ""),
                        "updated_at": data.get("updated_at", ""),
                        "deployed_at": data.get("deployed_at"),
                    })
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning("Failed to read version file %s: %s", fname, e)

            return versions

    def save_deployed(self, topology: TopologyDocument) -> TopologyDocument:
        """Save topology as the deployed snapshot.

        Marks deployment timestamp and saves both as deployed and as a version.
        """
        with self._lock:
            topology.deployed_at = datetime.now(timezone.utc).isoformat()
            topology.version_hash = topology.compute_version_hash()

            data = topology.model_dump(mode="json")
            with open(self._deployed_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)

            # Also save as versioned snapshot
            version_path = os.path.join(
                self._versions_dir, f"v{topology.version}.json"
            )
            with open(version_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)

            logger.info(
                "Saved deployed topology v%d at %s",
                topology.version, topology.deployed_at,
            )
            return topology

    def load_deployed(self) -> Optional[TopologyDocument]:
        """Load the last deployed topology."""
        with self._lock:
            return self._load_file(self._deployed_path)

    def _load_file(self, path: str) -> Optional[TopologyDocument]:
        """Load a topology from a JSON file. Caller must hold lock."""
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return TopologyDocument(**data)
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.error("Failed to load topology from %s: %s", path, e)
            return None

    def delete_active(self) -> bool:
        """Delete the active topology. Returns True if deleted."""
        with self._lock:
            if os.path.exists(self._active_path):
                os.remove(self._active_path)
                return True
            return False
