"""Governance subsystem wiring for the Lancelot orchestrator.

This module keeps startup wiring, trust summaries, and execution event
recording separate from the main chat/runtime orchestration flow.
"""

from __future__ import annotations

import logging as _logging
import os
from typing import Any


_gov_logger = _logging.getLogger("orchestrator")

try:
    from governance.config import load_governance_config
    from governance.risk_classifier import RiskClassifier
    from governance.async_verifier import AsyncVerificationQueue
    from governance.rollback import RollbackManager
    from governance.models import RiskTier
    from governance.intent_templates import IntentTemplateRegistry
    import feature_flags as _ff

    _GOVERNANCE_AVAILABLE = True
except ImportError:
    _GOVERNANCE_AVAILABLE = False

try:
    from governance.trust_ledger import TrustLedger
    from governance.trust_models import load_trust_config

    _TRUST_AVAILABLE = True
except ImportError:
    _TRUST_AVAILABLE = False

try:
    from governance.approval_learning.decision_log import DecisionLog
    from governance.approval_learning.rule_engine import RuleEngine
    from governance.approval_learning.config import load_apl_config

    _APL_AVAILABLE = True
except ImportError:
    _APL_AVAILABLE = False


def init_governance(runtime: Any) -> None:
    """Initialize governance subsystems when their feature flags are enabled."""
    if _TRUST_AVAILABLE:
        try:
            import feature_flags as _trust_ff

            if _trust_ff.FEATURE_TRUST_LEDGER:
                trust_config = load_trust_config()
                runtime.trust_ledger = TrustLedger(
                    config=trust_config,
                    data_dir=runtime.data_dir,
                )
                runtime.seed_trust_records()
                _gov_logger.info("TrustLedger initialized")
        except Exception as exc:
            _gov_logger.error("TrustLedger init failed: %s", exc)
            runtime.trust_ledger = None

    if _APL_AVAILABLE:
        try:
            import feature_flags as _apl_ff

            if _apl_ff.FEATURE_APPROVAL_LEARNING:
                apl_config = load_apl_config()
                runtime.decision_log = DecisionLog(config=apl_config)
                runtime.rule_engine = RuleEngine(config=apl_config, decision_log=runtime.decision_log)
                _gov_logger.info("DecisionLog + RuleEngine initialized (APL)")
        except Exception as exc:
            _gov_logger.error("APL init failed: %s", exc)
            runtime.decision_log = None
            runtime.rule_engine = None

    if not _GOVERNANCE_AVAILABLE:
        return
    if not _ff.FEATURE_RISK_TIERED_GOVERNANCE:
        return

    try:
        gov_config = load_governance_config()
        risk_classifier = RiskClassifier(gov_config.risk_classification)
        async_queue = None
        rollback_manager = None
        template_registry = None
        runtime.set_governance_runtime(risk_classifier=risk_classifier)
        _gov_logger.info("vNext4: RiskClassifier initialized")

        if _ff.FEATURE_ASYNC_VERIFICATION:
            async_queue = AsyncVerificationQueue(
                verify_fn=runtime.verify_async_job,
                config=gov_config.async_verification,
            )
            workspace = os.getenv("LANCELOT_WORKSPACE", "/home/lancelot/workspace")
            rollback_manager = RollbackManager(workspace=workspace)
            runtime.set_governance_runtime(
                risk_classifier=risk_classifier,
                async_queue=async_queue,
                rollback_manager=rollback_manager,
                template_registry=template_registry,
            )
            _gov_logger.info("vNext4: AsyncVerificationQueue + RollbackManager initialized")

        if _ff.FEATURE_INTENT_TEMPLATES:
            template_registry = IntentTemplateRegistry(
                config=gov_config.intent_templates,
                data_dir=os.path.join(runtime.data_dir, "governance"),
            )
            runtime.set_governance_runtime(
                risk_classifier=risk_classifier,
                async_queue=async_queue,
                rollback_manager=rollback_manager,
                template_registry=template_registry,
            )
            _gov_logger.info("vNext4: IntentTemplateRegistry initialized")
    except Exception as exc:
        _gov_logger.error("vNext4 governance init failed: %s", exc)
        runtime.set_governance_runtime(
            risk_classifier=None,
            async_queue=None,
            rollback_manager=None,
            template_registry=None,
        )


