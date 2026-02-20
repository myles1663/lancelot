"""
Built-in skill: daily_news_brief — fetch breaking AI news and deliver via Telegram.

Fetches RSS/Atom feeds from major AI news sources, filters to the last 24 hours,
deduplicates, formats a Markdown briefing, and sends it to Telegram.
"""

from __future__ import annotations

import html
import logging
import re
import ssl
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

MANIFEST = {
    "name": "daily_news_brief",
    "version": "1.0.0",
    "description": "Fetch breaking AI news from RSS feeds and deliver a summary via Telegram",
    "risk": "LOW",
    "permissions": ["network_read", "telegram.write"],
    "inputs": [
        {"name": "max_articles", "type": "integer", "required": False,
         "description": "Maximum number of articles to include (default 15)"},
        {"name": "chat_id", "type": "string", "required": False,
         "description": "Override Telegram chat ID (uses default if omitted)"},
        {"name": "hours_lookback", "type": "integer", "required": False,
         "description": "Hours to look back for articles (default 24)"},
    ],
}

# ---------------------------------------------------------------------------
# RSS feed sources
# ---------------------------------------------------------------------------

_RSS_FEEDS: List[tuple] = [
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("The Verge AI", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/technology-lab"),
    ("VentureBeat AI", "https://venturebeat.com/category/ai/feed/"),
    ("MIT Tech Review", "https://www.technologyreview.com/feed/"),
]

_USER_AGENT = "Lancelot-AI-Agent/1.0"
_FETCH_TIMEOUT = 15
_MAX_SUMMARY_LEN = 200
_ATOM_NS = "{http://www.w3.org/2005/Atom}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_date(date_str: str) -> Optional[datetime]:
    """Parse RSS (RFC 2822) or Atom (ISO 8601) date strings to aware datetime."""
    if not date_str:
        return None
    date_str = date_str.strip()

    # RFC 2822 (common in RSS: "Thu, 20 Feb 2026 10:30:00 +0000")
    try:
        return parsedate_to_datetime(date_str)
    except (ValueError, TypeError):
        pass

    # ISO 8601 (common in Atom: "2026-02-20T10:30:00Z")
    try:
        cleaned = date_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        pass

    return None


def _strip_html(text: str) -> str:
    """Remove HTML tags, unescape entities, and truncate."""
    if not text:
        return ""
    cleaned = re.sub(r"<[^>]+>", "", text)
    cleaned = html.unescape(cleaned).strip()
    # Collapse whitespace
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) > _MAX_SUMMARY_LEN:
        cleaned = cleaned[:_MAX_SUMMARY_LEN].rsplit(" ", 1)[0] + "..."
    return cleaned


def _fetch_feed(name: str, url: str, cutoff: datetime) -> List[Dict[str, Any]]:
    """Fetch and parse a single RSS/Atom feed. Returns articles newer than cutoff."""
    req = Request(url)
    req.add_header("User-Agent", _USER_AGENT)
    req.add_header("Accept", "application/rss+xml, application/atom+xml, application/xml, text/xml")

    ctx = ssl.create_default_context()
    resp = urlopen(req, timeout=_FETCH_TIMEOUT, context=ctx)
    data = resp.read()

    root = ET.fromstring(data)
    articles: List[Dict[str, Any]] = []

    # Detect RSS vs Atom
    if root.tag == "rss" or root.find("channel") is not None:
        articles = _parse_rss(root, name, cutoff)
    elif root.tag.endswith("feed") or root.find(f"{_ATOM_NS}entry") is not None:
        articles = _parse_atom(root, name, cutoff)
    else:
        logger.warning("daily_news_brief: unknown feed format for '%s'", name)

    return articles


def _parse_rss(root: ET.Element, source: str, cutoff: datetime) -> List[Dict[str, Any]]:
    """Parse RSS 2.0 <channel><item> structure."""
    articles = []
    channel = root.find("channel")
    if channel is None:
        return articles

    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date_str = item.findtext("pubDate") or ""
        description = item.findtext("description") or ""

        if not title or not link:
            continue

        published = _parse_date(pub_date_str)
        if published and published < cutoff:
            continue

        articles.append({
            "source": source,
            "title": title,
            "link": link,
            "summary": _strip_html(description),
            "published": published,
        })

    return articles


