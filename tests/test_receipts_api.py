from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import api_auth, auth_api
from src.core.operator_identity import OperatorIdentity
from src.core import receipts_api


def _insert_session(token: str) -> None:
    auth_api._sessions[token] = {
        "expires_at": 9999999999,
        "username": "Arthur",
        "operator_identity": OperatorIdentity(
            operator_id="op-arthur",
            display_name="Arthur",
            session_id="session-1",
            session_started_at="2026-04-19T00:00:00Z",
            auth_method="local",
            ip_address="127.0.0.1",
        ),
        "capabilities": ["warroom.login"],
        "groups": [],
    }


def _client() -> TestClient:
    auth_api._sessions.clear()
    _insert_session("receipt-session")
    api_auth.init_api_auth(lambda request: True)
    app = FastAPI()
    app.include_router(receipts_api.router)
    client = TestClient(app)
    client.cookies.set(auth_api.get_warroom_session_cookie_name(), "receipt-session")
    return client


def _receipt(receipt_id: str, **overrides):
    return SimpleNamespace(
        id=receipt_id,
        timestamp=overrides.get("timestamp", "2026-04-19T00:00:00Z"),
        action_type=overrides.get("action_type", "verification"),
        action_name=overrides.get("action_name", "check"),
        inputs=overrides.get("inputs", {"target": "demo"}),
        outputs=overrides.get("outputs", {"status": "ok"}),
        status=overrides.get("status", "success"),
        duration_ms=overrides.get("duration_ms", 42),
        token_count=overrides.get("token_count", 7),
        tier=overrides.get("tier", "T1"),
        parent_id=overrides.get("parent_id"),
        quest_id=overrides.get("quest_id"),
        error_message=overrides.get("error_message"),
        metadata=overrides.get("metadata", {"source": "test"}),
    )


class _FakeReceiptService:
    def __init__(self):
        self.list_calls = []
        self.search_calls = []
        self.stats_calls = []
        self.receipts = {}
        self.children = {}
        self.quest_receipts = {}
        self.list_result = []
        self.search_result = []
        self.stats_result = {"total": 1}

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        return list(self.list_result)

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        return list(self.search_result)

    def get_stats(self, **kwargs):
        self.stats_calls.append(kwargs)
        return dict(self.stats_result)

    def get(self, receipt_id):
        return self.receipts.get(receipt_id)

    def get_children(self, receipt_id):
        return list(self.children.get(receipt_id, []))

    def get_quest_receipts(self, quest_id):
        return list(self.quest_receipts.get(quest_id, []))


