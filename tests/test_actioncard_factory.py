"""
Lancelot — ActionCardFactory Unit Tests
========================================
Tests for creating ActionCards from each approval subsystem.
"""

import tempfile
import shutil
import pytest

from actioncard.models import ActionCard, ActionCardType, ActionButtonStyle
from actioncard.store import ActionCardStore
from actioncard.factory import ActionCardFactory


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
        )
        assert card.card_type == ActionCardType.APPROVAL.value
        assert card.source_system == "governance"
        assert card.source_item_id == "sentry-001"
        assert card.quest_id == "quest-123"
        assert len(card.buttons) == 2
        assert card.buttons[0].id == "approve"
        assert card.buttons[1].id == "deny"
        assert "deploy_service" in card.title
        assert card.expires_at is not None

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
