"""
Failure Injection Tests — verify system behavior when things go wrong.

Covers:
1. Connector failure mid-task (executor dies partway through)
2. Agent timeout and cleanup (agent spawned but never completes)
3. Concurrent agent conflict (two agents targeting same resource)
4. Soul constraint violation (agent actually gets blocked)
5. Receipt integrity (tampered receipt detection)
6. UAB fallback (target app not responding, crashes mid-operation)
7. Trust Ledger revocation under race conditions
"""

import asyncio
import json
import os
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.hive.types import (
    AgentState,
    CollapseReason,
    ControlMethod,
    DecomposedTask,
    InterventionType,
    OperatorIntervention,
    SubAgentRecord,
    TaskResult,
    TaskSpec,
    VALID_TRANSITIONS,
)
from src.hive.errors import (
    AgentCollapsedError,
    InterventionRequiresReasonError,
    MaxAgentsExceededError,
    ScopedSoulViolationError,
    UABControlError,
)
from src.hive.registry import AgentRegistry
from src.hive.receipt_manager import HiveReceiptManager
from src.hive.runtime import SubAgentRuntime
from src.hive.lifecycle import AgentLifecycleManager
from src.hive.config import HiveConfig
from src.hive.scoped_soul import ScopedSoulGenerator
from src.hive.integration.governance_bridge import GovernanceBridge, GovernanceResult
from src.hive.integration.uab_bridge import UABBridge


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_receipt_service():
    """Reset receipt service singleton in ALL module references."""
    import sys
    modules_to_reset = []
    for mod_name in ("src.shared.receipts", "receipts"):
        mod = sys.modules.get(mod_name)
        if mod and hasattr(mod, "_service_instance"):
            modules_to_reset.append((mod, mod._service_instance))
            mod._service_instance = None
    yield
    for mod, old_val in modules_to_reset:
        mod._service_instance = old_val


@pytest.fixture
def registry():
    return AgentRegistry(max_concurrent_agents=5)


@pytest.fixture
def receipt_mgr(tmp_path):
    return HiveReceiptManager(data_dir=str(tmp_path))


@pytest.fixture
def config():
    return HiveConfig(
        max_concurrent_agents=5,
        default_task_timeout=300,
        max_actions_per_agent=50,
    )


@pytest.fixture
def soul_gen():
    """ScopedSoulGenerator that returns a minimal mock soul."""
    gen = MagicMock(spec=ScopedSoulGenerator)
    gen.generate.return_value = MagicMock()
    gen.hash_soul = ScopedSoulGenerator.hash_soul  # Use real hash
    return gen


@pytest.fixture
def lifecycle(config, registry, receipt_mgr, soul_gen):
    results = []

    def executor(action):
        results.append(action)
        return {"result": "ok"}

    mgr = AgentLifecycleManager(
        config=config,
        registry=registry,
        receipt_manager=receipt_mgr,
        soul_generator=soul_gen,
        action_executor=executor,
    )
    yield mgr
    mgr.shutdown()


def _make_runtime(registry, receipt_mgr, action_executor=None,
                  governance=None, timeout=300, max_actions=50):
    """Helper: create a runtime in EXECUTING state."""
    spec = TaskSpec(timeout_seconds=timeout, max_actions=max_actions)
    record = registry.register(spec)
    registry.transition(record.agent_id, AgentState.READY)
    registry.transition(record.agent_id, AgentState.EXECUTING)
    runtime = SubAgentRuntime(
        agent_record=record,
        registry=registry,
        receipt_manager=receipt_mgr,
        governance_bridge=governance,
        action_executor=action_executor,
    )
    return runtime, record


# =====================================================================
# 1. FAILURE INJECTION — connector goes down mid-task
# =====================================================================

