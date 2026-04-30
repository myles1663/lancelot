from __future__ import annotations

import os
import shlex
from typing import Any


def tool_target_key(skill_name: str, inputs: dict[str, Any] | None) -> str:
    """Return a stable key for resolving retries by later successes."""
    inputs = inputs or {}
    if skill_name == "repo_writer":
        action = str(inputs.get("action") or "").lower() or "unknown"
        path = str(inputs.get("path") or "").strip()
        if not path:
            return f"{skill_name}:input_validation"
        workspace = str(inputs.get("workspace") or os.getenv("LANCELOT_WORKSPACE", "default"))
        return f"{skill_name}:{workspace}:{action}:{path}"
    if skill_name == "command_runner":
        command = str(inputs.get("command") or "").strip()
        if not command:
            return f"{skill_name}:input_validation"
        try:
            binary = shlex.split(command, posix=os.name != "nt")[0]
        except Exception:
            binary = command.split(" ", 1)[0]
        return f"{skill_name}:{binary}:{command}"
    if skill_name == "network_client":
        method = str(inputs.get("method") or "GET").upper()
        url = str(inputs.get("url") or "").strip()
        if not url:
            return f"{skill_name}:input_validation"
        return f"{skill_name}:{method}:{url}"
    return f"{skill_name}:{str(inputs)[:240]}"


def is_unresolved_failure_result(result: str) -> bool:
    return result.startswith(("FAILED", "EXCEPTION", "REJECTED", "ESCALATED"))


def unresolved_tool_failures(tool_receipts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return failed/escalated receipts that were not corrected later."""
    unresolved: dict[str, dict[str, Any]] = {}
    success_by_skill: set[str] = set()

    for receipt in tool_receipts:
        skill_name = str(receipt.get("skill") or "")
        result = str(receipt.get("result") or "")
        key = tool_target_key(skill_name, receipt.get("inputs") or {})

        if result == "SUCCESS":
            success_by_skill.add(skill_name)
            unresolved.pop(key, None)
            unresolved.pop(f"{skill_name}:input_validation", None)
            continue

        if is_unresolved_failure_result(result):
            unresolved[key] = receipt

    # A later success for the same skill means an earlier malformed request was corrected.
    for skill_name in success_by_skill:
        unresolved.pop(f"{skill_name}:input_validation", None)

    return list(unresolved.values())


def find_successful_tool_receipt(
    tool_receipts: list[dict[str, Any]],
    skill_name: str,
    inputs: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return a prior success for the same tool target within this run."""
    target_key = tool_target_key(skill_name, inputs or {})
    for receipt in reversed(tool_receipts):
        if receipt.get("result") != "SUCCESS":
            continue
        if str(receipt.get("skill") or "") != skill_name:
            continue
        if tool_target_key(skill_name, receipt.get("inputs") or {}) == target_key:
            return receipt
    return None


def claims_completion(text: str) -> bool:
    """Detect final responses that read as completion despite unresolved failures."""
    normalized = (text or "").lower()
    completion_markers = (
        "completed",
        "complete",
        "done",
        "finished",
        "implemented",
        "created",
        "updated",
        "fixed",
        "pushed",
        "committed",
        "successfully",
    )
    uncertainty_markers = (
        "could not",
        "was not able",
        "wasn't able",
        "failed",
        "blocked",
        "pending approval",
        "not complete",
        "incomplete",
    )
    return any(marker in normalized for marker in completion_markers) and not any(
        marker in normalized for marker in uncertainty_markers
    )


def completion_contract_note(unresolved: list[dict[str, Any]]) -> str:
    parts = []
    for receipt in unresolved[:3]:
        skill = receipt.get("skill") or "tool"
        result = str(receipt.get("result") or "failed")
        inputs = receipt.get("inputs") or {}
        target = inputs.get("path") or inputs.get("command") or inputs.get("url") or "unspecified target"
        parts.append(f"- {skill} on `{target}`: {result}")
    if len(unresolved) > 3:
        parts.append(f"- plus {len(unresolved) - 3} more unresolved tool issue(s)")
    return "\n".join(parts)
