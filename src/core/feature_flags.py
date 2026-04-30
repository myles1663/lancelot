# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Feature Flags - subsystem kill switches.

Each flag controls whether a subsystem is active. When disabled,
the system boots without that subsystem.

Core subsystem environment variables:
    FEATURE_SOUL           — default: true
    FEATURE_SKILLS         — default: true
    FEATURE_HEALTH_MONITOR — default: true
    FEATURE_SCHEDULER      — default: true
    FEATURE_MEMORY_VNEXT   — default: false (structured memory subsystem)

Tool Fabric Environment variables:
    FEATURE_TOOLS_FABRIC         — default: true (global enable)
    FEATURE_TOOLS_CLI_PROVIDERS  — default: false (optional CLI adapters)
    FEATURE_TOOLS_ANTIGRAVITY    — default: false (Antigravity providers)
    FEATURE_TOOLS_NETWORK        — default: false (network access in sandbox)
    FEATURE_TOOLS_HOST_EXECUTION — default: false (container Linux access)
    FEATURE_TOOLS_HOST_BRIDGE    — default: false (DANGEROUS: real host OS bridge)

Governance environment variables:
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

Response hygiene environment variables:
    FEATURE_STRUCTURED_OUTPUT        — default: false (JSON schema output mode with receipt verification)
    FEATURE_CLAIM_VERIFICATION       — default: false (cross-reference response claims vs tool receipts)
    FEATURE_UNIFIED_CLASSIFICATION   — default: false (single LLM call intent classification)

Competitive intelligence environment variables:
    FEATURE_GITHUB_SEARCH            — default: true (GitHub API skill for structured repo/commit/issue data)
    FEATURE_COMPETITIVE_SCAN         — default: false (episodic memory storage for competitive scan diffing)

Deep reasoning environment variables:
    FEATURE_DEEP_REASONING_LOOP      — default: false (deep reasoning pass before agentic execution)

Google OAuth environment variables:
    FEATURE_GOOGLE_OAUTH             — default: false (Google OAuth 2.0 for Gmail + Calendar)

Federation Environment variables:
    FEATURE_FEDERATION               — default: false (multi-instance federation layer)
    FEATURE_FEDERATION_DASHBOARD     — default: false (fleet dashboard UI/API)

MCP (Model Context Protocol) Environment variables:
    FEATURE_MCP                      — default: false (master kill switch for all MCP invocations)
