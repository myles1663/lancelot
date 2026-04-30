import asyncio
import logging
from contextlib import suppress
from types import SimpleNamespace

import pytest
import yaml

from src.core.soul.store import Soul
from src.federation import api as federation_api
from src.federation.command_relay import CommandRelay
from src.federation.cost_reporter import CostReporter
from src.federation.handoff_protocol import HandoffPackage, HandoffProtocol
from src.federation.identity import generate_identity, sign_payload
from src.federation.kill_switch import FederatedKillSwitch
from src.federation.peer_protocol import PeerRegistrationProtocol, PendingRegistration
from src.federation.soul_compat import hash_soul
from src.federation.soul_handshake import SoulVersionHandshake, check_timeouts
from src.federation.soul_transport import SoulTransport
from src.federation.topology import TopologyRegistry
from src.hive.api import _resolve_operator_context
from src.hive.config import HiveConfig
from src.hive.errors import AgentSpawnDeniedError
from src.hive.lifecycle import AgentLifecycleManager
from src.hive.receipt_manager import HiveReceiptManager
from src.hive.registry import AgentRegistry
from src.hive.scoped_soul import ScopedSoulGenerator
from src.hive.types import AgentState, TaskSpec


def _make_soul_dict(version: str = "v1", **overrides):
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


def _make_soul(version: str = "v1", **overrides) -> Soul:
    return Soul(**_make_soul_dict(version, **overrides))


def _make_hive_lifecycle(tmp_path, action_executor=None, spawn_record_hook=None):
    if action_executor is None:
        action_executor = lambda action: {"result": "ok"}
    return AgentLifecycleManager(
        config=HiveConfig(max_concurrent_agents=5),
        registry=AgentRegistry(max_concurrent_agents=5),
        receipt_manager=HiveReceiptManager(data_dir=str(tmp_path)),
        soul_generator=ScopedSoulGenerator(),
        action_executor=action_executor,
        spawn_record_hook=spawn_record_hook,
    )


class _RaisingAudit:
    def record(self, *args, **kwargs):
        raise RuntimeError("audit exploded")


class _RaisingHandoffReceiptMgr:
    def record_handoff_initiated(self, **kwargs):
        raise RuntimeError("handoff receipt exploded")

    def record_handoff_received(self, **kwargs):
        raise RuntimeError("handoff receipt exploded")


class _RaisingPeerReceiptMgr:
    def record_peer_registered(self, **kwargs):
        raise RuntimeError("peer receipt exploded")

    def record_peer_removed(self, **kwargs):
        raise RuntimeError("peer removal receipt exploded")


class _RaisingSoulReceiptMgr:
    def record_soul_version_push(self, **kwargs):
        raise RuntimeError("soul push receipt exploded")

    def record_soul_handshake_ack(self, **kwargs):
        raise RuntimeError("soul handshake receipt exploded")


class _RaisingKillReceiptMgr:
    def record_kill_acknowledged(self, **kwargs):
        raise RuntimeError("kill receipt exploded")


class _BroadcastAllSuccess:
    def __init__(self):
        self.calls = []

    async def broadcast(self, peers, **kwargs):
        self.calls.append({"peers": peers, **kwargs})
        return {
            peer["instance_id"]: SimpleNamespace(
                success=True,
                error="",
                latency_ms=1,
                body={"accepted": True},
                status_code=200,
            )
            for peer in peers
        }


class _SendAcceptedTransport:
    async def send(self, **kwargs):
        return SimpleNamespace(
            success=True,
            body={"accepted": True},
            latency_ms=1,
            status_code=200,
            error="",
        )


class _ConfirmingTransport:
    def __init__(self, signer_identity):
        self._signer_identity = signer_identity

    async def send(self, **kwargs):
        counter = kwargs["body"]["counter_challenge"]
        return SimpleNamespace(
            success=True,
            body={
                "accepted": True,
                "counter_challenge_response": sign_payload(
                    self._signer_identity,
                    counter.encode("utf-8"),
                ).hex(),
            },
            latency_ms=1,
            status_code=200,
            error="",
        )


class _QueueFullEmitter:
    def __init__(self):
        self.unsubscribed = False

    def subscribe(self, callback):
        callback(SimpleNamespace(to_dict=lambda: {"instance_id": "peer-1"}))

    def unsubscribe(self, callback):
        self.unsubscribed = True


