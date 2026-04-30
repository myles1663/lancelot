"""
Quality Verify Skill — checks output quality before delivery.

Third stage of the content repurposing pipeline: validates that
generated content meets platform requirements and quality standards.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class QualityResult:
    """Aggregate quality check result."""
    passed: bool
    score: float  # 0.0 - 1.0
    issues: List[str] = field(default_factory=list)
    per_format: Dict[str, dict] = field(default_factory=dict)


_PLACEHOLDER_PATTERNS = ["lorem ipsum", "[placeholder]", "TODO", "FIXME", "xxx"]


class QualityVerifySkill:
    """Checks generated content quality before delivery."""

    def verify_tweets(self, tweets: List[str]) -> dict:
        """Check tweet quality: length, no broken URLs, no placeholders."""
        issues = []
        for i, tweet in enumerate(tweets):
            if len(tweet) > 280:
                issues.append(f"Tweet {i+1} exceeds 280 chars ({len(tweet)})")
            if "http" in tweet and " " in tweet.split("http")[-1].split()[0] if tweet.split("http")[-1].split() else False:
                issues.append(f"Tweet {i+1} may have broken URL")
            for pattern in _PLACEHOLDER_PATTERNS:
                if pattern.lower() in tweet.lower():
                    issues.append(f"Tweet {i+1} contains placeholder text: '{pattern}'")

        return {
            "passed": len(issues) == 0,
            "issues": issues,
            "count": len(tweets),
        }

    def verify_linkedin(self, posts: List[str]) -> dict:
        """Check LinkedIn quality: word count range, no repetition."""
        issues = []
        for i, post in enumerate(posts):
            word_count = len(post.split())
            if word_count < 200:
                issues.append(f"LinkedIn post {i+1} too short ({word_count} words)")
            if word_count > 600:
                issues.append(f"LinkedIn post {i+1} too long ({word_count} words)")
            for pattern in _PLACEHOLDER_PATTERNS:
                if pattern.lower() in post.lower():
                    issues.append(f"LinkedIn post {i+1} contains placeholder: '{pattern}'")

        return {
            "passed": len(issues) == 0,
            "issues": issues,
            "count": len(posts),
        }

    def verify_email(self, snippets: List[str]) -> dict:
        """Check email snippet quality."""
        issues = []
        for i, snippet in enumerate(snippets):
            if "Subject:" not in snippet:
                issues.append(f"Email {i+1} missing subject line")
            if len(snippet) < 50:
                issues.append(f"Email {i+1} too short")

        return {
            "passed": len(issues) == 0,
            "issues": issues,
            "count": len(snippets),
        }

    def verify_instagram(self, caption: str) -> dict:
        """Check Instagram caption quality."""
        issues = []
        if len(caption) > 2200:
            issues.append(f"Caption exceeds 2200 chars ({len(caption)})")
        if not caption.strip():
            issues.append("Caption is empty")

        return {
            "passed": len(issues) == 0,
            "issues": issues,
        }

    def verify_all(self, repurposed: dict) -> QualityResult:
        """Run all checks and compute aggregate score."""
        per_format = {}
        all_issues = []

        if "tweets" in repurposed:
            r = self.verify_tweets(repurposed["tweets"])
            per_format["tweets"] = r
            all_issues.extend(r["issues"])

        if "linkedin" in repurposed:
            r = self.verify_linkedin(repurposed["linkedin"])
            per_format["linkedin"] = r
            all_issues.extend(r["issues"])

        if "email" in repurposed:
            r = self.verify_email(repurposed["email"])
            per_format["email"] = r
            all_issues.extend(r["issues"])

        if "instagram" in repurposed:
            r = self.verify_instagram(repurposed["instagram"])
            per_format["instagram"] = r
            all_issues.extend(r["issues"])

        # Score: 1.0 if no issues, decreasing with issues
        total_checks = len(per_format)
        passed_checks = sum(1 for r in per_format.values() if r.get("passed", False))
        score = passed_checks / total_checks if total_checks > 0 else 0.0

        return QualityResult(
            passed=len(all_issues) == 0,
            score=round(score, 2),
            issues=all_issues,
            per_format=per_format,
        )
