"""Runtime-flow mixin methods for LancelotOrchestrator."""

from __future__ import annotations

import logging as _logging
import os
import re
import shlex
import sys
from typing import Optional

import chat_flow as _chat_flow_module
from chat_flow import chat as _chat_impl
from orchestrator_consts import COMMAND_BLACKLIST_CHARS, COMMAND_WHITELIST
from orchestrator_governance import (
    get_trust_summary as _get_trust_summary_impl,
    record_governance_event as _record_governance_event_impl,
    suggest_alternatives as _suggest_alternatives_impl,
)
from orchestrator_context import (
    init_context_cache as _init_context_cache_impl,
    log_rule_candidate as _log_rule_candidate_impl,
    query_memory as _query_memory_impl,
    update_rules as _update_rules_impl,
)
from orchestrator_provider import get_deep_model as _get_deep_model_impl, route_model as _route_model_impl
from orchestrator_response_delivery import (
    append_download_links as _append_download_links_impl,
    auto_create_document as _auto_create_document_impl,
    deliver_war_room_artifacts as _deliver_war_room_artifacts_impl,
    force_synthesis as _force_synthesis_impl,
    validate_llm_response as _validate_llm_response_impl,
)
from orch_helpers.safety_helpers import (
    generate_honest_replacement as _generate_honest_replacement_fn,
    is_narration_without_content as _is_narration_without_content_fn,
    strip_failure_narration as _strip_failure_narration_fn,
    validate_rule_content as _validate_rule_content_fn,
)
from receipts import ActionType, CognitionTier, create_finalized_receipt, create_receipt
from tool_loop import _execute_command as _execute_command_impl, execute_plan as _execute_plan_impl
from orchestrator_ext import _record_task_experience as _record_task_experience_impl

try:
    from action_language_gate import check_action_language
except ImportError:
    try:
        from src.core.action_language_gate import check_action_language
    except ImportError:
        check_action_language = None

_gov_logger = _logging.getLogger(__name__)


def _compat_impl(name: str, fallback):
    orchestrator_module = sys.modules.get("orchestrator") or sys.modules.get("src.core.orchestrator")
    return getattr(orchestrator_module, name, fallback) if orchestrator_module is not None else fallback


def _runtime_state(name: str):
    orchestrator_module = sys.modules.get("orchestrator") or sys.modules.get("src.core.orchestrator")
    runtime_state = getattr(orchestrator_module, "RuntimeState", None) if orchestrator_module is not None else None
    if runtime_state is None:
        raise RuntimeError("RuntimeState enum is unavailable")
    return getattr(runtime_state, name)


class OrchestratorRuntimeFlowMixin:
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
        return _compat_impl("_get_trust_summary_impl", _get_trust_summary_impl)(self, skill_name, inputs)

    def get_trust_summary(self, skill_name: str, inputs: dict) -> str:
        return self._get_trust_summary(skill_name, inputs)

    def _suggest_alternatives(self, skill_name: str, inputs: dict) -> list:
        return _compat_impl("_suggest_alternatives_impl", _suggest_alternatives_impl)(skill_name, inputs)

    def suggest_alternatives(self, skill_name: str, inputs: dict) -> list:
        return self._suggest_alternatives(skill_name, inputs)

    # End autonomy loop v2

    def _init_context_cache(self):
        return _init_context_cache_impl(self)

    def initialize_context_cache(self):
        return self._init_context_cache()

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

    def validate_rule_content(self, content: str) -> tuple:
        return self._validate_rule_content(content)

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

    def auto_create_document(self, content: str, title: str = "Research Report") -> str:
        return self._auto_create_document(content, title=title)

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

        # Format plan for display: human-readable only, no tool/param internals.
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
        so the plan pauses; the Commander can approve via the War Room
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
                    "T3 action requires approval: %s (request_id=%s); visible in War Room",
                    capability, perm.get("request_id", "?"),
                )
                return False

        _gov_logger.warning("T3 action requires approval: %s (auto-denied, no sentry)", step.tool)
        return False

    def execute_plan(self, plan) -> str:
        return _execute_plan_impl(self, plan)

    def _record_governance_event(self, capability: str, scope: str, tier, success: bool):
        return _record_governance_event_impl(self, capability, scope, tier, success)

    def record_governance_event(self, capability: str, scope: str, tier, success: bool):
        return self._record_governance_event(capability, scope, tier, success)

    def _get_deep_model(self) -> str:
        """Return the deep/reasoning model name with graceful fallback.

        Checks the provider profile first, then environment configuration,
        and finally falls back to the fast lane. The chosen model is validated
        before first use.
        """
        return _get_deep_model_impl(self)

    def get_deep_model(self) -> str:
        return self._get_deep_model()

    def _route_model(self, user_message: str) -> str:
        """Smart model routing: selects the best model for the task.

        Routes to deep model (e.g. gemini-2.5-pro) for complex reasoning tasks,
        and fast model (Flash) for everything else. This ensures Lancelot never
        'feels dumb' on hard questions while staying cost-efficient on simple ones.
        """
        return _route_model_impl(self, user_message)

    def route_model(self, user_message: str) -> str:
        return self._route_model(user_message)

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
        2. Check for structural fake work proposals; replace entire response
        3. Check individual forbidden phrases; replace if >= 2, strip if 1

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

        # Tier 2b: Action Language Gate; block execution claims
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
            # 2+ violations means systemic stalling; replace entire response.
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
        sleeping = _runtime_state("SLEEPING")
        if self.state == sleeping:
            return

        _gov_logger.info("Lancelot entering SLEEP mode...")
        # 1. Flush Context (keep only essential history)
        # self.context_env.clear_heavy_context() # Future optimization

        # 2. Log Event
        self.set_state(sleeping)

    def wake_up(self, reason: str = "Manual Trigger"):
        """Transitions agent to ACTIVE mode."""
        active = _runtime_state("ACTIVE")
        if self.state == active:
            return

        _gov_logger.info("Lancelot WAKING UP (%s)...", reason)
        self.set_state(active)
        # Refresh context or checks could go here

    def _execute_command(self, command_parts: list) -> str:
        return _execute_command_impl(self, command_parts)
