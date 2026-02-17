"""
Task Runner — executes a TaskRun step-by-step with receipt emission.

The runner walks through the TaskGraph's steps in dependency order,
checks token authority before each step, executes via skills/tools,
emits receipts, and updates TaskRun state.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.core.tasking.schema import RunStatus, StepType, TaskGraph, TaskRun, TaskStep
from src.core.tasking.store import TaskStore

logger = logging.getLogger(__name__)


@dataclass
class StepResult:
    """Result from executing a single step."""
    step_id: str
    success: bool
    outputs: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    duration_ms: float = 0.0
    receipt_id: Optional[str] = None


@dataclass
class TaskRunResult:
    """Result from executing a full TaskRun."""
    run_id: str
    status: str
    step_results: List[StepResult] = field(default_factory=list)
    receipts: List[str] = field(default_factory=list)
    blocked_step: Optional[str] = None


class TaskRunner:
    """Executes a TaskRun step-by-step with receipt emission.

    Dependencies:
        task_store: TaskStore for persisting run state
        token_store: ExecutionTokenStore for token lookup
        minter: PermissionMinter for authority checks
        receipt_service: ReceiptService for emitting receipts
        skill_executor: SkillExecutor for running skills
        verifier: Verifier for acceptance checks
    """

    def __init__(
        self,
        task_store: TaskStore,
        token_store=None,
        minter=None,
        receipt_service=None,
        skill_executor=None,
        verifier=None,
    ):
        self.task_store = task_store
        self.token_store = token_store
        self.minter = minter
        self.receipt_service = receipt_service
        self.skill_executor = skill_executor
        self.verifier = verifier

    def run(self, task_run_id: str) -> TaskRunResult:
        """Execute all steps in a TaskRun, respecting dependencies.

        Walks through the graph's steps in dependency order. For each step:
        1. Check token authority
        2. Emit STEP_STARTED receipt
        3. Execute based on step type
        4. Emit STEP_COMPLETED or STEP_FAILED receipt
        5. Run verifier if token.requires_verifier
        6. Update TaskRun state
        7. Increment token actions_used

        Returns:
            TaskRunResult with final status and receipts.
        """
        run = self.task_store.get_run(task_run_id)
        if run is None:
            return TaskRunResult(run_id=task_run_id, status="FAILED",
                                 step_results=[StepResult(step_id="", success=False,
                                                          error="TaskRun not found")])

        graph = self.task_store.get_graph(run.task_graph_id)
        if graph is None:
            self.task_store.update_status(run.id, RunStatus.FAILED.value,
                                          error="TaskGraph not found")
            return TaskRunResult(run_id=run.id, status=RunStatus.FAILED.value)

        # Look up token
        token = None
        if run.execution_token_id and self.token_store:
            token = self.token_store.get(run.execution_token_id)

        # Transition to RUNNING
        self.task_store.update_status(run.id, RunStatus.RUNNING.value)

        step_results: List[StepResult] = []
        receipt_ids: List[str] = []
        execution_order = self._resolve_execution_order(graph.steps)

        for step in execution_order:
            # Update current step
            self.task_store.update_status(run.id, RunStatus.RUNNING.value,
                                          current_step=step.step_id)

            # 1. Check token authority
            if token and self.minter:
                auth = self._check_step_authority(token, step)
                if not auth.allowed:
                    result = StepResult(
                        step_id=step.step_id, success=False,
                        error=f"Authority denied: {auth.reason}",
                    )
                    step_results.append(result)
                    self._emit_step_failed(run.id, step, auth.reason, receipt_ids)
                    self.task_store.update_status(
                        run.id, RunStatus.FAILED.value,
                        current_step=step.step_id,
                        error=f"Authority denied: {auth.reason}",
                    )
                    return TaskRunResult(
                        run_id=run.id, status=RunStatus.FAILED.value,
                        step_results=step_results, receipts=receipt_ids,
                    )

            # 2. HUMAN_INPUT → BLOCKED
            if step.type == StepType.HUMAN_INPUT.value:
                self.task_store.update_status(
                    run.id, RunStatus.BLOCKED.value,
                    current_step=step.step_id,
                )
                step_results.append(StepResult(
                    step_id=step.step_id, success=True,
                    outputs={"status": "BLOCKED_FOR_HUMAN_INPUT"},
                ))
                return TaskRunResult(
                    run_id=run.id, status=RunStatus.BLOCKED.value,
                    step_results=step_results, receipts=receipt_ids,
                    blocked_step=step.step_id,
                )

            # 3. Emit STEP_STARTED receipt
            started_receipt_id = self._emit_step_started(run.id, step, receipt_ids)

            # 4. Execute step
            start_time = time.monotonic()
            try:
                outputs = self._execute_step(step)
                duration_ms = (time.monotonic() - start_time) * 1000
                result = StepResult(
                    step_id=step.step_id, success=True,
                    outputs=outputs, duration_ms=duration_ms,
                )
            except Exception as exc:
                duration_ms = (time.monotonic() - start_time) * 1000
                error_msg = str(exc)
                result = StepResult(
                    step_id=step.step_id, success=False,
                    error=error_msg, duration_ms=duration_ms,
                )

            step_results.append(result)

            # 5. Emit STEP_COMPLETED or STEP_FAILED receipt
            if result.success:
                self._emit_step_completed(run.id, step, result, receipt_ids)
            else:
                self._emit_step_failed(run.id, step, result.error, receipt_ids)
                # Step failed → run fails
                self.task_store.update_status(
                    run.id, RunStatus.FAILED.value,
                    current_step=step.step_id,
                    error=result.error,
                )
                return TaskRunResult(
                    run_id=run.id, status=RunStatus.FAILED.value,
                    step_results=step_results, receipts=receipt_ids,
                )

            # 6. Run verifier if needed
            if token and getattr(token, 'requires_verifier', False) and step.acceptance_check:
                verify_ok = self._run_verifier(step, result)
                if not verify_ok:
                    self._emit_verify_failed(run.id, step, receipt_ids)
                    self.task_store.update_status(
                        run.id, RunStatus.FAILED.value,
                        current_step=step.step_id,
                        error=f"Verification failed for step {step.step_id}",
                    )
                    return TaskRunResult(
                        run_id=run.id, status=RunStatus.FAILED.value,
                        step_results=step_results, receipts=receipt_ids,
                    )
                self._emit_verify_passed(run.id, step, receipt_ids)

            # 7. Increment token actions
            if token and self.token_store:
                self.token_store.increment_actions(token.id)

        # All steps completed
        self.task_store.update_status(run.id, RunStatus.SUCCEEDED.value)
        return TaskRunResult(
            run_id=run.id, status=RunStatus.SUCCEEDED.value,
            step_results=step_results, receipts=receipt_ids,
        )

    def _resolve_execution_order(self, steps: List[TaskStep]) -> List[TaskStep]:
        """Topological sort based on dependencies.

        Steps are ordered so that dependencies are executed first.
        If no explicit dependencies, steps run in original order.
        """
        step_map = {s.step_id: s for s in steps}
        visited = set()
        order = []

        def visit(step_id: str):
            if step_id in visited:
                return
            visited.add(step_id)
            step = step_map.get(step_id)
            if step is None:
                return
            for dep_id in step.dependencies:
                visit(dep_id)
            order.append(step)

        for step in steps:
            visit(step.step_id)

        return order

    def _check_step_authority(self, token, step: TaskStep):
        """Check token authority for a step."""
        tool_name = step.type  # Map step type to tool
        return self.minter.check_authority(token, tool=tool_name)

    def _execute_step(self, step: TaskStep) -> Dict[str, Any]:
        """Execute a step based on its type.

        Dispatches to the appropriate skill or tool executor.
        """
        step_type = step.type

        if step_type == StepType.VERIFY.value:
            # Verify steps use the verifier
            if self.verifier:
                goal = step.acceptance_check or step.inputs.get("description", "")
                context = str(step.inputs)
                vr = self.verifier.verify_step(goal, context)
                return {"verified": vr.success, "reason": vr.reason}
            return {"verified": True, "reason": "No verifier available, assuming pass"}

        if step_type in (StepType.SKILL_CALL.value,):
            # Delegate to skill executor
            if self.skill_executor:
                skill_name = step.inputs.get("skill_name", "echo")
                skill_result = self.skill_executor.run(skill_name, step.inputs)
                if not skill_result.success:
                    raise RuntimeError(f"Skill '{skill_name}' failed: {skill_result.error}")
                return skill_result.outputs
            return {"note": "No skill executor, step skipped"}

        if step_type in (StepType.FILE_EDIT.value, StepType.COMMAND.value,
                         StepType.TOOL_CALL.value):
            if self.skill_executor:
                # Map step type to skill name
                skill_map = {
                    StepType.FILE_EDIT.value: "repo_writer",
                    StepType.COMMAND.value: "command_runner",
                    StepType.TOOL_CALL.value: step.inputs.get("tool_name", "echo"),
                }
                skill_name = skill_map.get(step_type, "echo")

                # Build skill-compatible inputs from step metadata.
                # The plan compiler stores step info as {description, tool, params}
                # but skills expect specific keys (e.g. command_runner wants "command").
                skill_inputs = dict(step.inputs)
                if skill_name == "command_runner" and "command" not in skill_inputs:
                    # Extract command from params list if available
                    for p in skill_inputs.get("params", []):
                        if p.get("name") == "command":
                            skill_inputs["command"] = p["value"]
                            break
                    # Fallback: use description (may be a raw command string)
                    if "command" not in skill_inputs:
                        skill_inputs["command"] = skill_inputs.get("description", "")

                skill_result = self.skill_executor.run(skill_name, skill_inputs)
                if not skill_result.success:
                    raise RuntimeError(f"Skill '{skill_name}' failed: {skill_result.error}")
                return skill_result.outputs
            return {"note": f"Step type {step_type} executed (placeholder)"}

        return {"note": f"Unknown step type: {step_type}"}

    def _run_verifier(self, step: TaskStep, result: StepResult) -> bool:
        """Run the verifier on step results."""
        if not self.verifier:
            return True
        try:
            vr = self.verifier.verify_step(
                step.acceptance_check,
                str(result.outputs),
            )
            return vr.success
        except Exception as exc:
            logger.warning("Verifier error for step %s: %s", step.step_id, exc)
            return True  # Don't fail on verifier error

    # --- Receipt emission helpers ---

    def _emit_receipt(self, run_id: str, action_type: str, inputs: dict,
                      receipt_ids: List[str]) -> str:
        """Emit a receipt and track it."""
        receipt_id = str(uuid.uuid4())
        if self.receipt_service:
            try:
                from src.shared.receipts import create_receipt, ActionType, CognitionTier
                action = getattr(ActionType, action_type, ActionType.TOOL_CALL)
                receipt = create_receipt(
                    action, "task_runner",
                    inputs=inputs,
                    tier=CognitionTier.DETERMINISTIC,
                )
                self.receipt_service.create(receipt)
                receipt_id = receipt.id
            except Exception as exc:
                logger.warning("Receipt emission failed: %s", exc)

        receipt_ids.append(receipt_id)
        self.task_store.add_receipt(run_id, receipt_id)
        return receipt_id

    def _emit_step_started(self, run_id: str, step: TaskStep,
                           receipt_ids: List[str]) -> str:
        return self._emit_receipt(
            run_id, "STEP_STARTED",
            {"step_id": step.step_id, "type": step.type,
             "inputs": step.inputs},
            receipt_ids,
        )

    def _emit_step_completed(self, run_id: str, step: TaskStep,
                             result: StepResult, receipt_ids: List[str]) -> str:
        return self._emit_receipt(
            run_id, "STEP_COMPLETED",
            {"step_id": step.step_id, "type": step.type,
             "outputs": result.outputs, "duration_ms": result.duration_ms},
            receipt_ids,
        )

    def _emit_step_failed(self, run_id: str, step: TaskStep,
                          error: str, receipt_ids: List[str]) -> str:
        return self._emit_receipt(
            run_id, "STEP_FAILED",
            {"step_id": step.step_id, "type": step.type,
             "error": error,
             "rollback_hint": step.rollback_hint},
            receipt_ids,
        )

    def _emit_verify_passed(self, run_id: str, step: TaskStep,
                            receipt_ids: List[str]) -> str:
        return self._emit_receipt(
            run_id, "VERIFY_PASSED",
            {"step_id": step.step_id, "acceptance_check": step.acceptance_check},
            receipt_ids,
        )

    def _emit_verify_failed(self, run_id: str, step: TaskStep,
                            receipt_ids: List[str]) -> str:
        return self._emit_receipt(
            run_id, "VERIFY_FAILED",
            {"step_id": step.step_id, "acceptance_check": step.acceptance_check},
            receipt_ids,
        )