class _AlwaysFullQueue:
    def put_nowait(self, item):
        raise asyncio.QueueFull

    async def get(self):
        await asyncio.sleep(3600)


@pytest.mark.asyncio
async def test_cost_reporter_stop_logs_cancelled_task(caplog):
    identity = generate_identity()
    reporter = CostReporter(
        identity=identity,
        transport=None,
        topology=TopologyRegistry(self_instance_id=identity.instance_id),
    )
    reporter._running = True
    reporter._task = asyncio.create_task(asyncio.sleep(10))

    with caplog.at_level(logging.DEBUG):
        await reporter.stop()

    assert "Cost reporter background task cancelled during stop" in caplog.text


def test_hive_operator_context_logs_resolution_failure(monkeypatch, caplog):
    import src.core.governance_receipts as governance_receipts

    def _boom(request):
        raise RuntimeError("identity exploded")

    monkeypatch.setattr(governance_receipts, "_resolve_identity", _boom)

    with caplog.at_level(logging.WARNING):
        result = _resolve_operator_context(object())

    assert result == (None, None, None)
    assert "Failed to resolve HIVE operator context from request" in caplog.text


def test_hive_spawn_logs_force_collapse_failure_after_spawn_record_error(tmp_path, monkeypatch, caplog):
    lifecycle = _make_hive_lifecycle(
        tmp_path,
        spawn_record_hook=lambda record: (_ for _ in ()).throw(RuntimeError("spawn record exploded")),
    )
    try:
        original_transition = lifecycle._registry.transition

        def _flaky_transition(agent_id, new_state, *args, **kwargs):
            if new_state == AgentState.COLLAPSED:
                raise ValueError("collapse transition failed")
            return original_transition(agent_id, new_state, *args, **kwargs)

        monkeypatch.setattr(lifecycle._registry, "transition", _flaky_transition)

        with caplog.at_level(logging.WARNING):
            with pytest.raises(AgentSpawnDeniedError):
                lifecycle.spawn(TaskSpec(description="spawn failure"))

        assert "could not be force-collapsed after spawn record failure" in caplog.text
    finally:
        lifecycle.shutdown()


def test_hive_execute_logs_success_transition_failures(tmp_path, monkeypatch, caplog):
    lifecycle = _make_hive_lifecycle(tmp_path)
    try:
        record = lifecycle.spawn(TaskSpec(description="success path"))
        original_transition = lifecycle._registry.transition

        def _flaky_transition(agent_id, new_state, *args, **kwargs):
            if new_state in {AgentState.COMPLETING, AgentState.COLLAPSED}:
                raise ValueError(f"{new_state.value} transition failed")
            return original_transition(agent_id, new_state, *args, **kwargs)

        monkeypatch.setattr(lifecycle._registry, "transition", _flaky_transition)

        with caplog.at_level(logging.WARNING):
            result = lifecycle.execute(record.agent_id, [{"action": "done"}]).result(timeout=5)

        assert result.success is True
        assert "could not transition to COMPLETING after runtime success" in caplog.text
        assert "could not transition to COLLAPSED after runtime success" in caplog.text
    finally:
        lifecycle.shutdown()


def test_hive_execute_logs_error_transition_failure(tmp_path, monkeypatch, caplog):
    lifecycle = _make_hive_lifecycle(
        tmp_path,
        action_executor=lambda action: (_ for _ in ()).throw(RuntimeError("runtime exploded")),
    )
    try:
        record = lifecycle.spawn(TaskSpec(description="error path"))
        original_transition = lifecycle._registry.transition

        def _flaky_transition(agent_id, new_state, *args, **kwargs):
            if new_state == AgentState.COLLAPSED:
                raise ValueError("collapse transition failed")
            return original_transition(agent_id, new_state, *args, **kwargs)

        monkeypatch.setattr(lifecycle._registry, "transition", _flaky_transition)

        with caplog.at_level(logging.WARNING):
            result = lifecycle.execute(record.agent_id, [{"action": "fail"}]).result(timeout=5)

        assert result.success is False
        assert "could not transition to COLLAPSED after runtime success" in caplog.text
    finally:
        lifecycle.shutdown()


