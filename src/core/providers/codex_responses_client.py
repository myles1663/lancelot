"""
OpenAICodexResponsesProviderClient -- ChatGPT Codex OAuth via Responses API.

This client uses the ChatGPT/Codex backend as model transport while keeping
tool execution inside Lancelot's governed tool loop. It reads the same OAuth
material used by the Codex CLI, but it does not invoke the Codex CLI agent
runtime for turns.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from providers.base import GenerateResult, ModelInfo, ProviderAuthError, ProviderClient, ToolCall, _is_auth_error
from providers.codex_cli_client import resolve_codex_auth_file
from providers.tool_schema import NormalizedToolDeclaration

logger = logging.getLogger(__name__)


class OpenAICodexResponsesProviderClient(ProviderClient):
    """OpenAI Codex provider backed by ChatGPT OAuth and Responses streaming."""

    CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
    _DEFAULT_INSTRUCTIONS = "You are Lancelot's governed model planner."
    _CODEX_MODELS = [
        ModelInfo(id="gpt-5.4", display_name="GPT-5.4", supports_tools=True,
                  capability_tier="deep", context_window=1050000,
                  input_cost_per_1k=0.0025, output_cost_per_1k=0.015),
        ModelInfo(id="gpt-5.4-mini", display_name="GPT-5.4 Mini", supports_tools=True,
                  capability_tier="fast", context_window=272000,
                  input_cost_per_1k=0.00075, output_cost_per_1k=0.0045),
    ]

    def __init__(self, auth_token: str = "", codex_home: str = "", base_url: str = ""):
        self._codex_home = codex_home
        self._auth_token = auth_token or self._read_cli_access_token(codex_home)
        if not self._auth_token:
            raise ProviderAuthError(
                "openai-codex",
                "Codex OAuth token is not available. Sign in with Codex CLI or complete Codex OAuth.",
            )
        self._base_url = (base_url or self.CODEX_BASE_URL).rstrip("/")
        try:
            import openai
        except ImportError as exc:
            raise ImportError("OpenAI SDK not installed. Run: pip install openai") from exc
        self._client = openai.OpenAI(api_key=self._auth_token, base_url=self._base_url)
        logger.info("OpenAI Codex Responses provider initialized (base_url=%s)", self._base_url)

    @property
    def provider_name(self) -> str:
        return "openai-codex"

    def generate(
        self,
        model: str,
        messages: list,
        system_instruction: str = "",
        config: Optional[dict] = None,
    ) -> GenerateResult:
        return self._create_response(
            model=model,
            messages=messages,
            system_instruction=system_instruction,
            tools=None,
            tool_config=None,
            config=config,
        )

    def generate_with_tools(
        self,
        model: str,
        messages: list,
        system_instruction: str,
        tools: list,
        tool_config: Optional[dict] = None,
        config: Optional[dict] = None,
    ) -> GenerateResult:
        return self._create_response(
            model=model,
            messages=messages,
            system_instruction=system_instruction,
            tools=tools,
            tool_config=tool_config,
            config=config,
        )

    def build_tool_response_message(
        self,
        tool_results: list[tuple[str, str, str]],
    ) -> Any:
        return [
            {"type": "function_call_output", "call_id": call_id, "output": str(result_str)}
            for call_id, _fn_name, result_str in tool_results
        ]

    def build_user_message(self, text: str, images: Optional[list] = None) -> Any:
        if images:
            text = f"{text}\n\n[Attached images omitted for Codex Responses provider]"
        return {"role": "user", "content": text}

    def list_models(self) -> list[ModelInfo]:
        return list(self._CODEX_MODELS)

    def validate_model(self, model_id: str) -> bool:
        return any(model.id == model_id for model in self._CODEX_MODELS)

    def _create_response(
        self,
        *,
        model: str,
        messages: list,
        system_instruction: str,
        tools: Optional[list],
        tool_config: Optional[dict],
        config: Optional[dict],
    ) -> GenerateResult:
        request: dict[str, Any] = {
            "model": model,
            "instructions": system_instruction or self._DEFAULT_INSTRUCTIONS,
            "input": self._convert_messages(messages),
            "stream": True,
            "store": False,
        }
        temperature = (config or {}).get("temperature")
        if temperature is not None:
            request["temperature"] = temperature
        reasoning_effort = (config or {}).get("reasoning_effort", "none")
        if reasoning_effort:
            request["reasoning"] = {"effort": reasoning_effort}

        response_tools = self._to_responses_tools(tools or [])
        if response_tools:
            request["tools"] = response_tools
            mode = str((tool_config or {}).get("mode", "AUTO")).upper()
            if mode == "ANY":
                request["tool_choice"] = "required"
            elif mode == "NONE":
                request["tool_choice"] = "none"

        try:
            stream = self._client.responses.create(**request)
            return self._parse_stream(stream)
        except Exception as exc:
            if _is_auth_error(exc):
                raise ProviderAuthError("openai-codex", str(exc)) from exc
            raise

    def _parse_stream(self, stream: Any) -> GenerateResult:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        raw_tool_items: list[dict[str, Any]] = []
        usage = {"input_tokens": 0, "output_tokens": 0}

        for event in stream:
            event_type = str(getattr(event, "type", "") or "")
            if event_type == "response.output_text.delta":
                text_parts.append(str(getattr(event, "delta", "") or ""))
            elif event_type == "response.output_item.done":
                item = getattr(event, "item", None)
                if str(getattr(item, "type", "") or "") == "function_call":
                    raw_item = self._function_call_item_to_raw(item)
                    raw_tool_items.append(raw_item)
                    tool_calls.append(ToolCall(
                        name=str(raw_item.get("name", "")),
                        args=self._parse_args_json(raw_item.get("arguments")),
                        id=str(raw_item.get("call_id", "")),
                    ))
            elif event_type == "response.completed":
                response = getattr(event, "response", None)
                response_usage = getattr(response, "usage", None)
                if response_usage is not None:
                    usage["input_tokens"] = int(
                        getattr(response_usage, "input_tokens", None)
                        or getattr(response_usage, "prompt_tokens", None)
                        or 0
                    )
                    usage["output_tokens"] = int(
                        getattr(response_usage, "output_tokens", None)
                        or getattr(response_usage, "completion_tokens", None)
                        or 0
                    )

        text = "".join(text_parts) or None
        raw: Any
        if raw_tool_items:
            raw = raw_tool_items
        else:
            raw = {"role": "assistant", "content": text or ""}
        return GenerateResult(text=text, tool_calls=tool_calls, raw=raw, usage=usage)

    @staticmethod
    def _function_call_item_to_raw(item: Any) -> dict[str, Any]:
        raw = {
            "type": "function_call",
            "call_id": str(getattr(item, "call_id", "") or ""),
            "name": str(getattr(item, "name", "") or ""),
            "arguments": str(getattr(item, "arguments", "") or "{}"),
        }
        item_id = getattr(item, "id", None)
        if item_id:
            raw["id"] = str(item_id)
        status = getattr(item, "status", None)
        if status:
            raw["status"] = str(status)
        return raw

    @staticmethod
    def _convert_messages(messages: list) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for message in messages or []:
            if not isinstance(message, dict):
                converted.append({
                    "role": "user",
                    "content": [{"type": "input_text", "text": str(message)}],
                })
                continue
            msg_type = message.get("type")
            if msg_type in {"function_call", "function_call_output"}:
                converted.append(message)
                continue
            role = str(message.get("role", "user") or "user")
            content = OpenAICodexResponsesProviderClient._flatten_content(message.get("content"))
            if role == "assistant":
                converted.append({
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": content}],
                })
            else:
                converted.append({
                    "role": "user",
                    "content": [{"type": "input_text", "text": content}],
                })
        return converted

    @staticmethod
    def _flatten_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    if "text" in item:
                        parts.append(str(item.get("text", "")))
                    elif item.get("type") == "image_url":
                        parts.append("[image attachment]")
                    else:
                        parts.append(json.dumps(item, ensure_ascii=True))
                else:
                    parts.append(str(item))
            return "\n".join(part for part in parts if part)
        if content is None:
            return ""
        return str(content)

    @staticmethod
    def _to_responses_tools(tools: list) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for tool in tools:
            if isinstance(tool, NormalizedToolDeclaration):
                name = tool.name
                description = tool.description
                parameters = tool.parameters
            elif isinstance(tool, dict) and tool.get("type") == "function" and "function" in tool:
                fn = tool.get("function") or {}
                name = str(fn.get("name", ""))
                description = str(fn.get("description", ""))
                parameters = fn.get("parameters") or {}
            elif isinstance(tool, dict) and tool.get("type") == "function" and "name" in tool:
                converted.append(tool)
                continue
            elif isinstance(tool, dict):
                name = str(tool.get("name", ""))
                description = str(tool.get("description", ""))
                parameters = tool.get("parameters") or {}
            elif all(hasattr(tool, attr) for attr in ("name", "description", "parameters")):
                name = str(getattr(tool, "name", ""))
                description = str(getattr(tool, "description", ""))
                parameters = getattr(tool, "parameters", {}) or {}
            else:
                continue
            if not name:
                continue
            if not isinstance(parameters, dict):
                parameters = {}
            parameters = dict(parameters)
            parameters.setdefault("type", "object")
            parameters.setdefault("properties", {})
            converted.append({
                "type": "function",
                "name": name,
                "description": description,
                "parameters": parameters,
                "strict": False,
            })
        return converted

    @staticmethod
    def _parse_args_json(raw_args: Any) -> dict[str, Any]:
        if isinstance(raw_args, dict):
            return raw_args
        if raw_args is None:
            return {}
        if isinstance(raw_args, str):
            text = raw_args.strip()
            if not text:
                return {}
            try:
                parsed = json.loads(text)
                return parsed if isinstance(parsed, dict) else {"value": parsed}
            except json.JSONDecodeError:
                return {"raw": raw_args}
        return {"value": raw_args}

    @staticmethod
    def _read_cli_access_token(codex_home: str = "") -> str:
        auth_file = resolve_codex_auth_file(codex_home)
        try:
            data = json.loads(Path(auth_file).read_text(encoding="utf-8"))
        except Exception:
            return ""
        token = data.get("tokens", {}).get("access_token", "")
        return token.strip() if isinstance(token, str) else ""
