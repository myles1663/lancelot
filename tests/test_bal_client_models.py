"""
Tests for BAL Client Pydantic models and enums (Step 2A).
"""

import json
import pytest

from src.core.bal.clients.models import (
    Client,
    ClientBilling,
    ClientCreate,
    ClientPreferences,
    ClientStatus,
    ClientUpdate,
    ContentHistory,
    EmojiPolicy,
    HashtagPolicy,
    PaymentStatus,
    PlanTier,
    TonePreference,
)


# ===================================================================
# Enum Tests
# ===================================================================

class TestClientEnums:
    def test_client_status_values(self):
        assert ClientStatus.ONBOARDING == "onboarding"
        assert ClientStatus.ACTIVE == "active"
        assert ClientStatus.PAUSED == "paused"
        assert ClientStatus.CHURNED == "churned"

    def test_plan_tier_values(self):
        assert PlanTier.STARTER == "starter"
        assert PlanTier.GROWTH == "growth"
        assert PlanTier.SCALE == "scale"

    def test_payment_status_values(self):
        assert PaymentStatus.ACTIVE == "active"
        assert PaymentStatus.PAST_DUE == "past_due"
        assert PaymentStatus.CANCELED == "canceled"

    def test_tone_preference_values(self):
        assert TonePreference.CASUAL == "casual"
        assert TonePreference.PROFESSIONAL == "professional"
        assert TonePreference.TECHNICAL == "technical"
        assert TonePreference.WITTY == "witty"

    def test_enum_json_serialization(self):
        """Enums serialize to their string values in JSON."""
        client = Client(name="Test", email="test@example.com")
        data = json.loads(client.model_dump_json())
        assert data["status"] == "onboarding"
        assert data["plan_tier"] == "starter"

    def test_enum_from_string(self):
        """Enums can be constructed from string values."""
        assert ClientStatus("onboarding") == ClientStatus.ONBOARDING
        assert PlanTier("growth") == PlanTier.GROWTH


# ===================================================================
# Model Tests
# ===================================================================

class TestClientBilling:
    def test_default_billing(self):
        billing = ClientBilling()
        assert billing.stripe_customer_id is None
        assert billing.subscription_id is None
        assert billing.current_period_end is None
        assert billing.payment_status == PaymentStatus.ACTIVE

    def test_billing_json_roundtrip(self):
        billing = ClientBilling(
            stripe_customer_id="cus_123",
            payment_status=PaymentStatus.PAST_DUE,
        )
        dumped = billing.model_dump_json()
        restored = ClientBilling.model_validate_json(dumped)
        assert restored.stripe_customer_id == "cus_123"
        assert restored.payment_status == PaymentStatus.PAST_DUE


class TestClientPreferences:
    def test_default_preferences(self):
        prefs = ClientPreferences()
        assert prefs.tone == TonePreference.PROFESSIONAL
        assert prefs.platforms == ["twitter", "linkedin"]
        assert prefs.hashtag_policy == HashtagPolicy.CONTEXTUAL
        assert prefs.emoji_policy == EmojiPolicy.CONSERVATIVE
        assert prefs.brand_voice_notes == ""
        assert prefs.excluded_topics == []
        assert prefs.posting_schedule == {}

    def test_custom_preferences(self):
        prefs = ClientPreferences(
            tone=TonePreference.WITTY,
            platforms=["instagram", "tiktok"],
            hashtag_policy=HashtagPolicy.ALWAYS,
            brand_voice_notes="Edgy and fun",
        )
        assert prefs.tone == TonePreference.WITTY
        assert "instagram" in prefs.platforms

    def test_preferences_json_roundtrip(self):
        prefs = ClientPreferences(
            tone=TonePreference.TECHNICAL,
            excluded_topics=["politics", "religion"],
        )
        dumped = prefs.model_dump_json()
        restored = ClientPreferences.model_validate_json(dumped)
        assert restored.tone == TonePreference.TECHNICAL
        assert "politics" in restored.excluded_topics


class TestContentHistory:
    def test_default_history(self):
        history = ContentHistory()
        assert history.total_pieces_delivered == 0
        assert history.last_delivery_at is None
        assert history.average_satisfaction == 0.0


class TestClient:
    def test_creation_with_defaults(self):
        client = Client(name="Acme Corp", email="hello@acme.com")
        assert client.name == "Acme Corp"
        assert client.email == "hello@acme.com"
        assert client.status == ClientStatus.ONBOARDING
        assert client.plan_tier == PlanTier.STARTER
        assert client.memory_block_id is None
        assert client.id  # UUID generated
        assert client.created_at is not None
        assert client.updated_at is not None

    def test_creation_with_all_fields(self):
        client = Client(
            name="BigCo",
            email="admin@bigco.com",
            status=ClientStatus.ACTIVE,
            plan_tier=PlanTier.SCALE,
            preferences=ClientPreferences(tone=TonePreference.CASUAL),
            billing=ClientBilling(stripe_customer_id="cus_xyz"),
        )
        assert client.plan_tier == PlanTier.SCALE
        assert client.preferences.tone == TonePreference.CASUAL
        assert client.billing.stripe_customer_id == "cus_xyz"

    def test_uuid_generation(self):
        c1 = Client(name="A", email="a@a.com")
        c2 = Client(name="B", email="b@b.com")
        assert c1.id != c2.id
        assert len(c1.id) == 36  # UUID format

    def test_email_validation_valid(self):
        client = Client(name="Test", email="User@Example.COM")
        assert client.email == "user@example.com"  # lowercased

    def test_email_validation_invalid(self):
        with pytest.raises(ValueError, match="Invalid email"):
            Client(name="Test", email="not-an-email")

    def test_json_roundtrip(self):
        client = Client(
            name="Roundtrip Corp",
            email="rt@example.com",
            plan_tier=PlanTier.GROWTH,
        )
        dumped = client.model_dump_json()
        restored = Client.model_validate_json(dumped)
        assert restored.name == "Roundtrip Corp"
        assert restored.plan_tier == PlanTier.GROWTH
        assert restored.id == client.id


class TestClientCreate:
    def test_minimal_create(self):
        create = ClientCreate(name="New Client", email="new@example.com")
        assert create.name == "New Client"
        assert create.plan_tier == PlanTier.STARTER
        assert create.preferences is None

    def test_create_with_preferences(self):
        create = ClientCreate(
            name="Fancy Client",
            email="fancy@example.com",
            plan_tier=PlanTier.GROWTH,
            preferences=ClientPreferences(tone=TonePreference.WITTY),
        )
        assert create.preferences.tone == TonePreference.WITTY

    def test_create_email_validation(self):
        with pytest.raises(ValueError, match="Invalid email"):
            ClientCreate(name="Bad", email="notvalid")


class TestClientUpdate:
    def test_empty_update(self):
        update = ClientUpdate()
        assert update.name is None
        assert update.email is None
        assert update.preferences is None

    def test_partial_update(self):
        update = ClientUpdate(name="Updated Name")
        assert update.name == "Updated Name"
        assert update.email is None

    def test_update_email_validation(self):
        with pytest.raises(ValueError, match="Invalid email"):
            ClientUpdate(email="notvalid")

    def test_update_email_none_is_valid(self):
        update = ClientUpdate(email=None)
        assert update.email is None