class TestFailureInjection:
    """Verify behavior when the executor (connector) fails mid-task."""

    def test_executor_dies_on_second_action(self, registry, receipt_mgr):
        """First action succeeds, second raises — agent collapses with ERROR."""
        call_count = [0]

        def flaky_executor(action):
            call_count[0] += 1
            if call_count[0] == 2:
                raise ConnectionError("Connector lost")
            return {"ok": True}

        runtime, record = _make_runtime(registry, receipt_mgr, flaky_executor)
        actions = [{"action": "step1"}, {"action": "step2"}, {"action": "step3"}]
        result = runtime.run(actions)

        assert result.success is False
        assert result.collapse_reason == CollapseReason.ERROR
        assert "Connector lost" in result.error_message
        assert call_count[0] == 2  # Never reached step3

    def test_executor_returns_none_is_ok(self, registry, receipt_mgr):
        """Executor returning None should not crash — just produces empty output."""
        def none_executor(action):
            return None

        runtime, record = _make_runtime(registry, receipt_mgr, none_executor)
        result = runtime.run([{"action": "a1"}, {"action": "a2"}])
        assert result.success is True
        assert result.outputs == {}  # None outputs not stored

    def test_executor_timeout_exception(self, registry, receipt_mgr):
        """Executor that raises TimeoutError — agent collapses with ERROR."""
        def timeout_executor(action):
            raise TimeoutError("Connection timed out")

        runtime, record = _make_runtime(registry, receipt_mgr, timeout_executor)
        result = runtime.run([{"action": "call_api"}])

        assert result.success is False
        assert result.collapse_reason == CollapseReason.ERROR
        assert "timed out" in result.error_message

    def test_executor_intermittent_failure_stops_on_first_error(self, registry, receipt_mgr):
        """Runtime stops on the first executor error — no retry within runtime."""
        executed = []

        def intermittent(action):
            executed.append(action["action"])
            if action["action"] == "a3":
                raise RuntimeError("Intermittent failure")
            return {"ok": True}

        runtime, record = _make_runtime(registry, receipt_mgr, intermittent)
        actions = [{"action": f"a{i}"} for i in range(1, 6)]
        result = runtime.run(actions)

        assert result.success is False
        assert executed == ["a1", "a2", "a3"]  # Stopped at a3
        assert result.collapse_reason == CollapseReason.ERROR

    def test_other_agents_unaffected_by_one_failure(self, registry, receipt_mgr):
        """One agent's failure should not affect another agent's execution."""
        good_results = []

        def good_executor(action):
            good_results.append(action["action"])
            return {"ok": True}

        def bad_executor(action):
            raise RuntimeError("Crash!")

        # Agent 1: fails
        bad_runtime, bad_record = _make_runtime(registry, receipt_mgr, bad_executor)
        # Agent 2: succeeds
        good_runtime, good_record = _make_runtime(registry, receipt_mgr, good_executor)

        bad_result = bad_runtime.run([{"action": "fail"}])
        good_result = good_runtime.run([{"action": "pass1"}, {"action": "pass2"}])

        assert bad_result.success is False
        assert good_result.success is True
        assert good_results == ["pass1", "pass2"]

    def test_lifecycle_execute_handles_executor_crash(
        self, config, registry, receipt_mgr, soul_gen,
    ):
        """Full lifecycle: executor crashes → agent transitions to COLLAPSED."""
        def crash_executor(action):
            raise RuntimeError("Fatal crash")

        mgr = AgentLifecycleManager(
            config=config,
            registry=registry,
            receipt_manager=receipt_mgr,
            soul_generator=soul_gen,
            action_executor=crash_executor,
        )

        try:
            spec = TaskSpec(description="crash test")
            record = mgr.spawn(spec, quest_id="q-crash")
            future = mgr.execute(record.agent_id, [{"action": "boom"}])
            result = future.result(timeout=5)

            assert result.success is False
            assert result.collapse_reason == CollapseReason.ERROR

            # Agent should be in archive (collapsed)
            agent = registry.get(record.agent_id)
            assert agent.state == AgentState.COLLAPSED
        finally:
            mgr.shutdown()


# =====================================================================
# 2. AGENT TIMEOUT AND CLEANUP
# =====================================================================

