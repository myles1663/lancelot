from __future__ import annotations

import hashlib
import hmac
import logging as _logging
import os
import re
import shlex
import subprocess
import uuid
import time as _time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import feature_flags as _ff
from intent_classifier import classify_intent, IntentType
from plan_builder import EnvContext
from plan_types import OutcomeType
from providers.tool_schema import NormalizedToolDeclaration
from receipts import (
    ActionType,
    CognitionTier,
    ReceiptStatus,
    create_finalized_receipt,
    create_receipt,
)
from orchestrator_consts import COMMAND_BLACKLIST_CHARS, COMMAND_WHITELIST

_gov_logger = _logging.getLogger("src.core.orchestrator")

try:
    from memory.receipt_events import MemoryReceiptEmitter
except Exception:  # pragma: no cover - package path differs in test runners
    from src.core.memory.receipt_events import MemoryReceiptEmitter

_SHORT_ACKNOWLEDGEMENTS = {
    "ok",
    "okay",
    "yes",
    "yep",
    "yeah",
    "sure",
    "alright",
    "all right",
    "cool",
    "sounds good",
    "ok sounds good",
    "okay sounds good",
    "looks good",
    "that works",
    "works for me",
    "good to go",
    "go for it",
}


def _normalize_short_turn(text: str) -> str:
    normalized = re.sub(r"[\s,!.]+", " ", (text or "").strip().lower())
    return normalized.strip()


def _is_short_acknowledgement(message: str) -> bool:
    normalized = _normalize_short_turn(message)
    return len(normalized) <= 80 and normalized in _SHORT_ACKNOWLEDGEMENTS


def _is_parrot_response(user_message: str, response_text: str) -> bool:
    normalized_user = _normalize_short_turn(user_message)
    normalized_response = _normalize_short_turn(response_text)
    return (
        bool(normalized_user)
        and len(normalized_user) <= 80
        and normalized_user == normalized_response
    )


def _short_acknowledgement_response() -> str:
    return "Understood. I'll keep the current plan in focus."


_APPROVAL_WAIT_MARKERS = (
    "paused for commander approval",
    "pending commander approval",
    "waiting for commander approval",
    "review the actioncard",
    "approval id",
    "approval group id",
    "requires commander approval",
)


_AUTO_ESCALATION_CONTROL_PREFIXES = (
    "completed approved governed actions:",
    "completion contract failed:",
)


def _is_control_flow_response(response_text: str) -> bool:
    """Return true for bounded governance/tool-loop responses that must not be rewritten."""
    lowered = str(response_text or "").strip().lower()
    return (
        any(lowered.startswith(prefix) for prefix in _AUTO_ESCALATION_CONTROL_PREFIXES)
        or any(marker in lowered for marker in _APPROVAL_WAIT_MARKERS)
    )


def _approval_wait_acknowledgement_response() -> str:
    return (
        "I am paused for Commander approval. Review the ActionCard in War Room, "
        "then use that card's Continue control after approving. No additional "
        "governed tool work will run until that decision is recorded."
    )


def _previous_assistant_waiting_for_approval(self) -> bool:
    context = getattr(self, "context_env", None)
    history = getattr(context, "history", None)
    if not history:
        return False

    for entry in reversed(history[-6:]):
        if entry.get("role") != "assistant":
            continue
        content = str(entry.get("content") or "").lower()
        return any(marker in content for marker in _APPROVAL_WAIT_MARKERS)
    return False


def _explicit_tool_or_live_inspection_request(message: str) -> bool:
    """Return True when the request needs current repo/runtime state.

    The classifier can reasonably label these as "questions", but answering
    them from chat history makes Lancelot look hung or stale. These phrases
    are intentional operator requests to use governed tools.
    """
    msg = (message or "").lower()
    if not msg.strip():
        return False

    explicit_tool_terms = (
        "command_runner",
        "repo_writer",
        "github_connector",
        "network_client",
        "service_runner",
        "tool call",
        "tool-call",
        "use tool",
        "use lancelot",
        "governed connector",
    )
    if any(term in msg for term in explicit_tool_terms):
        return True

    live_targets = (
        "/home/lancelot/",
        "repo",
        "repository",
        "workspace",
        "container",
        "docker",
        "service logs",
        "health endpoint",
        "actioncard",
        "approval card",
    )
    live_actions = (
        "inspect",
        "read",
        "list",
        "check",
        "verify",
        "continue",
        "resume",
        "monitor",
        "look at",
        "search",
        "run",
        "execute",
        "tail",
    )
    return any(target in msg for target in live_targets) and any(
        action in msg for action in live_actions
    )


def _explicit_write_tool_request(message: str) -> bool:
    msg = (message or "").lower()
    write_terms = (
        "repo_writer",
        "write",
        "edit",
        "modify",
        "create",
        "delete",
        "patch",
        "overwrite",
        "commit",
        "push",
    )
    return _explicit_tool_or_live_inspection_request(message) and any(
        term in msg for term in write_terms
    )


def _agentic_write_execution_allowed(
    *,
    channel: str,
    explicit_write_request: bool,
    needs_research: bool,
    wants_action: bool,
) -> bool:
    """Return whether the agentic loop may auto-execute write-capable tool calls.

    War Room is the operator-facing governed surface. Write-capable actions there
    must remain approval-gated so grouped ActionCards can bound exact scope before
    repo, workspace, command, or connector mutations run.
    """
    if channel == "warroom":
        return False
    return explicit_write_request or (needs_research and wants_action)


