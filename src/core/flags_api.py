"""
Flags API — /api/flags

Exposes current feature flag values, descriptions, dependency info,
and allows runtime toggling for the War Room Kill Switches page.
"""

import logging
import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from typing import List, Optional
from src.core.api_auth import require_authenticated_request
from src.core.auth_api import require_operator_capability
from src.core.network_allowlist import NetworkAllowlistService
from src.core.outbound_http import assert_local_control_url

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/flags",
    tags=["flags"],
    dependencies=[Depends(require_authenticated_request)],
)

_audit_logger = None


def _resolve_audit_user(request: Request) -> str:
    """Resolve the authenticated operator display name for text audit logs."""
    try:
        from src.core.auth_api import get_api_key_identity, resolve_operator_identity

        identity = resolve_operator_identity(request)
        if identity is None:
            identity = get_api_key_identity(request)
        if identity.display_name:
            return identity.display_name
        if identity.operator_id:
            return identity.operator_id
    except Exception as exc:
        logger.debug("Falling back to generic flags audit user: %s", exc)
    return "operator"


def init_flags_api(audit_logger=None):
    """Inject audit logger (called from gateway startup)."""
    global _audit_logger
    _audit_logger = audit_logger

# ── Flag Metadata Registry ───────────────────────────────────────────
# Each entry: description, category, dependencies, conflicts, warnings

