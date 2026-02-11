"""
Tests for Prompt 59: ContentIntakeSkill.
"""

import pytest
from src.business.skills.content_intake import (
    ContentIntakeSkill,
    CONTENT_INTAKE_MANIFEST,
)
from src.skills.security.manifest import validate_manifest


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
def skill():
    return ContentIntakeSkill()


class TestParseContent:
    def test_extracts_title_body_wordcount(self, skill):
        parsed = skill.parse_content(SAMPLE_CONTENT)
        assert parsed["title"] == "How AI is Transforming Content Creation"
        assert len(parsed["body"]) > 0
        assert parsed["word_count"] > 100


class TestIdentifyContentType:
    def test_recognizes_blog_post(self, skill):
        assert skill.identify_content_type(SAMPLE_CONTENT) == "blog_post"

    def test_recognizes_transcript(self, skill):
        text = "Speaker: Hello everyone\n[00:01] Welcome to the show"
        assert skill.identify_content_type(text) == "transcript"


class TestExtractKeyTopics:
    def test_returns_relevant_keywords(self, skill):
        topics = skill.extract_key_topics(SAMPLE_CONTENT)
        assert len(topics) > 0
        assert len(topics) <= 5
        # Should include AI-related terms
        assert any("content" in t for t in topics)


class TestValidateContent:
    def test_passes_for_good_content(self, skill):
        parsed = skill.parse_content(SAMPLE_CONTENT)
        valid, issues = skill.validate_content(parsed)
        assert valid is True
        assert len(issues) == 0

    def test_fails_for_short_content(self, skill):
        parsed = skill.parse_content("Short text only")
        valid, issues = skill.validate_content(parsed)
        assert valid is False
        assert any("short" in i.lower() for i in issues)


class TestManifest:
    def test_validates_through_schema(self):
        m = validate_manifest(CONTENT_INTAKE_MANIFEST)
        assert m.id == "content-intake"
