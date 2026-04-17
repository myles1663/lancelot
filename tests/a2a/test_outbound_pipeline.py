# Lancelot - A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""Unit tests for the hardened outbound A2A governance pipeline."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.a2a.outbound_pipeline import OutboundPipeline
from src.a2a.types import A2ATaskStatus, AgentFramework, RemoteAgent


def _make_mock_soul(
    *,
    allow_outbound: bool = True,
    allowed_targets=None,
    max_delegation_depth: int = 2,
    require_agent_card_verification: bool = True,
):
    soul = MagicMock()
    outbound = MagicMock()
    outbound.allow_outbound = allow_outbound
    outbound.allowed_targets = allowed_targets or []
    outbound.max_delegation_depth = max_delegation_depth
    outbound.require_agent_card_verification = require_agent_card_verification
    soul.outbound_a2a_permissions = outbound
    return soul


def _make_agent(
    *,
    agent_id: str = "target-1",
    auth_type: str = "bearer_token",
    credentials_ref: str = "a2a.target-1",
    agent_card_url: str = "https://agent.example.com/.well-known/agent.json",
    agent_framework: str = "crewai",
    outbound_trust_tier: int = 2,
    status: str = "active",
    network_allowlist_entries=None,
) -> RemoteAgent:
    return RemoteAgent(
        agent_id=agent_id,
        display_name="Target One",
        auth_type=auth_type,
        credentials_ref=credentials_ref,
        agent_card_url=agent_card_url,
        agent_framework=agent_framework,
        outbound_trust_tier=outbound_trust_tier,
        status=status,
        network_allowlist_entries=network_allowlist_entries or ["agent.example.com"],
    )


def _make_registry(agents=None):
    registry = MagicMock()
    store = {agent.agent_id: agent for agent in (agents or [])}
    registry.get.side_effect = lambda agent_id: store.get(agent_id)
    registry.update_interaction.return_value = None
    return registry


def _make_vault(secret_map=None):
    vault = MagicMock()
    secrets = {"a2a.target-1": "remote-secret"} if secret_map is None else secret_map
    vault.retrieve.side_effect = lambda key: secrets[key]
    return vault


def _make_client(*, verify: bool = True, send_response=None, poll_responses=None):
    client = MagicMock()
    client.verify_agent_card.return_value = verify
    client.assess_agent_card.return_value = {"allowed": verify, "reason": "failed" if not verify else ""}
    client.send_task.return_value = send_response or {"id": "remote-task", "status": A2ATaskStatus.COMPLETED.value, "artifacts": []}
    poll_sequence = list(poll_responses or [])

    def _poll(*args, **kwargs):
        if poll_sequence:
            return poll_sequence.pop(0)
        return {"id": "remote-task", "status": A2ATaskStatus.COMPLETED.value, "artifacts": []}

    client.poll_task_status.side_effect = _poll
    return client


def _make_pipeline(
    *,
    agent: RemoteAgent | None = None,
    soul=None,
    vault=None,
    client=None,
    receipt_service=None,
):
    return OutboundPipeline(
        registry=_make_registry([agent or _make_agent()]),
        receipt_service=receipt_service or MagicMock(),
        soul=soul or _make_mock_soul(),
        vault=vault or _make_vault(),
        a2a_client=client or _make_client(),
    )


class TestAgentResolution:
    def test_registered_agent_resolved_and_delegated(self):
        pipeline = _make_pipeline(agent=_make_agent())
        result = pipeline.delegate("target-1", "Do something")
        assert result.success
        assert result.status == A2ATaskStatus.COMPLETED.value

    def test_unknown_agent_fails(self):
        pipeline = _make_pipeline(agent=None)
        result = pipeline.delegate("unknown", "Do something")
        assert not result.success
        assert result.block_reason == "AGENT_NOT_REGISTERED"

    def test_suspended_agent_fails(self):
        pipeline = _make_pipeline(agent=_make_agent(status="suspended"))
        result = pipeline.delegate("target-1", "Do something")
        assert not result.success
        assert result.block_reason == "AGENT_SUSPENDED"


class TestSoulAndVerification:
    def test_allow_outbound_false_blocks(self):
        pipeline = _make_pipeline(agent=_make_agent(), soul=_make_mock_soul(allow_outbound=False))
        result = pipeline.delegate("target-1", "Do something")
        assert not result.success
        assert result.stage_blocked == "soul_evaluation"

    def test_allowed_targets_permits_matching_agent(self):
        soul = _make_mock_soul(allowed_targets=[{"agent_id": "target-1", "allowed_task_types": ["*"]}])
        pipeline = _make_pipeline(agent=_make_agent(), soul=soul)
        result = pipeline.delegate("target-1", "Do something")
        assert result.success

    def test_allowed_targets_blocks_non_matching_agent(self):
        soul = _make_mock_soul(allowed_targets=[{"agent_id": "other", "allowed_task_types": ["*"]}])
        pipeline = _make_pipeline(agent=_make_agent(), soul=soul)
        result = pipeline.delegate("target-1", "Do something")
        assert not result.success
        assert result.block_reason == "SOUL_DENIED"

    def test_agent_card_verification_failure_blocks(self):
        pipeline = _make_pipeline(agent=_make_agent(), client=_make_client(verify=False))
        result = pipeline.delegate("target-1", "Do something")
        assert not result.success
        assert result.stage_blocked == "agent_card_verification"

    def test_unpinned_agent_card_blocks(self):
        client = _make_client()
        client.assess_agent_card.return_value = {
            "allowed": False,
            "reason": "Agent Card is not pinned; operator verification is required.",
        }
        pipeline = _make_pipeline(agent=_make_agent(), client=client)
        result = pipeline.delegate("target-1", "Do something")
        assert not result.success
        assert result.stage_blocked == "agent_card_verification"