FLAG_META = {
    # ── Core Subsystems (restart required) ────────────────────────
    "FEATURE_SOUL": {
        "description": "Constitutional identity system. Loads soul.yaml with versioned governance rules, amendment workflows, and invariant checks that constrain all agent behavior.",
        "category": "Core Subsystem",
        "requires": [],
        "conflicts": [],
        "warning": "Disabling removes all constitutional constraints. The agent will operate without governance rules or identity invariants.",
    },
    "FEATURE_SKILLS": {
        "description": "Modular capability system. Manages skill registry, ownership tracking, and the factory pipeline for creating new skills. Required for tool execution and scheduled jobs.",
        "category": "Core Subsystem",
        "requires": [],
        "conflicts": [],
        "warning": "Disabling breaks tool execution and scheduled job dispatch. SCHEDULER depends on this for running jobs.",
    },
    "FEATURE_HEALTH_MONITOR": {
        "description": "Background health monitoring with liveness/readiness probes. Runs periodic checks on all components and exposes /health/live and /health/ready endpoints.",
        "category": "Core Subsystem",
        "requires": [],
        "conflicts": [],
        "warning": "Disabling removes /health/live and /health/ready endpoints. The VitalsBar will show partial data.",
    },
    "FEATURE_SCHEDULER": {
        "description": "Cron and interval-based job scheduling. Reads jobs from scheduler.yaml and executes them via the skill executor on configured schedules.",
        "category": "Core Subsystem",
        "requires": ["FEATURE_SKILLS"],
        "conflicts": [],
        "warning": "Requires SKILLS to be enabled for job execution. Without SKILLS, jobs will be registered but cannot run.",
    },
    "FEATURE_MEMORY_VNEXT": {
        "description": "Structured memory system. Provides 5 core blocks (persona, human, mission, operating_rules, workspace_state), working/episodic/archival storage, context compiler, governed self-edits, and full-text search.",
        "category": "Core Subsystem",
        "requires": [],
        "conflicts": [],
        "warning": "Disabling reverts to basic file-based context loading. The Memory tab in War Room will show 'disabled'. No governed self-edits or tiered storage.",
    },

    # ── Tool Fabric ───────────────────────────────────────────────
    "FEATURE_TOOLS_FABRIC": {
        "description": "Global enable for the provider-agnostic tool execution layer. Controls the ToolFabric orchestrator, policy engine, and all tool providers.",
        "category": "Tool Fabric",
        "requires": [],
        "conflicts": [],
        "warning": "Disabling shuts down all tool providers (local_sandbox, ui_templates). No sandboxed code execution or file operations via Tool Fabric.",
    },
    "FEATURE_TOOLS_CLI_PROVIDERS": {
        "description": "Optional CLI adapter providers for Tool Fabric. Adds shell-based tool providers that wrap command-line tools as capabilities.",
        "category": "Tool Fabric",
        "requires": ["FEATURE_TOOLS_FABRIC"],
        "conflicts": [],
        "warning": "Requires TOOLS_FABRIC. Adds additional attack surface through CLI adapters.",
    },
    "FEATURE_TOOLS_ANTIGRAVITY": {
        "description": "Antigravity UI providers - generative UI scaffolding, vision-based UI control, and AI browser automation. Provider-agnostic (works with Gemini, OpenAI, or Anthropic).",
        "category": "Tool Fabric",
        "requires": ["FEATURE_TOOLS_FABRIC"],
        "conflicts": [],
        "warning": "Requires TOOLS_FABRIC. Needs a running browser instance (Playwright). Increases resource usage.",
    },
    "FEATURE_TOOLS_NETWORK": {
        "description": "Allows network access from within the Docker sandbox during tool execution. By default, sandboxed code runs with no network. Works with NETWORK_ALLOWLIST to restrict which domains are reachable.",
        "category": "Tool Fabric",
        "requires": ["FEATURE_TOOLS_FABRIC"],
        "conflicts": [],
        "warning": "Security risk: sandboxed code can make outbound network requests. Enable NETWORK_ALLOWLIST and configure allowed domains to restrict access.",
    },
    "FEATURE_TOOLS_HOST_EXECUTION": {
        "description": "Docker Linux Access. Runs commands inside the Lancelot container's Linux environment (Debian) instead of in sandboxed sibling containers. No container isolation from Lancelot's own filesystem.",
        "category": "Tool Fabric",
        "requires": ["FEATURE_TOOLS_FABRIC"],
        "conflicts": [],
        "warning": "Commands run in the container's Linux environment with full access to Lancelot's filesystem. No sandbox isolation. For actual host OS access, use HOST_BRIDGE instead.",
    },
    "FEATURE_TOOLS_HOST_BRIDGE": {
        "description": "Host OS Bridge. Executes commands on the actual host operating system (e.g., Windows, macOS) via the Lancelot Host Agent. Requires the host agent running on the host machine (host_agent/start_agent.bat).",
        "category": "Tool Fabric",
        "requires": ["FEATURE_TOOLS_FABRIC"],
        "conflicts": [],
        "warning": "CRITICAL SECURITY RISK. Full access to the host machine. Requires host_agent running on the host. Only for trusted development environments.",
        "confirm_enable": "You are about to grant Lancelot direct access to your host operating system. This bypasses all container isolation — Lancelot will be able to run any command on your actual machine. Only enable this in trusted development environments.\n\nThe Lancelot Host Agent must be running on the host (host_agent/start_agent.bat).\n\nDo you accept this risk?",
        "has_editor": "host_agent",
    },
    "FEATURE_HOST_WRITE_COMMANDS": {
        "description": "Host Write Commands. Unlocks DESTRUCTIVE commands (rm, del, kill, shutdown, etc.) on the host OS via the Host Bridge. All write commands still require Sentry approval before execution.",
        "category": "Tool Fabric",
        "requires": ["FEATURE_TOOLS_HOST_BRIDGE"],
        "conflicts": [],
        "warning": "EXTREME DANGER. Enables file deletion, process killing, and system commands on your REAL HOST MACHINE. Mistakes are IRREVERSIBLE.",
        "confirm_enable": "\u26a0\ufe0f EXTREME DANGER \u26a0\ufe0f\n\nThis enables DESTRUCTIVE commands (rm, del, kill, shutdown, etc.) on your REAL HOST MACHINE.\n\nFiles deleted CANNOT be recovered. Services stopped may not restart. Registry edits can break your system.\n\nAll write commands still require your approval in the Sentry, but mistakes are IRREVERSIBLE.\n\nOnly enable this if you fully understand the risks.",
        "has_editor": "host_write_commands",
        "hidden": True,
    },
    "FEATURE_TOOLS_UAB": {
        "description": "Universal App Bridge. Framework-level desktop app control — hooks into UI toolkits (Electron, Qt, GTK, WPF, Flutter, Java) to give Lancelot structured control of desktop applications via the UAB daemon on the host machine.",
        "category": "Tool Fabric",
        "requires": ["FEATURE_TOOLS_FABRIC", "FEATURE_TOOLS_HOST_BRIDGE"],
        "conflicts": [],
        "warning": "UAB connects to a local daemon (port 7900) on the host machine via the Host Bridge. The daemon can introspect and control running desktop applications — reading UI state, clicking buttons, typing text, and invoking actions. All actions are receipt-traced.",
        "confirm_enable": "You are about to enable the Universal App Bridge (UAB).\n\nUAB gives Lancelot the ability to:\n  - Detect and connect to running desktop applications\n  - Read the full UI element tree of connected apps\n  - Perform actions (click, type, select) on UI elements\n  - Monitor application state changes in real-time\n\nThis operates on your actual desktop applications via the Host Bridge.\n\nThe UAB daemon must be running on the host.\nRun scripts\\install-uab.bat to install it as an auto-start service.\nAll actions produce auditable AppControl receipts.\n\nDo you accept this risk?",
        "has_editor": "uab_panel",
    },

    # ── Execution & Runtime ───────────────────────────────────────
    "FEATURE_RESPONSE_ASSEMBLER": {
        "description": "Response assembly pipeline. Always active; this flag is informational only. The assembler processes raw LLM output through formatting, citation injection, and artifact extraction.",
        "category": "Runtime",
        "requires": [],
        "conflicts": [],
        "warning": "This flag is informational only. The response assembler is always active for output hygiene. Toggling has no effect.",
    },
    "FEATURE_EXECUTION_TOKENS": {
        "description": "Execution token system. Generates time-limited, permission-scoped tokens for tool execution. Provides fine-grained authorization control.",
        "category": "Runtime",
        "requires": [],
        "conflicts": [],
        "warning": "Disabling removes token-based authorization for tool calls. Tools will execute with ambient permissions only.",
    },
    "FEATURE_TASK_GRAPH_EXECUTION": {
        "description": "Task graph execution engine. Enables multi-step task planning with dependency tracking, parallel execution, and progress monitoring.",
        "category": "Runtime",
        "requires": [],
        "conflicts": [],
        "warning": "Disabling falls back to sequential single-step execution. Complex multi-step tasks will not be decomposed.",
    },
    "FEATURE_NETWORK_ALLOWLIST": {
        "description": "Network allowlist enforcement. Restricts outbound HTTP requests to a configured list of allowed domains. Best used alongside TOOLS_NETWORK — enables network access while limiting reachable domains. Edit the allowlist below when enabled.",
        "category": "Runtime",
        "requires": [],
        "conflicts": [],
        "warning": "When enabled, only allowlisted domains can be reached. Tokens default to domains from config/network_allowlist.yaml. Keep the list minimal.",
        "has_editor": "network_allowlist",
    },
    "FEATURE_VOICE_NOTES": {
        "description": "Voice note processing. Enables audio file uploads to be transcribed and processed as text input using the local model.",
        "category": "Runtime",
        "requires": [],
        "conflicts": [],
        "warning": "Requires a working local model for transcription. No conflicts.",
    },
    "FEATURE_AGENTIC_LOOP": {
        "description": "Multi-step autonomous execution loop. Allows the agent to chain multiple tool calls and reasoning steps without waiting for user input between each step.",
        "category": "Runtime",
        "requires": ["FEATURE_SKILLS"],
        "conflicts": [],
        "warning": "Increases autonomy — the agent can take multiple actions in sequence. Monitor via receipts. Requires SKILLS for tool execution.",
    },
    "FEATURE_LOCAL_AGENTIC": {
        "description": "Use the local LLM (llama.cpp) for agentic reasoning steps instead of the flagship model. Reduces API costs but may lower quality for complex reasoning.",
        "category": "Runtime",
        "requires": ["FEATURE_AGENTIC_LOOP"],
        "conflicts": [],
        "warning": "Requires AGENTIC_LOOP. Local model quality is lower — only suitable for simple agentic tasks. Complex plans should use flagship.",
    },

    # ── Governance ────────────────────────────────────────────────
    "FEATURE_RISK_TIERED_GOVERNANCE": {
        "description": "Master switch for risk-tiered governance. Enables 4-tier risk classification (T0-T3) with escalating approval requirements per tier.",
        "category": "Governance",
        "requires": ["FEATURE_SOUL"],
        "conflicts": [],
        "warning": "Requires SOUL for governance rules. Enabling adds overhead to every action (risk classification step). All other governance flags depend on this.",
    },
    "FEATURE_POLICY_CACHE": {
        "description": "Boot-time policy compilation. Pre-compiles governance policies into a cache at startup for faster runtime evaluation.",
        "category": "Governance",
        "requires": ["FEATURE_RISK_TIERED_GOVERNANCE"],
        "conflicts": [],
        "warning": "Requires RISK_TIERED_GOVERNANCE. Increases startup time but improves runtime policy evaluation speed.",
    },
    "FEATURE_ASYNC_VERIFICATION": {
        "description": "Asynchronous verification for Tier 1 actions. Allows low-risk actions to proceed immediately while verification runs in the background.",
        "category": "Governance",
        "requires": ["FEATURE_RISK_TIERED_GOVERNANCE"],
        "conflicts": [],
        "warning": "Requires RISK_TIERED_GOVERNANCE. T1 actions execute before verification completes — rollback may be needed if verification fails.",
    },
    "FEATURE_INTENT_TEMPLATES": {
        "description": "Cached plan templates. Stores and reuses verified execution plans for common intents, reducing re-planning overhead.",
        "category": "Governance",
        "requires": ["FEATURE_RISK_TIERED_GOVERNANCE"],
        "conflicts": [],
        "warning": "Requires RISK_TIERED_GOVERNANCE. Templates may become stale if governance rules change — clear cache after soul amendments.",
    },
    "FEATURE_BATCH_RECEIPTS": {
        "description": "Batched receipt emission. Buffers action receipts and writes them in batches instead of one-at-a-time, reducing I/O overhead.",
        "category": "Governance",
        "requires": [],
        "conflicts": [],
        "warning": "Receipts may be delayed or lost if the process crashes before a batch flush. Trade-off: performance vs auditability.",
    },

    # ── Capability Upgrades ───────────────────────────────────────
    "FEATURE_CONNECTORS": {
        "description": "External connector system. Enables integration with third-party services (APIs, databases, SaaS platforms) through a standardized connector interface.",
        "category": "Capabilities",
        "requires": ["FEATURE_TOOLS_FABRIC"],
        "conflicts": [],
        "warning": "Requires TOOLS_FABRIC. Each connector adds external dependencies and potential failure points. Audit connectors before enabling.",
    },
    "FEATURE_TRUST_LEDGER": {
        "description": "Progressive trust relaxation. Tracks per-capability trust scores that increase with successful execution, allowing tier requirements to relax over time.",
        "category": "Capabilities",
        "requires": ["FEATURE_RISK_TIERED_GOVERNANCE"],
        "conflicts": [],
        "warning": "Requires RISK_TIERED_GOVERNANCE. Trust scores accumulate — a capability that earns trust may eventually bypass approval. Review graduation proposals.",
    },
    "FEATURE_SKILL_SECURITY_PIPELINE": {
        "description": "6-stage skill security pipeline. Adds code scanning, manifest validation, sandbox testing, ownership verification, approval, and audit for new skills.",
        "category": "Capabilities",
        "requires": ["FEATURE_SKILLS"],
        "conflicts": [],
        "warning": "Requires SKILLS. Adds latency to skill registration (each stage runs sequentially). Recommended for production.",
    },

    # ── Approval Pattern Learning ─────────────────────────────────
    "FEATURE_APPROVAL_LEARNING": {
        "description": "Approval Pattern Learning (APL). Learns from owner approval/denial decisions to auto-approve routine actions matching established patterns. Reduces approval fatigue.",
        "category": "Intelligence",
        "requires": ["FEATURE_RISK_TIERED_GOVERNANCE"],
        "conflicts": [],
        "warning": "Requires RISK_TIERED_GOVERNANCE. The system will auto-approve actions matching learned patterns. Review APL rules regularly — incorrect patterns can bypass intended oversight.",
    },

    # ── Response Hygiene ──────────────────────────────────────────
    "FEATURE_STRUCTURED_OUTPUT": {
        "description": "Structured Output Mode. After the agentic loop completes, a reformat step converts the raw response into a verified JSON structure with explicit fields: response_to_user, actions_taken, next_action. A presentation layer cross-references claimed actions against actual tool receipts and silently drops anything that didn't happen. Converts the verified JSON back to readable chat text. Eliminates narration, fake progress claims, and verbose failure descriptions at the format level.",
        "category": "Intelligence",
        "requires": ["FEATURE_AGENTIC_LOOP"],
        "conflicts": [],
        "warning": "Requires AGENTIC_LOOP. Adds one additional LLM call per agentic response for reformatting. If reformatting fails, falls back to raw text. Monitor structured reformat logs.",
    },
    "FEATURE_CLAIM_VERIFICATION": {
        "description": "Response Claim Verification. Scans the response for action claims ('I sent the email', 'I searched for X') and cross-references each claim against actual tool receipts from the current turn. Claims without matching receipts are neutralized before the user sees them. Works with or without STRUCTURED_OUTPUT — when structured output is enabled, verifies the response_to_user field; when disabled, verifies raw text directly.",
        "category": "Intelligence",
        "requires": [],
        "conflicts": [],
        "warning": "Adds a post-processing step to every response. May occasionally flag legitimate statements as unverified if the wording closely matches action verbs. Monitor flagged claims in logs.",
    },
    "FEATURE_UNIFIED_CLASSIFICATION": {
        "description": "Unified Intent Classification. Replaces the 7-function keyword heuristic chain (classify_intent, _verify_intent_with_llm, _is_continuation, _needs_research, _is_low_risk_exec, _is_conversational, _is_simple_for_local) with a single LLM call using structured output. Returns intent, confidence, is_continuation, and requires_tools in one JSON response. Falls back to the keyword classifier on any failure. Cost: ~$0.0002 per message.",
        "category": "Intelligence",
        "requires": [],
        "conflicts": [],
        "warning": "Adds one fast-model API call per incoming message for classification (~100 input tokens). Falls back to keyword classifier if the API call fails. Monitor unified classifier logs for accuracy.",
    },
    "FEATURE_PROCEDURAL_RECOMMENDATIONS": {
        "description": "Procedural Recommendations. Adds restrained, auditable operating-pattern suggestions to chat and War Room when the current work looks repeatable, release-sensitive, or better handled by a formal workflow.",
        "category": "Intelligence",
        "requires": [],
        "conflicts": [],
        "warning": "Operator-facing suggestions can feel noisy if tuned poorly. Disable this switch immediately if recommendations interrupt more than they assist.",
    },

    # ── Competitive Intelligence ──────────────────────────────────
    "FEATURE_GITHUB_SEARCH": {
        "description": "GitHub Search Skill. Adds a dedicated tool for querying GitHub's REST API — search repositories, get recent commits, issues/PRs, and releases. Returns structured data with source URLs for every result. Grounds competitive intelligence in verifiable artifacts instead of web search summaries.",
        "category": "Intelligence",
        "requires": ["FEATURE_AGENTIC_LOOP"],
        "conflicts": [],
        "warning": "Uses GitHub's public API (60 requests/hour unauthenticated). Set GITHUB_TOKEN env var for 5000 req/hour. Read-only — all actions are auto-approved.",
    },
    "FEATURE_COMPETITIVE_SCAN": {
        "description": "Competitive Scan Memory. Stores competitive intelligence results in episodic memory after each scan. On subsequent scans of the same target, retrieves previous scans and generates a diff showing new findings, removed findings, and trends over time.",
        "category": "Intelligence",
        "requires": ["FEATURE_MEMORY_VNEXT"],
        "conflicts": [],
        "warning": "Requires structured memory to be enabled for episodic storage. Scans are stored with 30-day decay. Each scan adds ~200-500 tokens to episodic memory.",
    },

    # ── Deep Reasoning Loop ───────────────────────────────────────
    "FEATURE_DEEP_REASONING_LOOP": {
        "description": "Deep reasoning pass before agentic execution. Runs a reasoning-only LLM call (no tools, deep model, high thinking) before the agentic loop. The model analyzes the task, identifies information needs, proposes approaches, and flags capability gaps. Reasoning output is injected as context for the agentic loop and provides structured governance feedback when actions are blocked. Task experience recording is handled by the core chat flow and is not gated by this flag.",
        "category": "Reasoning",
        "requires": [],
        "conflicts": [],
        "warning": "Adds one extra LLM call (deep model with extended thinking) per qualifying request. Increases latency by 3-10 seconds and token cost by ~2000-8000 tokens per request. Disable if response time is critical.",
    },

    # ── HIVE Agent Mesh ──────────────────────────────────────────────
    "FEATURE_HIVE": {
        "description": "HIVE Agent Mesh — ephemeral sub-agent architecture. Decomposes complex tasks into governed sub-agents that execute via UAB under full operator pause/kill/modify control. Each sub-agent gets a Scoped Soul (more restrictive than parent), every action is receipt-traced, and the operator can intervene at any time.",
        "category": "Core Subsystem",
        "requires": [],
        "conflicts": [],
        "warning": "Spawns ephemeral sub-agents that consume LLM tokens for decomposition and execution. Each agent runs in a thread with governance checks between actions. Monitor capacity via War Room HIVE page.",
    },
    "FEATURE_HIVE_UAB": {
        "description": "HIVE UAB Bridge — enables HIVE sub-agents to control desktop applications via the Universal App Bridge. Without this flag, HIVE agents can only perform planning and decomposition.",
        "category": "Core Subsystem",
        "requires": ["FEATURE_HIVE", "FEATURE_TOOLS_UAB"],
        "conflicts": [],
        "warning": "Requires both HIVE and TOOLS_UAB. Sub-agents will be able to interact with desktop applications through the UAB daemon. All actions are governance-gated and receipt-traced.",
    },

    # ── Vault-Backed Secrets ────────────────────────────────────────
    "FEATURE_VAULT_SECRETS": {
        "description": "Vault-Backed Secret Management — credentials stored in Fernet-encrypted vault instead of raw environment variables. Supports Docker secrets, env vars, or passphrase-derived keys (PBKDF2).",
        "category": "Security",
        "requires": [],
        "conflicts": [],
        "warning": "Disabling falls back to os.getenv() for credential resolution. Not recommended for production.",
    },

    # ── Federation ──────────────────────────────────────────────────
    "FEATURE_FEDERATION": {
        "description": "Federation — multi-instance coordination layer. Enables peer registration, task handoff, soul propagation, heartbeat mesh, and cross-instance kill propagation with Ed25519 authentication.",
        "category": "Core Subsystem",
        "requires": [],
        "conflicts": [],
        "warning": "Exposes federation API endpoints. Peers authenticate via Ed25519 challenge/response. All federation events are receipt-traced and audit-logged.",
    },
    "FEATURE_FEDERATION_DASHBOARD": {
        "description": "Federation Dashboard - operator fleet view above per-instance War Rooms. Surfaces instance health, pending approvals, trust proposals, budget pressure, and Command Center entry points.",
        "category": "Federation",
        "requires": ["FEATURE_FEDERATION"],
        "conflicts": [],
        "warning": "Shows cross-instance operational metadata to authenticated War Room operators. Remote detail retrieval uses signed federation requests.",
    },

    # ── MCP (Model Context Protocol) ────────────────────────────────
    "FEATURE_MCP": {
        "description": "MCP Master Kill Switch — enables governed MCP tool invocations through the MCP proxy. Every call routes through Soul permission → kill switch → network allowlist → argument screening → risk tier → receipt. Fail-closed on every gate.",
        "category": "Core Subsystem",
        "requires": ["FEATURE_CONNECTORS"],
        "conflicts": [],
        "warning": "MCP servers consume LLM tokens for tool discovery and invocation. Each server must be individually registered with credentials in the Vault. HTTP+SSE transport only — no stdio process spawning.",
    },
}

