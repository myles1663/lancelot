# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Federation Topology Registry — tracks known peers, their states, and Soul versions.

Maintains the federation graph and derives the deployment mode
(STANDALONE / HIERARCHICAL / FEDERATED) from the topology shape.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.federation.heartbeat import StalenessLevel, compute_staleness

logger = logging.getLogger(__name__)


class DeploymentMode(str, Enum):
    """Deployment mode derived from federation topology."""
    STANDALONE = "standalone"      # No peers — single instance
    HIERARCHICAL = "hierarchical"  # Parent-child tree (root + children)
    FEDERATED = "federated"        # Peer mesh (no single root)


class PeerRole(str, Enum):
    """Role of a peer in the topology."""
    ROOT = "root"        # Top of hierarchy (only one)
    CHILD = "child"      # Child in hierarchy
    PEER = "peer"        # Equal peer in mesh
    SELF = "self"        # This instance


class PeerHealth(str, Enum):
    """Health status of a peer based on heartbeat freshness."""
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    LOST = "lost"
    UNKNOWN = "unknown"


@dataclass
class PeerRecord:
    """A known federation peer."""
    instance_id: str
    fingerprint: str = ""
    public_key_hex: str = ""
    address: str = ""
    role: str = PeerRole.PEER.value
    soul_version_hash: str = ""
    last_heartbeat_at: Optional[str] = None
    registered_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PeerRecord":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class TopologyRegistry:
    """Thread-safe registry of federation peers.

    Maintains known peers, derives deployment mode from topology shape,
    and provides health summaries based on heartbeat freshness.
    """

    def __init__(
        self,
        self_instance_id: str,
        staleness_warning_s: float = 10.0,
        staleness_critical_s: float = 20.0,
        staleness_lost_s: float = 30.0,
        max_peers: int = 50,
        persistence_path: Optional[str] = None,
    ):
        self._self_id = self_instance_id
        self._staleness_warning_s = staleness_warning_s
        self._staleness_critical_s = staleness_critical_s
        self._staleness_lost_s = staleness_lost_s
        self._max_peers = max_peers
        self._persistence_path = persistence_path
        self._peers: Dict[str, PeerRecord] = {}
        self._lock = threading.Lock()
        self._deployment_mode = DeploymentMode.STANDALONE

        # Load persisted topology if available
        if persistence_path:
            self._load_from_disk()

    @property
    def deployment_mode(self) -> DeploymentMode:
        return self._deployment_mode

    def register_peer(
        self,
        instance_id: str,
        fingerprint: str = "",
        public_key_hex: str = "",
        address: str = "",
        role: str = PeerRole.PEER.value,
        soul_version_hash: str = "",
    ) -> PeerRecord:
        """Register or update a federation peer.

        Raises ValueError if max_peers would be exceeded.
        """
        with self._lock:
            if instance_id == self._self_id:
                raise ValueError("Cannot register self as a peer")

            if instance_id not in self._peers and len(self._peers) >= self._max_peers:
                raise ValueError(
                    f"Maximum peer count ({self._max_peers}) reached"
                )

            existing = self._peers.get(instance_id)
            if existing:
                # Update existing peer
                existing.fingerprint = fingerprint or existing.fingerprint
                existing.public_key_hex = public_key_hex or existing.public_key_hex
                existing.address = address or existing.address
                existing.role = role or existing.role
                existing.soul_version_hash = soul_version_hash or existing.soul_version_hash
                peer = existing
            else:
                peer = PeerRecord(
                    instance_id=instance_id,
                    fingerprint=fingerprint,
                    public_key_hex=public_key_hex,
                    address=address,
                    role=role,
                    soul_version_hash=soul_version_hash,
                )
                self._peers[instance_id] = peer

            self._recompute_mode()
            self._persist_to_disk()

            logger.info(
                "Peer registered: %s (role=%s, fingerprint=%s)",
                instance_id, role, fingerprint[:8] if fingerprint else "none",
            )
            return peer

    def remove_peer(self, instance_id: str) -> bool:
        """Remove a peer from the registry. Returns True if found and removed."""
        with self._lock:
            if instance_id in self._peers:
                del self._peers[instance_id]
                self._recompute_mode()
                self._persist_to_disk()
                logger.info("Peer removed: %s", instance_id)
                return True
            return False

    def update_heartbeat(
        self,
        instance_id: str,
        timestamp: Optional[str] = None,
        soul_version_hash: Optional[str] = None,
    ) -> bool:
        """Update a peer's last heartbeat timestamp. Returns False if unknown peer."""
        with self._lock:
            peer = self._peers.get(instance_id)
            if not peer:
                return False
            peer.last_heartbeat_at = timestamp or datetime.now(timezone.utc).isoformat()
            if soul_version_hash:
                peer.soul_version_hash = soul_version_hash
            return True

    def get_peer(self, instance_id: str) -> Optional[PeerRecord]:
        """Get a specific peer record."""
        with self._lock:
            return self._peers.get(instance_id)

    def list_peers(self) -> List[PeerRecord]:
        """Return all known peers."""
        with self._lock:
            return list(self._peers.values())

    def peer_count(self) -> int:
        with self._lock:
            return len(self._peers)

    def get_peer_heartbeats(self) -> Dict[str, Optional[str]]:
        """Return map of peer_id → last_heartbeat_at for divergence detection."""
        with self._lock:
            return {
                pid: p.last_heartbeat_at
                for pid, p in self._peers.items()
            }

    def get_peer_health(self, instance_id: str) -> PeerHealth:
        """Get health classification for a specific peer."""
        with self._lock:
            peer = self._peers.get(instance_id)
            if not peer:
                return PeerHealth.UNKNOWN
            level, _ = compute_staleness(
                peer.last_heartbeat_at,
                warning_s=self._staleness_warning_s,
                critical_s=self._staleness_critical_s,
                lost_s=self._staleness_lost_s,
            )
            return {
                StalenessLevel.FRESH: PeerHealth.HEALTHY,
                StalenessLevel.WARNING: PeerHealth.WARNING,
                StalenessLevel.CRITICAL: PeerHealth.CRITICAL,
                StalenessLevel.LOST: PeerHealth.LOST,
            }.get(level, PeerHealth.UNKNOWN)

    def get_health_summary(self) -> Dict[str, Any]:
        """Get health summary of all peers."""
        with self._lock:
            summary = {
                "total_peers": len(self._peers),
                "healthy": 0,
                "warning": 0,
                "critical": 0,
                "lost": 0,
                "unknown": 0,
            }
            for peer in self._peers.values():
                level, _ = compute_staleness(
                    peer.last_heartbeat_at,
                    warning_s=self._staleness_warning_s,
                    critical_s=self._staleness_critical_s,
                    lost_s=self._staleness_lost_s,
                )
                if level == StalenessLevel.FRESH:
                    summary["healthy"] += 1
                elif level == StalenessLevel.WARNING:
                    summary["warning"] += 1
                elif level == StalenessLevel.CRITICAL:
                    summary["critical"] += 1
                else:
                    summary["lost"] += 1

            summary["deployment_mode"] = self._deployment_mode.value
            return summary

    def _recompute_mode(self) -> None:
        """Derive deployment mode from current topology shape."""
        if not self._peers:
            self._deployment_mode = DeploymentMode.STANDALONE
            return

        roles = [p.role for p in self._peers.values()]
        has_root = PeerRole.ROOT.value in roles
        has_children = PeerRole.CHILD.value in roles

        if has_root or has_children:
            self._deployment_mode = DeploymentMode.HIERARCHICAL
        else:
            self._deployment_mode = DeploymentMode.FEDERATED

    def _persist_to_disk(self) -> None:
        """Save topology to disk for persistence across restarts."""
        if not self._persistence_path:
            return
        try:
            path = Path(self._persistence_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "self_instance_id": self._self_id,
                "peers": {pid: p.to_dict() for pid, p in self._peers.items()},
                "deployment_mode": self._deployment_mode.value,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            logger.warning("Failed to persist topology: %s", exc)

    def _load_from_disk(self) -> None:
        """Load topology from disk."""
        if not self._persistence_path:
            return
        path = Path(self._persistence_path)
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for pid, pdata in data.get("peers", {}).items():
                self._peers[pid] = PeerRecord.from_dict(pdata)
            self._recompute_mode()
            logger.info(
                "Loaded topology from disk: %d peers, mode=%s",
                len(self._peers), self._deployment_mode.value,
            )
        except Exception as exc:
            logger.warning("Failed to load topology from disk: %s", exc)
