"""
Content Repurpose Skill — generates multi-format output from parsed content.

Second stage of the content repurposing pipeline: takes structured content
and produces tweets, LinkedIn posts, email snippets, and Instagram captions.
"""

from __future__ import annotations

import textwrap
from typing import List


class ContentRepurposeSkill:
    """Generates multi-format content from parsed input."""

    def generate_tweets(self, parsed_content: dict, count: int = 5) -> List[str]:
        """Generate tweet-length summaries (<=280 chars each)."""
        topics = parsed_content.get("key_topics", [])
        title = parsed_content.get("title", "")
        paragraphs = parsed_content.get("paragraphs", [])
        tweets = []

        # Tweet from title
        if title:
            tweet = title[:250] + " #ContentRepurposing"
            tweets.append(tweet[:280])

        # Tweets from key topics
        for topic in topics[:count]:
            tweet = f"Key insight on {topic}: {title[:180]}... #AI #Content"
            tweets.append(tweet[:280])

        # Tweets from paragraph openings
        for para in paragraphs[:count]:
            sentences = para.split(".")
            if sentences:
                tweet = sentences[0].strip()[:250] + "..."
                tweets.append(tweet[:280])

        return tweets[:count]

    def generate_linkedin_posts(self, parsed_content: dict, count: int = 3) -> List[str]:
        """Professional format, 200-500 words each."""
        title = parsed_content.get("title", "Insights")
        paragraphs = parsed_content.get("paragraphs", [])
        topics = parsed_content.get("key_topics", [])
        posts = []

        for i in range(min(count, max(1, len(paragraphs)))):
            hook = f"Here's something worth discussing about {topics[i] if i < len(topics) else 'this topic'}:\n\n"
            body_paras = paragraphs[i * 2:(i + 1) * 2] if paragraphs else [title]
            body = "\n\n".join(body_paras)

            # Pad to minimum 200 words
            words = body.split()
            while len(words) < 200:
                body += f"\n\nThis connects to broader themes in {topics[0] if topics else 'business'} and {topics[1] if len(topics) > 1 else 'strategy'}. "
                body += "Understanding these connections helps professionals make better decisions. "
                body += "The key takeaway is that thoughtful analysis drives meaningful results. "
                body += "Consider how this applies to your own work and professional development."
                words = body.split()

            # Trim to max 500 words
            words = body.split()[:500]
            body = " ".join(words)

            post = f"{hook}{body}\n\n#ProfessionalDevelopment #Strategy"
            posts.append(post)

        return posts[:count]

    def generate_email_snippets(self, parsed_content: dict, count: int = 2) -> List[str]:
        """Newsletter-style excerpts with CTA."""
        title = parsed_content.get("title", "Weekly Update")
        paragraphs = parsed_content.get("paragraphs", [])
        snippets = []

        for i in range(min(count, max(1, len(paragraphs)))):
            excerpt = paragraphs[i] if i < len(paragraphs) else title
            snippet = (
                f"Subject: {title}\n\n"
                f"Hi there,\n\n"
                f"{excerpt[:500]}\n\n"
                f"Read the full piece for more insights.\n\n"
                f"Best regards,\n"
                f"Your Content Team"
            )
            snippets.append(snippet)

        return snippets[:count]

    def generate_instagram_caption(self, parsed_content: dict) -> str:
        """Engaging caption with hashtags, <=2200 chars."""
        title = parsed_content.get("title", "")
        topics = parsed_content.get("key_topics", [])
        paragraphs = parsed_content.get("paragraphs", [])

        body = paragraphs[0][:500] if paragraphs else title
        hashtags = " ".join(f"#{t}" for t in topics[:10])

        caption = f"{title}\n\n{body}\n\n{hashtags}"
        return caption[:2200]

    def repurpose_all(self, parsed_content: dict) -> dict:
        """Run all generators, return all formats."""
        return {
            "tweets": self.generate_tweets(parsed_content),
            "linkedin": self.generate_linkedin_posts(parsed_content),
            "email": self.generate_email_snippets(parsed_content),
            "instagram": self.generate_instagram_caption(parsed_content),
        }
