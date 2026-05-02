import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.federation import api as federation_api


class Config(SimpleNamespace):
    def model_copy(self, update=None):
        values = dict(self.__dict__)
        values.update(update or {})
        return Config(**values)


def _dashboard_config():
    return SimpleNamespace(
        enabled=True,
        poll_interval_s=10.0,
        stream_interval_s=2.0,
        max_recent_activity_items=25,
        card_sort_order="urgency",
        show_fleet_activity_feed=True,
        activity_feed_max_events=10,
    )


def _config(**overrides):
    values = {
        "self_address": "https://local.example:8443",
        "heartbeat_interval_s": 2.0,
        "tls_required": True,
        "staleness_warning_s": 10.0,
        "staleness_critical_s": 20.0,
        "staleness_lost_s": 30.0,
        "command_timeout_s": 4.0,
        "dashboard": _dashboard_config(),
    }
    values.update(overrides)
    return Config(**values)


def _identity(instance_id="local-instance"):
    return SimpleNamespace(
        instance_id=instance_id,
        fingerprint="fingerprint",
        public_key_hex=lambda: "public-key",
        to_public_dict=lambda: {"instance_id": instance_id, "fingerprint": "fingerprint"},
    )


class FakeRequest:
    def __init__(self, payload=None, *, body=None, headers=None, method="POST", path="/api/federation/test"):
        self._payload = payload
        self._body = body
        raw = b"" if body is None else body
        if isinstance(payload, Exception):
            raw = b"invalid-json"
        elif payload is not None:
            raw = json.dumps(payload).encode("utf-8")
        self.headers = headers if headers is not None else {"content-length": str(len(raw))}
        self.method = method
        self.url = SimpleNamespace(path=path)
        self.state = SimpleNamespace()

    async def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    async def body(self):
        if self._body is not None:
            return self._body
        if self._payload is None:
            return b""
        return json.dumps(self._payload).encode("utf-8")


def _body(response):
    return json.loads(response.body.decode("utf-8"))


def teardown_function():
    federation_api.shutdown_federation_api()


def _init(**kwargs):
    topology = kwargs.pop("topology_registry", SimpleNamespace(list_peers=lambda: [], deployment_mode=SimpleNamespace(value="standalone")))
    federation_api.init_federation_api(
        kwargs.pop("identity", _identity()),
        kwargs.pop("heartbeat_emitter", None),
        kwargs.pop("config", _config()),
        topology_registry=topology,
        divergence_detector=kwargs.pop("divergence_detector", None),
    )
    federation_api.init_federation_transport(**kwargs)


def test_parse_request_model_handles_empty_invalid_json_and_validation_errors():
    empty = asyncio.run(
        federation_api._parse_request_model(
            FakeRequest(payload=None, headers={"content-length": "0"}),
            federation_api.SoulHandshakeRequest,
            allow_empty=True,
        )
    )
    assert empty.remote_instance_id == ""

    with pytest.raises(HTTPException) as invalid_json:
        asyncio.run(
            federation_api._parse_request_model(
                FakeRequest(payload=ValueError("bad json")),
                federation_api.PauseSignalRequest,
            )
        )
    assert invalid_json.value.status_code == 422

    with pytest.raises(HTTPException) as invalid_model:
        asyncio.run(
            federation_api._parse_request_model(
                FakeRequest(payload={"issuer_instance_id": "peer", "extra": "nope"}),
                federation_api.PauseSignalRequest,
            )
        )
    assert invalid_model.value.status_code == 422


