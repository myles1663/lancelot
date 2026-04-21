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
from receipts import ActionType, CognitionTier, ReceiptStatus, create_finalized_receipt
from orchestrator_consts import COMMAND_BLACKLIST_CHARS, COMMAND_WHITELIST

_gov_logger = _logging.getLogger("src.core.orchestrator")

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
    """Core agentic loop: LLM + function calling via skills.

    Calls the active LLM provider with tool declarations. When the model
    returns a tool call, executes it via SkillExecutor and feeds the result
    back. Loops until the model returns a text response or max iterations.

    Provider-agnostic — works with Gemini, OpenAI, and Anthropic via
    the ProviderClient abstraction.

    Args:
        prompt: The user's prompt/question
        system_instruction: Optional system instruction override
        allow_writes: If True, write operations auto-execute. If False, escalate.
        context_str: Optional pre-built context string
        force_tool_use: If True, first iteration forces tool call via mode=ANY
        image_parts: Optional list of (bytes, mime_type) tuples for multimodal
        skip_structured_reformat: Skip the structured JSON reformat step.
            Set True when called from execution/enrichment paths where free-form
            output is expected and JSON schema reformat always fails.

    Returns:
        The final text response from the LLM
    """
    MAX_ITERATIONS = 10

    if not self.provider:
        return "Error: LLM provider not initialized."

    if not self.skill_executor:
        _gov_logger.warning("skill_executor_unavailable_fallback_text_only")
        return self._text_only_generate(prompt, system_instruction, context_str, image_parts=image_parts)

    # Build normalized tool declarations (provider converts to native format)
    declarations = self._build_tool_declarations()

    if not system_instruction:
        system_instruction = self._build_system_instruction()

    # When force_tool_use=True, first iteration uses mode=ANY
    # to force the model to call at least one tool before returning text.
    # After first tool call, switch back to AUTO.
    current_tool_config = {"mode": "ANY"} if force_tool_use else None
    if force_tool_use:
        _gov_logger.debug(
            "forced_tool_use_enabled",
            extra={"mode": "ANY"},
        )

    # Structured output — force JSON schema on text responses
    # Skip structured output for Telegram — users need natural text, not JSON
    from feature_flags import FEATURE_STRUCTURED_OUTPUT, FEATURE_CLAIM_VERIFICATION, FEATURE_DEEP_REASONING_LOOP
    _channel = getattr(self, "_current_channel", "api")
    _use_structured_output = FEATURE_STRUCTURED_OUTPUT and not skip_structured_reformat and _channel != "telegram"
    if _use_structured_output:
        _gov_logger.debug(
            "structured_output_enabled",
            extra={"channel": _channel},
        )

    # Build initial message (with optional image/PDF parts for multimodal)
    ctx = context_str or self.context_env.get_context_string()

    # Extract proper nouns / quoted terms and inject as untouchable literals
    literal_terms = self._extract_literal_terms(prompt)
    literal_guard = ""
    if literal_terms:
        terms_str = ", ".join(f'"{t}"' for t in literal_terms)
        literal_guard = (
            f"\n\n⚠️ LITERAL TERMS (use exactly as written — do NOT correct, "
            f"interpret, or substitute these): {terms_str}"
        )
        _gov_logger.debug(
            "literal_terms_extracted",
            extra={"terms": literal_terms},
        )

    full_text = f"{ctx}\n\n{prompt}{literal_guard}"
    initial_msg = self._build_frontier_user_message(full_text, images=image_parts)
    messages = [initial_msg]

    # Track tool calls for receipts and cost
    tool_receipts = []
    total_est_tokens = 0
    # Expose receipts for task experience recording
    self._last_tool_receipts = tool_receipts

    # Emit quest_started for tool flow streaming
    _quest_id = getattr(self, "_current_quest_id", None) or ""
    _channel = getattr(self, "_current_channel", "api")
    _agentic_start_ms = int(_time.time() * 1000)
    if self.toolflow_emitter:
        self.toolflow_emitter.quest_started(_quest_id, _channel, MAX_ITERATIONS)

    for iteration in range(MAX_ITERATIONS):
        _gov_logger.debug(
            "agentic_loop_iteration_started",
            extra={
                "iteration": iteration + 1,
                "max_iterations": MAX_ITERATIONS,
            },
        )

        # Emit iteration_started
        if self.toolflow_emitter:
            self.toolflow_emitter.iteration_started(_quest_id, iteration + 1, _channel)

        # Cost guard: check governance limit before each LLM call
        iter_est_tokens = sum(len(str(m)) for m in messages) // 4
        if not self.governor.check_limit("tokens", iter_est_tokens):
            _gov_logger.warning(
                "agentic_governance_token_limit_reached",
                extra={
                    "iteration": iteration + 1,
                    "estimated_tokens": iter_est_tokens,
                },
            )
            return self._format_tool_receipts(
                tool_receipts,
                note="Stopped: daily token limit reached. Here's what I found so far:",
            )

        try:
            _gen_config = {"thinking": self._get_thinking_config()}
            # After 3+ tool calls, increase max_tokens so the model
            # has room to synthesize a comprehensive report (not just a summary)
            if len(tool_receipts) >= 3:
                _gen_config["max_tokens"] = 16384
            # NOTE — structured output (response_mime_type/response_schema)
            # is NOT applied to generate_with_tools. Combining JSON schema
            # enforcement with function calling causes the model to avoid
            # returning text and keep making tool calls until max iterations.
            # Instead, structured output is applied as a post-processing
            # reformat step after the loop completes (see below).
            result = self._llm_call_with_retry(
                lambda: self._provider_generate_with_tools(
                    model=self._route_model(prompt),
                    messages=messages,
                    system_instruction=system_instruction,
                    tools=declarations,
                    tool_config=current_tool_config,
                    config=_gen_config,
                )
            )
        except Exception as e:
            _gov_logger.warning(
                "agentic_loop_llm_call_failed",
                extra={
                    "iteration": iteration + 1,
                    "error": str(e),
                },
            )
            # Emit quest_failed on LLM error
            if self.toolflow_emitter:
                _dur = int(_time.time() * 1000) - _agentic_start_ms
                self.toolflow_emitter.quest_failed(_quest_id, str(e), _dur, _channel)
            if tool_receipts:
                # Try structured reformat to produce a clean summary
                if _use_structured_output:
                    try:
                        from response.presenter import ResponsePresenter, AGENTIC_RESPONSE_SCHEMA, parse_structured_response
                        _receipt_summary = "\n".join(
                            f"- {r['skill']}: {r.get('result', 'unknown')}"
                            for r in tool_receipts
                        )
                        _err_prompt = (
                            f"Summarize what was accomplished based on these tool receipts. "
                            f"The process was interrupted by an error: {e}\n"
                            f"Be concise. Only claim actions that appear in the receipts.\n\n"
                            f"TOOL RECEIPTS:\n{_receipt_summary}"
                        )
                        _err_msg = self._build_frontier_user_message(_err_prompt)
                        _err_result = self._provider_generate(
                            model=self._route_model(prompt),
                            messages=[_err_msg],
                            system_instruction="You summarize tool results into JSON. Only include actions verified by receipts.",
                            config={
                                "response_mime_type": "application/json",
                                "response_schema": AGENTIC_RESPONSE_SCHEMA,
                            },
                        )
                        structured = parse_structured_response(_err_result.text)
                        if structured:
                            presenter = ResponsePresenter(claim_verification=FEATURE_CLAIM_VERIFICATION)
                            return presenter.present(structured, tool_receipts)
                    except Exception as reformat_err:
                        _gov_logger.warning(
                            "error_path_reformat_failed",
                            extra={"error": str(reformat_err)},
                        )
                return self._format_tool_receipts(tool_receipts, error=str(e))
            return f"Error during agentic generation: {e}"

        # Track token usage per iteration
        resp_text = result.text or ""
        iter_out_tokens = len(resp_text) // 4
        iter_total = iter_est_tokens + iter_out_tokens
        total_est_tokens += iter_total
        self.governor.log_usage("tokens", iter_total)
        if self.usage_tracker:
            self.usage_tracker.record_simple(self.model_name, iter_total)
        _gov_logger.debug(
            "agentic_loop_token_usage",
            extra={
                "iteration": iteration + 1,
                "iter_tokens": iter_total,
                "total_est_tokens": total_est_tokens,
            },
        )

        # Check if response has tool calls
        if not result.tool_calls:
            # Text response — we're done
            text = result.text or ""
            if tool_receipts:
                _gov_logger.debug(
                    "agentic_loop_completed",
                    extra={"tool_call_count": len(tool_receipts)},
                )

            # Emit quest_completed
            if self.toolflow_emitter and tool_receipts:
                _ok = sum(1 for r in tool_receipts if r.get("result") == "SUCCESS")
                _dur = int(_time.time() * 1000) - _agentic_start_ms
                self.toolflow_emitter.quest_completed(
                    _quest_id, len(tool_receipts), _ok, _dur, _channel,
                )

            # Detect narration-without-content after tool-heavy loops.
            # When the model says "Let me compile..." instead of producing
            # the actual report, force a fresh synthesis call with the full
            # conversation context (all tool results are in `messages`).
            if tool_receipts and len(tool_receipts) >= 3 and self._is_narration_without_content(text):
                _gov_logger.debug(
                    "narration_without_content_detected",
                    extra={
                        "response_chars": len(text),
                        "tool_call_count": len(tool_receipts),
                    },
                )
                synthesis_text = self._force_synthesis(
                    messages, result.raw, system_instruction, prompt
                )
                if synthesis_text and len(synthesis_text) > len(text):
                    text = synthesis_text
                    _gov_logger.debug(
                        "synthesis_improved_response",
                        extra={"response_chars": len(text)},
                    )
                else:
                    _gov_logger.debug("synthesis_kept_original_response")

            # Structured output — reformat via a separate generate call
            # (structured output can't be combined with generate_with_tools)
            if _use_structured_output and text and tool_receipts:
                try:
                    from response.presenter import ResponsePresenter, AGENTIC_RESPONSE_SCHEMA, parse_structured_response
                    # Build a reformat prompt with the raw response + receipt summary
                    _receipt_summary = "\n".join(
                        f"- {r['skill']}: {r.get('result', 'unknown')}"
                        for r in tool_receipts
                    )
                    _reformat_prompt = (
                        f"Reformat this response into the required JSON schema. "
                        f"Only include actions that appear in the ACTUAL TOOL RECEIPTS below.\n\n"
                        f"TOOL RECEIPTS (ground truth — only these actions happened):\n{_receipt_summary}\n\n"
                        f"ORIGINAL RESPONSE:\n{text}"
                    )
                    _reformat_msg = self._build_frontier_user_message(_reformat_prompt)
                    _reformat_result = self._provider_generate(
                        model=self._route_model(prompt),
                        messages=[_reformat_msg],
                        system_instruction="You reformat text into JSON. Only include actions verified by tool receipts.",
                        config={
                            "response_mime_type": "application/json",
                            "response_schema": AGENTIC_RESPONSE_SCHEMA,
                        },
                    )
                    structured = parse_structured_response(_reformat_result.text)
                    if structured:
                        presenter = ResponsePresenter(claim_verification=FEATURE_CLAIM_VERIFICATION)
                        presented = presenter.present(structured, tool_receipts)
                        _gov_logger.debug(
                            "structured_reformat_succeeded",
                            extra={"presented_chars": len(presented)},
                        )
                        return presented
                    else:
                        _gov_logger.debug("structured_reformat_parse_failed")
                except Exception as e:
                    _gov_logger.warning(
                        "structured_reformat_failed",
                        extra={"error": str(e)},
                    )

            # Claim verification on raw text (no structured output needed)
            if FEATURE_CLAIM_VERIFICATION and text:
                try:
                    from response.presenter import ResponsePresenter
                    presenter = ResponsePresenter(claim_verification=True)
                    text = presenter.present_fallback(text, tool_receipts)
                except Exception as e:
                    _gov_logger.warning(
                        "claim_verification_failed",
                        extra={"error": str(e)},
                    )

            # Strip failure narration from final response (legacy fallback)
            text = self._strip_failure_narration(text)
            return text

        # Append model's response to conversation (provider-native format)
        # Strip non-message fields (e.g. thinking) before sending back to API
        raw_msg = result.raw
        if isinstance(raw_msg, dict):
            raw_msg = {k: v for k, v in raw_msg.items() if k in ("role", "content")}
        if isinstance(raw_msg, list):
            messages.extend(raw_msg)
        else:
            messages.append(raw_msg)

        # Process ALL tool calls and collect results.
        # Hallucination guard — derived from actual declarations so
        # new tools (builtins or dynamic skills) are automatically allowed.
        _DECLARED_TOOL_NAMES = {d.name for d in declarations}

        tool_results = []  # list of (call_id, fn_name, result_json_str)
        for tc in result.tool_calls:
            skill_name = tc.name
            inputs = tc.args
            _gov_logger.debug(
                "tool_call_started",
                extra={
                    "skill": skill_name,
                    "tool_call_id": tc.id,
                },
            )

            # Emit tool_call_started before safety/execution
            if self.toolflow_emitter:
                self.toolflow_emitter.tool_call_started(
                    _quest_id, iteration + 1, skill_name, inputs, _channel,
                )

            # Guard against hallucinated tool names
            if skill_name not in _DECLARED_TOOL_NAMES:
                result_data = {
                    "error": f"Tool '{skill_name}' does not exist. "
                    f"Available tools: {', '.join(sorted(_DECLARED_TOOL_NAMES))}. "
                    "If this is a conversational request, respond directly without tools."
                }
                tool_receipts.append({
                    "skill": skill_name,
                    "inputs": inputs,
                    "result": f"REJECTED — undeclared tool '{skill_name}'",
                })
                # Emit tool_call_completed for rejected call
                if self.toolflow_emitter:
                    self.toolflow_emitter.tool_call_completed(
                        _quest_id, iteration + 1, skill_name,
                        "REJECTED", "undeclared tool", _channel,
                    )
                _gov_logger.warning(
                    "hallucinated_tool_call_rejected",
                    extra={"skill": skill_name},
                )
                tool_results.append((tc.id, skill_name, str(result_data)))
                continue

            # Safety classification
            safety = self._classify_tool_call_safety(skill_name, inputs)
            sentry_req_id = None
            sentry_blocked = False

            # MCP Sentry gate: all escalated ops require sentry approval
            if safety == "escalate":
                if hasattr(self, 'sentry') and self.sentry is not None:
                    try:
                        from mcp_sentry import MCPSentry
                        if isinstance(self.sentry, MCPSentry):
                            perm = self.sentry.check_permission(skill_name, inputs)
                            sentry_req_id = perm.get("request_id")
                            if perm["status"] == "APPROVED":
                                safety = "auto"  # Pre-approved — allow execution
                            elif perm["status"] == "PENDING":
                                sentry_blocked = True
                    except Exception as exc:
                        _logging.warning("MCP Sentry check failed for flagship agentic tool %s: %s", skill_name, exc)
                elif not allow_writes:
                    sentry_blocked = True

            if sentry_blocked:
                if FEATURE_DEEP_REASONING_LOOP:
                    # Governed Negotiation — structured feedback (Phase 3)
                    from src.core.reasoning_artifact import GovernanceFeedback
                    feedback = GovernanceFeedback(
                        skill_name=skill_name,
                        action_detail=str(inputs)[:200],
                        blocked_reason="Requires Commander approval" if not allow_writes else "Escalated by security classification",
                        permission_state="PENDING" if sentry_req_id else "DENIED",
                        trust_record_summary=self._get_trust_summary(skill_name, inputs),
                        alternatives=self._suggest_alternatives(skill_name, inputs),
                        resolution_hint="Commander can approve in War Room > Governance Dashboard",
                        request_id=sentry_req_id or "",
                    )
                    result_data = {"governance_feedback": feedback.to_tool_result()}
                else:
                    # Legacy behavior
                    escalation_msg = (
                        f"BLOCKED: {skill_name} requires Commander approval. "
                        "Approve in the War Room Governance Dashboard."
                    )
                    if sentry_req_id:
                        escalation_msg += f" (Approval ID: {sentry_req_id})"
                    result_data = {"error": escalation_msg}
                tool_receipts.append({
                    "skill": skill_name,
                    "inputs": inputs,
                    "result": "ESCALATED — needs Commander approval",
                    "approval_id": sentry_req_id,
                })
                # Emit tool_call_blocked
                if self.toolflow_emitter:
                    self.toolflow_emitter.tool_call_blocked(
                        _quest_id, iteration + 1, skill_name,
                        sentry_req_id or "", _channel,
                    )
                # Create ActionCard for approval
                if self.actioncard_factory:
                    try:
                        self.actioncard_factory.from_sentry_request(
                            req_id=sentry_req_id or f"block-{skill_name}-{_quest_id[:8]}",
                            tool_name=skill_name,
                            params=inputs or {},
                            quest_id=_quest_id,
                        )
                    except Exception as _ac_exc:
                        _gov_logger.warning(
                            "action_card_creation_failed",
                            extra={
                                "context": "agentic_loop",
                                "error": str(_ac_exc),
                            },
                        )
            else:
                # Execute the skill
                self.governor.log_usage("tool_calls", 1)
                _exec_success = False
                try:
                    exec_result = self.skill_executor.run(skill_name, inputs)
                    if exec_result.success:
                        _exec_success = True
                        result_data = exec_result.outputs or {"status": "success"}
                        # Inject download URL for document_creator results
                        if skill_name == "document_creator" and result_data.get("path"):
                            doc_abs = result_data["path"]
                            _ws = os.getenv("LANCELOT_WORKSPACE", "/home/lancelot/workspace")
                            doc_rel = doc_abs.replace(f"{_ws}/", "").lstrip("/")
                            _dl_url = f"/api/files/{doc_rel}"
                            result_data["download_url"] = _dl_url
                            result_data["download_note"] = (
                                f"Document created. Include this link in your response so "
                                f"the user can download it: [Download {Path(doc_abs).name}]({_dl_url})"
                            )
                        result_str = str(result_data)
                        if len(result_str) > 8000:
                            result_data = {"truncated": result_str[:8000] + "... [truncated]"}
                        tool_receipts.append({
                            "skill": skill_name,
                            "inputs": inputs,
                            "result": "SUCCESS",
                            "outputs": result_data,
                        })
                    else:
                        # Nudge model to silently retry with alternative
                        err_msg = exec_result.error or "Unknown error"
                        result_data = {
                            "error": err_msg,
                            "instruction": "Tool failed. Try an alternative approach immediately — do NOT narrate the failure or say 'let me try'. Just call the next tool.",
                        }
                        tool_receipts.append({
                            "skill": skill_name,
                            "inputs": inputs,
                            "result": f"FAILED: {err_msg}",
                        })
                except Exception as e:
                    result_data = {
                        "error": str(e),
                        "instruction": "Tool failed. Try an alternative approach immediately — do NOT narrate the failure or say 'let me try'. Just call the next tool.",
                    }
                    tool_receipts.append({
                        "skill": skill_name,
                        "inputs": inputs,
                        "result": f"EXCEPTION: {e}",
                    })

                # Emit tool_call_completed with status from the last receipt
                if self.toolflow_emitter and tool_receipts:
                    _last = tool_receipts[-1]
                    _result_status = _last.get("result", "UNKNOWN")
                    _out_summary = str(_last.get("outputs", ""))[:200] if _exec_success else ""
                    self.toolflow_emitter.tool_call_completed(
                        _quest_id, iteration + 1, skill_name,
                        _result_status, _out_summary, _channel,
                    )

                # Record governance event for trust ledger tracking
                try:
                    from governance.models import RiskTier as _GovRiskTier
                    _SKILL_TIER_MAP = {
                        "network_client": _GovRiskTier.T2_CONTROLLED,
                        "command_runner": _GovRiskTier.T2_CONTROLLED,
                        "repo_writer": _GovRiskTier.T1_REVERSIBLE,
                        "service_runner": _GovRiskTier.T2_CONTROLLED,
                    }
                    _gov_tier = _SKILL_TIER_MAP.get(skill_name, _GovRiskTier.T0_INERT)
                    _gov_scope = str(inputs.get("url", inputs.get("command", inputs.get("path", "default"))))
                    self._record_governance_event(skill_name, _gov_scope, _gov_tier, _exec_success)
                except Exception as exc:
                    _logging.warning("Failed to record governance event for flagship agentic tool %s: %s", skill_name, exc)

            tool_results.append((tc.id, skill_name, str(result_data)))

        # Feed ALL results back via provider's tool response builder
        tool_response_msg = self._build_frontier_tool_response_message(tool_results)
        if isinstance(tool_response_msg, list):
            messages.extend(tool_response_msg)
        else:
            messages.append(tool_response_msg)

        # If any tool calls failed, inject a nudge message telling the
        # model to silently retry — not narrate the failure to the user.
        _batch_had_failures = any(
            "FAILED" in str(r.get("result", "")) or "EXCEPTION" in str(r.get("result", ""))
            for r in tool_receipts[-len(result.tool_calls):]
        )
        if _batch_had_failures:
            _nudge = self._build_frontier_user_message(
                "[SYSTEM] A tool failed. Try an alternative tool or approach NOW. "
                "Do NOT tell the user about the failure or say 'let me try'. "
                "Just silently use the next tool. If no alternatives exist, "
                "report what you WERE able to accomplish."
            )
            messages.append(_nudge)
            _gov_logger.debug("silent_retry_nudge_injected")

        # After first tool call(s), switch from ANY back to AUTO
        # so the model can return text on subsequent iterations.
        # If tool calls had HTTP errors on iteration 0, keep ANY
        # for one more iteration to encourage retries, then switch to AUTO.
        if force_tool_use and iteration <= 1 and tool_receipts:
            should_retry = False
            if iteration == 0:
                # Check if current batch had HTTP errors
                batch = tool_receipts[-len(result.tool_calls):]
                has_http_error = any(
                    (isinstance(r.get("result"), str) and "FAILED" in r.get("result", ""))
                    or (isinstance(r.get("outputs"), dict) and r["outputs"].get("error"))
                    for r in batch
                )
                if has_http_error:
                    should_retry = True
                    _gov_logger.warning("forced_tool_use_retry_due_to_http_error")

            if not should_retry:
                current_tool_config = None  # Back to AUTO (default)
                if iteration == 0:
                    _gov_logger.debug(
                        "tool_mode_reset_to_auto",
                        extra={"reason": "first_tool_call"},
                    )
                else:
                    _gov_logger.debug(
                        "tool_mode_reset_to_auto",
                        extra={"reason": "retry_iteration"},
                    )

    # Max iterations reached — model never returned a text response
    _gov_logger.warning(
        "agentic_loop_max_iterations_reached",
        extra={"max_iterations": MAX_ITERATIONS},
    )

    # Emit quest_completed even on max-iterations
    if self.toolflow_emitter and tool_receipts:
        _ok = sum(1 for r in tool_receipts if r.get("result") == "SUCCESS")
        _dur = int(_time.time() * 1000) - _agentic_start_ms
        self.toolflow_emitter.quest_completed(
            _quest_id, len(tool_receipts), _ok, _dur, _channel,
        )

    # When structured output is enabled, try to produce a clean
    # summary via the presenter instead of raw receipt list
    if _use_structured_output and tool_receipts:
        try:
            from response.presenter import ResponsePresenter, AGENTIC_RESPONSE_SCHEMA, parse_structured_response
            _receipt_summary = "\n".join(
                f"- {r['skill']}: {r.get('result', 'unknown')}"
                for r in tool_receipts
            )
            _summary_prompt = (
                f"Summarize what was accomplished based on these tool receipts. "
                f"Be concise. Only claim actions that appear in the receipts.\n\n"
                f"TOOL RECEIPTS:\n{_receipt_summary}"
            )
            _summary_msg = self._build_frontier_user_message(_summary_prompt)
            _summary_result = self._provider_generate(
                model=self._route_model(prompt),
                messages=[_summary_msg],
                system_instruction="Summarize tool execution results concisely. Only mention actions in the receipts.",
                config={
                    "response_mime_type": "application/json",
                    "response_schema": AGENTIC_RESPONSE_SCHEMA,
                },
            )
            structured = parse_structured_response(_summary_result.text)
            if structured:
                presenter = ResponsePresenter(claim_verification=FEATURE_CLAIM_VERIFICATION)
                presented = presenter.present(structured, tool_receipts)
                _gov_logger.debug(
                    "max_iterations_summary_rendered",
                    extra={"presented_chars": len(presented)},
                )
                return presented
        except Exception as e:
            _gov_logger.warning(
                "max_iterations_presenter_failed",
                extra={"error": str(e)},
            )

    return self._format_tool_receipts(
        tool_receipts,
        note="Reached maximum tool call limit. Here's what I found so far:",
    )

