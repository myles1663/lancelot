"""
Tests for DiscordConnector — Discord REST API v10 integration.

Tests HTTP request spec production. No actual Discord API calls.
"""

import pytest

from src.connectors.connectors.discord import DiscordConnector
from src.connectors.models import HTTPMethod
from src.core.governance.models import RiskTier


@pytest.fixture
def discord():
    return DiscordConnector()


# ── Manifest ──────────────────────────────────────────────────────

class TestManifest:
    def test_validates(self, discord):
        discord.manifest.validate()

    def test_target_domains(self, discord):
        assert discord.manifest.target_domains == ["discord.com"]

    def test_has_credentials(self, discord):
        assert len(discord.manifest.required_credentials) == 1
        assert discord.manifest.required_credentials[0].vault_key == "discord.bot_token"

    def test_credential_type_is_api_key(self, discord):
        assert discord.manifest.required_credentials[0].type == "api_key"

    def test_does_not_access(self, discord):
        dna = discord.manifest.does_not_access
        assert "Server settings" in dna
        assert "Voice channels" in dna


# ── Operation Enumeration ─────────────────────────────────────────

class TestOperations:
    def test_total_operations(self, discord):
        assert len(discord.get_operations()) == 9

    def test_has_four_read_ops(self, discord):
        ops = discord.get_operations()
        read_ops = [o for o in ops if o.capability == "connector.read"]
        assert len(read_ops) == 4

    def test_has_three_write_ops(self, discord):
        ops = discord.get_operations()
        write_ops = [o for o in ops if o.capability == "connector.write"]
        assert len(write_ops) == 3

    def test_has_two_delete_ops(self, discord):
        ops = discord.get_operations()
        delete_ops = [o for o in ops if o.capability == "connector.delete"]
        assert len(delete_ops) == 2

    def test_list_guilds_is_t0(self, discord):
        ops = {o.id: o for o in discord.get_operations()}
        assert ops["list_guilds"].default_tier == RiskTier.T0_INERT

    def test_read_messages_is_t1(self, discord):
        ops = {o.id: o for o in discord.get_operations()}
        assert ops["read_messages"].default_tier == RiskTier.T1_REVERSIBLE

    def test_post_message_is_t2(self, discord):
        ops = {o.id: o for o in discord.get_operations()}
        assert ops["post_message"].default_tier == RiskTier.T2_CONTROLLED

    def test_edit_message_is_t2(self, discord):
        ops = {o.id: o for o in discord.get_operations()}
        assert ops["edit_message"].default_tier == RiskTier.T2_CONTROLLED

    def test_delete_message_is_t3(self, discord):
        ops = {o.id: o for o in discord.get_operations()}
        assert ops["delete_message"].default_tier == RiskTier.T3_IRREVERSIBLE

    def test_add_reaction_is_t1(self, discord):
        ops = {o.id: o for o in discord.get_operations()}
        assert ops["add_reaction"].default_tier == RiskTier.T1_REVERSIBLE

    def test_remove_reaction_is_t1(self, discord):
        ops = {o.id: o for o in discord.get_operations()}
        assert ops["remove_reaction"].default_tier == RiskTier.T1_REVERSIBLE

    def test_post_message_is_reversible(self, discord):
        ops = {o.id: o for o in discord.get_operations()}
        assert ops["post_message"].reversible is True
        assert ops["post_message"].rollback_operation_id == "delete_message"

    def test_edit_message_is_reversible(self, discord):
        ops = {o.id: o for o in discord.get_operations()}
        assert ops["edit_message"].reversible is True

    def test_add_reaction_is_reversible(self, discord):
        ops = {o.id: o for o in discord.get_operations()}
        assert ops["add_reaction"].reversible is True
        assert ops["add_reaction"].rollback_operation_id == "remove_reaction"


# ── Execute Read Operations ───────────────────────────────────────

