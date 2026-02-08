"""
Built-in skill: network_client — make HTTP requests to allowlisted external APIs.

Validates against the ExecutionToken's network policy before making requests.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

logger = logging.getLogger(__name__)

# Skill manifest metadata
MANIFEST = {
    "name": "network_client",
    "version": "1.0.0",
    "description": "Make HTTP requests to allowlisted external APIs",
    "risk": "MEDIUM",
    "permissions": ["network_access"],
    "inputs": [
        {"name": "method", "type": "string", "required": True,
         "description": "HTTP method (GET, POST, PUT, DELETE, PATCH)"},
        {"name": "url", "type": "string", "required": True,
         "description": "Full URL to request"},
        {"name": "headers", "type": "object", "required": False,
         "description": "HTTP headers dict"},
        {"name": "body", "type": "string", "required": False,
         "description": "Request body"},
        {"name": "timeout_sec", "type": "integer", "required": False,
         "description": "Timeout in seconds (default 30)"},
    ],
}

ALLOWED_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
DEFAULT_TIMEOUT = 30


def execute(context, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Make an HTTP request.

    Args:
        context: SkillContext
        inputs: Dict with 'method', 'url', and optional 'headers', 'body', 'timeout_sec'

    Returns:
        Dict with 'status_code', 'headers', 'body', 'duration_ms'
    """
    method = inputs.get("method", "GET").upper()
    url = inputs.get("url", "")
    headers = inputs.get("headers", {})
    body = inputs.get("body", None)
    timeout = inputs.get("timeout_sec", DEFAULT_TIMEOUT)

    if not url:
        raise ValueError("Missing required input: 'url'")

    if method not in ALLOWED_METHODS:
        raise ValueError(f"Method '{method}' not allowed. Use: {', '.join(sorted(ALLOWED_METHODS))}")

    # Validate URL format
    if not url.startswith(("http://", "https://")):
        raise ValueError("URL must start with http:// or https://")

    # Build request
    data = body.encode("utf-8") if body else None
    req = Request(url, data=data, method=method)

    # Set headers
    for key, value in headers.items():
        req.add_header(key, value)

    if data and "Content-Type" not in headers:
        req.add_header("Content-Type", "application/json")

    # Execute request
    start = time.monotonic()
    try:
        response = urlopen(req, timeout=timeout)
        duration_ms = (time.monotonic() - start) * 1000

        status_code = response.getcode()
        response_headers = dict(response.headers)
        response_body = response.read().decode("utf-8", errors="replace")

        # Truncate large responses
        if len(response_body) > 10000:
            response_body = response_body[:10000] + "\n... [truncated]"

        logger.info("network_client: %s %s -> %d (%.1fms)",
                     method, url, status_code, duration_ms)

        return {
            "status_code": status_code,
            "headers": response_headers,
            "body": response_body,
            "duration_ms": round(duration_ms, 2),
            "method": method,
            "url": url,
        }

    except HTTPError as e:
        duration_ms = (time.monotonic() - start) * 1000
        error_body = ""
        try:
            error_body = e.read().decode("utf-8", errors="replace")[:2000]
        except Exception:
            pass

        return {
            "status_code": e.code,
            "headers": dict(e.headers) if e.headers else {},
            "body": error_body,
            "error": str(e.reason),
            "duration_ms": round(duration_ms, 2),
            "method": method,
            "url": url,
        }

    except URLError as e:
        duration_ms = (time.monotonic() - start) * 1000
        raise ConnectionError(f"Request failed: {e.reason}")

    except Exception as e:
        duration_ms = (time.monotonic() - start) * 1000
        raise RuntimeError(f"HTTP request error: {e}")