def _local_agentic_generate(
    self,
    prompt: str,
    system_instruction: str = None,
    allow_writes: bool = False,
    context_str: str = None,
) -> str:
    """Agentic loop using local model for simple tool calls.

    Uses the local model through chat completions and OpenAI-format tool calling.
    Lower max iterations (5 vs 10) since local queries are simpler.

    Returns:
        The final text response from the local model.
    """
    MAX_LOCAL_ITERATIONS = 5

    if not self.local_model:
        _gov_logger.debug(
            "local_agentic_fallback_to_flagship",
            extra={"reason": "local_model_unavailable"},
        )
        return self._agentic_generate(
            prompt=prompt,
            system_instruction=system_instruction,
            allow_writes=allow_writes,
            context_str=context_str,
        )

    if not self.local_model.is_healthy():
        _gov_logger.debug(
            "local_agentic_fallback_to_flagship",
            extra={"reason": "local_model_unhealthy"},
        )
        return self._agentic_generate(
            prompt=prompt,
            system_instruction=system_instruction,
            allow_writes=allow_writes,
            context_str=context_str,
        )

    from feature_flags import FEATURE_DEEP_REASONING_LOOP
    tools = self._build_openai_tool_declarations()

    # Local model has a 4K context window. Budget increased from
    # 2500→4000 chars. Use a condensed but informative system prompt that
    # includes core capabilities so the model doesn't hallucinate.
    _LOCAL_CTX_BUDGET = 4000  # chars (~1000 tokens), leaves room for tools + response
    ctx = context_str or self.context_env.get_context_string()
    if len(ctx) > _LOCAL_CTX_BUDGET:
        ctx = ctx[-_LOCAL_CTX_BUDGET:]  # Keep most recent context
        _gov_logger.debug(
            "local_context_truncated",
            extra={"context_chars": len(ctx)},
        )
    sys_msg = (
        "You are Lancelot, an autonomous AI agent. Answer the user concisely. "
        "Use tools when needed. Never claim to have done something you haven't actually done via a tool call. "
        "You have access to tools including: schedule_job, network_client, file_operations, memory. "
        "When the user refers to a previous message or adds to a prior request, use the conversation "
        "history in context to understand what they mean."
    )

    messages = [
        {"role": "system", "content": sys_msg},
        {"role": "user", "content": f"{ctx}\n\n{prompt}"},
    ]

    tool_receipts = []
    total_est_tokens = 0

    for iteration in range(MAX_LOCAL_ITERATIONS):
        _gov_logger.debug(
            "local_agentic_iteration_started",
            extra={
                "iteration": iteration + 1,
                "max_iterations": MAX_LOCAL_ITERATIONS,
            },
        )

        try:
            result = self.local_model.chat_with_tools(
                messages=messages,
                tools=tools,
                max_tokens=512,
                temperature=0.1,
            )
        except Exception as e:
            _gov_logger.warning(
                "local_agentic_model_call_failed",
                extra={
                    "iteration": iteration + 1,
                    "error": str(e),
                },
            )
            if tool_receipts:
                return self._format_tool_receipts(tool_receipts, error=str(e))
            # Fall back to flagship model
            _gov_logger.info(
                "local_agentic_fallback_to_flagship",
                extra={"reason": "local_model_error"},
            )
            return self._agentic_generate(
                prompt=prompt,
                system_instruction=system_instruction,
                allow_writes=allow_writes,
                context_str=context_str,
            )

        # Parse response
        choices = result.get("choices", [])
        if not choices:
            _gov_logger.warning("local_agentic_no_choices")
            return "Error: Local model returned no response."

        choice = choices[0]
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "stop")

        # Track token usage
        usage = result.get("usage", {})
        iter_tokens = usage.get("total_tokens", 200)
        total_est_tokens += iter_tokens
        self.governor.log_usage("tokens", iter_tokens)
        if self.usage_tracker:
            self.usage_tracker.record_simple("local-llm", iter_tokens)
        _gov_logger.debug(
            "local_agentic_token_usage",
            extra={
                "iteration": iteration + 1,
                "iter_tokens": iter_tokens,
                "total_est_tokens": total_est_tokens,
            },
        )

        # Check for tool calls
        tool_calls = message.get("tool_calls")

        if not tool_calls or finish_reason == "stop":
            # Text response — we're done
            text = message.get("content", "")
            if tool_receipts:
                _gov_logger.debug(
                    "local_agentic_completed",
                    extra={"tool_call_count": len(tool_receipts)},
                )
            return text or "No response from local model."

        # Append assistant message to conversation
        # Ensure content is "" not None — llama-cpp-python can't iterate None
        if message.get("content") is None:
            message["content"] = ""
        messages.append(message)

        # Execute each tool call
        for tc in tool_calls:
            func = tc.get("function", {})
            skill_name = func.get("name", "")
            try:
                import json as _json
                inputs = _json.loads(func.get("arguments", "{}"))
            except (ValueError, TypeError):
                inputs = {}

            tc_id = tc.get("id", f"call_{iteration}_{skill_name}")
            _gov_logger.debug(
                "local_tool_call_started",
                extra={
                    "skill": skill_name,
                    "tool_call_id": tc_id,
                },
            )

            # Safety classification
            safety = self._classify_tool_call_safety(skill_name, inputs)
            sentry_req_id = None
            sentry_blocked = False

            # MCP Sentry gate: all escalated ops require sentry approval
            if safety == "escalate":
                if hasattr(self, 'sentry') and self.sentry is not None:
                    try:
                        from mcp_sentry import MCPSentry
                        if isinstance(self.sentry, MCPSentry):
                            perm = self.sentry.check_permission(skill_name, inputs)
                            sentry_req_id = perm.get("request_id")
                            if perm["status"] == "APPROVED":
                                safety = "auto"
                            elif perm["status"] == "PENDING":
                                sentry_blocked = True
                    except Exception as exc:
                        _logging.warning("MCP Sentry check failed for local agentic tool %s: %s", skill_name, exc)
                elif not allow_writes:
                    sentry_blocked = True

            if sentry_blocked:
                if FEATURE_DEEP_REASONING_LOOP:
                    # Governed Negotiation — structured feedback (Phase 3)
                    from src.core.reasoning_artifact import GovernanceFeedback
                    feedback = GovernanceFeedback(
                        skill_name=skill_name,
                        action_detail=str(inputs)[:200],
                        blocked_reason="Requires Commander approval",
                        permission_state="PENDING" if sentry_req_id else "DENIED",
                        trust_record_summary=self._get_trust_summary(skill_name, inputs),
                        alternatives=self._suggest_alternatives(skill_name, inputs),
                        resolution_hint="Commander can approve in War Room > Governance Dashboard",
                        request_id=sentry_req_id or "",
                    )
                    result_content = feedback.to_tool_result()
                else:
                    result_content = f"BLOCKED: {skill_name} requires Commander approval. Approve in War Room."
                    if sentry_req_id:
                        result_content += f" (Approval ID: {sentry_req_id})"
                tool_receipts.append({
                    "skill": skill_name,
                    "inputs": inputs,
                    "result": "ESCALATED — needs Commander approval",
                    "approval_id": sentry_req_id,
                })
                # Create ActionCard for approval
                if self.actioncard_factory:
                    try:
                        _quest_id_here = getattr(self, "_current_quest_id", None) or ""
                        self.actioncard_factory.from_sentry_request(
                            req_id=sentry_req_id or f"block-{skill_name}",
                            tool_name=skill_name,
                            params=inputs or {},
                            quest_id=_quest_id_here,
                        )
                    except Exception as _ac_exc:
                        _gov_logger.warning(
                            "action_card_creation_failed",
                            extra={
                                "context": "local_agentic",
                                "error": str(_ac_exc),
                            },
                        )
            else:
                # Execute the skill
                self.governor.log_usage("tool_calls", 1)
                _exec_success = False
                try:
                    skill_result = self.skill_executor.run(skill_name, inputs)
                    if skill_result.success:
                        _exec_success = True
                        result_content = str(skill_result.outputs or {"status": "success"})
                        if len(result_content) > 4000:
                            result_content = result_content[:4000] + "... [truncated]"
                        tool_receipts.append({
                            "skill": skill_name,
                            "inputs": inputs,
                            "result": "SUCCESS",
                            "outputs": skill_result.outputs,
                        })
                    else:
                        result_content = f"Error: {skill_result.error}"
                        tool_receipts.append({
                            "skill": skill_name,
                            "inputs": inputs,
                            "result": f"FAILED: {skill_result.error}",
                        })
                except Exception as e:
                    result_content = f"Exception: {e}"
                    tool_receipts.append({
                        "skill": skill_name,
                        "inputs": inputs,
                        "result": f"EXCEPTION: {e}",
                    })

                # Record governance event for trust ledger tracking
                try:
                    from governance.models import RiskTier as _GovRiskTier
                    _SKILL_TIER_MAP = {
                        "network_client": _GovRiskTier.T2_CONTROLLED,
                        "command_runner": _GovRiskTier.T2_CONTROLLED,
                        "repo_writer": _GovRiskTier.T1_REVERSIBLE,
                        "service_runner": _GovRiskTier.T2_CONTROLLED,
                    }
                    _gov_tier = _SKILL_TIER_MAP.get(skill_name, _GovRiskTier.T0_INERT)
                    _gov_scope = str(inputs.get("url", inputs.get("command", inputs.get("path", "default"))))
                    self._record_governance_event(skill_name, _gov_scope, _gov_tier, _exec_success)
                except Exception as exc:
                    _logging.warning("Failed to record governance event for local agentic tool %s: %s", skill_name, exc)

            # Feed tool result back as tool message (OpenAI format)
            messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": result_content,
            })

    # Max iterations reached
    _gov_logger.warning(
        "local_agentic_max_iterations_reached",
        extra={"max_iterations": MAX_LOCAL_ITERATIONS},
    )
    return self._format_tool_receipts(
        tool_receipts,
        note="Reached maximum local tool call limit. Here's what I found:",
    )

