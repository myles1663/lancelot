import json
import types
import urllib.error

import pytest

from src.core.response.presenter import ResponsePresenter, parse_structured_response
from src.core.soul.layers import SoulOverlay, load_active_soul_with_overlays, load_overlays, merge_soul
from src.core.soul.store import RiskRule, Soul
from src.integrations.ucp_connector import UCPConnector


class FakeResponse:
    def __init__(self, payload, headers=None):
        self.payload = payload
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class AuditRecorder:
    def __init__(self):
        self.events = []

    def log_event(self, event, detail):
        self.events.append((event, detail))


def make_soul() -> Soul:
    return Soul(
        version="v1",
        mission="Protect the operator",
        allegiance="Operator",
        autonomy_posture={
            "level": "guarded",
            "description": "base posture",
            "allowed_autonomous": ["read"],
            "requires_approval": ["write"],
        },
        risk_rules=[{"name": "base", "description": "base rule", "enforced": True}],
        tone_invariants=["direct"],
        memory_ethics=["minimize sensitive retention"],
        scheduling_boundaries={
            "max_concurrent_jobs": 2,
            "max_job_duration_seconds": 60,
            "description": "base schedule",
        },
        spawn_budget={"max_concurrent_spawns": 3},
        mcp_permissions=[{"server_id": "github", "allowed_tools": ["search"], "risk_tier": "T1"}],
        fork_permissions={"allow_fork": False},
    )


def test_github_search_formats_actions_and_execute_errors(monkeypatch):
    from src.core.skills.builtins import github_search

    calls = []

    def fake_request(endpoint, params=None):
        calls.append((endpoint, params))
        if endpoint == "/search/repositories":
            return {
                "total_count": 1,
                "items": [{
                    "full_name": "owner/repo",
                    "description": "x" * 250,
                    "stargazers_count": 42,
                    "forks_count": 3,
                    "language": "Python",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "html_url": "https://github.com/owner/repo",
                    "open_issues_count": 7,
                }],
            }
        if endpoint.endswith("/commits"):
            return [{"sha": "abcdef123456", "commit": {"message": "fix bug\n\nbody", "author": {"name": "Myles", "date": "2026-01-02"}}, "html_url": "https://github.com/owner/repo/commit/abcdef"}]
        if endpoint.endswith("/issues"):
            return [{"number": 4, "title": "issue title", "state": "open", "pull_request": {}, "labels": [{"name": "bug"}], "user": {"login": "dev"}, "created_at": "c", "updated_at": "u", "html_url": "https://github.com/owner/repo/pull/4"}]
        if endpoint.endswith("/releases"):
            return [{"tag_name": "v1", "name": "Release", "published_at": "p", "prerelease": False, "body": "b" * 600, "html_url": "https://github.com/owner/repo/releases/tag/v1"}]
        raise RuntimeError("unexpected")

    monkeypatch.setattr(github_search, "_github_request", fake_request)

    repos = github_search.execute(None, {"action": "search_repos", "query": "agent", "limit": 1})
    commits = github_search.execute(None, {"action": "get_commits", "repo": "owner/repo", "limit": 1})
    issues = github_search.execute(None, {"action": "get_issues", "repo": "owner/repo", "state": "invalid", "limit": 1})
    releases = github_search.execute(None, {"action": "get_releases", "repo": "owner/repo", "limit": 1})
    unknown = github_search.execute(None, {"action": "bad"})

    assert repos["results"][0]["description"] == "x" * 200
    assert commits["commits"][0]["sha"] == "abcdef12"
    assert issues["issues"][0]["type"] == "pull_request"
    assert calls[2][1]["state"] == "all"
    assert releases["releases"][0]["body"] == "b" * 500
    assert "Unknown action" in unknown["error"]

    assert github_search._search_repos("", 5)["error"].startswith("query is required")
    assert github_search._get_commits("bad", 5)["error"].startswith("repo must")
    assert github_search._get_issues("", "open", 5)["error"].startswith("repo must")
    assert github_search._get_releases("bad", 5)["error"].startswith("repo must")