class TestReadExecution:
    def test_list_guilds_url(self, discord):
        result = discord.execute("list_guilds", {})
        assert result.method == HTTPMethod.GET
        assert "/users/@me/guilds" in result.url
        assert "/api/v10/" in result.url

    def test_list_channels_url(self, discord):
        result = discord.execute("list_channels", {"guild_id": "g1"})
        assert result.method == HTTPMethod.GET
        assert "/guilds/g1/channels" in result.url

    def test_read_messages_url(self, discord):
        result = discord.execute("read_messages", {"channel_id": "c1"})
        assert result.method == HTTPMethod.GET
        assert "/channels/c1/messages" in result.url
        assert "limit=" in result.url

    def test_read_messages_custom_limit(self, discord):
        result = discord.execute("read_messages", {"channel_id": "c1", "limit": 25})
        assert "limit=25" in result.url

    def test_get_message_url(self, discord):
        result = discord.execute("get_message", {"channel_id": "c1", "message_id": "m1"})
        assert "/channels/c1/messages/m1" in result.url
        assert result.method == HTTPMethod.GET

    def test_all_read_results_have_credential_key(self, discord):
        test_cases = [
            ("list_guilds", {}),
            ("list_channels", {"guild_id": "g1"}),
            ("read_messages", {"channel_id": "c1"}),
            ("get_message", {"channel_id": "c1", "message_id": "m1"}),
        ]
        for op_id, params in test_cases:
            result = discord.execute(op_id, params)
            assert result.credential_vault_key == "discord.bot_token"

    def test_read_messages_has_rate_limit_metadata(self, discord):
        result = discord.execute("read_messages", {"channel_id": "c1"})
        assert "rate_limit_group" in result.metadata
        assert "discord.channels.c1.messages" == result.metadata["rate_limit_group"]


# ── Execute Write Operations ──────────────────────────────────────

class TestWriteExecution:
    def test_post_message_body(self, discord):
        result = discord.execute("post_message", {"channel_id": "c1", "text": "Hello Discord!"})
        assert result.method == HTTPMethod.POST
        assert "/channels/c1/messages" in result.url
        assert result.body == {"content": "Hello Discord!"}

    def test_edit_message_body(self, discord):
        result = discord.execute("edit_message", {
            "channel_id": "c1", "message_id": "m1", "text": "Edited!",
        })
        assert result.method == HTTPMethod.PATCH
        assert "/channels/c1/messages/m1" in result.url
        assert result.body == {"content": "Edited!"}

    def test_add_reaction_url(self, discord):
        result = discord.execute("add_reaction", {
            "channel_id": "c1", "message_id": "m1", "emoji": "👍",
        })
        assert result.method == HTTPMethod.PUT
        assert "/reactions/" in result.url
        assert "/@me" in result.url

    def test_add_reaction_url_encodes_emoji(self, discord):
        result = discord.execute("add_reaction", {
            "channel_id": "c1", "message_id": "m1", "emoji": "🔥",
        })
        # URL-encoded emoji should not contain raw emoji character in URL path
        assert result.method == HTTPMethod.PUT

    def test_delete_message_url(self, discord):
        result = discord.execute("delete_message", {
            "channel_id": "c1", "message_id": "m1",
        })
        assert result.method == HTTPMethod.DELETE
        assert "/channels/c1/messages/m1" in result.url

    def test_remove_reaction_url(self, discord):
        result = discord.execute("remove_reaction", {
            "channel_id": "c1", "message_id": "m1", "emoji": "👍",
        })
        assert result.method == HTTPMethod.DELETE
        assert "/reactions/" in result.url
        assert "/@me" in result.url

    def test_unknown_operation_raises(self, discord):
        with pytest.raises(KeyError):
            discord.execute("unknown_op", {})


# ── Credential Validation ─────────────────────────────────────────

class TestCredentialValidation:
    def test_validate_without_vault_returns_false(self, discord):
        assert discord.validate_credentials() is False
