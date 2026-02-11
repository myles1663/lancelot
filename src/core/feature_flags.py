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
    FEATURE_TOOLS_HOST_EXECUTION — default: false (DANGEROUS: host execution)

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
"""

from __future__ import annotations

import os
import logging

logger = logging.getLogger(__name__)


def _env_bool(key: str, default: bool = True) -> bool:
    """Read a boolean from env. Accepts 'true', '1', 'yes' (case-insensitive)."""
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


def reload_flags() -> None:
    """Re-read feature flags from environment. Used in tests."""
    global FEATURE_SOUL, FEATURE_SKILLS, FEATURE_HEALTH_MONITOR, FEATURE_SCHEDULER, FEATURE_MEMORY_VNEXT
    global FEATURE_TOOLS_FABRIC, FEATURE_TOOLS_CLI_PROVIDERS, FEATURE_TOOLS_ANTIGRAVITY
    global FEATURE_TOOLS_NETWORK, FEATURE_TOOLS_HOST_EXECUTION
    global FEATURE_RESPONSE_ASSEMBLER, FEATURE_EXECUTION_TOKENS
    global FEATURE_TASK_GRAPH_EXECUTION, FEATURE_NETWORK_ALLOWLIST, FEATURE_VOICE_NOTES
    global FEATURE_AGENTIC_LOOP
    global FEATURE_LOCAL_AGENTIC
    global FEATURE_RISK_TIERED_GOVERNANCE, FEATURE_POLICY_CACHE
    global FEATURE_ASYNC_VERIFICATION, FEATURE_INTENT_TEMPLATES, FEATURE_BATCH_RECEIPTS
    global FEATURE_CONNECTORS, FEATURE_TRUST_LEDGER, FEATURE_SKILL_SECURITY_PIPELINE

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


def log_feature_flags() -> None:
    """Log current feature flag state at startup."""
    logger.info(
        "Feature flags: SOUL=%s, SKILLS=%s, HEALTH_MONITOR=%s, SCHEDULER=%s, MEMORY_VNEXT=%s",
        FEATURE_SOUL, FEATURE_SKILLS, FEATURE_HEALTH_MONITOR, FEATURE_SCHEDULER, FEATURE_MEMORY_VNEXT,
    )
    logger.info(
        "Tool Fabric flags: FABRIC=%s, CLI_PROVIDERS=%s, ANTIGRAVITY=%s, NETWORK=%s, HOST_EXEC=%s",
        FEATURE_TOOLS_FABRIC, FEATURE_TOOLS_CLI_PROVIDERS, FEATURE_TOOLS_ANTIGRAVITY,
        FEATURE_TOOLS_NETWORK, FEATURE_TOOLS_HOST_EXECUTION,
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
