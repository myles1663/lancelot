"""Tests for Federation Soul Transport."""

from __future__ import annotations

import asyncio
import yaml
import types
from datetime import datetime, timedelta, timezone

import pytest

from src.core.soul.store import Soul
from src.federation.identity import generate_identity
from src.federation.soul_propagation import InstancePropState, PropagationTier, SoulPropagationEngine
from src.federation.soul_compat import hash_soul
from src.federation.soul_transport import SoulTransport
from src.federation.topology import TopologyRegistry


def _valid_soul_dict(version: str = "v1", **overrides) -> dict:
    base = {
        "version": version,
        "mission": "Serve the owner faithfully.",
        "allegiance": "Single owner loyalty.",
        "autonomy_posture": {
            "level": "supervised",
            "description": "Supervised autonomy.",
            "allowed_autonomous": ["classify_intent"],
            "requires_approval": ["deploy", "delete"],
        },
        "risk_rules": [
            {
                "name": "destructive_actions_require_approval",
                "description": "Destructive actions need approval",
                "enforced": True,
            }
        ],
        "approval_rules": {
            "default_timeout_seconds": 3600,
            "escalation_on_timeout": "skip_and_log",
            "channels": ["war_room"],
        },
        "tone_invariants": [
            "Never mislead the owner",
            "Never suppress errors or degrade silently",
        ],
        "memory_ethics": ["Do not store PII without consent"],
        "scheduling_boundaries": {
            "max_concurrent_jobs": 5,
            "max_job_duration_seconds": 300,
            "no_autonomous_irreversible": True,
            "require_ready_state": True,
            "description": "Safe scheduling.",
        },
    }
    base.update(overrides)
    return base


@pytest.fixture
def root_identity():
    return generate_identity()


@pytest.fixture
def child_identity():
    return generate_identity()


@pytest.fixture
def topology(root_identity, child_identity):
    topo = TopologyRegistry(self_instance_id=root_identity.instance_id)
    topo.register_peer(
        instance_id=child_identity.instance_id,
        fingerprint=child_identity.fingerprint,
        public_key_hex=child_identity.public_key_hex(),
        address="http://child:8000",
        role="child",
    )
    return topo


@pytest.fixture
def runtime_soul():
    return Soul(**_valid_soul_dict())


@pytest.fixture
def soul_dir(tmp_path):
    soul_root = tmp_path / "soul"
    versions_dir = soul_root / "soul_versions"
    versions_dir.mkdir(parents=True)
    (versions_dir / "soul_v1.yaml").write_text(
        yaml.safe_dump(_valid_soul_dict("v1"), sort_keys=False),
        encoding="utf-8",
    )
    (soul_root / "ACTIVE").write_text("v1", encoding="utf-8")
    return str(soul_root)


@pytest.fixture
def transport_obj(root_identity, topology, runtime_soul, soul_dir):
    return SoulTransport(
        identity=root_identity,
        transport=None,
        topology=topology,
        current_soul_provider=lambda: runtime_soul,
        runtime_reload_callback=lambda soul: None,
        soul_dir=soul_dir,
    )


class _StubTransport:
    def __init__(self, body):
        self.body = body

    async def send(self, **kwargs):
        return type("Result", (), {"success": True, "body": self.body, "error": "", "latency_ms": 1})()


class _BroadcastStubTransport:
    def __init__(self, result_map):
        self._result_map = result_map
        self.calls = []
        self.requests = []

    async def broadcast(self, peers, **kwargs):
        self.calls.append(kwargs.get("path"))
        return {
            peer["instance_id"]: self._result_map[peer["instance_id"]]
            for peer in peers
        }

    async def send(self, **kwargs):
        self.requests.append(kwargs)
        return type(
            "Result",
            (),
            {"success": True, "error": "", "latency_ms": 1, "body": {"accepted": True}, "status_code": 200},
        )()


@pytest.mark.asyncio
async def test_push_with_no_targets_returns_empty_handshake(root_identity, runtime_soul, soul_dir):
    empty_topology = TopologyRegistry(self_instance_id=root_identity.instance_id)
    transport = SoulTransport(
        identity=root_identity,
        transport=_BroadcastStubTransport({}),
        topology=empty_topology,
        current_soul_provider=lambda: runtime_soul,
        soul_dir=soul_dir,
    )

    result = await transport.push_soul_update(
        soul_document=_valid_soul_dict("v2"),
        soul_hash="hash-v2",
        tier="T2",
        reason="no peers",
    )

    assert result["delivered"] == 0
    assert result["handshake"]["total_peers"] == 0
    assert result["handshake"]["all_acknowledged"] is False