def _execute_with_llm(self, graph, user_text: str = "") -> str:
    """Use Gemini to execute approved plan steps and produce actionable content.

    Called after the user approves a plan. Uses execution-mode system
    instruction (no honesty blocks) and bypasses the honesty gate,
    applying only tool-scaffolding cleanup.

    When the plan is already approved, this path allows the agentic loop to execute the required skills with writes enabled.

    Returns the LLM-generated content string, or empty string on failure.
    """
    if not self.provider:
        return ""

    steps_text = "\n".join(
        f"- {s.inputs.get('description', s.type)}" for s in graph.steps
    )
    goal = graph.goal or user_text

    # Include recent conversation history so execution honors user corrections made after plan approval.
    recent_history = self.context_env.get_history_string(limit=12)
    history_block = ""
    if recent_history:
        history_block = f"\n\nRECENT CONVERSATION (includes user corrections):\n{recent_history}\n"

    prompt = (
        f"The user asked: \"{goal}\"\n\n"
        f"Original plan:\n{steps_text}\n"
        f"{history_block}\n"
        "EXECUTION RULES — YOU MUST FOLLOW THESE:\n"
        "1. You ARE Lancelot — a governed autonomous system deployed on Telegram.\n"
        "2. When the user says 'us' or 'we', that includes YOU.\n"
        "3. If the user corrected the plan in the conversation above, follow their correction — NOT the original plan.\n"
        "4. You MUST use your tools to execute each step. For example:\n"
        "   - Use network_client (method=GET) to fetch API docs, check endpoints, research\n"
        "   - Use command_runner to run shell commands, check system state\n"
        "   - Use repo_writer to create/edit configuration files\n"
        "   - Use service_runner to manage Docker services\n"
        "5. Do NOT just describe what you would do — actually CALL the tools.\n"
        "6. Do NOT claim you have accomplished something unless you called a tool and got a result.\n"
        "7. After executing steps with tools, summarize what you ACTUALLY did and what the results were.\n"
        "8. If a step requires information, fetch it with network_client first.\n"
        "9. Be direct and concise. Max 10-15 lines in your final summary."
    )

    try:
        # Use execution-mode instruction (no honesty blocks).
        system_instruction = self._build_execution_instruction()

        # Force at least one tool call when executing an approved plan through the agentic loop.
        from feature_flags import FEATURE_AGENTIC_LOOP
        if FEATURE_AGENTIC_LOOP:
            _gov_logger.info(
                "approved_plan_execution_forces_tool_use"
            )
            result = self._agentic_generate(
                prompt=prompt,
                system_instruction=system_instruction,
                allow_writes=True,
                force_tool_use=True,
                skip_structured_reformat=True,
            )
        else:
            msg = self._build_frontier_user_message(
                f"{self.context_env.get_context_string()}\n\n{prompt}"
            )
            gen_result = self._llm_call_with_retry(
                lambda: self._provider_generate(
                    model=self._route_model(goal),
                    messages=[msg],
                    system_instruction=system_instruction,
                    config={"thinking": self._get_thinking_config()},
                )
            )
            result = gen_result.text if gen_result.text else ""

        # Strip tool scaffolding but bypass honesty gate
        from response.policies import OutputPolicy
        result = OutputPolicy.strip_tool_scaffolding(result)
        _gov_logger.info("LLM execution produced %d chars of content", len(result))
        return result
    except Exception as e:
        _gov_logger.warning("LLM execution failed: %s", e)
        return ""

