"""
Built-in skill: daily_news_brief — fetch breaking AI news and deliver via Telegram.

Multi-source intelligence gathering:
1. RSS/Atom feeds from 15+ diverse sources (tech, research, industry, policy)
2. Google News search as a supplemental discovery engine
3. Source diversity balancing — caps per-source articles to ensure breadth
4. AI-relevance keyword filtering for general feeds
5. Deduplication by normalized title similarity
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import ssl
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

MANIFEST = {
    "name": "daily_news_brief",
    "version": "2.0.0",
    "description": "Fetch breaking AI news from 15+ sources and deliver a summary via Telegram",
    "risk": "LOW",
    "permissions": ["network_read", "telegram.write"],
    "inputs": [
        {"name": "max_articles", "type": "integer", "required": False,
         "description": "Maximum number of articles to include (default 15)"},
        {"name": "chat_id", "type": "string", "required": False,
         "description": "Override Telegram chat ID (uses default if omitted)"},
        {"name": "hours_lookback", "type": "integer", "required": False,
         "description": "Hours to look back for articles (default 24)"},
        {"name": "max_per_source", "type": "integer", "required": False,
         "description": "Max articles from any single source (default 3)"},
    ],
}

# ---------------------------------------------------------------------------
# RSS feed sources — organized by category for diversity
# ---------------------------------------------------------------------------

# (source_name, url, is_ai_specific)
# is_ai_specific=True means all articles are relevant; False means filter by keywords
_RSS_FEEDS: List[tuple] = [
    # ── Major Tech News (AI sections) ──
    ("TechCrunch", "https://techcrunch.com/category/artificial-intelligence/feed/", True),
    ("The Verge", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", True),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/technology-lab", False),
    ("VentureBeat", "https://venturebeat.com/category/ai/feed/", True),
    ("Wired", "https://www.wired.com/feed/tag/ai/latest/rss", True),
    ("CNBC Tech", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910", False),

    # ── AI-Specific Publications ──
    ("MIT Tech Review", "https://www.technologyreview.com/feed/", False),
    ("Synced AI", "https://syncedreview.com/feed/", True),
    ("AI News", "https://www.artificialintelligence-news.com/feed/", True),
    ("Towards Data Science", "https://towardsdatascience.com/feed", False),
    ("Marktechpost", "https://www.marktechpost.com/feed/", True),
    ("The Decoder", "https://the-decoder.com/feed/", True),

    # ── Research & Labs ──
    ("Google AI Blog", "https://blog.research.google/feeds/posts/default/-/AI", True),
    ("OpenAI Blog", "https://openai.com/blog/rss.xml", True),
    ("Hugging Face Blog", "https://huggingface.co/blog/feed.xml", True),

    # ── Business & Industry ──
    ("Bloomberg AI", "https://feeds.bloomberg.com/technology/news.rss", False),
    ("InfoQ AI", "https://feed.infoq.com/ai-ml-data-eng/", True),
    ("The Register AI", "https://www.theregister.com/software/ai_ml/headlines.atom", True),
    ("ZDNet AI", "https://www.zdnet.com/topic/artificial-intelligence/rss.xml", True),
]

# Keywords for filtering AI-relevant articles from general tech feeds
_AI_KEYWORDS = {
    "ai", "artificial intelligence", "machine learning", "deep learning",
    "neural network", "llm", "large language model", "gpt", "claude",
    "gemini", "openai", "anthropic", "google ai", "meta ai", "deepmind",
    "chatbot", "generative ai", "gen ai", "copilot", "transformer",
    "diffusion model", "stable diffusion", "midjourney", "dall-e",
    "autonomous agent", "agi", "alignment", "foundation model",
    "hugging face", "nvidia ai", "inference", "fine-tuning", "rlhf",
    "computer vision", "nlp", "natural language", "robotics ai",
    "ai regulation", "ai safety", "ai policy", "ai ethics",
    "ai chip", "ai hardware", "gpu", "tpu",
}

# Google News RSS for supplemental discovery
_GOOGLE_NEWS_QUERIES = [
    "artificial intelligence news today",
    "AI breakthrough",
    "AI startup funding",
]
_GOOGLE_NEWS_URL = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

_USER_AGENT = "Mozilla/5.0 (compatible; Lancelot-AI-Agent/2.0)"
_FETCH_TIMEOUT = 12
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


def _is_ai_relevant(title: str, summary: str) -> bool:
    """Check if an article is AI-relevant based on title and summary keywords."""
    text = (title + " " + summary).lower()
    return any(kw in text for kw in _AI_KEYWORDS)


def _title_fingerprint(title: str) -> str:
    """Normalize a title for deduplication. Strips punctuation and lowercases."""
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


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


def _fetch_google_news(query: str, cutoff: datetime) -> List[Dict[str, Any]]:
    """Fetch AI news from Google News RSS. Returns articles as a supplemental source."""
    url = _GOOGLE_NEWS_URL.format(query=quote_plus(query))
    req = Request(url)
    req.add_header("User-Agent", _USER_AGENT)

    ctx = ssl.create_default_context()
    resp = urlopen(req, timeout=_FETCH_TIMEOUT, context=ctx)
    data = resp.read()

    root = ET.fromstring(data)
    articles = _parse_rss(root, "Google News", cutoff)

    # Google News wraps the real source in the title: "Article Title - Source Name"
    for a in articles:
        title = a["title"]
        if " - " in title:
            parts = title.rsplit(" - ", 1)
            a["title"] = parts[0].strip()
            a["source"] = parts[1].strip()

    return articles


def _diversity_select(
    articles: List[Dict[str, Any]],
    max_articles: int,
    max_per_source: int,
) -> List[Dict[str, Any]]:
    """Select articles ensuring source diversity.

    Round-robin across sources so no single source dominates.
    Respects max_per_source cap per source.
    """
    # Group by source
    by_source: Dict[str, List[Dict[str, Any]]] = {}
    for a in articles:
        src = a["source"]
        by_source.setdefault(src, []).append(a)

    # Sort each source's articles by date (newest first)
    _min_dt = datetime.min.replace(tzinfo=timezone.utc)
    for src in by_source:
        by_source[src].sort(key=lambda a: a["published"] or _min_dt, reverse=True)
        # Apply per-source cap
        by_source[src] = by_source[src][:max_per_source]

    # Round-robin selection
    selected: List[Dict[str, Any]] = []
    source_names = sorted(by_source.keys())
    idx = 0
    while len(selected) < max_articles and source_names:
        src = source_names[idx % len(source_names)]
        if by_source[src]:
            selected.append(by_source[src].pop(0))
        else:
            source_names.remove(src)
            if not source_names:
                break
            idx = idx % len(source_names)
            continue
        idx += 1

    return selected


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
    """Fetch AI news from RSS feeds + Google News and send a briefing via Telegram.

    Args:
        context: SkillContext (unused beyond logging)
        inputs: Dict with optional 'max_articles', 'chat_id', 'hours_lookback',
                'max_per_source'

    Returns:
        Dict with delivery status and article stats.
    """
    max_articles = inputs.get("max_articles", 15)
    max_per_source = inputs.get("max_per_source", 3)
    chat_id_override = inputs.get("chat_id", None)
    hours = inputs.get("hours_lookback", 24)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    # 1. Fetch all RSS feeds in parallel
    all_articles: List[Dict[str, Any]] = []
    feed_errors: List[str] = []
    feeds_succeeded = 0

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {}
        for name, url, is_ai_specific in _RSS_FEEDS:
            fut = pool.submit(_fetch_feed, name, url, cutoff)
            futures[fut] = (name, is_ai_specific)

        # Also submit Google News queries
        for query in _GOOGLE_NEWS_QUERIES:
            fut = pool.submit(_fetch_google_news, query, cutoff)
            futures[fut] = (f"Google News ({query})", True)

        for fut in as_completed(futures):
            name, is_ai_specific = futures[fut]
            try:
                articles = fut.result()
                # Filter non-AI-specific feeds for relevance
                if not is_ai_specific:
                    articles = [a for a in articles if _is_ai_relevant(a["title"], a.get("summary", ""))]
                all_articles.extend(articles)
                feeds_succeeded += 1
                logger.info("daily_news_brief: '%s' returned %d articles", name, len(articles))
            except Exception as e:
                feed_errors.append(f"{name}: {e}")
                logger.warning("daily_news_brief: feed '%s' failed: %s", name, e)

    # 2. Deduplicate by normalized title fingerprint
    seen: set = set()
    unique: List[Dict[str, Any]] = []
    for a in all_articles:
        fp = _title_fingerprint(a["title"])
        if fp and fp not in seen:
            seen.add(fp)
            unique.append(a)

    # 3. Sort by published date (newest first); articles without dates go last
    _min_dt = datetime.min.replace(tzinfo=timezone.utc)
    unique.sort(key=lambda a: a["published"] or _min_dt, reverse=True)

    # 4. Select with source diversity
    articles = _diversity_select(unique, max_articles, max_per_source)

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
    sources_in_report = {a["source"] for a in articles}
    return {
        "status": send_result.get("status", "error"),
        "articles_found": len(unique),
        "articles_sent": len(articles),
        "sources_in_report": sorted(sources_in_report),
        "feeds_checked": len(_RSS_FEEDS) + len(_GOOGLE_NEWS_QUERIES),
        "feeds_succeeded": feeds_succeeded,
        "feeds_failed": len(feed_errors),
        "feed_errors": feed_errors if feed_errors else None,
        "telegram_result": send_result,
    }
