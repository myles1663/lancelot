# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""Tests for Federation Handoff Protocol — task handoff handlers."""

import os
import tempfile

import pytest
from src.core.soul.store import (
    ApprovalRules,
    AutonomyPosture,
    RiskRule,
    SchedulingBoundaries,
    Soul,
    SpawnBudgetGovernance,
)
from src.federation.contradiction_detector import ContradictionDetector

from src.federation.identity import generate_identity
from src.federation.topology import TopologyRegistry
from src.federation.handoff_protocol import HandoffProtocol, HandoffPackage


@pytest.fixture
def source_identity():
    return generate_identity()


@pytest.fixture
def target_identity():
    return generate_identity()


@pytest.fixture
def source_topology(source_identity, target_identity):
    topo = TopologyRegistry(self_instance_id=source_identity.instance_id)
    topo.register_peer(
        instance_id=target_identity.instance_id,
        address="http://target:8000",
        role="child",
    )
    return topo


@pytest.fixture
def target_topology(target_identity, source_identity):
    topo = TopologyRegistry(self_instance_id=target_identity.instance_id)
    topo.register_peer(
        instance_id=source_identity.instance_id,
        address="http://source:8000",
        role="root",
    )
    return topo


@pytest.fixture
def target_protocol(target_identity, target_topology):
    return HandoffProtocol(
        identity=target_identity,
        transport=None,
        topology=target_topology,
        current_soul_provider=lambda: _make_soul(),
    )


def _make_soul(**overrides) -> Soul:
    defaults = dict(
        version="v1",
        mission="Support software reliably",
        allegiance="Customer and operator trust",
        autonomy_posture=AutonomyPosture(
            level="supervised",
            description="test",
            allowed_autonomous=["classify", "summarize", "health_check"],
            requires_approval=["deploy", "delete", "uab_click", "uab_type"],
        ),
        risk_rules=[RiskRule(name="destructive_actions_require_approval", description="test")],
        approval_rules=ApprovalRules(default_timeout_seconds=3600, channels=["war_room"]),
        tone_invariants=["Never mislead"],
        memory_ethics=["No PII without consent"],
        scheduling_boundaries=SchedulingBoundaries(
            max_concurrent_jobs=5,
            max_job_duration_seconds=300,
        ),
        spawn_budget=SpawnBudgetGovernance(
            max_concurrent_spawns=10,
            max_spawn_model_tier="T2",
        ),
    )
    defaults.update(overrides)
    return Soul(**defaults)


