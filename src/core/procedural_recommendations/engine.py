"""Deterministic proactive procedural recommendation engine.

The MVP intentionally avoids model calls. It looks for a few high-signal
patterns and records auditable candidates without creating a second governance
policy path.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class DeliveryMode(str, Enum):
    """Where a recommendation should appear."""

    NONE = "none"
    SILENT_SIGNAL = "silent_signal"
    WAR_ROOM = "war_room"
    INLINE_NUDGE = "inline_nudge"
    ACTION_OFFER = "action_offer"


@dataclass(frozen=True)
class ScoreBreakdown:
    impact: int = 0
    risk_reduction: int = 0
    repetition: int = 0
    confidence: int = 0
    timing_fit: int = 0
    domain_standard_strength: int = 0
    interruption_cost: int = 0
    user_fatigue_risk: int = 0

    @property
    def total(self) -> int:
        return (
            self.impact
            + self.risk_reduction
            + self.repetition
            + self.confidence
            + self.timing_fit
            + self.domain_standard_strength
            - self.interruption_cost
            - self.user_fatigue_risk
        )

    def to_dict(self) -> dict[str, int]:
        data = asdict(self)
        data["total"] = self.total
        return data


@dataclass(frozen=True)
class RecommendationCandidate:
    category: str
    title: str
    observation: str
    risk_or_opportunity: str
    recommendation: str
    evidence: list[str]
    score_breakdown: ScoreBreakdown
    suggested_action: str = ""

    @property
    def score(self) -> int:
        return self.score_breakdown.total

    def delivery_mode(self) -> DeliveryMode:
        if self.score < 6:
            return DeliveryMode.NONE
        if self.score < 10:
            return DeliveryMode.WAR_ROOM
        if self.score < 14:
            return DeliveryMode.INLINE_NUDGE
        return DeliveryMode.ACTION_OFFER

    def inline_text(self) -> str:
        return (
            "One thing worth calling out: "
            f"{self.observation} {self.risk_or_opportunity} "
            f"{self.recommendation}"
        )

    def to_receipt_payload(self, delivery_mode: DeliveryMode) -> dict[str, Any]:
        return {
            "category": self.category,
            "title": self.title,
            "score": self.score,
            "score_breakdown": self.score_breakdown.to_dict(),
            "delivery_mode": delivery_mode.value,
            "observation": self.observation,
            "risk_or_opportunity": self.risk_or_opportunity,
            "recommendation": self.recommendation,
            "suggested_action": self.suggested_action,
            "evidence": list(self.evidence),
            "user_response": "pending",
        }


@dataclass(frozen=True)
class RecommendationContext:
    user_message: str
    response_text: str
    history: list[dict[str, str]] = field(default_factory=list)
    tool_receipts: list[Any] = field(default_factory=list)
    channel: str = "api"
    quest_id: str = ""
    session_id: str = ""
    operator_id: str = ""


@dataclass(frozen=True)
class RecommendationDecision:
    response_text: str
    surfaced: RecommendationCandidate | None = None
    recorded: list[RecommendationCandidate] = field(default_factory=list)
    recommendation_ids: dict[str, str] = field(default_factory=dict)
    delivery_modes: dict[str, DeliveryMode] = field(default_factory=dict)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", (text or "").lower()))


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = (text or "").lower()
    for term in terms:
        normalized = (term or "").lower().strip()
        if not normalized:
            continue
        if re.search(rf"(?<![a-z0-9_]){re.escape(normalized)}(?![a-z0-9_])", lowered):
            return True
    return False


def _recent_user_messages(history: list[dict[str, str]], limit: int = 10) -> list[str]:
    messages: list[str] = []
    for entry in reversed(history or []):
        if entry.get("role") == "user":
            content = str(entry.get("content") or "")
            if content.strip():
                messages.append(content)
        if len(messages) >= limit:
            break
    return list(reversed(messages))


def _recent_assistant_messages(history: list[dict[str, str]], limit: int = 10) -> list[str]:
    messages: list[str] = []
    for entry in reversed(history or []):
        if entry.get("role") == "assistant":
            content = str(entry.get("content") or "")
            if content.strip():
                messages.append(content)
        if len(messages) >= limit:
            break
    return list(reversed(messages))


def _similar_count(current: str, previous: list[str]) -> int:
    current_words = _words(current)
    if not current_words:
        return 0
    count = 0
    for message in previous:
        prior_words = _words(message)
        if not prior_words:
            continue
        overlap = len(current_words & prior_words) / max(1, min(len(current_words), len(prior_words)))
        if overlap >= 0.35:
            count += 1
    return count


def _tool_names(tool_receipts: list[Any]) -> list[str]:
    names: list[str] = []
    for receipt in tool_receipts or []:
        if isinstance(receipt, dict):
            value = receipt.get("skill") or receipt.get("tool") or receipt.get("action_name")
        else:
            value = (
                getattr(receipt, "skill", None)
                or getattr(receipt, "tool", None)
                or getattr(receipt, "action_name", None)
            )
        text = str(value or "").strip()
        if text and text not in names:
            names.append(text)
    return names


def _already_recommended(candidate: RecommendationCandidate, history: list[dict[str, str]]) -> bool:
    haystack = "\n".join(_recent_assistant_messages(history, limit=8)).lower()
    if not haystack:
        return False
    return candidate.title.lower() in haystack or candidate.recommendation.lower()[:80] in haystack


class RecommendationEngine:
    """Generate and score procedural recommendation candidates."""

    DOCUMENT_TERMS = (
        "draft",
        "write",
        "rewrite",
        "polish",
        "edit this",
        "proposal",
        "memo",
        "document",
        "report",
        "email",
        "letter",
        "slides",
        "brief",
    )
    SOFTWARE_TERMS = (
        "production",
        "public repo",
        "public repository",
        "release",
        "deploy",
        "ship",
        "publish",
        "github",
        "main branch",
        "protected branch",
        "show hn",
        "ci",
        "tests",
    )
    WORKFLOW_TERMS = (
        "again",
        "repeat",
        "same",
        "every time",
        "workflow",
        "process",
        "sop",
        "checklist",
        "template",
        "recurring",
    )

    def __init__(self, *, sensitivity: str = "balanced"):
        self.sensitivity = sensitivity if sensitivity in {"low", "balanced", "high"} else "balanced"

    def candidates(self, context: RecommendationContext) -> list[RecommendationCandidate]:
        if self._skip_context(context):
            return []

        software = self._software_maturity_candidate(context)
        tool_mode = self._tool_mode_candidate(context)
        workflow = None if tool_mode is not None else self._workflow_maturity_candidate(context)
        candidates = [
            candidate
            for candidate in (software, tool_mode, workflow)
            if candidate is not None
        ]
        return sorted(candidates, key=lambda c: c.score, reverse=True)

    def decide(self, context: RecommendationContext) -> RecommendationDecision:
        response = context.response_text
        candidates = self.candidates(context)
        recordable: list[RecommendationCandidate] = []
        surfaced: RecommendationCandidate | None = None
        delivery_modes: dict[str, DeliveryMode] = {}

        for candidate in candidates:
            if _already_recommended(candidate, context.history):
                continue
            mode = self._delivery_mode(candidate)
            if mode == DeliveryMode.NONE:
                continue
            delivery_modes[candidate.title] = mode
            recordable.append(candidate)
            if surfaced is None and mode in (DeliveryMode.INLINE_NUDGE, DeliveryMode.ACTION_OFFER):
                surfaced = candidate

        if surfaced is not None:
            separator = "\n\n" if response.strip() else ""
            response = f"{response}{separator}{surfaced.inline_text()}"

        return RecommendationDecision(
            response_text=response,
            surfaced=surfaced,
            recorded=recordable[:3],
            delivery_modes=delivery_modes,
        )

    def _delivery_mode(self, candidate: RecommendationCandidate) -> DeliveryMode:
        score = candidate.score
        if self.sensitivity == "low":
            score -= 2
        elif self.sensitivity == "high":
            score += 2
        if score < 6:
            return DeliveryMode.NONE
        if score < 10:
            return DeliveryMode.WAR_ROOM
        if score < 14:
            return DeliveryMode.INLINE_NUDGE
        return DeliveryMode.ACTION_OFFER

    def _skip_context(self, context: RecommendationContext) -> bool:
        message = (context.user_message or "").strip()
        response = (context.response_text or "").strip()
        if not message or not response:
            return True
        if len(message) <= 80 and message.lower() in {
            "ok",
            "okay",
            "yes",
            "yep",
            "sure",
            "thanks",
            "thank you",
            "go for it",
        }:
            return True
        lowered = response.lower()
        return (
            "paused for commander approval" in lowered
            or "pending commander approval" in lowered
            or response.startswith("Error generating response:")
        )

    def _software_maturity_candidate(
        self,
        context: RecommendationContext,
    ) -> RecommendationCandidate | None:
        combined = f"{context.user_message}\n{context.response_text}"
        if not _contains_any(combined, self.SOFTWARE_TERMS):
            return None

        production_signal = _contains_any(
            combined,
            ("production", "public repo", "public repository", "release", "deploy", "ship", "publish", "show hn"),
        )
        repo_signal = _contains_any(combined, ("github", "repo", "repository", "main branch", "branch"))
        maturity_control_signal = _contains_any(combined, ("ci", "test", "protected branch", "release tag", "rollback", "pr review"))
        if not (production_signal or repo_signal):
            return None

        evidence = []
        if production_signal:
            evidence.append("Current turn references public, release, deploy, publish, or production work.")
        if repo_signal:
            evidence.append("Current turn references repository or branch workflow.")
        if not maturity_control_signal:
            evidence.append("No explicit CI, release tag, rollback, or protected-branch control was detected.")
        tools = _tool_names(context.tool_receipts)
        if tools:
            evidence.append(f"Tool activity in this turn: {', '.join(tools[:4])}.")

        repetition = min(4, _similar_count(context.user_message, _recent_user_messages(context.history)[:-1]))
        breakdown = ScoreBreakdown(
            impact=4 if production_signal else 3,
            risk_reduction=4 if production_signal else 3,
            repetition=max(1, repetition),
            confidence=3 if production_signal and repo_signal else 2,
            timing_fit=3 if production_signal else 2,
            domain_standard_strength=4,
            interruption_cost=2,
            user_fatigue_risk=1,
        )
        return RecommendationCandidate(
            category="software_development",
            title="Add production software controls",
            observation="this is starting to behave like production or public-facing software.",
            risk_or_opportunity="The risk is shipping credible-looking work without CI, protected branches, release tags, or rollback discipline.",
            recommendation="I recommend treating CI checks, PR review, protected main, release tags, and a short release SOP as the next operating layer.",
            suggested_action="Create a release-readiness checklist or SOP.",
            evidence=evidence,
            score_breakdown=breakdown,
        )

    def _tool_mode_candidate(
        self,
        context: RecommendationContext,
    ) -> RecommendationCandidate | None:
        if not _contains_any(context.user_message, self.DOCUMENT_TERMS):
            return None

        recent = _recent_user_messages(context.history)
        document_like = [
            msg
            for msg in recent
            if _contains_any(msg, self.DOCUMENT_TERMS)
        ]
        if len(document_like) < 2 and not _contains_any(context.response_text, ("##", "|", "executive summary", "proposal")):
            return None

        breakdown = ScoreBreakdown(
            impact=2,
            risk_reduction=1,
            repetition=min(4, max(1, len(document_like) - 1)),
            confidence=3 if len(document_like) >= 3 else 2,
            timing_fit=2,
            domain_standard_strength=2,
            interruption_cost=1,
            user_fatigue_risk=1,
        )
        return RecommendationCandidate(
            category="tool_mode_mismatch",
            title="Move repeated document work into a document workflow",
            observation="we are using chat for content that looks document-like or template-like.",
            risk_or_opportunity="The friction is that chat becomes a temporary staging area, which makes reuse, formatting, and revision harder.",
            recommendation="I recommend moving this into a document workflow or reusable template if we keep iterating on it.",
            suggested_action="Create a document/template workflow.",
            evidence=[
                f"Detected {len(document_like)} recent document-like request(s).",
                "Current turn asks for drafting, writing, editing, reporting, or proposal-style output.",
            ],
            score_breakdown=breakdown,
        )

    def _workflow_maturity_candidate(
        self,
        context: RecommendationContext,
    ) -> RecommendationCandidate | None:
        recent = _recent_user_messages(context.history)
        repetition = _similar_count(context.user_message, recent[:-1])
        explicit_workflow_signal = _contains_any(context.user_message, self.WORKFLOW_TERMS)
        if repetition < 2 and not explicit_workflow_signal:
            return None

        breakdown = ScoreBreakdown(
            impact=3,
            risk_reduction=2,
            repetition=min(4, max(repetition, 1 if explicit_workflow_signal else 0)),
            confidence=3 if repetition >= 2 else 2,
            timing_fit=3 if explicit_workflow_signal else 2,
            domain_standard_strength=3,
            interruption_cost=1,
            user_fatigue_risk=1,
        )
        return RecommendationCandidate(
            category="workflow_maturity",
            title="Formalize repeatable workflow",
            observation="this is starting to look like a repeatable workflow rather than a one-off task.",
            risk_or_opportunity="The opportunity is to reduce drift and manual rework by capturing the trigger, owner, steps, validation, and fallback.",
            recommendation="I recommend turning it into a short SOP, checklist, or reusable skill before the pattern spreads.",
            suggested_action="Draft SOP/checklist for the repeated workflow.",
            evidence=[
                f"Detected {repetition} similar recent request(s).",
                "Current turn includes repeat/workflow language." if explicit_workflow_signal else "Recent turns share overlapping task language.",
            ],
            score_breakdown=breakdown,
        )


def apply_procedural_recommendations(
    context: RecommendationContext,
    *,
    receipt_service: Any = None,
    recommendation_store: Any = None,
    actioncard_factory: Any = None,
) -> RecommendationDecision:
    """Apply recommendation decisioning and emit best-effort receipts."""

    sensitivity = os.getenv("LANCELOT_PROCEDURAL_RECOMMENDATION_SENSITIVITY", "balanced").strip().lower()
    engine = RecommendationEngine(sensitivity=sensitivity)
    decision = engine.decide(context)
    if recommendation_store is not None:
        decision = _persist_recommendations(recommendation_store, context, decision)
    if receipt_service is not None:
        _emit_receipts(receipt_service, context, decision)
    if recommendation_store is not None and actioncard_factory is not None:
        _present_actioncard(recommendation_store, actioncard_factory, decision)
    return decision


def _persist_recommendations(
    recommendation_store: Any,
    context: RecommendationContext,
    decision: RecommendationDecision,
) -> RecommendationDecision:
    recommendation_ids: dict[str, str] = {}
    kept: list[RecommendationCandidate] = []
    surfaced = decision.surfaced
    response_text = decision.response_text

    for candidate in decision.recorded:
        try:
            if recommendation_store.should_suppress(
                category=candidate.category,
                title=candidate.title,
                recommendation=candidate.recommendation,
                operator_id=context.operator_id or "",
            ):
                if candidate == surfaced:
                    response_text = context.response_text
                    surfaced = None
                continue
            mode = decision.delivery_modes.get(candidate.title, candidate.delivery_mode())
            record = recommendation_store.upsert_candidate(
                category=candidate.category,
                title=candidate.title,
                observation=candidate.observation,
                risk_or_opportunity=candidate.risk_or_opportunity,
                recommendation=candidate.recommendation,
                suggested_action=candidate.suggested_action,
                score=candidate.score,
                score_breakdown=candidate.score_breakdown.to_dict(),
                evidence=list(candidate.evidence),
                delivery_mode=mode.value,
                quest_id=context.quest_id or "",
                session_id=context.session_id or "",
                operator_id=context.operator_id or "",
                channel=context.channel or "",
            )
            recommendation_ids[candidate.title] = record.recommendation_id
            kept.append(candidate)
        except Exception as exc:  # pragma: no cover - advisory path must not break chat
            logger.warning(
                "procedural_recommendation_persist_failed",
                extra={"error": str(exc), "category": candidate.category},
            )
            kept.append(candidate)

    if surfaced is not None and surfaced not in kept:
        surfaced = None
        response_text = context.response_text

    return RecommendationDecision(
        response_text=response_text,
        surfaced=surfaced,
        recorded=kept,
        recommendation_ids=recommendation_ids,
        delivery_modes=decision.delivery_modes,
    )


def _present_actioncard(
    recommendation_store: Any,
    actioncard_factory: Any,
    decision: RecommendationDecision,
) -> None:
    candidate = decision.surfaced
    if candidate is None:
        return
    mode = decision.delivery_modes.get(candidate.title, candidate.delivery_mode())
    if mode != DeliveryMode.ACTION_OFFER:
        return
    recommendation_id = decision.recommendation_ids.get(candidate.title)
    if not recommendation_id:
        return
    try:
        record = recommendation_store.get(recommendation_id)
        if record is None or record.actioncard_id:
            return
        if not hasattr(actioncard_factory, "from_procedural_recommendation"):
            return
        card = actioncard_factory.from_procedural_recommendation(record)
        recommendation_store.set_actioncard_id(recommendation_id, card.card_id)
    except Exception as exc:  # pragma: no cover - action card path is best-effort
        logger.warning(
            "procedural_recommendation_actioncard_failed",
            extra={"error": str(exc), "recommendation_id": recommendation_id},
        )


def _emit_receipts(
    receipt_service: Any,
    context: RecommendationContext,
    decision: RecommendationDecision,
) -> None:
    if not decision.recorded:
        return

    try:
        from receipts import ActionType, CognitionTier, ReceiptStatus, create_finalized_receipt
    except Exception:  # pragma: no cover - package path differs in some runners
        try:
            from src.shared.receipts import ActionType, CognitionTier, ReceiptStatus, create_finalized_receipt
        except Exception as exc:  # pragma: no cover
            logger.warning("procedural_recommendation_receipt_import_failed", extra={"error": str(exc)})
            return

    for candidate in decision.recorded:
        mode = decision.delivery_modes.get(candidate.title, candidate.delivery_mode())
        if mode == DeliveryMode.NONE:
            continue
        try:
            receipt = create_finalized_receipt(
                getattr(ActionType, "PROCEDURAL_RECOMMENDATION", ActionType.SYSTEM),
                "procedural_recommendation",
                {
                    "category": candidate.category,
                    "title": candidate.title,
                    "score": candidate.score,
                },
                outputs=candidate.to_receipt_payload(mode),
                status=ReceiptStatus.SUCCESS,
                tier=CognitionTier.DETERMINISTIC,
                quest_id=context.quest_id or None,
                metadata={
                    "subsystem": "procedural_recommendations",
                    "delivery_mode": mode.value,
                    "channel": context.channel,
                    "operator_id": context.operator_id or None,
                    "session_id": context.session_id or None,
                    "surfaced_inline": decision.surfaced == candidate,
                },
                operator_id=context.operator_id or None,
                session_id=context.session_id or None,
            )
            receipt_service.create(receipt)
        except Exception as exc:  # pragma: no cover - advisory path must not break chat
            logger.warning(
                "procedural_recommendation_receipt_failed",
                extra={"error": str(exc), "category": candidate.category},
            )
