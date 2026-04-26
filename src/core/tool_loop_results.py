from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


FAILED_TOOL_RETRY_INSTRUCTION = (
    "Tool failed. Try an alternative approach immediately - do NOT narrate the failure "
    "or say 'let me try'. Just call the next tool."
)


@dataclass(frozen=True)
class ToolExecutionRecord:
    receipt: dict[str, Any]
    result_data: Any
    result_content: str
    result_label: str
    error: str | None
    success: bool


def command_return_code_failure(skill_name: str, outputs: Any) -> str:
    """Treat command-like tool return_code failures as failed governed actions."""
    if skill_name not in {"command_runner", "service_runner"} or not isinstance(outputs, dict):
        return ""
    if "return_code" not in outputs:
        return ""
    try:
        return_code = int(outputs.get("return_code"))
    except (TypeError, ValueError):
        return_code = 1
    if return_code == 0:
        return ""
    detail = (
        outputs.get("stderr")
        or outputs.get("stdout")
        or outputs.get("command")
        or outputs.get("service_name")
        or "no detail returned"
    )
    return f"{skill_name} exited with return_code={return_code}: {str(detail)[:500]}"


def normalize_tool_success(
    skill_name: str,
    inputs: dict[str, Any],
    outputs: Any,
    *,
    max_result_chars: int,
    workspace: str | None = None,
) -> ToolExecutionRecord:
    normalized_outputs = outputs or {"status": "success"}

    return_code_error = command_return_code_failure(skill_name, normalized_outputs)
    if return_code_error:
        result_data = {
            "error": return_code_error,
            "outputs": normalized_outputs,
            "instruction": (
                "Tool execution returned a nonzero exit code. "
                "Try a valid local inspection command or report the failed command accurately."
            ),
        }
        return ToolExecutionRecord(
            receipt={
                "skill": skill_name,
                "inputs": inputs,
                "result": f"FAILED: {return_code_error}",
                "outputs": result_data,
            },
            result_data=result_data,
            result_content=str(result_data),
            result_label=f"FAILED: {return_code_error}",
            error=return_code_error,
            success=False,
        )

    if skill_name == "document_creator" and isinstance(normalized_outputs, dict) and normalized_outputs.get("path"):
        normalized_outputs = dict(normalized_outputs)
        doc_abs = str(normalized_outputs["path"])
        workspace_root = workspace or os.getenv("LANCELOT_WORKSPACE", "/home/lancelot/workspace")
        doc_rel = doc_abs.replace(f"{workspace_root}/", "").lstrip("/")
        download_url = f"/api/files/{doc_rel}"
        normalized_outputs["download_url"] = download_url
        normalized_outputs["download_note"] = (
            f"Document created. Include this link in your response so "
            f"the user can download it: [Download {Path(doc_abs).name}]({download_url})"
        )

    result_content = str(normalized_outputs)
    if len(result_content) > max_result_chars:
        truncated = result_content[:max_result_chars] + "... [truncated]"
        if max_result_chars >= 8000:
            normalized_outputs = {"truncated": truncated}
            result_content = str(normalized_outputs)
        else:
            result_content = truncated

    return ToolExecutionRecord(
        receipt={
            "skill": skill_name,
            "inputs": inputs,
            "result": "SUCCESS",
            "outputs": normalized_outputs,
        },
        result_data=normalized_outputs,
        result_content=result_content,
        result_label="SUCCESS",
        error=None,
        success=True,
    )


def normalize_tool_failure(
    skill_name: str,
    inputs: dict[str, Any],
    error: Any,
    *,
    exception: bool = False,
    structured_result: bool = True,
) -> ToolExecutionRecord:
    error_text = str(error or "Unknown error")
    prefix = "EXCEPTION" if exception else "FAILED"
    result_label = f"{prefix}: {error_text}"
    result_data = {"error": error_text}
    if structured_result:
        result_data["instruction"] = FAILED_TOOL_RETRY_INSTRUCTION

    return ToolExecutionRecord(
        receipt={
            "skill": skill_name,
            "inputs": inputs,
            "result": result_label,
        },
        result_data=result_data,
        result_content=f"Exception: {error_text}" if exception else f"Error: {error_text}",
        result_label=result_label,
        error=error_text,
        success=False,
    )
