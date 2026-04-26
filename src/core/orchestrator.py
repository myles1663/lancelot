# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

import os
import re
import subprocess
import shlex
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Optional
from providers.base import ProviderClient, GenerateResult, ToolCall
from providers.tool_schema import NormalizedToolDeclaration
from security import InputSanitizer, AuditLogger, NetworkInterceptor, CognitionGovernor, Sentry
from receipts import (
    create_finalized_receipt,
    create_receipt,
    get_receipt_service,
    ActionType,
    CognitionTier,
)
from context_env import ContextEnvironment
from librarian import FileAction
from planner import Planner
from verifier import Verifier
from planning_pipeline import PlanningPipeline
from intent_classifier import classify_intent, IntentType

# Extracted helper functions for orchestrator intent, safety, and response flow.
from orch_helpers.safety_helpers import (
    classify_tool_call_safety as _classify_tool_call_safety_fn,
    is_narration_without_content as _is_narration_without_content_fn,
    strip_failure_narration as _strip_failure_narration_fn,
    validate_rule_content as _validate_rule_content_fn,
    generate_honest_replacement as _generate_honest_replacement_fn,
)
from orch_helpers.response_helpers import (
    format_tool_receipts as _format_tool_receipts_fn,
)
import chat_flow as _chat_flow_module
from chat_flow import chat as _chat_impl
from orchestrator_consts import COMMAND_BLACKLIST_CHARS, COMMAND_WHITELIST
from orchestrator_ext import (
    _build_openai_tool_declarations as _build_openai_tool_declarations_impl,
    _build_system_instruction as _build_system_instruction_impl,
    _build_tool_declarations as _build_tool_declarations_impl,
    _deep_reasoning_pass as _deep_reasoning_pass_impl,
    _handle_proceed as _handle_proceed_impl,
    _init_provider as _init_provider_impl,
    _record_task_experience as _record_task_experience_impl,
)
from orchestrator_frontier import (
    build_frontier_tool_response_message as _build_frontier_tool_response_message_impl,
    build_frontier_user_message as _build_frontier_user_message_impl,
    emit_frontier_scrub_receipt as _emit_frontier_scrub_receipt_impl,
    get_frontier_scrubber as _get_frontier_scrubber_impl,
    provider_generate as _provider_generate_impl,
    provider_generate_with_tools as _provider_generate_with_tools_impl,
    record_frontier_scrub_result as _record_frontier_scrub_result_impl,
    redact_for_frontier as _redact_for_frontier_impl,
    scrub_frontier_payload as _scrub_frontier_payload_impl,
)
from orchestrator_governance import (
    get_trust_summary as _get_trust_summary_impl,
    init_governance as _init_governance_impl,
    record_governance_event as _record_governance_event_impl,
    seed_trust_records as _seed_trust_records_impl,
    suggest_alternatives as _suggest_alternatives_impl,
)
from orchestrator_generation import (
    build_reasoning_instruction as _build_reasoning_instruction_impl,
    get_thinking_config as _get_thinking_config_impl,
    is_retryable_error as _is_retryable_error_impl,
    llm_call_with_retry as _llm_call_with_retry_impl,
    should_use_deep_reasoning as _should_use_deep_reasoning_impl,
    text_only_generate as _text_only_generate_impl,
)
from orchestrator_identity import (
    build_execution_instruction as _build_execution_instruction_impl,
    build_self_awareness as _build_self_awareness_impl,
)
from orchestrator_provider import (
    get_anthropic_oauth_token as _get_anthropic_oauth_token_impl,
    get_deep_model as _get_deep_model_impl,
    get_openai_codex_oauth_token as _get_openai_codex_oauth_token_impl,
    has_openai_codex_cli_auth as _has_openai_codex_cli_auth_impl,
    route_model as _route_model_impl,
    set_lane_model as _set_lane_model_impl,
    switch_provider as _switch_provider_impl,
)
from orchestrator_routing import (
    SIMPLE_ACTION_MAP as _SIMPLE_ACTION_MAP_IMPL,
    build_simple_action_plan as _build_simple_action_plan_impl,
    check_name_update as _check_name_update_impl,
    extract_literal_terms as _extract_literal_terms_impl,
    is_conversational as _is_conversational_impl,
    is_continuation as _is_continuation_impl,
    is_low_risk_exec as _is_low_risk_exec_impl,
    is_simple_for_local as _is_simple_for_local_impl,
    needs_research as _needs_research_impl,
    previous_was_substantive as _previous_was_substantive_impl,
    verify_intent_with_llm as _verify_intent_with_llm_impl,
    wants_action as _wants_action_impl,
)
from orchestrator_response_delivery import (
    append_download_links as _append_download_links_impl,
    auto_create_document as _auto_create_document_impl,
    deliver_war_room_artifacts as _deliver_war_room_artifacts_impl,
    force_synthesis as _force_synthesis_impl,
    validate_llm_response as _validate_llm_response_impl,
)
from orchestrator_approval import (
    handle_approval as _handle_approval_impl,
    is_proceed_message as _is_proceed_message_impl,
    request_permission as _request_permission_impl,
)
from orchestrator_context import (
    init_context_cache as _init_context_cache_impl,
    load_memory as _load_memory_impl,
    log_rule_candidate as _log_rule_candidate_impl,
    query_memory as _query_memory_impl,
    update_rules as _update_rules_impl,
)
from tool_loop import (
    _agentic_generate as _agentic_generate_impl,
    _execute_command as _execute_command_impl,
    _execute_with_llm as _execute_with_llm_impl,
    _local_agentic_generate as _local_agentic_generate_impl,
    execute_plan as _execute_plan_impl,
)
from src.core.frontier_scrubber import (
    LocalPIIScrubber,
    detect_frontier_pii_categories as _detect_frontier_pii_categories_fn,
    normalize_frontier_pii_text as _normalize_frontier_pii_text_fn,
    validate_frontier_redaction as _validate_frontier_redaction_fn,
)

