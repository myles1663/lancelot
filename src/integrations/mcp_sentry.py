import os
import json
import uuid
import time
import datetime
import logging
import hashlib
import re

# Approval time-to-live in seconds
APPROVAL_TTL = 300  # 5 minutes
MAX_REQUESTS_PER_MINUTE = 30


logger = logging.getLogger(__name__)

REPO_WRITER_WORKSPACE_ACTIONS = {"create", "edit"}
REPO_WRITER_WORKSPACE_EXTENSIONS = {".txt", ".md", ".log", ".csv"}


def _stable_json_hash(value) -> str:
    try:
        rendered = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    except Exception:
        rendered = str(value)
    return hashlib.sha256(rendered.encode("utf-8", errors="replace")).hexdigest()


def _canonical_posix_path(value: str) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    is_absolute = raw.startswith("/")
    parts = []
    for part in raw.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            if parts:
                parts.pop()
            else:
                parts.append(part)
            continue
        parts.append(part)
    prefix = "/" if is_absolute else ""
    return prefix + "/".join(parts)


def _path_has_traversal(value: str) -> bool:
    return any(part == ".." for part in str(value or "").replace("\\", "/").split("/"))


def _is_under_path(path: str, root: str) -> bool:
    path = _canonical_posix_path(path).rstrip("/")
    root = _canonical_posix_path(root).rstrip("/")
    return bool(path and root and (path == root or path.startswith(f"{root}/")))


def _approval_text_signature(value: object) -> str:
    text = str(value or "")
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text.rstrip(" \t\r\n.,;:!")


def _repo_writer_target(params: dict) -> dict:
    workspace = _canonical_posix_path(
        params.get("workspace") or os.getenv("LANCELOT_WORKSPACE", "/home/lancelot/workspace")
    )
    rel_path = str(params.get("path") or "").strip()
    rel_path_normalized = _canonical_posix_path(rel_path)
    if rel_path_normalized.startswith("/"):
        target = rel_path_normalized
    else:
        target = _canonical_posix_path(f"{workspace}/{rel_path_normalized}")

    app_root = _canonical_posix_path(os.getenv("LANCELOT_APP_ROOT", "/home/lancelot/app"))
    shared_root = _canonical_posix_path(os.getenv("LANCELOT_WORKSPACE", "/home/lancelot/workspace"))
    if _is_under_path(target, app_root) or _is_under_path(workspace, app_root):
        write_scope = "lancelot_self_development"
    elif _is_under_path(target, shared_root) or _is_under_path(workspace, shared_root):
        write_scope = "shared_workspace_artifact"
    else:
        write_scope = "custom_workspace"

    return {
        "workspace": workspace,
        "relative_path": rel_path_normalized,
        "target_path": target,
        "write_scope": write_scope,
        "has_traversal": _path_has_traversal(rel_path),
    }


def _derive_approval_intent(tool_name: str, params: dict) -> dict:
    params = params if isinstance(params, dict) else {}
    if tool_name != "repo_writer":
        return {
            "tool": tool_name,
            "match_policy": "exact",
            "params_hash": _stable_json_hash(params),
        }

    action = str(params.get("action") or "").strip().lower()
    target = _repo_writer_target(params)
    content = str(params.get("content") or "")
    extension = os.path.splitext(target["target_path"])[1].lower()
    operation_key = (
        f"repo_writer:{action}:{target['write_scope']}:"
        f"{target['workspace']}:{target['relative_path']}"
    )
    workspace_text_artifact = (
        action in REPO_WRITER_WORKSPACE_ACTIONS
        and target["write_scope"] == "shared_workspace_artifact"
        and extension in REPO_WRITER_WORKSPACE_EXTENSIONS
        and not target["has_traversal"]
        and len(content) <= 20_000
    )

    return {
        "tool": tool_name,
        "action": action,
        "operation_key": operation_key,
        "workspace": target["workspace"],
        "relative_path": target["relative_path"],
        "target_path": target["target_path"],
        "write_scope": target["write_scope"],
        "match_policy": "workspace_text_artifact" if workspace_text_artifact else "exact",
        "content_hash": hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest(),
        "content_signature": _approval_text_signature(content),
        "params_hash": _stable_json_hash(params),
    }


def _approval_intents_match(approved: dict, current: dict) -> tuple[bool, str]:
    if approved.get("tool") != current.get("tool"):
        return False, "different tool"
    if approved.get("params_hash") == current.get("params_hash"):
        return True, "exact parameter match"
    if (
        approved.get("match_policy") == "workspace_text_artifact"
        and current.get("match_policy") == "workspace_text_artifact"
        and approved.get("operation_key") == current.get("operation_key")
        and approved.get("content_signature")
        and approved.get("content_signature") == current.get("content_signature")
    ):
        return True, "same approved workspace artifact intent"
    return False, "outside approved intent envelope"