def test_hive_pause_resume_and_kill_log_transition_failures(tmp_path, monkeypatch, caplog):
    def _slow_executor(action):
        import time

        time.sleep(0.02)
        return {"result": "ok"}

    lifecycle = _make_hive_lifecycle(tmp_path, action_executor=_slow_executor)
    try:
        record = lifecycle.spawn(TaskSpec(description="intervention path"))
        lifecycle.execute(record.agent_id, [{"action": f"step-{i}"} for i in range(10)])

        original_transition = lifecycle._registry.transition

        def _flaky_transition(agent_id, new_state, *args, **kwargs):
            if new_state in {AgentState.PAUSED, AgentState.EXECUTING, AgentState.COLLAPSED}:
                raise ValueError(f"{new_state.value} transition failed")
            return original_transition(agent_id, new_state, *args, **kwargs)

        monkeypatch.setattr(lifecycle._registry, "transition", _flaky_transition)

        with caplog.at_level(logging.WARNING):
            lifecycle.pause(record.agent_id, "pause for inspection", operator_id="op-1", session_id="sess-1")
            lifecycle.resume(record.agent_id, operator_id="op-1", session_id="sess-1")

        record2 = lifecycle.spawn(TaskSpec(description="kill path"))
        lifecycle._runtimes.pop(record2.agent_id, None)
        with caplog.at_level(logging.WARNING):
            lifecycle.kill(record2.agent_id, "kill now", operator_id="op-1", session_id="sess-1")

        assert "could not transition to PAUSED during operator pause" in caplog.text
        assert "could not transition back to EXECUTING during operator resume" in caplog.text
        assert "could not be force-collapsed during operator kill" in caplog.text
    finally:
        lifecycle.shutdown()


@pytest.mark.asyncio
async def test_federation_heartbeat_stream_logs_queue_overflow_and_cancellation(monkeypatch, caplog):
    identity = generate_identity()
    emitter = _QueueFullEmitter()
    federation_api.init_federation_api(identity, emitter, config=object())
    monkeypatch.setattr(federation_api.asyncio, "Queue", _AlwaysFullQueue)

    try:
        response = await federation_api.heartbeat_stream(None)
        iterator = response.body_iterator
        with caplog.at_level(logging.DEBUG):
            task = asyncio.create_task(iterator.__anext__())
            await asyncio.sleep(0)
            task.cancel()
            with suppress(asyncio.CancelledError, StopAsyncIteration):
                await task
        assert "queue full; dropping heartbeat event" in caplog.text
        assert "Federation heartbeat stream cancelled" in caplog.text
        assert emitter.unsubscribed is True
    finally:
        federation_api.shutdown_federation_api()


@pytest.mark.asyncio
async def test_command_relay_logs_kill_delivery_audit_failure(caplog):
    root = generate_identity()
    child = generate_identity()
    topology = TopologyRegistry(self_instance_id=root.instance_id)
    topology.register_peer(
        instance_id=child.instance_id,
        fingerprint=child.fingerprint,
        public_key_hex=child.public_key_hex(),
        address="http://child:8000",
        role="child",
    )
    relay = CommandRelay(
        identity=root,
        transport=_BroadcastAllSuccess(),
        topology=topology,
        audit=_RaisingAudit(),
    )

    with caplog.at_level(logging.WARNING):
        result = await relay.propagate_kill({"command_id": "kill-1"})

    assert result == {child.instance_id: True}
    assert "Failed to record kill command delivery audit for peer" in caplog.text


def test_command_relay_logs_kill_receipt_failure(caplog):
    root = generate_identity()
    child = generate_identity()
    topology = TopologyRegistry(self_instance_id=child.instance_id)
    topology.register_peer(
        instance_id=root.instance_id,
        fingerprint=root.fingerprint,
        public_key_hex=root.public_key_hex(),
        address="http://root:8000",
        role="root",
    )
    relay = CommandRelay(
        identity=child,
        transport=None,
        topology=topology,
        kill_switch=FederatedKillSwitch(
            self_instance_id=child.instance_id,
            local_kill_handler=lambda reason: 1,
        ),
        receipt_mgr=_RaisingKillReceiptMgr(),
    )

    with caplog.at_level(logging.WARNING):
        result = relay.handle_kill_command(
            {
                "command": {
                    "command_id": "kill-ack",
                    "command_type": "federation_kill",
                    "authority": "L1_federation_root",
                    "reason": "test",
                },
                "issuer_instance_id": root.instance_id,
            }
        )

    assert result["accepted"] is True
    assert "Failed to record kill acknowledgement receipt for command kill-ack" in caplog.text


