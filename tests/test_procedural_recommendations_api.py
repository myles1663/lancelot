from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import api_auth, auth_api, procedural_recommendations_api
from src.core.actioncard.factory import ActionCardFactory
from src.core.actioncard.store import ActionCardStore
from src.core.operator_identity import OperatorIdentity
from src.core.procedural_recommendations import ProceduralRecommendationStore


def _client(
    store: ProceduralRecommendationStore,
    *,
    operator_id: str = "op-arthur",
    display_name: str = "Arthur",
) -> TestClient:
    auth_api._sessions.clear()
    auth_api._sessions["procedural-admin"] = {
        "expires_at": 9999999999,
        "username": display_name,
        "operator_identity": OperatorIdentity(
            operator_id=operator_id,
            display_name=display_name,
            session_id="session-1",
            session_started_at="2026-04-19T00:00:00Z",
            auth_method="local",
            ip_address="127.0.0.1",
        ),
        "capabilities": ["warroom.login", "platform.admin"],
        "groups": [],
    }
    api_auth.init_api_auth(lambda request: True)
    procedural_recommendations_api.init_procedural_recommendations_api(store)
    app = FastAPI()
    app.include_router(procedural_recommendations_api.router)
    client = TestClient(app)
    client.cookies.set(auth_api.get_warroom_session_cookie_name(), "procedural-admin")
    return client


def _seed(
    store: ProceduralRecommendationStore,
    *,
    operator_id: str = "op-arthur",
    title: str = "Add production software controls",
    recommendation: str = "Add CI, protected main, tags, and rollback.",
):
    return store.upsert_candidate(
        category="software_development",
        title=title,
        observation="this behaves like production software.",
        risk_or_opportunity="Shipping without release controls is risky.",
        recommendation=recommendation,
        suggested_action="Create release checklist.",
        score=16,
        score_breakdown={"total": 16},
        evidence=["Release signal."],
        delivery_mode="action_offer",
        operator_id=operator_id,
    )


def test_recommendations_api_lists_and_accepts(tmp_path):
    store = ProceduralRecommendationStore(data_dir=str(tmp_path))
    record = _seed(store)
    client = _client(store)

    listed = client.get("/api/procedural-recommendations/")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1

    accepted = client.post(f"/api/procedural-recommendations/{record.recommendation_id}/accept", json={})
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"

    stats = client.get("/api/procedural-recommendations/stats")
    assert stats.status_code == 200
    assert stats.json()["stats"]["by_status"]["accepted"] == 1


def test_recommendations_api_snoozes_and_converts_to_sop(tmp_path):
    store = ProceduralRecommendationStore(data_dir=str(tmp_path))
    record = _seed(store)
    client = _client(store)

    snoozed = client.post(
        f"/api/procedural-recommendations/{record.recommendation_id}/snooze",
        json={"snooze_hours": 2},
    )
    assert snoozed.status_code == 200
    assert snoozed.json()["status"] == "snoozed"
    assert snoozed.json()["snoozed_until"] is not None

    converted = client.post(f"/api/procedural-recommendations/{record.recommendation_id}/convert-to-sop")
    assert converted.status_code == 200
    assert converted.json()["status"] == "converted_to_sop"
    assert converted.json()["sop_draft_path"].endswith(".md")


def test_actioncard_handler_updates_recommendation(tmp_path):
    store = ProceduralRecommendationStore(data_dir=str(tmp_path))
    record = _seed(store)
    procedural_recommendations_api.init_procedural_recommendations_api(store)

    result = procedural_recommendations_api.resolve_recommendation_action(
        record.recommendation_id,
        "dismiss",
        operator_id="op-arthur",
        session_id="session-1",
        actor="Arthur",
    )

    assert result["status"] == "dismissed"
    assert store.get(record.recommendation_id).status == "dismissed"


def test_recommendations_api_scopes_records_to_operator(tmp_path):
    store = ProceduralRecommendationStore(data_dir=str(tmp_path))
    own = _seed(store, operator_id="op-arthur")
    other = _seed(
        store,
        operator_id="op-morgan",
        title="Add branch release controls",
        recommendation="Add branch protection and rollback checks.",
    )
    client = _client(store, operator_id="op-arthur")

    listed = client.get("/api/procedural-recommendations/")
    assert listed.status_code == 200
    ids = {
        item["recommendation_id"]
        for item in listed.json()["recommendations"]
    }
    assert own.recommendation_id in ids
    assert other.recommendation_id not in ids

    stats = client.get("/api/procedural-recommendations/stats")
    assert stats.status_code == 200
    assert stats.json()["stats"]["total"] == 1

    accepted_other = client.post(f"/api/procedural-recommendations/{other.recommendation_id}/accept", json={})
    assert accepted_other.status_code == 404
    assert store.get(other.recommendation_id).status == "pending"


def test_actioncard_handler_rejects_other_operator_recommendation(tmp_path):
    store = ProceduralRecommendationStore(data_dir=str(tmp_path))
    record = _seed(store, operator_id="op-morgan")
    procedural_recommendations_api.init_procedural_recommendations_api(store)

    result = procedural_recommendations_api.resolve_recommendation_action(
        record.recommendation_id,
        "dismiss",
        operator_id="op-arthur",
        session_id="session-1",
        actor="Arthur",
    )

    assert result["status"] == "error"
    assert store.get(record.recommendation_id).status == "pending"


def test_panel_action_resolves_linked_actioncard(tmp_path):
    store = ProceduralRecommendationStore(data_dir=str(tmp_path / "recommendations"))
    card_store = ActionCardStore(data_dir=str(tmp_path / "actioncards"))
    record = _seed(store)
    card = ActionCardFactory(card_store=card_store).from_procedural_recommendation(record)
    store.set_actioncard_id(record.recommendation_id, card.card_id)
    procedural_recommendations_api.bind_procedural_recommendations_actioncard_store(card_store)
    client = _client(store)

    snoozed = client.post(
        f"/api/procedural-recommendations/{record.recommendation_id}/snooze",
        json={"snooze_hours": 24},
    )

    assert snoozed.status_code == 200
    updated_card = card_store.get(card.card_id)
    assert updated_card.resolved is True
    assert updated_card.resolved_action == "snooze"
    assert updated_card.resolved_channel == "procedural_recommendations_panel"
    card_store.close()
