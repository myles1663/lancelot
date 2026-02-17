# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under AGPL-3.0. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Feature Flags — vNext2/vNext3/vNext4 subsystem kill switches.

Each flag controls whether a subsystem is active. When disabled,
the system boots without that subsystem.

vNext2 Environment variables:
    FEATURE_SOUL           — default: true
    FEATURE_SKILLS         — default: true
    FEATURE_HEALTH_MONITOR — default: true
    FEATURE_SCHEDULER      — default: true
    FEATURE_MEMORY_VNEXT   — default: false (vNext3 Memory subsystem)

Tool Fabric Environment variables:
    FEATURE_TOOLS_FABRIC         — default: true (global enable)
    FEATURE_TOOLS_CLI_PROVIDERS  — default: false (optional CLI adapters)
    FEATURE_TOOLS_ANTIGRAVITY    — default: false (Antigravity providers)
    FEATURE_TOOLS_NETWORK        — default: false (network access in sandbox)
    FEATURE_TOOLS_HOST_EXECUTION — default: false (container Linux access)
    FEATURE_TOOLS_HOST_BRIDGE    — default: false (DANGEROUS: real host OS bridge)

vNext4 Governance Environment variables:
    FEATURE_RISK_TIERED_GOVERNANCE — default: false (master switch)
    FEATURE_POLICY_CACHE           — default: false (boot-time policy compilation)
    FEATURE_ASYNC_VERIFICATION     — default: false (async verify for T1 actions)
    FEATURE_INTENT_TEMPLATES       — default: false (cached plan templates)
    FEATURE_BATCH_RECEIPTS         — default: false (batched receipt emission)

Capability Upgrade Environment variables:
    FEATURE_CONNECTORS               — default: false (external connector system)
    FEATURE_TRUST_LEDGER             — default: false (progressive tier relaxation)
    FEATURE_SKILL_SECURITY_PIPELINE  — default: false (6-stage skill security)

Approval Pattern Learning Environment variables:
    FEATURE_APPROVAL_LEARNING        — default: false (APL: learn owner decision patterns)