def execute_plan(self, plan) -> str:
    """S17: Executes a plan autonomously with risk-tiered governance.

    vNext4: Full risk-tiered pipeline:
      T0: Policy cache → Execute → Batch receipt
      T1: Policy cache → Snapshot → Execute → Async verify → Receipt
      T2: Flush + Drain → Execute → Sync verify → Receipt
      T3: Flush + Drain → Approval → Execute → Sync verify → Receipt

    When FEATURE_RISK_TIERED_GOVERNANCE is False, uses legacy behavior.
    """
    self.wake_up("Plan Execution")
    results = []
    plan_id = getattr(plan, "plan_id", str(uuid.uuid4()))

    # vNext4: Initialize batch buffer if enabled
    batch_buffer = None
    if _GOVERNANCE_AVAILABLE and _ff.FEATURE_RISK_TIERED_GOVERNANCE and _ff.FEATURE_BATCH_RECEIPTS:
        try:
            from governance.batch_receipts import BatchReceiptBuffer
            from governance.config import BatchReceiptConfig
            batch_buffer = BatchReceiptBuffer(
                task_id=plan_id,
                data_dir=os.path.join(self.data_dir, "governance"),
            )
        except Exception as e:
            _gov_logger.warning("Batch receipt init failed: %s", e)

    for i, step in enumerate(plan.steps):
        _gov_logger.info(
            "autonomous_mission_step_started",
            extra={
                "step_id": step.id,
                "description": step.description,
                "step_index": i + 1,
            },
        )
        params = {p.key: p.value for p in step.params}
        capability = _TOOL_CAPABILITY_MAP.get(step.tool, step.tool)
        target = params.get("path", params.get("dir", ""))

        # ── Legacy path when governance is disabled ─────────────
        if not _GOVERNANCE_AVAILABLE or not _ff.FEATURE_RISK_TIERED_GOVERNANCE or self._risk_classifier is None:
            try:
                output = self._execute_step_tool(step, params)
            except Exception as e:
                output = f"Execution Error: {e}"
            verification = self.verifier.verify_step(step.description, output)
            self._record_governance_event(capability, target, 0, verification.success)
            results.append(f"Step {step.id}: {verification.success} ({verification.reason})")
            if not verification.success:
                return f"Plan Failed at Step {step.id}.\nReason: {verification.reason}\nSuggestion: {verification.correction_suggestion}"
            continue

        # ── vNext4: Classify risk tier ──────────────────────────
        try:
            profile = self._risk_classifier.classify(capability, target=target)
        except Exception as e:
            _gov_logger.warning("Risk classification failed for step %s: %s", step.id, e)
            profile = None

        tier = profile.tier if profile else RiskTier.T3_IRREVERSIBLE

        # ═══════════════════════════════════════════════════════
        # T0: INERT — Policy cache → Execute → Batch receipt
        # ═══════════════════════════════════════════════════════
        if tier == RiskTier.T0_INERT:
            # Policy cache check
            if _ff.FEATURE_POLICY_CACHE and hasattr(self, '_policy_cache') and self._policy_cache:
                cached = self._policy_cache.lookup(capability, target or "workspace")
                if cached and cached.decision == "deny":
                    results.append(f"Step {step.id}: BLOCKED by policy cache ({capability})")
                    return f"Plan Blocked at Step {step.id}: Policy denied {capability}"

            try:
                output = self._execute_step_tool(step, params)
            except Exception as e:
                output = f"Execution Error: {e}"

            # Batch receipt
            if batch_buffer:
                batch_buffer.append(
                    capability, step.tool, RiskTier.T0_INERT,
                    str(params), output, "Error" not in output,
                )
            self._record_governance_event(capability, target, RiskTier.T0_INERT, "Error" not in output)
            results.append(f"Step {step.id}: T0 executed ({capability})")

        # ═══════════════════════════════════════════════════════
        # T1: REVERSIBLE — Snapshot → Execute → Async verify
        # ═══════════════════════════════════════════════════════
        elif tier == RiskTier.T1_REVERSIBLE:
            # Policy cache check
            if _ff.FEATURE_POLICY_CACHE and hasattr(self, '_policy_cache') and self._policy_cache:
                cached = self._policy_cache.lookup(capability, target or "workspace")
                if cached and cached.decision == "deny":
                    results.append(f"Step {step.id}: BLOCKED by policy cache ({capability})")
                    return f"Plan Blocked at Step {step.id}: Policy denied {capability}"

            snapshot = None
            if self._rollback_manager:
                snapshot = self._rollback_manager.create_snapshot(
                    task_id=plan_id, step_index=i,
                    capability=capability, target=target,
                )

            try:
                output = self._execute_step_tool(step, params)
            except Exception as e:
                output = f"Execution Error: {e}"

            if _ff.FEATURE_ASYNC_VERIFICATION and self._async_queue and snapshot:
                rollback_action = self._rollback_manager.get_rollback_action(snapshot.snapshot_id)
                self._async_queue.submit(VerificationJob(
                    task_id=plan_id, step_index=i,
                    capability=capability,
                    goal=step.description,
                    output=output,
                    rollback_action=rollback_action,
                ))
                results.append(f"Step {step.id}: T1 async-queued ({capability})")
            else:
                # Sync verify fallback
                verification = self.verifier.verify_step(step.description, output)
                self._record_governance_event(capability, target, RiskTier.T1_REVERSIBLE, verification.success)
                results.append(f"Step {step.id}: T1 sync-verified {verification.success} ({capability})")
                if not verification.success:
                    if snapshot and self._rollback_manager:
                        self._rollback_manager.get_rollback_action(snapshot.snapshot_id)()
                    return f"Plan Failed at Step {step.id}.\nReason: {verification.reason}"

        # ═══════════════════════════════════════════════════════
        # T2: CONTROLLED — Flush + Drain → Execute → Sync verify
        # ═══════════════════════════════════════════════════════
        elif tier == RiskTier.T2_CONTROLLED:
            # Boundary enforcement: flush batch + drain async queue
            if batch_buffer:
                batch_buffer.flush_if_tier_boundary(RiskTier.T2_CONTROLLED)
            if _ff.FEATURE_ASYNC_VERIFICATION and self._async_queue:
                drain_result = self._async_queue.drain()
                if drain_result.failed > 0:
                    self._async_queue.clear_results()
                    results.append(f"Step {step.id}: BLOCKED — {drain_result.failed} prior verification failures")
                    return f"Plan Failed: {drain_result.failed} prior T1 verification failures detected before T2 step {step.id}"
                self._async_queue.clear_results()

            try:
                output = self._execute_step_tool(step, params)
            except Exception as e:
                output = f"Execution Error: {e}"

            verification = self.verifier.verify_step(step.description, output)
            self._record_governance_event(capability, target, RiskTier.T2_CONTROLLED, verification.success)
            results.append(f"Step {step.id}: T2 sync-verified {verification.success} ({capability})")
            if not verification.success:
                return f"Plan Failed at Step {step.id}.\nReason: {verification.reason}\nSuggestion: {verification.correction_suggestion}"

        # ═══════════════════════════════════════════════════════
        # T3: IRREVERSIBLE — Flush + Drain → Approval → Execute → Sync verify
        # ═══════════════════════════════════════════════════════
        elif tier == RiskTier.T3_IRREVERSIBLE:
            # Boundary enforcement
            if batch_buffer:
                batch_buffer.flush_if_tier_boundary(RiskTier.T3_IRREVERSIBLE)
            if _ff.FEATURE_ASYNC_VERIFICATION and self._async_queue:
                drain_result = self._async_queue.drain()
                if drain_result.failed > 0:
                    self._async_queue.clear_results()
                    results.append(f"Step {step.id}: BLOCKED — prior verification failures")
                    return f"Plan Failed: {drain_result.failed} prior T1 verification failures detected before T3 step {step.id}"
                self._async_queue.clear_results()

            # Approval gate
            if not self._request_approval(step, profile):
                results.append(f"Step {step.id}: APPROVAL DENIED ({capability})")
                return f"Plan Stopped at Step {step.id}: Commander approval denied for {capability}"

            try:
                output = self._execute_step_tool(step, params)
            except Exception as e:
                output = f"Execution Error: {e}"

            verification = self.verifier.verify_step(step.description, output)
            self._record_governance_event(capability, target, RiskTier.T3_IRREVERSIBLE, verification.success)
            results.append(f"Step {step.id}: T3 sync-verified {verification.success} ({capability})")
            if not verification.success:
                return f"Plan Failed at Step {step.id}.\nReason: {verification.reason}\nSuggestion: {verification.correction_suggestion}"

    # ── End-of-plan cleanup ─────────────────────────────────
    if batch_buffer:
        batch_buffer.flush()
    if _GOVERNANCE_AVAILABLE and self._async_queue is not None:
        if self._async_queue.depth > 0:
            drain_result = self._async_queue.drain()
            if drain_result.failed > 0:
                _gov_logger.warning(
                    "Async verification: %d/%d steps rolled back",
                    drain_result.failed, drain_result.drained_count,
                )
                results.append(
                    f"[vNext4] Async verification: {drain_result.passed} passed, "
                    f"{drain_result.failed} rolled back"
                )
        self._async_queue.clear_results()

    return "Plan Executed Successfully.\n" + "\n".join(results)

