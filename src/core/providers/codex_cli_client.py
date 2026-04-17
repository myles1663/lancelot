"""
CodexCLIProviderClient -- OpenAI Codex via the official codex CLI.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

from providers.base import GenerateResult, ModelInfo, ProviderAuthError, ProviderClient, ToolCall
from providers.tool_schema import NormalizedToolDeclaration


class CodexCLIProviderClient(ProviderClient):
    """Provider adapter backed by `codex exec`.

    Codex acts as the planner. Lancelot still owns the actual tool execution,
    approval flow, receipts, and safety checks.
    """

    _CODEX_MODELS = [
        ModelInfo(id="gpt-5.4", display_name="GPT-5.4", supports_tools=True, capability_tier="deep"),
        ModelInfo(id="gpt-5.4-mini", display_name="GPT-5.4 Mini", supports_tools=True, capability_tier="fast"),
        ModelInfo(id="gpt-5.4-nano", display_name="GPT-5.4 Nano", supports_tools=True, capability_tier="fast"),
    ]

    def __init__(self, workdir: str = "", codex_home: str = ""):
        self._workdir = workdir or os.getcwd()
        self._codex_home = codex_home or os.path.expanduser("~")
        self._ensure_cli_ready()

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
        prompt = self._render_prompt(system_instruction, messages)
        text = self._run_codex(prompt=prompt, model=model)
        return GenerateResult(
            text=text,
            tool_calls=[],
            raw={"role": "assistant", "content": text},
            usage={"input_tokens": 0, "output_tokens": 0},
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
        normalized_tools = self._normalize_tools(tools)
        if not normalized_tools:
            return self.generate(
                model=model,
                messages=messages,
                system_instruction=system_instruction,
                config=config,
            )

        prompt = self._render_tool_prompt(
            system_instruction=system_instruction,
            messages=messages,
            tools=normalized_tools,
            tool_config=tool_config,
        )
        decision = self._run_codex_json(
            prompt=prompt,
            model=model,
            schema=self._build_tool_decision_schema(
                normalized_tools,
                mode=str((tool_config or {}).get("mode", "AUTO")).upper(),
            ),
        )

        raw_content = json.dumps(decision, ensure_ascii=True)
        action = str(decision.get("action", "")).strip().lower()
        if action == "tool_calls":
            tool_calls = []
            for item in decision.get("tool_calls", []):
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                if not name:
                    continue
                args = self._parse_args_json(item.get("args_json"))
                tool_calls.append(ToolCall(name=name, args=args))

            return GenerateResult(
                text=None,
                tool_calls=tool_calls,
                raw={"role": "assistant", "content": raw_content},
                usage={"input_tokens": 0, "output_tokens": 0},
            )

        return GenerateResult(
            text=str(decision.get("final_text", "")).strip(),
            tool_calls=[],
            raw={"role": "assistant", "content": raw_content},
            usage={"input_tokens": 0, "output_tokens": 0},
        )

    def build_tool_response_message(
        self,
        tool_results: list[tuple[str, str, str]],
    ) -> Any:
        return [
            {"role": "tool", "name": fn_name, "content": str(result_str)}
            for _call_id, fn_name, result_str in tool_results
        ]

    def build_user_message(self, text: str, images: Optional[list] = None) -> Any:
        if images:
            text = f"{text}\n\n[Attached images omitted for Codex CLI provider]"
        return {"role": "user", "content": text}

    def list_models(self) -> list[ModelInfo]:
        return list(self._CODEX_MODELS)

    def validate_model(self, model_id: str) -> bool:
        return any(model.id == model_id for model in self._CODEX_MODELS)

    def _ensure_cli_ready(self) -> None:
        if not shutil.which("codex"):
            raise ProviderAuthError("openai-codex", "codex CLI is not installed in the container")
        auth_file = Path(self._codex_home) / ".codex" / "auth.json"
        if not auth_file.exists():
            raise ProviderAuthError(
                "openai-codex",
                "Codex CLI auth is not available in the container. Mount ~/.codex into /home/lancelot/.codex.",
            )

    def _run_codex(self, prompt: str, model: str, schema: Optional[dict] = None) -> str:
        with tempfile.TemporaryDirectory(prefix="lancelot-codex-") as temp_dir:
            temp_path = Path(temp_dir)
            output_path = temp_path / "last_message.txt"
            cmd = [
                "codex",
                "exec",
                "--ephemeral",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--color",
                "never",
                "-C",
                self._workdir,
                "--output-last-message",
                str(output_path),
            ]
            if schema is not None:
                schema_path = temp_path / "output_schema.json"
                schema_path.write_text(json.dumps(schema, ensure_ascii=True), encoding="utf-8")
                cmd.extend(["--output-schema", str(schema_path)])
            if model and self.validate_model(model):
                cmd.extend(["-m", model])
            cmd.append(prompt)

            env = os.environ.copy()
            env["HOME"] = self._codex_home

            proc = subprocess.run(
                cmd,
                text=True,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                env=env,
                timeout=300,
                check=False,
            )

            if proc.returncode != 0:
                message = (proc.stderr or proc.stdout or "codex exec failed").strip()
                raise ProviderAuthError("openai-codex", message[:500])
            output = output_path.read_text(encoding="utf-8").strip()
            if output:
                return output
            fallback = (proc.stdout or "").strip()
            if fallback:
                return fallback
            raise RuntimeError("codex exec returned no message")

    def _run_codex_json(self, prompt: str, model: str, schema: dict) -> dict:
        output = self._run_codex(prompt=prompt, model=model, schema=schema)
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"codex exec returned invalid JSON: {output[:500]}") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("codex exec JSON response must be an object")
        return parsed

    @staticmethod
    def _flatten_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
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

    def _render_prompt(self, system_instruction: str, messages: list) -> str:
        blocks = []
        if system_instruction:
            blocks.append(f"SYSTEM:\n{system_instruction.strip()}")

        for message in messages:
            if isinstance(message, dict):
                role = str(message.get("role", "user")).upper()
                if role == "TOOL" and message.get("name"):
                    role = f"TOOL[{message['name']}]"
                content = self._flatten_content(message.get("content"))
            else:
                role = "USER"
                content = self._flatten_content(message)
            if content:
                blocks.append(f"{role}:\n{content}")

        blocks.append("Respond to the latest request only.")
        return "\n\n".join(blocks)

    def _render_tool_prompt(
        self,
        system_instruction: str,
        messages: list,
        tools: list[dict],
        tool_config: Optional[dict],
    ) -> str:
        mode = str((tool_config or {}).get("mode", "AUTO")).upper()
        tool_blocks = []
        for tool in tools:
            tool_blocks.append(
                f"- {tool['name']}: {tool['description']}\n"
                f"  Parameters JSON Schema: {json.dumps(tool['parameters'], ensure_ascii=True)}"
            )

        instructions = [
            "You are the planning model inside Lancelot's governed tool loop.",
            "Do not use Codex built-in shell, filesystem, git, or network tools.",
            "Your only valid outputs are declared Lancelot tool calls or the final user-facing response.",
            "Only request tools from the declared list.",
            "Treat TOOL results in the transcript as the ground truth of what actually happened.",
            "Never claim to have edited files, run commands, or fetched data unless a TOOL result shows it.",
            "If a tool fails, either choose a different declared tool or produce the best final answer from successful results.",
            "If the user asks about current system health, runtime status, files, services, logs, network state, or any other live environment detail, you must call the relevant declared tool before answering.",
            "If the user asks you to perform an action in the environment, you must use the relevant declared tool instead of answering from general knowledge.",
            "When returning tool_calls, each item must include args_json as a compact JSON object string. Use '{}' when the tool takes no arguments.",
        ]
        if mode == "ANY":
            instructions.append("This turn must request at least one declared tool call.")
        elif mode == "NONE":
            instructions.append("This turn must return a final response and must not request tool calls.")

        prompt = [
            "SYSTEM RULES:",
            *instructions,
            "",
            "DECLARED LANCELOT TOOLS:",
            *tool_blocks,
            "",
            "CONVERSATION TRANSCRIPT:",
            self._render_prompt(system_instruction, messages),
            "",
            "Return only data that satisfies the JSON schema.",
        ]
        return "\n".join(prompt)

    @staticmethod
    def _normalize_tools(tools: list) -> list[dict]:
        normalized = []
        for tool in tools or []:
            if isinstance(tool, NormalizedToolDeclaration) or (
                hasattr(tool, "name") and hasattr(tool, "description") and hasattr(tool, "parameters")
            ):
                normalized.append({
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                })
                continue
            if isinstance(tool, dict):
                if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
                    fn = tool["function"]
                    normalized.append({
                        "name": fn.get("name", ""),
                        "description": fn.get("description", ""),
                        "parameters": fn.get("parameters", {"type": "object"}),
                    })
                elif tool.get("name"):
                    normalized.append({
                        "name": tool.get("name", ""),
                        "description": tool.get("description", ""),
                        "parameters": tool.get("parameters") or tool.get("input_schema") or {"type": "object"},
                    })
        return [tool for tool in normalized if tool["name"]]

    @staticmethod
    def _build_tool_decision_schema(tools: list[dict], mode: str = "AUTO") -> dict:
        tool_names = sorted({tool["name"] for tool in tools})
        action_enum = ["tool_calls", "final"]
        tool_calls_schema: dict[str, Any] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "enum": tool_names},
                    "args_json": {"type": "string"},
                },
                "required": ["name", "args_json"],
                "additionalProperties": False,
            },
        }
        if mode == "ANY":
            action_enum = ["tool_calls"]
            tool_calls_schema["minItems"] = 1
        schema = {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": action_enum},
                "tool_calls": tool_calls_schema,
                "final_text": {"type": "string"},
            },
            "required": ["action", "tool_calls", "final_text"],
            "additionalProperties": False,
        }
        return schema

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
            except json.JSONDecodeError:
                return {}
            if isinstance(parsed, dict):
                return parsed
        return {}
