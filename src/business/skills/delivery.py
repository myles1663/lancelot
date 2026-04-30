"""
Delivery Skill — formats and prepares content for delivery.

Final stage of the content repurposing pipeline: packages content
into email-ready format and creates posting schedules.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List

from src.skills.security.manifest import validate_manifest


# ── Manifest ─────────────────────────────────────────────────────

DELIVERY_MANIFEST = {
    "id": "content-delivery",
    "name": "Content Delivery",
    "version": "1.0.0",
    "author": "lancelot",
    "source": "first-party",
    "description": "Formats and delivers repurposed content",
    "capabilities_required": [
        {"capability": "connector.write", "description": "Send email delivery"},
    ],
    "target_domains": ["googleapis.com"],
    "credentials": [
        {"vault_key": "email.gmail_token", "type": "oauth_token",
         "purpose": "Send delivery emails"},
    ],
    "does_not_access": ["Calendar", "Contacts"],
}


class DeliverySkill:
    """Formats and prepares content for delivery."""

    def format_email_package(
        self,
        client_email: str,
        repurposed: dict,
        quality: object,
    ) -> dict:
        """Format repurposed content into email parameters.

        Returns dict compatible with EmailConnector.send_message params.
        """
        tweets = repurposed.get("tweets", [])
        linkedin = repurposed.get("linkedin", [])

        tweet_section = "\n".join(f"  - {t}" for t in tweets[:5])
        linkedin_section = "\n\n---\n\n".join(linkedin[:2])

        body = (
            f"Hi,\n\n"
            f"Your repurposed content is ready! Here's what we generated:\n\n"
            f"## Tweets ({len(tweets)} ready)\n{tweet_section}\n\n"
            f"## LinkedIn Posts ({len(linkedin)} ready)\n{linkedin_section}\n\n"
            f"Quality Score: {getattr(quality, 'score', 'N/A')}\n\n"
            f"Best regards,\nLancelot Content Team"
        )

        return {
            "to": client_email,
            "subject": "Your Repurposed Content is Ready",
            "body": body,
        }

    def create_delivery_schedule(self, repurposed: dict) -> List[dict]:
        """Create posting schedule: which content posts when."""
        schedule = []
        base_time = datetime.now(timezone.utc)

        # Schedule tweets: every 4 hours
        for i, tweet in enumerate(repurposed.get("tweets", [])):
            schedule.append({
                "platform": "twitter",
                "content": tweet,
                "scheduled_time": (base_time + timedelta(hours=4 * i)).isoformat(),
            })

        # Schedule LinkedIn: every 24 hours
        for i, post in enumerate(repurposed.get("linkedin", [])):
            schedule.append({
                "platform": "linkedin",
                "content": post,
                "scheduled_time": (base_time + timedelta(days=i + 1)).isoformat(),
            })

        return schedule

    def prepare_social_posts(self, repurposed: dict, platform: str) -> List[dict]:
        """Format content for specific platform API."""
        posts = []

        if platform == "twitter":
            for tweet in repurposed.get("tweets", []):
                posts.append({
                    "text": tweet,
                    "platform": "twitter",
                })

        elif platform == "linkedin":
            for post in repurposed.get("linkedin", []):
                posts.append({
                    "text": post,
                    "platform": "linkedin",
                    "visibility": "public",
                })

        return posts
