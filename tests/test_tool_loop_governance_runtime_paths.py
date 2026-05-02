from enum import Enum
from types import SimpleNamespace

import pytest

import tool_loop


class RiskTier(Enum):
    T0_INERT = 0
    T1_REVERSIBLE = 1
    T2_CONTROLLED = 2
    T3_IRREVERSIBLE = 3


class Step:
    def __init__(self, step_id, tool, description, **params):
        self.id = step_id
        self.tool = tool
        self.description = description
        self.params = [SimpleNamespace(key=key, value=value) for key, value in params.items()]


class Runtime:
    def __init__(self, tiers):
        self.data_dir = "/tmp/lancelot-test"
        self.wake_reasons = []
        self.executed = []
        self.governance_events = []
        self.approval_allowed = True
        self._risk_classifier = SimpleNamespace(
            classify=lambda capability, target="": SimpleNamespace(tier=tiers.pop(0))
        )
        self._policy_cache = SimpleNamespace(lookup=lambda capability, target: None)
        self._rollback_manager = None
        self._async_queue = None
        self.verifier = SimpleNamespace(
            verify_step=lambda description, output: SimpleNamespace(
                success=True,
                reason="verified",
                correction_suggestion="",
            )
        )

    def wake_up(self, reason):
        self.wake_reasons.append(reason)

    def _execute_step_tool(self, step, params):
        self.executed.append((step.id, step.tool, params))
        return f"ok:{step.id}"

    def _record_governance_event(self, *args):
        self.governance_events.append(args)

    def _request_approval(self, step, profile):
        return self.approval_allowed


class AsyncQueue:
    def __init__(self, *, failed=0, depth=0):
        self.failed = failed
        self.depth = depth
        self.submitted = []
        self.cleared = 0

    def submit(self, job):
        self.submitted.append(job)

    def drain(self):
        return SimpleNamespace(
            failed=self.failed,
            passed=max(0, 2 - self.failed),
            drained_count=2,
        )

    def clear_results(self):
        self.cleared += 1


@pytest.fixture(autouse=True)
def governance_globals(monkeypatch):
    monkeypatch.setattr(tool_loop, "_GOVERNANCE_AVAILABLE", True, raising=False)
    monkeypatch.setattr(tool_loop, "RiskTier", RiskTier, raising=False)
    monkeypatch.setattr(tool_loop, "VerificationJob", lambda **kwargs: kwargs, raising=False)
    monkeypatch.setattr(tool_loop, "_TOOL_CAPABILITY_MAP", {"repo_writer": "filesystem.write"}, raising=False)
    monkeypatch.setattr(tool_loop._ff, "FEATURE_RISK_TIERED_GOVERNANCE", True, raising=False)
    monkeypatch.setattr(tool_loop._ff, "FEATURE_BATCH_RECEIPTS", False, raising=False)
    monkeypatch.setattr(tool_loop._ff, "FEATURE_POLICY_CACHE", True, raising=False)
    monkeypatch.setattr(tool_loop._ff, "FEATURE_ASYNC_VERIFICATION", True, raising=False)


def test_execute_plan_runs_each_governance_tier_and_records_boundaries():
    runtime = Runtime(
        [
            RiskTier.T0_INERT,
            RiskTier.T1_REVERSIBLE,
            RiskTier.T2_CONTROLLED,
            RiskTier.T3_IRREVERSIBLE,
        ]
    )
    runtime._rollback_manager = SimpleNamespace(
        create_snapshot=lambda **kwargs: SimpleNamespace(snapshot_id=f"snap-{kwargs['step_index']}"),
        get_rollback_action=lambda snapshot_id: (lambda: None),
    )
    runtime._async_queue = AsyncQueue(depth=1)
    plan = SimpleNamespace(
        plan_id="plan-1",
        steps=[
            Step("s0", "read_context", "inspect context", path="README.md"),
            Step("s1", "repo_writer", "edit a config", path="config.yaml"),
            Step("s2", "command_runner", "run controlled command", dir="."),
            Step("s3", "repo_writer", "write irreversible migration", path="migration.sql"),
        ],
    )

    result = tool_loop.execute_plan(runtime, plan)

    assert result.startswith("Plan Executed Successfully.")
    assert "Step s0: T0 executed" in result
    assert "Step s1: T1 async-queued" in result
    assert "Step s2: T2 sync-verified True" in result
    assert "Step s3: T3 sync-verified True" in result
    assert runtime._async_queue.submitted[0]["task_id"] == "plan-1"
    assert runtime._async_queue.cleared == 3
    assert [event[0] for event in runtime.governance_events] == [
        "read_context",
        "command_runner",
        "filesystem.write",
    ]


