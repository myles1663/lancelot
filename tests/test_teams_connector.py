"""
Tests for TeamsConnector — Microsoft Graph API integration.

Tests HTTP request spec production. No actual Graph API calls.
"""

import pytest

from src.connectors.connectors.teams import TeamsConnector
from src.connectors.models import HTTPMethod
from src.core.governance.models import RiskTier


@pytest.fixture
def teams():
    return TeamsConnector()


# ── Manifest ──────────────────────────────────────────────────────

class TestManifest:
    def test_validates(self, teams):
        teams.manifest.validate()

    def test_target_domains(self, teams):
        assert teams.manifest.target_domains == ["graph.microsoft.com"]

    def test_has_credentials(self, teams):
        assert len(teams.manifest.required_credentials) == 1
        assert teams.manifest.required_credentials[0].vault_key == "teams.graph_token"

    def test_does_not_access(self, teams):
        dna = teams.manifest.does_not_access
        assert "Email" in dna
        assert "Calendar" in dna


# ── Operation Enumeration ─────────────────────────────────────────

class TestOperations:
    def test_total_operations(self, teams):
        assert len(teams.get_operations()) == 10

    def test_has_six_read_ops(self, teams):
        ops = teams.get_operations()
        read_ops = [o for o in ops if o.capability == "connector.read"]
        assert len(read_ops) == 6

    def test_has_three_write_ops(self, teams):
        ops = teams.get_operations()
        write_ops = [o for o in ops if o.capability == "connector.write"]
        assert len(write_ops) == 3

    def test_has_one_delete_op(self, teams):
        ops = teams.get_operations()
        delete_ops = [o for o in ops if o.capability == "connector.delete"]
        assert len(delete_ops) == 1

    def test_list_teams_is_t0(self, teams):
        ops = {o.id: o for o in teams.get_operations()}
        assert ops["list_teams"].default_tier == RiskTier.T0_INERT

    def test_list_channels_is_t0(self, teams):
        ops = {o.id: o for o in teams.get_operations()}
        assert ops["list_channels"].default_tier == RiskTier.T0_INERT

    def test_read_messages_is_t1(self, teams):
        ops = {o.id: o for o in teams.get_operations()}
        assert ops["read_messages"].default_tier == RiskTier.T1_REVERSIBLE

    def test_post_channel_message_is_t2(self, teams):
        ops = {o.id: o for o in teams.get_operations()}
        assert ops["post_channel_message"].default_tier == RiskTier.T2_CONTROLLED

    def test_send_chat_message_is_t3(self, teams):
        ops = {o.id: o for o in teams.get_operations()}
        assert ops["send_chat_message"].default_tier == RiskTier.T3_IRREVERSIBLE

    def test_delete_message_is_t3(self, teams):
        ops = {o.id: o for o in teams.get_operations()}
        assert ops["delete_message"].default_tier == RiskTier.T3_IRREVERSIBLE

    def test_post_channel_message_is_reversible(self, teams):
        ops = {o.id: o for o in teams.get_operations()}
        assert ops["post_channel_message"].reversible is True
        assert ops["post_channel_message"].rollback_operation_id == "delete_message"

    def test_send_chat_message_is_not_reversible(self, teams):
        ops = {o.id: o for o in teams.get_operations()}
        assert ops["send_chat_message"].reversible is False


# ── Execute Read Operations ───────────────────────────────────────

class TestReadExecution:
    def test_list_teams_url(self, teams):
        result = teams.execute("list_teams", {})
        assert result.method == HTTPMethod.GET
        assert "/me/joinedTeams" in result.url

    def test_list_channels_url(self, teams):
        result = teams.execute("list_channels", {"team_id": "t1"})
        assert result.method == HTTPMethod.GET
        assert "/teams/t1/channels" in result.url

    def test_read_messages_url(self, teams):
        result = teams.execute("read_messages", {"team_id": "t1", "channel_id": "c1"})
        assert result.method == HTTPMethod.GET
        assert "/teams/t1/channels/c1/messages" in result.url
        assert "$top=" in result.url

    def test_read_messages_custom_limit(self, teams):
        result = teams.execute("read_messages", {"team_id": "t1", "channel_id": "c1", "limit": 25})
        assert "$top=25" in result.url

    def test_get_message_url(self, teams):
        result = teams.execute("get_message", {"team_id": "t1", "channel_id": "c1", "message_id": "m1"})
        assert "/teams/t1/channels/c1/messages/m1" in result.url
        assert result.method == HTTPMethod.GET

    def test_read_replies_url(self, teams):
        result = teams.execute("read_replies", {"team_id": "t1", "channel_id": "c1", "message_id": "m1"})
        assert "/teams/t1/channels/c1/messages/m1/replies" in result.url

    def test_read_chat_messages_url(self, teams):
        result = teams.execute("read_chat_messages", {"chat_id": "chat1"})
        assert "/chats/chat1/messages" in result.url
        assert "$top=" in result.url

    def test_all_read_results_have_credential_key(self, teams):
        test_cases = [
            ("list_teams", {}),
            ("list_channels", {"team_id": "t1"}),
            ("read_messages", {"team_id": "t1", "channel_id": "c1"}),
            ("get_message", {"team_id": "t1", "channel_id": "c1", "message_id": "m1"}),
            ("read_replies", {"team_id": "t1", "channel_id": "c1", "message_id": "m1"}),
            ("read_chat_messages", {"chat_id": "chat1"}),
        ]
        for op_id, params in test_cases:
            result = teams.execute(op_id, params)
            assert result.credential_vault_key == "teams.graph_token"


# ── Execute Write Operations ──────────────────────────────────────

class TestWriteExecution:
    def test_post_channel_message_body(self, teams):
        result = teams.execute("post_channel_message", {
            "team_id": "t1", "channel_id": "c1", "text": "Hello Teams!",
        })
        assert result.method == HTTPMethod.POST
        assert "/teams/t1/channels/c1/messages" in result.url
        assert result.body["body"]["content"] == "Hello Teams!"
        assert result.body["body"]["contentType"] == "text"

    def test_reply_to_message_body(self, teams):
        result = teams.execute("reply_to_message", {
            "team_id": "t1", "channel_id": "c1", "message_id": "m1", "text": "Reply!",
        })
        assert result.method == HTTPMethod.POST
        assert "/messages/m1/replies" in result.url
        assert result.body["body"]["content"] == "Reply!"

    def test_send_chat_message_body(self, teams):
        result = teams.execute("send_chat_message", {
            "chat_id": "chat1", "text": "Direct message",
        })
        assert result.method == HTTPMethod.POST
        assert "/chats/chat1/messages" in result.url
        assert result.body["body"]["content"] == "Direct message"

    def test_delete_message_url(self, teams):
        result = teams.execute("delete_message", {
            "team_id": "t1", "channel_id": "c1", "message_id": "m1",
        })
        assert result.method == HTTPMethod.DELETE
        assert "/teams/t1/channels/c1/messages/m1" in result.url

    def test_unknown_operation_raises(self, teams):
        with pytest.raises(KeyError):
            teams.execute("unknown_op", {})


# ── Credential Validation ─────────────────────────────────────────

class TestCredentialValidation:
    def test_validate_without_vault_returns_false(self, teams):
        assert teams.validate_credentials() is False
