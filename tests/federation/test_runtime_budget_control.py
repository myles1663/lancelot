from src.core.runtime_pause import get_runtime_pause_status, init_runtime_pause
from src.federation.cost_aggregation import CostThreshold, FederatedCostAggregator, InstanceCostData
from src.federation.runtime_budget_control import handle_federation_cost_threshold_change


class _ReceiptManager:
    def __init__(self):
        self.calls = []

    def record_budget_threshold(self, **kwargs):
        self.calls.append(kwargs)


class _AuditEngine:
    def __init__(self):
        self.calls = []

    def record(self, **kwargs):
        self.calls.append(kwargs)


def test_hard_stop_pauses_runtime_without_auto_resume(tmp_path):
    init_runtime_pause(str(tmp_path))
    aggregator = FederatedCostAggregator()
    aggregator.update_instance(
        InstanceCostData(
            instance_id="self",
            actual_today_usd=10.0,
            projected_today_usd=10.0,
            daily_ceiling_usd=10.0,
        )
    )
    receipts = _ReceiptManager()
    audit = _AuditEngine()
    identity = type("Identity", (), {"instance_id": "self"})()

    handle_federation_cost_threshold_change(
        CostThreshold.NORMAL,
        CostThreshold.HARD_STOP,
        cost_aggregator=aggregator,
        receipt_mgr=receipts,
        audit_engine=audit,
        identity=identity,
        soul_hash_provider=lambda: "hash-1",
    )

    paused = get_runtime_pause_status()
    assert paused["paused"] is True
    assert paused["source"] == "federation_cost_hard_stop"
    assert "hard stop" in (paused["reason"] or "").lower()
    assert receipts.calls[-1]["action_taken"] == "pause_all_activity"
    assert audit.calls[-1]["details"]["new_threshold"] == "hard_stop"

    handle_federation_cost_threshold_change(
        CostThreshold.HARD_STOP,
        CostThreshold.WARNING,
        cost_aggregator=aggregator,
        receipt_mgr=receipts,
        audit_engine=audit,
        identity=identity,
        soul_hash_provider=lambda: "hash-1",
    )

    still_paused = get_runtime_pause_status()
    assert still_paused["paused"] is True


def test_hard_stop_passes_source_and_full_stop_to_pause_handler():
    aggregator = FederatedCostAggregator()
    aggregator.update_instance(
        InstanceCostData(
            instance_id="self",
            actual_today_usd=10.0,
            projected_today_usd=10.0,
            daily_ceiling_usd=10.0,
        )
    )
    calls = []

    def pause_handler(reason, *, full_stop=False, source="warroom"):
        calls.append({
            "reason": reason,
            "full_stop": full_stop,
            "source": source,
        })
        return {"paused": True}

    handle_federation_cost_threshold_change(
        CostThreshold.NORMAL,
        CostThreshold.HARD_STOP,
        cost_aggregator=aggregator,
        pause_runtime_fn=pause_handler,
        is_runtime_paused_fn=lambda: False,
    )

    assert calls == [{
        "reason": "Federation cost hard stop reached at 100.0% utilization",
        "full_stop": True,
        "source": "federation_cost_hard_stop",
    }]
