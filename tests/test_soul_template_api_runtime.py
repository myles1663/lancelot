from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import api_auth, auth_api
from src.core.operator_identity import OperatorIdentity
from src.core.soul import template_api
from src.core.soul.store import SoulStoreError


def _client() -> TestClient:
    auth_api._sessions.clear()
    auth_api._sessions["soul-admin"] = {
        "expires_at": 9999999999,
        "username": "Arthur",
        "operator_identity": OperatorIdentity(
            operator_id="op-arthur",
            display_name="Arthur",
            session_id="session-1",
            session_started_at="2026-04-18T00:00:00Z",
            auth_method="local",
            ip_address="127.0.0.1",
        ),
        "capabilities": ["warroom.login", "soul.admin"],
        "groups": [],
    }
    api_auth.init_api_auth(lambda request: True)
    app = FastAPI()
    app.include_router(template_api.router)
    client = TestClient(app)
    client.cookies.set(auth_api.get_warroom_session_cookie_name(), "soul-admin")
    return client


def test_template_dirs_and_list_detail_reload_success(monkeypatch, tmp_path):
    template_api._set_templates_dir(str(tmp_path / "templates"))
    template_api._set_soul_dir(str(tmp_path / "souls"))
    monkeypatch.setattr(
        template_api,
        "list_template_metadata",
        lambda templates_dir, industry=None: [{"name": "base", "industry": industry, "dir": templates_dir}],
    )
    monkeypatch.setattr(
        template_api,
        "get_template",
        lambda name, templates_dir: SimpleNamespace(to_dict=lambda: {"name": name, "dir": templates_dir}),
    )
    invalidated = []
    monkeypatch.setattr(template_api, "invalidate_cache", lambda: invalidated.append(True))
    client = _client()

    listed = client.get("/soul/templates?industry=healthcare")
    detail = client.get("/soul/templates/base")
    reload_response = client.post("/soul/templates/reload")

    assert listed.status_code == 200
    assert listed.json()["templates"][0]["industry"] == "healthcare"
    assert detail.json()["name"] == "base"
    assert reload_response.json() == {"status": "cache_invalidated"}
    assert invalidated == [True]


def test_list_detail_and_apply_error_paths(monkeypatch):
    monkeypatch.setattr(template_api, "list_template_metadata", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("list failed")))
    monkeypatch.setattr(template_api, "get_template", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(template_api, "_emit_template_receipt", lambda *_args, **_kwargs: None)
    client = _client()

    assert client.get("/soul/templates").status_code == 500
    assert client.get("/soul/templates/missing").status_code == 404

    monkeypatch.setattr(template_api, "apply_template", lambda **_kwargs: (_ for _ in ()).throw(SoulStoreError("bad soul")))
    assert client.post("/soul/templates/base/apply", json={}).status_code == 422

    monkeypatch.setattr(template_api, "apply_template", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("apply failed")))
    assert client.post("/soul/templates/base/apply", json={}).status_code == 500


def test_apply_template_accepts_empty_body_customizations_and_emits_receipt(monkeypatch):
    calls = []
    receipts = []

    def fake_apply_template(**kwargs):
        calls.append(kwargs)
        return {
            "template_name": kwargs["template_name"],
            "template_version": "1.0.0",
            "proposal_id": "proposal-1",
            "proposed_version": "soul-v2",
            "fields_customized": list((kwargs["customizations"] or {}).keys()),
            "diff_summary": ["changed name"],
        }

    monkeypatch.setattr(template_api, "apply_template", fake_apply_template)

    class Receipt:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setitem(
        __import__("sys").modules,
        "src.shared.receipts",
        SimpleNamespace(
            ActionType=SimpleNamespace(SOUL_TEMPLATE_APPLIED=SimpleNamespace(value="soul_template_applied")),
            ReceiptStatus=SimpleNamespace(SUCCESS=SimpleNamespace(value="success")),
            Receipt=Receipt,
            get_receipt_service=lambda: SimpleNamespace(create=lambda receipt: receipts.append(receipt)),
        ),
    )
    client = _client()

    empty = client.post("/soul/templates/base/apply")
    customized = client.post("/soul/templates/base/apply", json={"customizations": {"name": "Custom"}})

    assert empty.status_code == 200
    assert customized.status_code == 200
    assert calls[0]["customizations"] is None
    assert calls[1]["customizations"] == {"name": "Custom"}
    assert calls[0]["operator_id"] == "op-arthur"
    assert receipts[-1].kwargs["action_name"] == "template_applied:base"


def test_apply_template_rejects_bad_json_and_extra_fields():
    client = _client()

    bad_json = client.post("/soul/templates/base/apply", content=b"{bad", headers={"content-type": "application/json"})
    extra = client.post("/soul/templates/base/apply", json={"customizations": {}, "extra": True})

    assert bad_json.status_code == 422
    assert extra.status_code == 422