def test_github_request_handles_success_auth_and_failures(monkeypatch):
    from src.core.skills.builtins import github_search

    seen = {}

    def fake_urlopen(req, timeout=None, context=None):
        seen["url"] = req.full_url
        seen["auth"] = req.headers.get("Authorization")
        return FakeResponse({"ok": True}, headers={"X-RateLimit-Remaining": "12"})

    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(github_search, "urlopen", fake_urlopen)

    assert github_search._github_request("/repos/o/r", {"per_page": "1"}) == {"ok": True}
    assert seen["url"].endswith("/repos/o/r?per_page=1")
    assert seen["auth"] == "Bearer token"

    class RateLimitError(urllib.error.HTTPError):
        def read(self):
            return b'{"message":"API rate limit exceeded"}'

    def raise_403(*_args, **_kwargs):
        raise RateLimitError("https://api.github.com", 403, "forbidden", {"X-RateLimit-Reset": "later"}, None)

    monkeypatch.delenv("GITHUB_TOKEN")
    monkeypatch.setattr(github_search, "urlopen", raise_403)
    with pytest.raises(RuntimeError, match="rate limit exceeded"):
        github_search._github_request("/rate")

    monkeypatch.setattr(
        github_search,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(urllib.error.URLError("offline")),
    )
    with pytest.raises(RuntimeError, match="connection error"):
        github_search._github_request("/offline")

    def raise_404(*_args, **_kwargs):
        raise urllib.error.HTTPError("https://api.github.com", 404, "missing", {}, None)

    monkeypatch.setattr(github_search, "urlopen", raise_404)
    with pytest.raises(RuntimeError, match="resource not found"):
        github_search._github_request("/missing")

    def raise_500(*_args, **_kwargs):
        raise urllib.error.HTTPError("https://api.github.com", 500, "error", {}, None)

    monkeypatch.setattr(github_search, "urlopen", raise_500)
    with pytest.raises(RuntimeError, match="API error 500"):
        github_search._github_request("/broken")

    monkeypatch.setattr(
        github_search,
        "_search_repos",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("api failed")),
    )
    assert github_search.execute(None, {"action": "search_repos", "query": "x"})["error"] == "api failed"

    monkeypatch.setattr(
        github_search,
        "_search_repos",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad input")),
    )
    assert github_search.execute(None, {"action": "search_repos", "query": "x"})["error"] == "Unexpected error: bad input"


def test_response_presenter_verifies_actions_claims_and_json_parsing(monkeypatch):
    presenter = ResponsePresenter(claim_verification=False)
    receipts = [
        {"skill": "repo_reader", "result": "SUCCESS", "outputs": {"path": "README.md"}},
        {"skill": "repo_writer", "result": "FAILED: denied"},
        {"skill": "email", "result": "ESCALATED"},
    ]
    structured = {
        "response_to_user": "I inspected README and did not write.",
        "actions_taken": [
            {"tool": "repo_reader", "summary": "read README", "status": "success"},
            {"tool": "repo_writer", "summary": "wrote file", "status": "success"},
            {"tool": "email", "summary": "send update", "status": "success"},
            {"tool": "missing_tool", "summary": "hallucinated", "status": "success"},
        ],
        "next_action": "needs_approval",
    }

    rendered = presenter.present(structured, receipts)
    verified = presenter._verify_actions(structured["actions_taken"], receipts)

    assert rendered.endswith("Approve or Deny?")
    assert [action["tool"] for action in verified] == ["repo_reader", "repo_writer", "email"]
    assert verified[1]["status"] == "failed"
    assert verified[2]["status"] == "pending_approval"
    assert presenter._actions_described_in_text(
        "read README using repo reader and wrote file through repo writer",
        verified[:2],
    ) is True
    assert presenter._actions_described_in_text("", verified) is False
    assert presenter._format_chat("", [], "done") == "Done."

    assert parse_structured_response('{"response_to_user":"ok","next_action":"done"}')["next_action"] == "done"
    assert parse_structured_response('```json\n{"response_to_user":"ok","next_action":"done"}\n```') is not None
    assert parse_structured_response("") is None
    assert parse_structured_response("[1, 2]") is None
    assert parse_structured_response('{"next_action":"done"}') is None


def test_response_presenter_claim_verifier_success_and_fallback(monkeypatch):
    claim_module = types.ModuleType("response.claim_verifier")

    class FakeVerifier:
        def verify(self, text, receipts):
            return types.SimpleNamespace(is_clean=False, flagged_claims=["claim"], cleaned_text=f"clean:{text}")

    claim_module.ClaimVerifier = FakeVerifier
    monkeypatch.setitem(__import__("sys").modules, "response.claim_verifier", claim_module)

    presenter = ResponsePresenter(claim_verification=True)
    assert presenter.present({"response_to_user": "raw", "next_action": "done"}, []) == "clean:raw"
    assert presenter.present_fallback("raw fallback", []) == "clean:raw fallback"

    class BrokenVerifier:
        def verify(self, text, receipts):
            raise RuntimeError("verifier offline")

    claim_module.ClaimVerifier = BrokenVerifier
    assert presenter.present({"response_to_user": "raw", "next_action": "done"}, []) == "raw"
    assert presenter.present_fallback("raw fallback", []) == "raw fallback"


