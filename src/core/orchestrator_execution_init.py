"""Execution-authority subsystem initialization for LancelotOrchestrator."""

from __future__ import annotations

from pathlib import Path


def init_execution_authority(runtime, logger) -> None:
    """Initialize task execution, permission minting, and response assembly."""
    runtime.task_store = None
    runtime.token_store = None
    runtime.minter = None
    runtime.plan_compiler = None
    runtime.task_runner = None
    runtime.assembler = None
    runtime.set_last_plan_artifact(None)

    try:
        from feature_flags import (
            FEATURE_EXECUTION_TOKENS,
            FEATURE_RESPONSE_ASSEMBLER,
            FEATURE_TASK_GRAPH_EXECUTION,
        )
        from src.core.execution_authority.minter import PermissionMinter
        from src.core.execution_authority.store import ExecutionTokenStore
        from src.core.response.assembler import ResponseAssembler
        from src.core.tasking.compiler import PlanCompiler
        from src.core.tasking.runner import TaskRunner
        from src.core.tasking.store import TaskStore
    except ImportError as exc:
        logger.warning("Execution authority imports unavailable; subsystem skipped: %s", exc)
        return

    try:
        db_dir = Path(runtime.data_dir)

        if FEATURE_TASK_GRAPH_EXECUTION:
            runtime.task_store = TaskStore(db_dir / "tasks.db")
            runtime.plan_compiler = PlanCompiler()
            logger.info("TaskStore + PlanCompiler initialized.")

        if FEATURE_EXECUTION_TOKENS:
            runtime.token_store = ExecutionTokenStore(db_dir / "tokens.db")
            runtime.minter = PermissionMinter(
                store=runtime.token_store,
                receipt_service=runtime.receipt_service,
            )
            logger.info("ExecutionTokenStore + PermissionMinter initialized.")

        if FEATURE_TASK_GRAPH_EXECUTION and runtime.task_store:
            runtime.task_runner = TaskRunner(
                task_store=runtime.task_store,
                token_store=runtime.token_store,
                minter=runtime.minter,
                receipt_service=runtime.receipt_service,
                skill_executor=runtime.skill_executor,
                verifier=runtime.verifier,
                connector_runtime=getattr(runtime, "connector_runtime", None),
            )
            logger.info("TaskRunner initialized.")

        if FEATURE_RESPONSE_ASSEMBLER:
            logger.info("FEATURE_RESPONSE_ASSEMBLER flag active.")

    except Exception as exc:
        logger.warning("Execution authority init error; continuing without full task runtime: %s", exc)

    try:
        runtime.assembler = ResponseAssembler()
        logger.info("ResponseAssembler initialized.")
    except Exception as exc:
        logger.warning("ResponseAssembler init failed; output assembly disabled: %s", exc)
        runtime.assembler = None