@pytest.mark.asyncio
async def test_handoff_protocol_logs_initiation_receipt_and_audit_failures(caplog):
    source = generate_identity()
    target = generate_identity()
    topology = TopologyRegistry(self_instance_id=source.instance_id)
    topology.register_peer(
        instance_id=target.instance_id,
        address="http://target:8000",
        role="child",
    )
    protocol = HandoffProtocol(
        identity=source,
        transport=_SendAcceptedTransport(),
        topology=topology,
        receipt_mgr=_RaisingHandoffReceiptMgr(),
        audit=_RaisingAudit(),
        current_soul_provider=lambda: _make_soul(),
    )

    with caplog.at_level(logging.WARNING):
        result = await protocol.initiate_handoff(
            target_instance_id=target.instance_id,
            task_context={"goal": "delegate"},
            soul_context=_make_soul().model_dump(),
            contract={"success_criteria": ["done"]},
            federation_quest_id="quest-1",
        )

    assert result.success is True
    assert "Failed to record handoff initiation receipt" in caplog.text
    assert "Failed to write handoff initiation audit" in caplog.text


def test_handoff_protocol_logs_received_receipt_and_audit_failures(caplog):
    source = generate_identity()
    target = generate_identity()
    topology = TopologyRegistry(self_instance_id=target.instance_id)
    topology.register_peer(
        instance_id=source.instance_id,
        address="http://source:8000",
        role="root",
    )
    protocol = HandoffProtocol(
        identity=target,
        transport=None,
        topology=topology,
        receipt_mgr=_RaisingHandoffReceiptMgr(),
        audit=_RaisingAudit(),
        current_soul_provider=lambda: _make_soul(),
    )

    with caplog.at_level(logging.WARNING):
        result = protocol.handle_handoff_initiation(
            {
                "handoff_id": "handoff-1",
                "federation_quest_id": "quest-1",
                "source_instance_id": source.instance_id,
                "task_context": {"goal": "delegate"},
                "soul_context": _make_soul().model_dump(),
                "contract": {"success_criteria": ["done"]},
                "receipt_chain": [],
            }
        )

    assert result["accepted"] is True
    assert "Failed to record handoff received receipt" in caplog.text
    assert "Failed to write handoff received audit" in caplog.text


@pytest.mark.asyncio
async def test_handoff_protocol_logs_completion_audit_failure(caplog):
    source = generate_identity()
    target = generate_identity()
    topology = TopologyRegistry(self_instance_id=target.instance_id)
    topology.register_peer(
        instance_id=source.instance_id,
        address="http://source:8000",
        role="root",
    )
    protocol = HandoffProtocol(
        identity=target,
        transport=_SendAcceptedTransport(),
        topology=topology,
        audit=_RaisingAudit(),
        current_soul_provider=lambda: _make_soul(),
    )
    protocol._active_handoffs["handoff-1"] = HandoffPackage(
        handoff_id="handoff-1",
        federation_quest_id="quest-1",
        source_instance_id=source.instance_id,
        target_instance_id=target.instance_id,
    )

    with caplog.at_level(logging.WARNING):
        delivered = await protocol.report_completion("handoff-1", {"status": "success"})

    assert delivered is True
    assert "Failed to write handoff completion audit" in caplog.text


def test_handoff_protocol_logs_inbound_completion_audit_failure(caplog):
    source = generate_identity()
    target = generate_identity()
    topology = TopologyRegistry(self_instance_id=source.instance_id)
    protocol = HandoffProtocol(
        identity=source,
        transport=None,
        topology=topology,
        audit=_RaisingAudit(),
        current_soul_provider=lambda: _make_soul(),
    )
    protocol._active_handoffs["handoff-1"] = HandoffPackage(
        handoff_id="handoff-1",
        federation_quest_id="quest-1",
        source_instance_id=source.instance_id,
        target_instance_id=target.instance_id,
        contract={},
    )

    with caplog.at_level(logging.WARNING):
        result = protocol.handle_completion_report(
            {
                "handoff_id": "handoff-1",
                "federation_quest_id": "quest-1",
                "reporting_instance_id": target.instance_id,
                "result": {"status": "success"},
                "receipts": [],
            },
            authenticated_instance_id=target.instance_id,
        )

    assert result["acknowledged"] is True
    assert "Failed to write inbound handoff completion audit" in caplog.text