class TestHandleSoulPush:
    def test_valid_push_accepted_and_applied(self, child_identity, root_identity, soul_dir):
        applied = []
        child_topo = TopologyRegistry(self_instance_id=child_identity.instance_id)
        child_topo.register_peer(
            instance_id=root_identity.instance_id,
            fingerprint=root_identity.fingerprint,
            public_key_hex=root_identity.public_key_hex(),
            address="http://root:8000",
            role="root",
        )
        incoming = _valid_soul_dict("v2", mission="Serve the updated owner faithfully.")
        incoming_soul = Soul(**incoming)
        child_transport = SoulTransport(
            identity=child_identity,
            transport=None,
            topology=child_topo,
            current_soul_provider=lambda: Soul(**_valid_soul_dict("v1")),
            runtime_reload_callback=lambda soul: applied.append(soul.version),
            soul_dir=soul_dir,
        )

        result = child_transport.handle_soul_push(
            {
                "source_instance_id": root_identity.instance_id,
                "soul_document": incoming,
                "soul_hash": hash_soul(incoming_soul),
                "tier": "T1",
            }
        )

        assert result["accepted"] is True
        assert result["soul_hash"] == hash_soul(incoming_soul)
        assert applied == ["v2"]

    def test_t3_push_dispatches_confirmation_to_root(
        self,
        child_identity,
        root_identity,
        soul_dir,
    ):
        applied = []
        child_topo = TopologyRegistry(self_instance_id=child_identity.instance_id)
        child_topo.register_peer(
            instance_id=root_identity.instance_id,
            fingerprint=root_identity.fingerprint,
            public_key_hex=root_identity.public_key_hex(),
            address="http://root:8000",
            role="root",
        )
        transport = _BroadcastStubTransport({})
        child_transport = SoulTransport(
            identity=child_identity,
            transport=transport,
            topology=child_topo,
            current_soul_provider=lambda: Soul(**_valid_soul_dict("v1")),
            runtime_reload_callback=lambda soul: applied.append(soul.version),
            soul_dir=soul_dir,
        )

        result = child_transport.handle_soul_push(
            {
                "source_instance_id": root_identity.instance_id,
                "soul_document": _valid_soul_dict("v2", mission="Updated"),
                "soul_hash": hash_soul(Soul(**_valid_soul_dict("v2", mission="Updated"))),
                "tier": "T3",
                "event_id": "ev-1",
            }
        )

        assert result["accepted"] is True
        assert result["event_id"] == "ev-1"
        assert applied == ["v2"]
        assert len(transport.requests) == 1
        assert transport.requests[0]["path"] == "/api/federation/soul/confirm"
        assert transport.requests[0]["body"] == {
            "event_id": "ev-1",
            "instance_id": child_identity.instance_id,
        }

    def test_push_from_non_root_peer_rejected(self, child_identity, soul_dir):
        applied = []
        child_topo = TopologyRegistry(self_instance_id=child_identity.instance_id)
        non_root_peer = generate_identity()
        child_topo.register_peer(
            instance_id=non_root_peer.instance_id,
            fingerprint=non_root_peer.fingerprint,
            public_key_hex=non_root_peer.public_key_hex(),
            address="http://peer:8000",
            role="peer",
        )
        child_transport = SoulTransport(
            identity=child_identity,
            transport=None,
            topology=child_topo,
            current_soul_provider=lambda: Soul(**_valid_soul_dict("v1")),
            runtime_reload_callback=lambda soul: applied.append(soul.version),
            soul_dir=soul_dir,
        )

        result = child_transport.handle_soul_push(
            {
                "source_instance_id": non_root_peer.instance_id,
                "soul_document": _valid_soul_dict("v2"),
                "soul_hash": hash_soul(Soul(**_valid_soul_dict("v2"))),
                "tier": "T1",
            }
        )

        assert result["accepted"] is False
        assert "root-authority" in result["error"]
        assert applied == []


