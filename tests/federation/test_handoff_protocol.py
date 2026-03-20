# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""Tests for Federation Handoff Protocol — task handoff handlers."""

import pytest

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
    )


class TestHandleHandoffInitiation:
    def test_valid_handoff_accepted(self, target_protocol, source_identity):
        result = target_protocol.handle_handoff_initiation({
            "handoff_id": "handoff-001",
            "federation_quest_id": "quest-001",
            "source_instance_id": source_identity.instance_id,
            "target_instance_id": target_protocol._identity.instance_id,
            "task_context": {"goal": "analyze data"},
            "soul_context": {"constraints": ["no-external-api"]},
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

    def test_handoff_tracked_as_active(self, target_protocol, source_identity):
        target_protocol.handle_handoff_initiation({
            "handoff_id": "handoff-tracked",
            "federation_quest_id": "quest-track",
            "source_instance_id": source_identity.instance_id,
            "task_context": {"goal": "test"},
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
            "contract": {},
            "receipt_chain": [],
        })

        # Then handle completion from source side
        result = target_protocol.handle_completion_report({
            "handoff_id": "handoff-complete",
            "federation_quest_id": "quest-done",
            "reporting_instance_id": source_identity.instance_id,
            "result": {"status": "success"},
        })
        assert result["acknowledged"]


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