class TestHandleHandoffInitiation:
    def test_receipt_manager_uses_correct_handoff_parameter_names(self, target_identity, target_topology, source_identity):
        class ReceiptManagerStub:
            def __init__(self):
                self.initiated = []
                self.received = []

            def record_handoff_initiated(self, **kwargs):
                self.initiated.append(kwargs)

            def record_handoff_received(self, **kwargs):
                self.received.append(kwargs)

        receipt_mgr = ReceiptManagerStub()
        target_protocol = HandoffProtocol(
            identity=target_identity,
            transport=None,
            topology=target_topology,
            receipt_mgr=receipt_mgr,
            current_soul_provider=lambda: _make_soul(),
        )

        result = target_protocol.handle_handoff_initiation({
            "handoff_id": "handoff-receipts",
            "federation_quest_id": "quest-receipts",
            "source_instance_id": source_identity.instance_id,
            "target_instance_id": target_identity.instance_id,
            "task_context": {"goal": "analyze data"},
            "soul_context": _make_soul().model_dump(),
            "contract": {"success_criteria": ["done"]},
            "receipt_chain": [],
        })

        assert result["accepted"] is True
        assert receipt_mgr.received == [{
            "handoff_id": "handoff-receipts",
            "source_instance_id": source_identity.instance_id,
            "federation_quest_id": "quest-receipts",
        }]

    def test_valid_handoff_accepted(self, target_protocol, source_identity):
        result = target_protocol.handle_handoff_initiation({
            "handoff_id": "handoff-001",
            "federation_quest_id": "quest-001",
            "source_instance_id": source_identity.instance_id,
            "target_instance_id": target_protocol._identity.instance_id,
            "task_context": {"goal": "analyze data"},
            "soul_context": _make_soul().model_dump(),
            "contract": {"success_criteria": ["data analyzed"]},
            "receipt_chain": [],
        })
        assert result["accepted"]
        assert result["handoff_id"] == "handoff-001"

    def test_unknown_source_rejected(self, target_protocol):
        result = target_protocol.handle_handoff_initiation({
            "handoff_id": "handoff-bad",
            "source_instance_id": "unknown-peer",
            "task_context": {},
        })
        assert not result["accepted"]
        assert "Unknown" in result["reason"]

    def test_handoff_rejects_authenticated_source_mismatch(self, target_protocol, source_identity):
        result = target_protocol.handle_handoff_initiation(
            {
                "handoff_id": "handoff-spoof",
                "source_instance_id": "spoofed-peer",
                "task_context": {"goal": "analyze data"},
                "soul_context": _make_soul().model_dump(),
                "contract": {"success_criteria": ["done"]},
                "receipt_chain": [],
            },
            authenticated_instance_id=source_identity.instance_id,
        )
        assert not result["accepted"]
        assert "does not match authenticated peer" in result["reason"]

    def test_incompatible_soul_rejected(self, target_protocol, source_identity):
        result = target_protocol.handle_handoff_initiation({
            "handoff_id": "handoff-red",
            "federation_quest_id": "quest-red",
            "source_instance_id": source_identity.instance_id,
            "task_context": {"goal": "delete production data"},
            "soul_context": _make_soul(
                mission="Different mission entirely",
                allegiance="Competing interests",
            ).model_dump(),
            "contract": {"success_criteria": ["done"]},
            "receipt_chain": [],
        })
        assert not result["accepted"]
        assert "Incompatible Soul boundary" in result["reason"]

    def test_payload_schema_required_fields_enforced(self, target_protocol, source_identity):
        result = target_protocol.handle_handoff_initiation({
            "handoff_id": "handoff-schema",
            "federation_quest_id": "quest-schema",
            "source_instance_id": source_identity.instance_id,
            "task_context": {"goal": "analyze data"},
            "soul_context": _make_soul().model_dump(),
            "contract": {
                "success_criteria": ["done"],
                "data_payload_schema": {
                    "required": ["goal", "ticket_id"],
                    "properties": {"ticket_id": {"type": "string"}},
                },
            },
            "receipt_chain": [],
        })
        assert not result["accepted"]
        assert "missing required fields" in result["reason"]

    def test_soul_constraints_enforced(self, target_protocol, source_identity):
        result = target_protocol.handle_handoff_initiation({
            "handoff_id": "handoff-constraints",
            "federation_quest_id": "quest-constraints",
            "source_instance_id": source_identity.instance_id,
            "task_context": {"goal": "analyze data"},
            "soul_context": _make_soul().model_dump(),
            "contract": {
                "success_criteria": ["done"],
                "soul_context_constraints": {
                    "autonomy_posture": {
                        "allowed_autonomous": ["deploy"],
                    },
                },
            },
            "receipt_chain": [],
        })
        assert not result["accepted"]
        assert "does not satisfy handoff contract constraints" in result["reason"]

    def test_handoff_tracked_as_active(self, target_protocol, source_identity):
        target_protocol.handle_handoff_initiation({
            "handoff_id": "handoff-tracked",
            "federation_quest_id": "quest-track",
            "source_instance_id": source_identity.instance_id,
            "task_context": {"goal": "test"},
            "soul_context": _make_soul().model_dump(),
            "contract": {},
            "receipt_chain": [],
        })

        status = target_protocol.get_handoff_status("handoff-tracked")
        assert status is not None
        assert status["state"] == "active"
        assert status["federation_quest_id"] == "quest-track"

    def test_list_active_handoffs(self, target_protocol, source_identity):
        for i in range(3):
            target_protocol.handle_handoff_initiation({
                "handoff_id": f"handoff-{i}",
                "federation_quest_id": f"quest-{i}",
                "source_instance_id": source_identity.instance_id,
                "task_context": {},
                "soul_context": _make_soul().model_dump(),
                "contract": {},
                "receipt_chain": [],
            })

        active = target_protocol.list_active_handoffs()
        assert len(active) == 3


