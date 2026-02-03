import re
import json
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError

from security import NetworkInterceptor


class PostDispatcher:
    """Routes content to different platforms based on tagged prompts."""

    def __init__(self, vault=None):
        self.vault = vault
        self.platforms = {}  # name -> {"handler": callable, "endpoint": str, "mode": str}

    def register_platform(self, name: str, handler=None, endpoint: str = None, mode: str = "local") -> bool:
        """
        Registers a platform target.

        Args:
            name: Platform identifier (e.g., "twitter", "slack").
            handler: Callable for local mode (receives content, returns response).
            endpoint: URL for http mode.
            mode: "local" (call handler), "http" (POST to endpoint), or "mcp" (MCP server ref).

        Returns:
            True if registered successfully.
        """
        if mode == "local" and handler is None:
            return False
        if mode == "http" and endpoint is None:
            return False

        self.platforms[name] = {
            "handler": handler,
            "endpoint": endpoint,
            "mode": mode,
        }
        return True

    def parse_tags(self, prompt: str) -> list:
        """
        Parses platform routing tags from prompt text.

        Tag format: [platform:mode:target]
        Examples: [twitter:local:post], [slack:http:webhook], [blog:mcp:publish]

        Returns:
            List of dicts: [{"platform": str, "mode": str, "target": str}]
        """
        tags = []
        pattern = re.compile(r'\[(\w+):(\w+):(\w+)\]')
        for match in pattern.finditer(prompt):
            tags.append({
                "platform": match.group(1),
                "mode": match.group(2),
                "target": match.group(3),
            })
        return tags

    def dispatch(self, content: str, platform: str, mode: str = "local", target: str = None) -> dict:
        """
        Routes content to a registered platform.

        Args:
            content: The content to dispatch.
            platform: Target platform name.
            mode: Dispatch mode (local/http/mcp).
            target: Additional target context (e.g., action name).

        Returns:
            {"status": "success"|"error", "platform": str, "response": ...}
        """
        platform_config = self.platforms.get(platform)
        if not platform_config:
            return {
                "status": "error",
                "platform": platform,
                "response": f"Platform '{platform}' is not registered",
            }

        try:
            if mode == "local" or platform_config["mode"] == "local":
                return self._dispatch_local(content, platform, platform_config, target)
            elif mode == "http" or platform_config["mode"] == "http":
                return self._dispatch_http(content, platform, platform_config, target)
            elif mode == "mcp" or platform_config["mode"] == "mcp":
                return self._dispatch_mcp(content, platform, platform_config, target)
            else:
                return {
                    "status": "error",
                    "platform": platform,
                    "response": f"Unknown dispatch mode: {mode}",
                }
        except Exception as e:
            return {
                "status": "error",
                "platform": platform,
                "response": str(e),
            }

    def _dispatch_local(self, content, platform, config, target):
        """Dispatches via local handler function."""
        handler = config.get("handler")
        if not handler:
            return {"status": "error", "platform": platform, "response": "No local handler registered"}

        response = handler(content)
        return {"status": "success", "platform": platform, "response": response}

    def _dispatch_http(self, content, platform, config, target):
        """Dispatches via HTTP POST to registered endpoint."""
        endpoint = config.get("endpoint")
        if not endpoint:
            return {"status": "error", "platform": platform, "response": "No HTTP endpoint registered"}

        # Security check: validate endpoint URL against allowlist and SSRF protections
        net_interceptor = NetworkInterceptor()
        if not net_interceptor.check_url(endpoint):
            return {"status": "error", "platform": platform, "response": "URL blocked by security policy"}

        headers = {"Content-Type": "application/json"}

        # Inject auth from vault if available
        if self.vault:
            api_key = self.vault.retrieve(f"{platform}_api_key")
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

        payload = json.dumps({"content": content, "target": target}).encode("utf-8")
        try:
            req = Request(endpoint, data=payload, headers=headers, method="POST")
            with urlopen(req, timeout=10) as response:
                resp_data = response.read().decode("utf-8", errors="replace")
                return {"status": "success", "platform": platform, "response": resp_data}
        except (URLError, Exception) as e:
            return {"status": "error", "platform": platform, "response": f"HTTP dispatch failed: {e}"}

    def _dispatch_mcp(self, content, platform, config, target):
        """Dispatches via MCP server reference (simulated)."""
        return {
            "status": "success",
            "platform": platform,
            "response": f"[MCP] Dispatched to {platform} via MCP server (target: {target})",
        }

    def dispatch_from_prompt(self, prompt: str, content: str) -> list:
        """
        Parses platform tags from the prompt and dispatches content to each.

        Args:
            prompt: Prompt text containing [platform:mode:target] tags.
            content: The content to dispatch.

        Returns:
            List of dispatch result dicts.
        """
        tags = self.parse_tags(prompt)
        results = []
        for tag in tags:
            result = self.dispatch(
                content=content,
                platform=tag["platform"],
                mode=tag["mode"],
                target=tag["target"],
            )
            results.append(result)
        return results
