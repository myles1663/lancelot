"""
Tests for Prompt 62: DeliverySkill.
"""

import pytest
from src.business.skills.delivery import DeliverySkill, DELIVERY_MANIFEST
from src.business.skills.quality_verify import QualityResult
from src.skills.security.manifest import validate_manifest


@pytest.fixture
def delivery():
    return DeliverySkill()


@pytest.fixture
def repurposed():
    return {
        "tweets": ["Tweet 1 about AI", "Tweet 2 about content"],
        "linkedin": ["A professional post " + " ".join(["word"] * 250)],
        "email": ["Subject: Test\n\nBody content"],
        "instagram": "Caption #hashtag",
    }


@pytest.fixture
def quality():
    return QualityResult(passed=True, score=0.95)


class TestFormatEmailPackage:
    def test_creates_valid_params(self, delivery, repurposed, quality):
        result = delivery.format_email_package("client@example.com", repurposed, quality)
        assert "to" in result
        assert "subject" in result
        assert "body" in result

    def test_has_required_fields(self, delivery, repurposed, quality):
        result = delivery.format_email_package("client@example.com", repurposed, quality)
        assert result["to"] == "client@example.com"
        assert len(result["subject"]) > 0
        assert len(result["body"]) > 0


class TestDeliverySchedule:
    def test_spaces_posts_over_time(self, delivery, repurposed):
        schedule = delivery.create_delivery_schedule(repurposed)
        assert len(schedule) > 0
        # Check that times are different
        times = [s["scheduled_time"] for s in schedule]
        assert len(set(times)) == len(times)  # All unique


class TestPrepareSocialPosts:
    def test_twitter_format(self, delivery, repurposed):
        posts = delivery.prepare_social_posts(repurposed, "twitter")
        assert len(posts) == 2
        assert posts[0]["platform"] == "twitter"

    def test_linkedin_format(self, delivery, repurposed):
        posts = delivery.prepare_social_posts(repurposed, "linkedin")
        assert len(posts) == 1
        assert posts[0]["platform"] == "linkedin"


class TestManifest:
    def test_validates_through_schema(self):
        m = validate_manifest(DELIVERY_MANIFEST)
        assert m.id == "content-delivery"