FLAG_META.update({
    "FEATURE_GOOGLE_OAUTH": {
        "description": "Google OAuth support for Gmail and Calendar connectors using Authorization Code plus PKCE.",
        "category": "Authentication",
        "requires": ["FEATURE_CONNECTORS"],
        "conflicts": [],
        "warning": "Requires a configured Google OAuth client and local callback routing. Disable when Google connectors are not in use.",
    },
    "FEATURE_TOOL_FLOW_STREAMING": {
        "description": "Streams tool and governance progress events to operator-facing clients while a Command Center run is active.",
        "category": "Runtime",
        "requires": ["FEATURE_AGENTIC_LOOP"],
        "conflicts": [],
        "warning": "Adds live event traffic for each governed run. Keep enabled for operator visibility during long-running work.",
    },
    "FEATURE_ACTION_CARDS": {
        "description": "Channel-neutral approval and action cards used by War Room and messaging surfaces for governed decisions.",
        "category": "Governance",
        "requires": ["FEATURE_RISK_TIERED_GOVERNANCE"],
        "conflicts": [],
        "warning": "Disabling removes interactive approval cards; governed actions may fall back to text-only approval flows.",
    },
    "FEATURE_OBSERVABILITY": {
        "description": "Observability subsystem for runtime metrics, health surfaces, receipt bridge signals, and optional OpenTelemetry export.",
        "category": "Operations",
        "requires": [],
        "conflicts": [],
        "warning": "When disabled, local health endpoints remain available but external telemetry export and observability panels may show limited data.",
    },
    "FEATURE_TIME_TRAVEL": {
        "description": "Time-travel debugging subsystem for inspecting historical runtime state and governance events.",
        "category": "Operations",
        "requires": ["FEATURE_BATCH_RECEIPTS"],
        "conflicts": [],
        "warning": "Stores additional diagnostic state. Disable in minimal deployments that do not need replay/debug inspection.",
    },
    "FEATURE_A2A": {
        "description": "Agent-to-Agent protocol support for signed cross-instance handoffs and coordination.",
        "category": "Federation",
        "requires": ["FEATURE_FEDERATION"],
        "conflicts": [],
        "warning": "Enables A2A endpoints and peer-facing coordination paths. Keep federation keys and allowlists configured before customer use.",
    },
    "FEATURE_INCIDENT_RESPONSE": {
        "description": "Incident response playbook engine for governed triage, containment, and operator notification workflows.",
        "category": "Operations",
        "requires": ["FEATURE_ACTION_CARDS"],
        "conflicts": [],
        "warning": "Playbooks can trigger operator-facing containment workflows. Review playbook configuration before enabling in production.",
    },
})