@pytest.mark.asyncio
async def test_transport_fetch_handshake_and_runtime_helper_edges(root_identity, topology, runtime_soul, soul_dir):
    class FailingTransport:
        async def send(self, **kwargs):
            return type("Result", (), {"success": False, "body": {}, "error": "timeout", "status_code": 504})()

    transport = SoulTransport(
        identity=root_identity,
        transport=FailingTransport(),
        topology=topology,
        current_soul_provider=lambda: runtime_soul,
        soul_dir=soul_dir,
    )

    assert await transport.fetch_soul_from_peer("http://peer", "peer-1") == {"error": "timeout"}
    assert (await transport.perform_handshake("http://peer", "peer-1"))["error"] == "timeout"
    assert transport._parse_tier("unknown") == PropagationTier.T1_MINOR
    assert transport._event_to_dict(None) == {}
    assert transport._event_to_dict(types.SimpleNamespace(to_dict=lambda: (_ for _ in ()).throw(RuntimeError("bad")))) == {}
    assert SoulTransport.parse_timestamp("not-a-date") is None
    assert transport.get_local_soul_hash() == hash_soul(runtime_soul)
    assert transport.get_consistency_state() == "synchronized"
    assert transport.get_active_propagations() == []

    no_soul = SoulTransport(
        identity=root_identity,
        transport=FailingTransport(),
        topology=topology,
        current_soul_provider=lambda: None,
        soul_dir=soul_dir,
    )
    no_soul._soul_dir = "__missing_soul_dir__"
    fetched = no_soul.handle_soul_fetch()
    assert fetched["error"] == "No active runtime Soul available"
    assert no_soul.handle_handshake({"remote_soul_document": _valid_soul_dict()})["error"] == "No local runtime Soul available"
    assert no_soul.get_local_soul_hash() == ""

    missing_doc = transport.handle_handshake({"remote_soul_hash": "hash", "remote_instance_id": "peer-1"})
    assert missing_doc["error"] == "Handshake payload missing remote_soul_document"
    bad_hash = transport.handle_handshake({
        "remote_soul_document": _valid_soul_dict("v2"),
        "remote_soul_hash": "wrong",
        "remote_instance_id": "peer-1",
    })
    assert bad_hash["error"] == "Remote Soul hash mismatch"


@pytest.mark.asyncio
async def test_t2_pause_failure_marks_handshake_and_resumes_local_fallback(child_identity, root_identity, soul_dir):
    child_topo = TopologyRegistry(self_instance_id=root_identity.instance_id)
    child_topo.register_peer(
        instance_id=child_identity.instance_id,
        fingerprint=child_identity.fingerprint,
        public_key_hex=child_identity.public_key_hex(),
        address="http://child:8000",
        role="child",
    )

    pause_failure = type(
        "Result",
        (),
        {"success": False, "error": "governance approval denied", "latency_ms": 1, "body": {}, "status_code": 403},
    )()
    transport = _BroadcastStubTransport({child_identity.instance_id: pause_failure})
    local_pause_calls = []
    soul_transport = SoulTransport(
        identity=root_identity,
        transport=transport,
        topology=child_topo,
        current_soul_provider=lambda: Soul(**_valid_soul_dict("v1")),
        local_pause_handler=lambda reason: local_pause_calls.append(reason),
        soul_dir=soul_dir,
    )

    result = await soul_transport.push_soul_update(
        soul_document=_valid_soul_dict("v2"),
        soul_hash=hash_soul(Soul(**_valid_soul_dict("v2"))),
        tier="T2",
        reason="governed update",
    )

    assert result["delivered"] == 0
    assert result["handshake"]["denied"] == 1
    assert "governed update" in local_pause_calls[0]
    assert transport.calls == ["/api/federation/pause"]


