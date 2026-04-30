"""
BAL Gates — subsystem enablement checks.

Provides a single helper function that checks whether the master
FEATURE_BAL flag AND a specific sub-system flag are both enabled.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def bal_gate(subsystem: str) -> bool:
    """Check if BAL master flag + a specific subsystem flag are both enabled.

    Args:
        subsystem: One of "intake", "repurpose", "delivery", "billing".

    Returns:
        True if both FEATURE_BAL and BAL_{subsystem} are enabled.
    """
    try:
        import feature_flags
        if not feature_flags.FEATURE_BAL:
            return False
    except ImportError:
        from src.core.feature_flags import FEATURE_BAL
        if not FEATURE_BAL:
            return False

    from bal.config import load_bal_config
    config = load_bal_config()

    subsystem_map = {
        "intake": config.bal_intake,
        "repurpose": config.bal_repurpose,
        "delivery": config.bal_delivery,
        "billing": config.bal_billing,
    }

    enabled = subsystem_map.get(subsystem, False)
    if not enabled:
        logger.debug("BAL gate closed for subsystem '%s'", subsystem)
    return enabled
