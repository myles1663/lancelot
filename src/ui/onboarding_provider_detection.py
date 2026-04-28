"""Provider detection helpers for onboarding startup state."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def has_codex_cli_auth() -> bool:
    """Return True when mounted Codex CLI auth is available during onboarding."""
    try:
        from src.core.providers.codex_cli_client import has_codex_cli_auth as _has_auth

        return _has_auth()
    except Exception as exc:
        logger.warning("Onboarding failed to inspect Codex CLI auth: %s", exc)
        return False


def load_persisted_provider() -> str:
    """Return the durable active provider when provider persistence is available."""
    candidates = []
    configured_data_dir = os.getenv("LANCELOT_DATA_DIR", "").strip()
    if configured_data_dir:
        candidates.append(Path(configured_data_dir) / "provider_config.json")

    candidates.append(Path("/home/lancelot/data/provider_config.json"))
    candidates.append(Path("lancelot_data/provider_config.json"))

    seen = set()
    for path in candidates:
        normalized = str(path)
        if normalized in seen:
            continue
        seen.add(normalized)
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                provider = (data.get("active_provider") or "").strip()
                if provider:
                    return provider
        except Exception as exc:
            logger.warning("Onboarding failed to read persisted provider from %s: %s", path, exc)

    return ""
