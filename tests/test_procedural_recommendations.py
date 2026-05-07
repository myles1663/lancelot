from types import SimpleNamespace

import feature_flags

from procedural_recommendations import (
    DeliveryMode,
    ProceduralRecommendationStore,
    RecommendationContext,
    RecommendationEngine,
    apply_procedural_recommendations,
)
from actioncard.factory import ActionCardFactory
from actioncard.store import ActionCardStore


def test_feature_flag_defaults_off(monkeypatch):
    previous = feature_flags.persisted_flag_state_snapshot()
    try:
        feature_flags.clear_persisted_flag_state("FEATURE_PROCEDURAL_RECOMMENDATIONS")
        monkeypatch.delenv("FEATURE_PROCEDURAL_RECOMMENDATIONS", raising=False)
        feature_flags.reload_flags()

        assert feature_flags.FEATURE_PROCEDURAL_RECOMMENDATIONS is False
        assert "FEATURE_PROCEDURAL_RECOMMENDATIONS" in feature_flags.get_all_flags()
    finally:
        feature_flags.replace_persisted_flag_state(previous)
        feature_flags.reload_flags()


def test_simple_turn_produces_no_recommendation():
    decision = RecommendationEngine().decide(
        RecommendationContext(
            user_message="What is a checksum?",
            response_text="A checksum is a compact value used to detect data changes.",
        )
    )

    assert decision.response_text == "A checksum is a compact value used to detect data changes."
    assert decision.surfaced is None
    assert decision.recorded == []


def test_software_release_context_surfaces_high_confidence_recommendation():
    decision = RecommendationEngine().decide(
        RecommendationContext(
            user_message="We need to publish the GitHub repo and ship the public release.",
            response_text="Here is the release checklist.",
            history=[
                {"role": "user", "content": "Can you review the repo sync process?"},
                {"role": "assistant", "content": "Reviewed."},
            ],
        )
    )

    assert decision.surfaced is not None
    assert decision.surfaced.category == "software_development"
    assert decision.surfaced.delivery_mode() == DeliveryMode.ACTION_OFFER
    assert "One thing worth calling out:" in decision.response_text


def test_repeated_document_work_becomes_tool_mode_nudge():
    history = [
        {"role": "user", "content": "Draft a proposal for the client."},
        {"role": "assistant", "content": "Drafted."},
        {"role": "user", "content": "Rewrite the proposal with a stronger executive summary."},
        {"role": "assistant", "content": "Rewritten."},
        {"role": "user", "content": "Polish this proposal again for the board."},
    ]

    decision = RecommendationEngine().decide(
        RecommendationContext(
            user_message="Polish this proposal again for the board.",
            response_text="Here is the polished proposal.",
            history=history,
        )
    )

    assert decision.surfaced is not None
    assert decision.surfaced.category == "tool_mode_mismatch"
    assert "document workflow" in decision.response_text


def test_report_does_not_match_repo_signal():
    decision = RecommendationEngine().decide(
        RecommendationContext(
            user_message="Polish the QA update into a reusable report format.",
            response_text="## Draft\nHere is a concise report structure.",
            history=[
                {"role": "user", "content": "Draft a short email update about QA work."},
                {"role": "assistant", "content": "## Draft\nDone."},
                {"role": "user", "content": "Rewrite that QA update more directly."},
                {"role": "assistant", "content": "## Draft\nDone."},
            ],
        )
    )

    assert decision.surfaced is None
    assert any(candidate.category == "tool_mode_mismatch" for candidate in decision.recorded)
    assert all(candidate.category != "software_development" for candidate in decision.recorded)


def test_recommendation_receipts_are_best_effort_and_structured():
    created = []
    receipt_service = SimpleNamespace(create=lambda receipt: created.append(receipt))

    decision = apply_procedural_recommendations(
        RecommendationContext(
            user_message="We need to publish the GitHub repo and ship the public release.",
            response_text="Release notes are ready.",
            channel="warroom",
            quest_id="quest-1",
            session_id="session-1",
            operator_id="operator-1",
        ),
        receipt_service=receipt_service,
    )

    assert decision.surfaced is not None
    assert len(created) == 1
    receipt = created[0]
    assert receipt.action_type == "procedural_recommendation"
    assert receipt.quest_id == "quest-1"
    assert receipt.operator_id == "operator-1"
    assert receipt.outputs["score"] >= 10
    assert receipt.outputs["evidence"]


