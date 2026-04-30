from types import SimpleNamespace

from governance.models import RiskTier
from orchestrator_governance import (
    get_trust_summary,
    record_governance_event,
    seed_trust_records,
    suggest_alternatives,
)


class _TrustRecord:
    current_tier = RiskTier.T1_REVERSIBLE
    consecutive_successes = 7
    total_failures = 1


def test_get_trust_summary_reads_scope_specific_record():
    calls = []

    class Ledger:
        def get_record(self, skill_name, scope):
            calls.append((skill_name, scope))
            return _TrustRecord()

    summary = get_trust_summary(
        SimpleNamespace(trust_ledger=Ledger()),
        "repo_writer",
        {"path": "src/core/orchestrator.py"},
    )

    assert calls == [("repo_writer", "src/core/orchestrator.py")]
    assert summary == "Tier: T1_REVERSIBLE, 7 consecutive successes, 1 failures"


def test_seed_trust_records_registers_baseline_governance_capabilities():
    created = []

    class Ledger:
        def get_or_create_record(self, capability, scope, default_tier):
            created.append((capability, scope, default_tier))

    seed_trust_records(SimpleNamespace(trust_ledger=Ledger()))

    assert len(created) == 10
    assert ("fs.read", "workspace", RiskTier.T0_INERT) in created
    assert ("scheduler.create", "default", RiskTier.T2_CONTROLLED) in created
    assert ("skill.install", "marketplace", RiskTier.T3_IRREVERSIBLE) in created


def test_suggest_alternatives_returns_domain_specific_and_default_guidance():
    assert suggest_alternatives("network_client", {}) == [
        "Use GET to read-only fetch data first",
        "Use github_search for GitHub-specific queries",
        "Queue the write operation for Commander approval",
    ]

    assert suggest_alternatives("unknown_tool", {}) == [
        "Try a read-only approach to gather the needed information",
        "Break the operation into smaller, lower-risk steps",
        "Note the limitation and suggest the Commander approve via War Room",
    ]


def test_record_governance_event_updates_trust_and_decision_log():
    ledger_calls = []
    decision_calls = []

    class Ledger:
        def get_or_create_record(self, capability, scope, default_tier):
            ledger_calls.append(("ensure", capability, scope, default_tier))

        def record_success(self, capability, scope):
            ledger_calls.append(("success", capability, scope))

        def record_failure(self, capability, scope):
            ledger_calls.append(("failure", capability, scope))

    class DecisionLog:
        def record(self, context, decision, reason):
            decision_calls.append((context, decision, reason))

    runtime = SimpleNamespace(trust_ledger=Ledger(), decision_log=DecisionLog())

    record_governance_event(
        runtime,
        "connector.email.send",
        "myles@example.com",
        RiskTier.T2_CONTROLLED,
        True,
    )

    assert ledger_calls == [
        ("ensure", "connector.email.send", "myles@example.com", RiskTier.T2_CONTROLLED),
        ("success", "connector.email.send", "myles@example.com"),
    ]
    context, decision, reason = decision_calls[0]
    assert context.capability == "connector.email.send"
    assert context.target == "myles@example.com"
    assert int(context.risk_tier) == int(RiskTier.T2_CONTROLLED)
    assert decision == "approved"
    assert reason == "auto-execution"


def test_record_governance_event_uses_default_scope_and_failure_reason():
    ledger_calls = []
    decision_calls = []

    class Ledger:
        def get_or_create_record(self, capability, scope, default_tier):
            ledger_calls.append(("ensure", capability, scope, default_tier))

        def record_success(self, capability, scope):
            ledger_calls.append(("success", capability, scope))

        def record_failure(self, capability, scope):
            ledger_calls.append(("failure", capability, scope))

    class DecisionLog:
        def record(self, context, decision, reason):
            decision_calls.append((context, decision, reason))

    runtime = SimpleNamespace(trust_ledger=Ledger(), decision_log=DecisionLog())

    record_governance_event(runtime, "shell.exec", "", RiskTier.T2_CONTROLLED, False)

    assert ledger_calls == [
        ("ensure", "shell.exec", "default", RiskTier.T2_CONTROLLED),
        ("failure", "shell.exec", "default"),
    ]
    context, decision, reason = decision_calls[0]
    assert context.target == ""
    assert decision == "denied"
    assert reason == "execution-failed"
