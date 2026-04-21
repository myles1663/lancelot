from src.incidents.models import IncidentCategory, IncidentSeverity
from src.incidents.trigger_engine import (
    DEFAULT_TRIGGERS,
    TriggerCondition,
    TriggerEngine,
    TriggerRule,
    _FixedWindowCounter,
)


def test_fixed_window_counter_increments_resets_and_cleans_up(monkeypatch):
    counter = _FixedWindowCounter()
    times = iter([100.0, 105.0, 200.0, 5000.0])
    monkeypatch.setattr("src.incidents.trigger_engine.time.time", lambda: next(times))

    assert counter.increment("burst", "source-1", 60) == 1
    assert counter.increment("burst", "source-1", 60) == 2
    assert counter.increment("burst", "source-1", 60) == 1

    counter.reset("burst", "source-1")
    counter._counters[("stale", "source-2")] = {"window_start": 0.0, "count": 5}
    counter.cleanup(max_age_seconds=60)

    assert counter._counters == {}


def test_check_conditions_supports_metadata_json_and_operators():
    engine = TriggerEngine(triggers=[])
    receipt = {
        "metadata": '{"block_reason":"INJECTION_DETECTED","count":5,"ratio":0.25}',
        "top_level": "Arthur controls the bridge",
    }

    assert engine._check_conditions(
        TriggerRule(
            name="contains",
            receipt_types=["mcp_tool_blocked"],
            category=IncidentCategory.SECURITY_EVENT,
            severity=IncidentSeverity.HIGH,
            playbook="pb",
            conditions=[TriggerCondition(field="block_reason", value="INJECTION", operator="contains")],
        ),
        receipt,
    ) is True
    assert engine._check_conditions(
        TriggerRule(
            name="gt",
            receipt_types=["metric"],
            category=IncidentCategory.COST_ANOMALY,
            severity=IncidentSeverity.MEDIUM,
            playbook="pb",
            conditions=[TriggerCondition(field="count", value=3, operator="gt")],
        ),
        receipt,
    ) is True
    assert engine._check_conditions(
        TriggerRule(
            name="lt",
            receipt_types=["metric"],
            category=IncidentCategory.COST_ANOMALY,
            severity=IncidentSeverity.MEDIUM,
            playbook="pb",
            conditions=[TriggerCondition(field="ratio", value=1, operator="lt")],
        ),
        receipt,
    ) is True
    assert engine._check_conditions(
        TriggerRule(
            name="top-level",
            receipt_types=["note"],
            category=IncidentCategory.GOVERNANCE_BREACH,
            severity=IncidentSeverity.HIGH,
            playbook="pb",
            conditions=[TriggerCondition(field="top_level", value="Arthur", operator="contains")],
        ),
        receipt,
    ) is True
    assert engine._check_conditions(
        TriggerRule(
            name="missing",
            receipt_types=["note"],
            category=IncidentCategory.GOVERNANCE_BREACH,
            severity=IncidentSeverity.HIGH,
            playbook="pb",
            conditions=[TriggerCondition(field="unknown", value="Arthur")],
        ),
        receipt,
    ) is False


def test_extract_source_id_prefers_metadata_then_top_level_and_unknown():
    engine = TriggerEngine(triggers=[])
    trigger = TriggerRule(
        name="source",
        receipt_types=["kill_switch_issued"],
        category=IncidentCategory.GOVERNANCE_BREACH,
        severity=IncidentSeverity.HIGH,
        playbook="pb",
        dedup_source_field="switch_name",
    )

    assert engine._extract_source_id(trigger, {"metadata": '{"switch_name":"FEATURE_MCP"}'}) == "FEATURE_MCP"
    assert engine._extract_source_id(trigger, {"switch_name": "FEATURE_HIVE"}) == "FEATURE_HIVE"
    assert engine._extract_source_id(trigger, {"metadata": "not-json"}) == "unknown"
    assert engine._extract_source_id(
        TriggerRule(
            name="fallback",
            receipt_types=["receipt"],
            category=IncidentCategory.AVAILABILITY_INCIDENT,
            severity=IncidentSeverity.HIGH,
            playbook="pb",
            dedup_source_field=None,
        ),
        {"id": "receipt-1"},
    ) == "receipt-1"


def test_evaluate_returns_none_when_no_trigger_matches():
    engine = TriggerEngine(
        triggers=[
            TriggerRule(
                name="security",
                receipt_types=["mcp_tool_blocked"],
                conditions=[TriggerCondition(field="block_reason", value="INJECTION_DETECTED")],
                category=IncidentCategory.SECURITY_EVENT,
                severity=IncidentSeverity.CRITICAL,
                playbook="security-event-injection",
            )
        ]
    )

    assert engine.evaluate({"id": "r-1", "action_type": "kill_switch_issued"}) is None


def test_default_trigger_fires_and_sets_dedup_key():
    engine = TriggerEngine(triggers=list(DEFAULT_TRIGGERS))

    incident = engine.evaluate(
        {
            "id": "receipt-1",
            "action_type": "kill_switch_issued",
            "metadata": {"switch_name": "FEATURE_MCP"},
        }
    )

    assert incident is not None
    assert incident.category == IncidentCategory.GOVERNANCE_BREACH.value
    assert incident.severity == IncidentSeverity.HIGH.value
    assert incident.playbook_name == "governance-breach-kill-switch"
    assert incident.dedup_key == "kill_switch_activated:FEATURE_MCP"


def test_burst_trigger_waits_for_threshold_then_resets():
    engine = TriggerEngine(
        triggers=[
            TriggerRule(
                name="burst",
                receipt_types=["t3_rejected"],
                category=IncidentCategory.GOVERNANCE_BREACH,
                severity=IncidentSeverity.HIGH,
                playbook="governance-breach-t3-pattern",
                dedup_source_field="quest_id",
                burst_threshold=3,
                burst_window_seconds=900,
            )
        ]
    )

    receipt = {"id": "receipt-1", "action_type": "t3_rejected", "quest_id": "quest-1"}
    assert engine.evaluate(receipt) is None
    assert engine.evaluate(receipt) is None

    incident = engine.evaluate(receipt)
    assert incident is not None
    assert incident.dedup_key == "burst:quest-1"
    assert engine._counter._counters == {}


def test_default_burst_security_trigger_uses_metadata_condition_and_source():
    engine = TriggerEngine(triggers=list(DEFAULT_TRIGGERS))
    receipt = {
        "id": "receipt-inject-1",
        "action_type": "mcp_tool_blocked",
        "metadata": {
            "block_reason": "INJECTION_DETECTED",
            "source_id": "peer-7",
        },
    }

    incident = engine.evaluate(receipt)

    assert incident is not None
    assert incident.category == IncidentCategory.SECURITY_EVENT.value
    assert incident.severity == IncidentSeverity.CRITICAL.value
    assert incident.dedup_key == "injection_detected:peer-7"


def test_cleanup_counters_forwards_to_counter(monkeypatch):
    engine = TriggerEngine(triggers=[])
    called = []
    monkeypatch.setattr(engine._counter, "cleanup", lambda: called.append(True))

    engine.cleanup_counters()

    assert called == [True]
