"""
Tests for Prompt 60: ContentRepurposeSkill.
"""

import pytest
from src.business.skills.content_intake import ContentIntakeSkill
from src.business.skills.content_repurpose import ContentRepurposeSkill


SAMPLE_CONTENT = """# How AI is Transforming Content Creation

Artificial intelligence is rapidly changing how businesses create and distribute content.
From automated writing assistants to sophisticated content analysis tools, AI is becoming
an essential part of the modern content workflow.

The key benefits include faster production times, more consistent quality, and the ability
to personalize content at scale. Companies that adopt AI-powered tools are seeing significant
improvements in their content marketing metrics.

However, it's important to remember that AI works best when combined with human creativity
and editorial judgment. The most successful approaches use AI to handle repetitive tasks
while humans focus on strategy and storytelling.

Looking ahead, we can expect even more advanced AI capabilities in content creation. Natural
language processing continues to improve, and new tools are making it easier than ever to
repurpose content across multiple platforms and formats.

The future of content is collaborative — humans and AI working together to create better,
more engaging experiences for audiences everywhere.
"""


@pytest.fixture
def parsed():
    intake = ContentIntakeSkill()
    return intake.parse_content(SAMPLE_CONTENT)


@pytest.fixture
def repurposer():
    return ContentRepurposeSkill()


class TestTweets:
    def test_under_280_chars(self, repurposer, parsed):
        tweets = repurposer.generate_tweets(parsed)
        for tweet in tweets:
            assert len(tweet) <= 280

    def test_correct_count(self, repurposer, parsed):
        tweets = repurposer.generate_tweets(parsed, count=3)
        assert len(tweets) == 3


class TestLinkedIn:
    def test_200_to_500_words(self, repurposer, parsed):
        posts = repurposer.generate_linkedin_posts(parsed)
        for post in posts:
            word_count = len(post.split())
            assert word_count >= 200
            assert word_count <= 550  # Allow slight padding


class TestEmail:
    def test_has_structure(self, repurposer, parsed):
        snippets = repurposer.generate_email_snippets(parsed)
        for snippet in snippets:
            assert "Subject:" in snippet
            assert "Best regards" in snippet


class TestInstagram:
    def test_under_2200_chars(self, repurposer, parsed):
        caption = repurposer.generate_instagram_caption(parsed)
        assert len(caption) <= 2200


class TestRepurposeAll:
    def test_returns_all_formats(self, repurposer, parsed):
        result = repurposer.repurpose_all(parsed)
        assert "tweets" in result
        assert "linkedin" in result
        assert "email" in result
        assert "instagram" in result
        assert isinstance(result["tweets"], list)
        assert isinstance(result["instagram"], str)
