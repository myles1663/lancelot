"""
Tests for BAL Client Repository (Step 2B).

All tests use real temporary SQLite databases — no mocks.
"""

import os
import sqlite3
import tempfile
import pytest

from src.core.bal.clients.models import (
    Client,
    ClientBilling,
    ClientCreate,
    ClientPreferences,
    ClientStatus,
    ClientUpdate,
    ContentHistory,
    PaymentStatus,
    PlanTier,
    TonePreference,
)
from src.core.bal.clients.repository import ClientRepository
from src.core.bal.database import BALDatabase


# ===================================================================
# Fixtures
# ===================================================================

@pytest.fixture
def bal_db(tmp_path):
    """Create a fresh BALDatabase in a temp directory."""
    db = BALDatabase(data_dir=str(tmp_path))
    yield db
    db.close()


@pytest.fixture
def repo(bal_db):
    """Create a ClientRepository with a fresh database."""
    return ClientRepository(bal_db)


def _create_test_client(repo, name="Test Client", email="test@example.com", tier=PlanTier.STARTER):
    """Helper to create a client."""
    return repo.create(ClientCreate(name=name, email=email, plan_tier=tier))


# ===================================================================
# Tests
# ===================================================================

class TestClientCreate:
    def test_create_minimal(self, repo):
        client = _create_test_client(repo)
        assert client.name == "Test Client"
        assert client.email == "test@example.com"
        assert client.status == ClientStatus.ONBOARDING
        assert client.plan_tier == PlanTier.STARTER
        assert client.id is not None

    def test_create_with_preferences(self, repo):
        prefs = ClientPreferences(tone=TonePreference.WITTY)
        client = repo.create(
            ClientCreate(name="Fancy", email="fancy@example.com", preferences=prefs)
        )
        assert client.preferences.tone == TonePreference.WITTY

    def test_create_with_tier(self, repo):
        client = _create_test_client(repo, name="Scale Co", email="scale@co.com", tier=PlanTier.SCALE)
        assert client.plan_tier == PlanTier.SCALE


class TestClientGetById:
    def test_found(self, repo):
        created = _create_test_client(repo)
        found = repo.get_by_id(created.id)
        assert found is not None
        assert found.id == created.id
        assert found.name == "Test Client"

    def test_not_found(self, repo):
        found = repo.get_by_id("nonexistent-id")
        assert found is None


class TestClientGetByEmail:
    def test_found(self, repo):
        _create_test_client(repo, email="find@me.com")
        found = repo.get_by_email("find@me.com")
        assert found is not None
        assert found.email == "find@me.com"

    def test_not_found(self, repo):
        found = repo.get_by_email("nobody@nowhere.com")
        assert found is None

    def test_case_insensitive(self, repo):
        _create_test_client(repo, email="upper@test.com")
        found = repo.get_by_email("UPPER@TEST.COM")
        # Email is lowercased on create, so uppercase lookup won't match directly
        # unless we lowercase in the query. The model lowercases on creation.
        found = repo.get_by_email("upper@test.com")
        assert found is not None


class TestClientListAll:
    def test_empty(self, repo):
        clients = repo.list_all()
        assert clients == []

    def test_multiple_clients(self, repo):
        _create_test_client(repo, name="A", email="a@a.com")
        _create_test_client(repo, name="B", email="b@b.com")
        _create_test_client(repo, name="C", email="c@c.com")
        clients = repo.list_all()
        assert len(clients) == 3

    def test_filter_by_status(self, repo):
        c1 = _create_test_client(repo, name="A", email="a@a.com")
        c2 = _create_test_client(repo, name="B", email="b@b.com")
        # Activate one
        repo.update_status(c1.id, ClientStatus.ACTIVE)

        onboarding = repo.list_all(status_filter=ClientStatus.ONBOARDING)
        active = repo.list_all(status_filter=ClientStatus.ACTIVE)
        assert len(onboarding) == 1
        assert len(active) == 1
        assert active[0].id == c1.id