_PUBLIC_TEXT_REPLACEMENTS = {
    "\u00e2\u0080\u0094": "-",
    "\u00e2\u20ac\u201d": "-",
    "\u00e2\u0080\u0093": "-",
    "\u00e2\u20ac\u201c": "-",
    "\u00e2\u0086\u0092": "->",
    "\u00e2\u2020\u2019": "->",
    "\u00e2\u0080\u0099": "'",
    "\u00e2\u20ac\u2122": "'",
}


def _public_text(value: str) -> str:
    text = str(value or "")
    for bad, replacement in _PUBLIC_TEXT_REPLACEMENTS.items():
        text = text.replace(bad, replacement)
    return text


@router.get("")
async def get_flags():
    """Return all feature flag values with descriptions and metadata."""
    try:
        import feature_flags as ff
        flags = {}

        for attr in sorted(dir(ff)):
            if attr.startswith("FEATURE_"):
                val = getattr(ff, attr, None)
                if isinstance(val, bool):
                    meta = FLAG_META.get(attr, {})
                    entry = {
                        "enabled": val,
                        "restart_required": attr in ff.RESTART_REQUIRED_FLAGS,
                        "hot_toggleable": attr not in ff.RESTART_REQUIRED_FLAGS,
                        "hot_toggle_mode": _hot_toggle_mode_for_flag(attr),
                        "description": _public_text(meta.get("description", "")),
                        "category": _public_text(meta.get("category", "Other")),
                        "requires": meta.get("requires", []),
                        "conflicts": meta.get("conflicts", []),
                        "warning": _public_text(meta.get("warning", "")),
                    }
                    if meta.get("has_editor"):
                        entry["has_editor"] = meta["has_editor"]
                    if meta.get("confirm_enable"):
                        entry["confirm_enable"] = _public_text(meta["confirm_enable"])
                    if meta.get("hidden"):
                        entry["hidden"] = True
                    flags[attr] = entry

        return {"flags": flags}
    except Exception as exc:
        logger.error("get_flags error: %s", exc)
        return JSONResponse(status_code=500, content={"error": "Failed to read flags"})


