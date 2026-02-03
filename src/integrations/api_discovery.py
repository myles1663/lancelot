import re
import json
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError

from security import NetworkInterceptor


class APIDiscoveryEngine:
    """Scrapes API documentation and generates structured manifests and wrapper scripts."""

    def __init__(self, orchestrator=None):
        self.orchestrator = orchestrator
        self._net_interceptor = NetworkInterceptor()

    def scrape_docs(self, url_or_text: str) -> str:
        """
        Fetches API documentation from a URL or returns raw text as-is.

        Args:
            url_or_text: A URL starting with 'http' or raw documentation text.

        Returns:
            The documentation text content.
        """
        if url_or_text.strip().startswith("http"):
            # Security check: validate URL against allowlist and SSRF protections
            if not self._net_interceptor.check_url(url_or_text.strip()):
                return "Error fetching URL: URL blocked by security policy"
            try:
                req = Request(url_or_text, headers={"User-Agent": "Lancelot-Discovery/1.0"})
                with urlopen(req, timeout=10) as response:
                    return response.read().decode("utf-8", errors="replace")
            except (URLError, Exception) as e:
                return f"Error fetching URL: {e}"
        return url_or_text

    def generate_manifest(self, doc_text: str) -> dict:
        """
        Extracts API endpoints from documentation text into a structured manifest.

        Tries Gemini LLM first (if orchestrator available), falls back to regex extraction.

        Returns:
            {"api_name": str, "base_url": str, "endpoints": [...]}
        """
        # Try LLM extraction first
        if self.orchestrator and self.orchestrator.client:
            try:
                return self._llm_extract(doc_text)
            except Exception as e:
                print(f"LLM extraction failed, using regex fallback: {e}")

        return self._regex_fallback_extract(doc_text)

    def _llm_extract(self, doc_text: str) -> dict:
        """Uses Gemini to extract structured API manifest from docs."""
        prompt = (
            "Extract all API endpoints from the following documentation. "
            "Return ONLY valid JSON in this exact format:\n"
            '{"api_name": "...", "base_url": "...", "endpoints": ['
            '{"method": "GET|POST|PUT|DELETE", "path": "/...", '
            '"description": "...", "parameters": [{"name": "...", "type": "...", "required": true|false}]}'
            "]}\n\n"
            f"Documentation:\n{doc_text[:4000]}"
        )
        response = self.orchestrator.client.models.generate_content(
            model=self.orchestrator.model_name,
            contents=prompt,
        )
        # Extract JSON from response
        text = response.text
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            return json.loads(json_match.group())
        raise ValueError("No valid JSON found in LLM response")

    def _regex_fallback_extract(self, doc_text: str) -> dict:
        """
        Extracts endpoints using regex pattern matching.

        Recognizes patterns like:
            POST /api/posts - Create a new post
            GET /users/{id}
            PUT /api/v1/items/:id - Update an item
        """
        manifest = {
            "api_name": "Unknown API",
            "base_url": "",
            "endpoints": []
        }

        # Extract API name - prefer explicit "API Name:" label first
        name_match = re.search(r'(?:API|Service)\s*Name[:\s]+([^\n]+)', doc_text, re.IGNORECASE)
        if not name_match:
            # Fallback: look for "X API" in headings
            name_match = re.search(r'#\s*(.+?API)\b', doc_text, re.IGNORECASE)
        if name_match:
            manifest["api_name"] = name_match.group(1).strip()

        # Extract base URL
        base_match = re.search(r'(?:Base\s*URL|Host|Server)[:\s]+(https?://[^\s\n]+)', doc_text, re.IGNORECASE)
        if base_match:
            manifest["base_url"] = base_match.group(1).strip()

        # Extract endpoints: METHOD /path - description
        endpoint_pattern = re.compile(
            r'(GET|POST|PUT|DELETE|PATCH)\s+(/[^\s\n]+)(?:\s*[-:]\s*([^\n]*))?',
            re.IGNORECASE
        )
        seen = set()
        for match in endpoint_pattern.finditer(doc_text):
            method = match.group(1).upper()
            path = match.group(2).strip()
            description = (match.group(3) or "").strip()

            key = f"{method} {path}"
            if key in seen:
                continue
            seen.add(key)

            # Extract path parameters like {id} or :id
            params = []
            for param in re.findall(r'\{(\w+)\}|:(\w+)', path):
                param_name = param[0] or param[1]
                params.append({"name": param_name, "type": "string", "required": True})

            # Extract body/query parameters from nearby text (stop at next endpoint or heading)
            param_block_full = doc_text[match.end():match.end() + 500]
            # Truncate at next endpoint definition or markdown heading
            stop = re.search(r'(?:^|\n)\s*(?:GET|POST|PUT|DELETE|PATCH)\s+/|(?:^|\n)#{1,3}\s', param_block_full)
            param_block = param_block_full[:stop.start()] if stop else param_block_full

            seen_params = {p["name"] for p in params}
            for pm in re.finditer(r'[-*]\s*`?(\w+)`?\s*\((\w+)(?:,\s*(required|optional))?\)', param_block):
                pname = pm.group(1)
                if pname in seen_params:
                    continue
                seen_params.add(pname)
                params.append({
                    "name": pname,
                    "type": pm.group(2),
                    "required": pm.group(3) != "optional" if pm.group(3) else False
                })

            manifest["endpoints"].append({
                "method": method,
                "path": path,
                "description": description,
                "parameters": params
            })

        return manifest

    def generate_wrapper_script(self, manifest: dict) -> str:
        """
        Generates a Python script with functions for each API endpoint.

        Args:
            manifest: A structured API manifest dict.

        Returns:
            Python source code string.
        """
        base_url = manifest.get("base_url", "https://api.example.com")
        api_name = manifest.get("api_name", "API")
        lines = [
            f'# Auto-generated wrapper for {api_name}',
            f'# Base URL: {base_url}',
            '',
            'def make_request(method, url, headers=None, json_body=None):',
            '    """HTTP request helper - uses injected http_client if available."""',
            '    try:',
            '        return http_client.request(method, url, headers=headers, json=json_body)',
            '    except NameError:',
            '        print(f"[DRY RUN] {method} {url} headers={headers} body={json_body}")',
            '        return {"status": "dry_run"}',
            '',
        ]

        for endpoint in manifest.get("endpoints", []):
            method = endpoint["method"]
            path = endpoint["path"]
            description = endpoint.get("description", "")
            params = endpoint.get("parameters", [])

            # Build function name from method + path
            func_name = self._path_to_func_name(method, path)

            # Separate path params from body params
            path_param_names = set()
            for p in params:
                if "{" + p["name"] + "}" in path or ":" + p["name"] in path:
                    path_param_names.add(p["name"])

            # Deduplicate param names preserving order
            seen_names = set()
            unique_params = []
            for p in params:
                if p["name"] not in seen_names:
                    seen_names.add(p["name"])
                    unique_params.append(p)

            body_params_info = [(p["name"], p.get("required", False)) for p in unique_params if p["name"] not in path_param_names]
            required_params = [p["name"] for p in unique_params if p["name"] in path_param_names]

            # Build signature: required params first, then required body params,
            # then base_url with default, then optional body params, then headers
            sig_parts = []
            for rp in required_params:
                sig_parts.append(rp)
            required_body = [name for name, req in body_params_info if req]
            optional_body = [name for name, req in body_params_info if not req]
            for bp in required_body:
                sig_parts.append(bp)
            sig = ", ".join(sig_parts)
            if sig:
                sig = sig + ", "
            sig = f'{sig}base_url="{base_url}"'
            for op in optional_body:
                sig += f", {op}=None"
            sig += ", headers=None"

            body_params = [name for name, _ in body_params_info]

            lines.append(f'def {func_name}({sig}):')
            if description:
                lines.append(f'    """{description}"""')

            # Build URL - convert :param to {param} for f-string variables
            url_path = path
            for pp in path_param_names:
                url_path = url_path.replace(":" + pp, "{" + pp + "}")

            lines.append(f'    url = f"{{base_url}}{url_path}"')

            if body_params and method in ("POST", "PUT", "PATCH"):
                body_dict = ", ".join(f'"{p}": {p}' for p in body_params)
                lines.append(f'    body = {{{body_dict}}}')
                lines.append(f'    return make_request("{method}", url, headers=headers, json_body=body)')
            else:
                lines.append(f'    return make_request("{method}", url, headers=headers)')

            lines.append('')

        return "\n".join(lines)

    def _path_to_func_name(self, method: str, path: str) -> str:
        """Converts a method + path into a valid Python function name."""
        # Remove path parameters and special chars
        clean = re.sub(r'[{}\-:.]', '_', path)
        clean = re.sub(r'/+', '_', clean)
        clean = clean.strip('_')
        clean = re.sub(r'_+', '_', clean)
        return f"{method.lower()}_{clean}"
