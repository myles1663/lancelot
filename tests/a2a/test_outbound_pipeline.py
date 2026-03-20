# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""Unit tests for the Outbound A2A Governance Pipeline."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "core"))

import pytest
from unittest.mock import MagicMock, patch

from src.a2a.types import (
    RemoteAgent, A2ATaskStatus, AgentFramework, RemoteAgentStatus,
)
from src.a2a.outbound_pipeline import OutboundPipeline, DelegationResult


# ── Helpers ─────────────────────────────────────────────────

def _make_mock_soul(
    allow_outbound=True,
    allowed_targets=None,
    max_delegation_depth=2,
):
    soul = MagicMock()
    soul.version = "1.0.0"

    outbound = MagicMock()
    outbound.allow_outbound = allow_outbound
    outbound.allowed_targets = allowed_targets or []
    outbound.max_delegation_depth = max_delegation_depth
    soul.outbound_a2a_permissions = outbound

    return soul


def _make_agent(
    agent_id="target-1",
    display_name="Target One",
    agent_card_url="https://agent.example.com/.well-known/agent.json",
    agent_framework="crewai",
    outbound_trust_tier=2,
    status="active",
    network_allowlist_entries=None,
):
    allowlist = network_allowlist_entries if network_allowlist_entries is not None else ["agent.example.com"]
    return RemoteAgent(
        agent_id=agent_id,
        display_name=display_name,
        agent_card_url=agent_card_url,
        agent_framework=agent_framework,
        outbound_trust_tier=outbound_trust_tier,
        status=status,
        network_allowlist_entries=allowlist,
    )


def _make_registry(agents=None):
    registry = MagicMock()
    store = {}
    if agents:
        for a in agents:
            store[a.agent_id] = a
    registry.get.side_effect = lambda aid: store.get(aid)
    registry.update_interaction.return_value = None
    return registry


def _make_receipt_service():
    svc = MagicMock()
    svc.create.return_value = None
    return svc


# ── Stage 1: Remote Agent Resolution ──────────────────────

class TestAgentResolution:
    def test_registered_agent_resolved(self):
        agent = _make_agent()
        pipeline = OutboundPipeline(
            registry=_make_registry([agent]),
            receipt_service=_make_receipt_service(),
            soul=_make_mock_soul(),
        )
        result = pipeline.delegate("target-1", "Do something")
        assert result.success

    def test_unknown_agent_fails(self):
        pipeline = OutboundPipeline(
            registry=_make_registry(),
            receipt_service=_make_receipt_service(),
            soul=_make_mock_soul(),
        )
        result = pipeline.delegate("nonexistent", "Do something")
        assert not result.success
        assert result.stage_blocked == "agent_resolution"
        assert result.block_reason == "AGENT_NOT_REGISTERED"

    def test_suspended_agent_fails(self):
        agent = _make_agent(status="suspended")
        pipeline = OutboundPipeline(
            registry=_make_registry([agent]),
            receipt_service=_make_receipt_service(),
            soul=_make_mock_soul(),
        )
        result = pipeline.delegate("target-1", "Do something")
        assert not result.success
        assert result.block_reason == "AGENT_SUSPENDED"

    def test_no_registry_fails(self):
        pipeline = OutboundPipeline(
            registry=None,
            receipt_service=_make_receipt_service(),
            soul=_make_mock_soul(),
        )
        result = pipeline.delegate("target-1", "Do something")
        assert not result.success


# ── Stage 2: Soul Evaluation ──────────────────────────────