# ── Network Allowlist Config ─────────────────────────────────────────
# NOTE: These routes MUST be defined before /{name}/* routes to avoid
# FastAPI matching "network-allowlist" as a flag name parameter.

_network_allowlist = NetworkAllowlistService()


class AllowlistUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domains: List[str]


@router.get("/network-allowlist")
async def get_network_allowlist():
    """Return current network allowlist config."""
    try:
        data = _network_allowlist.load_config()
        return {
            "domains": data.get("domains", []),
            "path": _network_allowlist.path,
        }
    except Exception as exc:
        logger.error("get_network_allowlist error: %s", exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})


@router.put("/network-allowlist")
async def update_network_allowlist(
    body: AllowlistUpdate,
    _authz: None = Depends(require_operator_capability("flags.admin")),
):
    """Update the network allowlist domains."""
    try:
        clean = _network_allowlist.set_domains(body.domains)
        # Reload the orchestrator's live NetworkInterceptor so changes take effect immediately
        try:
            from gateway import main_orchestrator
            if hasattr(main_orchestrator, 'network_interceptor'):
                main_orchestrator.network_interceptor.reload_allowlist()
                logger.info("Live NetworkInterceptor reloaded with %d domains", len(main_orchestrator.network_interceptor.ALLOW_LIST))
        except Exception as e:
            logger.warning("Could not reload live NetworkInterceptor: %s", e)
        logger.info("Network allowlist updated: %d domains", len(clean))
        return {"domains": clean, "count": len(clean)}
    except Exception as exc:
        logger.error("update_network_allowlist error: %s", exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})