# Governance imports (conditional)
import logging as _logging
_gov_logger = _logging.getLogger(__name__)

def _normalize_frontier_pii_text(text: str) -> str:
    """Normalize obfuscated separators before structured PII detection."""
    return _normalize_frontier_pii_text_fn(text)


def _detect_frontier_pii_categories(text: str) -> set[str]:
    """Detect obvious structured PII that must not leave the local scrub lane."""
    return _detect_frontier_pii_categories_fn(text)


def _validate_frontier_redaction(original: str, redacted: str) -> tuple[bool, str]:
    """Reject local scrub output that still carries detectable structured PII."""
    return _validate_frontier_redaction_fn(original, redacted)

from plan_builder import EnvContext
from plan_types import OutcomeType
from dataclasses import dataclass

# File/image attachment for multimodal chat
@dataclass
class ChatAttachment:
    """A file or image attached to a chat message."""
    filename: str
    mime_type: str
    data: bytes

# Execution authority and tasking imports
try:
    from response.assembler import ResponseAssembler, AssembledResponse
    from action_language_gate import check_action_language
    from tasking.schema import RunStatus, TaskGraph, TaskRun, TaskStep
    from tasking.store import TaskStore
    from tasking.compiler import PlanCompiler
    from tasking.runner import TaskRunner
    from execution_authority.schema import ExecutionToken, TokenStatus
    from execution_authority.store import ExecutionTokenStore
    from execution_authority.minter import PermissionMinter
except ImportError:
    try:
        from src.core.response.assembler import ResponseAssembler, AssembledResponse
        from src.core.action_language_gate import check_action_language
        from src.core.tasking.schema import RunStatus, TaskGraph, TaskRun, TaskStep
        from src.core.tasking.store import TaskStore
        from src.core.tasking.compiler import PlanCompiler
        from src.core.tasking.runner import TaskRunner
        from src.core.execution_authority.schema import ExecutionToken, TokenStatus
        from src.core.execution_authority.store import ExecutionTokenStore
        from src.core.execution_authority.minter import PermissionMinter
    except ImportError as e:
        _gov_logger.warning("Execution authority imports unavailable: %s", e)
        ResponseAssembler = None
        check_action_language = None
        TaskStore = None
        PlanCompiler = None
        TaskRunner = None
        ExecutionTokenStore = None
        PermissionMinter = None

class RuntimeState(Enum):
    ACTIVE = "active"
    SLEEPING = "sleeping"
    BUSY = "busy"