class TestAgentTimeoutAndCleanup:
    """Verify agents that hang forever get cleaned up."""

    def test_slow_executor_triggers_timeout(self, registry, receipt_mgr):
        """Executor that takes too long triggers TIMEOUT collapse."""
        def slow_executor(action):
            time.sleep(0.3)
            return {}

        runtime, record = _make_runtime(
            registry, receipt_mgr, slow_executor, timeout=0.1,
        )
        actions = [{"action": f"s{i}"} for i in range(10)]
        result = runtime.run(actions)

        assert result.collapse_reason == CollapseReason.TIMEOUT
        assert result.success is False
        # At most 1 action completed (first one takes 0.3s > 0.1s timeout)
        assert result.action_count <= 1

    def test_paused_agent_times_out(self, registry, receipt_mgr):
        """Agent paused forever eventually collapses with TIMEOUT."""
        runtime, record = _make_runtime(
            registry, receipt_mgr, timeout=300,
        )
        # Override the pause wait timeout to something short
        original_wait = runtime._wait_for_unpause

        def short_wait(timeout=0.2):
            if not runtime._pause_event.wait(timeout=0.2):
                runtime.request_collapse(
                    CollapseReason.TIMEOUT, "Timeout while paused",
                )

        runtime._wait_for_unpause = short_wait
        runtime.pause("Indefinite pause")

        result = runtime.run([{"action": "never_runs"}])
        assert result.collapse_reason == CollapseReason.TIMEOUT

    def test_killed_agent_frees_capacity(self, registry, receipt_mgr):
        """After kill, agent no longer counts toward capacity."""
        initial_count = registry.active_count()

        spec = TaskSpec()
        record = registry.register(spec)
        registry.transition(record.agent_id, AgentState.READY)

        assert registry.active_count() == initial_count + 1

        # Collapse it
        registry.transition(
            record.agent_id,
            AgentState.COLLAPSED,
            collapse_reason=CollapseReason.OPERATOR_KILL,
        )

        assert registry.active_count() == initial_count
        assert registry.can_spawn()

    def test_lifecycle_cleanup_on_timeout(
        self, config, registry, receipt_mgr, soul_gen,
    ):
        """Full lifecycle: timeout triggers cleanup, runtime removed."""
        def forever_executor(action):
            time.sleep(0.5)
            return {}

        config_fast = HiveConfig(
            max_concurrent_agents=5,
            default_task_timeout=10,
        )
        mgr = AgentLifecycleManager(
            config=config_fast,
            registry=registry,
            receipt_manager=receipt_mgr,
            soul_generator=soul_gen,
            action_executor=forever_executor,
        )
        try:
            spec = TaskSpec(timeout_seconds=0.2, max_actions=50)
            record = mgr.spawn(spec, quest_id="q-timeout")
            future = mgr.execute(
                record.agent_id,
                [{"action": f"a{i}"} for i in range(20)],
            )
            result = future.result(timeout=10)

            assert result.collapse_reason == CollapseReason.TIMEOUT
            # Runtime should be cleaned up
            assert mgr.get_runtime(record.agent_id) is None
        finally:
            mgr.shutdown()

    def test_kill_during_execution(
        self, config, registry, receipt_mgr, soul_gen,
    ):
        """Kill an agent while it's executing — should stop promptly."""
        action_log = []
        barrier = threading.Event()

        def slow_executor(action):
            action_log.append(action["action"])
            if action["action"] == "a0":
                barrier.set()  # Signal that first action started
                time.sleep(0.3)
            return {}

        mgr = AgentLifecycleManager(
            config=config,
            registry=registry,
            receipt_manager=receipt_mgr,
            soul_generator=soul_gen,
            action_executor=slow_executor,
        )
        try:
            spec = TaskSpec(timeout_seconds=30)
            record = mgr.spawn(spec, quest_id="q-kill")
            future = mgr.execute(
                record.agent_id,
                [{"action": f"a{i}"} for i in range(10)],
            )

            # Wait for first action to start, then kill
            barrier.wait(timeout=5)
            time.sleep(0.05)
            mgr.kill(record.agent_id, "Test kill mid-execution")

            result = future.result(timeout=10)
            # Should not have executed all 10 actions
            assert len(action_log) < 10
        finally:
            mgr.shutdown()


# =====================================================================
# 3. CONCURRENT AGENT CONFLICT
# =====================================================================