class TestSoulEvaluation:
    def test_soul_allows_outbound(self):
        agent = _make_agent()
        pipeline = OutboundPipeline(
            registry=_make_registry([agent]),
            receipt_service=_make_receipt_service(),
            soul=_make_mock_soul(),
        )
        result = pipeline.delegate("target-1", "Do something")
        assert result.success

    def test_allow_outbound_false_blocks(self):
        agent = _make_agent()
        pipeline = OutboundPipeline(
            registry=_make_registry([agent]),
            receipt_service=_make_receipt_service(),
            soul=_make_mock_soul(allow_outbound=False),
        )
        result = pipeline.delegate("target-1", "Do something")
        assert not result.success
        assert result.stage_blocked == "soul_evaluation"
        assert result.block_reason == "SOUL_DENIED"

    def test_no_outbound_perms_blocks(self):
        agent = _make_agent()
        soul = MagicMock()
        soul.outbound_a2a_permissions = None
        pipeline = OutboundPipeline(
            registry=_make_registry([agent]),
            receipt_service=_make_receipt_service(),
            soul=soul,
        )
        result = pipeline.delegate("target-1", "Do something")
        assert not result.success

    def test_allowed_targets_permits_matching_agent(self):
        agent = _make_agent()
        soul = _make_mock_soul(
            allowed_targets=[{"agent_id": "target-1", "allowed_task_types": ["*"]}],
        )
        pipeline = OutboundPipeline(
            registry=_make_registry([agent]),
            receipt_service=_make_receipt_service(),
            soul=soul,
        )
        result = pipeline.delegate("target-1", "Do something")
        assert result.success

    def test_allowed_targets_blocks_non_matching(self):
        agent = _make_agent()
        soul = _make_mock_soul(
            allowed_targets=[{"agent_id": "other-agent", "allowed_task_types": ["*"]}],
        )
        pipeline = OutboundPipeline(
            registry=_make_registry([agent]),
            receipt_service=_make_receipt_service(),
            soul=soul,
        )
        result = pipeline.delegate("target-1", "Do something")
        assert not result.success
        assert result.block_reason == "SOUL_DENIED"

    def test_allowed_targets_framework_match(self):
        agent = _make_agent(agent_framework="crewai")
        soul = _make_mock_soul(
            allowed_targets=[{"agent_framework": "crewai", "allowed_task_types": ["*"]}],
        )
        pipeline = OutboundPipeline(
            registry=_make_registry([agent]),
            receipt_service=_make_receipt_service(),
            soul=soul,
        )
        result = pipeline.delegate("target-1", "Do something")
        assert result.success


# ── Stage 3: Network Allowlist ─────────────────────────────

class TestNetworkAllowlist:
    def test_agent_with_allowlist_passes(self):
        agent = _make_agent(network_allowlist_entries=["agent.example.com"])
        pipeline = OutboundPipeline(
            registry=_make_registry([agent]),
            receipt_service=_make_receipt_service(),
            soul=_make_mock_soul(),
        )
        result = pipeline.delegate("target-1", "Do something")
        assert result.success

    def test_agent_without_allowlist_blocked(self):
        agent = _make_agent(network_allowlist_entries=[])
        pipeline = OutboundPipeline(
            registry=_make_registry([agent]),
            receipt_service=_make_receipt_service(),
            soul=_make_mock_soul(),
        )
        result = pipeline.delegate("target-1", "Do something")
        assert not result.success
        assert result.stage_blocked == "network_allowlist"

    def test_agent_without_card_url_blocked(self):
        agent = _make_agent(agent_card_url="", network_allowlist_entries=["x.com"])
        pipeline = OutboundPipeline(
            registry=_make_registry([agent]),
            receipt_service=_make_receipt_service(),
            soul=_make_mock_soul(),
        )
        result = pipeline.delegate("target-1", "Do something")
        assert not result.success
        assert result.stage_blocked == "network_allowlist"


# ── Stage 4: PII Scrubbing ────────────────────────────────

