"""
Helpers for gating Google-backed connectors behind FEATURE_GOOGLE_OAUTH.
"""

from __future__ import annotations


def is_google_connector_enabled(connector_id: str, backend: str | None = None) -> bool:
    """Return whether the requested Google-backed connector should be available."""
    from src.core import feature_flags

    if connector_id == "calendar":
        return bool(feature_flags.FEATURE_GOOGLE_OAUTH)
    if connector_id == "email" and (backend or "gmail") == "gmail":
        return bool(feature_flags.FEATURE_GOOGLE_OAUTH)
    return True


def google_connector_disabled_reason(connector_id: str, backend: str | None = None) -> str:
    """Human-readable reason for why the connector is unavailable."""
    if connector_id == "calendar":
        return "Google OAuth is disabled; Calendar connector is unavailable."
    if connector_id == "email" and (backend or "gmail") == "gmail":
        return "Google OAuth is disabled; Gmail backend is unavailable."
    return "Connector is unavailable."