class MCPSentry:
    def __init__(self, data_dir="/home/lancelot/data"):
        self.data_dir = data_dir
        self.configs_dir = os.path.join(data_dir, "mcp_configs")
        self.pending_requests_file = os.path.join(data_dir, "mcp_pending_requests.json")
        self.pending_requests = {}  # Map request_id -> status
        self.audit_file = os.path.join(data_dir, "MEMORY_SUMMARY.md")
        self._rate_tracker = {}  # Map tool_name -> list of timestamps

        # Ensure configs dir exists
        if not os.path.exists(self.configs_dir):
            os.makedirs(self.configs_dir)

        self.tools = self.discover_tools()
        self._load_pending_requests()

    def _load_pending_requests(self):
        """Restore pending/approved MCP requests from disk."""
        if not os.path.exists(self.pending_requests_file):
            self.pending_requests = {}
            return
        try:
            with open(self.pending_requests_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.pending_requests = data if isinstance(data, dict) else {}
        except Exception:
            self.pending_requests = {}
            logger.warning("MCP Sentry: failed to load pending requests; starting empty", exc_info=True)

    def _save_pending_requests(self):
        """Persist pending/approved MCP requests to disk."""
        try:
            with open(self.pending_requests_file, "w", encoding="utf-8") as f:
                json.dump(self.pending_requests, f)
        except Exception as exc:
            logger.warning("MCP Sentry: failed to persist pending requests: %s", exc)

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
                    logger.warning("Error loading MCP config %s: %s", filename, e)
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
        if expired:
            self._save_pending_requests()

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
        params = params if isinstance(params, dict) else {}
        # Cleanup expired approvals
        self._cleanup_expired()

        tool_config = self.tools.get(tool_name)

        # SECURITY: Unknown tools default to HIGH risk
        risk_level = "high"
        if tool_config:
            risk_level = tool_config.get("risk", "low").lower()

        if risk_level == "high":
            current_intent = _derive_approval_intent(tool_name, params)
            for req_id, req in self.pending_requests.items():
                if (
                    req.get("tool") != tool_name
                    or req.get("status") != "APPROVED"
                    or req.get("_created_at", 0) + APPROVAL_TTL <= time.time()
                ):
                    continue

                approved_intent = req.get("intent") or _derive_approval_intent(
                    tool_name,
                    req.get("params") or {},
                )
                matches, reason = _approval_intents_match(approved_intent, current_intent)
                if not matches:
                    continue
                result = "APPROVED" if reason == "exact parameter match" else "APPROVED_INTENT"
                self._log_permission_check(tool_name, params, result)
                return {
                    "status": "APPROVED",
                    "message": "Previously Approved" if result == "APPROVED" else "Approved by bounded intent",
                    "request_id": req_id,
                    "approval_match": "exact" if result == "APPROVED" else "bounded_intent",
                    "approval_match_reason": reason,
                }

            # Rate-limit only new approval requests. Approved resumes should not
            # be denied because the operator clicked Continue several times.
            if self._check_rate_limit(tool_name):
                return {
                    "status": "DENIED",
                    "message": f"Rate limit exceeded for '{tool_name}'. Try again later.",
                    "request_id": None,
                }

            request_id = str(uuid.uuid4())
            self.pending_requests[request_id] = {
                "tool": tool_name,
                "params": params,
                "intent": current_intent,
                "status": "PENDING",
                "timestamp": datetime.datetime.utcnow().isoformat(),
                "_created_at": time.time(),
            }
            self._save_pending_requests()

            # Log permission check
            self._log_permission_check(tool_name, params, "PENDING")

            return {
                "status": "PENDING",
                "message": f"High-Risk Action detected: {tool_name}. Approval Card sent to Chat.",
                "request_id": request_id,
            }

        # Rate limiting
        if self._check_rate_limit(tool_name):
            return {
                "status": "DENIED",
                "message": f"Rate limit exceeded for '{tool_name}'. Try again later.",
                "request_id": None,
            }

        # Log permission check for approved low/medium risk
        self._log_permission_check(tool_name, params, "APPROVED")

        return {"status": "APPROVED", "message": "Access Granted", "request_id": None}

    def approve_request(self, request_id: str) -> bool:
        """Callback to approve a pending request."""
        if request_id in self.pending_requests:
            self.pending_requests[request_id]["status"] = "APPROVED"
            self._save_pending_requests()
            return True
        return False

    def deny_request(self, request_id: str) -> bool:
        """Explicitly denies a pending request."""
        if request_id in self.pending_requests:
            self.pending_requests[request_id]["status"] = "DENIED"
            self._save_pending_requests()
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
            logger.warning("Error logging MCP permission check: %s", e)

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
            logger.warning("Error logging MCP execution: %s", e)