# ── Host Agent Bridge ────────────────────────────────────────────────
# Routes for checking/controlling the Lancelot Host Agent running on the
# host machine. Must be defined before /{name}/* wildcard routes.

HOST_AGENT_URL = os.environ.get("HOST_AGENT_URL", "http://host.docker.internal:9111")
_LEGACY_HOST_AGENT_TOKEN = "lancelot-host-agent"
_HOST_AGENT_ALLOWED_HOSTNAMES = frozenset({
    "localhost",
    "127.0.0.1",
    "::1",
    "host.docker.internal",
})


def _get_host_agent_token_state() -> tuple[str, str]:
    token = os.environ.get("HOST_AGENT_TOKEN", "").strip()
    if not token:
        return "", "missing"
    if token == _LEGACY_HOST_AGENT_TOKEN:
        return "", "legacy_default"
    return token, "configured"


def _host_agent_url(path: str) -> str:
    return assert_local_control_url(
        f"{HOST_AGENT_URL}{path}",
        component="Host agent control request",
        allowed_hostnames=_HOST_AGENT_ALLOWED_HOSTNAMES,
    )


@router.get("/host-agent-status")
async def get_host_agent_status():
    """Check if the host agent is reachable and return its status."""
    import urllib.request
    import json as _json
    token, token_state = _get_host_agent_token_state()
    try:
        req = urllib.request.Request(_host_agent_url("/health"), method="GET")
        with urllib.request.urlopen(req, timeout=0.75) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        return {
            "reachable": True,
            "auth_configured": bool(token),
            "auth_state": token_state,
            "platform": data.get("platform", "unknown"),
            "platform_version": data.get("platform_version", ""),
            "hostname": data.get("hostname", "unknown"),
            "agent_version": data.get("agent_version", "unknown"),
        }
    except Exception:
        return {
            "reachable": False,
            "auth_configured": bool(token),
            "auth_state": token_state,
            "platform": "",
            "platform_version": "",
            "hostname": "",
            "agent_version": "",
        }


@router.post("/host-agent-shutdown")
async def shutdown_host_agent(
    _authz: None = Depends(require_operator_capability("flags.admin")),
):
    """Send shutdown signal to the host agent."""
    import urllib.request
    import json as _json
    token, token_state = _get_host_agent_token_state()
    if not token:
        error_message = "HOST_AGENT_TOKEN is not configured for host bridge control."
        if token_state == "legacy_default":
            error_message = (
                "HOST_AGENT_TOKEN is still using the rejected legacy default value "
                "for host bridge control."
            )
        return JSONResponse(
            status_code=503,
            content={"error": error_message},
        )
    try:
        req = urllib.request.Request(
            _host_agent_url("/shutdown"),
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        logger.info("Host agent shutdown signal sent")
        return {"status": "shutdown_sent", "agent_response": data}
    except Exception as exc:
        logger.warning("Failed to send shutdown to host agent: %s", exc)
        return JSONResponse(
            status_code=502,
            content={"error": f"Could not reach host agent: {str(exc)[:200]}"},
        )


# ── Host Write Commands Config ────────────────────────────────────────
# Editable list of dangerous commands allowed on the host when
# FEATURE_HOST_WRITE_COMMANDS is enabled.

WRITE_COMMANDS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config", "host_write_commands.yaml",
)


@router.get("/host-write-commands")
async def get_host_write_commands():
    """Return current host write commands list."""
    try:
        commands = []
        raw = ""
        if os.path.exists(WRITE_COMMANDS_PATH):
            with open(WRITE_COMMANDS_PATH, "r") as f:
                raw = f.read()
            for line in raw.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    commands.append(stripped)
        return {"commands": commands, "raw": raw, "path": WRITE_COMMANDS_PATH}
    except Exception as exc:
        logger.error("get_host_write_commands error: %s", exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})


class WriteCommandsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raw: str


@router.put("/host-write-commands")
async def update_host_write_commands(
    body: WriteCommandsUpdate,
    _authz: None = Depends(require_operator_capability("flags.admin")),
):
    """Update the host write commands list."""
    try:
        os.makedirs(os.path.dirname(WRITE_COMMANDS_PATH), exist_ok=True)
        with open(WRITE_COMMANDS_PATH, "w") as f:
            f.write(body.raw)
        # Count non-comment, non-empty lines
        commands = [
            ln.strip() for ln in body.raw.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        logger.info("Host write commands updated: %d commands", len(commands))
        return {"commands": commands, "count": len(commands)}
    except Exception as exc:
        logger.error("update_host_write_commands error: %s", exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})


# ── Host Write Commands Sub-Toggle ────────────────────────────────────
# Inline toggle for FEATURE_HOST_WRITE_COMMANDS (nested in Host Bridge panel).

@router.get("/host-write-status")
async def get_host_write_status():
    """Return current state of FEATURE_HOST_WRITE_COMMANDS."""
    import feature_flags as ff
    return {"enabled": getattr(ff, "FEATURE_HOST_WRITE_COMMANDS", False)}


@router.post("/host-write-toggle")
async def toggle_host_write_commands(
    _authz: None = Depends(require_operator_capability("flags.admin")),
):
    """Toggle FEATURE_HOST_WRITE_COMMANDS on/off."""
    import feature_flags as ff
    new_val = ff.toggle_flag("FEATURE_HOST_WRITE_COMMANDS")
    logger.info("FEATURE_HOST_WRITE_COMMANDS toggled to %s", new_val)
    return {"enabled": new_val}


# ── Dependency Validation ────────────────────────────────────────────

