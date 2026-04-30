"""
Tests for Prompt 61: QualityVerifySkill.
"""

import pytest
from src.business.skills.quality_verify import QualityVerifySkill, QualityResult


@pytest.fixture
def qv():
    return QualityVerifySkill()


class TestVerifyTweets:
    def test_good_tweets_pass(self, qv):
        tweets = ["This is a good tweet about AI.", "Another tweet here."]
        result = qv.verify_tweets(tweets)
        assert result["passed"] is True

    def test_long_tweet_fails(self, qv):
        tweets = ["x" * 281]
        result = qv.verify_tweets(tweets)
        assert result["passed"] is False
        assert any("280" in i for i in result["issues"])


class TestVerifyLinkedIn:
    def test_good_post_passes(self, qv):
        post = " ".join(["word"] * 250)
        result = qv.verify_linkedin([post])
        assert result["passed"] is True

    def test_short_post_fails(self, qv):
        post = "Too short"
        result = qv.verify_linkedin([post])
        assert result["passed"] is False


class TestVerifyAll:
    def test_aggregates_correctly(self, qv):
        repurposed = {
            "tweets": ["Good tweet here."],
            "linkedin": [" ".join(["word"] * 250)],
            "email": ["Subject: Test\n\nBody here with enough content to pass the minimum length requirement for email snippets in the quality verification check."],
            "instagram": "A nice caption #hashtag",
        }
        result = qv.verify_all(repurposed)
        assert isinstance(result, QualityResult)
        assert result.passed is True

    def test_score_between_0_and_1(self, qv):
        repurposed = {
            "tweets": ["Good tweet."],
            "linkedin": [" ".join(["word"] * 250)],
        }
        result = qv.verify_all(repurposed)
        assert 0.0 <= result.score <= 1.0