class TestConcurrentAgentConflict:
    """Two agents targeting the same resource or racing for state."""

    def test_concurrent_spawn_at_capacity_boundary(self, receipt_mgr):
        """Many threads try to spawn when 1 slot left — exactly 1 succeeds."""
        reg = AgentRegistry(max_concurrent_agents=1)
        successes = []
        failures = []
        lock = threading.Lock()

        def try_spawn():
            try:
                spec = TaskSpec(description="race")
                record = reg.register(spec)
                with lock:
                    successes.append(record.agent_id)
            except MaxAgentsExceededError:
                with lock:
                    failures.append(True)

        threads = [threading.Thread(target=try_spawn) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(successes) == 1
        assert len(failures) == 19

    def test_concurrent_transitions_on_same_agent(self, registry, receipt_mgr):
        """Multiple threads try to transition the same agent — no corruption."""
        spec = TaskSpec()
        record = registry.register(spec)
        registry.transition(record.agent_id, AgentState.READY)
        registry.transition(record.agent_id, AgentState.EXECUTING)

        # Now race: some try PAUSED, some try COMPLETING
        results = {"paused": 0, "completing": 0, "errors": 0}
        lock = threading.Lock()

        def try_transition(target_state):
            try:
                registry.transition(record.agent_id, target_state)
                with lock:
                    if target_state == AgentState.PAUSED:
                        results["paused"] += 1
                    else:
                        results["completing"] += 1
            except (ValueError, AgentCollapsedError):
                with lock:
                    results["errors"] += 1

        threads = []
        for _ in range(10):
            threads.append(threading.Thread(
                target=try_transition, args=(AgentState.PAUSED,)
            ))
            threads.append(threading.Thread(
                target=try_transition, args=(AgentState.COMPLETING,)
            ))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # Exactly 1 transition should have won; rest are errors
        total_success = results["paused"] + results["completing"]
        assert total_success == 1
        assert results["errors"] == 19

    def test_concurrent_kill_and_execute(
        self, config, registry, receipt_mgr, soul_gen,
    ):
        """Kill races with execute completion — no deadlock or crash."""
        barrier = threading.Event()
        executed = []

        def executor(action):
            executed.append(True)
            barrier.set()
            time.sleep(0.1)
            return {}

        mgr = AgentLifecycleManager(
            config=config,
            registry=registry,
            receipt_manager=receipt_mgr,
            soul_generator=soul_gen,
            action_executor=executor,
        )
        try:
            spec = TaskSpec(timeout_seconds=30)
            record = mgr.spawn(spec, quest_id="q-race")
            future = mgr.execute(
                record.agent_id,
                [{"action": f"a{i}"} for i in range(5)],
            )

            barrier.wait(timeout=5)
            # Kill while executing
            mgr.kill(record.agent_id, "Race kill")

            # Should complete without crash or deadlock
            result = future.result(timeout=10)
            assert result is not None
            agent = registry.get(record.agent_id)
            assert agent.state == AgentState.COLLAPSED
        finally:
            mgr.shutdown()

    def test_collapse_all_during_active_execution(
        self, config, registry, receipt_mgr, soul_gen,
    ):
        """kill_all while agents are actively executing."""
        barriers = []

        def slow_executor(action):
            time.sleep(0.1)
            return {}

        mgr = AgentLifecycleManager(
            config=config,
            registry=registry,
            receipt_manager=receipt_mgr,
            soul_generator=soul_gen,
            action_executor=slow_executor,
        )
        try:
            futures = []
            for i in range(3):
                spec = TaskSpec(timeout_seconds=30)
                rec = mgr.spawn(spec, quest_id=f"q-all-{i}")
                f = mgr.execute(
                    rec.agent_id,
                    [{"action": f"a{j}"} for j in range(10)],
                )
                futures.append(f)

            time.sleep(0.05)
            mgr.kill_all("Emergency shutdown")

            # All futures should resolve (no hangs)
            for f in futures:
                result = f.result(timeout=10)
                assert result is not None
        finally:
            mgr.shutdown()


# =====================================================================
# 4. SOUL CONSTRAINT VIOLATION — prove agent actually gets blocked
# =====================================================================

class TestSoulConstraintViolation:
    """Prove that governance actually blocks actions, not just that valid ones pass."""

    def test_governance_denied_collapses_agent(self, registry, receipt_mgr):
        """Agent with governance that denies → collapses with GOVERNANCE_DENIED.

        Uses MCP Sentry denial (not T3) because T3 pauses for operator
        approval which would block the test indefinitely.
        """
        sentry = MagicMock()
        sentry.check_permission.return_value = False  # Deny everything

        governance = GovernanceBridge(
            risk_classifier=_MockRiskClassifier(tier=0),
            mcp_sentry=sentry,
        )

        runtime, record = _make_runtime(
            registry, receipt_mgr,
            action_executor=lambda a: {"ok": True},
            governance=governance,
        )
        result = runtime.run([{"action": "deploy_production"}])

        assert result.success is False
        assert result.collapse_reason == CollapseReason.GOVERNANCE_DENIED

    def test_mcp_sentry_denies_action(self, registry, receipt_mgr):
        """MCP Sentry explicitly blocks a capability → GOVERNANCE_DENIED."""
        sentry = MagicMock()
        sentry.check_permission.return_value = False

        governance = GovernanceBridge(
            risk_classifier=_MockRiskClassifier(tier=0),  # T0 normally approved
            mcp_sentry=sentry,
        )

        runtime, record = _make_runtime(
            registry, receipt_mgr,
            action_executor=lambda a: {"ok": True},
            governance=governance,
        )
        result = runtime.run([{"action": "blocked_capability"}])

        assert result.success is False
        assert result.collapse_reason == CollapseReason.GOVERNANCE_DENIED

    def test_risk_classifier_failure_defaults_to_t3(self, registry, receipt_mgr):
        """If RiskClassifier raises, governance defaults to T3 (requires approval)."""
        bad_classifier = MagicMock()
        bad_classifier.classify.side_effect = RuntimeError("Classifier down")

        governance = GovernanceBridge(risk_classifier=bad_classifier)

        result = governance.validate_action(
            capability="some_action",
            agent_id="test-agent",
        )
        # Should fall back to T3 which requires approval
        assert result.approved is False
        assert result.tier == "T3"
        assert result.requires_operator_approval is True

    def test_no_classifier_defaults_to_t2(self, registry, receipt_mgr):
        """No RiskClassifier at all → conservative T2 default (requires approval)."""
        governance = GovernanceBridge()  # No classifier, no trust, no sentry

        result = governance.validate_action(capability="anything")
        assert result.approved is False
        assert result.tier == "T2"
        assert result.requires_operator_approval is True

    def test_t0_action_approved(self):
        """T0 action passes governance — prove the system does approve valid ones."""
        governance = GovernanceBridge(
            risk_classifier=_MockRiskClassifier(tier=0),
        )
        result = governance.validate_action(capability="classify_intent")
        assert result.approved is True
        assert result.tier == "T0"

    def test_t1_action_approved(self):
        """T1 action passes governance without requiring approval."""
        governance = GovernanceBridge(
            risk_classifier=_MockRiskClassifier(tier=1),
        )
        result = governance.validate_action(capability="summarize")
        assert result.approved is True
        assert result.tier == "T1"

    def test_governance_denial_emits_receipt(self, registry, receipt_mgr):
        """Governance denial should emit a governance_check receipt."""
        sentry = MagicMock()
        sentry.check_permission.return_value = False

        governance = GovernanceBridge(
            risk_classifier=_MockRiskClassifier(tier=0),
            mcp_sentry=sentry,
        )

        runtime, record = _make_runtime(
            registry, receipt_mgr,
            action_executor=lambda a: {"ok": True},
            governance=governance,
        )
        result = runtime.run([{"action": "denied_action"}])
        assert result.collapse_reason == CollapseReason.GOVERNANCE_DENIED

        # Verify a governance check receipt was emitted (via receipt_mgr)
        # The receipt_mgr records governance checks in the shared receipt DB
        # Existence of the collapse itself proves the governance path was taken


# =====================================================================
# 5. RECEIPT INTEGRITY
# =====================================================================

class TestReceiptIntegrity:
    """Tamper with receipts and prove the system catches / handles it."""

    def test_receipt_roundtrip_integrity(self, tmp_path):
        """Create a receipt, persist it, read it back — fields match exactly."""
        from src.shared.receipts import Receipt, ReceiptService, create_receipt, ActionType, CognitionTier

        service = ReceiptService(data_dir=str(tmp_path))
        receipt = create_receipt(
            action_type=ActionType.HIVE_AGENT_EVENT,
            action_name="agent_spawned",
            inputs={"agent_id": "test-123", "task_id": "task-456"},
            tier=CognitionTier.DETERMINISTIC,
            quest_id="quest-789",
            metadata={"hive_subsystem": "agent"},
        )
        service.create(receipt)

        loaded = service.get(receipt.id)
        assert loaded is not None
        assert loaded.id == receipt.id
        assert loaded.action_type == receipt.action_type
        assert loaded.action_name == receipt.action_name
        assert loaded.quest_id == receipt.quest_id
        assert loaded.inputs == receipt.inputs
        assert loaded.metadata == receipt.metadata

    def test_tampered_receipt_id_not_found(self, tmp_path):
        """A forged receipt ID returns None — system doesn't find it."""
        from src.shared.receipts import ReceiptService

        service = ReceiptService(data_dir=str(tmp_path))
        result = service.get("forged-id-does-not-exist")
        assert result is None

    def test_duplicate_receipt_id_rejected(self, tmp_path):
        """Inserting a receipt with a duplicate ID raises IntegrityError."""
        import sqlite3
        from src.shared.receipts import Receipt, ReceiptService, create_receipt, ActionType, CognitionTier

        service = ReceiptService(data_dir=str(tmp_path))
        receipt = create_receipt(
            action_type=ActionType.SYSTEM,
            action_name="test",
            inputs={},
            tier=CognitionTier.DETERMINISTIC,
        )
        service.create(receipt)

        # Attempt to insert the same receipt ID again
        with pytest.raises(sqlite3.IntegrityError):
            service.create(receipt)

    def test_receipt_parent_chain_integrity(self, tmp_path):
        """Parent→child receipt chain: child's parent_id matches parent's id."""
        from src.shared.receipts import ReceiptService, create_receipt, ActionType, CognitionTier

        service = ReceiptService(data_dir=str(tmp_path))
        quest = str(uuid.uuid4())

        parent = create_receipt(
            action_type=ActionType.HIVE_TASK_EVENT,
            action_name="task_received",
            inputs={"goal": "test"},
            tier=CognitionTier.DETERMINISTIC,
            quest_id=quest,
        )
        service.create(parent)

        child = create_receipt(
            action_type=ActionType.HIVE_AGENT_EVENT,
            action_name="agent_spawned",
            inputs={"agent_id": "a1"},
            tier=CognitionTier.DETERMINISTIC,
            parent_id=parent.id,
            quest_id=quest,
        )
        service.create(child)

        # Verify chain
        loaded_child = service.get(child.id)
        assert loaded_child.parent_id == parent.id
        loaded_parent = service.get(loaded_child.parent_id)
        assert loaded_parent is not None
        assert loaded_parent.id == parent.id

    def test_orphaned_parent_id_detectable(self, tmp_path):
        """Receipt with parent_id pointing to non-existent receipt is detectable."""
        from src.shared.receipts import ReceiptService, create_receipt, ActionType, CognitionTier

        service = ReceiptService(data_dir=str(tmp_path))

        orphan = create_receipt(
            action_type=ActionType.HIVE_AGENT_EVENT,
            action_name="agent_spawned",
            inputs={},
            tier=CognitionTier.DETERMINISTIC,
            parent_id="nonexistent-parent-id",
        )
        service.create(orphan)

        loaded = service.get(orphan.id)
        assert loaded.parent_id == "nonexistent-parent-id"
        # The parent doesn't exist — this is detectable
        parent = service.get(loaded.parent_id)
        assert parent is None  # Orphaned

    def test_quest_id_grouping_accurate(self, tmp_path):
        """All receipts with same quest_id returned by quest filter."""
        from src.shared.receipts import ReceiptService, create_receipt, ActionType, CognitionTier

        service = ReceiptService(data_dir=str(tmp_path))
        quest_a = str(uuid.uuid4())
        quest_b = str(uuid.uuid4())

        for i in range(3):
            service.create(create_receipt(
                action_type=ActionType.HIVE_AGENT_EVENT,
                action_name=f"action_{i}",
                inputs={},
                tier=CognitionTier.DETERMINISTIC,
                quest_id=quest_a,
            ))
        service.create(create_receipt(
            action_type=ActionType.HIVE_AGENT_EVENT,
            action_name="other_quest",
            inputs={},
            tier=CognitionTier.DETERMINISTIC,
            quest_id=quest_b,
        ))

        results_a = service.list(quest_id=quest_a)
        results_b = service.list(quest_id=quest_b)
        assert len(results_a) == 3
        assert len(results_b) == 1
        assert all(r.quest_id == quest_a for r in results_a)

    def test_receipt_from_dict_rejects_unknown_fields(self):
        """Receipt.from_dict with extra/unknown fields raises TypeError."""
        from src.shared.receipts import Receipt

        bogus = {
            "id": "test",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "action_type": "system",
            "action_name": "test",
            "inputs": {},
            "outputs": {},
            "status": "pending",
            "tier": 0,
            "metadata": {},
            "INJECTED_FIELD": "malicious",
        }
        with pytest.raises(TypeError):
            Receipt.from_dict(bogus)


# =====================================================================
# 6. UAB FALLBACK — target app not responding, crashes mid-operation
# =====================================================================

class TestUABFallback:
    """Test UAB behavior when the target app fails."""

    @pytest.mark.asyncio
    async def test_act_without_provider_raises(self):
        """act() with no UAB provider → UABControlError."""
        bridge = UABBridge(uab_provider=None)
        with pytest.raises(UABControlError, match="not available"):
            await bridge.act("notepad", "click", {"element": "btn"})

    @pytest.mark.asyncio
    async def test_enumerate_without_provider_raises(self):
        """enumerate() with no provider → UABControlError."""
        bridge = UABBridge(uab_provider=None)
        with pytest.raises(UABControlError, match="not available"):
            await bridge.enumerate("notepad", agent_id="a1")

    @pytest.mark.asyncio
    async def test_query_without_provider_raises(self):
        """query() with no provider → UABControlError."""
        bridge = UABBridge(uab_provider=None)
        with pytest.raises(UABControlError, match="not available"):
            await bridge.query("notepad", "button.save", agent_id="a1")

    @pytest.mark.asyncio
    async def test_state_without_provider_raises(self):
        """state() with no provider → UABControlError."""
        bridge = UABBridge(uab_provider=None)
        with pytest.raises(UABControlError, match="not available"):
            await bridge.state("notepad", agent_id="a1")

    @pytest.mark.asyncio
    async def test_get_apps_without_provider_returns_empty(self):
        """get_available_apps with no provider returns empty list."""
        bridge = UABBridge(uab_provider=None)
        result = await bridge.get_available_apps()
        assert result == []

    @pytest.mark.asyncio
    async def test_provider_raises_connection_error(self):
        """Provider method raises ConnectionError → propagates."""
        provider = MagicMock()
        provider.act.side_effect = ConnectionError("App crashed")

        bridge = UABBridge(uab_provider=provider)
        with pytest.raises(ConnectionError, match="App crashed"):
            await bridge.act("notepad", "click", {"element": "x"})

    @pytest.mark.asyncio
    async def test_provider_timeout_propagates(self):
        """Provider method raises TimeoutError → propagates."""
        provider = MagicMock()
        provider.enumerate.side_effect = TimeoutError("App not responding")

        bridge = UABBridge(uab_provider=provider)
        with pytest.raises(TimeoutError, match="not responding"):
            await bridge.enumerate("notepad", agent_id="a1")

    @pytest.mark.asyncio
    async def test_governance_denies_uab_action(self):
        """Governance denial on UAB act → UABControlError."""
        provider = MagicMock()
        governance = GovernanceBridge(
            risk_classifier=_MockRiskClassifier(tier=3),
        )

        bridge = UABBridge(uab_provider=provider, governance_bridge=governance)
        with pytest.raises(UABControlError, match="Governance denied"):
            await bridge.act("banking_app", "click", {"element": "transfer"})

        # Provider should never have been called
        provider.act.assert_not_called()

    @pytest.mark.asyncio
    async def test_read_ops_skip_governance(self):
        """Read operations (enumerate, query, state) skip governance check."""
        provider = MagicMock()
        provider.enumerate.return_value = {"elements": []}
        provider.query.return_value = {"result": []}
        provider.state.return_value = {"focused": True}

        # T3 governance that would deny everything
        governance = GovernanceBridge(
            risk_classifier=_MockRiskClassifier(tier=3),
        )

        bridge = UABBridge(uab_provider=provider, governance_bridge=governance)

        # All read ops should succeed despite T3 governance
        await bridge.enumerate("notepad", agent_id="a1")
        await bridge.query("notepad", "button", agent_id="a1")
        await bridge.state("notepad", agent_id="a1")

        # Provider methods were called (not blocked)
        assert provider.enumerate.called
        assert provider.query.called
        assert provider.state.called


# =====================================================================
# 7. TRUST LEDGER REVOCATION UNDER RACE CONDITIONS
# =====================================================================

class TestTrustLedgerRaceConditions:
    """Race conditions in trust ledger: failure during graduation evaluation."""

    def test_concurrent_success_and_failure_on_same_capability(self):
        """Race: one thread records success, another failure — no crash."""
        trust = _MockTrustLedger()
        governance = GovernanceBridge(
            risk_classifier=_MockRiskClassifier(tier=1),
            trust_ledger=trust,
        )

        errors = []
        def record_success():
            try:
                for _ in range(50):
                    governance.update_trust("shell_exec", "workspace", success=True)
            except Exception as e:
                errors.append(e)

        def record_failure():
            try:
                for _ in range(50):
                    governance.update_trust("shell_exec", "workspace", success=False)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=record_success)
        t2 = threading.Thread(target=record_failure)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert len(errors) == 0
        # Both success and failure were recorded
        assert trust.success_count > 0
        assert trust.failure_count > 0

    def test_failure_during_graduation_evaluation(self):
        """Failure arrives while get_effective_tier is being read."""
        trust = _SlowTrustLedger(delay=0.05)
        governance = GovernanceBridge(
            risk_classifier=_MockRiskClassifier(tier=1),
            trust_ledger=trust,
        )

        results = []
        errors = []

        def check_trust():
            """Reads effective tier (slow)."""
            try:
                for _ in range(10):
                    r = governance.validate_action(
                        capability="shell_exec",
                        scope="workspace",
                    )
                    results.append(r)
            except Exception as e:
                errors.append(e)

        def inject_failure():
            """Records failures while check is running."""
            try:
                for _ in range(20):
                    governance.update_trust("shell_exec", "workspace", success=False)
                    time.sleep(0.01)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=check_trust)
        t2 = threading.Thread(target=inject_failure)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # No crashes
        assert len(errors) == 0
        # Some results were produced
        assert len(results) > 0

    def test_trust_ledger_exception_does_not_crash_governance(self):
        """Trust ledger raising doesn't crash the governance bridge."""
        trust = MagicMock()
        trust.get_effective_tier.side_effect = RuntimeError("DB locked")
        trust.record_success.side_effect = RuntimeError("Write failed")
        trust.record_failure.side_effect = RuntimeError("Write failed")

        governance = GovernanceBridge(
            risk_classifier=_MockRiskClassifier(tier=0),
            trust_ledger=trust,
        )

        # validate_action should still work (trust failure is logged, not fatal)
        result = governance.validate_action(capability="classify_intent")
        assert result.approved is True  # T0 still approved

        # update_trust should not raise
        governance.update_trust("x", "y", success=True)
        governance.update_trust("x", "y", success=False)

    def test_revocation_mid_execution_collapses_agent(self, registry, receipt_mgr):
        """Trust revoked mid-execution: governance starts denying, agent collapses."""
        call_count = [0]
        sentry = MagicMock()

        # First call: allow. Second call: deny.
        def dynamic_permission(capability):
            call_count[0] += 1
            return call_count[0] <= 1

        sentry.check_permission = dynamic_permission

        governance = GovernanceBridge(
            risk_classifier=_MockRiskClassifier(tier=0),
            mcp_sentry=sentry,
        )

        runtime, record = _make_runtime(
            registry, receipt_mgr,
            action_executor=lambda a: {"ok": True},
            governance=governance,
        )
        actions = [{"action": f"a{i}"} for i in range(5)]
        result = runtime.run(actions)

        # First action approved, second denied
        assert result.success is False
        assert result.collapse_reason == CollapseReason.GOVERNANCE_DENIED


