import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.federation import api as federation_api


def _identity(instance_id="local-instance"):
    return SimpleNamespace(
        instance_id=instance_id,
        fingerprint="fingerprint",
        public_key_hex=lambda: "public-key",
        to_public_dict=lambda: {"instance_id": instance_id, "fingerprint": "fingerprint"},
    )


def _config(**overrides):
    dashboard = SimpleNamespace(
        enabled=True,
        poll_interval_s=10.0,
        stream_interval_s=3.0,
        max_recent_activity_items=50,
        card_sort_order="urgency",
        show_fleet_activity_feed=True,
        activity_feed_max_events=200,
    )
    base = {
        "self_address": "http://localhost:8000",
        "heartbeat_interval_s": 2.0,
        "tls_required": False,
        "staleness_warning_s": 10.0,
        "staleness_critical_s": 20.0,
        "staleness_lost_s": 30.0,
        "command_timeout_s": 5.0,
        "dashboard": dashboard,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def teardown_function():
    federation_api.shutdown_federation_api()


def test_empty_dashboard_snapshot_contains_no_demo_cluster_data():
    snapshot = federation_api._empty_dashboard_snapshot(
        enabled=False,
        disabled_reason="disabled for test",
    )
    serialized = str(snapshot).lower()

    assert snapshot["fleet"]["total_instances"] == 0
    assert snapshot["instances"] == []
    assert snapshot["activity"] == []
    assert "demo federation cluster" not in serialized
    assert "demo" not in serialized


def test_dashboard_labels_are_derived_from_live_identity_and_peers():
    peer = SimpleNamespace(
        instance_id="peer-instance-123456",
        address="https://peer.example:8443",
        metadata={},
    )
    topology = SimpleNamespace(list_peers=lambda: [peer])
    federation_api.init_federation_api(
        _identity("local-instance"),
        heartbeat_emitter=None,
        config=_config(),
        topology_registry=topology,
    )

    labels = federation_api._dashboard_instance_label_map()
    formatted = federation_api._format_dashboard_instance_list(
        ["local-instance", "peer-instance-123456", "unknown-instance-abcdef"]
    )

    assert labels == {
        "local-instance": "Local Lancelot",
        "peer-instance-123456": "peer.example",
    }
    assert formatted == "Local Lancelot, peer.example, Instance unknown-inst"
    assert "Demo" not in formatted


def test_dashboard_runtime_attention_formats_relevant_peer_reasons_only():
    peers = [
        SimpleNamespace(instance_id="peer-a", address="http://peer-a.local", metadata={"instance_name": "Ops Node"}),
    ]
    topology = SimpleNamespace(list_peers=lambda: peers)
    federation_api.init_federation_api(
        _identity("local-instance"),
        heartbeat_emitter=None,
        config=_config(),
        topology_registry=topology,
    )

    reasons = federation_api._dashboard_runtime_attention_reasons(
        {
            "degraded_reasons": [
                "Federation cost data stale for peer(s): peer-a, old-demo-peer",
                "Federation heartbeat stream failed for peer(s): peer-a",
                "Federation transport not started",
                "Federation transport not started",
            ]
        }
    )

    assert reasons == [
        "Cost telemetry stale for Ops Node",
        "Heartbeat stream failed for Ops Node",
        "Federation transport not started",
    ]
    assert "old-demo-peer" not in str(reasons)


@pytest.mark.parametrize(
    ("budget_pct", "threshold"),
    [
        (101.0, "hard_stop"),
        (96.0, "spawn_gated"),
        (86.0, "spawn_restricted"),
        (76.0, "warning"),
        (12.0, "normal"),
    ],
)
def test_budget_threshold_for_pct_boundaries(budget_pct, threshold):
    assert federation_api._budget_threshold_for_pct(budget_pct) == threshold


def test_derive_attention_state_prioritizes_paused_critical_attention_and_healthy():
    paused, paused_reasons = federation_api._derive_attention_state({"paused": True})
    critical, critical_reasons = federation_api._derive_attention_state(
        {"heartbeat_state": "lost", "runtime_errors": ["boom"]}
    )
    attention, attention_reasons = federation_api._derive_attention_state(
        {"pending_approvals": 2, "trust_proposals": 1}
    )
    healthy, healthy_reasons = federation_api._derive_attention_state({})

    assert paused == "paused"
    assert "Runtime paused" in paused_reasons
    assert critical == "critical"
    assert "Heartbeat lost" in critical_reasons
    assert attention == "attention"
    assert "2 pending approval(s)" in attention_reasons
    assert healthy == "healthy"
    assert healthy_reasons == []


def test_normalize_remote_rows_ignores_invalid_rows_and_fills_identity():
    rows = federation_api._normalize_remote_rows(
        [{"id": "approval-1"}, "bad", {"id": "approval-2", "instance_name": "Given"}],
        instance_id="peer-1",
        instance_name="Peer One",
    )

    assert rows == [
        {"id": "approval-1", "instance_id": "peer-1", "instance_name": "Peer One"},
        {"id": "approval-2", "instance_name": "Given", "instance_id": "peer-1"},
    ]


def test_sort_dashboard_instances_uses_configured_order():
    dashboard = SimpleNamespace(
        enabled=True,
        poll_interval_s=10,
        stream_interval_s=3,
        max_recent_activity_items=50,
        card_sort_order="alphabetical",
        show_fleet_activity_feed=True,
        activity_feed_max_events=200,
    )
    federation_api.init_federation_api(
        _identity(),
        heartbeat_emitter=None,
        config=_config(dashboard=dashboard),
    )
    instances = [
        {"name": "Zulu", "state": "healthy"},
        {"name": "Alpha", "state": "critical"},
    ]

    assert [item["name"] for item in federation_api._sort_dashboard_instances(instances)] == [
        "Alpha",
        "Zulu",
    ]

    dashboard.card_sort_order = "urgency"
    instances[0]["pending_approvals"] = 5
    instances[1]["pending_approvals"] = 0
    assert [item["name"] for item in federation_api._sort_dashboard_instances(instances)] == [
        "Alpha",
        "Zulu",
    ]


def test_build_fleet_summary_counts_only_supplied_live_instances():
    summary = federation_api._build_fleet_summary(
        [
            {
                "state": "critical",
                "heartbeat_state": "lost",
                "pending_approvals": 2,
                "trust_proposals": 1,
                "active_agents": 3,
            },
            {
                "state": "healthy",
                "heartbeat_state": "fresh",
                "pending_approvals": 0,
                "trust_proposals": 0,
                "active_agents": 1,
            },
        ],
        aggregate_cost={"utilization_pct": 81.5, "threshold": "warning"},
        runtime_status={"soul_consistency": "consistent"},
    )

    assert summary["total_instances"] == 2
    assert summary["critical_instances"] == 1
    assert summary["lost_instances"] == 1
    assert summary["pending_approvals"] == 2
    assert summary["active_agents"] == 4
    assert summary["fleet_cost_utilization_pct"] == 81.5


def test_dashboard_disabled_reason_uses_runtime_flags(monkeypatch):
    flags = SimpleNamespace(FEATURE_FEDERATION=False, FEATURE_FEDERATION_DASHBOARD=True)
    monkeypatch.setitem(sys.modules, "feature_flags", flags)

    assert federation_api._dashboard_disabled_reason() == "FEATURE_FEDERATION is disabled"

    flags.FEATURE_FEDERATION = True
    flags.FEATURE_FEDERATION_DASHBOARD = False
    assert federation_api._dashboard_disabled_reason() == "FEATURE_FEDERATION_DASHBOARD is disabled"

    flags.FEATURE_FEDERATION_DASHBOARD = True
    federation_api.init_federation_api(
        _identity(),
        heartbeat_emitter=None,
        config=_config(dashboard=SimpleNamespace(enabled=False)),
    )
    assert federation_api._dashboard_disabled_reason() == "Federation dashboard is disabled in config/federation.yaml"


def test_build_runtime_status_filters_stale_cost_to_current_peers():
    peer = SimpleNamespace(instance_id="peer-a")
    topology = SimpleNamespace(list_peers=lambda: [peer])
    transport = SimpleNamespace(
        started=False,
        get_circuit_breaker_states=lambda: {"peer-a": {"state": "open"}},
    )
    heartbeat_mesh = SimpleNamespace(
        running=False,
        get_subscription_status=lambda: {"peer-a": "connected"},
        get_stream_outcome_status=lambda: {"peer-a": "failed", "old-peer": "failed"},
        get_stream_errors=lambda: {"peer-a": "timeout"},
        divergence_evaluation_failed=True,
        divergence_status_error="diverged",
    )
    cost_reporter = SimpleNamespace(
        running=False,
        get_aggregate_status=lambda: {
            "threshold": "warning",
            "stale_instance_ids": ["peer-a", "old-peer"],
        },
    )
    soul_transport = SimpleNamespace(
        get_consistency_state=lambda: "consistent",
        get_active_propagations=lambda: [{"id": "prop-1"}],
        get_local_soul_hash=lambda: "soul-hash",
    )
    divergence_detector = SimpleNamespace(
        state=SimpleNamespace(value="diverged"),
        get_divergence_duration_s=lambda: 12.5,
        last_reconciliation=SimpleNamespace(to_dict=lambda: {"status": "pending"}),
    )
    federation_api.init_federation_api(
        _identity("local-instance"),
        heartbeat_emitter=None,
        config=_config(),
        topology_registry=topology,
        divergence_detector=divergence_detector,
    )
    federation_api.init_federation_transport(
        transport=transport,
        heartbeat_mesh=heartbeat_mesh,
        cost_reporter=cost_reporter,
        soul_transport=soul_transport,
    )

    status = federation_api._build_runtime_status()

    assert status["runtime_degraded"] is True
    assert status["transport_started"] is False
    assert status["circuit_breaker_summary"]["open"] == 1
    assert status["stale_instance_ids"] == ["peer-a"]
    assert status["soul_consistency"] == "consistent"
    assert status["divergence_state"] == "diverged"
    assert status["reconciliation"] == {"status": "pending"}


def test_clean_decision_reason_and_payload_identity_validation():
    assert federation_api._clean_decision_reason("  ship it ") == "ship it"
    with pytest.raises(HTTPException) as missing_reason:
        federation_api._clean_decision_reason("  ")
    with pytest.raises(HTTPException) as bad_identity:
        federation_api._operator_identity_from_payload({"operator_id": ""})

    assert missing_reason.value.status_code == 400
    assert bad_identity.value.status_code == 401
