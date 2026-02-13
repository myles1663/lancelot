"""
BAL Configuration — Pydantic model loaded from environment variables.

All BAL subsystem settings are centralized here.  BAL_ENABLED mirrors
the FEATURE_BAL feature flag.  Sub-flags control individual pipelines.
"""

from __future__ import annotations

import os
import logging
from pydantic import BaseModel

logger = logging.getLogger(__name__)


def _env_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key, "").strip().lower()
    if not val:
        return default
    return val in ("true", "1", "yes")


def _env_str(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _env_int(key: str, default: int = 0) -> int:
    val = os.environ.get(key, "").strip()
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        return default


class BALConfig(BaseModel):
    """Central BAL configuration loaded from environment variables."""

    # Master switch (mirrors FEATURE_BAL)
    bal_enabled: bool = False

    # Sub-system flags
    bal_intake: bool = False
    bal_repurpose: bool = False
    bal_delivery: bool = False
    bal_billing: bool = False

    # Data storage
    bal_data_dir: str = "/home/lancelot/data/bal"

    # SMTP config (Phase 5 — delivery)
    bal_smtp_host: str = ""
    bal_smtp_port: int = 587
    bal_smtp_user: str = ""
    bal_smtp_password: str = ""

    # Stripe config (Phase 6 — billing)
    bal_stripe_secret_key: str = ""
    bal_stripe_webhook_secret: str = ""

    # Limits
    bal_max_clients: int = 100
    bal_max_content_per_client: int = 50


def load_bal_config() -> BALConfig:
    """Load BAL configuration from environment variables."""
    return BALConfig(
        bal_enabled=_env_bool("FEATURE_BAL", default=False),
        bal_intake=_env_bool("BAL_INTAKE", default=False),
        bal_repurpose=_env_bool("BAL_REPURPOSE", default=False),
        bal_delivery=_env_bool("BAL_DELIVERY", default=False),
        bal_billing=_env_bool("BAL_BILLING", default=False),
        bal_data_dir=_env_str("BAL_DATA_DIR", "/home/lancelot/data/bal"),
        bal_smtp_host=_env_str("BAL_SMTP_HOST"),
        bal_smtp_port=_env_int("BAL_SMTP_PORT", 587),
        bal_smtp_user=_env_str("BAL_SMTP_USER"),
        bal_smtp_password=_env_str("BAL_SMTP_PASSWORD"),
        bal_stripe_secret_key=_env_str("BAL_STRIPE_SECRET_KEY"),
        bal_stripe_webhook_secret=_env_str("BAL_STRIPE_WEBHOOK_SECRET"),
        bal_max_clients=_env_int("BAL_MAX_CLIENTS", 100),
        bal_max_content_per_client=_env_int("BAL_MAX_CONTENT_PER_CLIENT", 50),
    )