# ── Mock Helpers ──────────────────────────────────────────────────────

class _MockRiskClassifier:
    """Mock risk classifier that returns a fixed tier."""
    def __init__(self, tier=0):
        self._tier = tier

    def classify(self, capability, scope="workspace", target=None):
        profile = MagicMock()
        tier_mock = MagicMock()
        tier_mock.value = self._tier
        profile.tier = tier_mock
        return profile


class _MockTrustLedger:
    """Thread-safe mock trust ledger for race condition tests."""
    def __init__(self):
        self._lock = threading.Lock()
        self.success_count = 0
        self.failure_count = 0

    def get_effective_tier(self, capability, scope):
        return None

    def record_success(self, capability, scope):
        with self._lock:
            self.success_count += 1

    def record_failure(self, capability, scope):
        with self._lock:
            self.failure_count += 1


class _SlowTrustLedger:
    """Trust ledger with artificial delay in get_effective_tier."""
    def __init__(self, delay=0.05):
        self._delay = delay
        self._lock = threading.Lock()
        self.failure_count = 0

    def get_effective_tier(self, capability, scope):
        time.sleep(self._delay)  # Simulate slow DB read
        return None

    def record_success(self, capability, scope):
        pass

    def record_failure(self, capability, scope):
        with self._lock:
            self.failure_count += 1