def test_init_receipts_api_success_and_failure(monkeypatch, tmp_path):
    fake_service = object()
    monkeypatch.setattr("receipts.get_receipt_service", lambda data_dir: fake_service)

    receipts_api.init_receipts_api(str(tmp_path))

    assert receipts_api._receipt_service is fake_service

    monkeypatch.setattr(
        "receipts.get_receipt_service",
        lambda data_dir: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    receipts_api._receipt_service = None

    receipts_api.init_receipts_api(str(tmp_path))

    assert receipts_api._receipt_service is None


def test_list_receipts_supports_degraded_list_and_search_paths():
    client = _client()
    receipts_api._receipt_service = None

    degraded = client.get("/api/receipts")
    assert degraded.status_code == 200
    assert degraded.json() == {
        "receipts": [],
        "total": 0,
        "message": "Receipt service not initialised",
    }

    service = _FakeReceiptService()
    service.list_result = [_receipt("r-1"), _receipt("r-2", action_name="summarize")]
    service.search_result = [_receipt("r-3", action_name="search_hit")]
    receipts_api._receipt_service = service

    listed = client.get(
        "/api/receipts",
        params={
            "limit": 25,
            "offset": 10,
            "action_type": "verification",
            "status": "success",
            "quest_id": "quest-1",
            "since": "2026-04-18T00:00:00Z",
            "until": "2026-04-19T00:00:00Z",
        },
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 2
    assert service.list_calls == [
        {
            "limit": 25,
            "offset": 10,
            "action_type": "verification",
            "status": "success",
            "quest_id": "quest-1",
            "since": "2026-04-18T00:00:00Z",
            "until": "2026-04-19T00:00:00Z",
        }
    ]

    searched = client.get("/api/receipts", params={"q": "search_hit", "action_type": "verification", "limit": 5})
    assert searched.status_code == 200
    assert searched.json()["receipts"][0]["id"] == "r-3"
    assert service.search_calls == [
        {
            "query": "search_hit",
            "limit": 5,
            "action_types": ["verification"],
        }
    ]


def test_list_and_stats_return_safe_errors_on_service_exceptions():
    class _BrokenService(_FakeReceiptService):
        def list(self, **kwargs):
            raise RuntimeError("list exploded")

        def get_stats(self, **kwargs):
            raise RuntimeError("stats exploded")

    receipts_api._receipt_service = _BrokenService()
    client = _client()

    listed = client.get("/api/receipts")
    assert listed.status_code == 500
    assert listed.json() == {"error": "Failed to list receipts", "status": 500}

    stats = client.get("/api/receipts/stats")
    assert stats.status_code == 500
    assert stats.json() == {"error": "Failed to get receipt stats", "status": 500}


def test_receipt_stats_and_detail_routes_return_contextual_data():
    service = _FakeReceiptService()
    parent = _receipt("parent-1", action_name="root", action_type="governance", status="success")
    target = _receipt("child-1", parent_id="parent-1", quest_id="quest-1")
    service.receipts = {
        "parent-1": parent,
        "child-1": target,
    }
    service.children = {"child-1": [_receipt("grandchild-1", parent_id="child-1")]}
    service.quest_receipts = {"quest-1": [target, _receipt("peer-1", quest_id="quest-1")]}
    service.stats_result = {"total": 2, "action_types": {"verification": 2}}
    receipts_api._receipt_service = service
    client = _client()

    stats = client.get("/api/receipts/stats", params={"since": "2026-04-18T00:00:00Z", "quest_id": "quest-1"})
    assert stats.status_code == 200
    assert stats.json() == {"stats": {"total": 2, "action_types": {"verification": 2}}}
    assert service.stats_calls == [{"since": "2026-04-18T00:00:00Z", "quest_id": "quest-1"}]

    detail = client.get("/api/receipts/child-1")
    assert detail.status_code == 200
    assert detail.json()["receipt"]["parent_id"] == "parent-1"

    context = client.get("/api/receipts/child-1/context")
    assert context.status_code == 200
    assert context.json() == {
        "children": [receipts_api._receipt_to_dict(service.children["child-1"][0])],
        "quest_receipts_count": 2,
        "parent": {
            "id": "parent-1",
            "action_name": "root",
            "action_type": "governance",
            "status": "success",
        },
    }


def test_receipt_detail_and_context_fail_closed_for_missing_service_or_receipt():
    receipts_api._receipt_service = None
    client = _client()

    missing_service_detail = client.get("/api/receipts/r-1")
    assert missing_service_detail.status_code == 400
    assert missing_service_detail.json() == {"error": "Receipt service not initialised", "status": 400}

    missing_service_context = client.get("/api/receipts/r-1/context")
    assert missing_service_context.status_code == 400
    assert missing_service_context.json() == {"error": "Receipt service not initialised", "status": 400}

    receipts_api._receipt_service = _FakeReceiptService()
    missing_detail = client.get("/api/receipts/r-404")
    assert missing_detail.status_code == 404
    assert missing_detail.json() == {"error": "Receipt r-404 not found", "status": 404}

    missing_context = client.get("/api/receipts/r-404/context")
    assert missing_context.status_code == 404
    assert missing_context.json() == {"error": "Receipt r-404 not found", "status": 404}