def _parse_atom(root: ET.Element, source: str, cutoff: datetime) -> List[Dict[str, Any]]:
    """Parse Atom <feed><entry> structure."""
    articles = []

    # Try with and without namespace
    entries = root.findall(f"{_ATOM_NS}entry")
    if not entries:
        entries = root.findall("entry")

    for entry in entries:
        title = (entry.findtext(f"{_ATOM_NS}title") or entry.findtext("title") or "").strip()

        # Atom link is an attribute: <link href="..." />
        link_el = entry.find(f"{_ATOM_NS}link") or entry.find("link")
        link = (link_el.get("href", "") if link_el is not None else "").strip()

        pub_str = (entry.findtext(f"{_ATOM_NS}published")
                   or entry.findtext("published")
                   or entry.findtext(f"{_ATOM_NS}updated")
                   or entry.findtext("updated")
                   or "")

        summary = (entry.findtext(f"{_ATOM_NS}summary")
                   or entry.findtext("summary")
                   or entry.findtext(f"{_ATOM_NS}content")
                   or entry.findtext("content")
                   or "")

        if not title or not link:
            continue

        published = _parse_date(pub_str)
        if published and published < cutoff:
            continue

        articles.append({
            "source": source,
            "title": title,
            "link": link,
            "summary": _strip_html(summary),
            "published": published,
        })

    return articles


def _format_briefing(articles: List[Dict[str, Any]]) -> str:
    """Format articles into a Telegram Markdown message."""
    now_est = datetime.now(timezone.utc).astimezone()
    date_str = now_est.strftime("%B %d, %Y")

    if not articles:
        return (
            f"*AI News Briefing*\n"
            f"_{date_str}_\n\n"
            f"No breaking AI news in the last 24 hours."
        )

    lines = [
        f"*AI News Briefing*",
        f"_{date_str}_\n",
    ]

    sources_seen = set()
    for i, article in enumerate(articles, 1):
        title = article["title"].replace("[", "(").replace("]", ")")
        link = article["link"]
        source = article["source"]
        summary = article.get("summary", "")
        sources_seen.add(source)

        lines.append(f"*{i}.* [{title}]({link})")
        lines.append(f"_{source}_")
        if summary:
            lines.append(summary)
        lines.append("")  # blank line between articles

    lines.append("---")
    lines.append(f"_{len(articles)} articles from {len(sources_seen)} sources | Powered by Lancelot_")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------


def execute(context: Any, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch AI news from RSS feeds and send a briefing via Telegram.

    Args:
        context: SkillContext (unused beyond logging)
        inputs: Dict with optional 'max_articles', 'chat_id', 'hours_lookback'

    Returns:
        Dict with delivery status and article stats.
    """
    max_articles = inputs.get("max_articles", 15)
    chat_id_override = inputs.get("chat_id", None)
    hours = inputs.get("hours_lookback", 24)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    # 1. Fetch all feeds
    all_articles: List[Dict[str, Any]] = []
    feed_errors: List[str] = []

    for name, url in _RSS_FEEDS:
        try:
            articles = _fetch_feed(name, url, cutoff)
            all_articles.extend(articles)
            logger.info("daily_news_brief: '%s' returned %d articles", name, len(articles))
        except Exception as e:
            feed_errors.append(f"{name}: {e}")
            logger.warning("daily_news_brief: feed '%s' failed: %s", name, e)

    # 2. Deduplicate by normalized title
    seen_titles: set = set()
    unique: List[Dict[str, Any]] = []
    for a in all_articles:
        key = a["title"].lower().strip()
        if key not in seen_titles:
            seen_titles.add(key)
            unique.append(a)

    # 3. Sort by published date (newest first); articles without dates go last
    _min_dt = datetime.min.replace(tzinfo=timezone.utc)
    unique.sort(key=lambda a: a["published"] or _min_dt, reverse=True)

    # 4. Truncate to max_articles
    articles = unique[:max_articles]

    # 5. Format the briefing
    message = _format_briefing(articles)

    # 6. Send via telegram_send
    try:
        from src.core.skills.builtins import telegram_send
    except ImportError:
        try:
            from skills.builtins import telegram_send
        except ImportError:
            return {
                "status": "error",
                "error": "Cannot import telegram_send module",
                "articles_found": len(unique),
            }

    send_result = telegram_send._send_text(message, chat_id_override)

    # 7. Return stats
    return {
        "status": send_result.get("status", "error"),
        "articles_found": len(unique),
        "articles_sent": len(articles),
        "feeds_checked": len(_RSS_FEEDS),
        "feeds_failed": len(feed_errors),
        "feed_errors": feed_errors if feed_errors else None,
        "telegram_result": send_result,
    }
