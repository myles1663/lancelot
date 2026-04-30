from __future__ import annotations

import logging
import time
from typing import Optional

from src.federation.graph_persistence import TopologyStore
from src.federation.identity import FederationIdentity

logger = logging.getLogger(__name__)


def _match_local_budget_ceiling(topology, identity: FederationIdentity) -> Optional[float]:
    for node in topology.nodes:
        if node.is_local:
            return float(node.budget_config.daily_ceiling_usd)

    public_key_hex = identity.public_key_hex()
    for node in topology.nodes:
        if node.federation_identity_public_key == public_key_hex:
            return float(node.budget_config.daily_ceiling_usd)

    for node in topology.nodes:
        if node.fingerprint == identity.fingerprint:
            return float(node.budget_config.daily_ceiling_usd)

    for node in topology.nodes:
        if node.node_id == "LOCAL_INSTANCE":
            return float(node.budget_config.daily_ceiling_usd)

    return None


class RuntimeBudgetResolver:
    """Resolve the local federation daily ceiling from deployed topology first."""

    def __init__(
        self,
        topology_data_dir: str,
        identity: FederationIdentity,
        fallback_daily_ceiling_usd: float,
        refresh_interval_s: float = 5.0,
    ) -> None:
        self._store = TopologyStore(topology_data_dir)
        self._identity = identity
        self._fallback_daily_ceiling_usd = float(fallback_daily_ceiling_usd)
        self._refresh_interval_s = refresh_interval_s
        self._cached_value = self._fallback_daily_ceiling_usd
        self._last_refresh_at = 0.0

    def resolve_daily_ceiling_usd(self) -> float:
        now = time.monotonic()
        if (
            self._last_refresh_at > 0.0
            and now - self._last_refresh_at < self._refresh_interval_s
        ):
            return self._cached_value

        self._last_refresh_at = now
        self._cached_value = self._resolve_uncached()
        return self._cached_value

    def _resolve_uncached(self) -> float:
        for loader_name in ("load_deployed", "load"):
            try:
                topology = getattr(self._store, loader_name)()
            except Exception as exc:
                logger.warning(
                    "Failed to load federation topology while resolving runtime budget ceiling: %s",
                    exc,
                )
                continue

            if topology is None:
                continue

            ceiling = _match_local_budget_ceiling(topology, self._identity)
            if ceiling is not None and ceiling > 0:
                return ceiling

        return self._fallback_daily_ceiling_usd
