"""
Content Intake Skill — parses raw content into structured format.

First stage of the content repurposing pipeline: reads raw text,
identifies type, extracts key topics, and validates quality.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import List, Tuple

from src.skills.security.manifest import SkillManifest, validate_manifest


# ── Manifest ─────────────────────────────────────────────────────

CONTENT_INTAKE_MANIFEST = {
    "id": "content-intake",
    "name": "Content Intake",
    "version": "1.0.0",
    "author": "lancelot",
    "source": "first-party",
    "description": "Parses raw content into structured format for repurposing",
    "capabilities_required": [
        {"capability": "connector.read", "description": "Read email attachments"},
    ],
    "target_domains": [],
    "credentials": [],
}


# ── Stop words for topic extraction ──────────────────────────────

_STOP_WORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "this", "that",
    "these", "those", "it", "its", "i", "you", "he", "she", "we", "they",
    "me", "him", "her", "us", "them", "my", "your", "his", "our", "their",
    "what", "which", "who", "whom", "how", "when", "where", "why", "not",
    "no", "all", "each", "every", "both", "few", "more", "most", "other",
    "some", "such", "than", "too", "very", "just", "about", "also", "so",
    "if", "as", "into", "through", "during", "before", "after", "above",
    "below", "between", "out", "off", "over", "under", "again", "then",
    "once", "here", "there", "any", "only", "own", "same", "up", "down",
}


class ContentIntakeSkill:
    """Parses raw content into structured format for repurposing."""

    def parse_content(self, raw_text: str) -> dict:
        """Parse raw text into structured format."""
        lines = raw_text.strip().split("\n")
        title = self._extract_title(lines)
        body = raw_text.strip()
        words = body.split()
        word_count = len(words)
        content_type = self.identify_content_type(raw_text)
        paragraphs = [p.strip() for p in raw_text.split("\n\n") if p.strip()]
        key_topics = self.extract_key_topics(raw_text)
        estimated_read_time = max(1, word_count // 200)  # ~200 wpm

        return {
            "title": title,
            "body": body,
            "word_count": word_count,
            "content_type": content_type,
            "paragraphs": paragraphs,
            "key_topics": key_topics,
            "estimated_read_time": estimated_read_time,
        }

    def identify_content_type(self, text: str) -> str:
        """Identify content type from structural markers."""
        lower = text.lower()

        if re.search(r"^#\s+", text, re.MULTILINE) or "blog post" in lower:
            return "blog_post"
        if any(kw in lower for kw in ["transcript", "speaker:", "interviewer:", "[00:"]):
            return "transcript"
        if any(kw in lower for kw in ["newsletter", "subscribe", "unsubscribe", "this week"]):
            return "newsletter"
        if any(kw in lower for kw in ["abstract", "introduction", "conclusion", "references"]):
            return "article"
        return "other"

    def extract_key_topics(self, text: str, max_topics: int = 5) -> List[str]:
        """Simple keyword extraction via word frequency."""
        words = re.findall(r"[a-zA-Z]{3,}", text.lower())
        filtered = [w for w in words if w not in _STOP_WORDS]
        counts = Counter(filtered)
        return [word for word, _ in counts.most_common(max_topics)]

    def validate_content(self, parsed: dict) -> Tuple[bool, List[str]]:
        """Check minimum quality requirements."""
        issues = []
        if parsed.get("word_count", 0) < 100:
            issues.append("Content too short (minimum 100 words)")
        if not parsed.get("title"):
            issues.append("No title detected")
        if not parsed.get("paragraphs"):
            issues.append("No paragraphs detected")
        return (len(issues) == 0, issues)

    def _extract_title(self, lines: list) -> str:
        """Extract title from first line or markdown heading."""
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped[2:].strip()
            if stripped and len(stripped) < 200:
                return stripped
        return ""