def _validate_flag_dependencies(name: str, new_value: bool) -> Optional[str]:
    """Validate requires/conflicts before enabling or disabling a flag.

    Returns an error message if validation fails, None if OK.
    """
    import feature_flags as ff

    meta = FLAG_META.get(name, {})

    if new_value:
        # Enabling: all required flags must be enabled
        for req in meta.get("requires", []):
            if not getattr(ff, req, False):
                return f"Cannot enable {name}: requires {req} to be enabled first"
        # Enabling: no conflicting flags can be enabled
        for conflict in meta.get("conflicts", []):
            if getattr(ff, conflict, False):
                return f"Cannot enable {name}: conflicts with {conflict} (currently enabled)"
    else:
        # Disabling: check if any other enabled flag depends on this one
        for other_name, other_meta in FLAG_META.items():
            if other_name == name:
                continue
            if name in other_meta.get("requires", []):
                if getattr(ff, other_name, False):
                    return f"Cannot disable {name}: {other_name} depends on it (disable {other_name} first)"

    return None


def _restart_required_for_flag(name: str) -> bool:
    import feature_flags as ff
    return name in getattr(ff, "RESTART_REQUIRED_FLAGS", frozenset())


def _registered_subsystem_for_flag(name: str):
    """Return the runtime subsystem registered for a flag, when one exists."""
    try:
        from subsystem_manager import subsystem_manager
        return subsystem_manager.get_by_flag(name)
    except Exception as exc:
        logger.debug("Subsystem lookup failed for %s: %s", name, exc)
        return None


def _hot_toggle_mode_for_flag(name: str) -> str:
    """Classify how a flag applies at runtime."""
    if _restart_required_for_flag(name):
        return "restart"
    if _registered_subsystem_for_flag(name) is not None:
        return "subsystem"
    return "dynamic"


def _apply_hot_toggle(name: str, value: bool, previous: bool) -> tuple[bool, str]:
    """Apply lifecycle hot-toggle when needed and report the toggle mode."""
    subsystem = _registered_subsystem_for_flag(name)
    if subsystem is None:
        return previous != value and not _restart_required_for_flag(name), "dynamic"

    from subsystem_manager import subsystem_manager

    if value and not subsystem_manager.is_running(subsystem.name):
        subsystem_manager.start(subsystem.name)
        return True, "subsystem"
    if not value and subsystem_manager.is_running(subsystem.name):
        subsystem_manager.stop(subsystem.name)
        return True, "subsystem"
    return previous != value and not _restart_required_for_flag(name), "subsystem"


# ── Flag Toggle/Set Routes ───────────────────────────────────────────

@router.post("/{name}/toggle")
async def toggle_flag(
    name: str,
    request: Request,
    _authz: None = Depends(require_operator_capability("flags.admin")),
):
    """Toggle a feature flag at runtime. Hot-toggles subsystems automatically."""
    try:
        import feature_flags as ff
        # Determine what the new value will be before toggling
        current = getattr(ff, name, None)
        if current is None or not isinstance(current, bool):
            return JSONResponse(status_code=400, content={"error": f"Unknown flag: {name}"})
        new_val = not current

        # Validate dependencies
        dep_error = _validate_flag_dependencies(name, new_val)
        if dep_error:
            return JSONResponse(status_code=400, content={"error": dep_error})

        new_val = ff.toggle_flag(name)

        try:
            hot_toggled, hot_toggle_mode = _apply_hot_toggle(name, new_val, current)
        except Exception as exc:
            logger.error("Hot-toggle failed for %s: %s", name, exc)
            return {
                "flag": name,
                "enabled": new_val,
                "restart_required": _restart_required_for_flag(name),
                "hot_toggled": False,
                "hot_toggle_mode": _hot_toggle_mode_for_flag(name),
                "message": f"{name} set to {new_val} but subsystem toggle failed: {exc}",
            }

        # Host Bridge lifecycle: auto-shutdown on disable, reachability check on enable
        agent_reachable = None
        if name == "FEATURE_TOOLS_HOST_BRIDGE":
            token, _ = _get_host_agent_token_state()
            if not new_val:
                # Shutting down — send stop signal to host agent
                if not token:
                    agent_reachable = False
                    logger.info("Host agent token not configured - skipping shutdown request")
                else:
                    try:
                        import urllib.request as _ur
                        _req = _ur.Request(
                            _host_agent_url("/shutdown"),
                            method="POST",
                            headers={"Authorization": f"Bearer {token}"},
                        )
                        _ur.urlopen(_req, timeout=3)
                        logger.info("Host agent shutdown signal sent (flag toggled off)")
                        agent_reachable = False
                    except Exception:
                        agent_reachable = False
            else:
                # Enabling — check if host agent is reachable
                try:
                    import urllib.request as _ur
                    _req = _ur.Request(_host_agent_url("/health"), method="GET")
                    _ur.urlopen(_req, timeout=3)
                    agent_reachable = True
                    logger.info("Host agent reachable on enable")
                except Exception:
                    agent_reachable = False
                    logger.info("Host agent NOT reachable on enable — user must start it")

        # Audit log the toggle
        if _audit_logger:
            _audit_logger.log_event(
                "WARROOM_FLAG_TOGGLE",
                f"Flag {name} toggled to {new_val}" + (
                    f" ({hot_toggle_mode} hot-toggled)" if hot_toggled else ""
                ),
                user=_resolve_audit_user(request),
            )

        # Governance receipt — kill switch issued (disabled) or lifted (enabled)
        from src.core.governance_receipts import emit_governance_receipt
        from src.shared.receipts import ActionType
        emit_governance_receipt(
            request,
            ActionType.KILL_SWITCH_LIFTED if new_val else ActionType.KILL_SWITCH_ISSUED,
            action_name="toggle_flag",
            inputs={"flag": name, "new_value": new_val, "hot_toggled": hot_toggled, "hot_toggle_mode": hot_toggle_mode},
        )

        result = {
            "flag": name,
            "enabled": new_val,
            "restart_required": _restart_required_for_flag(name),
            "hot_toggled": hot_toggled,
            "hot_toggle_mode": hot_toggle_mode,
            "message": f"{name} set to {new_val}" + (
                f" ({hot_toggle_mode} hot-toggled)" if hot_toggled else ""
            ),
        }
        if agent_reachable is not None:
            result["agent_reachable"] = agent_reachable
            if new_val and not agent_reachable:
                result["agent_start_hint"] = (
                    "Host Bridge enabled but the Host Agent is not running. "
                    "Start it on your host machine:\n"
                    "  host_agent\\start_agent.bat\n"
                    "Or install as a service:\n"
                    "  host_agent\\install_service.bat"
                )
        return result
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except Exception as exc:
        logger.error("toggle_flag error: %s", exc)
        return JSONResponse(status_code=500, content={"error": "Failed to toggle flag"})


