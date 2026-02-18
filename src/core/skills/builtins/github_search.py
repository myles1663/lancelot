"""
Built-in skill: github_search — query GitHub's public REST API.

Provides structured access to GitHub repositories, commits, issues,
and releases with source URLs for every result.  Designed for
competitive intelligence and open-source project tracking.

Uses the public GitHub API (api.github.com).  Set the GITHUB_TOKEN
env var for higher rate limits (60/hr unauthenticated → 5000/hr).
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import time
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode

logger = logging.getLogger(__name__)

# Skill manifest metadata
MANIFEST = {
    "name": "github_search",
    "version": "1.0.0",
    "description": "Search GitHub for repositories, commits, issues, and releases",
    "risk": "LOW",
    "permissions": ["network_read"],
    "inputs": [
        {"name": "action", "type": "string", "required": True,
         "description": "One of: search_repos, get_commits, get_issues, get_releases"},
        {"name": "query", "type": "string", "required": False,
         "description": "Search query (for search_repos)"},
        {"name": "repo", "type": "string", "required": False,
         "description": "Repository in owner/repo format"},
        {"name": "limit", "type": "integer", "required": False,
         "description": "Max results to return (default 5)"},
        {"name": "state", "type": "string", "required": False,
         "description": "Issue/PR state filter: open, closed, all (default: all)"},
    ],
}

GITHUB_API = "https://api.github.com"
USER_AGENT = "Lancelot-AI-Agent/0.2.10"
DEFAULT_TIMEOUT = 15
DEFAULT_LIMIT = 5


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _github_request(endpoint: str, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Make a request to GitHub's REST API.

    Returns parsed JSON on success.
    Raises RuntimeError on failure with a descriptive message.
    """
    url = f"{GITHUB_API}{endpoint}"
    if params:
        qs = urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{url}?{qs}"

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }

    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = Request(url, headers=headers)

    try:
        # Create SSL context that works inside Docker
        ctx = ssl.create_default_context()
        with urlopen(req, timeout=DEFAULT_TIMEOUT, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))

            # Extract rate limit info from headers
            remaining = resp.headers.get("X-RateLimit-Remaining", "?")
            logger.info(f"github_search: {endpoint} -> 200 (rate remaining: {remaining})")
            return data
    except HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        if e.code == 403 and "rate limit" in body.lower():
            reset = e.headers.get("X-RateLimit-Reset", "unknown")
            raise RuntimeError(
                f"GitHub API rate limit exceeded. "
                f"{'Set GITHUB_TOKEN env var for 5000 req/hr.' if not token else ''} "
                f"Reset at: {reset}"
            )
        if e.code == 404:
            raise RuntimeError(f"GitHub resource not found: {endpoint}")
        raise RuntimeError(f"GitHub API error {e.code}: {body}")
    except URLError as e:
        raise RuntimeError(f"GitHub API connection error: {e.reason}")


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def _search_repos(query: str, limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
    """Search GitHub repositories by keyword."""
    if not query:
        return {"error": "query is required for search_repos"}

    data = _github_request("/search/repositories", {
        "q": query,
        "sort": "updated",
        "order": "desc",
        "per_page": str(min(limit, 30)),
    })

    repos = []
    for item in data.get("items", [])[:limit]:
        repos.append({
            "name": item.get("full_name", ""),
            "description": (item.get("description") or "")[:200],
            "stars": item.get("stargazers_count", 0),
            "forks": item.get("forks_count", 0),
            "language": item.get("language", ""),
            "updated_at": item.get("updated_at", ""),
            "url": item.get("html_url", ""),
            "open_issues": item.get("open_issues_count", 0),
        })

    return {
        "action": "search_repos",
        "query": query,
        "total_count": data.get("total_count", 0),
        "results": repos,
        "source": f"https://github.com/search?q={quote(query)}&type=repositories",
    }


def _get_commits(repo: str, limit: int = 10) -> Dict[str, Any]:
    """Get recent commits for a repository."""
    if not repo or "/" not in repo:
        return {"error": "repo must be in owner/repo format (e.g., 'anthropics/anthropic-sdk-python')"}

    data = _github_request(f"/repos/{repo}/commits", {
        "per_page": str(min(limit, 30)),
    })

    commits = []
    for item in data[:limit]:
        commit = item.get("commit", {})
        author = commit.get("author", {})
        commits.append({
            "sha": item.get("sha", "")[:8],
            "message": commit.get("message", "").split("\n")[0][:200],
            "author": author.get("name", ""),
            "date": author.get("date", ""),
            "url": item.get("html_url", ""),
        })

    return {
        "action": "get_commits",
        "repo": repo,
        "count": len(commits),
        "commits": commits,
        "source": f"https://github.com/{repo}/commits",
    }


def _get_issues(repo: str, state: str = "all", limit: int = 10) -> Dict[str, Any]:
    """Get recent issues and PRs for a repository."""
    if not repo or "/" not in repo:
        return {"error": "repo must be in owner/repo format"}

    if state not in ("open", "closed", "all"):
        state = "all"

    data = _github_request(f"/repos/{repo}/issues", {
        "state": state,
        "sort": "updated",
        "direction": "desc",
        "per_page": str(min(limit, 30)),
    })

    issues = []
    for item in data[:limit]:
        is_pr = "pull_request" in item
        labels = [lb.get("name", "") for lb in item.get("labels", [])]
        issues.append({
            "number": item.get("number", 0),
            "title": item.get("title", "")[:200],
            "state": item.get("state", ""),
            "type": "pull_request" if is_pr else "issue",
            "labels": labels,
            "author": (item.get("user") or {}).get("login", ""),
            "created_at": item.get("created_at", ""),
            "updated_at": item.get("updated_at", ""),
            "url": item.get("html_url", ""),
        })

    return {
        "action": "get_issues",
        "repo": repo,
        "state_filter": state,
        "count": len(issues),
        "issues": issues,
        "source": f"https://github.com/{repo}/issues",
    }


def _get_releases(repo: str, limit: int = 5) -> Dict[str, Any]:
    """Get recent releases for a repository."""
    if not repo or "/" not in repo:
        return {"error": "repo must be in owner/repo format"}

    data = _github_request(f"/repos/{repo}/releases", {
        "per_page": str(min(limit, 30)),
    })

    releases = []
    for item in data[:limit]:
        releases.append({
            "tag": item.get("tag_name", ""),
            "name": item.get("name", "")[:200],
            "published_at": item.get("published_at", ""),
            "prerelease": item.get("prerelease", False),
            "body": (item.get("body") or "")[:500],
            "url": item.get("html_url", ""),
        })

    return {
        "action": "get_releases",
        "repo": repo,
        "count": len(releases),
        "releases": releases,
        "source": f"https://github.com/{repo}/releases",
    }


# ---------------------------------------------------------------------------
# Skill entry point
# ---------------------------------------------------------------------------

def execute(context, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a GitHub search action.

    Args:
        context: SkillContext (unused — read-only API calls)
        inputs: Dict with 'action' and action-specific parameters

    Returns:
        Dict with structured results including source URLs
    """
    action = inputs.get("action", "search_repos")
    limit = inputs.get("limit", DEFAULT_LIMIT)

    start = time.time()
    try:
        if action == "search_repos":
            result = _search_repos(inputs.get("query", ""), limit)
        elif action == "get_commits":
            result = _get_commits(inputs.get("repo", ""), limit)
        elif action == "get_issues":
            result = _get_issues(inputs.get("repo", ""), inputs.get("state", "all"), limit)
        elif action == "get_releases":
            result = _get_releases(inputs.get("repo", ""), limit)
        else:
            result = {"error": f"Unknown action: {action}. Use: search_repos, get_commits, get_issues, get_releases"}

        duration_ms = round((time.time() - start) * 1000, 1)
        result["duration_ms"] = duration_ms
        logger.info(f"github_search: {action} -> {result.get('count', '?')} results ({duration_ms}ms)")
        return result

    except RuntimeError as e:
        duration_ms = round((time.time() - start) * 1000, 1)
        logger.warning(f"github_search: {action} failed ({duration_ms}ms): {e}")
        return {"error": str(e), "action": action, "duration_ms": duration_ms}
    except Exception as e:
        duration_ms = round((time.time() - start) * 1000, 1)
        logger.error(f"github_search: unexpected error: {e}")
        return {"error": f"Unexpected error: {e}", "action": action, "duration_ms": duration_ms}