class TestHandleCompletionReport:
    def test_completion_acknowledged(self, target_protocol, source_identity):
        # First accept a handoff
        target_protocol.handle_handoff_initiation({
            "handoff_id": "handoff-complete",
            "federation_quest_id": "quest-done",
            "source_instance_id": source_identity.instance_id,
            "task_context": {},
            "soul_context": _make_soul().model_dump(),
            "contract": {},
            "receipt_chain": [],
        })

        # Then handle completion from source side
        result = target_protocol.handle_completion_report({
            "handoff_id": "handoff-complete",
            "federation_quest_id": "quest-done",
            "reporting_instance_id": target_protocol._identity.instance_id,
            "result": {"status": "success"},
        }, authenticated_instance_id=target_protocol._identity.instance_id)
        assert result["acknowledged"]

    def test_completion_rejected_from_unexpected_peer(self, target_protocol, source_identity, target_identity):
        target_protocol.handle_handoff_initiation({
            "handoff_id": "handoff-wrong-peer",
            "federation_quest_id": "quest-wrong-peer",
            "source_instance_id": source_identity.instance_id,
            "task_context": {},
            "soul_context": _make_soul().model_dump(),
            "contract": {},
            "receipt_chain": [],
        })

        result = target_protocol.handle_completion_report(
            {
                "handoff_id": "handoff-wrong-peer",
                "federation_quest_id": "quest-wrong-peer",
                "reporting_instance_id": source_identity.instance_id,
                "result": {"status": "success"},
            },
            authenticated_instance_id=source_identity.instance_id,
        )

        assert result["acknowledged"] is False
        assert "unexpected peer" in result["error"]
        assert target_protocol.get_handoff_status("handoff-wrong-peer") is not None

    def test_completion_rejected_when_reporting_identity_mismatch(self, target_protocol, source_identity, target_identity):
        target_protocol.handle_handoff_initiation({
            "handoff_id": "handoff-mismatch",
            "federation_quest_id": "quest-mismatch",
            "source_instance_id": source_identity.instance_id,
            "task_context": {},
            "soul_context": _make_soul().model_dump(),
            "contract": {},
            "receipt_chain": [],
        })

        result = target_protocol.handle_completion_report(
            {
                "handoff_id": "handoff-mismatch",
                "federation_quest_id": "quest-mismatch",
                "reporting_instance_id": target_identity.instance_id,
                "result": {"status": "success"},
            },
            authenticated_instance_id=source_identity.instance_id,
        )

        assert result["acknowledged"] is False
        assert "instance mismatch" in result["error"]

    def test_handoff_rejected_when_receipt_chain_is_temporally_invalid(self, target_identity, target_topology, source_identity):
        detector = ContradictionDetector()
        target_protocol = HandoffProtocol(
            identity=target_identity,
            transport=None,
            topology=target_topology,
            contradiction_detector=detector,
            current_soul_provider=lambda: _make_soul(),
        )

        result = target_protocol.handle_handoff_initiation({
            "handoff_id": "handoff-contradiction",
            "federation_quest_id": "quest-contradiction",
            "source_instance_id": source_identity.instance_id,
            "task_context": {"goal": "analyze data"},
            "soul_context": _make_soul().model_dump(),
            "contract": {"success_criteria": ["done"]},
            "receipt_chain": [
                {"id": "r1", "timestamp": "2026-01-01T01:00:00+00:00"},
                {"id": "r2", "timestamp": "2026-01-01T00:00:00+00:00"},
            ],
        })

        assert result["accepted"] is False
        assert result["contradictions"] == 1

    def test_completion_rejected_when_result_violates_contract(self, target_identity, target_topology, source_identity):
        detector = ContradictionDetector()
        target_protocol = HandoffProtocol(
            identity=target_identity,
            transport=None,
            topology=target_topology,
            contradiction_detector=detector,
            current_soul_provider=lambda: _make_soul(),
        )

        target_protocol.handle_handoff_initiation({
            "handoff_id": "handoff-result-contradiction",
            "federation_quest_id": "quest-result-contradiction",
            "source_instance_id": source_identity.instance_id,
            "task_context": {"goal": "analyze data"},
            "soul_context": _make_soul().model_dump(),
            "contract": {
                "success_criteria": ["report completed"],
                "result_schema": {"required": ["summary", "status"]},
            },
            "receipt_chain": [],
        })

        result = target_protocol.handle_completion_report(
            {
                "handoff_id": "handoff-result-contradiction",
                "federation_quest_id": "quest-result-contradiction",
                "reporting_instance_id": target_identity.instance_id,
                "result": {"status": "failed"},
                "receipts": [{"id": "rc1", "timestamp": "2026-01-01T00:00:00+00:00"}],
            },
            authenticated_instance_id=target_identity.instance_id,
        )

        assert result["acknowledged"] is False
        assert result["contradictions"] >= 1
        assert target_protocol.get_handoff_status("handoff-result-contradiction") is not None

    def test_handoff_rejected_when_contradiction_detector_is_unavailable(self, target_identity, target_topology, source_identity):
        class BrokenDetector:
            def check_receipt_chain(self, *args, **kwargs):
                raise RuntimeError("detector exploded")

        target_protocol = HandoffProtocol(
            identity=target_identity,
            transport=None,
            topology=target_topology,
            contradiction_detector=BrokenDetector(),
            current_soul_provider=lambda: _make_soul(),
        )

        result = target_protocol.handle_handoff_initiation({
            "handoff_id": "handoff-detector-down",
            "federation_quest_id": "quest-detector-down",
            "source_instance_id": source_identity.instance_id,
            "task_context": {"goal": "analyze data"},
            "soul_context": _make_soul().model_dump(),
            "contract": {"success_criteria": ["done"]},
            "receipt_chain": [{"id": "r1", "timestamp": "2026-01-01T00:00:00+00:00"}],
        })

        assert result["accepted"] is False
        assert "Contradiction detector unavailable" in result["reason"]
        assert target_protocol.get_handoff_status("handoff-detector-down") is None

    def test_completion_rejected_when_contradiction_detector_is_unavailable(self, target_identity, target_topology, source_identity):
        class BrokenDetector:
            def check_receipt_chain(self, *args, **kwargs):
                raise RuntimeError("detector exploded")

        target_protocol = HandoffProtocol(
            identity=target_identity,
            transport=None,
            topology=target_topology,
            contradiction_detector=BrokenDetector(),
            current_soul_provider=lambda: _make_soul(),
        )

        target_protocol.handle_handoff_initiation({
            "handoff_id": "handoff-complete-detector-down",
            "federation_quest_id": "quest-complete-detector-down",
            "source_instance_id": source_identity.instance_id,
            "task_context": {"goal": "analyze data"},
            "soul_context": _make_soul().model_dump(),
            "contract": {"success_criteria": ["done"]},
            "receipt_chain": [],
        })

        result = target_protocol.handle_completion_report(
            {
                "handoff_id": "handoff-complete-detector-down",
                "federation_quest_id": "quest-complete-detector-down",
                "reporting_instance_id": target_identity.instance_id,
                "result": {"status": "success"},
                "receipts": [{"id": "r1", "timestamp": "2026-01-01T00:00:00+00:00"}],
            },
            authenticated_instance_id=target_identity.instance_id,
        )

        assert result["acknowledged"] is False
        assert "Contradiction detector unavailable" in result["error"]
        assert target_protocol.get_handoff_status("handoff-complete-detector-down") is not None