def test_ucp_connector_discovery_search_and_transaction_lifecycle(monkeypatch, tmp_path):
    from src.integrations import ucp_connector

    audit = AuditRecorder()
    monkeypatch.setenv("LANCELOT_UCP_STATE_FILE", str(tmp_path / "ucp.json"))

    responses = [
        {"name": "Shop", "endpoints": {"search": "/search", "transact": "/buy"}},
        {"products": [{"id": "p1", "name": "Widget"}]},
        {"order_id": "o1", "status": "accepted"},
    ]

    requested_urls = []

    def fake_urlopen(req, timeout=None):
        requested_urls.append(req.full_url)
        return FakeResponse(responses.pop(0))

    monkeypatch.setattr(ucp_connector, "urlopen", fake_urlopen)
    connector = UCPConnector(audit_logger=audit)
    monkeypatch.setattr(connector._net_interceptor, "check_url", lambda url: True)

    manifest = connector.discover_merchant("https://shop.example")
    products = connector.search_products("https://shop.example", "widget")
    tx = connector.initiate_transaction("https://shop.example", "p1", {"quantity": 1})
    completed = connector.confirm_transaction(tx["transaction_id"])

    assert manifest["name"] == "Shop"
    assert products == [{"id": "p1", "name": "Widget"}]
    assert completed["status"] == "completed"
    assert completed["result"]["order_id"] == "o1"
    assert connector.get_transaction(tx["transaction_id"])["merchant_name"] == "Shop"
    assert connector.list_merchants() == [{"url": "https://shop.example", "name": "Shop"}]
    assert requested_urls == [
        "https://shop.example/.well-known/ucp.json",
        "https://shop.example/search?q=widget",
        "https://shop.example/buy",
    ]
    assert any(event[0] == "UCP_TRANSACTION_COMPLETED" for event in audit.events)


def test_ucp_connector_blocks_and_records_failed_paths(monkeypatch, tmp_path):
    from src.integrations import ucp_connector

    monkeypatch.setenv("LANCELOT_UCP_STATE_FILE", str(tmp_path / "ucp.json"))
    connector = UCPConnector(audit_logger=AuditRecorder())
    monkeypatch.setattr(connector._net_interceptor, "check_url", lambda url: False)

    with pytest.raises(ValueError, match="blocked"):
        connector.discover_merchant("https://blocked.example")

    connector._registered_merchants["https://shop.example"] = {"name": "Shop", "endpoints": {}}
    with pytest.raises(ValueError, match="does not support product search"):
        connector.search_products("https://shop.example", "widget")

    connector._registered_merchants["https://shop.example"] = {"name": "Shop", "endpoints": {"search": "/search"}}
    with pytest.raises(ValueError, match="Search URL blocked"):
        connector.search_products("https://shop.example", "widget")

    connector._registered_merchants["https://shop.example"] = {"name": "Shop", "endpoints": {"transact": "/buy"}}
    tx = connector.initiate_transaction("https://shop.example", "p1", {})
    connector._registered_merchants["https://shop.example"] = {"name": "Shop", "endpoints": {"transact": "/buy"}}

    def fail_urlopen(*_args, **_kwargs):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(connector._net_interceptor, "check_url", lambda url: True)
    monkeypatch.setattr(ucp_connector, "urlopen", fail_urlopen)

    failed = connector.confirm_transaction(tx["transaction_id"])
    assert failed["status"] == "failed"
    assert "offline" in failed["error"]

    with pytest.raises(ValueError, match="not found"):
        connector.confirm_transaction("missing")
    with pytest.raises(ValueError, match="not pending"):
        connector.confirm_transaction(tx["transaction_id"])


