"""
Flags API — /api/flags

Exposes current feature flag values, descriptions, dependency info,
and allows runtime toggling for the War Room Kill Switches page.
"""

import logging
import os

import yaml
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/flags", tags=["flags"])

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
        "description": "Tiered memory system (vNext3). Provides 5 core blocks (persona, human, mission, operating_rules, workspace_state), working/episodic/archival storage, context compiler, governed self-edits, and full-text search.",
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

    # ── Execution & Runtime ───────────────────────────────────────
    "FEATURE_RESPONSE_ASSEMBLER": {
        "description": "Response assembly pipeline. Always active since Fix Pack V2 — this flag is informational only. The assembler processes raw LLM output through formatting, citation injection, and artifact extraction.",
        "category": "Runtime",
        "requires": [],
        "conflicts": [],
        "warning": "This flag is informational only. The response assembler is always active since Fix Pack V2 for output hygiene. Toggling has no effect.",
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

    # ── Governance (vNext4) ───────────────────────────────────────
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

    # ── Business Automation Layer ──────────────────────────────────
    "FEATURE_BAL": {
        "description": "Business Automation Layer — client management, intake, delivery, and billing workflows. Provides CRM, content pipeline, and revenue tracking.",
        "category": "Core Subsystem",
        "requires": [],
        "conflicts": [],
        "warning": "Disabling will stop all BAL client operations and close the database connection. Active client workflows will be interrupted.",
    },

    # ── Structural Fixes (V23) ────────────────────────────────────
    "FEATURE_STRUCTURED_OUTPUT": {
        "description": "Structured Output Mode. After the agentic loop completes, a reformat step converts the raw response into a verified JSON structure with explicit fields: response_to_user, actions_taken, next_action. A presentation layer cross-references claimed actions against actual tool receipts and silently drops anything that didn't happen. Converts the verified JSON back to readable chat text. Eliminates narration, fake progress claims, and verbose failure descriptions at the format level.",
        "category": "Intelligence",
        "requires": ["FEATURE_AGENTIC_LOOP"],
        "conflicts": [],
        "warning": "Requires AGENTIC_LOOP. Adds one additional LLM call per agentic response for reformatting. If reformatting fails, falls back to raw text. Monitor logs for 'V23: Structured reformat' entries.",
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
        "warning": "Adds one fast-model API call per incoming message for classification (~100 input tokens). Falls back to keyword classifier if the API call fails. Monitor for classification accuracy — check logs for 'V23 Unified Classifier' entries.",
    },
}


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
                        "description": meta.get("description", ""),
                        "category": meta.get("category", "Other"),
                        "requires": meta.get("requires", []),
                        "conflicts": meta.get("conflicts", []),
                        "warning": meta.get("warning", ""),
                    }
                    if meta.get("has_editor"):
                        entry["has_editor"] = meta["has_editor"]
                    if meta.get("confirm_enable"):
                        entry["confirm_enable"] = meta["confirm_enable"]
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

ALLOWLIST_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config", "network_allowlist.yaml",
)


def _load_allowlist() -> dict:
    """Load network_allowlist.yaml, returning default structure if missing."""
    try:
        with open(ALLOWLIST_PATH, "r") as f:
            data = yaml.safe_load(f) or {}
        return data
    except FileNotFoundError:
        return {"domains": [], "notes": "No allowlist config found. Create config/network_allowlist.yaml."}


def _save_allowlist(data: dict) -> None:
    """Persist allowlist config to YAML."""
    os.makedirs(os.path.dirname(ALLOWLIST_PATH), exist_ok=True)
    with open(ALLOWLIST_PATH, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


class AllowlistUpdate(BaseModel):
    domains: List[str]


@router.get("/network-allowlist")
async def get_network_allowlist():
    """Return current network allowlist config."""
    try:
        data = _load_allowlist()
        return {
            "domains": data.get("domains", []),
            "path": ALLOWLIST_PATH,
        }
    except Exception as exc:
        logger.error("get_network_allowlist error: %s", exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})


@router.put("/network-allowlist")
async def update_network_allowlist(body: AllowlistUpdate):
    """Update the network allowlist domains."""
    try:
        data = _load_allowlist()
        # Clean and deduplicate domains
        clean = sorted(set(d.strip().lower() for d in body.domains if d.strip()))
        data["domains"] = clean
        _save_allowlist(data)
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
HOST_AGENT_TOKEN = os.environ.get("HOST_AGENT_TOKEN", "lancelot-host-agent")


