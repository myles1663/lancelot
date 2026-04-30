from __future__ import annotations

from typing import Any

from orchestrator_consts import COMMAND_BLACKLIST_CHARS


def pending_approval_response(
    skill_name: str,
    approval_id: str | None,
    approval_count: int = 1,
) -> str:
    if approval_count > 1:
        details = [f"Paused for Commander approval before running {approval_count} governed actions."]
    else:
        details = [f"Paused for Commander approval before running `{skill_name}`."]
    if approval_id:
        label = "Approval group ID" if approval_count > 1 else "Approval ID"
        details.append(f"{label}: `{approval_id}`.")
    details.append(
        "Review and resolve the ActionCard in War Room. "
        "After approval, use that card's Continue control to resume the same run."
    )
    return "\n\n".join(details)


PENDING_APPROVAL_RESPONSE_MARKERS = (
    "paused for commander approval",
    "pending commander approval",
    "waiting for commander approval",
    "review the actioncard",
    "send `continue` after approval",
    "continue control to resume",
    "approval id:",
    "approval group id:",
)


def looks_like_pending_approval_response(text: str) -> bool:
    """Detect approval prompts that are only valid if a new approval was created."""
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in PENDING_APPROVAL_RESPONSE_MARKERS)


def approval_context(prompt: str, skill_name: str, inputs: dict[str, Any]) -> str:
    prompt_summary = " ".join((prompt or "").split())
    if len(prompt_summary) > 500:
        prompt_summary = prompt_summary[:497].rstrip() + "..."
    input_keys = ", ".join(sorted(str(key) for key in (inputs or {}).keys())) or "none"
    return (
        f"User request: {prompt_summary or 'unspecified'}. "
        f"Requested governed tool: {skill_name}. "
        f"Input fields present: {input_keys}."
    )


def approval_reason(skill_name: str) -> str:
    if skill_name in {"repo_writer", "file_operations", "document_creator"}:
        return "This can create or modify files in the workspace or repository."
    if skill_name in {"command_runner", "service_runner"}:
        return "This can execute commands or affect the runtime environment."
    if skill_name in {"network_client", "github_connector"}:
        return "This can reach external systems or move data across a connector boundary."
    return "This tool is classified as a governed write or high-risk action."


def approval_group_reason(requests: list[dict[str, Any]]) -> str:
    tools = {str(item.get("tool_name") or "") for item in requests}
    if tools == {"repo_writer"}:
        return "This grouped approval covers multiple bounded file changes for the same user request."
    if tools.issubset({"network_client", "github_connector"}):
        return "This grouped approval covers multiple outbound connector requests for the same user request."
    if tools == {"command_runner"}:
        return "This grouped approval covers multiple command executions for the same user request."
    return "This grouped approval covers multiple governed actions for the same user request."


def tool_input_error(skill_name: str, inputs: dict[str, Any]) -> str:
    if not isinstance(inputs, dict):
        return f"{skill_name} inputs must be a JSON object."

    required_fields = {
        "command_runner": ("command",),
        "repo_writer": ("action", "path"),
        "network_client": ("method", "url"),
        "service_runner": ("action",),
        "document_creator": ("format", "path", "content"),
        "schedule_job": ("action",),
    }
    missing = [
        field for field in required_fields.get(skill_name, ())
        if inputs.get(field) in (None, "")
    ]
    if missing:
        return f"{skill_name} missing required input(s): {', '.join(missing)}."
    if skill_name == "command_runner":
        command = str(inputs.get("command") or "")
        for char in COMMAND_BLACKLIST_CHARS:
            if char in command:
                rendered = "\\n" if char == "\n" else char
                return (
                    f"command_runner blocked shell metacharacter '{rendered}' in command. "
                    "Use one allowed command per tool call; do not chain, pipe, redirect, "
                    "substitute, or group shell commands."
                )
    return ""