@pytest.mark.asyncio
async def test_initiate_handoff_emits_receipt_with_federation_quest_id(source_identity, source_topology, target_identity):
    class TransportStub:
        async def send(self, **kwargs):
            class Result:
                success = True
                body = {"accepted": True}
                latency_ms = 12
                status_code = 200
                error = ""
            return Result()

    class ReceiptManagerStub:
        def __init__(self):
            self.initiated = []

        def record_handoff_initiated(self, **kwargs):
            self.initiated.append(kwargs)

    receipt_mgr = ReceiptManagerStub()
    protocol = HandoffProtocol(
        identity=source_identity,
        transport=TransportStub(),
        topology=source_topology,
        receipt_mgr=receipt_mgr,
        current_soul_provider=lambda: _make_soul(),
    )

    result = await protocol.initiate_handoff(
        target_instance_id=target_identity.instance_id,
        task_context={"goal": "delegate"},
        soul_context=_make_soul().model_dump(),
        contract={"success_criteria": ["done"]},
        federation_quest_id="fed-quest-1",
    )

    assert result.success is True
    assert receipt_mgr.initiated == [{
        "handoff_id": result.handoff_id,
        "target_instance_id": target_identity.instance_id,
        "federation_quest_id": "fed-quest-1",
    }]


class TestHandoffPackage:
    def test_to_dict(self):
        pkg = HandoffPackage(
            handoff_id="h-1",
            federation_quest_id="q-1",
            source_instance_id="src",
            target_instance_id="tgt",
            task_context={"goal": "test"},
        )
        d = pkg.to_dict()
        assert d["handoff_id"] == "h-1"
        assert d["task_context"]["goal"] == "test"

    def test_auto_generates_id(self):
        pkg = HandoffPackage()
        assert pkg.handoff_id  # Not empty
        assert len(pkg.handoff_id) == 36  # UUID format


class TestPersistence:
    def test_active_handoffs_survive_reopen(self, target_identity, source_identity):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "handoffs.json")
            topology = TopologyRegistry(self_instance_id=target_identity.instance_id)
            topology.register_peer(
                instance_id=source_identity.instance_id,
                address="http://source:8000",
                role="root",
            )

            protocol1 = HandoffProtocol(
                identity=target_identity,
                transport=None,
                topology=topology,
                current_soul_provider=lambda: _make_soul(),
                persistence_path=path,
            )
            protocol1.handle_handoff_initiation({
                "handoff_id": "handoff-persisted",
                "federation_quest_id": "quest-persisted",
                "source_instance_id": source_identity.instance_id,
                "task_context": {"goal": "persist"},
                "soul_context": _make_soul().model_dump(),
                "contract": {"success_criteria": ["done"]},
                "receipt_chain": [],
            })

            protocol2 = HandoffProtocol(
                identity=target_identity,
                transport=None,
                topology=topology,
                current_soul_provider=lambda: _make_soul(),
                persistence_path=path,
            )
            status = protocol2.get_handoff_status("handoff-persisted")
            assert status is not None
            assert status["federation_quest_id"] == "quest-persisted"