class TestCredentialInjection:
    def test_bearer_credentials_are_resolved_from_vault(self):
        client = _make_client()
        pipeline = _make_pipeline(agent=_make_agent(), client=client)
        result = pipeline.delegate("target-1", "Do something")
        assert result.success
        call = client.send_task.call_args
        assert call.kwargs["credentials"] == {"type": "bearer_token", "token": "remote-secret"}

    def test_json_api_key_credentials_are_resolved(self):
        agent = _make_agent(auth_type="api_key")
        client = _make_client()
        pipeline = _make_pipeline(
            agent=agent,
            client=client,
            vault=_make_vault({"a2a.target-1": '{"api_key":"my-key"}'}),
        )
        result = pipeline.delegate("target-1", "Do something")
        assert result.success
        call = client.send_task.call_args
        assert call.kwargs["credentials"] == {"type": "api_key", "key": "my-key"}

    def test_missing_credentials_block_when_auth_required(self):
        pipeline = _make_pipeline(agent=_make_agent(), vault=_make_vault({}))
        result = pipeline.delegate("target-1", "Do something")
        assert not result.success
        assert result.stage_blocked == "credential_injection"


class TestDelegationExecution:
    def test_working_remote_task_is_polled_to_completion(self):
        client = _make_client(
            send_response={"id": "remote-task", "status": A2ATaskStatus.WORKING.value},
            poll_responses=[{"id": "remote-task", "status": A2ATaskStatus.COMPLETED.value, "artifacts": []}],
        )
        pipeline = _make_pipeline(agent=_make_agent(), client=client)
        result = pipeline.delegate("target-1", "Do something")
        assert result.success
        assert result.status == A2ATaskStatus.COMPLETED.value
        assert client.poll_task_status.called

    def test_delegation_exception_updates_failure_trust(self):
        client = _make_client()
        client.send_task.side_effect = RuntimeError("Connection refused")
        registry = _make_registry([_make_agent()])
        pipeline = OutboundPipeline(
            registry=registry,
            receipt_service=MagicMock(),
            soul=_make_mock_soul(),
            vault=_make_vault(),
            a2a_client=client,
        )
        result = pipeline.delegate("target-1", "Do something")
        assert not result.success
        assert "Connection refused" in result.error
        registry.update_interaction.assert_called_with("target-1", "failed", "outbound")


class TestRiskAndDepth:
    def test_t3_outbound_requires_approval(self):
        pipeline = _make_pipeline(agent=_make_agent(outbound_trust_tier=3))
        result = pipeline.delegate("target-1", "Do something")
        assert not result.success
        assert result.requires_approval

    def test_financial_task_is_escalated(self):
        agent = _make_agent(outbound_trust_tier=1)
        pipeline = _make_pipeline(agent=agent)
        assert pipeline._classify_risk(agent, "payment") >= 3

    def test_depth_limit_is_enforced(self):
        pipeline = _make_pipeline(
            agent=_make_agent(),
            soul=_make_mock_soul(max_delegation_depth=1),
        )
        result = pipeline.delegate("target-1", "Do something", delegation_depth=1)
        assert not result.success
        assert result.block_reason == "DEPTH_EXCEEDED"

    def test_network_allowlist_requires_matching_host(self):
        agent = _make_agent(
            agent_card_url="https://evil.example/.well-known/agent.json",
            network_allowlist_entries=["allowed.example"],
        )
        pipeline = _make_pipeline(agent=agent)
        result = pipeline.delegate("target-1", "Do something")
        assert not result.success
        assert result.block_reason == "NETWORK_BLOCKED"


class TestReceiptsAndTrust:
    def test_successful_delegation_updates_trust_and_receipts(self):
        svc = MagicMock()
        registry = _make_registry([_make_agent()])
        pipeline = OutboundPipeline(
            registry=registry,
            receipt_service=svc,
            soul=_make_mock_soul(),
            vault=_make_vault(),
            a2a_client=_make_client(),
        )
        result = pipeline.delegate("target-1", "Do something")
        assert result.success
        assert svc.create.call_count >= 2
        registry.update_interaction.assert_called_with("target-1", "completed", "outbound")

    def test_outbound_receipts_carry_operator_identity(self):
        svc = MagicMock()
        pipeline = _make_pipeline(receipt_service=svc)
        result = pipeline.delegate(
            "target-1",
            "Do something",
            operator_id="op-123",
            session_id="sess-456",
        )
        assert result.success
        receipts = [call.args[0] for call in svc.create.call_args_list]
        assert receipts
        assert all(r.operator_id == "op-123" for r in receipts)
        assert all(r.session_id == "sess-456" for r in receipts)
