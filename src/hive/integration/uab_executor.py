"""
HIVE UAB Executor — translates subtask descriptions into real UAB actions.

Each sub-agent's action_executor.  For every subtask action:
1. Connect to the target app via UABProvider
2. Enumerate UI elements to understand current state
3. Use LLM to plan specific UAB commands from the subtask description
4. Execute those commands against the UAB daemon
5. Return results with before/after state

This is the bridge from "governed planning" to "governed execution."
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── LLM Prompt for UAB Step Planning ─────────────────────────────────────────

_STEP_PLANNER_PROMPT = """\
You are a desktop automation agent executing a single subtask.
You must produce specific UAB (Universal App Bridge) commands.

## Subtask
{description}

## Target Application
- PID: {pid}
- App Name: {app_name}
- Window Title: {window_title}

## Current UI Elements (top-level)
{elements_summary}

## Available UAB Commands
- {{"method": "act", "element_id": "<id>", "action": "click"}}
- {{"method": "act", "element_id": "<id>", "action": "type", "params": {{"text": "..."}}}}
- {{"method": "act", "element_id": "<id>", "action": "clear"}}
- {{"method": "act", "element_id": "<id>", "action": "focus"}}
- {{"method": "act", "element_id": "<id>", "action": "select"}}
- {{"method": "keypress", "key": "<key>"}} — send keypress (Tab, Enter, etc.)
- {{"method": "hotkey", "keys": ["ctrl", "a"]}} — send hotkey combo
- {{"method": "maximize"}} — bring window to foreground / maximize
- {{"method": "restore"}} — restore window
- {{"method": "state"}} — read current window state
- {{"method": "query", "selector": {{"type": "edit"}}}} — find specific elements

## Rules
- Use element IDs from the Current UI Elements list — look for elements with "type" and "click" in their actions
- IMPORTANT: Target the specific actionable element (e.g. type=textarea, type=edit, type=textbox), NOT the parent window or container
- For typing: first click the textarea/edit element to focus it, then use act with action "type" on that SAME element
- For verification: use "state" to read the window state
- Use "maximize" to bring a window to the foreground
- If no suitable element found, use keypress/hotkey as fallback
- Return 1-5 steps maximum

