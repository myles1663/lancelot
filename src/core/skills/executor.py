"""
Skill Executor — runtime interface and execution adapter (Prompt 8 / B3-B4).

Defines the skill execution contract and safely loads/runs skills.

Public API:
    SkillContext          — execution context passed to skills
    SkillResult           — execution result
    SkillExecutor(registry) — loads and runs skills by name
"""

from __future__ import annotations

import importlib.util
import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.core.skills.schema import SkillError, SkillManifest
from src.core.skills.registry import SkillRegistry, SkillEntry, SkillOwnership, SignatureState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Skill runtime interface
# ---------------------------------------------------------------------------

@dataclass
class SkillContext:
    """Context passed to a skill's execute function."""
    skill_name: str
    request_id: str = ""
    caller: str = "system"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillResult:
    """Result returned from a skill execution."""
    success: bool
    outputs: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    duration_ms: float = 0.0
    receipt: Optional[Dict[str, Any]] = None


# Type for the execute function
SkillExecuteFunc = Callable[[SkillContext, Dict[str, Any]], Dict[str, Any]]


# ---------------------------------------------------------------------------
# Built-in skills
# ---------------------------------------------------------------------------

def _echo_execute(context: SkillContext, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Built-in echo skill — returns inputs as outputs."""
    return {"echo": inputs}


def _load_builtin_execute(module_name: str) -> SkillExecuteFunc:
    """Lazily load execute function from a builtins module."""
    def _wrapper(context: SkillContext, inputs: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from src.core.skills.builtins import repo_writer, command_runner, service_runner, network_client, telegram_send, warroom_send, schedule_job, health_check, document_creator, skill_manager, github_search, daily_news_brief
        except ImportError:
            from skills.builtins import repo_writer, command_runner, service_runner, network_client, telegram_send, warroom_send, schedule_job, health_check, document_creator, skill_manager, github_search, daily_news_brief

        module_map = {
            "repo_writer": repo_writer,
            "command_runner": command_runner,
            "service_runner": service_runner,
            "network_client": network_client,
            "telegram_send": telegram_send,
            "warroom_send": warroom_send,
            "schedule_job": schedule_job,
            "health_check": health_check,
            "document_creator": document_creator,
            "skill_manager": skill_manager,
            "github_search": github_search,
            "daily_news_brief": daily_news_brief,
        }
        mod = module_map.get(module_name)
        if mod is None:
            raise SkillError(f"Unknown builtin: {module_name}")
        return mod.execute(context, inputs)
    return _wrapper


_BUILTIN_SKILLS: Dict[str, SkillExecuteFunc] = {
    "echo": _echo_execute,
    "repo_writer": _load_builtin_execute("repo_writer"),
    "command_runner": _load_builtin_execute("command_runner"),
    "service_runner": _load_builtin_execute("service_runner"),
    "network_client": _load_builtin_execute("network_client"),
    "telegram_send": _load_builtin_execute("telegram_send"),
    "warroom_send": _load_builtin_execute("warroom_send"),
    "schedule_job": _load_builtin_execute("schedule_job"),
    "health_check": _load_builtin_execute("health_check"),
    "document_creator": _load_builtin_execute("document_creator"),
    "skill_manager": _load_builtin_execute("skill_manager"),
    "github_search": _load_builtin_execute("github_search"),
    "daily_news_brief": _load_builtin_execute("daily_news_brief"),
}


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# F-007: Sandbox runner — executed inside the Docker container
# ---------------------------------------------------------------------------

_SKILL_SANDBOX_RUNNER = r'''
import json, sys, importlib.util, os
try:
    payload = json.loads(sys.stdin.read())
    skill_dir = "/skill"
    execute_py = os.path.join(skill_dir, "execute.py")
    if not os.path.exists(execute_py):
        json.dump({"success": False, "error": "execute.py not found in skill directory"}, sys.stdout)
        sys.exit(0)
    spec = importlib.util.spec_from_file_location("skill_module", execute_py)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    func = getattr(mod, "execute", None)
    if not callable(func):
        json.dump({"success": False, "error": "No callable execute() in skill module"}, sys.stdout)
        sys.exit(0)

    class _Ctx:
        def __init__(self, d):
            self.skill_name = d.get("skill_name", "")
            self.request_id = d.get("request_id", "")
            self.caller = d.get("caller", "system")
            self.metadata = d.get("metadata", {})

    ctx = _Ctx(payload.get("context", {}))
    result = func(ctx, payload.get("inputs", {}))
    json.dump({"success": True, "outputs": result}, sys.stdout)
except Exception as exc:
    json.dump({"success": False, "error": str(exc)}, sys.stdout)
'''

# Sandbox configuration for skill execution
_SKILL_SANDBOX_IMAGE = "python:3.11-slim"
_SKILL_SANDBOX_MEMORY = "256m"
_SKILL_SANDBOX_TIMEOUT_S = 60


class SkillExecutor:
    """Loads and runs skills by name.

    Built-in skills run in-process for performance.
    Non-builtin skills (user/marketplace) run in isolated Docker containers (F-007).
    """

    def __init__(self, registry: SkillRegistry):
        self._registry = registry
        self._loaded: Dict[str, SkillExecuteFunc] = {}
        self._receipts: List[Dict[str, Any]] = []

    @property
    def receipts(self) -> List[Dict[str, Any]]:
        """All receipt events generated during this executor's lifetime."""
        return list(self._receipts)

    def _emit_receipt(self, event: str, **kwargs: Any) -> Dict[str, Any]:
        """Record a receipt event."""
        receipt = {
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **kwargs,
        }
        self._receipts.append(receipt)
        logger.info("%s: %s", event, {k: v for k, v in kwargs.items() if k != "error_trace"})
        return receipt

    def _load_execute_func(self, entry: SkillEntry) -> SkillExecuteFunc:
        """Load the execute function for a skill.

        Resolution order:
        1. Already loaded in cache
        2. Built-in skill
        3. execute.py adjacent to manifest file
        """
        if entry.name in self._loaded:
            return self._loaded[entry.name]

        # Check built-ins
        if entry.name in _BUILTIN_SKILLS:
            func = _BUILTIN_SKILLS[entry.name]
            self._loaded[entry.name] = func
            return func

        # Try loading execute.py from skill directory
        if entry.manifest_path:
            manifest_dir = Path(entry.manifest_path).parent
            execute_py = manifest_dir / "execute.py"

            # Validate skill path does not escape the manifest directory
            try:
                resolved = execute_py.resolve()
                if not str(resolved).startswith(str(manifest_dir.resolve())):
                    raise SkillError(
                        f"SECURITY: Skill path escapes manifest directory: {execute_py}"
                    )
            except OSError as exc:
                raise SkillError(f"Invalid skill path: {exc}") from exc

            if execute_py.exists():
                # Warn if skill is not signature-verified
                if entry.signature_state != SignatureState.VERIFIED:
                    logger.warning(
                        "SECURITY: Loading unsigned skill '%s' from %s. "
                        "Skill code has not been signature-verified.",
                        entry.name, execute_py,
                    )
                    self._emit_receipt(
                        "skill_unsigned_load",
                        skill=entry.name,
                        path=str(execute_py),
                        signature_state=entry.signature_state.value,
                    )

                func = self._load_module_execute(execute_py, entry.name)
                self._loaded[entry.name] = func
                return func

        raise SkillError(f"No execute function found for skill '{entry.name}'")

    def _load_module_execute(self, path: Path, skill_name: str) -> SkillExecuteFunc:
        """Safely load an execute function from a Python module.

        Only used for builtin skills that have been loaded in-process.
        Non-builtin skills use _run_skill_in_sandbox() instead.
        """
        module_name = f"skill_{skill_name}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, str(path))
            if spec is None or spec.loader is None:
                raise SkillError(f"Cannot load module spec from {path}")

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            func = getattr(module, "execute", None)
            if func is None or not callable(func):
                raise SkillError(f"Module {path} has no callable 'execute' function")

            return func
        except SkillError:
            raise
        except Exception as exc:
            raise SkillError(f"Failed to load skill module {path}: {exc}") from exc

    @staticmethod
    def _is_builtin(entry: SkillEntry) -> bool:
        """Check if a skill should run in-process (builtin) or sandboxed."""
        return (
            entry.name in _BUILTIN_SKILLS
            or entry.ownership == SkillOwnership.SYSTEM
        )

    def _run_skill_in_sandbox(
        self,
        entry: SkillEntry,
        context: SkillContext,
        inputs: Dict[str, Any],
    ) -> SkillResult:
        """F-007: Execute a non-builtin skill inside a Docker sandbox container.

        The skill directory is mounted read-only at /skill. A lightweight
        runner script reads JSON from stdin, loads execute.py, and writes
        JSON results to stdout. The container has no network access, limited
        memory (256MB), and a 60-second timeout.
        """
        if not entry.manifest_path:
            return SkillResult(success=False, error="Skill has no manifest_path for sandbox execution")

        skill_dir = Path(entry.manifest_path).parent
        execute_py = skill_dir / "execute.py"
        if not execute_py.exists():
            return SkillResult(success=False, error=f"execute.py not found in {skill_dir}")

        # Build JSON payload for the sandbox runner
        payload = json.dumps({
            "context": {
                "skill_name": context.skill_name,
                "request_id": context.request_id,
                "caller": context.caller,
                "metadata": context.metadata,
            },
            "inputs": inputs,
        })

        # Build Docker command
        docker_cmd = [
            "docker", "run", "--rm",
            f"--memory={_SKILL_SANDBOX_MEMORY}",
            "--cpus=1",
            "--network=none",
            "-v", f"{skill_dir.resolve()}:/skill:ro",
            "-i",  # Keep stdin open for payload
            _SKILL_SANDBOX_IMAGE,
            "python", "-c", _SKILL_SANDBOX_RUNNER,
        ]

        self._emit_receipt(
            "skill_sandbox_start",
            skill=entry.name,
            image=_SKILL_SANDBOX_IMAGE,
            memory=_SKILL_SANDBOX_MEMORY,
            timeout_s=_SKILL_SANDBOX_TIMEOUT_S,
        )

        start = time.monotonic()
        try:
            result = subprocess.run(
                docker_cmd,
                input=payload,
                capture_output=True,
                text=True,
                timeout=_SKILL_SANDBOX_TIMEOUT_S + 5,
            )
            duration_ms = (time.monotonic() - start) * 1000

            if result.returncode != 0:
                error = f"Sandbox exited with code {result.returncode}: {result.stderr[:500]}"
                self._emit_receipt(
                    "skill_sandbox_failed",
                    skill=entry.name,
                    error=error,
                    duration_ms=round(duration_ms, 2),
                )
                return SkillResult(success=False, error=error, duration_ms=duration_ms)

            # Parse JSON output from sandbox
            try:
                output = json.loads(result.stdout)
            except json.JSONDecodeError:
                error = f"Sandbox produced invalid JSON: {result.stdout[:300]}"
                self._emit_receipt(
                    "skill_sandbox_failed",
                    skill=entry.name,
                    error=error,
                    duration_ms=round(duration_ms, 2),
                )
                return SkillResult(success=False, error=error, duration_ms=duration_ms)

            if not output.get("success", False):
                error = output.get("error", "Unknown sandbox error")
                self._emit_receipt(
                    "skill_sandbox_failed",
                    skill=entry.name,
                    error=error,
                    duration_ms=round(duration_ms, 2),
                )
                return SkillResult(success=False, error=error, duration_ms=duration_ms)

            receipt = self._emit_receipt(
                "skill_sandbox_ran",
                skill=entry.name,
                duration_ms=round(duration_ms, 2),
            )
            return SkillResult(
                success=True,
                outputs=output.get("outputs", {}),
                duration_ms=duration_ms,
                receipt=receipt,
            )

        except subprocess.TimeoutExpired:
            duration_ms = (time.monotonic() - start) * 1000
            error = f"Sandbox timed out after {_SKILL_SANDBOX_TIMEOUT_S}s"
            self._emit_receipt(
                "skill_sandbox_failed",
                skill=entry.name,
                error=error,
                duration_ms=round(duration_ms, 2),
            )
            return SkillResult(success=False, error=error, duration_ms=duration_ms)
        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            error = f"Sandbox execution error: {str(exc)[:300]}"
            self._emit_receipt(
                "skill_sandbox_failed",
                skill=entry.name,
                error=error,
                duration_ms=round(duration_ms, 2),
            )
            return SkillResult(success=False, error=error, duration_ms=duration_ms)

    def run(
        self,
        skill_name: str,
        inputs: Dict[str, Any],
        context: Optional[SkillContext] = None,
    ) -> SkillResult:
        """Execute a skill by name.

        Args:
            skill_name: Name of the skill to run.
            inputs: Input dictionary for the skill.
            context: Optional execution context.

        Returns:
            SkillResult with success/failure and outputs.
        """
        if context is None:
            context = SkillContext(skill_name=skill_name)

        entry = self._registry.get_skill(skill_name)
        if entry is None:
            # Fallback: built-in skills work without registry entries
            if skill_name in _BUILTIN_SKILLS:
                entry = SkillEntry(
                    name=skill_name,
                    version="builtin",
                    enabled=True,
                    manifest_path="",
                    ownership=SkillOwnership.SYSTEM,
                    signature_state=SignatureState.VERIFIED,
                )
            else:
                error = f"Skill '{skill_name}' not found in registry or builtins"
                self._emit_receipt("skill_failed", skill=skill_name, error=error)
                return SkillResult(success=False, error=error)

        if not entry.enabled:
            error = f"Skill '{skill_name}' is disabled"
            self._emit_receipt("skill_failed", skill=skill_name, error=error)
            return SkillResult(success=False, error=error)

        # F-007: Route non-builtin skills to Docker sandbox
        if not self._is_builtin(entry):
            logger.info(
                "Routing non-builtin skill '%s' (ownership=%s) to Docker sandbox",
                skill_name, entry.ownership.value,
            )
            return self._run_skill_in_sandbox(entry, context, inputs)

        # Builtin skills run in-process
        try:
            execute_func = self._load_execute_func(entry)
        except SkillError as exc:
            self._emit_receipt("skill_failed", skill=skill_name, error=str(exc))
            return SkillResult(success=False, error=str(exc))

        start = time.monotonic()
        try:
            outputs = execute_func(context, inputs)
            duration_ms = (time.monotonic() - start) * 1000

            receipt = self._emit_receipt(
                "skill_ran",
                skill=skill_name,
                duration_ms=round(duration_ms, 2),
            )
            return SkillResult(
                success=True,
                outputs=outputs,
                duration_ms=duration_ms,
                receipt=receipt,
            )
        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            self._emit_receipt(
                "skill_failed",
                skill=skill_name,
                error=str(exc),
                duration_ms=round(duration_ms, 2),
            )
            return SkillResult(
                success=False,
                error=str(exc),
                duration_ms=duration_ms,
            )
