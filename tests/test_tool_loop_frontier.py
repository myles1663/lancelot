import types

from governance.models import RiskTier
from providers.base import ToolCall
from tool_loop_frontier import process_frontier_tool_calls


class _Governor:
    def __init__(self):
        self.logged = []

    def log_usage(self, *args):
        self.logged.append(args)


class _Runtime:
    def __init__(self):
        self.toolflow_emitter = None
        self.sentry = None
        self.actioncard_factory = None
        self.receipt_service = None
        self.governor = _Governor()
        self.governance_events = []
        self.skill_executor = types.SimpleNamespace(
            run=lambda name, inputs: types.SimpleNamespace(
                success=True,
                outputs={"body": "ok"},
                error="",
            )
        )

    def _classify_tool_call_safety(self, *_):
        return "auto"

    def _record_governance_event(self, *args):
        self.governance_events.append(args)


def test_process_frontier_tool_calls_executes_declared_tool_and_records_governance():
    runtime = _Runtime()
    receipts = []
    result = types.SimpleNamespace(
        tool_calls=[
            ToolCall(
                name="network_client",
                args={"method": "GET", "url": "https://example.invalid"},
                id="call-1",
            )
        ]
    )

    batch = process_frontier_tool_calls(
        runtime,
        prompt="fetch",
        result=result,
        declarations=[types.SimpleNamespace(name="network_client")],
        tool_receipts=receipts,
        find_successful_tool_receipt=lambda *_: None,
        allow_writes=True,
        iteration=0,
        quest_id="quest-1",
        channel="api",
        agentic_start_ms=0,
    )

    assert batch.pending_response is None
    assert batch.tool_results[0][0] == "call-1"
    assert batch.tool_results[0][1] == "network_client"
    assert receipts[0]["result"] == "SUCCESS"
    assert runtime.governor.logged == [("tool_calls", 1)]
    assert runtime.governance_events == [
        ("network_client", "https://example.invalid", RiskTier.T2_CONTROLLED, True)
    ]


def test_process_frontier_tool_calls_rejects_undeclared_tool_before_execution():
    runtime = _Runtime()
    runtime.skill_executor = types.SimpleNamespace(
        run=lambda *_: (_ for _ in ()).throw(AssertionError("tool should not run"))
    )
    receipts = []
    result = types.SimpleNamespace(
        tool_calls=[
            ToolCall(name="made_up_tool", args={}, id="call-1")
        ]
    )

    batch = process_frontier_tool_calls(
        runtime,
        prompt="use tool",
        result=result,
        declarations=[types.SimpleNamespace(name="network_client")],
        tool_receipts=receipts,
        find_successful_tool_receipt=lambda *_: None,
        allow_writes=True,
        iteration=0,
        quest_id="quest-1",
        channel="api",
        agentic_start_ms=0,
    )

    assert batch.pending_response is None
    assert batch.tool_results[0][1] == "made_up_tool"
    assert receipts[0]["result"].startswith("REJECTED")
    assert runtime.governor.logged == []