class TestPIIScrubbing:
    def test_ssn_scrubbed(self):
        pipeline = OutboundPipeline(
            registry=MagicMock(),
            receipt_service=_make_receipt_service(),
            soul=_make_mock_soul(),
        )
        result = pipeline._scrub_outbound("My SSN is 123-45-6789")
        assert "123-45-6789" not in result
        assert "[REDACTED]" in result

    def test_credit_card_scrubbed(self):
        pipeline = OutboundPipeline(
            registry=MagicMock(),
            receipt_service=_make_receipt_service(),
            soul=_make_mock_soul(),
        )
        result = pipeline._scrub_outbound("Card: 1234567890123456")
        assert "1234567890123456" not in result
        assert "[REDACTED]" in result

    def test_email_scrubbed(self):
        pipeline = OutboundPipeline(
            registry=MagicMock(),
            receipt_service=_make_receipt_service(),
            soul=_make_mock_soul(),
        )
        result = pipeline._scrub_outbound("Contact user@example.com for details")
        assert "user@example.com" not in result
        assert "[REDACTED]" in result

    def test_clean_content_unchanged(self):
        pipeline = OutboundPipeline(
            registry=MagicMock(),
            receipt_service=_make_receipt_service(),
            soul=_make_mock_soul(),
        )
        content = "Please analyze this quarterly earnings report"
        result = pipeline._scrub_outbound(content)
        assert result == content


# ── Stage 5: Risk Classification ──────────────────────────

class TestRiskClassification:
    def test_financial_task_escalated_to_t3(self):
        agent = _make_agent(outbound_trust_tier=1)
        pipeline = OutboundPipeline(
            registry=_make_registry([agent]),
            receipt_service=_make_receipt_service(),
            soul=_make_mock_soul(),
        )
        risk = pipeline._classify_risk(agent, "payment")
        assert risk >= 3

    def test_financial_types(self):
        agent = _make_agent(outbound_trust_tier=1)
        pipeline = OutboundPipeline(
            registry=MagicMock(),
            receipt_service=_make_receipt_service(),
            soul=_make_mock_soul(),
        )
        for task_type in ["payment", "transfer", "billing", "financial"]:
            risk = pipeline._classify_risk(agent, task_type)
            assert risk >= 3, f"Financial type '{task_type}' not escalated"

    def test_unknown_framework_escalation(self):
        agent = _make_agent(agent_framework="unknown", outbound_trust_tier=1)
        pipeline = OutboundPipeline(
            registry=MagicMock(),
            receipt_service=_make_receipt_service(),
            soul=_make_mock_soul(),
        )
        risk = pipeline._classify_risk(agent, "general")
        assert risk >= 2


# ── Stage 6: T3 Approval Gate ─────────────────────────────

class TestT3OutboundApproval:
    def test_t3_requires_approval(self):
        agent = _make_agent(outbound_trust_tier=3)
        pipeline = OutboundPipeline(
            registry=_make_registry([agent]),
            receipt_service=_make_receipt_service(),
            soul=_make_mock_soul(),
        )
        result = pipeline.delegate("target-1", "Do something")
        assert result.requires_approval
        assert not result.success

    def test_non_t3_bypasses_approval(self):
        agent = _make_agent(outbound_trust_tier=1)
        pipeline = OutboundPipeline(
            registry=_make_registry([agent]),
            receipt_service=_make_receipt_service(),
            soul=_make_mock_soul(),
        )
        result = pipeline.delegate("target-1", "Do something")
        assert result.success
        assert not result.requires_approval


# ── Stage 7: Credential injection (stub) ──────────────────

class TestCredentialInjection:
    def test_stub_delegation_succeeds(self):
        """Credential injection is a stub — delegation should succeed without credentials."""
        agent = _make_agent()
        pipeline = OutboundPipeline(
            registry=_make_registry([agent]),
            receipt_service=_make_receipt_service(),
            soul=_make_mock_soul(),
        )
        result = pipeline.delegate("target-1", "Do something")
        assert result.success


# ── Stage 8: Delegation execution ─────────────────────────

class TestDelegationExecution:
    def test_delegation_returns_completed(self):
        agent = _make_agent()
        pipeline = OutboundPipeline(
            registry=_make_registry([agent]),
            receipt_service=_make_receipt_service(),
            soul=_make_mock_soul(),
        )
        result = pipeline.delegate("target-1", "Do something")
        assert result.status == A2ATaskStatus.COMPLETED.value

    def test_delegation_exception_handled(self):
        agent = _make_agent()
        pipeline = OutboundPipeline(
            registry=_make_registry([agent]),
            receipt_service=_make_receipt_service(),
            soul=_make_mock_soul(),
        )
        pipeline._execute_delegation = MagicMock(side_effect=Exception("Connection refused"))
        result = pipeline.delegate("target-1", "Do something")
        assert not result.success
        assert result.status == A2ATaskStatus.FAILED.value
        assert "Connection refused" in result.error