@pytest.mark.asyncio
async def test_confirmation_rejects_invalid_events_and_resumes_when_all_confirmed(
    child_identity,
    root_identity,
    topology,
    runtime_soul,
    soul_dir,
):
    class Engine:
        def __init__(self):
            self.resume_recorded = []
            self.event = types.SimpleNamespace(
                event_id="ev-1",
                tier=PropagationTier.T3_CRITICAL,
                reason="critical",
                instances=[
                    types.SimpleNamespace(instance_id=root_identity.instance_id, state=InstancePropState.CONFIRMED),
                    types.SimpleNamespace(instance_id=child_identity.instance_id, state=InstancePropState.ACTIVATED),
                ],
                to_dict=lambda: {"event_id": "ev-1"},
            )
            self.consistency_state = types.SimpleNamespace(value="divergent")

        def get_event(self, event_id):
            return self.event if event_id == "ev-1" else None

        def record_confirmation(self, event_id, confirmer_id):
            if confirmer_id == "bad":
                return False
            for instance in self.event.instances:
                if instance.instance_id == confirmer_id:
                    instance.state = InstancePropState.CONFIRMED
            return True

        def record_resume(self, event_id, instance_id):
            self.resume_recorded.append((event_id, instance_id))

        def complete_confirmed_event(self, event_id):
            self.completed = event_id

        def get_active_events(self):
            return [self.event]

    success = type("Result", (), {"success": True, "error": "", "body": {}, "status_code": 200, "latency_ms": 1})()
    transport = _BroadcastStubTransport({child_identity.instance_id: success})
    resumes = []
    engine = Engine()
    soul_transport = SoulTransport(
        identity=root_identity,
        transport=transport,
        topology=topology,
        current_soul_provider=lambda: runtime_soul,
        local_resume_handler=lambda reason: resumes.append(reason),
        soul_dir=soul_dir,
    )
    soul_transport._propagation_engine = engine

    assert (await soul_transport.handle_soul_confirmation({}))["error"] == "Missing required field: event_id"
    assert (await soul_transport.handle_soul_confirmation({"event_id": "missing", "instance_id": "x"}))["error"].startswith("Unknown")
    engine.event.tier = PropagationTier.T2_SIGNIFICANT
    assert "only valid for T3" in (await soul_transport.handle_soul_confirmation({"event_id": "ev-1", "instance_id": child_identity.instance_id}))["error"]
    engine.event.tier = PropagationTier.T3_CRITICAL
    assert "does not match" in (await soul_transport.handle_soul_confirmation(
        {"event_id": "ev-1", "instance_id": "other"},
        authenticated_instance_id=child_identity.instance_id,
    ))["error"]
    assert "Unable to record" in (await soul_transport.handle_soul_confirmation({"event_id": "ev-1", "instance_id": "bad"}))["error"]

    accepted = await soul_transport.handle_soul_confirmation({"event_id": "ev-1", "instance_id": child_identity.instance_id})
    assert accepted["accepted"] is True
    assert accepted["all_confirmed"] is True
    assert transport.calls[-1] == "/api/federation/resume"
    assert resumes and "critical" in resumes[0]
    assert engine.completed == "ev-1"