def test_handoff_protocol_logs_contradiction_audit_failure(caplog):
    protocol = HandoffProtocol(
        identity=generate_identity(),
        transport=None,
        topology=TopologyRegistry(self_instance_id=generate_identity().instance_id),
        audit=_RaisingAudit(),
        current_soul_provider=lambda: _make_soul(),
    )
    contradiction = SimpleNamespace(
        contradiction_id="c-1",
        category=SimpleNamespace(value="temporal"),
        severity=SimpleNamespace(value="high"),
        description="timeline contradiction",
    )

    with caplog.at_level(logging.WARNING):
        protocol._record_contradictions([contradiction], quest_id="quest-1", handoff_id="handoff-1")

    assert "Failed to write contradiction audit for handoff handoff-1" in caplog.text


@pytest.mark.asyncio
async def test_peer_protocol_logs_inbound_receipt_and_audit_failures(caplog):
    initiator = generate_identity()
    target = generate_identity()
    topology = TopologyRegistry(self_instance_id=target.instance_id)
    protocol = PeerRegistrationProtocol(
        identity=target,
        topology=topology,
        transport=_ConfirmingTransport(initiator),
        receipt_mgr=_RaisingPeerReceiptMgr(),
        audit=_RaisingAudit(),
        self_address="http://target:8000",
    )
    challenge = "challenge-1"
    request = {
        "registration_id": "reg-1",
        "instance_id": initiator.instance_id,
        "public_key_hex": initiator.public_key_hex(),
        "fingerprint": initiator.fingerprint,
        "address": "http://initiator:8000",
        "role": "peer",
        "challenge": challenge,
        "challenge_signature": sign_payload(initiator, challenge.encode("utf-8")).hex(),
    }

    with caplog.at_level(logging.WARNING):
        result = await protocol.handle_registration_request(request)

    assert result["accepted"] is True
    assert "Failed to record inbound peer registration receipt" in caplog.text
    assert "Failed to write inbound peer registration audit" in caplog.text


def test_peer_protocol_logs_outbound_receipt_and_audit_failures(caplog):
    initiator = generate_identity()
    target = generate_identity()
    topology = TopologyRegistry(self_instance_id=initiator.instance_id)
    protocol = PeerRegistrationProtocol(
        identity=initiator,
        topology=topology,
        transport=None,
        receipt_mgr=_RaisingPeerReceiptMgr(),
        audit=_RaisingAudit(),
        self_address="http://initiator:8000",
    )
    protocol._pending["reg-1"] = PendingRegistration(
        registration_id="reg-1",
        instance_id="",
        public_key_hex="",
        fingerprint="",
        address="http://initiator:8000",
        role="peer",
        soul_version_hash="",
        challenge="challenge-1",
        expected_target_address="http://target:8000",
        direction="outbound",
    )
    response = {
        "registration_id": "reg-1",
        "instance_id": target.instance_id,
        "public_key_hex": target.public_key_hex(),
        "fingerprint": target.fingerprint,
        "challenge_response": sign_payload(target, b"challenge-1").hex(),
        "counter_challenge": "counter-1",
    }

    with caplog.at_level(logging.WARNING):
        result = protocol.handle_registration_confirm(response)

    assert result["accepted"] is True
    assert "Failed to record outbound peer registration receipt" in caplog.text
    assert "Failed to write outbound peer registration audit" in caplog.text


def test_peer_protocol_logs_peer_removal_receipt_and_audit_failures(caplog):
    identity = generate_identity()
    peer = generate_identity()
    topology = TopologyRegistry(self_instance_id=identity.instance_id)
    topology.register_peer(
        instance_id=peer.instance_id,
        fingerprint=peer.fingerprint,
        public_key_hex=peer.public_key_hex(),
        address="http://peer:8000",
        role="peer",
    )
    protocol = PeerRegistrationProtocol(
        identity=identity,
        topology=topology,
        transport=None,
        receipt_mgr=_RaisingPeerReceiptMgr(),
        audit=_RaisingAudit(),
        self_address="http://self:8000",
    )

    with caplog.at_level(logging.WARNING):
        result = protocol.handle_peer_removal(peer.instance_id)

    assert result["removed"] is True
    assert "Failed to record peer removal receipt" in caplog.text
    assert "Failed to write peer removal audit" in caplog.text


