import os
import json
import uuid
import time
import datetime

# Approval time-to-live in seconds
APPROVAL_TTL = 300  # 5 minutes
MAX_REQUESTS_PER_MINUTE = 30


class MCPSentry:
    def __init__(self, data_dir="/home/lancelot/data"):
        self.data_dir = data_dir
        self.configs_dir = os.path.join(data_dir, "mcp_configs")
        self.pending_requests = {}  # Map request_id -> status
        self.audit_file = os.path.join(data_dir, "MEMORY_SUMMARY.md")
        self._rate_tracker = {}  # Map tool_name -> list of timestamps

        # Ensure configs dir exists
        if not os.path.exists(self.configs_dir):
            os.makedirs(self.configs_dir)

        self.tools = self.discover_tools()

    def discover_tools(self):
        """Scans mcp_configs for available tools."""
        tools = {}
        if not os.path.exists(self.configs_dir):
            return tools

        for filename in os.listdir(self.configs_dir):
            if filename.endswith(".json"):
                try:
                    with open(os.path.join(self.configs_dir, filename), "r") as f:
                        config = json.load(f)
                        # Assume config is list of tools or single tool dict
                        if isinstance(config, list):
                            for t in config:
                                tools[t.get("name")] = t
                        else:
                            tools[config.get("name")] = config
                except Exception as e:
                    print(f"Error loading MCP config {filename}: {e}")
        return tools

    def _cleanup_expired(self):
        """Removes expired approval entries from pending_requests."""
        now = time.time()
        expired = [
            rid for rid, req in self.pending_requests.items()
            if req.get("_created_at", 0) + APPROVAL_TTL < now
        ]
        for rid in expired:
            del self.pending_requests[rid]

    def _check_rate_limit(self, tool_name: str) -> bool:
        """Returns True if rate limit is exceeded for the given tool."""
        now = time.time()
        window_start = now - 60

        if tool_name not in self._rate_tracker:
            self._rate_tracker[tool_name] = []

        # Clean old entries
        self._rate_tracker[tool_name] = [
            t for t in self._rate_tracker[tool_name] if t > window_start
        ]

        if len(self._rate_tracker[tool_name]) >= MAX_REQUESTS_PER_MINUTE:
            return True

        self._rate_tracker[tool_name].append(now)
        return False

    def check_permission(self, tool_name: str, params: dict) -> dict:
        """
        Checks if tool execution requires approval.
        Returns: {"status": "APPROVED" | "PENDING" | "DENIED", "message": "...", "request_id": "..."}
        """
        # Cleanup expired approvals
        self._cleanup_expired()

        # Rate limiting
        if self._check_rate_limit(tool_name):
            return {
                "status": "DENIED",
                "message": f"Rate limit exceeded for '{tool_name}'. Try again later.",
                "request_id": None,
            }

        tool_config = self.tools.get(tool_name)

        # SECURITY: Unknown tools default to HIGH risk
        risk_level = "high"
        if tool_config:
            risk_level = tool_config.get("risk", "low").lower()

        if risk_level == "high":
            # Check if there's an already approved request for these exact parameters
            for req_id, req in self.pending_requests.items():
                if (req["tool"] == tool_name
                        and req["params"] == params
                        and req["status"] == "APPROVED"):
                    # Check if approval is still within TTL
                    if req.get("_created_at", 0) + APPROVAL_TTL > time.time():
                        return {"status": "APPROVED", "message": "Previously Approved", "request_id": req_id}

            request_id = str(uuid.uuid4())
            self.pending_requests[request_id] = {
                "tool": tool_name,
                "params": params,
                "status": "PENDING",
                "timestamp": datetime.datetime.utcnow().isoformat(),
                "_created_at": time.time(),
            }

            # Log permission check
            self._log_permission_check(tool_name, params, "PENDING")

            return {
                "status": "PENDING",
                "message": f"High-Risk Action detected: {tool_name}. Approval Card sent to Chat.",
                "request_id": request_id,
            }

        # Log permission check for approved low/medium risk
        self._log_permission_check(tool_name, params, "APPROVED")

        return {"status": "APPROVED", "message": "Access Granted", "request_id": None}

    def approve_request(self, request_id: str) -> bool:
        """Callback to approve a pending request."""
        if request_id in self.pending_requests:
            self.pending_requests[request_id]["status"] = "APPROVED"
            return True
        return False

    def deny_request(self, request_id: str) -> bool:
        """Explicitly denies a pending request."""
        if request_id in self.pending_requests:
            self.pending_requests[request_id]["status"] = "DENIED"
            return True
        return False

    def _log_permission_check(self, tool_name: str, params: dict, result: str):
        """Logs all permission checks to audit file."""
        timestamp = datetime.datetime.utcnow().isoformat()
        log_entry = (
            f"\n- **MCP Permission Check** [{timestamp}]\n"
            f"    - Tool: `{tool_name}`\n"
            f"    - Result: `{result}`\n"
        )
        try:
            with open(self.audit_file, "a") as f:
                f.write(log_entry)
        except Exception as e:
            print(f"Error logging MCP permission check: {e}")

    def log_execution(self, tool_name: str, params: dict, output: str):
        """Logs execution to Tier B Audit Memory."""
        timestamp = datetime.datetime.utcnow().isoformat()
        log_entry = (
            f"\n- **MCP Execution** [{timestamp}]\n"
            f"    - Tool: `{tool_name}`\n"
            f"    - Params: `{json.dumps(params)}`\n"
            f"    - Result: `{str(output)[:100]}...`\n"
        )
        try:
            with open(self.audit_file, "a") as f:
                f.write(log_entry)
        except Exception as e:
            print(f"Error logging MCP execution: {e}")
