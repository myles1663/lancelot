"""
Lancelot — ActionCardFactory Unit Tests
========================================
Tests for creating ActionCards from each approval subsystem.
"""

import tempfile
import shutil
import pytest
import types

from actioncard.models import ActionCard, ActionCardType, ActionButtonStyle
from actioncard.store import ActionCardStore
from actioncard.factory import ActionCardFactory
from actioncard import factory as factory_module


@pytest.fixture
def temp_data_dir():
    temp_dir = tempfile.mkdtemp(prefix="lancelot_acf_test_")
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def factory(temp_data_dir):
    store = ActionCardStore(data_dir=temp_data_dir)
    f = ActionCardFactory(card_store=store)
    yield f
    store.close()


class TestActionCardFactory:

    def test_from_sentry_request(self, factory):
        """Creates correct card for T3 sentry action."""
        card = factory.from_sentry_request(
            req_id="sentry-001",
            tool_name="deploy_service",
            params={"service": "api", "version": "2.0"},
            quest_id="quest-123",
            approval_context="User asked Lancelot to deploy the API service.",
            approval_reason="This can change production service state.",
        )
        assert card.card_type == ActionCardType.APPROVAL.value
        assert card.source_system == "governance"
        assert card.source_item_id == "sentry-001"
        assert card.quest_id == "quest-123"
        assert len(card.buttons) == 2
        assert card.buttons[0].id == "approve"
        assert card.buttons[1].id == "deny"
        assert "deploy_service" in card.title
        assert "What I am trying to do: User asked Lancelot to deploy the API service." in card.description
        assert "Original request context: User asked Lancelot to deploy the API service." in card.description
        assert "Approval scope:\n- One exact `deploy_service` tool call" in card.description
        assert "This approval does not cover:" in card.description
        assert '"service": "api"' in card.description
        assert card.expires_at is not None

    def test_from_sentry_request_summarizes_repo_write(self, factory):
        """Approval cards lead with operator intent for repo writes."""
        card = factory.from_sentry_request(
            req_id="sentry-002",
            tool_name="repo_writer",
            params={"action": "edit", "path": "src/core/example_store.py"},
        )

        assert card.title == "Approve repository file edit: src/core/example_store.py"
        assert "I need approval to edit one repository file: `src/core/example_store.py`." in card.description
        assert "Approval scope:\n- One exact repository file operation" in card.description
        assert "- Target file: `src/core/example_store.py`" in card.description
        assert "Git commits, pushes, deployments, or external calls unless separately approved" in card.description
        assert "Tool: repo_writer" in card.description

    def test_from_sentry_request_summarizes_workspace_write(self, factory):
        """Workspace writes should not be described as repository changes."""
        card = factory.from_sentry_request(
            req_id="sentry-003",
            tool_name="repo_writer",
            params={
                "action": "create",
                "path": "operator_smoke/approval_probe.txt",
                "workspace": "/home/lancelot/workspace",
            },
        )

        assert card.title == "Approve workspace file create: operator_smoke/approval_probe.txt"
        assert "I need approval to create one workspace file: `operator_smoke/approval_probe.txt`." in card.description
        assert "Approval scope:\n- One bounded workspace file operation" in card.description
        assert "same file and equivalent text content" in card.description
        assert "- Workspace root: `/home/lancelot/workspace`" in card.description

    def test_from_sentry_request_batch_groups_repo_writes(self, factory):
        """Grouped approval cards summarize exact file scope."""
        card = factory.from_sentry_request_batch(
            requests=[
                {
                    "request_id": "req-1",
                    "tool_name": "repo_writer",
                    "params": {"action": "edit", "path": "src/tickets/store.py"},
                },
                {
                    "request_id": "req-2",
                    "tool_name": "repo_writer",
                    "params": {"action": "edit", "path": "tests/test_tickets.py"},
                },
            ],
            approval_context="User asked Lancelot to update a local workflow.",
            approval_reason="This changes repository files.",
        )

        assert card.title == "Approve 2 repository file edits"
        assert "I need approval to edit 2 repository files." in card.description
        assert "What I am trying to do: User asked Lancelot to update a local workflow." in card.description
        assert "Approval scope:\n- 2 exact repository file operations" in card.description
        assert "This approval does not cover:" in card.description
        assert "- edit `src/tickets/store.py`" in card.description
        assert "- edit `tests/test_tickets.py`" in card.description
        assert card.source_item_id == "batch:req-1,req-2"
        assert card.metadata["approval_type"] == "sentry_t3_batch"
        assert card.metadata["approval_request_ids"] == ["req-1", "req-2"]

    def test_from_soul_proposal(self, factory):
        """Creates correct card for soul amendment."""
        card = factory.from_soul_proposal(
            proposal_id="prop-001",
            version="v2",
            diff_summary=["Added autonomy rule", "Modified approval threshold"],
        )
        assert card.card_type == ActionCardType.APPROVAL.value
        assert card.source_system == "soul"
        assert card.source_item_id == "prop-001"
        assert "v2" in card.title
        assert "autonomy rule" in card.description
        assert len(card.buttons) == 2

    def test_from_skill_proposal(self, factory):
        """Creates correct card for skill proposal."""
        card = factory.from_skill_proposal(
            proposal_id="skill-001",
            name="web_scraper",
            description="Scrapes web pages for structured data extraction",
        )
        assert card.source_system == "skills"
        assert card.source_item_id == "skill-001"
        assert "web_scraper" in card.title
        assert card.buttons[0].id == "approve"
        assert card.buttons[1].id == "reject"

    def test_from_scheduler_approval(self, factory):
        """Creates correct card for scheduler job approval."""
        card = factory.from_scheduler_approval(
            job_id="job-daily-backup",
            job_name="Daily Backup",
            skill="command_runner",
        )
        assert card.source_system == "scheduler"
        assert card.source_item_id == "job-daily-backup"
        assert "Daily Backup" in card.title
        assert "command_runner" in card.description

    def test_create_custom(self, factory):
        """Creates a custom card with arbitrary buttons."""
        from actioncard.models import ActionButton
        card = factory.create_custom(
            card_type=ActionCardType.CHOICE.value,
            title="Pick a model",
            description="Which model for this task?",
            buttons=[
                ActionButton(id="gemini", label="Gemini Flash",
                             style=ActionButtonStyle.PRIMARY.value),
                ActionButton(id="gpt4", label="GPT-4o",
                             style=ActionButtonStyle.SECONDARY.value),
            ],
            source_system="router",
            quest_id="q-1",
        )
        assert card.card_type == ActionCardType.CHOICE.value
        assert len(card.buttons) == 2
        assert card.buttons[0].id == "gemini"

    def test_cards_saved_to_store(self, factory, temp_data_dir):
        """All factory methods persist cards to store."""
        card = factory.from_sentry_request("s1", "test", {})
        store = ActionCardStore(data_dir=temp_data_dir)
        retrieved = store.get(card.card_id)
        assert retrieved is not None
        assert retrieved.card_id == card.card_id
        store.close()

    def test_metadata_populated(self, factory):
        """Cards include approval_type in metadata."""
        card = factory.from_sentry_request("s1", "deploy", {})
        assert card.metadata["approval_type"] == "sentry_t3"
        assert card.metadata["tool_name"] == "deploy"

    def test_description_truncation(self, factory):
        """Long descriptions are truncated."""
        card = factory.from_skill_proposal(
            "p1", "test", "x" * 500,
        )
        assert len(card.description) <= 300

    def test_helper_copy_variants_for_operator_facing_approval_scope(self):
        assert factory_module._compact_text("  a\n b  ", limit=20) == "a b"
        assert factory_module._compact_text("x" * 20, limit=8) == "xxxxx..."
        assert factory_module._format_params({"b": 2, "a": 1}).startswith("{")
        assert factory_module._format_params({"x": object()}, limit=10).endswith("...")
        assert factory_module._extract_user_request(
            "User request: deploy the API. Requested governed tool: command_runner"
        ) == "deploy the API"
        assert factory_module._extract_user_request("User request: unspecified") == ""
        assert factory_module._file_target_kind({"workspace": "/home/lancelot/app", "path": "x"}) == "repository"
        assert factory_module._file_target_kind({"workspace": "/tmp/work", "path": "x"}) == "workspace"
        assert factory_module._file_target_kind({"path": "README.md"}) == "repository"
        assert factory_module._file_kind_label("other") == "file"
        assert factory_module._allows_bounded_workspace_retry({
            "action": "create",
            "workspace": "/tmp/work",
            "path": "note.md",
        }) is True

        command = factory_module._approval_copy("command_runner", {"command": "pytest -q", "cwd": "/repo"})
        network = factory_module._approval_copy("network_client", {"method": "post", "url": "https://api.example/v1"})
        service = factory_module._approval_copy("service_runner", {"action": "restart", "service_name": "api"})
        telegram = factory_module._approval_copy("telegram_send", {})

        assert command["title"] == "Approve command: pytest"
        assert "api.example" in network["headline"]
        assert "restart" in service["headline"]
        assert telegram["title"] == "Approve Telegram send"

    def test_batch_copy_variants_and_request_detail_truncation(self):
        network_group = factory_module._approval_group_copy([
            {"tool_name": "network_client", "params": {"method": "GET", "url": "https://a.example/x"}},
            {"tool_name": "github_connector", "params": {"method": "POST", "url": "https://api.github.com/repos"}},
        ])
        command_group = factory_module._approval_group_copy([
            {"tool_name": "command_runner", "params": {"command": "pytest tests/a.py"}},
            {"tool_name": "command_runner", "params": {"command": "python -m build"}},
        ])
        mixed_group = factory_module._approval_group_copy([
            {"tool_name": "repo_writer", "params": {}},
            {"tool_name": "telegram_send", "params": {}},
        ])
        details = factory_module._format_request_details([
            {"request_id": "r1", "tool_name": "repo_writer", "params": {"content": "x" * 2000}},
        ], limit=120)

        assert network_group["items_label"] == "Requests"
        assert "api.github.com" in network_group["items"]
        assert command_group["items_label"] == "Commands"
        assert mixed_group["title"] == "Approve 2 governed actions"
        assert details.endswith("...")

    def test_sentry_batch_rejects_empty_request_ids_and_event_bus_failures_are_non_fatal(self, temp_data_dir, monkeypatch):
        store = ActionCardStore(data_dir=temp_data_dir)
        published = []
        event_bus = types.SimpleNamespace(publish_sync=lambda event: published.append(event))
        monkeypatch.setitem(
            __import__("sys").modules,
            "event_bus",
            types.SimpleNamespace(Event=lambda **kwargs: types.SimpleNamespace(**kwargs)),
        )
        f = ActionCardFactory(card_store=store, event_bus=event_bus)

        with pytest.raises(ValueError, match="request_id"):
            f.from_sentry_request_batch([{"tool_name": "repo_writer", "params": {}}])

        card = f.from_sentry_request("r1", "telegram_send", {})
        assert published[0].type == "actioncard_presented"
        assert published[0].payload["card_id"] == card.card_id

        f._event_bus = types.SimpleNamespace(publish_sync=lambda event: (_ for _ in ()).throw(RuntimeError("bus down")))
        f.from_scheduler_approval("job-1", "Daily", "daily_news_brief")
        store.close()