def test_endpoints_return_clean_not_initialized_and_no_transport_errors():
    federation_api.shutdown_federation_api()
    request = FakeRequest({"issuer_instance_id": "peer-1"})

    not_initialized_calls = [
        lambda: federation_api.latest_heartbeat(),
        lambda: federation_api.receive_command(request),
        lambda: federation_api.receive_killswitch(request),
        lambda: federation_api.receive_pause(request),
        lambda: federation_api.receive_resume(request),
        lambda: federation_api.initiate_handoff(request),
        lambda: federation_api.confirm_soul_update(request),
        lambda: federation_api.complete_handoff(request),
        lambda: federation_api.get_handoff("h1"),
        lambda: federation_api.get_identity(),
        lambda: federation_api.get_status(),
        lambda: federation_api.get_federation_settings(),
        lambda: federation_api.update_federation_settings(federation_api.UpdateFederationSettingsRequest(self_address="https://local")),
        lambda: federation_api.get_federation_dashboard(),
        lambda: federation_api.get_local_federation_dashboard(FakeRequest()),
        lambda: federation_api.get_topology(),
        lambda: federation_api.get_soul_hash(FakeRequest()),
        lambda: federation_api.soul_handshake(FakeRequest()),
        lambda: federation_api.get_soul(FakeRequest()),
        lambda: federation_api.receive_soul_update(request),
        lambda: federation_api.register_peer(request),
        lambda: federation_api.confirm_peer_registration(request),
        lambda: federation_api.remove_peer("peer-1"),
        lambda: federation_api.get_budget(),
        lambda: federation_api.receive_budget_report(request),
        lambda: federation_api.get_budget_threshold(),
        lambda: federation_api.manage_register_peer(FakeRequest()),
        lambda: federation_api.manage_initiate_handoff(FakeRequest()),
        lambda: federation_api.manage_complete_handoff(FakeRequest()),
        lambda: federation_api.manage_propagate_kill(FakeRequest()),
        lambda: federation_api.get_audit_entries(),
        lambda: federation_api.get_audit_summary(),
        lambda: federation_api.get_quest_timeline("quest-1"),
    ]
    for make_call in not_initialized_calls:
        assert asyncio.run(make_call()).status_code == 503

    _init()
    no_transport_calls = [
        lambda: federation_api.receive_command(request),
        lambda: federation_api.receive_killswitch(request),
        lambda: federation_api.receive_pause(request),
        lambda: federation_api.receive_resume(request),
        lambda: federation_api.initiate_handoff(request),
        lambda: federation_api.confirm_soul_update(request),
        lambda: federation_api.complete_handoff(request),
        lambda: federation_api.get_handoff("h1"),
        lambda: federation_api.soul_handshake(FakeRequest()),
        lambda: federation_api.get_soul(FakeRequest()),
        lambda: federation_api.receive_soul_update(request),
        lambda: federation_api.register_peer(request),
        lambda: federation_api.confirm_peer_registration(request),
        lambda: federation_api.remove_peer("peer-1"),
        lambda: federation_api.get_budget(),
        lambda: federation_api.receive_budget_report(request),
        lambda: federation_api.get_budget_threshold(),
        lambda: federation_api.manage_register_peer(FakeRequest()),
        lambda: federation_api.manage_initiate_handoff(FakeRequest()),
        lambda: federation_api.manage_complete_handoff(FakeRequest()),
        lambda: federation_api.manage_propagate_kill(FakeRequest()),
        lambda: federation_api.get_audit_entries(),
        lambda: federation_api.get_audit_summary(),
        lambda: federation_api.get_quest_timeline("quest-1"),
    ]
    for make_call in no_transport_calls:
        body = _body(asyncio.run(make_call()))
        assert "transport layer not initialized" in body["error"]