def _should_record_task_experience(
    user_message: str,
    response_text: str,
    *,
    tool_receipts: list | None = None,
    needs_research: bool = False,
    wants_action: bool = False,
    explicit_tool_request: bool = False,
    explicit_write_request: bool = False,
    reasoning_artifact: Any = None,
) -> bool:
    """Return true when a completed turn is useful repeat-task evidence."""
    if not str(response_text or "").strip():
        return False
    message = str(user_message or "").strip()
    if not message or _is_short_acknowledgement(message):
        return False
    if tool_receipts:
        return True
    if needs_research or wants_action or explicit_tool_request or explicit_write_request:
        return True
    if reasoning_artifact is not None:
        return True
    action_terms = (
        "build",
        "fix",
        "update",
        "review",
        "analyze",
        "compare",
        "debug",
        "implement",
        "create",
        "write",
        "plan",
        "investigate",
        "summarize",
    )
    lowered = message.lower()
    return len(message) >= 40 and any(term in lowered for term in action_terms)


def _latest_task_graph(self, session_id: str):
    task_store = getattr(self, "task_store", None)
    if not task_store:
        return None
    try:
        return task_store.get_latest_graph_for_session(session_id)
    except Exception as exc:
        _gov_logger.warning(
            "pending_task_graph_lookup_failed",
            extra={"error": str(exc), "session_id": session_id},
        )
        return None

@dataclass
class ChatRequestContext:
    user_message: str
    channel: str
    session_id: str
    operator_id: str
    operator_name: str
    crusader_mode: bool
    quest_id: str
    has_attachments: bool
    start_time: float

class ChatPhase(Enum):
    PREFLIGHT = "preflight"
    CLASSIFICATION = "classification"
    EXECUTION = "execution"
    FINALIZATION = "finalization"


def _emit_progress(self, phase: str, message: str, **metadata: Any) -> None:
    emitter = getattr(self, "_emit_chat_progress", None)
    if callable(emitter):
        emitter(phase, message, **metadata)