def test_store_persists_actions_snooze_and_sop_draft(tmp_path):
    store = ProceduralRecommendationStore(data_dir=str(tmp_path))
    record = store.upsert_candidate(
        category="workflow_maturity",
        title="Formalize repeatable workflow",
        observation="this is repeatable.",
        risk_or_opportunity="The opportunity is lower drift.",
        recommendation="Create a short SOP.",
        suggested_action="Draft SOP/checklist.",
        score=12,
        score_breakdown={"total": 12},
        evidence=["Repeated request."],
        delivery_mode="inline_nudge",
        operator_id="op-1",
    )

    snoozed = store.record_action(
        record.recommendation_id,
        "snooze",
        operator_id="op-1",
        snooze_seconds=3600,
    )
    assert snoozed.status == "snoozed"
    assert snoozed.snoozed_until is not None
    assert store.should_suppress(
        category="workflow_maturity",
        title="Formalize repeatable workflow",
        recommendation="Create a short SOP.",
        operator_id="op-1",
    )

    draft = store.convert_to_sop_draft(record.recommendation_id, operator_id="op-1")
    assert draft.status == "converted_to_sop"
    assert draft.sop_draft_path.endswith(".md")
    assert "SOP Draft" in open(draft.sop_draft_path, encoding="utf-8").read()


def test_expired_snooze_reactivates_pending_recommendation(tmp_path):
    store = ProceduralRecommendationStore(data_dir=str(tmp_path))
    record = store.upsert_candidate(
        category="workflow_maturity",
        title="Formalize repeatable workflow",
        observation="this is repeatable.",
        risk_or_opportunity="The opportunity is lower drift.",
        recommendation="Create a short SOP.",
        suggested_action="Draft SOP/checklist.",
        score=12,
        score_breakdown={"total": 12},
        evidence=["Repeated request."],
        delivery_mode="inline_nudge",
        operator_id="op-1",
    )
    store.set_actioncard_id(record.recommendation_id, "resolved-card-1")

    store.record_action(record.recommendation_id, "snooze", operator_id="op-1", snooze_seconds=-1)

    assert not store.should_suppress(
        category="workflow_maturity",
        title="Formalize repeatable workflow",
        recommendation="Create a short SOP.",
        operator_id="op-1",
    )
    reactivated = store.upsert_candidate(
        category="workflow_maturity",
        title="Formalize repeatable workflow",
        observation="this is repeatable again.",
        risk_or_opportunity="The opportunity is lower drift.",
        recommendation="Create a short SOP.",
        suggested_action="Draft SOP/checklist.",
        score=13,
        score_breakdown={"total": 13},
        evidence=["Repeated request after snooze."],
        delivery_mode="inline_nudge",
        operator_id="op-1",
    )

    assert reactivated.status == "pending"
    assert reactivated.actioncard_id == ""
    assert reactivated.snoozed_until is None
    assert len(store.list(status="pending", operator_id="op-1")) == 1


def test_apply_persists_and_presents_actioncard(tmp_path):
    rec_store = ProceduralRecommendationStore(data_dir=str(tmp_path / "recommendations"))
    card_store = ActionCardStore(data_dir=str(tmp_path / "actioncards"))
    factory = ActionCardFactory(card_store=card_store)

    decision = apply_procedural_recommendations(
        RecommendationContext(
            user_message="We need to publish the GitHub repo and ship the public release.",
            response_text="Release notes are ready.",
            channel="warroom",
            quest_id="quest-1",
            session_id="session-1",
            operator_id="operator-1",
        ),
        recommendation_store=rec_store,
        actioncard_factory=factory,
    )

    assert decision.surfaced is not None
    records = rec_store.list(status="pending")
    assert len(records) == 1
    assert records[0].actioncard_id
    card = card_store.get(records[0].actioncard_id)
    assert card is not None
    assert card.source_system == "procedural_recommendations"
    assert {button.id for button in card.buttons} == {"accept", "make_sop", "snooze", "dismiss"}


def test_sensitivity_adjusted_delivery_controls_persistence_and_actioncard(tmp_path, monkeypatch):
    monkeypatch.setenv("LANCELOT_PROCEDURAL_RECOMMENDATION_SENSITIVITY", "high")
    rec_store = ProceduralRecommendationStore(data_dir=str(tmp_path / "recommendations"))
    card_store = ActionCardStore(data_dir=str(tmp_path / "actioncards"))
    factory = ActionCardFactory(card_store=card_store)

    decision = apply_procedural_recommendations(
        RecommendationContext(
            user_message="Create an SOP for recurring escalation triage when the same issue appears twice.",
            response_text="Here is the escalation triage outline.",
            operator_id="operator-1",
        ),
        recommendation_store=rec_store,
        actioncard_factory=factory,
    )

    assert decision.surfaced is not None
    record = rec_store.list(status="pending", operator_id="operator-1")[0]
    assert record.delivery_mode == "action_offer"
    assert record.actioncard_id