def test_execute_plan_blocks_policy_cache_denial_before_tool_execution():
    runtime = Runtime([RiskTier.T0_INERT])
    runtime._policy_cache = SimpleNamespace(
        lookup=lambda capability, target: SimpleNamespace(decision="deny")
    )
    plan = SimpleNamespace(plan_id="plan-1", steps=[Step("s0", "repo_writer", "write", path="secret.txt")])

    result = tool_loop.execute_plan(runtime, plan)

    assert result == "Plan Blocked at Step s0: Policy denied filesystem.write"
    assert runtime.executed == []


def test_execute_plan_blocks_when_prior_async_verification_failed_before_t2():
    runtime = Runtime([RiskTier.T2_CONTROLLED])
    runtime._async_queue = AsyncQueue(failed=1, depth=0)
    plan = SimpleNamespace(plan_id="plan-1", steps=[Step("s2", "command_runner", "deploy", dir=".")])

    result = tool_loop.execute_plan(runtime, plan)

    assert "prior T1 verification failures detected before T2 step s2" in result
    assert runtime.executed == []
    assert runtime._async_queue.cleared == 1


def test_execute_plan_stops_when_t3_approval_is_denied():
    runtime = Runtime([RiskTier.T3_IRREVERSIBLE])
    runtime.approval_allowed = False
    runtime._async_queue = AsyncQueue(depth=0)
    plan = SimpleNamespace(plan_id="plan-1", steps=[Step("s3", "repo_writer", "drop data", path="db")])

    result = tool_loop.execute_plan(runtime, plan)

    assert result == "Plan Stopped at Step s3: Commander approval denied for filesystem.write"
    assert runtime.executed == []


def test_execute_plan_legacy_path_returns_verifier_failure(monkeypatch):
    runtime = Runtime([])
    runtime._risk_classifier = None
    runtime.verifier = SimpleNamespace(
        verify_step=lambda description, output: SimpleNamespace(
            success=False,
            reason="assertion failed",
            correction_suggestion="retry with safer command",
        )
    )
    monkeypatch.setattr(tool_loop._ff, "FEATURE_RISK_TIERED_GOVERNANCE", False, raising=False)
    plan = SimpleNamespace(plan_id="plan-1", steps=[Step("legacy", "command_runner", "run check")])

    result = tool_loop.execute_plan(runtime, plan)

    assert result == (
        "Plan Failed at Step legacy.\n"
        "Reason: assertion failed\n"
        "Suggestion: retry with safer command"
    )
    assert runtime.governance_events == [("command_runner", "", 0, False)]


def test_execute_plan_rolls_back_failed_t1_sync_verification(monkeypatch):
    runtime = Runtime([RiskTier.T1_REVERSIBLE])
    rolled_back = []
    runtime._rollback_manager = SimpleNamespace(
        create_snapshot=lambda **kwargs: SimpleNamespace(snapshot_id="snap-1"),
        get_rollback_action=lambda snapshot_id: (lambda: rolled_back.append(snapshot_id)),
    )
    runtime._async_queue = None
    runtime.verifier = SimpleNamespace(
        verify_step=lambda description, output: SimpleNamespace(
            success=False,
            reason="changed wrong file",
            correction_suggestion="",
        )
    )
    monkeypatch.setattr(tool_loop._ff, "FEATURE_ASYNC_VERIFICATION", False, raising=False)
    plan = SimpleNamespace(plan_id="plan-1", steps=[Step("s1", "repo_writer", "edit", path="app.py")])

    result = tool_loop.execute_plan(runtime, plan)

    assert result == "Plan Failed at Step s1.\nReason: changed wrong file"
    assert rolled_back == ["snap-1"]