class LancelotOrchestrator:
    def __init__(self, data_dir: str = "/home/lancelot/data"):
        self.data_dir = data_dir
        self.state = RuntimeState.ACTIVE
        self.user_context = ""
        self.rules_context = ""
        self.memory_summary = ""
        self.provider: Optional[ProviderClient] = None
        self.model_name = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
        self.sentry = None

        # Context caching
        self._cache = None
        self._cache_ttl = int(os.getenv("GEMINI_CACHE_TTL", "3600"))
        self._cache_model = os.getenv("GEMINI_CACHE_MODEL", "gemini-2.5-flash")

        # Security Modules
        self.sanitizer = InputSanitizer()
        self.audit_logger = AuditLogger()
        self.network_interceptor = NetworkInterceptor()
        self.governor = CognitionGovernor(self.data_dir)
        self.sentry = Sentry(self.data_dir)
        
        
        # Receipt Service
        self.receipt_service = get_receipt_service(self.data_dir)
        self.file_ops = FileAction(receipt_service=self.receipt_service)
        
        # Context Environment (replaces RAG)
        self.context_env = ContextEnvironment(self.data_dir)
        
        # S15: Planner
        self.planner = Planner(self.model_name)
        
        # S16: Verifier
        self.verifier = Verifier(self.model_name)

        # Honest Closure: Planning Pipeline
        self.planning_pipeline = PlanningPipeline(
            env_context=EnvContext(
                available_tools=list(COMMAND_WHITELIST),
                os_info="Docker Alpine Linux",
            )
        )

        # Subsystem references (injected by gateway at startup)
        self.soul = None
        self.skill_executor = None
        self.scheduler_service = None
        self.job_executor = None
        self.local_model = None  # LocalModelClient for local agentic routing
        self.local_model_roles = None  # Role router for scrub + utility local model lanes
        self.model_router = None  # Injected by gateway for local redaction + utility routing
        self.frontier_scrubber = LocalPIIScrubber()
        self.usage_tracker = None  # Injected by gateway for Cost Tracker panel
        self._memory_enabled = False
        self.context_compiler = None
        self.work_ledger_store = None

        # Runtime bridge for streamed tool-execution events.
        self.toolflow_emitter = None
        # Runtime bridge for approval and action-card creation.
        self.actioncard_factory = None

        # Execution authority, tasking, and response assembler
        self._init_fix_pack_v1()

        self._load_memory()
        self._init_provider()
        self._init_context_cache()

        # Risk-tiered governance subsystems
        self._risk_classifier = None
        self._async_queue = None
        self._rollback_manager = None
        self._template_registry = None

        # Governance subsystem instances (used by Governance API, Trust API, APL API)
        self.trust_ledger = None
        self.decision_log = None
        self.rule_engine = None

        self._init_governance()

    def _verify_async_job(self, job) -> bool:
        """Fail closed when the verifier cannot prove a reversible action succeeded."""
        goal = getattr(job, "goal", "") or getattr(job, "capability", "")
        output = getattr(job, "output", "")
        verification = self.verifier.verify_step(goal, str(output))
        return verification.success

    def _current_model_usage_status(self) -> dict:
        """Return the persisted local-model usage policy + runtime status."""
        from src.core.model_usage_policy import get_model_usage_status

        return get_model_usage_status()

    def _emit_chat_progress(self, phase: str, message: str, **metadata: Any) -> None:
        """Publish bounded chat progress for War Room without exposing payloads."""
        try:
            from event_bus import Event, event_bus
        except Exception:
            return

        payload = {
            "quest_id": getattr(self, "_current_quest_id", None),
            "phase": phase,
            "message": message,
            "channel": getattr(self, "_current_channel", None),
            "operator_id": getattr(self, "_current_operator_id", None),
            "operator_name": getattr(self, "_current_operator_name", None),
            "session_id": getattr(self, "_current_session_id", None),
        }
        payload.update({
            key: value for key, value in metadata.items()
            if value is not None
        })
        event_bus.publish_sync(Event(type="chat.progress", payload=payload))

    def _emit_frontier_scrub_receipt(self, **kwargs) -> None:
        return _emit_frontier_scrub_receipt_impl(self, **kwargs)

    def _record_frontier_scrub_result(self, result, *, path: str, input_length: int) -> None:
        return _record_frontier_scrub_result_impl(
            self,
            result,
            path=path,
            input_length=input_length,
        )

    def _get_frontier_scrubber(self) -> LocalPIIScrubber:
        return _get_frontier_scrubber_impl(self)

    def _redact_for_frontier(self, text: str) -> str:
        return _redact_for_frontier_impl(self, text)

    def _scrub_frontier_payload(self, payload: Any) -> Any:
        return _scrub_frontier_payload_impl(self, payload)

    def _build_frontier_user_message(self, text: str, images: list | None = None) -> Any:
        return _build_frontier_user_message_impl(self, text, images=images)

    def _build_frontier_tool_response_message(
        self,
        tool_results: list[tuple[str, str, str]],
    ) -> Any:
        return _build_frontier_tool_response_message_impl(self, tool_results)

    def _provider_generate(
        self,
        *,
        model: str,
        messages: list,
        system_instruction: str = "",
        config: Optional[dict] = None,
    ):
        return _provider_generate_impl(
            self,
            model=model,
            messages=messages,
            system_instruction=system_instruction,
            config=config,
        )

    def _provider_generate_with_tools(
        self,
        *,
        model: str,
        messages: list,
        system_instruction: str,
        tools: list,
        tool_config: Optional[dict] = None,
        config: Optional[dict] = None,
    ):
        return _provider_generate_with_tools_impl(
            self,
            model=model,
            messages=messages,
            system_instruction=system_instruction,
            tools=tools,
            tool_config=tool_config,
            config=config,
        )

    def _init_governance(self):
        return _init_governance_impl(self)

    def _seed_trust_records(self):
        return _seed_trust_records_impl(self)

    def _init_fix_pack_v1(self):
        """Initialize execution authority, tasking, and response assembler."""
        self.task_store = None
        self.token_store = None
        self.minter = None
        self.plan_compiler = None
        self.task_runner = None
        self.assembler = None
        self._last_plan_artifact = None

        try:
            if TaskStore is None:
                _gov_logger.info("Execution authority imports not available; skipping init.")
                return

            from feature_flags import (
                FEATURE_EXECUTION_TOKENS,
                FEATURE_TASK_GRAPH_EXECUTION,
                FEATURE_RESPONSE_ASSEMBLER,
            )

            db_dir = Path(self.data_dir)

            if FEATURE_TASK_GRAPH_EXECUTION:
                self.task_store = TaskStore(db_dir / "tasks.db")
                self.plan_compiler = PlanCompiler()
                _gov_logger.info("TaskStore + PlanCompiler initialized.")

            if FEATURE_EXECUTION_TOKENS:
                self.token_store = ExecutionTokenStore(db_dir / "tokens.db")
                self.minter = PermissionMinter(
                    store=self.token_store,
                    receipt_service=self.receipt_service,
                )
                _gov_logger.info(
                    "ExecutionTokenStore + PermissionMinter initialized."
                )

            if FEATURE_TASK_GRAPH_EXECUTION and self.task_store:
                self.task_runner = TaskRunner(
                    task_store=self.task_store,
                    token_store=self.token_store,
                    minter=self.minter,
                    receipt_service=self.receipt_service,
                    skill_executor=self.skill_executor,
                    verifier=self.verifier,
                    connector_runtime=getattr(self, "connector_runtime", None),
                )
                _gov_logger.info("TaskRunner initialized.")

            if FEATURE_RESPONSE_ASSEMBLER:
                _gov_logger.info("FEATURE_RESPONSE_ASSEMBLER flag active.")

        except Exception as e:
            _gov_logger.warning("Execution authority init error (non-fatal): %s", e)

        # Always initialize assembler; output hygiene is mandatory.
        try:
            self.assembler = ResponseAssembler()
            _gov_logger.info("ResponseAssembler initialized (always-on).")
        except Exception as e:
            _gov_logger.warning("ResponseAssembler init failed (non-fatal): %s", e)
            self.assembler = None

    def _is_proceed_message(self, message: str) -> bool:
        return _is_proceed_message_impl(self, message)

    def _handle_proceed(self, user_message: str, session_id: str = "") -> str:
        return _handle_proceed_impl(self, user_message, session_id=session_id)

    def _request_permission(self, graph: TaskGraph) -> str:
        return _request_permission_impl(self, graph)

    def _handle_approval(self, session_id: str = "") -> str:
        return _handle_approval_impl(self, session_id=session_id)

    def _enrich_plan_with_llm(self, artifact, user_text: str):
        """Use Gemini to replace generic plan steps with domain-specific ones.

        Called after the deterministic plan_builder produces a template artifact.
        Sends the user's original request to Gemini to generate concrete,
        actionable plan steps specific to the domain.

        When agentic loop is enabled, Gemini can research
        (via network_client) before generating plan steps.

        Falls back to the original template steps if Gemini fails.
        """
        if not self.provider:
            return artifact

        self_awareness = self._build_self_awareness()

        prompt = (
            f"The user asked: \"{user_text}\"\n\n"
            f"Your goal: {artifact.goal}\n\n"
            f"{self_awareness}\n\n"
            "INSTRUCTIONS:\n"
            "1. FIRST: Use your network_client tool to research relevant APIs, docs, and endpoints. "
            "For example, call network_client with method=GET to fetch API documentation pages. "
            "Do this BEFORE generating any plan steps.\n"
            "2. AFTER you have research results, generate 4-6 specific, actionable plan steps.\n"
            "3. Ground the plan in YOUR real capabilities and the research results.\n"
            "4. You already communicate via Telegram with text and voice notes.\n"
            "5. If the user says 'us' or 'we', that includes you.\n"
            "6. Don't suggest downloading third-party apps when your existing capabilities cover the need.\n\n"
            "Your final text response must be ONLY a numbered list of steps (1. ... 2. ... etc).\n"
        )

        sys_instruction = (
            f"You are Lancelot's planning module. {self_awareness} "
            "You MUST use your tools to research before generating plan steps. "
            "Call network_client to fetch real API docs and data. "
            "Your final response should be only numbered steps."
        )

        try:
            from feature_flags import FEATURE_AGENTIC_LOOP
            if FEATURE_AGENTIC_LOOP:
                _gov_logger.info("Enriching plan with forced tool research")
                raw = self._agentic_generate(
                    prompt=prompt,
                    system_instruction=sys_instruction,
                    allow_writes=False,
                    force_tool_use=True,
                    skip_structured_reformat=True,
                )
            else:
                msg = self._build_frontier_user_message(
                    f"{self.context_env.get_context_string()}\n\n{prompt}"
                )
                result = self._llm_call_with_retry(
                    lambda: self._provider_generate(
                        model=self.model_name,
                        messages=[msg],
                        system_instruction=sys_instruction,
                    )
                )
                raw = result.text.strip() if result.text else ""

            # Parse numbered steps
            steps = re.findall(r"^\d+\.\s*(.+)$", raw, re.MULTILINE)
            if steps and len(steps) >= 3:
                artifact.plan_steps = steps
                artifact.next_action = steps[0]
                _gov_logger.info("Plan enriched with %d LLM-generated steps", len(steps))
        except Exception as e:
            _gov_logger.warning("Plan enrichment failed, using template: %s", e)

        return artifact

    def _execute_with_llm(self, graph, user_text: str = "") -> str:
        return _execute_with_llm_impl(self, graph, user_text=user_text)

    def _summarize_execution_results(self, graph, run_result) -> str:
        """Summarize real skill execution results using Gemini.

        Takes a TaskGraph and TaskRunResult, formats the real step outputs,
        and sends to Gemini for a concise user-facing summary.
        """
        if not self.provider:
            return ""

        # Format real step outputs
        results_text = []
        for sr in run_result.step_results:
            step_label = sr.step_id
            # Find matching step in graph for a readable label
            for s in graph.steps:
                if s.step_id == sr.step_id:
                    step_label = s.inputs.get("description", s.type)
                    break
            if sr.success:
                results_text.append(f"- {step_label}: SUCCESS — {sr.outputs}")
            else:
                results_text.append(f"- {step_label}: FAILED — {sr.error}")

        results_block = "\n".join(results_text)

        prompt = (
            f"Goal: {graph.goal}\n\n"
            f"Execution results:\n{results_block}\n\n"
            "Summarize what was accomplished for the user. "
            "Be direct and concise. Report real outcomes only. "
            "If steps failed, explain what went wrong and suggest fixes."
        )

        try:
            system_instruction = self._build_execution_instruction()
            msg = self._build_frontier_user_message(
                f"{self.context_env.get_context_string()}\n\n{prompt}"
            )
            gen_result = self._llm_call_with_retry(
                lambda: self._provider_generate(
                    model=self._route_model(graph.goal or ""),
                    messages=[msg],
                    system_instruction=system_instruction,
                    config={"thinking": self._get_thinking_config()},
                )
            )
            from response.policies import OutputPolicy
            return OutputPolicy.strip_tool_scaffolding(gen_result.text)
        except Exception as e:
            _gov_logger.warning("Result summarization failed: %s", e)
            # Fallback: return raw results
            return f"**Execution Complete**\n\n{results_block}"

    def _load_memory(self):
        return _load_memory_impl(self)

    def _init_provider(self):
        return _init_provider_impl(self)

    def switch_provider(self, provider_name: str) -> str:
        return _switch_provider_impl(self, provider_name)

    def _get_anthropic_oauth_token(self) -> str:
        return _get_anthropic_oauth_token_impl()

    def _get_openai_codex_oauth_token(self) -> str:
        return _get_openai_codex_oauth_token_impl()

    def _has_openai_codex_cli_auth(self) -> bool:
        return _has_openai_codex_cli_auth_impl()

    def set_lane_model(self, lane: str, model_id: str) -> None:
        return _set_lane_model_impl(self, lane, model_id)

    def _build_system_instruction(self, crusader_mode=False):
        return _build_system_instruction_impl(self, crusader_mode=crusader_mode)

    def _build_execution_instruction(self) -> str:
        return _build_execution_instruction_impl(self)

    def _build_self_awareness(self) -> str:
        return _build_self_awareness_impl()

    # ── Agentic Loop (Provider Function Calling) ───────────────────────

    def _build_tool_declarations(self):
        return _build_tool_declarations_impl(self)

    def _classify_tool_call_safety(self, skill_name: str, inputs: dict) -> str:
        """Classify tool-call safety via the shared safety helper."""
        return _classify_tool_call_safety_fn(
            skill_name,
            inputs,
            channel=getattr(self, "_current_channel", "api"),
        )

    # ------------------------------------------------------------------
    # Local agentic routing
    # ------------------------------------------------------------------

    def _build_openai_tool_declarations(self):
        return _build_openai_tool_declarations_impl(self)

    def _is_simple_for_local(self, prompt: str) -> bool:
        return _is_simple_for_local_impl(self, prompt)

    def _needs_research(self, prompt: str) -> bool:
        return _needs_research_impl(prompt)

    def _wants_action(self, prompt: str) -> bool:
        return _wants_action_impl(prompt)

    def _is_low_risk_exec(self, prompt: str) -> bool:
        return _is_low_risk_exec_impl(prompt)

    # Map simple execution requests directly to one skill when intent is unambiguous.
    _SIMPLE_ACTION_MAP = _SIMPLE_ACTION_MAP_IMPL

    def _build_simple_action_plan(self, user_message: str):
        return _build_simple_action_plan_impl(user_message, self._SIMPLE_ACTION_MAP)

    def _extract_literal_terms(self, text: str) -> list:
        return _extract_literal_terms_impl(text)

    def _is_conversational(self, prompt: str) -> bool:
        return _is_conversational_impl(prompt)

    def _check_name_update(self, message: str):
        return _check_name_update_impl(self, message)

    def _previous_was_substantive(self) -> bool:
        return _previous_was_substantive_impl(self)

    def _is_continuation(self, message: str) -> bool:
        return _is_continuation_impl(message)

    def _verify_intent_with_llm(self, user_message: str, keyword_intent: "IntentType") -> "IntentType":
        return _verify_intent_with_llm_impl(self, user_message, keyword_intent)

    def _local_agentic_generate(
        self,
        prompt: str,
        system_instruction: str = None,
        allow_writes: bool = False,
        context_str: str = None,
    ) -> str:
        return _local_agentic_generate_impl(
            self,
            prompt,
            system_instruction=system_instruction,
            allow_writes=allow_writes,
            context_str=context_str,
        )

    @staticmethod
    def _is_retryable_error(exc: Exception) -> bool:
        return _is_retryable_error_impl(exc)

    def _llm_call_with_retry(self, call_fn, max_retries=3, base_delay=1.0):
        return _llm_call_with_retry_impl(
            self,
            call_fn,
            max_retries=max_retries,
            base_delay=base_delay,
        )

    def _agentic_generate(
        self,
        prompt: str,
        system_instruction: str = None,
        allow_writes: bool = False,
        context_str: str = None,
        force_tool_use: bool = False,
        image_parts: list = None,
        skip_structured_reformat: bool = False,
    ) -> str:
        return _agentic_generate_impl(
            self,
            prompt,
            system_instruction=system_instruction,
            allow_writes=allow_writes,
            context_str=context_str,
            force_tool_use=force_tool_use,
            image_parts=image_parts,
            skip_structured_reformat=skip_structured_reformat,
        )

    def _format_tool_receipts(self, receipts: list, error: str = "", note: str = "") -> str:
        """Format tool receipts via the shared response helper."""
        return _format_tool_receipts_fn(receipts, error, note)

    def _text_only_generate(
        self,
        prompt: str,
        system_instruction: str = None,
        context_str: str = None,
        image_parts: list = None,
    ) -> str:
        return _text_only_generate_impl(
            self,
            prompt,
            system_instruction=system_instruction,
            context_str=context_str,
            image_parts=image_parts,
        )

    # ── End Agentic Loop ─────────────────────────────────────────────

    def _get_thinking_config(self):
        return _get_thinking_config_impl()

    # Deep reasoning lane

    def _should_use_deep_reasoning(self, user_message: str) -> bool:
        return _should_use_deep_reasoning_impl(self, user_message)

    def _build_reasoning_instruction(self) -> str:
        return _build_reasoning_instruction_impl(self)

    def _deep_reasoning_pass(
        self,
        user_message: str,
        past_experiences: str = "",
    ):
        return _deep_reasoning_pass_impl(
            self,
            user_message,
            past_experiences=past_experiences,
        )

    def _retrieve_task_experiences(self, user_message: str, limit: int = 3) -> str:
        """Retrieve relevant past task experiences from episodic memory.

        Returns formatted string of past experiences, or empty string.
        Non-fatal on failure.
        """
        try:
            _mem_mgr = getattr(self, '_memory_store_manager', None)
            if _mem_mgr is None:
                from memory.sqlite_store import MemoryStoreManager
                self._memory_store_manager = MemoryStoreManager(
                    data_dir=getattr(self, 'data_dir', '/home/lancelot/data')
                )
                _mem_mgr = self._memory_store_manager

            results = _mem_mgr.episodic.search(
                query=user_message[:200],
                namespace="task_experience",
                limit=limit,
            )

            if not results:
                return ""

            lines = ["Past similar tasks:"]
            for item in results:
                lines.append(f"- {item.content}")

            _gov_logger.debug(
                "task_experiences_retrieved",
                extra={"result_count": len(results)},
            )
            return "\n".join(lines)

        except Exception as e:
            _gov_logger.warning(
                "task_experience_retrieval_failed",
                extra={"error": str(e)},
            )
            return ""

    def _record_task_experience(
        self,
        user_message: str,
        response_text: str,
        tool_receipts: list,
        reasoning_artifact=None,
        duration_ms: float = 0.0,
    ) -> None:
        return _record_task_experience_impl(
            self,
            user_message,
            response_text,
            tool_receipts,
            reasoning_artifact=reasoning_artifact,
            duration_ms=duration_ms,
        )

    def _get_trust_summary(self, skill_name: str, inputs: dict) -> str:
        return _get_trust_summary_impl(self, skill_name, inputs)

    def _suggest_alternatives(self, skill_name: str, inputs: dict) -> list:
        return _suggest_alternatives_impl(skill_name, inputs)

    # ── End Autonomy Loop v2 ─────────────────────────────────────────

    def _init_context_cache(self):
        return _init_context_cache_impl(self)

    def _validate_command(self, command: str) -> tuple:
        """Validates a command against whitelist and blacklist.

        Returns:
            (True, "") if valid, (False, reason) if rejected.
        """
        # Check for shell metacharacters
        for char in COMMAND_BLACKLIST_CHARS:
            if char in command:
                return (False, f"Blocked shell metacharacter: '{char}'")

        # Parse with shlex for proper quoting
        try:
            parts = shlex.split(command)
        except ValueError as e:
            return (False, f"Invalid command syntax: {e}")

        if not parts:
            return (False, "Empty command")

        # Check binary against whitelist
        binary = os.path.basename(parts[0])
        if binary not in COMMAND_WHITELIST:
            return (False, f"Command '{binary}' is not in the allowed commands list")

        # Check all args for URL-like patterns (SSRF prevention)
        for arg in parts[1:]:
            if arg.startswith("http://") or arg.startswith("https://"):
                if not self.network_interceptor.check_url(arg):
                    return (False, f"Blocked URL in command arguments: {arg}")

        return (True, "")

    def execute_command(self, command: str, parent_id: Optional[str] = None) -> str:
        """Executes a shell command via subprocess (Safe Wrapper) with Receipt."""
        # Create Receipt
        receipt = create_receipt(
            ActionType.TOOL_CALL,
            "execute_command",
            {"command": command},
            tier=CognitionTier.DETERMINISTIC,
            parent_id=parent_id,
            quest_id=getattr(self, '_current_quest_id', None),
        )
        self.receipt_service.create(receipt)
        start_time = __import__("time").time()

        # Governance: Check Tool Limit
        if not self.governor.check_limit("tool_calls", 1):
             self.receipt_service.update(receipt.fail("Governance Block", 0))
             return "GOVERNANCE BLOCK: Daily tool call limit exceeded."
        self.governor.log_usage("tool_calls", 1)

        _gov_logger.info("Executing command via CLI: %s", command)

        # S3: Always check for shell metacharacters first (prevents chaining bypass)
        for char in COMMAND_BLACKLIST_CHARS:
            if char in command:
                duration = int((__import__("time").time() - start_time) * 1000)
                self.receipt_service.update(receipt.fail(f"Blocked shell metacharacter: '{char}'", duration))
                return f"SECURITY BLOCK: Blocked shell metacharacter: '{char}'"

        try:
            parts = shlex.split(command)
        except ValueError:
            return "Error parsing command."

        base_cmd = parts[0].lower() if parts else ""
        SAFEREPL_COMMANDS = {"ls", "dir", "cat", "read", "type", "grep", "search", "outline", "diff", "cp", "mv", "rm", "mkdir", "touch", "sleep", "wake"}

        if base_cmd not in SAFEREPL_COMMANDS:
            valid, reason = self._validate_command(command)
            if not valid:
                duration = int((__import__("time").time() - start_time) * 1000)
                self.receipt_service.update(receipt.fail(f"Validation failed: {reason}", duration))
                return f"SECURITY BLOCK: {reason}"

        try:
            output = self._execute_command(parts)
            duration = int((__import__("time").time() - start_time) * 1000)
            self.receipt_service.update(receipt.complete({"output": output}, duration))
            return output
        except Exception as e:
            duration = int((__import__("time").time() - start_time) * 1000)
            self.receipt_service.update(receipt.fail(str(e), duration))
            raise

    def query_memory(self, query_text: str, n_results: int = 3) -> str:
        return _query_memory_impl(self, query_text, n_results=n_results)

    def _validate_rule_content(self, content: str) -> tuple:
        """Validate rule content via the shared safety helper."""
        return _validate_rule_content_fn(content)

    def _log_rule_candidate(self, content: str):
        return _log_rule_candidate_impl(self, content)

    def _update_rules(self, new_knowledge: str):
        return _update_rules_impl(self, new_knowledge)



    def _strip_failure_narration(self, text: str) -> str:
        """Strip model narration about failed tool work via the safety helper."""
        return _strip_failure_narration_fn(text)

    def _is_narration_without_content(self, text: str) -> bool:
        """Detect model narration that never delivers user-facing content."""
        return _is_narration_without_content_fn(text)

    def _force_synthesis(self, messages: list, last_raw, system_instruction: str, prompt: str) -> str:
        return _force_synthesis_impl(self, messages, last_raw, system_instruction, prompt)

    def _deliver_war_room_artifacts(self, artifacts: list) -> list:
        return _deliver_war_room_artifacts_impl(self, artifacts)

    def _auto_create_document(self, content: str, title: str = "Research Report") -> str:
        return _auto_create_document_impl(self, content, title=title)

    @staticmethod
    def _append_download_links(response: str, doc_paths: list) -> str:
        return _append_download_links_impl(response, doc_paths)

    def _validate_llm_response(self, response_text: str) -> str:
        return _validate_llm_response_impl(self, response_text)

    def _create_plan(self, goal: str):
        """Internal helper to create a plan object."""
        self.wake_up("Planner")
        context_str = self.context_env.get_context_string()
        return self.planner.create_plan(goal, context_str)

    def plan_task(self, goal: str) -> str:
        """S15: Generates a structured plan for a goal and returns display string."""
        plan = self._create_plan(goal)
        if not plan:
            return "Failed to generate plan."
            
        # Format plan for display — human-readable only, no tool/param internals
        output = [f"Plan for: {plan.goal}"]
        for step in plan.steps:
            output.append(f"{step.id}. {step.description}")
            
        return "\n".join(output)

    def run_autonomous_mission(self, goal: str) -> str:
        """Generate and execute a plan autonomously."""
        _gov_logger.info(
            "autonomous_mission_started",
            extra={"goal": goal},
        )
        plan = self._create_plan(goal)
        if not plan:
            return "Mission Aborted: Planning Failed."
            
        return self.execute_plan(plan)

    def _execute_step_tool(self, step, params) -> str:
        """Execute a single plan step's tool and return output string."""
        if step.tool == "read_file":
            path = params.get("path")
            content = self.context_env.read_file(path)
            return f"Read file {path}. Content length: {len(content) if content else 0}"
        elif step.tool == "list_workspace":
            d = params.get("dir", ".")
            return self.context_env.list_workspace(d)
        elif step.tool == "search_workspace":
            q = params.get("query")
            return str(self.context_env.search_workspace(q))
        elif step.tool == "execute_command":
            cmd = params.get("command")
            return self.execute_command(cmd)
        elif step.tool == "write_to_file":
            p = params.get("path")
            c = params.get("content")
            success = self.file_ops.write_file(p, c, f"Plan Step {step.id}")
            return f"Write to {p}: {'Success' if success else 'Failed'}"
        else:
            return f"Unknown tool: {step.tool}"

    def _request_approval(self, step, profile) -> bool:
        """Request Commander approval for T3 actions.

        Override in tests or inject an approval_fn for custom behavior.
        Production: creates a pending approval in the MCP Sentry queue
        visible from the War Room Governance Dashboard.  Returns False
        so the plan pauses — the Commander can approve via the War Room
        and re-issue the command.
        """
        if hasattr(self, '_approval_fn') and self._approval_fn is not None:
            return self._approval_fn(step, profile)

        # Create a pending approval in the MCP Sentry so it appears
        # in /api/governance/approvals and the War Room dashboard.
        capability = getattr(step, 'tool', 'unknown')
        params = {
            "step_id": getattr(step, 'id', 'unknown'),
            "description": getattr(step, 'description', ''),
            "tool": capability,
        }

        if hasattr(self, 'sentry') and self.sentry is not None:
            from mcp_sentry import MCPSentry
            if isinstance(self.sentry, MCPSentry):
                perm = self.sentry.check_permission(capability, params)
                if perm["status"] == "APPROVED":
                    _gov_logger.info("T3 action pre-approved by sentry: %s", capability)
                    return True
                _gov_logger.warning(
                    "T3 action requires approval: %s (request_id=%s) — visible in War Room",
                    capability, perm.get("request_id", "?"),
                )
                return False

        _gov_logger.warning("T3 action requires approval: %s (auto-denied, no sentry)", step.tool)
        return False

    def execute_plan(self, plan) -> str:
        return _execute_plan_impl(self, plan)

    def _record_governance_event(self, capability: str, scope: str, tier, success: bool):
        return _record_governance_event_impl(self, capability, scope, tier, success)

    def _get_deep_model(self) -> str:
        """Return the deep/reasoning model name with graceful fallback.

        Checks the provider profile first, then environment configuration,
        and finally falls back to the fast lane. The chosen model is validated
        before first use.
        """
        return _get_deep_model_impl(self)

    def _route_model(self, user_message: str) -> str:
        """Smart model routing: selects the best model for the task.

        Routes to deep model (e.g. gemini-2.5-pro) for complex reasoning tasks,
        and fast model (Flash) for everything else. This ensures Lancelot never
        'feels dumb' on hard questions while staying cost-efficient on simple ones.
        """
        return _route_model_impl(self, user_message)

    def chat(
        self,
        user_message: str,
        crusader_mode: bool = False,
        attachments: list = None,
        channel: str = "api",
        session_id: str = "",
        operator_id: str = "",
        operator_name: str = "",
        quest_id: Optional[str] = None,
    ) -> str:
        _chat_flow_module.create_receipt = create_receipt
        _chat_flow_module.create_finalized_receipt = create_finalized_receipt
        return _chat_impl(
            self,
            user_message,
            crusader_mode=crusader_mode,
            attachments=attachments,
            channel=channel,
            session_id=session_id,
            operator_id=operator_id,
            operator_name=operator_name,
            quest_id=quest_id,
        )

    def _parse_response(self, response_text: str) -> str:
        """Parses the LLM response for confidence score and routes accordingly.

        Honest Closure policy: Never prefix output with "DRAFT:" or other
        planner-internal markers. Governor blocks simulated work language.
        """
        import re
        from response_governor import detect_forbidden_async_language

        # Look for a confidence score pattern like "Confidence: 85" or "[95]"
        match = re.search(r'(?:Confidence[:\s]*|^\[)(\d{1,3})(?:\]|%)?', response_text, re.IGNORECASE)

        if match:
            # S9: Clamp confidence to 0-100
            confidence = min(max(int(match.group(1)), 0), 100)
            # Strip the confidence tag from the displayed response
            # Handles "Confidence: 85", "[95]", and bare "95 Action:" formats
            clean_response = re.sub(r'(?:Confidence[:\s]*\d{1,3}%?\s*)', '', response_text, flags=re.IGNORECASE)
            clean_response = re.sub(r'^\[?\d{1,3}\]?\s*', '', clean_response).strip()

            if confidence > 90:
                # S9: Log candidate instead of auto-writing to RULES.md
                if clean_response.startswith("Action:"):
                    action_text = clean_response[len("Action:"):].strip()
                    self._log_rule_candidate(f"- [Learned Rule] (Confidence {confidence}%): {action_text}")
                return self._apply_honesty_gate(clean_response)
            elif confidence >= 70:
                # Medium confidence: return clean response (no DRAFT: prefix)
                return self._apply_honesty_gate(clean_response)
            else:
                # Low confidence: request permission
                return f"PERMISSION REQUIRED (Confidence {confidence}%): {clean_response}"

        # No confidence score found, return as-is
        return self._apply_honesty_gate(response_text)

    def _apply_honesty_gate(self, text: str) -> str:
        """Apply Honest Closure gates: strip leakage markers, block simulated work.

        Three-tier enforcement:
        1. Strip planner leakage markers (DRAFT:, PLANNER:, etc.)
        2. Check for structural fake work proposals — replace entire response
        3. Check individual forbidden phrases — replace if >= 2, strip if 1

        When agentic loop has tool receipts, research/execution
        phrases are allowed because they describe real tool-backed work.
        """
        import re
        from response_governor import (
            detect_forbidden_async_language,
            detect_fake_work_proposal,
            filter_forbidden_for_agentic_context,
        )

        # Only real tool receipts grant trust.
        # is_agentic_context=True must not be treated the same as
        # has_tool_receipts=True, letting stalling language through even when
        # no tools were called. Now only actual tool calls earn trust.
        has_tool_receipts = False
        try:
            if self.skill_executor:
                has_tool_receipts = len(self.skill_executor.receipts) > 0
        except Exception as exc:
            _logging.warning("Failed to inspect skill executor receipts during response cleanup: %s", exc)

        # Tier 1: Strip planner leakage markers
        cleaned = re.sub(r'^DRAFT:\s*', '', text, flags=re.IGNORECASE).strip()
        for marker in ["PLANNER:", "[INTERNAL]", "[SCRATCHPAD]", "PLANNING_INTERNAL"]:
            cleaned = cleaned.replace(marker, "").strip()

        # Tier 2: Check for structural fake work proposal (highest priority)
        # Only skip fake work detection when tools were ACTUALLY called.
        if not has_tool_receipts:
            fake_work_reason = detect_fake_work_proposal(cleaned)
            if fake_work_reason:
                return self._generate_honest_replacement(cleaned, fake_work_reason)

        # Tier 2b: Action Language Gate — block execution claims
        #   without a real TaskRun + receipt
        if check_action_language is not None:
            active_run = None
            if self.task_store:
                active_run = self.task_store.get_active_run()
            gate_result = check_action_language(
                cleaned, task_run=active_run, has_tool_receipts=has_tool_receipts,
            )
            if not gate_result.passed:
                cleaned = gate_result.corrected_text

        # Tier 3: Check for individual forbidden phrases
        violations = detect_forbidden_async_language(cleaned)
        # Only filter out phrases when tools were ACTUALLY called.
        # is_agentic_context must not be treated as has_tool_receipts.
        violations = filter_forbidden_for_agentic_context(
            violations, has_tool_receipts=has_tool_receipts
        )
        if violations:
            # 2+ violations = systemic stalling — replace entire response
            if len(violations) >= 2:
                return self._generate_honest_replacement(
                    cleaned,
                    f"Multiple stalling phrases: {', '.join(violations[:3])}",
                )
            # Single violation: strip it but keep the rest
            for v in violations:
                cleaned = re.sub(re.escape(v), '', cleaned, flags=re.IGNORECASE).strip()
            cleaned = re.sub(r'\s{2,}', ' ', cleaned).strip()

        return cleaned

    def _generate_honest_replacement(self, original_text: str, reason: str) -> str:
        """Generate an honest replacement response via the shared safety helper."""
        return _generate_honest_replacement_fn(original_text, reason)

    def set_state(self, new_state: RuntimeState):
        """Updates the runtime state with audit logging."""
        if self.state != new_state:
            self.audit_logger.log_event("STATE_CHANGE", f"Transitioned from {self.state.value} to {new_state.value}")
            self.state = new_state

    def enter_sleep(self):
        """Transitions agent to low-power SLEEP mode."""
        if self.state == RuntimeState.SLEEPING:
            return

        _gov_logger.info("Lancelot entering SLEEP mode...")
        # 1. Flush Context (keep only essential history)
        # self.context_env.clear_heavy_context() # Future optimization
        
        # 2. Log Event
        self.set_state(RuntimeState.SLEEPING)

    def wake_up(self, reason: str = "Manual Trigger"):
        """Transitions agent to ACTIVE mode."""
        if self.state == RuntimeState.ACTIVE:
            return

        _gov_logger.info("Lancelot WAKING UP (%s)...", reason)
        self.set_state(RuntimeState.ACTIVE)
        # Refresh context or checks could go here

    def _execute_command(self, command_parts: list) -> str:
        return _execute_command_impl(self, command_parts)