@router.post("/{name}/set")
async def set_flag(
    name: str,
    request: Request,
    value: bool = True,
    _authz: None = Depends(require_operator_capability("flags.admin")),
):
    """Set a feature flag to a specific value. Hot-toggles subsystems automatically."""
    try:
        import feature_flags as ff
        current = getattr(ff, name, None)
        if current is None or not isinstance(current, bool):
            return JSONResponse(status_code=400, content={"error": f"Unknown flag: {name}"})

        # Validate dependencies
        dep_error = _validate_flag_dependencies(name, value)
        if dep_error:
            return JSONResponse(status_code=400, content={"error": dep_error})

        ff.set_flag(name, value)

        try:
            hot_toggled, hot_toggle_mode = _apply_hot_toggle(name, value, current)
        except Exception as exc:
            logger.error("Hot-toggle failed for %s: %s", name, exc)
            return {
                "flag": name,
                "enabled": value,
                "restart_required": _restart_required_for_flag(name),
                "hot_toggled": False,
                "hot_toggle_mode": _hot_toggle_mode_for_flag(name),
                "hot_toggle_error": str(exc),
                "message": f"{name} set to {value} but subsystem toggle failed: {exc}",
            }

        # Governance receipt
        from src.core.governance_receipts import emit_governance_receipt
        from src.shared.receipts import ActionType
        emit_governance_receipt(
            request,
            ActionType.KILL_SWITCH_LIFTED if value else ActionType.KILL_SWITCH_ISSUED,
            action_name="set_flag",
            inputs={"flag": name, "value": value, "hot_toggled": hot_toggled, "hot_toggle_mode": hot_toggle_mode},
        )

        return {
            "flag": name,
            "enabled": value,
            "restart_required": _restart_required_for_flag(name),
            "hot_toggled": hot_toggled,
            "hot_toggle_mode": hot_toggle_mode,
        }
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except Exception as exc:
        logger.error("set_flag error: %s", exc)
        return JSONResponse(status_code=500, content={"error": "Failed to set flag"})


# ── UAB Panel Endpoints ──────────────────────────────────────────────
# Inline editor endpoints for the Universal App Bridge panel in Kill Switches.

UAB_DAEMON_URL = os.environ.get("UAB_DAEMON_URL", "http://host.docker.internal:7900")


def _uab_rpc(method: str, params: Optional[dict] = None, timeout: float = 0.75):
    """Call the UAB daemon JSON-RPC compatibility endpoint."""
    import urllib.request
    import json as _json

    payload = _json.dumps({
        "jsonrpc": "2.0",
        "method": method,
        "params": params or {},
        "id": 1,
    }).encode("utf-8")
    req = urllib.request.Request(
        UAB_DAEMON_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = _json.loads(resp.read().decode("utf-8"))
    return data.get("result")


@router.get("/uab-status")
async def get_uab_status():
    """Check if the UAB daemon is reachable and return its status."""
    try:
        result = _uab_rpc("getStatus") or {}
        connections = result.get("connections", [])
        return {
            "reachable": True,
            "version": result.get("version", "unknown"),
            "connected_apps": result.get("connectedApps", 0),
            "supported_frameworks": result.get("supportedFrameworks", []),
            "uptime_seconds": result.get("uptimeSeconds", 0),
            "transport": result.get("transport", "json-rpc"),
            "standalone_features": result.get("standaloneFeatures", []),
            "connections": connections if isinstance(connections, list) else [],
        }
    except Exception:
        return {
            "reachable": False,
            "version": "",
            "connected_apps": 0,
            "supported_frameworks": [],
            "uptime_seconds": 0,
            "transport": "",
            "standalone_features": [],
            "connections": [],
        }


@router.get("/uab-apps")
async def get_uab_connected_apps():
    """Get list of currently connected apps from the UAB daemon."""
    try:
        status = _uab_rpc("getStatus") or {}
        apps = status.get("connections", [])
        if not isinstance(apps, list):
            apps = []
        normalized = []
        for item in apps:
            if not isinstance(item, dict):
                continue
            normalized.append({
                "pid": item.get("pid", 0),
                "name": item.get("name", "unknown"),
                "framework": item.get("framework", "unknown"),
                "connectionMethod": item.get("connectionMethod") or item.get("method"),
                "windowTitle": item.get("windowTitle", ""),
                "elementCount": item.get("elementCount", 0),
            })
        return {"apps": normalized}
    except Exception:
        return {"apps": []}


@router.get("/uab-receipts")
async def get_uab_receipts(
    limit: int = 50,
    app_name: Optional[str] = None,
    mutating_only: bool = False,
    action_type: Optional[str] = None,
):
    """Query recent UAB app control receipts."""
    try:
        from src.tools.receipts_uab import get_uab_receipt_store
        store = get_uab_receipt_store()
        receipts = store.get_recent_receipts(
            limit=limit,
            app_name=app_name,
            mutating_only=mutating_only,
            action_type=action_type,
        )
        return {"receipts": [r.to_dict() for r in receipts]}
    except Exception as exc:
        logger.warning("Failed to query UAB receipts: %s", exc)
        return {"receipts": [], "error": str(exc)[:200]}


@router.get("/uab-sessions")
async def get_uab_sessions(limit: int = 20):
    """Get UAB app session summaries (active + recent)."""
    try:
        from src.tools.receipts_uab import get_uab_receipt_store
        store = get_uab_receipt_store()
        return {"sessions": store.get_session_summaries(limit=limit)}
    except Exception as exc:
        logger.warning("Failed to query UAB sessions: %s", exc)
        return {"sessions": [], "error": str(exc)[:200]}