"""

from __future__ import annotations

import json
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Persistent flag state file — survives container restarts via Docker volume.
# Uses .flag_state.json (dotfile) to avoid being picked up by the librarian.
_FLAG_STATE_PATH = Path(os.environ.get(
    "LANCELOT_FLAG_STATE_PATH",
    "/home/lancelot/data/.flag_state.json",
))
_persisted_state: dict[str, bool] = {}


def _load_persisted_state() -> dict[str, bool]:
    """Load previously persisted flag state from disk."""
    global _persisted_state
    try:
        if _FLAG_STATE_PATH.exists():
            with open(_FLAG_STATE_PATH, "r") as f:
                _persisted_state = json.load(f)
                logger.info("Loaded %d persisted flag states from %s", len(_persisted_state), _FLAG_STATE_PATH)
    except Exception as e:
        logger.warning("Failed to load persisted flag state: %s", e)
        _persisted_state = {}
    return _persisted_state


def _save_persisted_state() -> None:
    """Save current flag overrides to disk for persistence across restarts."""
    try:
        _FLAG_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_FLAG_STATE_PATH, "w") as f:
            json.dump(_persisted_state, f, indent=2)
    except Exception as e:
        logger.warning("Failed to save persisted flag state: %s", e)


# Load persisted state on module import (before flags are initialized)
_load_persisted_state()


def _env_bool(key: str, default: bool = True) -> bool:
    """Read a boolean from env, with persisted state taking priority.

    Priority order:
    1. Persisted state file (flag_state.json) — written by War Room toggles
    2. Environment variable (.env / docker-compose)
    3. Hardcoded default
    """
    # Check persisted state first (War Room toggles survive restart)
    if key in _persisted_state:
        return _persisted_state[key]

    val = os.environ.get(key, "").strip().lower()
    if not val:
        return default
    return val in ("true", "1", "yes")


FEATURE_SOUL: bool = _env_bool("FEATURE_SOUL")
FEATURE_SKILLS: bool = _env_bool("FEATURE_SKILLS")
FEATURE_HEALTH_MONITOR: bool = _env_bool("FEATURE_HEALTH_MONITOR")
FEATURE_SCHEDULER: bool = _env_bool("FEATURE_SCHEDULER")
FEATURE_MEMORY_VNEXT: bool = _env_bool("FEATURE_MEMORY_VNEXT", default=False)

# Tool Fabric flags
FEATURE_TOOLS_FABRIC: bool = _env_bool("FEATURE_TOOLS_FABRIC")
FEATURE_TOOLS_CLI_PROVIDERS: bool = _env_bool("FEATURE_TOOLS_CLI_PROVIDERS", default=False)
FEATURE_TOOLS_ANTIGRAVITY: bool = _env_bool("FEATURE_TOOLS_ANTIGRAVITY", default=False)
FEATURE_TOOLS_NETWORK: bool = _env_bool("FEATURE_TOOLS_NETWORK", default=False)
FEATURE_TOOLS_HOST_EXECUTION: bool = _env_bool("FEATURE_TOOLS_HOST_EXECUTION", default=False)
FEATURE_TOOLS_HOST_BRIDGE: bool = _env_bool("FEATURE_TOOLS_HOST_BRIDGE", default=False)
FEATURE_HOST_WRITE_COMMANDS: bool = _env_bool("FEATURE_HOST_WRITE_COMMANDS", default=False)

# Fix Pack V1 flags
FEATURE_RESPONSE_ASSEMBLER: bool = _env_bool("FEATURE_RESPONSE_ASSEMBLER")
FEATURE_EXECUTION_TOKENS: bool = _env_bool("FEATURE_EXECUTION_TOKENS")
FEATURE_TASK_GRAPH_EXECUTION: bool = _env_bool("FEATURE_TASK_GRAPH_EXECUTION")
FEATURE_NETWORK_ALLOWLIST: bool = _env_bool("FEATURE_NETWORK_ALLOWLIST")
FEATURE_VOICE_NOTES: bool = _env_bool("FEATURE_VOICE_NOTES")

# Fix Pack V6 flags
FEATURE_AGENTIC_LOOP: bool = _env_bool("FEATURE_AGENTIC_LOOP", default=False)

# Fix Pack V8 flags
FEATURE_LOCAL_AGENTIC: bool = _env_bool("FEATURE_LOCAL_AGENTIC", default=False)

# vNext4 Governance flags
FEATURE_RISK_TIERED_GOVERNANCE: bool = _env_bool("FEATURE_RISK_TIERED_GOVERNANCE", default=False)
FEATURE_POLICY_CACHE: bool = _env_bool("FEATURE_POLICY_CACHE", default=False)
FEATURE_ASYNC_VERIFICATION: bool = _env_bool("FEATURE_ASYNC_VERIFICATION", default=False)
FEATURE_INTENT_TEMPLATES: bool = _env_bool("FEATURE_INTENT_TEMPLATES", default=False)
FEATURE_BATCH_RECEIPTS: bool = _env_bool("FEATURE_BATCH_RECEIPTS", default=False)

# Capability Upgrade flags
FEATURE_CONNECTORS: bool = _env_bool("FEATURE_CONNECTORS", default=False)
FEATURE_TRUST_LEDGER: bool = _env_bool("FEATURE_TRUST_LEDGER", default=False)
FEATURE_SKILL_SECURITY_PIPELINE: bool = _env_bool("FEATURE_SKILL_SECURITY_PIPELINE", default=False)

# Approval Pattern Learning flags
FEATURE_APPROVAL_LEARNING: bool = _env_bool("FEATURE_APPROVAL_LEARNING", default=False)

# Business Automation Layer flags
FEATURE_BAL: bool = _env_bool("FEATURE_BAL", default=False)


# All flags are now hot-toggleable via SubsystemManager — no restart required.
RESTART_REQUIRED_FLAGS = frozenset()


def toggle_flag(name: str) -> bool:
    """Toggle a feature flag at runtime. Returns the new value.

    Updates the module global, os.environ, and persists to disk so the
    flag state survives container restarts.
    Raises ValueError if the flag name is not recognized.
    """
    import feature_flags as _self
    if not hasattr(_self, name):
        raise ValueError(f"Unknown flag: {name}")
    current = getattr(_self, name)
    if not isinstance(current, bool):
        raise ValueError(f"{name} is not a boolean flag")
    new_val = not current
    setattr(_self, name, new_val)
    os.environ[name] = "true" if new_val else "false"
    _persisted_state[name] = new_val
    _save_persisted_state()
    logger.info("Flag toggled: %s = %s (persisted)", name, new_val)
    return new_val


def set_flag(name: str, value: bool) -> None:
    """Set a feature flag to a specific value at runtime. Persists to disk."""
    import feature_flags as _self
    if not hasattr(_self, name):
        raise ValueError(f"Unknown flag: {name}")
    current = getattr(_self, name)
    if not isinstance(current, bool):
        raise ValueError(f"{name} is not a boolean flag")
    setattr(_self, name, value)
    os.environ[name] = "true" if value else "false"
    _persisted_state[name] = value
    _save_persisted_state()
    logger.info("Flag set: %s = %s (persisted)", name, value)


def reload_flags() -> None:
    """Re-read feature flags from environment. Used in tests."""
    global FEATURE_SOUL, FEATURE_SKILLS, FEATURE_HEALTH_MONITOR, FEATURE_SCHEDULER, FEATURE_MEMORY_VNEXT
    global FEATURE_TOOLS_FABRIC, FEATURE_TOOLS_CLI_PROVIDERS, FEATURE_TOOLS_ANTIGRAVITY
    global FEATURE_TOOLS_NETWORK, FEATURE_TOOLS_HOST_EXECUTION, FEATURE_TOOLS_HOST_BRIDGE
    global FEATURE_HOST_WRITE_COMMANDS
    global FEATURE_RESPONSE_ASSEMBLER, FEATURE_EXECUTION_TOKENS
    global FEATURE_TASK_GRAPH_EXECUTION, FEATURE_NETWORK_ALLOWLIST, FEATURE_VOICE_NOTES
    global FEATURE_AGENTIC_LOOP
    global FEATURE_LOCAL_AGENTIC
    global FEATURE_RISK_TIERED_GOVERNANCE, FEATURE_POLICY_CACHE
    global FEATURE_ASYNC_VERIFICATION, FEATURE_INTENT_TEMPLATES, FEATURE_BATCH_RECEIPTS
    global FEATURE_CONNECTORS, FEATURE_TRUST_LEDGER, FEATURE_SKILL_SECURITY_PIPELINE
    global FEATURE_APPROVAL_LEARNING
    global FEATURE_BAL

    # vNext2 flags
    FEATURE_SOUL = _env_bool("FEATURE_SOUL")
    FEATURE_SKILLS = _env_bool("FEATURE_SKILLS")
    FEATURE_HEALTH_MONITOR = _env_bool("FEATURE_HEALTH_MONITOR")
    FEATURE_SCHEDULER = _env_bool("FEATURE_SCHEDULER")
    FEATURE_MEMORY_VNEXT = _env_bool("FEATURE_MEMORY_VNEXT", default=False)

    # Tool Fabric flags
    FEATURE_TOOLS_FABRIC = _env_bool("FEATURE_TOOLS_FABRIC")
    FEATURE_TOOLS_CLI_PROVIDERS = _env_bool("FEATURE_TOOLS_CLI_PROVIDERS", default=False)
    FEATURE_TOOLS_ANTIGRAVITY = _env_bool("FEATURE_TOOLS_ANTIGRAVITY", default=False)
    FEATURE_TOOLS_NETWORK = _env_bool("FEATURE_TOOLS_NETWORK", default=False)
    FEATURE_TOOLS_HOST_EXECUTION = _env_bool("FEATURE_TOOLS_HOST_EXECUTION", default=False)
    FEATURE_TOOLS_HOST_BRIDGE = _env_bool("FEATURE_TOOLS_HOST_BRIDGE", default=False)
    FEATURE_HOST_WRITE_COMMANDS = _env_bool("FEATURE_HOST_WRITE_COMMANDS", default=False)

    # Fix Pack V1 flags
    FEATURE_RESPONSE_ASSEMBLER = _env_bool("FEATURE_RESPONSE_ASSEMBLER")
    FEATURE_EXECUTION_TOKENS = _env_bool("FEATURE_EXECUTION_TOKENS")
    FEATURE_TASK_GRAPH_EXECUTION = _env_bool("FEATURE_TASK_GRAPH_EXECUTION")
    FEATURE_NETWORK_ALLOWLIST = _env_bool("FEATURE_NETWORK_ALLOWLIST")
    FEATURE_VOICE_NOTES = _env_bool("FEATURE_VOICE_NOTES")

    # Fix Pack V6 flags
    FEATURE_AGENTIC_LOOP = _env_bool("FEATURE_AGENTIC_LOOP", default=False)

    # Fix Pack V8 flags
    FEATURE_LOCAL_AGENTIC = _env_bool("FEATURE_LOCAL_AGENTIC", default=False)

    # vNext4 Governance flags
    FEATURE_RISK_TIERED_GOVERNANCE = _env_bool("FEATURE_RISK_TIERED_GOVERNANCE", default=False)
    FEATURE_POLICY_CACHE = _env_bool("FEATURE_POLICY_CACHE", default=False)
    FEATURE_ASYNC_VERIFICATION = _env_bool("FEATURE_ASYNC_VERIFICATION", default=False)
    FEATURE_INTENT_TEMPLATES = _env_bool("FEATURE_INTENT_TEMPLATES", default=False)
    FEATURE_BATCH_RECEIPTS = _env_bool("FEATURE_BATCH_RECEIPTS", default=False)

    # Capability Upgrade flags
    FEATURE_CONNECTORS = _env_bool("FEATURE_CONNECTORS", default=False)
    FEATURE_TRUST_LEDGER = _env_bool("FEATURE_TRUST_LEDGER", default=False)
    FEATURE_SKILL_SECURITY_PIPELINE = _env_bool("FEATURE_SKILL_SECURITY_PIPELINE", default=False)

    # Approval Pattern Learning flags
    FEATURE_APPROVAL_LEARNING = _env_bool("FEATURE_APPROVAL_LEARNING", default=False)

    # Business Automation Layer flags
    FEATURE_BAL = _env_bool("FEATURE_BAL", default=False)


def get_all_flags() -> dict[str, bool]:
    """Return a snapshot of all feature flag values."""
    import feature_flags as _self
    result = {}
    for attr in sorted(dir(_self)):
        if attr.startswith("FEATURE_"):
            val = getattr(_self, attr, None)
            if isinstance(val, bool):
                result[attr] = val
    return result


def log_feature_flags() -> None:
    """Log current feature flag state at startup."""
    logger.info(
        "Feature flags: SOUL=%s, SKILLS=%s, HEALTH_MONITOR=%s, SCHEDULER=%s, MEMORY_VNEXT=%s",
        FEATURE_SOUL, FEATURE_SKILLS, FEATURE_HEALTH_MONITOR, FEATURE_SCHEDULER, FEATURE_MEMORY_VNEXT,
    )
    logger.info(
        "Tool Fabric flags: FABRIC=%s, CLI_PROVIDERS=%s, ANTIGRAVITY=%s, NETWORK=%s, HOST_EXEC=%s, HOST_BRIDGE=%s, HOST_WRITE_CMDS=%s",
        FEATURE_TOOLS_FABRIC, FEATURE_TOOLS_CLI_PROVIDERS, FEATURE_TOOLS_ANTIGRAVITY,
        FEATURE_TOOLS_NETWORK, FEATURE_TOOLS_HOST_EXECUTION, FEATURE_TOOLS_HOST_BRIDGE,
        FEATURE_HOST_WRITE_COMMANDS,
    )
    logger.info(
        "Fix Pack V1 flags: RESPONSE_ASSEMBLER=%s, EXECUTION_TOKENS=%s, TASK_GRAPH=%s, NETWORK_ALLOWLIST=%s, VOICE_NOTES=%s",
        FEATURE_RESPONSE_ASSEMBLER, FEATURE_EXECUTION_TOKENS,
        FEATURE_TASK_GRAPH_EXECUTION, FEATURE_NETWORK_ALLOWLIST,
        FEATURE_VOICE_NOTES,
    )
    logger.info(
        "Fix Pack V6 flags: AGENTIC_LOOP=%s",
        FEATURE_AGENTIC_LOOP,
    )
    logger.info(
        "Fix Pack V8 flags: LOCAL_AGENTIC=%s",
        FEATURE_LOCAL_AGENTIC,
    )
    logger.info(
        "vNext4 Governance flags: RISK_TIERED=%s, POLICY_CACHE=%s, ASYNC_VERIFY=%s, TEMPLATES=%s, BATCH_RECEIPTS=%s",
        FEATURE_RISK_TIERED_GOVERNANCE, FEATURE_POLICY_CACHE,
        FEATURE_ASYNC_VERIFICATION, FEATURE_INTENT_TEMPLATES,
        FEATURE_BATCH_RECEIPTS,
    )
    logger.info(
        "Capability Upgrade flags: CONNECTORS=%s, TRUST_LEDGER=%s, SKILL_SECURITY=%s",
        FEATURE_CONNECTORS, FEATURE_TRUST_LEDGER, FEATURE_SKILL_SECURITY_PIPELINE,
    )
    logger.info(
        "Approval Pattern Learning flags: APPROVAL_LEARNING=%s",
        FEATURE_APPROVAL_LEARNING,
    )
    logger.info(
        "Business Automation Layer flags: BAL=%s",
        FEATURE_BAL,
    )