def _execute_command(self, command_parts: list) -> str:
    """Executes a CLI command safely (SafeREPL)."""
    cmd_str = " ".join(command_parts)
    base_cmd = command_parts[0].lower() if command_parts else ""

    # SafeREPL: Intercept Inspection Commands
    # These run directly in the python process, creating traceable receipts,
    # avoiding subprocess overhead and shell risks.
    
    if base_cmd in ["ls", "dir"]:
         target = command_parts[1] if len(command_parts) > 1 else "."
         return self.context_env.list_workspace(target)
         
    elif base_cmd in ["cat", "read", "type"]:
         if len(command_parts) < 2: return "Usage: cat <file>"
         return self.context_env.read_file(command_parts[1]) or "Error reading file."
         
    elif base_cmd in ["grep", "search"]:
         if len(command_parts) < 2: return "Usage: grep <query>"
         # Handle rough arg parsing if needed, for now just take the last arg as query?
         # Or assume "grep query" structure.
         return self.context_env.search_workspace(command_parts[1])
         
    elif base_cmd == "outline":
         if len(command_parts) < 2: return "Usage: outline <file>"
         return self.context_env.get_file_outline(command_parts[1])
         
    elif base_cmd == "diff":
         staged = "--cached" in cmd_str or "--staged" in cmd_str
         return self.context_env.get_workspace_diff(staged=staged)

    elif base_cmd == "cp":
         if len(command_parts) < 3: return "Usage: cp <src> <dst_folder>"
         return self.file_ops.safe_copy(command_parts[1], command_parts[2], f"CLI: {cmd_str}") or "Copy failed."
         
    elif base_cmd == "mv":
         if len(command_parts) < 3: return "Usage: mv <src> <dst_folder>"
         return self.file_ops.safe_move(command_parts[1], command_parts[2], f"CLI: {cmd_str}") or "Move failed."

    elif base_cmd == "rm":
         if len(command_parts) < 2: return "Usage: rm <file>"
         return self.file_ops.safe_delete(command_parts[1], f"CLI: {cmd_str}") or "Delete failed."
         
    elif base_cmd == "mkdir":
         if len(command_parts) < 2: return "Usage: mkdir <path>"
         return str(self.file_ops.safe_mkdir(command_parts[1], f"CLI: {cmd_str}"))
         
    elif base_cmd == "touch":
         if len(command_parts) < 2: return "Usage: touch <path>"
         return str(self.file_ops.touch(command_parts[1], f"CLI: {cmd_str}"))

    elif base_cmd == "sleep":
         self.enter_sleep()
         return "Entered SLEEP mode."
         
    elif base_cmd == "wake":
         self.wake_up("Manual CLI")
         return "Entered ACTIVE mode."

    # SENTRY: Permission Check for Subprocesses
    if self.sentry:
        perm = self.sentry.check_permission("cli_shell", {"command": cmd_str})
        if perm["status"] == "PENDING":
             return f"PERMISSION REQUIRED: {perm['message']} Request ID: {perm['request_id']}"
        elif perm["status"] == "DENIED":
             return f"ACCESS DENIED: {perm['message']}"

    # SECURITY: Audit Log
    self.audit_logger.log_command(cmd_str)

    # SECURITY: Network Check — scan all args for URLs
    for arg in command_parts:
        if "http://" in arg or "https://" in arg:
            if not self.network_interceptor.check_url(arg):
                return f"SECURITY BLOCK: Connection to {arg} denied."

    try:
        result = subprocess.run(
            command_parts,
            capture_output=True,
            text=True,
            check=True,
        )
        output = result.stdout.strip()

        # SENTRY: Log Execution
        if self.sentry:
            self.sentry.log_execution("cli_shell", {"command": cmd_str}, output)

        return output
    except subprocess.CalledProcessError as e:
        return f"Error executing command: {e.stderr}"
    except Exception as e:
        return f"Error executing command: {e}"