def test_ucp_connector_discovery_and_confirmation_guardrails(monkeypatch, tmp_path):
    from src.integrations import ucp_connector

    class BadJsonResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"{bad-json"

    monkeypatch.setenv("LANCELOT_UCP_STATE_FILE", str(tmp_path / "ucp.json"))
    connector = UCPConnector(audit_logger=AuditRecorder())
    monkeypatch.setattr(connector._net_interceptor, "check_url", lambda url: True)
    monkeypatch.setattr(ucp_connector, "urlopen", lambda *_args, **_kwargs: BadJsonResponse())

    with pytest.raises(ConnectionError, match="Failed to discover"):
        connector.discover_merchant("https://bad.example")

    connector._pending_transactions["tx1"] = {
        "transaction_id": "tx1",
        "merchant_url": "https://missing.example",
        "product_id": "p1",
        "params": {},
        "status": "pending_confirmation",
    }
    with pytest.raises(ValueError, match="Merchant manifest not found"):
        connector.confirm_transaction("tx1")

    connector._registered_merchants["https://missing.example"] = {"name": "Shop", "endpoints": {}}
    with pytest.raises(ValueError, match="does not support transactions"):
        connector.confirm_transaction("tx1")

    connector._registered_merchants["https://missing.example"] = {"name": "Shop", "endpoints": {"transact": "/buy"}}
    monkeypatch.setattr(connector._net_interceptor, "check_url", lambda url: False)
    with pytest.raises(ValueError, match="Transaction URL blocked"):
        connector.confirm_transaction("tx1")

    empty_state = tmp_path / "empty.json"
    empty_state.write_text("", encoding="utf-8")
    monkeypatch.setenv("LANCELOT_UCP_STATE_FILE", str(empty_state))
    assert UCPConnector(audit_logger=AuditRecorder())._pending_transactions == {}

    list_state = tmp_path / "list.json"
    list_state.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("LANCELOT_UCP_STATE_FILE", str(list_state))
    assert UCPConnector(audit_logger=AuditRecorder())._pending_transactions == {}


def test_soul_overlay_loading_merging_and_active_loader(monkeypatch, tmp_path):
    overlays_dir = tmp_path / "overlays"
    overlays_dir.mkdir()
    (overlays_dir / "active.yaml").write_text(
        """
overlay_name: finance
feature_flag: FEATURE_FINANCE
description: Finance governance
risk_rules:
  - name: wire-transfer
    description: Wire transfers require approval
    enforced: true
tone_invariants:
  - precise money language
memory_ethics:
  - never store card numbers
autonomy_posture:
  allowed_autonomous:
    - reconcile receipts
  requires_approval:
    - initiate transfer
scheduling_boundaries: No unattended market actions.
""",
        encoding="utf-8",
    )
    (overlays_dir / "inactive.yaml").write_text(
        "overlay_name: inactive\nfeature_flag: FEATURE_OFF\n",
        encoding="utf-8",
    )
    (overlays_dir / "bad.yaml").write_text("- not-a-mapping", encoding="utf-8")
    (overlays_dir / "notes.txt").write_text("ignored", encoding="utf-8")

    loaded = load_overlays(str(tmp_path), {"FEATURE_FINANCE"})
    assert [overlay.overlay_name for overlay in loaded] == ["finance"]

    base = make_soul()
    duplicate = SoulOverlay(
        overlay_name="duplicate",
        feature_flag="FEATURE_FINANCE",
        risk_rules=[RiskRule(name="base", description="duplicate")],
        tone_invariants=["direct"],
        memory_ethics=["minimize sensitive retention"],
        autonomy_posture={
            "allowed_autonomous": ["read"],
            "requires_approval": ["write"],
        },
    )
    merged = merge_soul(base, [loaded[0], duplicate])

    assert merged.version == "v1"
    assert merged.mission == base.mission
    assert [rule.name for rule in merged.risk_rules] == ["base", "wire-transfer"]
    assert "precise money language" in merged.tone_invariants
    assert "reconcile receipts" in merged.autonomy_posture.allowed_autonomous
    assert "No unattended market actions" in merged.scheduling_boundaries.description

    monkeypatch.setattr("src.core.soul.layers.load_active_soul", lambda soul_dir: base)
    active = load_active_soul_with_overlays(str(tmp_path), {"FEATURE_FINANCE"})
    assert any(rule.name == "wire-transfer" for rule in active.risk_rules)

    monkeypatch.setattr("src.core.soul.layers.load_overlays", lambda *_args, **_kwargs: [])
    assert load_active_soul_with_overlays(str(tmp_path), set()) is base

    assert load_overlays(str(tmp_path / "missing"), {"FEATURE_FINANCE"}) == []
    assert merge_soul(base, []) is base