def test_finalize_stale_propagations_records_timeout(root_identity, topology, runtime_soul, soul_dir):
    event = types.SimpleNamespace(
        event_id="ev-stale",
        initiated_at=(datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat(),
        timeout_seconds=1,
        instances=[
            types.SimpleNamespace(instance_id="peer-1", state=InstancePropState.PENDING),
        ],
    )
    rejections = []
    engine = types.SimpleNamespace(
        get_active_events=lambda: [event],
        record_rejection=lambda event_id, instance_id, reason: rejections.append((event_id, instance_id, reason)),
        consistency_state=types.SimpleNamespace(value="synchronized"),
    )
    transport = SoulTransport(
        identity=root_identity,
        transport=None,
        topology=topology,
        current_soul_provider=lambda: runtime_soul,
        soul_dir=soul_dir,
    )
    transport._propagation_engine = engine

    assert transport.get_consistency_state() == "synchronized"
    transport._finalize_stale_propagations()
    assert rejections[0][0] == "ev-stale"

    def test_push_from_unknown_rejected(self, transport_obj):
        result = transport_obj.handle_soul_push(
            {
                "source_instance_id": "unknown-peer",
                "soul_document": _valid_soul_dict(),
                "soul_hash": "hash",
                "tier": "T1",
            }
        )
        assert result["accepted"] is False
        assert "Unknown" in result["error"]

    def test_hash_mismatch_rejected(self, child_identity, root_identity, soul_dir):
        child_topo = TopologyRegistry(self_instance_id=child_identity.instance_id)
        child_topo.register_peer(
            instance_id=root_identity.instance_id,
            fingerprint=root_identity.fingerprint,
            public_key_hex=root_identity.public_key_hex(),
            address="http://root:8000",
            role="root",
        )
        child_transport = SoulTransport(
            identity=child_identity,
            transport=None,
            topology=child_topo,
            current_soul_provider=lambda: Soul(**_valid_soul_dict("v1")),
            runtime_reload_callback=lambda soul: None,
            soul_dir=soul_dir,
        )
        result = child_transport.handle_soul_push(
            {
                "source_instance_id": root_identity.instance_id,
                "soul_document": _valid_soul_dict("v2"),
                "soul_hash": "bad-hash",
                "tier": "T1",
            }
        )
        assert result["accepted"] is False
        assert "hash mismatch" in result["error"].lower()

    def test_push_rejects_authenticated_source_mismatch(self, child_identity, root_identity, soul_dir):
        child_topo = TopologyRegistry(self_instance_id=child_identity.instance_id)
        child_topo.register_peer(
            instance_id=root_identity.instance_id,
            fingerprint=root_identity.fingerprint,
            public_key_hex=root_identity.public_key_hex(),
            address="http://root:8000",
            role="root",
        )
        child_transport = SoulTransport(
            identity=child_identity,
            transport=None,
            topology=child_topo,
            current_soul_provider=lambda: Soul(**_valid_soul_dict("v1")),
            runtime_reload_callback=lambda soul: None,
            soul_dir=soul_dir,
        )
        result = child_transport.handle_soul_push(
            {
                "source_instance_id": "spoofed-peer",
                "soul_document": _valid_soul_dict("v2"),
                "tier": "T1",
            },
            authenticated_instance_id=root_identity.instance_id,
        )
        assert result["accepted"] is False
        assert "does not match authenticated peer" in result["error"]

    def test_push_applies_mcp_ceiling_against_receiver_runtime_soul(
        self,
        child_identity,
        root_identity,
        soul_dir,
    ):
        applied = []
        child_topo = TopologyRegistry(self_instance_id=child_identity.instance_id)
        child_topo.register_peer(
            instance_id=root_identity.instance_id,
            fingerprint=root_identity.fingerprint,
            public_key_hex=root_identity.public_key_hex(),
            address="http://root:8000",
            role="root",
        )
        current = Soul(**_valid_soul_dict(
            "v1",
            mcp_permissions=[
                {
                    "server_id": "github-mcp",
                    "allowed_tools": ["read_repo"],
                    "risk_tier": "T2",
                }
            ],
        ))
        pushed = _valid_soul_dict(
            "v2",
            mcp_permissions=[
                {
                    "server_id": "github-mcp",
                    "allowed_tools": ["*"],
                    "risk_tier": "T1",
                }
            ],
        )
        child_transport = SoulTransport(
            identity=child_identity,
            transport=None,
            topology=child_topo,
            current_soul_provider=lambda: current,
            runtime_reload_callback=lambda soul: applied.append(soul),
            soul_dir=soul_dir,
        )

        result = child_transport.handle_soul_push(
            {
                "source_instance_id": root_identity.instance_id,
                "soul_document": pushed,
                "soul_hash": hash_soul(Soul(**pushed)),
                "tier": "T1",
            }
        )

        assert result["accepted"] is True
        assert result["mcp_ceiling"]["enforced"] is True
        assert len(applied) == 1
        assert applied[0].mcp_permissions == [
            {
                "server_id": "github-mcp",
                "allowed_tools": ["read_repo"],
                "risk_tier": "T2",
            }
        ]


class TestHandleSoulFetch:
    def test_returns_runtime_soul_document_and_hash(self, transport_obj, runtime_soul, root_identity):
        result = transport_obj.handle_soul_fetch()
        assert result["instance_id"] == root_identity.instance_id
        assert result["soul_document"]["mission"] == runtime_soul.mission
        assert result["soul_hash"] == hash_soul(runtime_soul)


class TestHandshake:
    @pytest.mark.asyncio
    async def test_perform_handshake_compares_remote_hash(self, root_identity, topology, runtime_soul):
        remote_doc = _valid_soul_dict("v1")
        stub_transport = _StubTransport(
            {
                "instance_id": "peer-1",
                "soul_version_hash": hash_soul(runtime_soul),
                "local_hash": hash_soul(runtime_soul),
                "remote_hash": hash_soul(Soul(**remote_doc)),
                "remote_instance_id": "peer-1",
                "compatible": True,
                "compatibility_level": "green",
                "notes": [],
            }
        )
        transport_obj = SoulTransport(
            identity=root_identity,
            transport=stub_transport,
            topology=topology,
            current_soul_provider=lambda: runtime_soul,
        )

        result = await transport_obj.perform_handshake("http://peer:8000", "peer-1")

        assert result["compatible"] is True
        assert result["local_hash"] == hash_soul(runtime_soul)
        assert result["remote_hash"] == hash_soul(Soul(**remote_doc))

    def test_handle_handshake_validates_remote_document(self, transport_obj, runtime_soul):
        remote_doc = _valid_soul_dict("v2", mission="Updated")
        result = transport_obj.handle_handshake(
            {
                "remote_instance_id": "peer-1",
                "remote_soul_hash": hash_soul(Soul(**remote_doc)),
                "remote_soul_document": remote_doc,
            }
        )

        assert result["local_hash"] == hash_soul(runtime_soul)
        assert result["remote_hash"] == hash_soul(Soul(**remote_doc))
        assert result["compatibility_level"] in {"yellow", "red"}


class TestResolveTargets:
    def test_all_peers(self, transport_obj, child_identity):
        targets = transport_obj._resolve_targets()
        assert len(targets) == 1
        assert targets[0].instance_id == child_identity.instance_id

    def test_specific_targets(self, transport_obj, child_identity):
        targets = transport_obj._resolve_targets([child_identity.instance_id])
        assert len(targets) == 1

    def test_unknown_target_filtered(self, transport_obj):
        targets = transport_obj._resolve_targets(["nonexistent"])
        assert len(targets) == 0


class TestPropagationRuntime:
    @pytest.mark.asyncio
    async def test_push_updates_live_propagation_state(self, root_identity, child_identity, topology, runtime_soul, soul_dir):
        transport = _BroadcastStubTransport({
            child_identity.instance_id: type("Result", (), {"success": True, "error": "", "latency_ms": 3})(),
        })
        propagation = SoulPropagationEngine(
            self_instance_id=root_identity.instance_id,
            peer_ids=[child_identity.instance_id],
        )
        transport_obj = SoulTransport(
            identity=root_identity,
            transport=transport,
            topology=topology,
            propagation_engine=propagation,
            current_soul_provider=lambda: runtime_soul,
            runtime_reload_callback=lambda soul: None,
            soul_dir=soul_dir,
        )

        result = await transport_obj.push_soul_update(
            soul_document=_valid_soul_dict("v2", mission="Updated"),
            soul_hash=hash_soul(Soul(**_valid_soul_dict("v2", mission="Updated"))),
            tier="T2",
            reason="autonomy update",
        )

        assert result["propagation"]["state"] == "completed"
        assert transport_obj.get_consistency_state() == "synchronized"
        assert transport_obj.get_active_propagations() == []
        assert transport.calls == [
            "/api/federation/pause",
            "/api/federation/soul/update",
            "/api/federation/resume",
        ]
        assert result["resume"][child_identity.instance_id]["success"] is True

    @pytest.mark.asyncio
    async def test_push_reports_governance_gaps_for_rejected_peer(self, root_identity, child_identity, topology, runtime_soul, soul_dir):
        result_map = {
            child_identity.instance_id: type(
                "Result",
                (),
                {
                    "success": False,
                    "error": "governance approval required",
                    "latency_ms": 3,
                    "body": {"accepted": False, "error": "governance approval required"},
                },
            )(),
        }
        propagation = SoulPropagationEngine(
            self_instance_id=root_identity.instance_id,
            peer_ids=[child_identity.instance_id],
        )
        transport_obj = SoulTransport(
            identity=root_identity,
            transport=_BroadcastStubTransport(result_map),
            topology=topology,
            propagation_engine=propagation,
            current_soul_provider=lambda: runtime_soul,
            runtime_reload_callback=lambda soul: None,
            soul_dir=soul_dir,
        )

        result = await transport_obj.push_soul_update(
            soul_document=_valid_soul_dict("v2", mission="Updated"),
            soul_hash=hash_soul(Soul(**_valid_soul_dict("v2", mission="Updated"))),
            tier="T2",
            reason="autonomy update",
        )

        assert result["handshake"]["all_acknowledged"] is False
        assert child_identity.instance_id in result["handshake"]["governance_gaps"]
        assert result["propagation"]["state"] == "failed"

    @pytest.mark.asyncio
    async def test_push_fails_when_resume_fails(self, root_identity, child_identity, topology, runtime_soul, soul_dir):
        class ResumeFailTransport:
            def __init__(self):
                self.calls = []

            async def broadcast(self, peers, **kwargs):
                self.calls.append(kwargs.get("path"))
                path = kwargs.get("path")
                if path == "/api/federation/resume":
                    return {
                        peer["instance_id"]: type(
                            "Result",
                            (),
                            {"success": False, "error": "resume engine unavailable", "latency_ms": 1, "body": {}},
                        )()
                        for peer in peers
                    }
                return {
                    peer["instance_id"]: type(
                        "Result",
                        (),
                        {"success": True, "error": "", "latency_ms": 1, "body": {"accepted": True}},
                    )()
                    for peer in peers
                }

        transport = ResumeFailTransport()
        propagation = SoulPropagationEngine(
            self_instance_id=root_identity.instance_id,
            peer_ids=[child_identity.instance_id],
        )
        transport_obj = SoulTransport(
            identity=root_identity,
            transport=transport,
            topology=topology,
            propagation_engine=propagation,
            current_soul_provider=lambda: runtime_soul,
            runtime_reload_callback=lambda soul: None,
            soul_dir=soul_dir,
        )

        result = await transport_obj.push_soul_update(
            soul_document=_valid_soul_dict("v2", mission="Updated"),
            soul_hash=hash_soul(Soul(**_valid_soul_dict("v2", mission="Updated"))),
            tier="T2",
            reason="autonomy update",
        )

        assert result["propagation"]["state"] == "failed"
        assert result["resume"][child_identity.instance_id]["success"] is False

    @pytest.mark.asyncio
    async def test_t2_push_aborts_when_pause_fails(
        self,
        root_identity,
        child_identity,
        topology,
        runtime_soul,
        soul_dir,
    ):
        transport = _BroadcastStubTransport({
            child_identity.instance_id: type(
                "Result",
                (),
                {
                    "success": False,
                    "error": "pause engine unavailable",
                    "latency_ms": 3,
                    "body": {"accepted": False, "error": "pause engine unavailable"},
                },
            )(),
        })
        propagation = SoulPropagationEngine(
            self_instance_id=root_identity.instance_id,
            peer_ids=[child_identity.instance_id],
        )
        transport_obj = SoulTransport(
            identity=root_identity,
            transport=transport,
            topology=topology,
            propagation_engine=propagation,
            current_soul_provider=lambda: runtime_soul,
            runtime_reload_callback=lambda soul: None,
            soul_dir=soul_dir,
        )

        result = await transport_obj.push_soul_update(
            soul_document=_valid_soul_dict("v2", mission="Updated"),
            soul_hash=hash_soul(Soul(**_valid_soul_dict("v2", mission="Updated"))),
            tier="T2",
            reason="autonomy update",
        )

        assert result["delivered"] == 0
        assert result["results"] == {}
        assert result["resume"] == {}
        assert result["handshake"]["all_acknowledged"] is False
        assert result["propagation"]["state"] == "failed"
        assert transport.calls == ["/api/federation/pause"]

    @pytest.mark.asyncio
    async def test_t3_push_stops_in_confirming_without_auto_resume(
        self,
        root_identity,
        child_identity,
        topology,
        runtime_soul,
        soul_dir,
    ):
        transport = _BroadcastStubTransport({
            child_identity.instance_id: type(
                "Result",
                (),
                {"success": True, "error": "", "latency_ms": 3, "body": {"accepted": True}},
            )(),
        })
        propagation = SoulPropagationEngine(
            self_instance_id=root_identity.instance_id,
            peer_ids=[child_identity.instance_id],
        )
        transport_obj = SoulTransport(
            identity=root_identity,
            transport=transport,
            topology=topology,
            propagation_engine=propagation,
            current_soul_provider=lambda: runtime_soul,
            runtime_reload_callback=lambda soul: None,
            soul_dir=soul_dir,
        )

        result = await transport_obj.push_soul_update(
            soul_document=_valid_soul_dict("v2", mission="Updated"),
            soul_hash=hash_soul(Soul(**_valid_soul_dict("v2", mission="Updated"))),
            tier="T3",
            reason="breaking change",
        )

        assert result["confirmation_required"] is True
        assert result["propagation"]["state"] == "confirming"
        assert result["confirmation_pending_instance_ids"] == [child_identity.instance_id]
        assert transport.calls == [
            "/api/federation/pause",
            "/api/federation/soul/update",
        ]
        assert transport.requests == []
        assert result["resume"] == {}

    @pytest.mark.asyncio
    async def test_t3_confirmation_resumes_after_all_peers_confirm(
        self,
        root_identity,
        child_identity,
        topology,
        runtime_soul,
        soul_dir,
    ):
        transport = _BroadcastStubTransport({
            child_identity.instance_id: type(
                "Result",
                (),
                {"success": True, "error": "", "latency_ms": 3, "body": {"accepted": True}},
            )(),
        })
        propagation = SoulPropagationEngine(
            self_instance_id=root_identity.instance_id,
            peer_ids=[child_identity.instance_id],
        )
        transport_obj = SoulTransport(
            identity=root_identity,
            transport=transport,
            topology=topology,
            propagation_engine=propagation,
            current_soul_provider=lambda: runtime_soul,
            runtime_reload_callback=lambda soul: None,
            soul_dir=soul_dir,
        )

        result = await transport_obj.push_soul_update(
            soul_document=_valid_soul_dict("v2", mission="Updated"),
            soul_hash=hash_soul(Soul(**_valid_soul_dict("v2", mission="Updated"))),
            tier="T3",
            reason="breaking change",
        )
        event_id = result["propagation"]["event_id"]

        confirm_result = await transport_obj.handle_soul_confirmation(
            {"event_id": event_id, "instance_id": child_identity.instance_id},
            authenticated_instance_id=child_identity.instance_id,
        )

        assert confirm_result["accepted"] is True
        assert confirm_result["all_confirmed"] is True
        assert confirm_result["propagation"]["state"] == "completed"
        assert confirm_result["resume"][child_identity.instance_id]["success"] is True
        assert transport.calls == [
            "/api/federation/pause",
            "/api/federation/soul/update",
            "/api/federation/resume",
        ]

    @pytest.mark.asyncio
    async def test_t3_confirmation_does_not_complete_when_resume_fails(
        self,
        root_identity,
        child_identity,
        topology,
        runtime_soul,
        soul_dir,
    ):
        class ResumeFailTransport:
            def __init__(self):
                self.calls = []

            async def broadcast(self, peers, **kwargs):
                self.calls.append(kwargs.get("path"))
                path = kwargs.get("path")
                if path == "/api/federation/resume":
                    return {
                        peer["instance_id"]: type(
                            "Result",
                            (),
                            {
                                "success": False,
                                "error": "resume engine unavailable",
                                "latency_ms": 1,
                                "body": {},
                            },
                        )()
                        for peer in peers
                    }
                return {
                    peer["instance_id"]: type(
                        "Result",
                        (),
                        {
                            "success": True,
                            "error": "",
                            "latency_ms": 1,
                            "body": {"accepted": True},
                        },
                    )()
                    for peer in peers
                }

        transport = ResumeFailTransport()
        propagation = SoulPropagationEngine(
            self_instance_id=root_identity.instance_id,
            peer_ids=[child_identity.instance_id],
        )
        transport_obj = SoulTransport(
            identity=root_identity,
            transport=transport,
            topology=topology,
            propagation_engine=propagation,
            current_soul_provider=lambda: runtime_soul,
            runtime_reload_callback=lambda soul: None,
            soul_dir=soul_dir,
        )

        result = await transport_obj.push_soul_update(
            soul_document=_valid_soul_dict("v2", mission="Updated"),
            soul_hash=hash_soul(Soul(**_valid_soul_dict("v2", mission="Updated"))),
            tier="T3",
            reason="breaking change",
        )
        event_id = result["propagation"]["event_id"]

        confirm_result = await transport_obj.handle_soul_confirmation(
            {"event_id": event_id, "instance_id": child_identity.instance_id},
            authenticated_instance_id=child_identity.instance_id,
        )

        assert confirm_result["accepted"] is True
        assert confirm_result["all_confirmed"] is True
        assert confirm_result["propagation"]["state"] == "failed"
        assert confirm_result["resume"][child_identity.instance_id]["success"] is False
        assert transport.calls == [
            "/api/federation/pause",
            "/api/federation/soul/update",
            "/api/federation/resume",
        ]