def seed_trust_records(runtime: Any) -> None:
    """Seed baseline trust records so the governance UI has data from day one."""
    if not runtime.trust_ledger:
        return
    try:
        seed_capabilities = [
            ("fs.read", "workspace", RiskTier.T0_INERT),
            ("fs.list", "workspace", RiskTier.T0_INERT),
            ("fs.write", "workspace", RiskTier.T1_REVERSIBLE),
            ("shell.exec", "workspace", RiskTier.T2_CONTROLLED),
            ("chat.send", "telegram", RiskTier.T1_REVERSIBLE),
            ("chat.send", "google_chat", RiskTier.T1_REVERSIBLE),
            ("memory.write", "working", RiskTier.T1_REVERSIBLE),
            ("memory.write", "archival", RiskTier.T2_CONTROLLED),
            ("scheduler.create", "default", RiskTier.T2_CONTROLLED),
            ("skill.install", "marketplace", RiskTier.T3_IRREVERSIBLE),
        ]
        for capability, scope, tier in seed_capabilities:
            runtime.trust_ledger.get_or_create_record(capability, scope, default_tier=tier)
        _gov_logger.info("Seeded %d baseline trust records", len(seed_capabilities))
    except Exception as exc:
        _gov_logger.debug("Trust seed failed (non-fatal): %s", exc)


def get_trust_summary(runtime: Any, skill_name: str, inputs: dict) -> str:
    """Get trust record summary for a skill."""
    try:
        if hasattr(runtime, "trust_ledger") and runtime.trust_ledger:
            scope = str(inputs.get("url", inputs.get("command", inputs.get("path", "default"))))
            record = runtime.trust_ledger.get_record(skill_name, scope)
            if record:
                return (
                    f"Tier: {record.current_tier.name}, "
                    f"{record.consecutive_successes} consecutive successes, "
                    f"{record.total_failures} failures"
                )
    except Exception as exc:
        _logging.warning("Failed to read trust summary for %s: %s", skill_name, exc)
    return "Trust data unavailable"


def suggest_alternatives(skill_name: str, inputs: dict) -> list[str]:
    """Suggest lower-risk approaches when a skill is blocked."""
    alternatives_map = {
        "command_runner": [
            "Use repo_writer for file operations instead of shell commands",
            "Use network_client for API calls instead of curl",
            "Break the command into smaller, pre-approved operations",
        ],
        "repo_writer": [
            "Use repo_writer with 'edit' action instead of 'delete'",
            "Write to a workspace-scoped temporary location",
            "Queue the file operation for Commander approval",
        ],
        "network_client": [
            "Use GET to read-only fetch data first",
            "Use github_search for GitHub-specific queries",
            "Queue the write operation for Commander approval",
        ],
        "service_runner": [
            "Use command_runner for status checks instead",
            "Request service changes via the War Room",
        ],
    }
    return alternatives_map.get(
        skill_name,
        [
            "Try a read-only approach to gather the needed information",
            "Break the operation into smaller, lower-risk steps",
            "Note the limitation and suggest the Commander approve via War Room",
        ],
    )


def record_governance_event(runtime: Any, capability: str, scope: str, tier: Any, success: bool) -> None:
    """Record a tool execution to the trust ledger and decision log."""
    if runtime.trust_ledger:
        try:
            ledger_scope = scope or "default"
            runtime.trust_ledger.get_or_create_record(capability, ledger_scope, default_tier=tier)
            if success:
                runtime.trust_ledger.record_success(capability, ledger_scope)
            else:
                runtime.trust_ledger.record_failure(capability, ledger_scope)
        except Exception as exc:
            _gov_logger.debug("Trust ledger record failed: %s", exc)

    if runtime.decision_log:
        try:
            from governance.approval_learning.models import DecisionContext

            ctx = DecisionContext.from_action(
                capability=capability,
                target=scope or "",
                risk_tier=tier if isinstance(tier, int) else int(tier),
            )
            runtime.decision_log.record(
                ctx,
                decision="approved" if success else "denied",
                reason="auto-execution" if success else "execution-failed",
            )
        except Exception as exc:
            _gov_logger.debug("Decision log record failed: %s", exc)
