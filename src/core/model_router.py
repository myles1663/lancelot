"""
ModelRouter v2 — full lane routing with escalation (Prompts 15 & 16).

Single-owner module that routes tasks to the appropriate lane:
  1. local_redaction  — PII redaction (always local)
  2. local_utility    — classify, extract, summarize, rag_rewrite
  3. flagship_fast    — orchestration, tool calls, retries
  4. flagship_deep    — planning, high-risk decisions

Escalation from fast → deep is triggered by:
  - Task type (plan, analyze, decide, architect, review)
  - Risk keywords in the input text
  - Fast lane failure (automatic retry on deep)

All routing decisions are logged and exposed to the War Room.

Public API:
    RouterDecision      — immutable record of a routing decision
    ModelRouter(registry, local_client, flagship_client)
    router.route(task_type, text, **kwargs) → RouterResult
    router.recent_decisions  → list[RouterDecision]
    router.stats             → dict
"""

import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from src.core.local_model_client import LocalModelClient, LocalModelError
from src.core.provider_profile import ProfileRegistry
from src.core.usage_tracker import UsageTracker

logger = logging.getLogger(__name__)

_MAX_RECENT = 200
_PREVIEW_LEN = 120

# Task types that always route to the deep lane
_DEEP_TASK_TYPES = frozenset({
    "plan", "architect", "decide", "review", "analyze",
    "strategy", "evaluate", "diagnose",
})

# Keywords in input text that trigger escalation to deep lane
_RISK_KEYWORDS = frozenset({
    "delete", "remove", "destroy", "drop", "irreversible",
    "production", "deploy", "rollback", "migrate",
    "security", "credential", "secret", "password",
    "critical", "urgent", "emergency",
})


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RouterDecision:
    """Immutable record of a routing decision."""
    id: str
    timestamp: str
    task_type: str
    lane: str
    model: str
    rationale: str
    elapsed_ms: float
    success: bool
    error: Optional[str] = None
    input_preview: str = ""
    output_preview: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "task_type": self.task_type,
            "lane": self.lane,
            "model": self.model,
            "rationale": self.rationale,
            "elapsed_ms": self.elapsed_ms,
            "success": self.success,
            "error": self.error,
            "input_preview": self.input_preview,
            "output_preview": self.output_preview,
        }


@dataclass
class RouterResult:
    """Result of a routing decision + execution."""
    decision: RouterDecision
    output: Optional[str] = None
    data: Optional[dict] = None
    executed: bool = False


# ---------------------------------------------------------------------------
# ModelRouter
# ---------------------------------------------------------------------------