# ── Stage 9: Response Inspection ──────────────────────────

class TestResponseInspection:
    def test_scrubs_pii_from_response_artifacts(self):
        pipeline = OutboundPipeline(
            registry=MagicMock(),
            receipt_service=_make_receipt_service(),
            soul=_make_mock_soul(),
        )
        artifacts = [
            {"parts": [{"type": "text", "text": "SSN: 123-45-6789"}]},
        ]
        scrubbed = pipeline._scrub_response_artifacts(artifacts)
        assert "123-45-6789" not in scrubbed[0]["parts"][0]["text"]
        assert "[REDACTED]" in scrubbed[0]["parts"][0]["text"]


# ── Stage 10: Receipt and Trust Update ────────────────────

class TestReceiptAndTrust:
    def test_receipt_generated_on_success(self):
        agent = _make_agent()
        svc = _make_receipt_service()
        pipeline = OutboundPipeline(
            registry=_make_registry([agent]),
            receipt_service=svc,
            soul=_make_mock_soul(),
        )
        pipeline.delegate("target-1", "Do something")
        assert svc.create.call_count >= 2  # delegation_sent + delegation_completed

    def test_trust_updated_on_success(self):
        agent = _make_agent()
        registry = _make_registry([agent])
        pipeline = OutboundPipeline(
            registry=registry,
            receipt_service=_make_receipt_service(),
            soul=_make_mock_soul(),
        )
        pipeline.delegate("target-1", "Do something")
        registry.update_interaction.assert_called_with("target-1", "completed", "outbound")

    def test_trust_updated_on_failure(self):
        agent = _make_agent()
        registry = _make_registry([agent])
        pipeline = OutboundPipeline(
            registry=registry,
            receipt_service=_make_receipt_service(),
            soul=_make_mock_soul(),
        )
        pipeline._execute_delegation = MagicMock(side_effect=Exception("error"))
        pipeline.delegate("target-1", "Do something")
        registry.update_interaction.assert_called_with("target-1", "failed", "outbound")


# ── Delegation Depth ──────────────────────────────────────

class TestDelegationDepth:
    def test_depth_limit_exceeded(self):
        agent = _make_agent()
        pipeline = OutboundPipeline(
            registry=_make_registry([agent]),
            receipt_service=_make_receipt_service(),
            soul=_make_mock_soul(max_delegation_depth=2),
        )
        result = pipeline.delegate("target-1", "Do something", delegation_depth=2)
        assert not result.success
        assert result.stage_blocked == "delegation_depth"
        assert result.block_reason == "DEPTH_EXCEEDED"

    def test_within_depth_limit_allowed(self):
        agent = _make_agent()
        pipeline = OutboundPipeline(
            registry=_make_registry([agent]),
            receipt_service=_make_receipt_service(),
            soul=_make_mock_soul(max_delegation_depth=3),
        )
        result = pipeline.delegate("target-1", "Do something", delegation_depth=1)
        assert result.success

    def test_max_delegation_depth_enforcement(self):
        agent = _make_agent()
        pipeline = OutboundPipeline(
            registry=_make_registry([agent]),
            receipt_service=_make_receipt_service(),
            soul=_make_mock_soul(max_delegation_depth=1),
        )
        result = pipeline.delegate("target-1", "Do something", delegation_depth=1)
        assert not result.success
        assert result.block_reason == "DEPTH_EXCEEDED"


# ── DelegationResult ──────────────────────────────────────

class TestDelegationResult:
    def test_to_dict(self):
        result = DelegationResult(
            success=True,
            task_id="t1",
            target_agent_id="a1",
            status="completed",
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["task_id"] == "t1"
        assert d["target_agent_id"] == "a1"