def _active_work_context_block(self) -> str:
    """Render compact active-work state for continuation turns."""
    store = getattr(self, "work_ledger_store", None)
    if store is None:
        return ""
    try:
        return store.render_context_block(
            quest_id=getattr(self, "_current_quest_id", "") or "",
            session_id=getattr(self, "_current_session_id", "") or "",
            operator_id=getattr(self, "_current_operator_id", "") or "",
            max_items=3,
            max_events=8,
        )
    except Exception as exc:
        _gov_logger.warning(
            "active_work_context_render_failed",
            extra={"error": str(exc)},
        )
        return ""


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
    """Sends a message to the LLM provider with full context.

    Uses context caching when available for token savings (Gemini only).
    Applies system instructions via dedicated parameter.
    Includes thinking config for reasoning-capable models.
    Supports multimodal attachments (images, PDFs, text files).

    Args:
        channel: Source channel — "telegram", "warroom", or "api" (default).
    """
    self.wake_up("User Chat")
    self._current_channel = channel
    self._current_session_id = session_id or ""
    self._current_operator_id = operator_id or ""
    self._current_operator_name = operator_name or ""
    self.clear_telegram_delivery_handled()
    # Quest ID — groups all receipts from a single chat() invocation
    import uuid as _uuid
    self._current_quest_id = quest_id or str(_uuid.uuid4())
    if hasattr(self, 'context_env') and self.context_env:
        set_quest_id = getattr(self.context_env, "set_current_quest_id", None)
        if callable(set_quest_id):
            set_quest_id(self._current_quest_id)
        else:
            setattr(self.context_env, "_current_quest_id", self._current_quest_id)
    start_time = __import__("time").time()
    _emit_progress(self, ChatPhase.PREFLIGHT.value, "Running governance preflight checks")

    # Governance: Check Token Limit (Estimate)
    est_input_tokens = len(user_message) // 4 + 1000 # Rough estimate
    if not self.governor.check_limit("tokens", est_input_tokens):
         return "GOVERNANCE BLOCK: Daily token limit exceeded."

    # SECURITY: Sanitize Input
    user_message = self.sanitizer.sanitize(user_message)

    # Injection detection gate — clear refusal instead of cryptic pipeline fallback
    if user_message.startswith("[SUSPICIOUS INPUT DETECTED]"):
        import logging
        logging.getLogger("lancelot.security").warning(
            "Prompt injection attempt blocked (channel=%s): %.200s", channel, user_message
        )
        if getattr(self, "receipt_service", None):
            try:
                security_receipt = create_finalized_receipt(
                    ActionType.VERIFICATION,
                    "prompt_injection_blocked",
                    {
                        "channel": channel,
                        "input_length": len(user_message),
                        "security_gate": "input_sanitizer",
                    },
                    outputs={"blocked": True},
                    status=ReceiptStatus.FAILURE,
                    tier=CognitionTier.DETERMINISTIC,
                    quest_id=getattr(self, "_current_quest_id", None),
                    metadata={
                        "channel": channel,
                        "operator_id": self._current_operator_id or None,
                        "operator_name": self._current_operator_name or None,
                        "session_id": self._current_session_id or None,
                        "security_event": True,
                        "security_gate": "input_sanitizer",
                    },
                    operator_id=self._current_operator_id or None,
                    session_id=self._current_session_id or None,
                    error_message="Prompt injection detected by input sanitizer",
                )
                self.receipt_service.create(security_receipt)
            except Exception as exc:
                _logging.warning("Failed to persist prompt injection block receipt: %s", exc)
        refusal = (
            "I detected patterns in your message that resemble prompt injection "
            "or instruction override attempts. I can't process this request.\n\n"
            "If this was a legitimate question, please rephrase it without "
            "instruction-like syntax (e.g., avoid phrases like 'ignore previous "
            "instructions' or 'you are now')."
        )
        self.context_env.add_history("assistant", refusal)
        return refusal

    # Detect and persist name preferences before intent routing.
    self._check_name_update(user_message)

    # ── Process file/image attachments into provider-agnostic format ──
    file_parts = []  # list of (bytes, mime_type) tuples for multimodal
    if attachments:
        for att in attachments:
            if att.mime_type.startswith("image/") or att.mime_type == "application/pdf":
                # Images and PDFs: pass as (bytes, mime_type) for provider handling
                file_parts.append((att.data, att.mime_type))
                user_message += f"\n[Attached: {att.filename}]"
            else:
                # Text-based documents: decode and include as context
                try:
                    text_content = att.data.decode("utf-8", errors="replace")
                    if len(text_content) > 50000:
                        text_content = text_content[:50000] + "\n... (truncated)"
                    user_message += (
                        f"\n\n--- Attached file: {att.filename} ---\n"
                        f"{text_content}\n"
                        f"--- End of {att.filename} ---"
                    )
                except Exception:
                    user_message += f"\n[Attached: {att.filename} (binary, not readable)]"

    # S6: Add to History (Short-term Memory) — tag with source channel
    channel_tag = f"[via {channel}] " if channel != "api" else ""
    self.context_env.add_history("user", f"{channel_tag}{user_message}")

    # Approval/proceed follow-ups and short acknowledgements are deterministic
    # control messages. Handle them before classifier/model routing so a simple
    # "continue" or "ok" never burns a full reasoning/model turn.
    if (
        self._is_proceed_message(user_message)
        and self.task_store
        and not _explicit_tool_or_live_inspection_request(user_message)
    ):
        session_id = getattr(self, '_current_session_id', '')
        graph = _latest_task_graph(self, session_id)
        if graph:
            result = self._handle_approval(session_id=session_id)
        else:
            result = self._handle_proceed(user_message, session_id=session_id)
        self.context_env.add_history("assistant", result)
        return result

    if _is_short_acknowledgement(user_message) and _previous_assistant_waiting_for_approval(self):
        _gov_logger.debug("short_acknowledgement_resolved_to_approval_wait")
        result = _approval_wait_acknowledgement_response()
        self.context_env.add_history("assistant", result)
        return result

    if (
        _is_short_acknowledgement(user_message)
        and self._previous_was_substantive()
    ):
        _gov_logger.debug("short_acknowledgement_bypassed_generation")
        result = _short_acknowledgement_response()
        self.context_env.add_history("assistant", result)
        return result

    # ── Honest Closure: Intent Classification + Pipeline Routing ──
    # Unified classifier — single LLM call replaces 7-function heuristic chain
    from feature_flags import FEATURE_UNIFIED_CLASSIFICATION
    _unified_result = None
    _emit_progress(self, ChatPhase.CLASSIFICATION.value, "Classifying request and routing lane")
    if FEATURE_UNIFIED_CLASSIFICATION and self.provider:
        try:
            from unified_classifier import UnifiedClassifier
            _clf = UnifiedClassifier(
                self.provider,
                model_router=getattr(self, "model_router", None),
                local_model=getattr(self, "local_model", None),
            )
            # Build recent history for continuation detection
            _recent_history = []
            if hasattr(self, 'context_env') and self.context_env:
                for entry in self.context_env.history[-6:]:
                    _recent_history.append({
                        "role": entry.get("role", "user"),
                        "text": entry.get("content", "")[:200],
                    })
            _unified_result = _clf.classify(user_message, _recent_history)
            intent = _unified_result.to_intent_type()
            _gov_logger.debug(
                "unified_classifier_result",
                extra={
                    "intent": _unified_result.intent,
                    "confidence": _unified_result.confidence,
                    "is_continuation": _unified_result.is_continuation,
                    "requires_tools": _unified_result.requires_tools,
                    "routed_intent": intent.value,
                },
            )
        except Exception as e:
            _gov_logger.warning(
                "unified_classifier_failed",
                extra={"error": str(e)},
            )
            _unified_result = None

    if _unified_result is None:
        # Legacy keyword chain 
        intent = classify_intent(user_message)
        _gov_logger.debug(
            "keyword_intent_classified",
            extra={"intent": intent.value},
        )
        # LLM-based intent verification for ambiguous classifications
        intent = self._verify_intent_with_llm(user_message, intent)

    # Continuation and research rerouting
    if _unified_result is not None:
        # Unified classifier already handles continuations and research detection
        if _unified_result.is_continuation and intent in (IntentType.PLAN_REQUEST, IntentType.MIXED_REQUEST):
            _gov_logger.debug(
                "continuation_routed_to_agentic_loop",
                extra={"source": "unified_classifier"},
            )
            intent = IntentType.KNOWLEDGE_REQUEST
        elif _unified_result.is_continuation and intent == IntentType.EXEC_REQUEST:
            # EXEC_REQUEST continuations still need governance — don't bypass permission
            _gov_logger.debug(
                "exec_continuation_kept_in_governance",
                extra={"source": "unified_classifier"},
            )
        elif _unified_result.intent == "action_low_risk":
            # Cross-check for write verbs — if the message contains a write
            # action verb, the classifier may be wrong about "low risk" and we
            # should route through governance. Read/search actions are trusted.
            _write_verbs = [
                "create", "write", "save", "update", "modify", "edit",
                "delete", "remove", "drop", "destroy",
                "send", "post", "notify", "message", "email",
                "deploy", "push", "publish", "install",
                "execute", "run command", "run script",
                "move", "rename", "overwrite",
            ]
            _msg_lower = user_message.lower()
            if any(v in _msg_lower for v in _write_verbs):
                _gov_logger.debug(
                    "low_risk_classifier_overridden_by_write_verbs"
                )
                intent = IntentType.EXEC_REQUEST
            else:
                _gov_logger.debug("low_risk_action_routed_to_agentic_loop")
                intent = IntentType.KNOWLEDGE_REQUEST
    else:
        # Fall back to the legacy continuation and research heuristics.
        # Only reroute PLAN/MIXED continuations — EXEC_REQUEST must stay in governance
        if intent in (IntentType.PLAN_REQUEST, IntentType.MIXED_REQUEST):
            if self._is_continuation(user_message):
                _gov_logger.debug(
                    "continuation_routed_from_planning_pipeline",
                    extra={"source": "keyword_chain"},
                )
                intent = IntentType.KNOWLEDGE_REQUEST
            elif self._needs_research(user_message):
                _gov_logger.debug(
                    "research_intent_routed_to_agentic_loop",
                    extra={"source": "keyword_chain"},
                )
                intent = IntentType.KNOWLEDGE_REQUEST
        elif intent == IntentType.EXEC_REQUEST:
            if self._is_continuation(user_message):
                _gov_logger.debug(
                    "exec_continuation_kept_in_governance",
                    extra={"source": "keyword_chain"},
                )
            elif self._needs_research(user_message):
                _gov_logger.debug(
                    "research_intent_routed_to_agentic_loop",
                    extra={"source": "keyword_chain"},
                )
                intent = IntentType.KNOWLEDGE_REQUEST

    # Also detect continuations for KNOWLEDGE_REQUEST
    # Short follow-up messages like "name it X" or "the txt file" reference prior conversation
    if _unified_result is None and intent == IntentType.KNOWLEDGE_REQUEST and self._is_continuation(user_message):
        _gov_logger.debug("knowledge_continuation_full_context")

    explicit_tool_request = _explicit_tool_or_live_inspection_request(user_message)
    explicit_write_request = _explicit_write_tool_request(user_message)
    if explicit_tool_request:
        _gov_logger.debug(
            "explicit_tool_request_routed_to_agentic_loop",
            extra={"writes_enabled": explicit_write_request},
        )
        intent = IntentType.KNOWLEDGE_REQUEST
        if _unified_result is not None:
            _unified_result.requires_tools = True

    if intent in (IntentType.PLAN_REQUEST, IntentType.MIXED_REQUEST):
        _emit_progress(self, ChatPhase.EXECUTION.value, "Building governed plan artifact")
        # Route through PlanningPipeline — produces PlanArtifact same turn
        pipeline_result = self.planning_pipeline.process(user_message)
        if pipeline_result.outcome == OutcomeType.COMPLETED_WITH_PLAN_ARTIFACT:
            # Replace generic planner steps with a concrete execution plan.
            if pipeline_result.artifact:
                pipeline_result.artifact = self._enrich_plan_with_llm(
                    pipeline_result.artifact, user_message
                )
                self._last_plan_artifact = pipeline_result.artifact

            # Normalize planner output through the response assembler when available.
            if self.assembler and pipeline_result.artifact:
                assembled = self.assembler.assemble(plan_artifact=pipeline_result.artifact, channel=channel)
                self.context_env.add_history("assistant", assembled.chat_response)
                if assembled.war_room_artifacts:
                    self._deliver_war_room_artifacts(assembled.war_room_artifacts)
                return assembled.chat_response

            # Fallback: route rendered markdown through assembler for section stripping
            if self.assembler and pipeline_result.rendered_output:
                assembled = self.assembler.assemble(raw_planner_output=pipeline_result.rendered_output, channel=channel)
                self.context_env.add_history("assistant", assembled.chat_response)
                if assembled.war_room_artifacts:
                    self._deliver_war_room_artifacts(assembled.war_room_artifacts)
                return assembled.chat_response

            self.context_env.add_history("assistant", pipeline_result.rendered_output)
            return pipeline_result.rendered_output
        # If pipeline couldn't complete, fall through to LLM

    if intent == IntentType.EXEC_REQUEST:
        # Just-do-it mode — low-risk exec requests skip the pipeline
        if self._is_low_risk_exec(user_message):
            _gov_logger.debug("low_risk_execution_routed_to_agentic_loop")
            intent = IntentType.KNOWLEDGE_REQUEST
            # Fall through to KNOWLEDGE_REQUEST handling below

    if intent == IntentType.EXEC_REQUEST:
        # Simple action detector — skip pipeline for single-skill operations
        _emit_progress(self, ChatPhase.EXECUTION.value, "Compiling action plan and permission scope")
        simple_artifact = self._build_simple_action_plan(user_message)
        if simple_artifact:
            self._last_plan_artifact = simple_artifact
            # Compile directly to TaskGraph → Permission (skip enrichment)
            if self.plan_compiler and self.task_store:
                session_id = getattr(self, '_current_session_id', '')
                graph = self.plan_compiler.compile_plan_artifact(
                    simple_artifact, session_id=session_id,
                )
                self.task_store.save_graph(graph)
                result = self._request_permission(graph)
                self.context_env.add_history("assistant", result)
                return result

        # Route executable requests through planning, compilation, and permission checks.
        pipeline_result = self.planning_pipeline.process(user_message)

        if pipeline_result.artifact:
            # Replace generic planner steps with a concrete execution plan.
            pipeline_result.artifact = self._enrich_plan_with_llm(
                pipeline_result.artifact, user_message
            )
            self._last_plan_artifact = pipeline_result.artifact

            # Compile to TaskGraph and request permission
            if self.plan_compiler and self.task_store:
                session_id = getattr(self, '_current_session_id', '')
                graph = self.plan_compiler.compile_plan_artifact(
                    pipeline_result.artifact, session_id=session_id,
                )
                self.task_store.save_graph(graph)
                result = self._request_permission(graph)
                self.context_env.add_history("assistant", result)
                return result

        # Fallback: show clean plan via assembler
        if self.assembler and pipeline_result.artifact:
            assembled = self.assembler.assemble(plan_artifact=pipeline_result.artifact, channel=channel)
            self.context_env.add_history("assistant", assembled.chat_response)
            if assembled.war_room_artifacts:
                self._deliver_war_room_artifacts(assembled.war_room_artifacts)
            return assembled.chat_response

        if self.assembler and pipeline_result.rendered_output:
            assembled = self.assembler.assemble(raw_planner_output=pipeline_result.rendered_output, channel=channel)
            self.context_env.add_history("assistant", assembled.chat_response)
            if assembled.war_room_artifacts:
                self._deliver_war_room_artifacts(assembled.war_room_artifacts)
            return assembled.chat_response

        # Last resort fallback
        resp = pipeline_result.rendered_output or "I need more details to create an execution plan."
        self.context_env.add_history("assistant", resp)
        return resp

    # KNOWLEDGE_REQUEST, AMBIGUOUS, or fallback — route to LLM
    # Model Routing
    _emit_progress(self, ChatPhase.EXECUTION.value, "Selecting governed model route")
    selected_model = self._route_model(user_message)
    _gov_logger.debug(
        "model_routed",
        extra={"model": selected_model},
    )

    # Create Receipt for LLM Call
    receipt = create_receipt(
        ActionType.LLM_CALL, "chat_generation",
        {"user_message": user_message, "model": selected_model},
        tier=CognitionTier.CLASSIFICATION,
        quest_id=getattr(self, '_current_quest_id', None),
        metadata={
            "model": selected_model,
            "channel": channel,
            "provider": getattr(self.provider, 'provider_name', 'unknown'),
            "operator_id": self._current_operator_id or None,
            "operator_name": self._current_operator_name or None,
            "session_id": self._current_session_id or None,
        },
    )
    self.receipt_service.create(receipt)

    if not self.provider:
        return "Error: LLM provider not initialized (Missing API Key)."

    try:
        _emit_progress(self, ChatPhase.EXECUTION.value, "Compiling memory, receipts, and conversation context")
        # Get Deterministic Context (memory-augmented if enabled)
        if self._memory_enabled and self.context_compiler:
            try:
                self.context_compiler.record_active_objective(
                    objective=user_message,
                    quest_id=getattr(self, "_current_quest_id", None),
                    channel=channel,
                )
                compiled = self.context_compiler.compile_for_objective(
                    objective=user_message,
                    quest_id=getattr(self, "_current_quest_id", None),
                    mode="crusader" if crusader_mode else "normal",
                    emit_receipt=True,
                    receipt_emitter=MemoryReceiptEmitter(
                        getattr(self, "data_dir", "/home/lancelot/data")
                    ),
                )
                # Structured memory compiler provides core blocks, working memory,
                # and retrieval items — but NOT conversation history or receipts.
                # Append those from ContextEnvironment so the LLM has full context.
                history_str = self.context_env.get_history_string(limit=30, channel=channel)
                receipts_str = self.context_env.get_recent_receipts(limit=10)
                context_str = compiled.rendered_prompt
                if receipts_str and receipts_str.strip():
                    context_str += f"\n\n{receipts_str}"
                if history_str and history_str.strip():
                    context_str += f"\n\n{history_str}"
            except Exception as mem_err:
                _gov_logger.warning(
                    "memory_compilation_failed",
                    extra={"error": str(mem_err)},
                )
                context_str = self.context_env.get_context_string(channel=channel)
        else:
            context_str = self.context_env.get_context_string(channel=channel)

        active_work_context = _active_work_context_block(self)
        if active_work_context:
            context_str = (context_str or "") + "\n\n" + active_work_context

        # Legacy fields
        self.rules_context = "See ContextEnv"
        self.user_context = "See ContextEnv"
        self.memory_summary = "See ContextEnv"

        system_instruction = self._build_system_instruction(crusader_mode)
        _emit_progress(self, ChatPhase.EXECUTION.value, "Preparing governed model request")

        # Competitive scan — inject previous scan context if available
        _competitive_target = None
        try:
            from feature_flags import FEATURE_COMPETITIVE_SCAN, FEATURE_MEMORY_VNEXT
            if FEATURE_COMPETITIVE_SCAN and FEATURE_MEMORY_VNEXT:
                from competitive_scan import detect_competitive_target, retrieve_previous_scans, build_context_from_previous
                _competitive_target = detect_competitive_target(user_message)
                if _competitive_target:
                    _mem_mgr = getattr(self, '_memory_store_manager', None)
                    if _mem_mgr is None:
                        from memory.sqlite_store import MemoryStoreManager
                        self._memory_store_manager = MemoryStoreManager(
                            data_dir=getattr(self, 'data_dir', '/home/lancelot/data')
                        )
                        _mem_mgr = self._memory_store_manager
                    _prev_scans = retrieve_previous_scans(_competitive_target, _mem_mgr)
                    if _prev_scans:
                        _scan_context = build_context_from_previous(_prev_scans)
                        context_str = (context_str or "") + _scan_context
                        _gov_logger.debug(
                            "competitive_scan_context_injected",
                            extra={
                                "target": _competitive_target,
                                "previous_scan_count": len(_prev_scans),
                            },
                        )
                    else:
                        _gov_logger.debug(
                            "competitive_scan_no_prior_context",
                            extra={"target": _competitive_target},
                        )
        except Exception as e:
            _gov_logger.warning(
                "competitive_scan_preprocessing_failed",
                extra={"error": str(e)},
            )        # Use the agentic loop for research and tool-backed knowledge requests.
        from feature_flags import FEATURE_AGENTIC_LOOP, FEATURE_LOCAL_AGENTIC, FEATURE_DEEP_REASONING_LOOP
        # When file_parts present (images/PDFs), skip local model — no vision support
        has_vision_input = bool(file_parts)
        # Use unified classifier result for continuation if available
        is_continuation = (
            _unified_result.is_continuation if _unified_result else self._is_continuation(user_message)
        ) and not explicit_tool_request

        # Check if the previous exchange was substantive (used tools,
        # long response, or action intent). If so, follow-ups should go to
        # flagship to preserve full context — local model can't see enough.
        _prev_substantive = self._previous_was_substantive()
        if _prev_substantive:
            _gov_logger.debug("substantive_follow_up_forces_flagship")

        if FEATURE_AGENTIC_LOOP:
            # Conversational messages bypass agentic loop entirely
            # (no tools needed for "call me Myles", "hello", "thanks", etc.)
            # Route to local model first to save flagship tokens.
            # BUT if it's a continuation ("yes", "go ahead", etc.),
            # skip conversational bypass — needs full context + tools.
            # Also skip local routing if previous exchange was substantive.
            _is_conv = (_unified_result.intent == "conversational" if _unified_result else self._is_conversational(user_message))
            if _is_conv and not explicit_tool_request and not has_vision_input and not is_continuation and not _prev_substantive:
                if FEATURE_LOCAL_AGENTIC and self.local_model and self.local_model.is_healthy():
                    _gov_logger.debug("conversational_message_routed_to_local_model")
                    raw_response = self._local_agentic_generate(
                        prompt=user_message,
                        system_instruction=system_instruction,
                        allow_writes=False,
                        context_str=context_str,
                    )
                else:
                    _gov_logger.debug("conversational_message_routed_to_text_only")
                    raw_response = self._text_only_generate(
                        prompt=user_message,
                        system_instruction=system_instruction,
                        context_str=context_str,
                        image_parts=file_parts,
                    )
                # Empty response fallback for simple acks
                if not raw_response or not raw_response.strip():
                    raw_response = "Understood."
            # Vision input always routes to flagship (skip local model)
            elif has_vision_input:
                _gov_logger.debug("vision_input_routed_to_flagship")
                raw_response = self._text_only_generate(
                    prompt=user_message,
                    system_instruction=system_instruction,
                    context_str=context_str,
                    image_parts=file_parts,
                )
            # Try local model for simple queries to save flagship tokens
            # Use unified classifier's confidence for local routing
            # Skip local model if previous exchange was substantive
            elif not explicit_tool_request and not _prev_substantive and FEATURE_LOCAL_AGENTIC and (
                (_unified_result.intent == "question" and not _unified_result.requires_tools)
                if _unified_result else self._is_simple_for_local(user_message)
            ):
                _gov_logger.debug("simple_query_routed_to_local_agentic")
                raw_response = self._local_agentic_generate(
                    prompt=user_message,
                    system_instruction=system_instruction,
                    allow_writes=False,
                    context_str=context_str,
                )
            else:
                # Continuations bypass research detection
                if is_continuation:
                    _gov_logger.debug("continuation_skips_research_detection")
                    needs_research = False
                    allow_writes = False
                elif _unified_result:
                    # Use unified classifier's requires_tools field
                    needs_research = _unified_result.requires_tools
                    wants_action = _unified_result.intent in ("action_low_risk", "action_high_risk")
                    allow_writes = _agentic_write_execution_allowed(
                        channel=channel,
                        explicit_write_request=explicit_write_request,
                        needs_research=needs_research,
                        wants_action=wants_action,
                    )
                else:
                    # Force tool use for research-oriented queries
                    needs_research = explicit_tool_request or self._needs_research(user_message)
                    # Allow writes when user expects action (code, config, setup)
                    wants_action = self._wants_action(user_message)
                    allow_writes = _agentic_write_execution_allowed(
                        channel=channel,
                        explicit_write_request=explicit_write_request,
                        needs_research=needs_research,
                        wants_action=wants_action,
                    )
                if needs_research:
                    _gov_logger.debug(
                        "research_query_forces_tool_use",
                        extra={
                            "writes_enabled": allow_writes,
                            "channel": channel,
                            "write_request_gated": (
                                channel == "warroom"
                                and (explicit_write_request or (needs_research and wants_action))
                            ),
                        },
                    )
                else:
                    _gov_logger.debug("knowledge_request_routed_to_agentic_loop")

                # Deep reasoning pass before agentic execution
                reasoning_artifact = None
                if FEATURE_DEEP_REASONING_LOOP and self._should_use_deep_reasoning(user_message):
                    _gov_logger.debug("deep_reasoning_pass_triggered")
                    past_exp = self._retrieve_task_experiences(user_message)
                    reasoning_artifact = self._deep_reasoning_pass(user_message, past_exp)

                    if (reasoning_artifact and reasoning_artifact.reasoning_text
                            and reasoning_artifact.reasoning_text != "[Reasoning pass unavailable]"):
                        # Inject reasoning as context for the agentic loop
                        reasoning_block = reasoning_artifact.to_context_block()
                        context_str = (context_str or "") + "\n\n" + reasoning_block
                        _gov_logger.debug(
                            "reasoning_artifact_injected",
                            extra={"chars": len(reasoning_artifact.reasoning_text)},
                        )

                        # If reasoning identified capability gaps, append to system instruction
                        if reasoning_artifact.capability_gaps:
                            gaps_note = "\n\nCAPABILITY GAPS IDENTIFIED IN REASONING:\n"
                            for gap in reasoning_artifact.capability_gaps:
                                gaps_note += f"- {gap}\n"
                            gaps_note += "Work around these gaps using available tools. Note unresolvable gaps in your response.\n"
                            system_instruction = (system_instruction or self._build_system_instruction()) + gaps_note
                            _gov_logger.debug(
                                "capability_gaps_noted",
                                extra={"count": len(reasoning_artifact.capability_gaps)},
                            )
                    else:
                        _gov_logger.debug("deep_reasoning_returned_empty")

                raw_response = self._agentic_generate(
                    prompt=user_message,
                    system_instruction=system_instruction,
                    allow_writes=allow_writes,
                    context_str=context_str,
                    force_tool_use=needs_research,
                    image_parts=file_parts,
                )
        else:
            # fallback: text-only LLM
            raw_response = self._text_only_generate(
                prompt=user_message,
                system_instruction=system_instruction,
                context_str=context_str,
                image_parts=file_parts,
            )

        # Auto-escalation — if Flash returned a thin response for a
        # non-trivial query, retry once with the deep model transparently.
        deep_model = self._get_deep_model()
        if (
            deep_model != self.model_name
            and len(user_message) > 200
            and raw_response
            and len(raw_response.strip()) < 100
            and not _is_control_flow_response(raw_response)
            and not self._is_conversational(user_message)
        ):
            _gov_logger.info(
                "auto_escalation_triggered",
                extra={
                    "response_chars": len(raw_response.strip()),
                    "model": deep_model,
                },
            )
            try:
                esc_msg = self._build_frontier_user_message(
                    f"{context_str or self.context_env.get_context_string()}\n\n{user_message}"
                )
                esc_result = self._llm_call_with_retry(
                    lambda: self._provider_generate(
                        model=deep_model,
                        messages=[esc_msg],
                        system_instruction=system_instruction,
                        config={"thinking": self._get_thinking_config()},
                    )
                )
                if esc_result.text and len(esc_result.text.strip()) > len(raw_response.strip()):
                    raw_response = esc_result.text
                    _gov_logger.info(
                        "auto_escalation_succeeded",
                        extra={
                            "response_chars": len(raw_response),
                            "model": deep_model,
                        },
                    )
                    if self.usage_tracker:
                        esc_tokens = len(raw_response) // 4
                        self.usage_tracker.record_simple(deep_model, esc_tokens)
            except Exception as e:
                _gov_logger.warning(
                    "auto_escalation_failed",
                    extra={
                        "model": deep_model,
                        "error": str(e),
                    },
                )

        # Provider/tooling failures from lower generation helpers must not be
        # recorded as successful llm_call receipts just because they were
        # converted into bounded user-facing error text.
        if (
            isinstance(raw_response, str)
            and raw_response.startswith("Error generating response:")
        ):
            raise RuntimeError(
                raw_response.split(":", 1)[1].strip() or "Unknown provider error"
            )

        # S10: Sanitize LLM output before parsing
        _emit_progress(self, ChatPhase.FINALIZATION.value, "Validating and assembling response")
        sanitized_response = self._validate_llm_response(raw_response)
        if _is_parrot_response(user_message, sanitized_response):
            _gov_logger.warning(
                "short_turn_parrot_response_replaced",
                extra={"message_chars": len(user_message)},
            )
            sanitized_response = _short_acknowledgement_response()

        # Store competitive scan in episodic memory (post-processing)
        if _competitive_target and sanitized_response:
            try:
                from feature_flags import FEATURE_COMPETITIVE_SCAN
                if FEATURE_COMPETITIVE_SCAN:
                    from competitive_scan import store_scan
                    _mem_mgr = getattr(self, '_memory_store_manager', None)
                    if _mem_mgr:
                        store_scan(
                            target=_competitive_target,
                            findings=sanitized_response,
                            receipt_skills=[],
                            memory_store_manager=_mem_mgr,
                        )
            except Exception as e:
                _gov_logger.warning(
                    "competitive_scan_postprocessing_failed",
                    extra={"error": str(e)},
                )

        # Record reusable task experience for future retrieval. This is not
        # gated on deep reasoning; routine governed work should also teach
        # future long-running and repeat-task sessions.
        _v25_artifact = locals().get('reasoning_artifact', None)
        _v25_tool_receipts = getattr(self, '_last_tool_receipts', [])
        if _should_record_task_experience(
            user_message,
            sanitized_response,
            tool_receipts=_v25_tool_receipts,
            needs_research=bool(locals().get('needs_research', False)),
            wants_action=bool(locals().get('wants_action', False)),
            explicit_tool_request=bool(explicit_tool_request),
            explicit_write_request=bool(explicit_write_request),
            reasoning_artifact=_v25_artifact,
        ):
            try:
                _v25_duration = int((__import__("time").time() - start_time) * 1000)
                self._record_task_experience(
                    user_message=user_message,
                    response_text=sanitized_response,
                    tool_receipts=_v25_tool_receipts,
                    reasoning_artifact=_v25_artifact,
                    duration_ms=_v25_duration,
                )
            except Exception as e:
                _gov_logger.warning(
                    "task_experience_recording_failed",
                    extra={"error": str(e)},
                )

        # S6: Add to History
        self.context_env.add_history("assistant", sanitized_response)

        # Helper to estimate tokens (since we don't always get usage metadata)
        est_tokens = len(sanitized_response) // 4

        duration = int((__import__("time").time() - start_time) * 1000)
        self.receipt_service.update(receipt.complete(
            {"response": sanitized_response},
            duration,
            token_count=est_tokens
        ))

        # Governance: Log Usage (skip if agentic loop already tracked per-iteration)
        if not FEATURE_AGENTIC_LOOP:
            self.governor.log_usage("tokens", est_tokens + est_input_tokens)
            if self.usage_tracker:
                self.usage_tracker.record_simple(self.model_name, est_tokens + est_input_tokens)

        final_response = self._parse_response(sanitized_response)

        # Normalize free-form model output through the assembler before returning it.
        # Pass delivery channel for channel-aware truncation + auto-document
        if self.assembler and final_response:
            assembled = self.assembler.assemble(
                raw_planner_output=final_response,
                channel=channel,
            )
            final_response = assembled.chat_response
            # Deliver War Room artifacts (research reports, auto-documents)
            if assembled.war_room_artifacts:
                doc_paths = self._deliver_war_room_artifacts(assembled.war_room_artifacts)
                # Append download links for auto-created documents
                if doc_paths:
                    final_response = self._append_download_links(final_response, doc_paths)

        # Store conversation turn in episodic memory if enabled
        if self._memory_enabled and self.context_compiler:
            try:
                from memory.schemas import MemoryItem, MemoryTier, MemoryStatus
                item = MemoryItem(
                    tier=MemoryTier.episodic,
                    title=f"Chat: {user_message[:80]}",
                    content=f"User: {user_message}\nAssistant: {final_response}",
                    namespace="conversation",
                    status=MemoryStatus.active,
                )
                self.context_compiler.memory_manager.episodic.insert(item)
            except Exception as mem_err:
                _gov_logger.warning(
                    "episodic_memory_store_failed",
                    extra={"error": str(mem_err)},
                )

        return final_response
    except Exception as e:
        duration = int((__import__("time").time() - start_time) * 1000)
        if 'receipt' in locals():
            self.receipt_service.update(receipt.fail(str(e), duration))
        error_text = str(e)
        if (
            e.__class__.__name__ in {"PIIScrubError", "PIIScrubPayloadError"}
            or "Frontier scrub policy is required" in error_text
        ):
            return (
                "Governance blocked frontier generation because required local "
                "PII scrubbing is unavailable. No frontier model call was made. "
                "Check the local-llm service/model health, or change Frontier "
                "Scrub Mode in Setup Recovery if you intentionally want a degraded "
                f"privacy fallback.\n\nReason: {error_text}"
            )
        return f"Error generating response: {e}"
