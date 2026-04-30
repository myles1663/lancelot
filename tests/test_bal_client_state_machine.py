"""
Tests for BAL Client State Machine (Step 2C).

All tests use real temporary SQLite databases — no mocks.
"""

import pytest

from src.core.bal.clients.models import ClientCreate, ClientStatus, PlanTier
from src.core.bal.clients.repository import ClientRepository
from src.core.bal.clients.state_machine import ClientStateMachine, InvalidTransitionError
from src.core.bal.database import BALDatabase


# ===================================================================
# Fixtures
# ===================================================================

@pytest.fixture
def bal_db(tmp_path):
    db = BALDatabase(data_dir=str(tmp_path))
    yield db
    db.close()


@pytest.fixture
def repo(bal_db):
    return ClientRepository(bal_db)


@pytest.fixture
def sm():
    return ClientStateMachine()


def _make_client(repo, status=None):
    """Create a client and optionally set its status."""
    client = repo.create(ClientCreate(name="Test", email=f"test-{id(status)}@example.com"))
    if status and status != ClientStatus.ONBOARDING:
        # Must follow valid transitions to reach desired state
        if status == ClientStatus.ACTIVE:
            repo.update_status(client.id, ClientStatus.ACTIVE)
        elif status == ClientStatus.PAUSED:
            repo.update_status(client.id, ClientStatus.ACTIVE)
            repo.update_status(client.id, ClientStatus.PAUSED)
        elif status == ClientStatus.CHURNED:
            repo.update_status(client.id, ClientStatus.CHURNED)
    return repo.get_by_id(client.id)


# ===================================================================
# Validation Tests
# ===================================================================

class TestValidateTransition:
    def test_onboarding_to_active(self, sm):
        assert sm.validate_transition(ClientStatus.ONBOARDING, ClientStatus.ACTIVE) is True

    def test_onboarding_to_churned(self, sm):
        assert sm.validate_transition(ClientStatus.ONBOARDING, ClientStatus.CHURNED) is True

    def test_active_to_paused(self, sm):
        assert sm.validate_transition(ClientStatus.ACTIVE, ClientStatus.PAUSED) is True

    def test_active_to_churned(self, sm):
        assert sm.validate_transition(ClientStatus.ACTIVE, ClientStatus.CHURNED) is True

    def test_paused_to_active(self, sm):
        assert sm.validate_transition(ClientStatus.PAUSED, ClientStatus.ACTIVE) is True

    def test_paused_to_churned(self, sm):
        assert sm.validate_transition(ClientStatus.PAUSED, ClientStatus.CHURNED) is True

    # Invalid transitions
    def test_onboarding_to_paused_invalid(self, sm):
        assert sm.validate_transition(ClientStatus.ONBOARDING, ClientStatus.PAUSED) is False

    def test_churned_to_active_invalid(self, sm):
        assert sm.validate_transition(ClientStatus.CHURNED, ClientStatus.ACTIVE) is False

    def test_churned_to_onboarding_invalid(self, sm):
        assert sm.validate_transition(ClientStatus.CHURNED, ClientStatus.ONBOARDING) is False

    def test_paused_to_onboarding_invalid(self, sm):
        assert sm.validate_transition(ClientStatus.PAUSED, ClientStatus.ONBOARDING) is False

    def test_churned_is_terminal(self, sm):
        """CHURNED has no valid outbound transitions."""
        for target in ClientStatus:
            assert sm.validate_transition(ClientStatus.CHURNED, target) is False


# ===================================================================
# Transition Tests (with real DB)
# ===================================================================

class TestTransition:
    def test_onboarding_to_active(self, sm, repo):
        client = _make_client(repo)
        updated = sm.transition(client.id, ClientStatus.ACTIVE, repo)
        assert updated.status == ClientStatus.ACTIVE

    def test_active_to_paused(self, sm, repo):
        client = _make_client(repo, ClientStatus.ACTIVE)
        updated = sm.transition(client.id, ClientStatus.PAUSED, repo, reason="vacation")
        assert updated.status == ClientStatus.PAUSED

    def test_paused_to_active(self, sm, repo):
        client = _make_client(repo, ClientStatus.PAUSED)
        updated = sm.transition(client.id, ClientStatus.ACTIVE, repo)
        assert updated.status == ClientStatus.ACTIVE

    def test_active_to_churned(self, sm, repo):
        client = _make_client(repo, ClientStatus.ACTIVE)
        updated = sm.transition(client.id, ClientStatus.CHURNED, repo, reason="canceled")
        assert updated.status == ClientStatus.CHURNED

    def test_invalid_transition_raises(self, sm, repo):
        client = _make_client(repo)  # ONBOARDING
        with pytest.raises(InvalidTransitionError) as exc_info:
            sm.transition(client.id, ClientStatus.PAUSED, repo)
        assert exc_info.value.current_status == ClientStatus.ONBOARDING
        assert exc_info.value.target_status == ClientStatus.PAUSED
        assert exc_info.value.client_id == client.id

    def test_churned_to_active_raises(self, sm, repo):
        client = _make_client(repo, ClientStatus.CHURNED)
        with pytest.raises(InvalidTransitionError):
            sm.transition(client.id, ClientStatus.ACTIVE, repo)

    def test_nonexistent_client_raises(self, sm, repo):
        with pytest.raises(ValueError, match="Client not found"):
            sm.transition("fake-id", ClientStatus.ACTIVE, repo)

    def test_transition_persists_to_db(self, sm, repo):
        client = _make_client(repo)
        sm.transition(client.id, ClientStatus.ACTIVE, repo)
        # Re-fetch to confirm persistence
        fetched = repo.get_by_id(client.id)
        assert fetched.status == ClientStatus.ACTIVE

    def test_transition_with_reason(self, sm, repo):
        client = _make_client(repo, ClientStatus.ACTIVE)
        updated = sm.transition(
            client.id, ClientStatus.PAUSED, repo, reason="maintenance window"
        )
        assert updated.status == ClientStatus.PAUSED
