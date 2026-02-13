"""
Tests for BAL Client Events / Receipt Emission (Step 2D).

Uses real receipt service — no mocks.
"""

import os
import pytest
import tempfile

from src.core.bal.clients.models import (
    Client,
    ClientCreate,
    ClientStatus,
    PlanTier,
)
from src.core.bal.clients.events import (
    emit_client_churned,
    emit_client_onboarded,
    emit_client_paused,
    emit_client_plan_changed,
    emit_client_preferences_updated,
    emit_client_status_changed,
)


# ===================================================================
# Fixtures
# ===================================================================

@pytest.fixture
def test_client():
    """A client instance for event tests."""
    return Client(
        name="Events Co",
        email="events@example.com",
        status=ClientStatus.ACTIVE,
        plan_tier=PlanTier.GROWTH,
    )


@pytest.fixture(autouse=True)
def _patch_receipt_service(tmp_path, monkeypatch):
    """Point receipt service to a temp directory."""
    monkeypatch.setenv("RECEIPT_DATA_DIR", str(tmp_path))


# ===================================================================
# Tests
# ===================================================================

class TestClientEventEmission:
    def test_emit_client_onboarded(self, test_client):
        """Emitting client_onboarded should not raise."""
        emit_client_onboarded(test_client)

    def test_emit_client_preferences_updated(self, test_client):
        emit_client_preferences_updated(test_client, ["tone", "platforms"])

    def test_emit_client_status_changed(self, test_client):
        emit_client_status_changed(
            test_client,
            old_status=ClientStatus.ONBOARDING,
            new_status=ClientStatus.ACTIVE,
            reason="activated",
        )

    def test_emit_client_plan_changed(self, test_client):
        emit_client_plan_changed(
            test_client,
            old_tier=PlanTier.STARTER,
            new_tier=PlanTier.GROWTH,
        )

    def test_emit_client_paused(self, test_client):
        emit_client_paused(test_client, reason="vacation")

    def test_emit_client_churned(self, test_client):
        emit_client_churned(test_client, reason="canceled service")