@pytest.mark.asyncio
async def test_soul_transport_logs_push_receipt_and_audit_failures(caplog):
    root = generate_identity()
    child = generate_identity()
    topology = TopologyRegistry(self_instance_id=root.instance_id)
    topology.register_peer(
        instance_id=child.instance_id,
        fingerprint=child.fingerprint,
        public_key_hex=child.public_key_hex(),
        address="http://child:8000",
        role="child",
    )
    transport = SoulTransport(
        identity=root,
        transport=_BroadcastAllSuccess(),
        topology=topology,
        current_soul_provider=lambda: _make_soul("v1"),
        receipt_mgr=_RaisingSoulReceiptMgr(),
        audit=_RaisingAudit(),
    )
    soul_doc = _make_soul_dict("v2", mission="Updated mission")

    with caplog.at_level(logging.WARNING):
        result = await transport.push_soul_update(
            soul_document=soul_doc,
            soul_hash=hash_soul(Soul(**soul_doc)),
            tier="T1",
            reason="update",
        )

    assert result["delivered"] == 1
    assert "Failed to record soul push receipt" in caplog.text
    assert "Failed to write soul push audit" in caplog.text


def test_soul_transport_logs_failed_apply_receipt_failure(tmp_path, caplog):
    root = generate_identity()
    child = generate_identity()
    topology = TopologyRegistry(self_instance_id=child.instance_id)
    topology.register_peer(
        instance_id=root.instance_id,
        fingerprint=root.fingerprint,
        public_key_hex=root.public_key_hex(),
        address="http://root:8000",
        role="root",
    )
    soul_root = tmp_path / "soul"
    versions_dir = soul_root / "soul_versions"
    versions_dir.mkdir(parents=True)
    (versions_dir / "soul_v1.yaml").write_text(
        yaml.safe_dump(_make_soul_dict("v1"), sort_keys=False),
        encoding="utf-8",
    )
    (soul_root / "ACTIVE").write_text("v1", encoding="utf-8")
    transport = SoulTransport(
        identity=child,
        transport=None,
        topology=topology,
        current_soul_provider=lambda: _make_soul("v1"),
        runtime_reload_callback=lambda soul: (_ for _ in ()).throw(RuntimeError("reload exploded")),
        soul_dir=str(soul_root),
        receipt_mgr=_RaisingSoulReceiptMgr(),
    )
    soul_doc = _make_soul_dict("v2", mission="Updated mission")

    with caplog.at_level(logging.WARNING):
        result = transport.handle_soul_push(
            {
                "source_instance_id": root.instance_id,
                "soul_document": soul_doc,
                "soul_hash": hash_soul(Soul(**soul_doc)),
                "tier": "T1",
            }
        )

    assert result["accepted"] is False
    assert "Failed to record failed soul handshake receipt" in caplog.text


def test_soul_transport_logs_success_ack_receipt_failure(tmp_path, caplog):
    root = generate_identity()
    child = generate_identity()
    topology = TopologyRegistry(self_instance_id=child.instance_id)
    topology.register_peer(
        instance_id=root.instance_id,
        fingerprint=root.fingerprint,
        public_key_hex=root.public_key_hex(),
        address="http://root:8000",
        role="root",
    )
    soul_root = tmp_path / "soul"
    versions_dir = soul_root / "soul_versions"
    versions_dir.mkdir(parents=True)
    (versions_dir / "soul_v1.yaml").write_text(
        yaml.safe_dump(_make_soul_dict("v1"), sort_keys=False),
        encoding="utf-8",
    )
    (soul_root / "ACTIVE").write_text("v1", encoding="utf-8")
    transport = SoulTransport(
        identity=child,
        transport=None,
        topology=topology,
        current_soul_provider=lambda: _make_soul("v1"),
        runtime_reload_callback=lambda soul: None,
        soul_dir=str(soul_root),
        receipt_mgr=_RaisingSoulReceiptMgr(),
    )
    soul_doc = _make_soul_dict("v2", mission="Updated mission")

    with caplog.at_level(logging.WARNING):
        result = transport.handle_soul_push(
            {
                "source_instance_id": root.instance_id,
                "soul_document": soul_doc,
                "soul_hash": hash_soul(Soul(**soul_doc)),
                "tier": "T1",
            }
        )

    assert result["accepted"] is True
    assert "Failed to record soul handshake acknowledgement" in caplog.text


def test_soul_handshake_logs_malformed_timeout_timestamp(caplog):
    handshake = SoulVersionHandshake(
        handshake_id="hs-1",
        target_instance_id="peer-1",
        timeout_at="not-a-timestamp",
    )

    with caplog.at_level(logging.DEBUG):
        timed_out = check_timeouts([handshake])

    assert timed_out == []
    assert "Ignoring malformed Soul handshake timestamp for hs-1" in caplog.text