@router.get("/host-agent-status")
async def get_host_agent_status():
    """Check if the host agent is reachable and return its status."""
    import urllib.request
    import json as _json
    try:
        req = urllib.request.Request(f"{HOST_AGENT_URL}/health", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        return {
            "reachable": True,
            "platform": data.get("platform", "unknown"),
            "platform_version": data.get("platform_version", ""),
            "hostname": data.get("hostname", "unknown"),
            "agent_version": data.get("agent_version", "unknown"),
        }
    except Exception:
        return {
            "reachable": False,
            "platform": "",
            "platform_version": "",
            "hostname": "",
            "agent_version": "",
        }


@router.post("/host-agent-shutdown")
async def shutdown_host_agent():
    """Send shutdown signal to the host agent."""
    import urllib.request
    import json as _json
    try:
        req = urllib.request.Request(
            f"{HOST_AGENT_URL}/shutdown",
            method="POST",
            headers={
                "Authorization": f"Bearer {HOST_AGENT_TOKEN}",
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
    raw: str


@router.put("/host-write-commands")
async def update_host_write_commands(body: WriteCommandsUpdate):
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
async def toggle_host_write_commands():
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


# ── Flag Toggle/Set Routes ───────────────────────────────────────────

@router.post("/{name}/toggle")
async def toggle_flag(name: str):
    """Toggle a feature flag at runtime. Hot-toggles subsystems automatically."""
    try:
        import feature_flags as ff
        from subsystem_manager import subsystem_manager

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

        # Hot-toggle: start or stop subsystem if one is registered for this flag
        hot_toggled = False
        subsystem = subsystem_manager.get_by_flag(name)
        if subsystem:
            try:
                if new_val and not subsystem_manager.is_running(subsystem.name):
                    subsystem_manager.start(subsystem.name)
                    hot_toggled = True
                elif not new_val and subsystem_manager.is_running(subsystem.name):
                    subsystem_manager.stop(subsystem.name)
                    hot_toggled = True
            except Exception as exc:
                logger.error("Hot-toggle failed for %s: %s", name, exc)
                return {
                    "flag": name,
                    "enabled": new_val,
                    "restart_required": False,
                    "hot_toggled": False,
                    "message": f"{name} set to {new_val} but subsystem toggle failed: {exc}",
                }

        # Auto-shutdown host agent when disabling HOST_BRIDGE
        if name == "FEATURE_TOOLS_HOST_BRIDGE" and not new_val:
            try:
                import urllib.request as _ur
                _req = _ur.Request(
                    f"{HOST_AGENT_URL}/shutdown",
                    method="POST",
                    headers={"Authorization": f"Bearer {HOST_AGENT_TOKEN}"},
                )
                _ur.urlopen(_req, timeout=3)
                logger.info("Host agent shutdown signal sent (flag toggled off)")
            except Exception:
                pass  # Best-effort — agent may already be offline

        return {
            "flag": name,
            "enabled": new_val,
            "restart_required": False,
            "hot_toggled": hot_toggled,
            "message": f"{name} set to {new_val}" + (
                " (subsystem hot-toggled)" if hot_toggled else ""
            ),
        }
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except Exception as exc:
        logger.error("toggle_flag error: %s", exc)
        return JSONResponse(status_code=500, content={"error": "Failed to toggle flag"})


@router.post("/{name}/set")
async def set_flag(name: str, value: bool = True):
    """Set a feature flag to a specific value. Hot-toggles subsystems automatically."""
    try:
        import feature_flags as ff
        from subsystem_manager import subsystem_manager

        # Validate dependencies
        dep_error = _validate_flag_dependencies(name, value)
        if dep_error:
            return JSONResponse(status_code=400, content={"error": dep_error})

        ff.set_flag(name, value)

        # Hot-toggle: start or stop subsystem if one is registered for this flag
        hot_toggled = False
        subsystem = subsystem_manager.get_by_flag(name)
        if subsystem:
            try:
                if value and not subsystem_manager.is_running(subsystem.name):
                    subsystem_manager.start(subsystem.name)
                    hot_toggled = True
                elif not value and subsystem_manager.is_running(subsystem.name):
                    subsystem_manager.stop(subsystem.name)
                    hot_toggled = True
            except Exception as exc:
                logger.error("Hot-toggle failed for %s: %s", name, exc)

        return {
            "flag": name,
            "enabled": value,
            "restart_required": False,
            "hot_toggled": hot_toggled,
        }
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except Exception as exc:
        logger.error("set_flag error: %s", exc)
        return JSONResponse(status_code=500, content={"error": "Failed to set flag"})