def test_peer_auth_dependency_records_signed_peer_and_rejects_bad_signatures():
    request = FakeRequest(body=b"{}", method="POST", path="/api/federation/command")
    auth = SimpleNamespace(
        verify_request=lambda **kwargs: SimpleNamespace(valid=True, instance_id="peer-1", reason="")
    )
    _init(auth=auth)

    asyncio.run(federation_api._require_valid_peer_request(request))
    assert request.state.federation_peer_instance_id == "peer-1"

    federation_api.init_federation_transport(
        auth=SimpleNamespace(
            verify_request=lambda **kwargs: SimpleNamespace(valid=False, instance_id="", reason="bad signature")
        )
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(federation_api._require_valid_peer_request(FakeRequest(body=b"{}")))
    assert exc.value.status_code == 401
    assert "bad signature" in exc.value.detail


def test_operator_or_root_peer_dependency_accepts_operator_or_root_peer(monkeypatch):
    request = FakeRequest()
    monkeypatch.setattr(federation_api, "require_authenticated_request", lambda req: None)
    asyncio.run(federation_api._require_operator_or_root_peer_request(request))
    assert request.state.federation_auth_mode == "operator"

    peer = SimpleNamespace(instance_id="root-1", role="root")
    topology = SimpleNamespace(
        list_peers=lambda: [peer],
        get_peer=lambda instance_id: peer if instance_id == "root-1" else None,
        deployment_mode=SimpleNamespace(value="federated"),
    )
    auth = SimpleNamespace(
        verify_request=lambda **kwargs: SimpleNamespace(valid=True, instance_id="root-1", reason="")
    )
    _init(topology_registry=topology, auth=auth)
    monkeypatch.setattr(
        federation_api,
        "require_authenticated_request",
        lambda req: (_ for _ in ()).throw(HTTPException(status_code=401, detail="missing")),
    )

    signed = FakeRequest(body=b"{}", path="/api/federation/dashboard/local")
    asyncio.run(federation_api._require_operator_or_root_peer_request(signed))
    assert signed.state.federation_auth_mode == "root_peer"

    peer.role = "child"
    with pytest.raises(HTTPException) as exc:
        asyncio.run(federation_api._require_operator_or_root_peer_request(FakeRequest(body=b"{}")))
    assert exc.value.status_code == 403


def test_heartbeat_identity_status_settings_and_health_routes(monkeypatch):
    heartbeat = SimpleNamespace(
        timestamp="2026-05-01T00:00:00Z",
        deployment_mode="federated",
        soul_version_hash="soul-hash",
        budget_utilization_pct=12.5,
        to_dict=lambda: {"timestamp": "2026-05-01T00:00:00Z", "soul_version_hash": "soul-hash"},
    )
    emitter = SimpleNamespace(get_latest=lambda: heartbeat)
    topology = SimpleNamespace(
        list_peers=lambda: [SimpleNamespace(instance_id="peer-1", to_dict=lambda: {"instance_id": "peer-1"})],
        get_health_summary=lambda: {"total_peers": 1, "healthy": 1},
        deployment_mode=SimpleNamespace(value="federated"),
    )
    saved = []
    monkeypatch.setattr(federation_api, "save_federation_config", lambda cfg: saved.append(cfg.self_address))
    _init(heartbeat_emitter=emitter, topology_registry=topology)

    assert _body(asyncio.run(federation_api.latest_heartbeat()))["heartbeat"]["soul_version_hash"] == "soul-hash"
    assert _body(asyncio.run(federation_api.get_identity()))["instance_id"] == "local-instance"
    assert _body(asyncio.run(federation_api.get_status()))["peer_count"] == 1
    assert _body(asyncio.run(federation_api.get_federation_settings()))["deployment_mode"] == "federated"

    updated = asyncio.run(
        federation_api.update_federation_settings(
            federation_api.UpdateFederationSettingsRequest(self_address="https://new.example/")
        )
    )
    assert _body(updated)["self_address"] == "https://new.example"
    assert saved == ["https://new.example"]

    with pytest.raises(HTTPException) as bad_url:
        asyncio.run(
            federation_api.update_federation_settings(
                federation_api.UpdateFederationSettingsRequest(self_address="ftp://bad")
            )
        )
    assert bad_url.value.status_code == 400

    health = _body(asyncio.run(federation_api.get_federation_health()))
    assert health["total_peers"] == 1
    assert health["runtime_degraded"] is True


def test_command_pause_resume_and_handoff_peer_routes():
    command_calls = []
    handoff_calls = []

    command_relay = SimpleNamespace(
        handle_kill_command=lambda body, authenticated_instance_id="": command_calls.append(("kill", body, authenticated_instance_id)) or {"accepted": True},
        handle_pause=lambda body, authenticated_instance_id="": command_calls.append(("pause", body, authenticated_instance_id)) or {"accepted": False},
        handle_resume=lambda body, authenticated_instance_id="": command_calls.append(("resume", body, authenticated_instance_id)) or {"accepted": True},
    )

    handoff_protocol = SimpleNamespace(
        handle_handoff_initiation=lambda body, authenticated_instance_id="": handoff_calls.append(("init", body, authenticated_instance_id)) or {"accepted": True},
        handle_completion_report=lambda body, authenticated_instance_id="": handoff_calls.append(("complete", body, authenticated_instance_id)) or {"completed": True},
        get_handoff_status=lambda handoff_id: {"handoff_id": handoff_id} if handoff_id == "h1" else None,
    )
    _init(command_relay=command_relay, handoff_protocol=handoff_protocol)
    request = FakeRequest({"issuer_instance_id": "peer-1"})
    request.state.federation_peer_instance_id = "peer-1"

    assert asyncio.run(federation_api.receive_command(request)).status_code == 200
    assert asyncio.run(federation_api.receive_killswitch(request)).status_code == 200
    assert asyncio.run(federation_api.receive_pause(request)).status_code == 403
    assert asyncio.run(federation_api.receive_resume(request)).status_code == 200

    handoff_req = FakeRequest({"handoff_id": "h1", "source_instance_id": "peer-1"})
    handoff_req.state.federation_peer_instance_id = "peer-1"
    assert asyncio.run(federation_api.initiate_handoff(handoff_req)).status_code == 200
    complete_req = FakeRequest({"handoff_id": "h1", "reporting_instance_id": "peer-1"})
    complete_req.state.federation_peer_instance_id = "peer-1"
    assert asyncio.run(federation_api.complete_handoff(complete_req)).status_code == 200
    assert _body(asyncio.run(federation_api.get_handoff("h1")))["handoff_id"] == "h1"
    assert asyncio.run(federation_api.get_handoff("missing")).status_code == 404
    assert command_calls[0][2] == "peer-1"
    assert handoff_calls[0][2] == "peer-1"


def test_soul_peer_budget_manage_and_audit_routes():
    class PeerProtocol:
        async def handle_registration_request(self, body):
            return {"accepted": True, "registration_id": body.get("registration_id")}

        def handle_registration_confirm(self, body):
            return {"accepted": False, "registration_id": body.get("registration_id")}

        def handle_peer_removal(self, instance_id):
            return {"removed": instance_id}

        async def initiate_registration(self, target_address, target_role):
            return SimpleNamespace(
                success=True,
                peer_instance_id="peer-2",
                peer_fingerprint="fp",
                mutual=True,
                error="",
            )

    class SoulTransport:
        def get_local_soul_hash(self):
            return "soul-hash"

        def handle_handshake(self, body):
            return {"accepted": True, "remote_instance_id": body.get("remote_instance_id")}

        def handle_soul_fetch(self):
            return {"soul": {"rules": []}}

        def handle_soul_push(self, body, authenticated_instance_id=""):
            return {"accepted": False, "from": authenticated_instance_id}

        async def handle_soul_confirmation(self, body, authenticated_instance_id=""):
            return {"accepted": True, "event_id": body.get("event_id")}

    class CostReporter:
        def __init__(self):
            self.error = ""

        def get_aggregate_status(self):
            if self.error:
                return {"error": self.error}
            return {"threshold": "warning", "utilization_pct": 80}

        def handle_cost_report(self, body, authenticated_instance_id=""):
            return {"accepted": True, "instance_id": authenticated_instance_id}

    class HandoffProtocol:
        async def initiate_handoff(self, **kwargs):
            return SimpleNamespace(success=True, handoff_id="h2", state="initiated", target_instance_id=kwargs["target_instance_id"], error="")

        async def report_completion(self, **kwargs):
            return True

    class CommandRelay:
        async def issue_and_propagate_kill(self, command_data):
            return {"issued": True, "target_instance_ids": command_data.get("target_instance_ids", [])}

    audit_entry = SimpleNamespace(to_dict=lambda: {"id": "audit-1"})
    audit_engine = SimpleNamespace(
        query=lambda **kwargs: [audit_entry],
        get_summary=lambda: {"total": 1},
        reconstruct_quest=lambda quest_id: SimpleNamespace(to_dict=lambda: {"quest_id": quest_id}),
    )
    cost_reporter = CostReporter()
    _init(
        peer_protocol=PeerProtocol(),
        soul_transport=SoulTransport(),
        cost_reporter=cost_reporter,
        handoff_protocol=HandoffProtocol(),
        command_relay=CommandRelay(),
        audit_engine=audit_engine,
    )

    peer_req = FakeRequest({"registration_id": "reg-1", "instance_id": "peer-1"})
    peer_req.state.federation_peer_instance_id = "peer-1"
    assert asyncio.run(federation_api.register_peer(peer_req)).status_code == 200
    assert asyncio.run(federation_api.confirm_peer_registration(peer_req)).status_code == 400
    assert _body(asyncio.run(federation_api.remove_peer("peer-1"))) == {"removed": "peer-1"}

    assert _body(asyncio.run(federation_api.get_soul_hash(FakeRequest())))["soul_version_hash"] == "soul-hash"
    assert _body(asyncio.run(federation_api.soul_handshake(FakeRequest({"remote_instance_id": "peer-1"}))))["accepted"] is True
    assert _body(asyncio.run(federation_api.get_soul(FakeRequest())))["soul"] == {"rules": []}
    soul_update_req = FakeRequest({"source_instance_id": "peer-1", "event_id": "e1"})
    soul_update_req.state.federation_peer_instance_id = "peer-1"
    assert asyncio.run(federation_api.receive_soul_update(soul_update_req)).status_code == 400
    soul_confirm_req = FakeRequest({"instance_id": "peer-1", "event_id": "e1"})
    soul_confirm_req.state.federation_peer_instance_id = "peer-1"
    assert _body(asyncio.run(federation_api.confirm_soul_update(soul_confirm_req)))["event_id"] == "e1"

    assert _body(asyncio.run(federation_api.get_budget()))["threshold"] == "warning"
    assert _body(asyncio.run(federation_api.get_budget_threshold()))["threshold"] == "warning"
    budget_req = FakeRequest({"instance_id": "peer-1", "actual_today_usd": 1.25})
    budget_req.state.federation_peer_instance_id = "peer-1"
    assert _body(asyncio.run(federation_api.receive_budget_report(budget_req)))["instance_id"] == "peer-1"
    cost_reporter.error = "cost unavailable"
    assert asyncio.run(federation_api.get_budget()).status_code == 503
    assert asyncio.run(federation_api.get_budget_threshold()).status_code == 503

    assert _body(asyncio.run(federation_api.manage_register_peer(FakeRequest({"target_address": "https://peer.example", "role": "child"}))))["peer_instance_id"] == "peer-2"
    assert asyncio.run(federation_api.manage_register_peer(FakeRequest({"target_address": ""}))).status_code == 400
    assert _body(asyncio.run(federation_api.manage_initiate_handoff(FakeRequest({"target_instance_id": "peer-2"}))))["handoff_id"] == "h2"
    assert asyncio.run(federation_api.manage_initiate_handoff(FakeRequest({"target_instance_id": ""}))).status_code == 400
    assert _body(asyncio.run(federation_api.manage_complete_handoff(FakeRequest({"handoff_id": "h2"}))))["success"] is True
    assert asyncio.run(federation_api.manage_complete_handoff(FakeRequest({"handoff_id": ""}))).status_code == 400
    assert _body(asyncio.run(federation_api.manage_propagate_kill(FakeRequest({"target_ids": ["peer-2"]}))))["issued"] is True

    assert _body(asyncio.run(federation_api.get_audit_entries(quest_id="q1")))["total"] == 1
    assert _body(asyncio.run(federation_api.get_audit_summary())) == {"total": 1}
    assert _body(asyncio.run(federation_api.get_quest_timeline("q1"))) == {"quest_id": "q1"}


def test_dashboard_snapshot_builds_local_and_remote_without_demo_data(monkeypatch):
    monkeypatch.setitem(
        __import__("sys").modules,
        "feature_flags",
        SimpleNamespace(FEATURE_FEDERATION=True, FEATURE_FEDERATION_DASHBOARD=True),
    )
    peer = SimpleNamespace(
        instance_id="peer-1",
        address="https://peer.example",
        role="child",
        metadata={"instance_name": "Peer One"},
        last_heartbeat_at="2026-05-01T00:00:00Z",
        soul_version_hash="soul-hash",
    )
    topology = SimpleNamespace(
        list_peers=lambda: [peer],
        get_peer=lambda instance_id: peer if instance_id == "peer-1" else None,
        deployment_mode=SimpleNamespace(value="federated"),
    )
    async def send_dashboard_detail(**kwargs):
        return SimpleNamespace(
            success=True,
            body={
                "instances": [{"name": "Peer Detail", "pending_approvals": 1}],
                "approvals": [{"id": "approval-remote"}],
                "trust_proposals": [{"id": "trust-remote"}],
                "activity": [{"id": "act-remote", "timestamp": "2026-05-01T00:00:01Z"}],
            },
        )

    transport = SimpleNamespace(
        started=True,
        send=send_dashboard_detail,
        get_circuit_breaker_states=lambda: {},
    )
    monkeypatch.setattr(federation_api, "_collect_local_hive_summary", lambda: {"active_agents": 2, "paused_agents": 0})
    monkeypatch.setattr(federation_api, "_collect_local_approvals", lambda instance_id, instance_name: [{"id": "approval-local", "risk_tier": "T3"}])
    monkeypatch.setattr(federation_api, "_collect_local_trust_proposals", lambda instance_id, instance_name: [{"id": "trust-local", "created_at": "now"}])
    monkeypatch.setattr(federation_api, "_collect_local_activity", lambda instance_id, instance_name, limit: [{"id": "act-local", "timestamp": "2026-05-01T00:00:02Z", "description": "local"}])
    monkeypatch.setattr(federation_api, "_collect_runtime_pause", lambda: {"paused": False})
    monkeypatch.setattr(federation_api, "_collect_local_health_state", lambda: ("healthy", []))
    _init(topology_registry=topology, transport=transport)

    snapshot = asyncio.run(federation_api._build_dashboard_snapshot(include_remote=True))
    serialized = json.dumps(snapshot).lower()

    assert snapshot["enabled"] is True
    assert snapshot["fleet"]["total_instances"] == 2
    assert {item["id"] for item in snapshot["approvals"]} == {"approval-local", "approval-remote"}
    assert "demo federation cluster" not in serialized
    assert "demo" not in serialized


def test_dashboard_collectors_return_clean_empty_state_without_runtime_modules(monkeypatch):
    federation_api.shutdown_federation_api()

    monkeypatch.setitem(__import__("sys").modules, "src.hive.api", SimpleNamespace(_registry=None))
    monkeypatch.setitem(__import__("sys").modules, "src.core.governance_api", SimpleNamespace(_mcp_sentry=None, _rule_engine=None, _trust_ledger=None))
    monkeypatch.setitem(__import__("sys").modules, "src.core.trust_api", SimpleNamespace(_trust_ledger=None))
    monkeypatch.setitem(__import__("sys").modules, "src.core.receipts_api", SimpleNamespace(get_receipt_service_instance=lambda: None))
    monkeypatch.setitem(__import__("sys").modules, "src.core.runtime_pause", SimpleNamespace(get_runtime_pause_status=lambda: "not-a-dict"))
    monkeypatch.setitem(__import__("sys").modules, "health.api", SimpleNamespace(_snapshot_provider=None))
    monkeypatch.setattr(__import__("src.hive", fromlist=[""]), "api", SimpleNamespace(_registry=None), raising=False)
    monkeypatch.setattr(__import__("src.core", fromlist=[""]), "governance_api", SimpleNamespace(_mcp_sentry=None, _rule_engine=None, _trust_ledger=None), raising=False)
    monkeypatch.setattr(__import__("src.core", fromlist=[""]), "trust_api", SimpleNamespace(_trust_ledger=None), raising=False)
    monkeypatch.setattr(__import__("src.core", fromlist=[""]), "receipts_api", SimpleNamespace(get_receipt_service_instance=lambda: None), raising=False)
    monkeypatch.setattr(__import__("src.core", fromlist=[""]), "runtime_pause", SimpleNamespace(get_runtime_pause_status=lambda: "not-a-dict"), raising=False)
    monkeypatch.setattr(__import__("health", fromlist=[""]), "api", SimpleNamespace(_snapshot_provider=None), raising=False)

    cost_by_instance, aggregate = federation_api._collect_cost_data()

    assert cost_by_instance == {}
    assert aggregate == {"utilization_pct": 0.0, "threshold": "unknown", "stale_instance_ids": []}
    assert federation_api._collect_local_hive_summary() == {"active_agents": 0, "paused_agents": 0}
    assert federation_api._collect_local_approvals("local", "Local") == []
    assert federation_api._collect_local_trust_proposals("local", "Local") == []
    assert federation_api._collect_local_activity("local", "Local", 10) == []
    assert federation_api._collect_runtime_pause() == {}
    assert federation_api._collect_local_health_state() == ("healthy", [])


def test_dashboard_collectors_surface_real_runtime_data(monkeypatch):
    cleanup_calls = []
    hive_agents = [
        SimpleNamespace(state=SimpleNamespace(value="executing")),
        SimpleNamespace(state="paused"),
        SimpleNamespace(state="ready"),
    ]
    sentry = SimpleNamespace(
        pending_requests={
            "approval-1": {
                "status": "PENDING",
                "tool": "shell.run",
                "risk_tier": "T3",
                "params": {"cmd": "git status"},
                "timestamp": "2026-05-01T01:00:00Z",
            },
            "approval-done": {"status": "APPROVED", "tool": "ignored"},
        },
        cleanup_expired=lambda: cleanup_calls.append("cleanup"),
    )
    rule = SimpleNamespace(
        id="rule-1",
        name="Allow safe status",
        description="Permit git status",
        created_at="2026-05-01T01:01:00Z",
    )
    proposal = SimpleNamespace(
        id="trust-1",
        capability="shell.run",
        scope="repo",
        current_tier=2,
        proposed_tier=1,
        consecutive_successes=5,
        status="pending",
        created_at="2026-05-01T01:02:00Z",
    )
    receipt = SimpleNamespace(
        id="receipt-1",
        timestamp="2026-05-01T01:03:00Z",
        action_type="tool",
        action_name="",
        metadata={"operator_id": "operator-1", "description": "Ran git status"},
        outputs={},
        inputs={},
        status="success",
    )
    snapshot = SimpleNamespace(
        ready=False,
        last_health_tick_at="2026-05-01T01:04:00Z",
        degraded_reasons=["LLM provider not initialized"],
    )

    class CostItem(dict):
        def to_dict(self):
            return dict(self)

    cost_reporter = SimpleNamespace(
        get_aggregate_status=lambda: {
            "instance_id": "local",
            "utilization_pct": 12.5,
            "threshold": "normal",
        },
        _cost_aggregator=SimpleNamespace(
            get_all_instances=lambda: [
                CostItem(instance_id="peer-1", utilization_pct=85.0, threshold="warning"),
                {"instance_id": "peer-2", "utilization_pct": 3.0},
            ],
        ),
    )
    federation_api.init_federation_transport(cost_reporter=cost_reporter)
    fake_hive_api = SimpleNamespace(_registry=SimpleNamespace(list_active=lambda: hive_agents))
    fake_governance_api = SimpleNamespace(
        _mcp_sentry=sentry,
        _rule_engine=SimpleNamespace(list_rules=lambda status: [rule]),
        _trust_ledger=None,
    )
    fake_trust_api = SimpleNamespace(_trust_ledger=SimpleNamespace(pending_proposals=lambda: [proposal]))
    fake_receipts_api = SimpleNamespace(get_receipt_service_instance=lambda: SimpleNamespace(list=lambda limit: [receipt]))
    fake_pause_api = SimpleNamespace(get_runtime_pause_status=lambda: {"paused": True, "reason": "operator pause"})
    fake_health_api = SimpleNamespace(_snapshot_provider=object(), _get_snapshot=lambda: snapshot)
    monkeypatch.setitem(
        __import__("sys").modules,
        "src.hive.api",
        fake_hive_api,
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "src.core.governance_api",
        fake_governance_api,
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "src.core.trust_api",
        fake_trust_api,
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "src.core.receipts_api",
        fake_receipts_api,
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "src.core.runtime_pause",
        fake_pause_api,
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "health.api",
        fake_health_api,
    )
    monkeypatch.setattr(__import__("src.hive", fromlist=[""]), "api", fake_hive_api, raising=False)
    monkeypatch.setattr(__import__("src.core", fromlist=[""]), "governance_api", fake_governance_api, raising=False)
    monkeypatch.setattr(__import__("src.core", fromlist=[""]), "trust_api", fake_trust_api, raising=False)
    monkeypatch.setattr(__import__("src.core", fromlist=[""]), "receipts_api", fake_receipts_api, raising=False)
    monkeypatch.setattr(__import__("src.core", fromlist=[""]), "runtime_pause", fake_pause_api, raising=False)
    monkeypatch.setattr(__import__("health", fromlist=[""]), "api", fake_health_api, raising=False)

    cost_by_instance, aggregate = federation_api._collect_cost_data()
    hive = federation_api._collect_local_hive_summary()
    approvals = federation_api._collect_local_approvals("local", "Local")
    trust = federation_api._collect_local_trust_proposals("local", "Local")
    activity = federation_api._collect_local_activity("local", "Local", 10)

    assert cost_by_instance["local"]["threshold"] == "normal"
    assert cost_by_instance["peer-1"]["threshold"] == "warning"
    assert aggregate["utilization_pct"] == 12.5
    assert hive == {"active_agents": 2, "paused_agents": 1}
    assert [item["type"] for item in approvals] == ["sentry", "apl_rule"]
    assert approvals[0]["context"] == '{"cmd": "git status"}'
    assert trust[0]["capability"] == "shell.run"
    assert activity[0]["description"] == "Ran git status"
    assert activity[0]["operator"] == "operator-1"
    assert federation_api._collect_runtime_pause() == {"paused": True, "reason": "operator pause"}
    assert federation_api._collect_local_health_state() == ("degraded", ["LLM provider not initialized"])
    assert cleanup_calls == ["cleanup"]


def test_dashboard_decision_helpers_apply_local_and_proxy_remote_decisions(monkeypatch):
    monkeypatch.setitem(
        __import__("sys").modules,
        "feature_flags",
        SimpleNamespace(FEATURE_FEDERATION=True, FEATURE_FEDERATION_DASHBOARD=True),
    )
    receipt_calls = []
    local_calls = []
    identity = SimpleNamespace(
        is_valid=True,
        operator_id="operator-1",
        to_dict=lambda: {"operator_id": "operator-1", "is_valid": True},
    )
    peer = SimpleNamespace(instance_id="peer-1", address="https://peer.example")
    topology = SimpleNamespace(
        list_peers=lambda: [peer],
        get_peer=lambda instance_id: peer if instance_id == "peer-1" else None,
        deployment_mode=SimpleNamespace(value="federated"),
    )

    async def send(**kwargs):
        receipt_calls.append(("send", kwargs))
        return SimpleNamespace(success=True, body={"success": True, "result": {"type": "sentry", "status": "approved"}})

    monkeypatch.setattr("src.core.auth_api.resolve_authenticated_identity", lambda request: identity)
    monkeypatch.setattr("src.core.auth_api.request_has_capability", lambda request, capability: True)
    monkeypatch.setattr("src.core.operator_identity.OperatorIdentity.from_dict", lambda payload: identity)
    fake_governance_api = SimpleNamespace(
        _approve_item_direct=lambda approval_id, reason, identity: local_calls.append(("approve", approval_id, reason, identity.operator_id)) or {"type": "sentry", "status": "approved"},
        _deny_item_direct=lambda approval_id, reason, identity: local_calls.append(("deny", approval_id, reason, identity.operator_id)) or {"type": "apl_rule", "status": "denied"},
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "src.core.governance_api",
        fake_governance_api,
    )
    monkeypatch.setattr(__import__("src.core", fromlist=[""]), "governance_api", fake_governance_api, raising=False)
    monkeypatch.setattr(
        "src.core.governance_receipts.emit_governance_receipt_for_identity",
        lambda *args, **kwargs: receipt_calls.append(("receipt", args, kwargs)),
    )
    _init(identity=_identity("local"), topology_registry=topology, transport=SimpleNamespace(send=send), config=_config(command_timeout_s=50.0))

    local = asyncio.run(
        federation_api._handle_federation_dashboard_decision(
            FakeRequest(),
            instance_id="local",
            approval_id="approval-local",
            decision="approve",
            reason=" ship ",
        )
    )
    remote = asyncio.run(
        federation_api._handle_federation_dashboard_decision(
            FakeRequest(),
            instance_id="peer-1",
            approval_id="approval remote",
            decision="deny",
            reason=" deny remotely ",
        )
    )
    local_direct = federation_api._handle_local_dashboard_decision(
        FakeRequest(),
        approval_id="approval-direct",
        decision="deny",
        body=federation_api.FederatedDashboardDecisionRequest(reason="deny", operator_identity=identity.to_dict(), source_instance_id="peer-1"),
    )

    assert _body(local)["result"]["status"] == "approved"
    assert _body(remote)["remote"]["success"] is True
    assert _body(local_direct)["result"]["status"] == "denied"
    assert local_calls == [
        ("approve", "approval-local", "ship", "operator-1"),
        ("deny", "approval-direct", "deny", "operator-1"),
    ]
    assert receipt_calls[0][0] == "send"
    assert receipt_calls[0][1]["path"].endswith("/approval%20remote/deny")
    assert receipt_calls[1][0] == "receipt"


def test_dashboard_decision_helpers_reject_invalid_inputs_and_remote_failures(monkeypatch):
    monkeypatch.setitem(
        __import__("sys").modules,
        "feature_flags",
        SimpleNamespace(FEATURE_FEDERATION=True, FEATURE_FEDERATION_DASHBOARD=True),
    )
    monkeypatch.setattr("src.core.auth_api.request_has_capability", lambda request, capability: capability == "federation.admin")
    fake_governance_api = SimpleNamespace(
        _approve_item_direct=lambda approval_id, reason, identity: None,
        _deny_item_direct=lambda approval_id, reason, identity: "denied",
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "src.core.governance_api",
        fake_governance_api,
    )
    monkeypatch.setattr(__import__("src.core", fromlist=[""]), "governance_api", fake_governance_api, raising=False)
    _init(identity=_identity("local"), topology_registry=SimpleNamespace(list_peers=lambda: [], get_peer=lambda instance_id: None, deployment_mode=SimpleNamespace(value="federated")))

    with pytest.raises(HTTPException) as empty_reason:
        federation_api._clean_decision_reason("  ")
    with pytest.raises(HTTPException) as missing_peer:
        federation_api._find_dashboard_peer("missing")
    with pytest.raises(HTTPException) as unsupported:
        federation_api._apply_local_dashboard_decision("approval", decision="maybe", reason="r", identity=SimpleNamespace())
    with pytest.raises(HTTPException) as not_found:
        federation_api._apply_local_dashboard_decision("approval", decision="approve", reason="r", identity=SimpleNamespace())
    with pytest.raises(HTTPException) as missing_capability:
        federation_api._require_dashboard_operator_decision_capabilities(FakeRequest())

    assert empty_reason.value.status_code == 400
    assert missing_peer.value.status_code == 404
    assert unsupported.value.status_code == 400
    assert not_found.value.status_code == 404
    assert missing_capability.value.status_code == 403

    request = FakeRequest()
    request.state.federation_auth_mode = "root_peer"
    federation_api._require_dashboard_operator_decision_capabilities(request)

    result = federation_api._apply_local_dashboard_decision("approval", decision="deny", reason="r", identity=SimpleNamespace())
    assert result == {"status": "deny", "id": "approval", "result": "denied"}

    with pytest.raises(HTTPException) as no_transport:
        asyncio.run(federation_api._send_dashboard_decision_to_peer(SimpleNamespace(address="https://peer"), approval_id="a", decision="approve", reason="r", identity=SimpleNamespace(to_dict=lambda: {})))
    assert no_transport.value.status_code == 503

    async def failing_send(**kwargs):
        return SimpleNamespace(success=False, status_code=399, body={"error": "peer failed"}, error="")

    federation_api.init_federation_transport(transport=SimpleNamespace(send=failing_send))
    with pytest.raises(HTTPException) as remote_failed:
        asyncio.run(federation_api._send_dashboard_decision_to_peer(SimpleNamespace(instance_id="peer", address="https://peer"), approval_id="a", decision="approve", reason="r", identity=SimpleNamespace(to_dict=lambda: {})))
    assert remote_failed.value.status_code == 502
    assert remote_failed.value.detail == "peer failed"

    async def invalid_payload(**kwargs):
        return SimpleNamespace(success=True, body=["not", "dict"])

    federation_api.init_federation_transport(transport=SimpleNamespace(send=invalid_payload))
    with pytest.raises(HTTPException) as invalid_remote:
        asyncio.run(federation_api._send_dashboard_decision_to_peer(SimpleNamespace(instance_id="peer", address="https://peer"), approval_id="a", decision="approve", reason="r", identity=SimpleNamespace(to_dict=lambda: {})))
    assert invalid_remote.value.status_code == 502