"""

from __future__ import annotations

import json
import os
import sys
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Canonicalize the module under both import paths. The codebase still contains
# a mix of `import feature_flags` and `import src.core.feature_flags`; without
# this aliasing, Python can materialize two independent module objects and flag
# toggles will mutate one while request-time gates read the other.
_self_module = sys.modules[__name__]
sys.modules.setdefault("feature_flags", _self_module)
sys.modules.setdefault("src.core.feature_flags", _self_module)

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


def get_persisted_flag_state(flag_name: str, default: bool | None = None) -> bool | None:
    """Return the persisted override for a flag when one exists."""
    return _persisted_state.get(flag_name, default)


def persisted_flag_state_snapshot() -> dict[str, bool]:
    """Return a copy of the persisted runtime overrides."""
    return dict(_persisted_state)


def set_persisted_flag_state(flag_name: str, value: bool | None) -> None:
    """Set or clear a persisted override for a single flag."""
    if value is None:
        _persisted_state.pop(flag_name, None)
    else:
        _persisted_state[flag_name] = value
    _save_persisted_state()


def replace_persisted_flag_state(state: dict[str, bool]) -> None:
    """Replace the persisted override snapshot in memory and on disk."""
    _persisted_state.clear()
    _persisted_state.update(state)
    _save_persisted_state()


def clear_persisted_flag_state(flag_name: str | None = None) -> None:
    """Clear one persisted override or all persisted overrides."""
    if flag_name is None:
        _persisted_state.clear()
    else:
        _persisted_state.pop(flag_name, None)
    _save_persisted_state()


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
FEATURE_TOOLS_UAB: bool = _env_bool("FEATURE_TOOLS_UAB", default=False)

# Execution authority and runtime hygiene flags
FEATURE_RESPONSE_ASSEMBLER: bool = _env_bool("FEATURE_RESPONSE_ASSEMBLER")
FEATURE_EXECUTION_TOKENS: bool = _env_bool("FEATURE_EXECUTION_TOKENS")
FEATURE_TASK_GRAPH_EXECUTION: bool = _env_bool("FEATURE_TASK_GRAPH_EXECUTION")
FEATURE_NETWORK_ALLOWLIST: bool = _env_bool("FEATURE_NETWORK_ALLOWLIST")
FEATURE_VOICE_NOTES: bool = _env_bool("FEATURE_VOICE_NOTES")

# Agentic tool loop flags
FEATURE_AGENTIC_LOOP: bool = _env_bool("FEATURE_AGENTIC_LOOP", default=False)

# Local agentic routing flags
FEATURE_LOCAL_AGENTIC: bool = _env_bool("FEATURE_LOCAL_AGENTIC", default=False)

# Risk-tiered governance flags
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

# Response hygiene flags
FEATURE_STRUCTURED_OUTPUT: bool = _env_bool("FEATURE_STRUCTURED_OUTPUT", default=False)       # JSON schema output + receipt verification — eliminates narration/hallucination at format level
FEATURE_CLAIM_VERIFICATION: bool = _env_bool("FEATURE_CLAIM_VERIFICATION", default=False)     # Cross-reference response text claims against tool receipts — neutralize unverified claims
FEATURE_UNIFIED_CLASSIFICATION: bool = _env_bool("FEATURE_UNIFIED_CLASSIFICATION", default=False)  # Single LLM call replaces 7-function keyword heuristic chain for intent routing

# Competitive intelligence flags
FEATURE_GITHUB_SEARCH: bool = _env_bool("FEATURE_GITHUB_SEARCH", default=True)           # GitHub API skill for repos, commits, issues, releases with source URLs
FEATURE_COMPETITIVE_SCAN: bool = _env_bool("FEATURE_COMPETITIVE_SCAN", default=False)     # Store competitive scans in episodic memory for trending/diffing (requires MEMORY_VNEXT)

# Deep reasoning before action
FEATURE_DEEP_REASONING_LOOP: bool = _env_bool("FEATURE_DEEP_REASONING_LOOP", default=False)  # Reasoning-only LLM pass before agentic loop + task experience memory

# Google OAuth flow for Gmail + Calendar connectors
FEATURE_GOOGLE_OAUTH: bool = _env_bool("FEATURE_GOOGLE_OAUTH", default=False)  # OAuth 2.0 Authorization Code + PKCE for Gmail and Google Calendar

# Tool flow streaming and ActionCards
FEATURE_TOOL_FLOW_STREAMING: bool = _env_bool("FEATURE_TOOL_FLOW_STREAMING", default=False)  # Real-time tool execution progress events via EventBus
FEATURE_ACTION_CARDS: bool = _env_bool("FEATURE_ACTION_CARDS", default=False)  # Channel-agnostic interactive buttons for approvals and actions

# HIVE Agent Mesh — ephemeral sub-agent architecture
FEATURE_HIVE: bool = _env_bool("FEATURE_HIVE", default=False)  # Master switch for HIVE Agent Mesh subsystem
FEATURE_HIVE_UAB: bool = _env_bool("FEATURE_HIVE_UAB", default=False)  # Enable UAB bridge for HIVE sub-agents

# Vault-backed secret management — secrets in encrypted vault instead of os.environ
FEATURE_VAULT_SECRETS: bool = _env_bool("FEATURE_VAULT_SECRETS", default=True)  # Vault-backed secret cache (rollback: false = os.getenv fallback)

# Federation — multi-instance coordination
FEATURE_FEDERATION: bool = _env_bool("FEATURE_FEDERATION", default=False)  # Master switch for Federation subsystem (Governance API, heartbeat, identity)
FEATURE_FEDERATION_DASHBOARD: bool = _env_bool("FEATURE_FEDERATION_DASHBOARD", default=False)  # Operator fleet dashboard above per-instance War Rooms

# MCP (Model Context Protocol) — governed tool proxy
FEATURE_MCP: bool = _env_bool("FEATURE_MCP", default=False)  # Master kill switch for all MCP tool invocations

# Observability — OTel export, webhooks, metrics API
FEATURE_OBSERVABILITY: bool = _env_bool("FEATURE_OBSERVABILITY", default=False)  # Master switch for observability subsystem

# Time-Travel Debugging — governed fork/replay of quest histories
FEATURE_TIME_TRAVEL: bool = _env_bool("FEATURE_TIME_TRAVEL", default=False)  # Master switch for time-travel debugging subsystem

# A2A Protocol — governed agent-to-agent interoperability
FEATURE_A2A: bool = _env_bool("FEATURE_A2A", default=False)  # Master switch for A2A protocol subsystem (inbound + outbound)

# Incident Response Playbooks — structured response protocols for governance events
FEATURE_INCIDENT_RESPONSE: bool = _env_bool("FEATURE_INCIDENT_RESPONSE", default=False)  # Master switch for incident response playbook engine


# Boot-wired subsystems are route-gated when disabled, but enabling them from an
# off-at-boot state still requires startup wiring that only happens during boot.
RESTART_REQUIRED_FLAGS = frozenset({
    "FEATURE_GOOGLE_OAUTH",
    "FEATURE_TOOL_FLOW_STREAMING",
    "FEATURE_ACTION_CARDS",
    "FEATURE_MCP",
    "FEATURE_OBSERVABILITY",
    "FEATURE_TIME_TRAVEL",
    "FEATURE_A2A",
    "FEATURE_INCIDENT_RESPONSE",
})


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
    global FEATURE_TOOLS_UAB
    global FEATURE_RESPONSE_ASSEMBLER, FEATURE_EXECUTION_TOKENS
    global FEATURE_TASK_GRAPH_EXECUTION, FEATURE_NETWORK_ALLOWLIST, FEATURE_VOICE_NOTES
    global FEATURE_AGENTIC_LOOP
    global FEATURE_LOCAL_AGENTIC
    global FEATURE_RISK_TIERED_GOVERNANCE, FEATURE_POLICY_CACHE
    global FEATURE_ASYNC_VERIFICATION, FEATURE_INTENT_TEMPLATES, FEATURE_BATCH_RECEIPTS
    global FEATURE_CONNECTORS, FEATURE_TRUST_LEDGER, FEATURE_SKILL_SECURITY_PIPELINE
    global FEATURE_APPROVAL_LEARNING
    global FEATURE_BAL
    global FEATURE_STRUCTURED_OUTPUT, FEATURE_CLAIM_VERIFICATION, FEATURE_UNIFIED_CLASSIFICATION
    global FEATURE_GITHUB_SEARCH, FEATURE_COMPETITIVE_SCAN
    global FEATURE_DEEP_REASONING_LOOP
    global FEATURE_GOOGLE_OAUTH
    global FEATURE_TOOL_FLOW_STREAMING, FEATURE_ACTION_CARDS
    global FEATURE_HIVE, FEATURE_HIVE_UAB
    global FEATURE_VAULT_SECRETS
    global FEATURE_FEDERATION, FEATURE_FEDERATION_DASHBOARD
    global FEATURE_MCP
    global FEATURE_TIME_TRAVEL
    global FEATURE_A2A
    global FEATURE_INCIDENT_RESPONSE

    # Core subsystem flags
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
    FEATURE_TOOLS_UAB = _env_bool("FEATURE_TOOLS_UAB", default=False)

    # Execution authority and runtime hygiene flags
    FEATURE_RESPONSE_ASSEMBLER = _env_bool("FEATURE_RESPONSE_ASSEMBLER")
    FEATURE_EXECUTION_TOKENS = _env_bool("FEATURE_EXECUTION_TOKENS")
    FEATURE_TASK_GRAPH_EXECUTION = _env_bool("FEATURE_TASK_GRAPH_EXECUTION")
    FEATURE_NETWORK_ALLOWLIST = _env_bool("FEATURE_NETWORK_ALLOWLIST")
    FEATURE_VOICE_NOTES = _env_bool("FEATURE_VOICE_NOTES")

    # Agentic tool loop flags
    FEATURE_AGENTIC_LOOP = _env_bool("FEATURE_AGENTIC_LOOP", default=False)

    # Local agentic routing flags
    FEATURE_LOCAL_AGENTIC = _env_bool("FEATURE_LOCAL_AGENTIC", default=False)

    # Risk-tiered governance flags
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

    # Response hygiene
    FEATURE_STRUCTURED_OUTPUT = _env_bool("FEATURE_STRUCTURED_OUTPUT", default=False)
    FEATURE_CLAIM_VERIFICATION = _env_bool("FEATURE_CLAIM_VERIFICATION", default=False)
    FEATURE_UNIFIED_CLASSIFICATION = _env_bool("FEATURE_UNIFIED_CLASSIFICATION", default=False)

    # Competitive intelligence
    FEATURE_GITHUB_SEARCH = _env_bool("FEATURE_GITHUB_SEARCH", default=True)
    FEATURE_COMPETITIVE_SCAN = _env_bool("FEATURE_COMPETITIVE_SCAN", default=False)

    # Deep reasoning
    FEATURE_DEEP_REASONING_LOOP = _env_bool("FEATURE_DEEP_REASONING_LOOP", default=False)

    # Google OAuth
    FEATURE_GOOGLE_OAUTH = _env_bool("FEATURE_GOOGLE_OAUTH", default=False)

    # Tool flow streaming and ActionCards
    FEATURE_TOOL_FLOW_STREAMING = _env_bool("FEATURE_TOOL_FLOW_STREAMING", default=False)
    FEATURE_ACTION_CARDS = _env_bool("FEATURE_ACTION_CARDS", default=False)

    # HIVE Agent Mesh
    FEATURE_HIVE = _env_bool("FEATURE_HIVE", default=False)
    FEATURE_HIVE_UAB = _env_bool("FEATURE_HIVE_UAB", default=False)

    # Vault-backed secret management
    FEATURE_VAULT_SECRETS = _env_bool("FEATURE_VAULT_SECRETS", default=True)

    # Federation
    FEATURE_FEDERATION = _env_bool("FEATURE_FEDERATION", default=False)
    FEATURE_FEDERATION_DASHBOARD = _env_bool("FEATURE_FEDERATION_DASHBOARD", default=False)

    # MCP
    FEATURE_MCP = _env_bool("FEATURE_MCP", default=False)

    # Time-Travel Debugging
    FEATURE_TIME_TRAVEL = _env_bool("FEATURE_TIME_TRAVEL", default=False)

    # A2A Protocol
    FEATURE_A2A = _env_bool("FEATURE_A2A", default=False)

    # Incident Response Playbooks
    FEATURE_INCIDENT_RESPONSE = _env_bool("FEATURE_INCIDENT_RESPONSE", default=False)


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
        "Tool Fabric flags: FABRIC=%s, CLI_PROVIDERS=%s, ANTIGRAVITY=%s, NETWORK=%s, HOST_EXEC=%s, HOST_BRIDGE=%s, HOST_WRITE_CMDS=%s, UAB=%s",
        FEATURE_TOOLS_FABRIC, FEATURE_TOOLS_CLI_PROVIDERS, FEATURE_TOOLS_ANTIGRAVITY,
        FEATURE_TOOLS_NETWORK, FEATURE_TOOLS_HOST_EXECUTION, FEATURE_TOOLS_HOST_BRIDGE,
        FEATURE_HOST_WRITE_COMMANDS, FEATURE_TOOLS_UAB,
    )
    logger.info(
        "Execution authority flags: RESPONSE_ASSEMBLER=%s, EXECUTION_TOKENS=%s, TASK_GRAPH=%s, NETWORK_ALLOWLIST=%s, VOICE_NOTES=%s",
        FEATURE_RESPONSE_ASSEMBLER, FEATURE_EXECUTION_TOKENS,
        FEATURE_TASK_GRAPH_EXECUTION, FEATURE_NETWORK_ALLOWLIST,
        FEATURE_VOICE_NOTES,
    )
    logger.info(
        "Agentic tool loop flags: AGENTIC_LOOP=%s",
        FEATURE_AGENTIC_LOOP,
    )
    logger.info(
        "Local agentic routing flags: LOCAL_AGENTIC=%s",
        FEATURE_LOCAL_AGENTIC,
    )
    logger.info(
        "Risk-tiered governance flags: RISK_TIERED=%s, POLICY_CACHE=%s, ASYNC_VERIFY=%s, TEMPLATES=%s, BATCH_RECEIPTS=%s",
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
    logger.info(
        "Response hygiene flags: STRUCTURED_OUTPUT=%s, CLAIM_VERIFICATION=%s, UNIFIED_CLASSIFICATION=%s",
        FEATURE_STRUCTURED_OUTPUT, FEATURE_CLAIM_VERIFICATION, FEATURE_UNIFIED_CLASSIFICATION,
    )
    logger.info(
        "Competitive Intelligence flags: GITHUB_SEARCH=%s, COMPETITIVE_SCAN=%s",
        FEATURE_GITHUB_SEARCH, FEATURE_COMPETITIVE_SCAN,
    )
    logger.info(
        "Deep reasoning flags: DEEP_REASONING_LOOP=%s",
        FEATURE_DEEP_REASONING_LOOP,
    )
    logger.info(
        "Google OAuth flags: GOOGLE_OAUTH=%s",
        FEATURE_GOOGLE_OAUTH,
    )
    logger.info(
        "Tool Flow + ActionCards flags: TOOL_FLOW_STREAMING=%s, ACTION_CARDS=%s",
        FEATURE_TOOL_FLOW_STREAMING, FEATURE_ACTION_CARDS,
    )
    logger.info(
        "HIVE Agent Mesh flags: HIVE=%s, HIVE_UAB=%s",
        FEATURE_HIVE, FEATURE_HIVE_UAB,
    )
    logger.info(
        "Vault-backed secrets flags: VAULT_SECRETS=%s",
        FEATURE_VAULT_SECRETS,
    )
    logger.info(
        "Federation flags: FEDERATION=%s, FEDERATION_DASHBOARD=%s",
        FEATURE_FEDERATION, FEATURE_FEDERATION_DASHBOARD,
    )
    logger.info(
        "MCP flags: MCP=%s",
        FEATURE_MCP,
    )
    logger.info(
        "Time-Travel Debugging flags: TIME_TRAVEL=%s",
        FEATURE_TIME_TRAVEL,
    )
    logger.info(
        "A2A Protocol flags: A2A=%s",
        FEATURE_A2A,
    )
