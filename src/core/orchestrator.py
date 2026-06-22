"""Runtime orchestrator facade for model routing, governance, and tool execution."""

# Lancelot - A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

import os
import subprocess
import shlex
import uuid
from enum import Enum
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
)
from orch_helpers.response_helpers import (
    format_tool_receipts as _format_tool_receipts_fn,
)
from orchestrator_consts import COMMAND_BLACKLIST_CHARS, COMMAND_WHITELIST
from orchestrator_ext import (
    _build_openai_tool_declarations as _build_openai_tool_declarations_impl,
    _build_system_instruction as _build_system_instruction_impl,
    _build_tool_declarations as _build_tool_declarations_impl,
    _deep_reasoning_pass as _deep_reasoning_pass_impl,
    _handle_proceed as _handle_proceed_impl,
    _init_provider as _init_provider_impl,
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
    get_openai_codex_oauth_token as _get_openai_codex_oauth_token_impl,
    has_openai_codex_cli_auth as _has_openai_codex_cli_auth_impl,
    set_lane_model as _set_lane_model_impl,
    switch_provider as _switch_provider_impl,
)
from orchestrator_planning import (
    enrich_plan_with_llm as _enrich_plan_with_llm_impl,
    summarize_execution_results as _summarize_execution_results_impl,
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
from orchestrator_approval import (
    handle_approval as _handle_approval_impl,
    is_proceed_message as _is_proceed_message_impl,
    request_permission as _request_permission_impl,
)
from orchestrator_context import load_memory as _load_memory_impl
from tool_loop import (
    _agentic_generate as _agentic_generate_impl,
    _execute_with_llm as _execute_with_llm_impl,
    _local_agentic_generate as _local_agentic_generate_impl,
)
from src.core.frontier_scrubber import (
    LocalPIIScrubber,
    detect_frontier_pii_categories as _detect_frontier_pii_categories_fn,
    normalize_frontier_pii_text as _normalize_frontier_pii_text_fn,
    validate_frontier_redaction as _validate_frontier_redaction_fn,
)
from src.core.orchestrator_execution_init import init_execution_authority as _init_execution_authority_impl
from orchestrator_runtime_flow import OrchestratorRuntimeFlowMixin

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

# Action-language gate imports
try:
    from action_language_gate import check_action_language
    from tasking.schema import TaskGraph
except ImportError:
    try:
        from src.core.action_language_gate import check_action_language
        from src.core.tasking.schema import TaskGraph
    except ImportError as e:
        _gov_logger.warning("Action-language gate imports unavailable: %s", e)
        check_action_language = None

class RuntimeState(Enum):
    ACTIVE = "active"
    SLEEPING = "sleeping"
    BUSY = "busy"

class LancelotOrchestrator(OrchestratorRuntimeFlowMixin):
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
        self._deep_model_validation_cache: dict[str, bool] = {}

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
        self.procedural_recommendation_store = None

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

    def set_memory_enabled(self, enabled: bool) -> None:
        self._memory_enabled = bool(enabled)

    def is_memory_enabled(self) -> bool:
        return bool(self._memory_enabled)

    def refresh_soul_policy(self, active_soul) -> None:
        self.soul = active_soul
        risk_classifier = getattr(self, "_risk_classifier", None)
        if risk_classifier is not None:
            risk_classifier.update_soul(active_soul)

    def attach_connector_registry(self, registry) -> None:
        self._connector_registry = registry

    @property
    def active_provider_name(self) -> str:
        return getattr(self, "_provider_name", "")

    def set_provider_runtime(self, provider, *, provider_name: str, provider_mode: str) -> None:
        self.provider = provider
        self._provider_name = provider_name
        self._provider_mode = provider_mode

    def provider_stop_event(self):
        return getattr(self, "_stop_event", None)

    def set_provider_lane_configuration(
        self,
        *,
        fast_model: str | None = None,
        deep_model: str | None = None,
        cache_model: str | None = None,
        deep_thinking_config=None,
    ) -> None:
        if fast_model:
            self.model_name = fast_model
        if deep_model:
            self._deep_model_name = deep_model
        if cache_model:
            self._cache_model = cache_model
        if deep_thinking_config is not None:
            self._deep_thinking_config = deep_thinking_config

    def set_model_lane(self, lane: str, model_id: str) -> None:
        if lane == "fast":
            self.model_name = model_id
        elif lane == "deep":
            self._deep_model_name = model_id
            self.invalidate_deep_model_validation_cache()
        elif lane == "cache":
            self._cache_model = model_id
            self.clear_context_cache()
        else:
            raise ValueError(f"Unknown lane: {lane}")

    def deep_model_name(self) -> str:
        return getattr(self, "_deep_model_name", "")

    def cached_deep_model_validation(self, model_id: str) -> bool | None:
        return self._deep_model_validation_cache.get(model_id)

    def record_deep_model_validation(self, model_id: str, valid: bool) -> None:
        self._deep_model_validation_cache[model_id] = bool(valid)

    def invalidate_deep_model_validation_cache(self) -> None:
        self._deep_model_validation_cache.clear()
        for attr in list(vars(self)):
            if attr.startswith("_deep_model_valid_"):
                delattr(self, attr)

    def clear_context_cache(self) -> None:
        self._cache = None

    def set_context_cache(self, cache) -> None:
        self._cache = cache

    def context_cache_name(self) -> str | None:
        return getattr(self._cache, "name", None)

    def context_cache_model(self) -> str:
        return self._cache_model

    def context_cache_ttl_seconds(self) -> int:
        return self._cache_ttl

    def create_context_cache(
        self,
        *,
        contents: str,
        system_instruction: str,
        display_name: str,
    ):
        if not self.provider:
            raise RuntimeError("Context cache cannot be created before provider initialization")
        return self.provider.create_context_cache(
            model=self.context_cache_model(),
            contents=contents,
            system_instruction=system_instruction,
            ttl_s=self.context_cache_ttl_seconds(),
            display_name=display_name,
        )

    def set_last_tool_receipts(self, receipts: list[dict[str, Any]]) -> None:
        self._last_tool_receipts = receipts

    def set_last_plan_artifact(self, artifact) -> None:
        self._last_plan_artifact = artifact

    def verify_async_job(self, job) -> bool:
        return self._verify_async_job(job)

    def set_governance_runtime(
        self,
        *,
        risk_classifier=None,
        async_queue=None,
        rollback_manager=None,
        template_registry=None,
    ) -> None:
        self._risk_classifier = risk_classifier
        self._async_queue = async_queue
        self._rollback_manager = rollback_manager
        self._template_registry = template_registry

    def _current_model_usage_status(self) -> dict:
        """Return the persisted local-model usage policy + runtime status."""
        from src.core.model_usage_policy import get_model_usage_status

        return get_model_usage_status()

    def current_model_usage_status(self) -> dict:
        return self._current_model_usage_status()

    def mark_telegram_delivery_handled(self) -> None:
        self._telegram_already_sent = True

    def was_telegram_delivery_handled(self) -> bool:
        return bool(getattr(self, "_telegram_already_sent", False))

    def clear_telegram_delivery_handled(self) -> None:
        self._telegram_already_sent = False

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

    def emit_chat_progress(self, phase: str, message: str, **metadata: Any) -> None:
        return self._emit_chat_progress(phase, message, **metadata)

    def _emit_frontier_scrub_receipt(self, **kwargs) -> None:
        return _emit_frontier_scrub_receipt_impl(self, **kwargs)

    def emit_frontier_scrub_receipt(self, **kwargs) -> None:
        return self._emit_frontier_scrub_receipt(**kwargs)

    def _record_frontier_scrub_result(self, result, *, path: str, input_length: int) -> None:
        return _record_frontier_scrub_result_impl(
            self,
            result,
            path=path,
            input_length=input_length,
        )

    def record_frontier_scrub_result(self, result, *, path: str, input_length: int) -> None:
        return self._record_frontier_scrub_result(
            result,
            path=path,
            input_length=input_length,
        )

    def _get_frontier_scrubber(self) -> LocalPIIScrubber:
        return _get_frontier_scrubber_impl(self)

    def get_frontier_scrubber(self) -> LocalPIIScrubber:
        return self._get_frontier_scrubber()

    def _redact_for_frontier(self, text: str) -> str:
        return _redact_for_frontier_impl(self, text)

    def redact_for_frontier(self, text: str) -> str:
        return self._redact_for_frontier(text)

    def _scrub_frontier_payload(self, payload: Any) -> Any:
        return _scrub_frontier_payload_impl(self, payload)

    def scrub_frontier_payload(self, payload: Any) -> Any:
        return self._scrub_frontier_payload(payload)

    def _build_frontier_user_message(self, text: str, images: list | None = None) -> Any:
        return _build_frontier_user_message_impl(self, text, images=images)

    def build_frontier_user_message(self, text: str, images: list | None = None) -> Any:
        return self._build_frontier_user_message(text, images=images)

    def _build_frontier_tool_response_message(
        self,
        tool_results: list[tuple[str, str, str]],
    ) -> Any:
        return _build_frontier_tool_response_message_impl(self, tool_results)

    def build_frontier_tool_response_message(
        self,
        tool_results: list[tuple[str, str, str]],
    ) -> Any:
        return self._build_frontier_tool_response_message(tool_results)

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

    def provider_generate(
        self,
        *,
        model: str,
        messages: list,
        system_instruction: str = "",
        config: Optional[dict] = None,
    ):
        return self._provider_generate(
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

    def provider_generate_with_tools(
        self,
        *,
        model: str,
        messages: list,
        system_instruction: str,
        tools: list,
        tool_config: Optional[dict] = None,
        config: Optional[dict] = None,
    ):
        return self._provider_generate_with_tools(
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

    def seed_trust_records(self):
        return self._seed_trust_records()

    def _init_fix_pack_v1(self):
        return _init_execution_authority_impl(self, _gov_logger)

    def _is_proceed_message(self, message: str) -> bool:
        return _is_proceed_message_impl(self, message)

    def _handle_proceed(self, user_message: str, session_id: str = "") -> str:
        return _handle_proceed_impl(self, user_message, session_id=session_id)

    def handle_proceed(self, user_message: str, session_id: str = "") -> str:
        return self._handle_proceed(user_message, session_id=session_id)

    def _request_permission(self, graph: TaskGraph) -> str:
        return _request_permission_impl(self, graph)

    def _handle_approval(self, session_id: str = "") -> str:
        return _handle_approval_impl(self, session_id=session_id)

    def _enrich_plan_with_llm(self, artifact, user_text: str):
        return _enrich_plan_with_llm_impl(self, artifact, user_text)

    def _execute_with_llm(self, graph, user_text: str = "") -> str:
        return _execute_with_llm_impl(self, graph, user_text=user_text)

    def _summarize_execution_results(self, graph, run_result) -> str:
        return _summarize_execution_results_impl(self, graph, run_result)

    def _load_memory(self):
        return _load_memory_impl(self)

    def _init_provider(self):
        return _init_provider_impl(self)

    def initialize_provider(self):
        """Initialize or refresh the active provider through the public runtime API."""
        return self._init_provider()

    def switch_provider(self, provider_name: str) -> str:
        return _switch_provider_impl(self, provider_name)

    def _get_anthropic_oauth_token(self) -> str:
        return _get_anthropic_oauth_token_impl()

    def get_anthropic_oauth_token(self) -> str:
        return self._get_anthropic_oauth_token()

    def _get_openai_codex_oauth_token(self) -> str:
        return _get_openai_codex_oauth_token_impl()

    def get_openai_codex_oauth_token(self) -> str:
        return self._get_openai_codex_oauth_token()

    def _has_openai_codex_cli_auth(self) -> bool:
        return _has_openai_codex_cli_auth_impl()

    def has_openai_codex_cli_auth(self) -> bool:
        return self._has_openai_codex_cli_auth()

    def set_lane_model(self, lane: str, model_id: str) -> None:
        return _set_lane_model_impl(self, lane, model_id)

    def _build_system_instruction(self, crusader_mode=False):
        return _build_system_instruction_impl(self, crusader_mode=crusader_mode)

    def build_system_instruction(self, crusader_mode=False):
        return self._build_system_instruction(crusader_mode=crusader_mode)

    def _build_execution_instruction(self) -> str:
        return _build_execution_instruction_impl(self)

    def _build_self_awareness(self) -> str:
        return _build_self_awareness_impl()

    # Agentic loop (provider function calling)

    def _build_tool_declarations(self):
        return _build_tool_declarations_impl(self)

    def _classify_tool_call_safety(self, skill_name: str, inputs: dict) -> str:
        """Classify tool-call safety via the shared safety helper."""
        return _classify_tool_call_safety_fn(
            skill_name,
            inputs,
            channel=getattr(self, "_current_channel", "api"),
        )

    def classify_tool_call_safety(self, skill_name: str, inputs: dict) -> str:
        return self._classify_tool_call_safety(skill_name, inputs)

    # ------------------------------------------------------------------
    # Local agentic routing
    # ------------------------------------------------------------------

    def _build_openai_tool_declarations(self):
        return _build_openai_tool_declarations_impl(self)

    def build_openai_tool_declarations(self):
        return self._build_openai_tool_declarations()

    def _is_simple_for_local(self, prompt: str) -> bool:
        return _is_simple_for_local_impl(self, prompt)

    def _needs_research(self, prompt: str) -> bool:
        return _needs_research_impl(prompt)

    def needs_research(self, prompt: str) -> bool:
        return self._needs_research(prompt)

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

    def is_continuation(self, message: str) -> bool:
        return self._is_continuation(message)

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
    def is_retryable_error(exc: Exception) -> bool:
        return _is_retryable_error_impl(exc)

    _is_retryable_error = is_retryable_error

    def _llm_call_with_retry(self, call_fn, max_retries=3, base_delay=1.0):
        return _llm_call_with_retry_impl(
            self,
            call_fn,
            max_retries=max_retries,
            base_delay=base_delay,
        )

    def llm_call_with_retry(self, call_fn, max_retries=3, base_delay=1.0):
        return self._llm_call_with_retry(
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

    def agentic_generate(
        self,
        prompt: str,
        system_instruction: str = None,
        allow_writes: bool = False,
        context_str: str = None,
        force_tool_use: bool = False,
        image_parts: list = None,
        skip_structured_reformat: bool = False,
    ) -> str:
        return self._agentic_generate(
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

    def format_tool_receipts(self, receipts: list, error: str = "", note: str = "") -> str:
        return self._format_tool_receipts(receipts, error=error, note=note)

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

    # End agentic loop

    def _get_thinking_config(self):
        return _get_thinking_config_impl()

    def get_thinking_config(self):
        return self._get_thinking_config()

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
