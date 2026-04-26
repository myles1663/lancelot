# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
ActionCardFactory — creates ActionCards from approval system events.

Each approval subsystem (soul, skills, scheduler, governance/sentry)
has a dedicated builder method that constructs the appropriate card
and saves it to the ActionCardStore.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from actioncard.models import (
    ActionButton,
    ActionButtonStyle,
    ActionCard,
    ActionCardType,
)
from actioncard.store import ActionCardStore

logger = logging.getLogger(__name__)

# Default expiry: 24 hours for approval cards
_DEFAULT_EXPIRY_SECONDS = 86400


def _compact_text(value: Any, limit: int = 500) -> str:
    text = str(value or "").strip()
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def _format_params(params: Dict[str, Any], limit: int = 700) -> str:
    if not params:
        return "No parameters supplied by the model."
    try:
        rendered = json.dumps(params, indent=2, sort_keys=True, default=str)
    except Exception:
        rendered = str(params)
    if len(rendered) > limit:
        return rendered[: limit - 3].rstrip() + "..."
    return rendered


def _shorten(value: str, limit: int = 80) -> str:
    value = str(value or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _extract_user_request(context: str) -> str:
    text = _compact_text(context, 260)
    if not text:
        return ""

    marker = "User request:"
    if marker not in text:
        return text

    request = text.split(marker, 1)[1].strip()
    for boundary in (". Requested governed tool:", ". Input fields present:"):
        if boundary in request:
            request = request.split(boundary, 1)[0].strip()
            break
    return "" if request.lower() == "unspecified" else request


def _purpose_line(context: str) -> str:
    request = _extract_user_request(context)
    if not request:
        return ""
    return f"What I am trying to do: {_shorten(request, 220)}"


def _workspace_scope_line(params: Dict[str, Any]) -> str:
    workspace = str((params or {}).get("workspace") or "").strip()
    if not workspace:
        return ""
    return f"- Workspace root: `{workspace}`"


def _file_target_kind(params: Dict[str, Any]) -> str:
    workspace = str((params or {}).get("workspace") or "").strip().replace("\\", "/").lower()
    path = str((params or {}).get("path") or "").strip().replace("\\", "/").lower()
    if workspace:
        if workspace.endswith("/home/lancelot/app") or "/lancelot/app" in workspace:
            return "repository"
        if workspace.endswith("/home/lancelot/workspace") or "/lancelot/workspace" in workspace:
            return "workspace"
        return "workspace"
    if path.startswith(("src/", "tests/", "docs/", "config/", "packages/", "scripts/")):
        return "repository"
    if path in {"readme.md", "docker-compose.yml", "pyproject.toml", "pytest.ini"}:
        return "repository"
    return "file"


def _file_kind_label(kind: str) -> str:
    if kind == "repository":
        return "repository file"
    if kind == "workspace":
        return "workspace file"
    return "file"


def _allows_bounded_workspace_retry(params: Dict[str, Any]) -> bool:
    action = str((params or {}).get("action") or "").strip().lower()
    path = str((params or {}).get("path") or "").strip().lower()
    if action not in {"create", "edit"}:
        return False
    if _file_target_kind(params or {}) != "workspace":
        return False
    return path.endswith((".txt", ".md", ".log", ".csv"))


def _approval_copy(tool_name: str, params: Dict[str, Any]) -> Dict[str, str]:
    tool = str(tool_name or "tool").strip()
    if tool == "repo_writer":
        action = str(params.get("action") or "modify").strip().lower()
        path = str(params.get("path") or "workspace file").strip()
        kind = _file_target_kind(params)
        label = _file_kind_label(kind)
        bounded_workspace_retry = _allows_bounded_workspace_retry(params)
        scope = [
            f"- One {'bounded' if bounded_workspace_retry else 'exact'} {label} operation",
            f"- Action: {action}",
            f"- Target file: `{path}`",
        ]
        if bounded_workspace_retry:
            scope.append(
                "- A resume retry may reuse this approval only for the same file and equivalent text content"
            )
        workspace_line = _workspace_scope_line(params)
        if workspace_line:
            scope.append(workspace_line)
        return {
            "title": f"Approve {label} {action}: {_shorten(path, 40)}",
            "headline": f"I need approval to {action} one {label}: `{path}`.",
            "scope": "\n".join(scope),
            "exclusions": (
                "- Other files\n"
                "- Follow-up writes not listed in this card\n"
                "- Git commits, pushes, deployments, or external calls unless separately approved"
            ),
        }

    if tool == "command_runner":
        command = str(params.get("command") or "").strip()
        verb = command.split()[0] if command else "command"
        scope = [
            "- One exact command_runner execution",
            f"- Command: `{_shorten(command or verb, 160)}`",
        ]
        cwd = str(params.get("cwd") or params.get("workspace") or "").strip()
        if cwd:
            scope.append(f"- Working directory: `{cwd}`")
        return {
            "title": f"Approve command: {_shorten(verb, 40)}",
            "headline": f"I need approval to run one server command: `{_shorten(verb, 60)}`.",
            "scope": "\n".join(scope),
            "exclusions": (
                "- An interactive shell session\n"
                "- Follow-up commands not listed in this card\n"
                "- File writes, network calls, or service changes outside this command's declared behavior"
            ),
        }

    if tool in {"network_client", "github_connector"}:
        method = str(params.get("method") or "request").upper()
        raw_url = str(params.get("url") or "")
        host = urlparse(raw_url).netloc or "external endpoint"
        return {
            "title": f"Approve {method}: {_shorten(host, 48)}",
            "headline": f"I need approval to send one governed {method} request to `{host}`.",
            "scope": (
                "- One exact outbound connector request\n"
                f"- Method: {method}\n"
                f"- Destination: `{host}`"
            ),
            "exclusions": (
                "- Future requests to this host\n"
                "- Requests to other hosts\n"
                "- Credential disclosure; credentials stay vault-backed and are not exposed in the card"
            ),
        }

    if tool == "service_runner":
        action = str(params.get("action") or "manage").strip()
        service = str(params.get("service_name") or "configured services").strip()
        return {
            "title": f"Approve service {action}: {_shorten(service, 44)}",
            "headline": f"I need approval to `{action}` `{service}`.",
            "scope": (
                "- One exact service_runner action\n"
                f"- Service: `{service}`\n"
                f"- Action: {action}"
            ),
            "exclusions": "- Other services\n- Follow-up service actions\n- Deployments unless separately listed",
        }

    if tool == "telegram_send":
        return {
            "title": "Approve Telegram send",
            "headline": "I need approval to send one Telegram message or file.",
            "scope": "- One outbound Telegram delivery\n- Recipient: configured owner chat",
            "exclusions": "- Future Telegram messages\n- Messages to other recipients unless separately approved",
        }

    return {
        "title": f"Approve tool use: {_shorten(tool, 48)}",
        "headline": f"I need approval to run one governed `{tool}` action.",
        "scope": f"- One exact `{tool}` tool call",
        "exclusions": "- Unrelated tool calls\n- Follow-up actions not listed in this card",
    }


def _approval_group_copy(requests: List[Dict[str, Any]]) -> Dict[str, str]:
    count = len(requests)
    tools = [str(item.get("tool_name") or "tool") for item in requests]
    unique_tools = sorted(set(tools))

    if unique_tools == ["repo_writer"]:
        actions = [
            str((item.get("params") or {}).get("action") or "modify").strip().lower()
            for item in requests
        ]
        paths = [
            str((item.get("params") or {}).get("path") or "workspace file").strip()
            for item in requests
        ]
        action_label = "write"
        if len(set(actions)) == 1:
            action_label = actions[0]
        workspaces = sorted({
            str((item.get("params") or {}).get("workspace") or "").strip()
            for item in requests
            if str((item.get("params") or {}).get("workspace") or "").strip()
        })
        kinds = [_file_target_kind(item.get("params") or {}) for item in requests]
        kind = kinds[0] if len(set(kinds)) == 1 else "file"
        label = _file_kind_label(kind)
        scope = [
            f"- {count} exact {label} operations",
            "- Only the files listed below",
        ]
        if len(workspaces) == 1:
            scope.append(f"- Workspace root: `{workspaces[0]}`")
        elif len(workspaces) > 1:
            scope.append("- Workspace roots are listed in technical details")
        if all(_allows_bounded_workspace_retry(item.get("params") or {}) for item in requests):
            scope.append(
                "- Resume retries may reuse approval only for the same listed files and equivalent text content"
            )
        return {
            "title": f"Approve {count} {label} {action_label}s",
            "headline": f"I need approval to {action_label} {count} {label}s.",
            "scope": "\n".join(scope),
            "exclusions": (
                "- Files not listed below\n"
                "- Follow-up writes after this approval group\n"
                "- Git commits, pushes, deployments, or external calls unless separately approved"
            ),
            "items": "\n".join(
                f"- {action or 'modify'} `{path}`"
                for action, path in zip(actions, paths)
            ),
            "items_label": "Files",
        }

    if set(unique_tools).issubset({"network_client", "github_connector"}):
        hosts = []
        for item in requests:
            params = item.get("params") or {}
            host = urlparse(str(params.get("url") or "")).netloc or "external endpoint"
            method = str(params.get("method") or "request").upper()
            hosts.append(f"- {method} `{host}`")
        return {
            "title": f"Approve {count} governed connector requests",
            "headline": f"I need approval to send {count} governed connector requests.",
            "scope": (
                f"- {count} exact outbound connector requests\n"
                "- Only the destinations listed below"
            ),
            "exclusions": (
                "- Future requests to these hosts\n"
                "- Requests to other hosts\n"
                "- Credential disclosure; credentials stay vault-backed and are not exposed in the card"
            ),
            "items": "\n".join(hosts),
            "items_label": "Requests",
        }

    if unique_tools == ["command_runner"]:
        commands = []
        for item in requests:
            params = item.get("params") or {}
            command = str(params.get("command") or "command").strip()
            commands.append(f"- `{_shorten(command, 160)}`")
        return {
            "title": f"Approve {count} server commands",
            "headline": f"I need approval to run {count} server commands.",
            "scope": (
                f"- {count} exact command_runner executions\n"
                "- Only the commands listed below"
            ),
            "exclusions": (
                "- An interactive shell session\n"
                "- Follow-up commands not listed below\n"
                "- File writes, network calls, or service changes outside the listed commands' declared behavior"
            ),
            "items": "\n".join(commands),
            "items_label": "Commands",
        }

    return {
        "title": f"Approve {count} governed actions",
        "headline": f"I need approval to run {count} governed actions for this step.",
        "scope": (
            f"- {count} exact governed tool calls\n"
            "- Only the tool calls listed below"
        ),
        "exclusions": "- Future actions\n- Tool calls not listed below\n- Broader access than the declared tool inputs",
        "items": "\n".join(
            f"- `{item.get('tool_name') or 'tool'}`"
            for item in requests
        ),
        "items_label": "Tool calls",
    }


def _format_request_details(requests: List[Dict[str, Any]], limit: int = 1400) -> str:
    parts = []
    for idx, item in enumerate(requests, start=1):
        req_id = item.get("request_id") or "untracked"
        tool_name = item.get("tool_name") or "tool"
        params = item.get("params") or {}
        parts.append(
            f"{idx}. Request: {req_id}\n"
            f"   Tool: {tool_name}\n"
            f"   Parameters:\n{_format_params(params, limit=500)}"
        )
    rendered = "\n\n".join(parts)
    if len(rendered) > limit:
        return rendered[: limit - 3].rstrip() + "..."
    return rendered


class ActionCardFactory:
    """Creates ActionCards from approval system events."""

    def __init__(self, card_store: ActionCardStore, event_bus=None):
        self._store = card_store
        self._event_bus = event_bus

    def _emit_presented(self, card: ActionCard) -> None:
        """Emit actioncard_presented event for cross-channel delivery."""
        if not self._event_bus:
            return
        try:
            from event_bus import Event
            self._event_bus.publish_sync(Event(
                type="actioncard_presented",
                payload=card.to_dict(),
            ))
        except Exception as exc:
            logger.warning("Failed to emit actioncard_presented: %s", exc)

    def from_sentry_request(
        self,
        req_id: str,
        tool_name: str,
        params: Dict[str, Any],
        quest_id: Optional[str] = None,
        approval_context: Optional[str] = None,
        approval_reason: Optional[str] = None,
    ) -> ActionCard:
        """Build ActionCard for MCP Sentry T3 action approval."""
        context = _compact_text(approval_context, 600)
        reason = _compact_text(
            approval_reason
            or "This tool call is governed because it can change local state, external systems, or repository content.",
            500,
        )
        copy = _approval_copy(tool_name, params or {})
        description_parts = [
            copy["headline"],
            _purpose_line(context),
            f"Why approval is required: {reason}",
            f"Approval scope:\n{copy['scope']}",
            f"This approval does not cover:\n{copy['exclusions']}",
        ]
        description_parts = [part for part in description_parts if part]
        if context:
            description_parts.append(f"Original request context: {context}")
        description_parts.append(f"Details:\nTool: {tool_name}\nParameters:\n{_format_params(params)}")
        card = ActionCard(
            card_type=ActionCardType.APPROVAL.value,
            title=copy["title"],
            description="\n\n".join(description_parts),
            source_system="governance",
            source_item_id=req_id,
            buttons=[
                ActionButton(
                    id="approve", label="Approve",
                    style=ActionButtonStyle.PRIMARY.value,
                ),
                ActionButton(
                    id="deny", label="Deny",
                    style=ActionButtonStyle.DANGER.value,
                ),
            ],
            quest_id=quest_id,
            expires_at=time.time() + _DEFAULT_EXPIRY_SECONDS,
            metadata={
                "approval_type": "sentry_t3",
                "tool_name": tool_name,
                "approval_context": context,
                "approval_reason": reason,
                "approval_summary": copy["headline"],
                "approval_scope": copy["scope"],
            },
        )
        self._store.save(card)
        self._emit_presented(card)
        logger.info("ActionCard created: sentry T3 %s (card=%s)", req_id, card.short_id())
        return card

    def from_sentry_request_batch(
        self,
        requests: List[Dict[str, Any]],
        quest_id: Optional[str] = None,
        approval_context: Optional[str] = None,
        approval_reason: Optional[str] = None,
    ) -> ActionCard:
        """Build one ActionCard for a batch of exact MCP Sentry approvals."""
        normalized = [
            {
                "request_id": str(item.get("request_id") or ""),
                "tool_name": str(item.get("tool_name") or "tool"),
                "params": item.get("params") or {},
            }
            for item in requests
            if item.get("request_id")
        ]
        if not normalized:
            raise ValueError("Sentry approval batch requires at least one request_id")

        context = _compact_text(approval_context, 600)
        reason = _compact_text(
            approval_reason
            or "These tool calls are governed because they can change local state, external systems, or repository content.",
            500,
        )
        copy = _approval_group_copy(normalized)
        req_ids = [item["request_id"] for item in normalized]
        batch_id = "batch:" + ",".join(req_ids)
        description_parts = [
            copy["headline"],
            _purpose_line(context),
            f"Why approval is required: {reason}",
            f"Approval scope:\n{copy['scope']}",
            f"This approval does not cover:\n{copy['exclusions']}",
            f"{copy.get('items_label', 'Items')}:\n{copy['items']}",
        ]
        description_parts = [part for part in description_parts if part]
        if context:
            description_parts.append(f"Original request context: {context}")
        description_parts.append("Details:\n" + _format_request_details(normalized))

        card = ActionCard(
            card_type=ActionCardType.APPROVAL.value,
            title=copy["title"],
            description="\n\n".join(description_parts),
            source_system="governance",
            source_item_id=batch_id,
            buttons=[
                ActionButton(
                    id="approve", label="Approve",
                    style=ActionButtonStyle.PRIMARY.value,
                ),
                ActionButton(
                    id="deny", label="Deny",
                    style=ActionButtonStyle.DANGER.value,
                ),
            ],
            quest_id=quest_id,
            expires_at=time.time() + _DEFAULT_EXPIRY_SECONDS,
            metadata={
                "approval_type": "sentry_t3_batch",
                "tool_names": sorted(set(item["tool_name"] for item in normalized)),
                "approval_request_ids": req_ids,
                "approval_context": context,
                "approval_reason": reason,
                "approval_summary": copy["headline"],
                "approval_scope": copy["scope"],
            },
        )
        self._store.save(card)
        self._emit_presented(card)
        logger.info("ActionCard created: sentry T3 batch %s (card=%s)", req_ids, card.short_id())
        return card

    def from_soul_proposal(
        self,
        proposal_id: str,
        version: str,
        diff_summary: List[str],
    ) -> ActionCard:
        """Build ActionCard for soul amendment approval."""
        diff_text = "\n".join(f"- {d}" for d in diff_summary[:5])
        card = ActionCard(
            card_type=ActionCardType.APPROVAL.value,
            title=f"Soul Amendment: {version}",
            description=f"A soul amendment proposal requires review.\n\n{diff_text}",
            source_system="soul",
            source_item_id=proposal_id,
            buttons=[
                ActionButton(
                    id="approve", label="Approve",
                    style=ActionButtonStyle.PRIMARY.value,
                ),
                ActionButton(
                    id="deny", label="Deny",
                    style=ActionButtonStyle.DANGER.value,
                ),
            ],
            expires_at=time.time() + _DEFAULT_EXPIRY_SECONDS,
            metadata={"approval_type": "soul_amendment", "version": version},
        )
        self._store.save(card)
        self._emit_presented(card)
        logger.info("ActionCard created: soul proposal %s (card=%s)", proposal_id, card.short_id())
        return card

    def from_skill_proposal(
        self,
        proposal_id: str,
        name: str,
        description: str,
    ) -> ActionCard:
        """Build ActionCard for skill proposal approval."""
        card = ActionCard(
            card_type=ActionCardType.APPROVAL.value,
            title=f"Skill Proposal: {name}",
            description=f"{description[:300]}",
            source_system="skills",
            source_item_id=proposal_id,
            buttons=[
                ActionButton(
                    id="approve", label="Approve",
                    style=ActionButtonStyle.PRIMARY.value,
                ),
                ActionButton(
                    id="reject", label="Reject",
                    style=ActionButtonStyle.DANGER.value,
                ),
            ],
            expires_at=time.time() + _DEFAULT_EXPIRY_SECONDS,
            metadata={"approval_type": "skill_proposal", "skill_name": name},
        )
        self._store.save(card)
        self._emit_presented(card)
        logger.info("ActionCard created: skill proposal %s (card=%s)", proposal_id, card.short_id())
        return card

    def from_scheduler_approval(
        self,
        job_id: str,
        job_name: str,
        skill: str,
    ) -> ActionCard:
        """Build ActionCard for scheduler job approval."""
        card = ActionCard(
            card_type=ActionCardType.APPROVAL.value,
            title=f"Scheduled Job: {job_name}",
            description=f"Job '{job_name}' requires approval to execute skill '{skill}'.",
            source_system="scheduler",
            source_item_id=job_id,
            buttons=[
                ActionButton(
                    id="approve", label="Approve",
                    style=ActionButtonStyle.PRIMARY.value,
                ),
                ActionButton(
                    id="deny", label="Deny",
                    style=ActionButtonStyle.DANGER.value,
                ),
            ],
            expires_at=time.time() + _DEFAULT_EXPIRY_SECONDS,
            metadata={"approval_type": "scheduler_job", "skill": skill},
        )
        self._store.save(card)
        self._emit_presented(card)
        logger.info("ActionCard created: scheduler job %s (card=%s)", job_id, card.short_id())
        return card

    def create_custom(
        self,
        card_type: str,
        title: str,
        description: str,
        buttons: List[ActionButton],
        source_system: str = "",
        source_item_id: str = "",
        quest_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        expires_seconds: int = _DEFAULT_EXPIRY_SECONDS,
    ) -> ActionCard:
        """Build a custom ActionCard for ad-hoc interactive prompts."""
        card = ActionCard(
            card_type=card_type,
            title=title,
            description=description,
            source_system=source_system,
            source_item_id=source_item_id,
            buttons=buttons,
            quest_id=quest_id,
            metadata=metadata or {},
            expires_at=time.time() + expires_seconds,
        )
        self._store.save(card)
        self._emit_presented(card)
        logger.info("ActionCard created: custom %s (card=%s)", card_type, card.short_id())
        return card