class ModelRouter:
    """Routes tasks to the appropriate lane and logs decisions."""

    def __init__(
        self,
        registry: ProfileRegistry,
        local_client: Optional[LocalModelClient] = None,
        flagship_client: Optional[Any] = None,
    ):
        self._registry = registry
        self._local = local_client
        self._flagship = flagship_client
        self._decisions: deque[RouterDecision] = deque(maxlen=_MAX_RECENT)
        self._usage = UsageTracker()

    # ------------------------------------------------------------------
    # Main routing entry point
    # ------------------------------------------------------------------

    def route(self, task_type: str, text: str, **kwargs: Any) -> RouterResult:
        """Route a task to the appropriate lane and execute.

        Args:
            task_type: One of the defined task types.
            text: The input text for the task.
            **kwargs: Additional arguments (e.g. schema for extract_json,
                      lane="deep" to force deep lane).

        Returns:
            RouterResult with the decision and output.
        """
        start = time.monotonic()
        input_preview = text[:_PREVIEW_LEN] if text else ""

        # Determine lane
        forced_lane = kwargs.pop("lane", None)
        lane, rationale = self._determine_lane(task_type, text, forced_lane)

        if lane in ("local_redaction", "local_utility"):
            return self._execute_local(
                task_type, text, lane, rationale, input_preview, start, **kwargs
            )
        else:
            return self._execute_flagship(
                task_type, text, lane, rationale, input_preview, start, **kwargs
            )

    # ------------------------------------------------------------------
    # Lane determination
    # ------------------------------------------------------------------

    def _determine_lane(
        self, task_type: str, text: str, forced_lane: Optional[str] = None
    ) -> tuple[str, str]:
        """Determine which lane handles this task type.

        Returns (lane, rationale).
        """
        # Honour forced lane override
        if forced_lane in ("flagship_fast", "flagship_deep"):
            return forced_lane, f"Lane forced to '{forced_lane}' by caller"

        # Redaction always local, highest priority
        if task_type == "redact":
            return (
                "local_redaction",
                "PII redaction always runs locally for privacy",
            )

        # Check if it's a registered local utility task
        if self._registry.is_local_task(task_type):
            return (
                "local_utility",
                f"'{task_type}' is a registered local utility task",
            )

        # Escalation: deep lane task types
        if task_type in _DEEP_TASK_TYPES:
            return (
                "flagship_deep",
                f"'{task_type}' requires deep reasoning — escalated to deep lane",
            )

        # Escalation: risk keywords in input
        risk_word = self._detect_risk_keyword(text)
        if risk_word:
            return (
                "flagship_deep",
                f"Risk keyword '{risk_word}' detected — escalated to deep lane",
            )

        # Default to flagship fast lane
        return (
            "flagship_fast",
            f"'{task_type}' routed to flagship fast lane",
        )

    def _detect_risk_keyword(self, text: str) -> Optional[str]:
        """Check if the text contains any risk keywords."""
        words = set(text.lower().split())
        for kw in _RISK_KEYWORDS:
            if kw in words:
                return kw
        return None

    # ------------------------------------------------------------------
    # Local execution
    # ------------------------------------------------------------------

    def _execute_local(
        self,
        task_type: str,
        text: str,
        lane: str,
        rationale: str,
        input_preview: str,
        start: float,
        **kwargs: Any,
    ) -> RouterResult:
        """Execute a task on the local model."""
        if self._local is None:
            elapsed = (time.monotonic() - start) * 1000
            decision = self._record(
                task_type=task_type,
                lane=lane,
                model="local-llm",
                rationale=rationale,
                elapsed_ms=elapsed,
                success=False,
                error="Local model client not configured",
                input_preview=input_preview,
            )
            return RouterResult(decision=decision, executed=False)

        try:
            output, data = self._run_local_task(task_type, text, **kwargs)
            elapsed = (time.monotonic() - start) * 1000
            output_preview = str(output)[:_PREVIEW_LEN] if output else ""

            decision = self._record(
                task_type=task_type,
                lane=lane,
                model="local-llm",
                rationale=rationale,
                elapsed_ms=elapsed,
                success=True,
                input_preview=input_preview,
                output_preview=output_preview,
            )
            return RouterResult(
                decision=decision,
                output=output if isinstance(output, str) else str(output),
                data=data,
                executed=True,
            )
        except LocalModelError as exc:
            elapsed = (time.monotonic() - start) * 1000
            decision = self._record(
                task_type=task_type,
                lane=lane,
                model="local-llm",
                rationale=rationale,
                elapsed_ms=elapsed,
                success=False,
                error=str(exc),
                input_preview=input_preview,
            )
            return RouterResult(decision=decision, executed=False)

    def _run_local_task(
        self, task_type: str, text: str, **kwargs: Any
    ) -> tuple[Optional[str], Optional[dict]]:
        """Dispatch to the appropriate LocalModelClient method."""
        if task_type == "classify_intent":
            return self._local.classify_intent(text), None
        if task_type == "extract_json":
            schema = kwargs.get("schema", "{}")
            data = self._local.extract_json(text, schema)
            return str(data), data
        if task_type == "summarize":
            return self._local.summarize(text), None
        if task_type == "redact":
            return self._local.redact(text), None
        if task_type == "rag_rewrite":
            return self._local.rag_rewrite(text), None
        return self._local.complete(text), None

    # ------------------------------------------------------------------
    # Flagship execution
    # ------------------------------------------------------------------

    def _execute_flagship(
        self,
        task_type: str,
        text: str,
        lane: str,
        rationale: str,
        input_preview: str,
        start: float,
        **kwargs: Any,
    ) -> RouterResult:
        """Execute a task on the flagship provider."""
        from src.core.flagship_client import FlagshipError

        if self._flagship is None:
            elapsed = (time.monotonic() - start) * 1000
            decision = self._record(
                task_type=task_type,
                lane=lane,
                model="pending",
                rationale=rationale,
                elapsed_ms=elapsed,
                success=False,
                error="Flagship client not configured",
                input_preview=input_preview,
            )
            return RouterResult(decision=decision, executed=False)

        flagship_lane = "deep" if lane == "flagship_deep" else "fast"

        try:
            output = self._flagship.complete(text, lane=flagship_lane)
            elapsed = (time.monotonic() - start) * 1000
            output_preview = str(output)[:_PREVIEW_LEN] if output else ""

            # Resolve model name from the client
            model_name = self._resolve_model_name(flagship_lane)

            decision = self._record(
                task_type=task_type,
                lane=lane,
                model=model_name,
                rationale=rationale,
                elapsed_ms=elapsed,
                success=True,
                input_preview=input_preview,
                output_preview=output_preview,
            )
            return RouterResult(
                decision=decision, output=output, executed=True,
            )
        except FlagshipError as exc:
            # Escalation on failure: if fast lane failed, retry on deep
            if flagship_lane == "fast":
                logger.warning(
                    "Fast lane failed for '%s', escalating to deep: %s",
                    task_type, exc,
                )
                return self._retry_on_deep(
                    task_type, text, rationale, input_preview, start, exc, **kwargs
                )

            elapsed = (time.monotonic() - start) * 1000
            model_name = self._resolve_model_name(flagship_lane)
            decision = self._record(
                task_type=task_type,
                lane=lane,
                model=model_name,
                rationale=rationale,
                elapsed_ms=elapsed,
                success=False,
                error=str(exc),
                input_preview=input_preview,
            )
            return RouterResult(decision=decision, executed=False)

    def _retry_on_deep(
        self,
        task_type: str,
        text: str,
        original_rationale: str,
        input_preview: str,
        start: float,
        original_error: Exception,
        **kwargs: Any,
    ) -> RouterResult:
        """Retry a failed fast-lane task on the deep lane."""
        from src.core.flagship_client import FlagshipError

        rationale = (
            f"Escalated from fast to deep after failure: {original_error}"
        )

        try:
            output = self._flagship.complete(text, lane="deep")
            elapsed = (time.monotonic() - start) * 1000
            output_preview = str(output)[:_PREVIEW_LEN] if output else ""
            model_name = self._resolve_model_name("deep")

            decision = self._record(
                task_type=task_type,
                lane="flagship_deep",
                model=model_name,
                rationale=rationale,
                elapsed_ms=elapsed,
                success=True,
                input_preview=input_preview,
                output_preview=output_preview,
            )
            return RouterResult(
                decision=decision, output=output, executed=True,
            )
        except FlagshipError as exc:
            elapsed = (time.monotonic() - start) * 1000
            model_name = self._resolve_model_name("deep")
            decision = self._record(
                task_type=task_type,
                lane="flagship_deep",
                model=model_name,
                rationale=rationale,
                elapsed_ms=elapsed,
                success=False,
                error=str(exc),
                input_preview=input_preview,
            )
            return RouterResult(decision=decision, executed=False)

    def _resolve_model_name(self, lane: str) -> str:
        """Get the model name from the flagship client's profile."""
        try:
            if hasattr(self._flagship, '_profile'):
                lc = self._flagship._get_lane_config(lane)
                return lc.model
        except Exception:
            pass
        return f"flagship-{lane}"

    # ------------------------------------------------------------------
    # Receipt logging
    # ------------------------------------------------------------------

    def _record(self, **kwargs: Any) -> RouterDecision:
        """Create and store a RouterDecision."""
        decision = RouterDecision(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat(),
            **kwargs,
        )
        self._decisions.append(decision)
        self._usage.record(decision)
        logger.info(
            "Router decision: %s → %s (%s) [%.1fms]",
            decision.task_type,
            decision.lane,
            "ok" if decision.success else "fail",
            decision.elapsed_ms,
        )
        return decision

    # ------------------------------------------------------------------
    # War Room accessors
    # ------------------------------------------------------------------

    @property
    def recent_decisions(self) -> list[RouterDecision]:
        """Return recent routing decisions (newest first)."""
        return list(reversed(self._decisions))

    @property
    def usage(self) -> UsageTracker:
        """Return the usage tracker instance."""
        return self._usage

    @property
    def stats(self) -> dict:
        """Routing statistics for the War Room."""
        decisions = list(self._decisions)
        total = len(decisions)
        if total == 0:
            return {
                "total_decisions": 0,
                "by_lane": {},
                "success_rate": 0.0,
                "avg_elapsed_ms": 0.0,
            }

        by_lane: dict[str, int] = {}
        successes = 0
        total_ms = 0.0

        for d in decisions:
            by_lane[d.lane] = by_lane.get(d.lane, 0) + 1
            if d.success:
                successes += 1
            total_ms += d.elapsed_ms

        return {
            "total_decisions": total,
            "by_lane": by_lane,
            "success_rate": round(successes / total, 4) if total else 0.0,
            "avg_elapsed_ms": round(total_ms / total, 2) if total else 0.0,
        }
