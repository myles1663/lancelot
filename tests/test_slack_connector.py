"""
Tests for Prompts 37-38: SlackConnector (Read + Write).
"""

import pytest
from src.connectors.connectors.slack import SlackConnector
from src.connectors.models import HTTPMethod
from src.core.governance.models import RiskTier


@pytest.fixture
def slack():
    return SlackConnector()


class TestManifest:
    def test_validates(self, slack):
        slack.manifest.validate()

    def test_target_domains(self, slack):
        assert slack.manifest.target_domains == ["slack.com"]


class TestReadOperations:
    def test_three_read_ops(self, slack):
        ops = [o for o in slack.get_operations() if o.capability == "connector.read"]
        assert len(ops) == 3

    def test_read_channels_is_t0(self, slack):
        ops = {o.id: o for o in slack.get_operations()}
        assert ops["read_channels"].default_tier == RiskTier.T0_INERT

    def test_read_messages_is_t1(self, slack):
        ops = {o.id: o for o in slack.get_operations()}
        assert ops["read_messages"].default_tier == RiskTier.T1_REVERSIBLE

    def test_read_channels_url(self, slack):
        result = slack.execute("read_channels", {})
        assert "conversations.list" in result.url

    def test_read_messages_url(self, slack):
        result = slack.execute("read_messages", {"channel": "C123"})
        assert "conversations.history" in result.url
        assert "channel=C123" in result.url

    def test_read_threads_url(self, slack):
        result = slack.execute("read_threads", {"channel": "C123", "thread_ts": "123.456"})
        assert "conversations.replies" in result.url
        assert "ts=123.456" in result.url

    def test_all_have_credential_key(self, slack):
        for op_id, params in [
            ("read_channels", {}),
            ("read_messages", {"channel": "C1"}),
            ("read_threads", {"channel": "C1", "thread_ts": "1"}),
        ]:
            assert slack.execute(op_id, params).credential_vault_key == "slack.bot_token"


class TestWriteOperations:
    def test_seven_total_operations(self, slack):
        assert len(slack.get_operations()) == 7

    def test_post_message_t2(self, slack):
        ops = {o.id: o for o in slack.get_operations()}
        assert ops["post_message"].default_tier == RiskTier.T2_CONTROLLED

    def test_delete_message_t3(self, slack):
        ops = {o.id: o for o in slack.get_operations()}
        assert ops["delete_message"].default_tier == RiskTier.T3_IRREVERSIBLE

    def test_post_message_url(self, slack):
        result = slack.execute("post_message", {"channel": "C123", "text": "hello"})
        assert "chat.postMessage" in result.url
        assert result.method == HTTPMethod.POST
        assert result.body["channel"] == "C123"
        assert result.body["text"] == "hello"

    def test_post_message_with_thread(self, slack):
        result = slack.execute("post_message", {
            "channel": "C123", "text": "reply", "thread_ts": "123.456",
        })
        assert result.body["thread_ts"] == "123.456"

    def test_add_reaction_url(self, slack):
        result = slack.execute("add_reaction", {
            "channel": "C123", "timestamp": "123.456", "name": "thumbsup",
        })
        assert "reactions.add" in result.url
        assert result.body["name"] == "thumbsup"

    def test_delete_message_url(self, slack):
        result = slack.execute("delete_message", {"channel": "C123", "ts": "123.456"})
        assert "chat.delete" in result.url

    def test_post_message_reversible(self, slack):
        ops = {o.id: o for o in slack.get_operations()}
        assert ops["post_message"].reversible is True

    def test_add_reaction_idempotent(self, slack):
        ops = {o.id: o for o in slack.get_operations()}
        assert ops["add_reaction"].idempotent is True