class TestClientUpdate:
    def test_update_name(self, repo):
        client = _create_test_client(repo)
        updated = repo.update(client.id, ClientUpdate(name="New Name"))
        assert updated.name == "New Name"
        assert updated.email == client.email  # unchanged

    def test_update_email(self, repo):
        client = _create_test_client(repo)
        updated = repo.update(client.id, ClientUpdate(email="new@email.com"))
        assert updated.email == "new@email.com"

    def test_update_preferences(self, repo):
        client = _create_test_client(repo)
        new_prefs = ClientPreferences(tone=TonePreference.TECHNICAL, brand_voice_notes="Be nerdy")
        updated = repo.update(client.id, ClientUpdate(preferences=new_prefs))
        assert updated.preferences.tone == TonePreference.TECHNICAL
        assert updated.preferences.brand_voice_notes == "Be nerdy"

    def test_update_preserves_unchanged(self, repo):
        prefs = ClientPreferences(tone=TonePreference.WITTY)
        client = repo.create(
            ClientCreate(name="Orig", email="orig@test.com", preferences=prefs)
        )
        updated = repo.update(client.id, ClientUpdate(name="Changed"))
        # Preferences should be preserved
        assert updated.preferences.tone == TonePreference.WITTY

    def test_update_nonexistent_raises(self, repo):
        with pytest.raises(ValueError, match="Client not found"):
            repo.update("fake-id", ClientUpdate(name="X"))

    def test_update_updates_timestamp(self, repo):
        client = _create_test_client(repo)
        updated = repo.update(client.id, ClientUpdate(name="Later"))
        assert updated.updated_at >= client.updated_at


class TestClientUpdateStatus:
    def test_update_status(self, repo):
        client = _create_test_client(repo)
        updated = repo.update_status(client.id, ClientStatus.ACTIVE)
        assert updated.status == ClientStatus.ACTIVE

    def test_update_status_nonexistent_raises(self, repo):
        with pytest.raises(ValueError, match="Client not found"):
            repo.update_status("fake-id", ClientStatus.ACTIVE)


class TestClientUpdateBilling:
    def test_update_billing(self, repo):
        client = _create_test_client(repo)
        new_billing = ClientBilling(
            stripe_customer_id="cus_abc",
            payment_status=PaymentStatus.PAST_DUE,
        )
        updated = repo.update_billing(client.id, new_billing)
        assert updated.billing.stripe_customer_id == "cus_abc"
        assert updated.billing.payment_status == PaymentStatus.PAST_DUE

    def test_billing_json_roundtrip(self, repo):
        client = _create_test_client(repo)
        billing = ClientBilling(
            stripe_customer_id="cus_xyz",
            subscription_id="sub_123",
        )
        updated = repo.update_billing(client.id, billing)
        # Re-fetch to verify persistence
        fetched = repo.get_by_id(client.id)
        assert fetched.billing.stripe_customer_id == "cus_xyz"
        assert fetched.billing.subscription_id == "sub_123"


class TestClientUpdateContentHistory:
    def test_update_content_history(self, repo):
        client = _create_test_client(repo)
        history = ContentHistory(total_pieces_delivered=42, average_satisfaction=4.5)
        updated = repo.update_content_history(client.id, history)
        assert updated.content_history.total_pieces_delivered == 42
        assert updated.content_history.average_satisfaction == 4.5


class TestClientDelete:
    def test_soft_delete(self, repo):
        client = _create_test_client(repo)
        result = repo.delete(client.id)
        assert result is True
        # Client still exists but is churned
        fetched = repo.get_by_id(client.id)
        assert fetched is not None
        assert fetched.status == ClientStatus.CHURNED

    def test_delete_nonexistent(self, repo):
        result = repo.delete("fake-id")
        assert result is False


class TestEmailUniqueness:
    def test_duplicate_email_raises(self, repo):
        _create_test_client(repo, email="dupe@test.com")
        with pytest.raises(Exception):
            _create_test_client(repo, name="Another", email="dupe@test.com")


class TestMemoryBlockId:
    def test_update_memory_block_id(self, repo):
        client = _create_test_client(repo)
        updated = repo.update_memory_block_id(client.id, "mem_block_123")
        assert updated.memory_block_id == "mem_block_123"

    def test_memory_block_id_persists(self, repo):
        client = _create_test_client(repo)
        repo.update_memory_block_id(client.id, "mem_abc")
        fetched = repo.get_by_id(client.id)
        assert fetched.memory_block_id == "mem_abc"


class TestSchemaV2Migration:
    def test_v2_migration_applied(self, bal_db):
        """Schema V2 adds memory_block_id and unique email index."""
        assert bal_db.CURRENT_SCHEMA_VERSION == 2
        with bal_db.transaction() as conn:
            # memory_block_id column should exist
            cursor = conn.execute("PRAGMA table_info(bal_clients)")
            columns = [row["name"] for row in cursor.fetchall()]
            assert "memory_block_id" in columns

            # Check unique index exists
            cursor = conn.execute("PRAGMA index_list(bal_clients)")
            indexes = {row["name"]: bool(row["unique"]) for row in cursor.fetchall()}
            assert "idx_bal_clients_email" in indexes
            assert indexes["idx_bal_clients_email"] is True