## Output
Return ONLY a JSON array of steps. No markdown fences, no explanation.
Example: [{{"method": "act", "element_id": "edit1", "action": "click"}}, {{"method": "act", "element_id": "edit1", "action": "type", "params": {{"text": "hello"}}}}]
"""


def _summarize_elements(elements: list, max_items: int = 50) -> str:
    """Summarize UI elements for the LLM prompt, including children.

    Flattens the tree so the LLM can see actionable elements like text editors,
    buttons, and inputs that are nested inside containers.
    """
    if not elements:
        return "No elements discovered"

    lines = []

    def _process(elem, depth=0):
        eid = elem.id if hasattr(elem, "id") else elem.get("id", "?")
        etype = elem.type if hasattr(elem, "type") else elem.get("type", "?")
        label = elem.label if hasattr(elem, "label") else elem.get("label", "")
        actions = elem.actions if hasattr(elem, "actions") else elem.get("actions", [])
        children = elem.children if hasattr(elem, "children") else elem.get("children", [])

        indent = "  " * depth
        line = f"{indent}- id={eid}  type={etype}"
        if label:
            line += f'  label="{label}"'
        if actions:
            line += f"  actions={actions}"
        lines.append(line)

        # Recurse into children (important: text editors are often inside containers)
        for child in children:
            if len(lines) < max_items:
                _process(child, depth + 1)

    for elem in elements:
        if len(lines) < max_items:
            _process(elem)

    if len(lines) >= max_items:
        lines.append(f"  ... (truncated)")

    return "\n".join(lines)


class HiveUABExecutor:
    """Action executor that translates subtask descriptions into real UAB calls.

    Created once per HIVE instance, called by each SubAgentRuntime in its thread.
    Thread-safe: UABProvider uses independent HTTP connections per call.
    """

    def __init__(
        self,
        uab_provider,
        llm_router=None,
    ):
        self._uab = uab_provider
        self._llm = llm_router  # _OrchestratorRouterAdapter or ModelRouter

    def __call__(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a subtask action via real UAB calls.

        Args:
            action: Dict with keys:
                - action: "execute_subtask"
                - spec: subtask description string
                - context: dict with target_app, target_pid, etc.

        Returns:
            Dict with steps executed and overall success.
        """
        spec = action.get("spec", "")
        context = action.get("context", {})
        pid = context.get("target_pid")
        app_name = context.get("target_app", "unknown")

        if not pid:
            return {"success": False, "error": "No target_pid in context", "steps": []}

        start_time = time.monotonic()
        step_results = []

        try:
            # Step 1: Connect to the target app
            conn_result = self._uab.connect(pid)
            step_results.append({
                "method": "connect",
                "pid": pid,
                "success": conn_result.success,
                "error": conn_result.error_message,
            })

            if not conn_result.success:
                # Try to continue anyway — app might already be connected
                logger.warning(
                    "UAB connect to PID %d returned success=False: %s (continuing)",
                    pid, conn_result.error_message,
                )

            # Step 2: Get current state + enumerate elements
            app_state = self._uab.state(pid)
            window_title = app_state.window_title or ""

            elements = self._uab.enumerate(pid)

            # Step 3: Plan specific UAB steps via LLM
            uab_steps = self._plan_steps(spec, pid, app_name, window_title, elements)
            logger.info(
                "UAB executor planned %d steps for PID %d: %s",
                len(uab_steps), pid, spec[:80],
            )

            # Step 4: Execute each UAB step
            for i, step in enumerate(uab_steps):
                step_result = self._execute_step(step, pid)
                step_results.append(step_result)

                if not step_result.get("success", False):
                    logger.warning(
                        "UAB step %d/%d failed: %s",
                        i + 1, len(uab_steps), step_result.get("error", "unknown"),
                    )
                    # Don't abort on query/state failures — they're informational
                    if step.get("method") not in ("state", "query"):
                        break

            elapsed_ms = int((time.monotonic() - start_time) * 1000)

            return {
                "success": all(
                    r.get("success", False)
                    for r in step_results
                    if r.get("method") not in ("connect",)  # Connect failures are soft
                ),
                "steps": step_results,
                "duration_ms": elapsed_ms,
                "pid": pid,
                "app_name": app_name,
                "spec": spec,
            }

        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            logger.error("UAB executor error for PID %d: %s", pid or 0, exc)
            return {
                "success": False,
                "error": str(exc),
                "steps": step_results,
                "duration_ms": elapsed_ms,
            }

    # ── Step Planning ────────────────────────────────────────────────────

    def _plan_steps(
        self,
        description: str,
        pid: int,
        app_name: str,
        window_title: str,
        elements: list,
    ) -> List[Dict[str, Any]]:
        """Use LLM to plan specific UAB steps from subtask description."""
        if self._llm is None:
            # No LLM — fall back to heuristic planning
            return self._heuristic_plan(description, pid, app_name, elements)

        elements_summary = _summarize_elements(elements)

        prompt = _STEP_PLANNER_PROMPT.format(
            description=description,
            pid=pid,
            app_name=app_name,
            window_title=window_title,
            elements_summary=elements_summary,
        )

        try:
            result = self._llm.route(task_type="plan", text=prompt)
            if result.output is None:
                logger.warning("LLM returned no output for step planning, using heuristic")
                return self._heuristic_plan(description, pid, app_name, elements)

            raw = result.output.strip()
            # Strip markdown fences
            if raw.startswith("```"):
                lines = raw.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                raw = "\n".join(lines)

            steps = json.loads(raw)
            if not isinstance(steps, list):
                steps = [steps]

            logger.info("LLM planned %d UAB steps", len(steps))
            return steps[:10]  # Safety cap

        except Exception as exc:
            logger.warning("LLM step planning failed: %s, using heuristic", exc)
            return self._heuristic_plan(description, pid, app_name, elements)

    def _heuristic_plan(
        self,
        description: str,
        pid: int,
        app_name: str,
        elements: list,
    ) -> List[Dict[str, Any]]:
        """Fallback heuristic planner when LLM is unavailable."""
        desc_lower = description.lower()
        steps = []

        # Find the main editor/text area element
        editor_id = self._find_editor_element(elements)

        if "verify" in desc_lower and ("open" in desc_lower or "accessible" in desc_lower):
            steps.append({"method": "state"})

        elif "foreground" in desc_lower or "focus" in desc_lower and "window" in desc_lower:
            steps.append({"method": "restore"})
            steps.append({"method": "maximize"})

        elif "click" in desc_lower and ("text" in desc_lower or "edit" in desc_lower or "area" in desc_lower):
            if editor_id:
                steps.append({"method": "act", "element_id": editor_id, "action": "click"})
            else:
                steps.append({"method": "act", "element_id": "", "action": "focus"})

        elif "type" in desc_lower:
            # Extract the text to type from the description
            text = self._extract_quoted_text(description)
            if text and editor_id:
                steps.append({"method": "act", "element_id": editor_id, "action": "click"})
                steps.append({"method": "act", "element_id": editor_id, "action": "type", "params": {"text": text}})
            elif text:
                steps.append({"method": "hotkey", "keys": ["ctrl", "a"]})
                steps.append({"method": "keypress", "key": "Delete"})
                # Use keypress for each character as fallback
                steps.append({"method": "act", "element_id": "", "action": "type", "params": {"text": text}})

        elif "verify" in desc_lower and "text" in desc_lower:
            steps.append({"method": "state"})
            if editor_id:
                steps.append({"method": "query", "selector": {"id": editor_id}})

        # Default: just get state
        if not steps:
            steps.append({"method": "state"})

        return steps

    def _find_editor_element(self, elements: list) -> Optional[str]:
        """Find the main text editor element from a UI tree (recursive)."""
        def _search(elem_list) -> Optional[str]:
            for elem in elem_list:
                eid = elem.id if hasattr(elem, "id") else elem.get("id", "")
                etype = (elem.type if hasattr(elem, "type") else elem.get("type", "")).lower()
                label = (elem.label if hasattr(elem, "label") else elem.get("label", "") or "").lower()
                actions = elem.actions if hasattr(elem, "actions") else elem.get("actions", [])

                # Match by type
                if etype in ("edit", "text", "textarea", "textbox", "richtext",
                             "document", "richedit20w"):
                    return eid
                # Match by label
                if "editor" in label or "text editor" in label:
                    return eid
                # Match by having "type" in actions (indicating it's an input element)
                if "type" in actions and etype not in ("window",):
                    return eid

                # Recurse into children
                children = elem.children if hasattr(elem, "children") else elem.get("children", [])
                found = _search(children)
                if found:
                    return found
            return None

        return _search(elements)

    @staticmethod
    def _extract_quoted_text(description: str) -> Optional[str]:
        """Extract text between quotes from a description."""
        import re
        # Try single quotes first, then double quotes
        match = re.search(r"'([^']+)'", description)
        if match:
            return match.group(1)
        match = re.search(r'"([^"]+)"', description)
        if match:
            return match.group(1)
        # Try after "text:" or "type:"
        match = re.search(r"(?:type|text|write)[:\s]+(.+?)(?:\.|$)", description, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return None

    # ── Step Execution ───────────────────────────────────────────────────

    def _execute_step(
        self,
        step: Dict[str, Any],
        pid: int,
    ) -> Dict[str, Any]:
        """Execute a single UAB step and return the result."""
        method = step.get("method", "")
        start = time.monotonic()

        try:
            if method == "act":
                element_id = step.get("element_id", "")
                action = step.get("action", "click")
                params = step.get("params", {})
                result = self._uab.act(pid, element_id, action, params)
                return {
                    "method": "act",
                    "action": action,
                    "element_id": element_id,
                    "success": result.success,
                    "error": result.error_message,
                    "duration_ms": result.duration_ms,
                    "state_changes": result.state_changes,
                }

            elif method == "keypress":
                key = step.get("key", "")
                result = self._uab.keypress(pid, key)
                return {
                    "method": "keypress",
                    "key": key,
                    "success": result.success,
                    "error": result.error_message,
                    "duration_ms": result.duration_ms,
                }

            elif method == "hotkey":
                keys = step.get("keys", [])
                result = self._uab.hotkey(pid, keys)
                return {
                    "method": "hotkey",
                    "keys": keys,
                    "success": result.success,
                    "error": result.error_message,
                    "duration_ms": result.duration_ms,
                }

            elif method == "maximize":
                result = self._uab.maximize(pid)
                return {
                    "method": "maximize",
                    "success": result.success,
                    "error": result.error_message,
                    "duration_ms": result.duration_ms,
                }

            elif method == "restore":
                result = self._uab.restore(pid)
                return {
                    "method": "restore",
                    "success": result.success,
                    "error": result.error_message,
                    "duration_ms": result.duration_ms,
                }

            elif method == "state":
                result = self._uab.state(pid)
                return {
                    "method": "state",
                    "success": True,
                    "window_title": result.window_title,
                    "focused": result.focused,
                    "duration_ms": int((time.monotonic() - start) * 1000),
                }

            elif method == "query":
                selector = step.get("selector", {})
                result = self._uab.query(pid, selector)
                return {
                    "method": "query",
                    "success": True,
                    "element_count": len(result) if isinstance(result, list) else 0,
                    "duration_ms": int((time.monotonic() - start) * 1000),
                }

            elif method == "connect":
                result = self._uab.connect(pid)
                return {
                    "method": "connect",
                    "success": result.success,
                    "error": result.error_message,
                    "duration_ms": int((time.monotonic() - start) * 1000),
                }

            else:
                return {
                    "method": method,
                    "success": False,
                    "error": f"Unknown UAB method: {method}",
                    "duration_ms": int((time.monotonic() - start) * 1000),
                }

        except Exception as exc:
            return {
                "method": method,
                "success": False,
                "error": str(exc)[:200],
                "duration_ms": int((time.monotonic() - start) * 1000),
            }
