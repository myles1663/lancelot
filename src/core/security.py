import hashlib
import ipaddress
import logging
import os
import datetime
import re
import socket
import threading
from urllib.parse import urlparse, unquote

_security_logger = logging.getLogger("lancelot.security")

class InputSanitizer:
    BANNED_PHRASES = [
        "ignore previous rules",
        "system prompt",
        "bypass security",
        "reveal hidden instructions",
        "ignore all instructions",
        "disregard above",
        "you are now",
        "act as",
        "pretend you",
        "override",
        "forget everything",
        "new instructions",
        "admin mode",
        "developer mode",
        "jailbreak",
        "DAN",
    ]

    # Cyrillic homoglyphs that visually resemble Latin characters
    _CYRILLIC_HOMOGLYPHS = {
        "\u0430": "a",  # Cyrillic а -> Latin a
        "\u0435": "e",  # Cyrillic е -> Latin e
        "\u043e": "o",  # Cyrillic о -> Latin o
        "\u0441": "c",  # Cyrillic с -> Latin c
        "\u0440": "p",  # Cyrillic р -> Latin p
    }

    # Suspicious instruction-override / role-injection patterns
    _SUSPICIOUS_PATTERNS = [
        re.compile(r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions|rules|prompts)", re.IGNORECASE),
        re.compile(r"disregard\s+(all\s+)?(previous|prior|above|earlier)", re.IGNORECASE),
        re.compile(r"you\s+are\s+now\s+", re.IGNORECASE),
        re.compile(r"from\s+now\s+on\s+you\s+are", re.IGNORECASE),
        re.compile(r"new\s+(system\s+)?instructions?\s*:", re.IGNORECASE),
        re.compile(r"enter\s+(admin|developer|debug|god)\s+mode", re.IGNORECASE),
        re.compile(r"\bsystem\s*:\s*", re.IGNORECASE),
        re.compile(r"\bassistant\s*:\s*", re.IGNORECASE),
        re.compile(r"\[INST\]", re.IGNORECASE),
        re.compile(r"<\|im_start\|>", re.IGNORECASE),
    ]

    def _normalize(self, text: str) -> str:
        """Normalizes text to defeat obfuscation attempts.

        - Strips zero-width characters
        - Collapses multiple spaces
        - Replaces Cyrillic homoglyphs with Latin equivalents
        - Decodes URL-encoded sequences
        """
        # Strip zero-width characters
        for zw in ("\u200b", "\u200c", "\u200d", "\ufeff"):
            text = text.replace(zw, "")

        # Replace Cyrillic homoglyphs
        for cyrillic, latin in self._CYRILLIC_HOMOGLYPHS.items():
            text = text.replace(cyrillic, latin)

        # Decode URL-encoded sequences (e.g. %20 -> space)
        text = unquote(text)

        # Collapse multiple spaces into one
        text = re.sub(r" {2,}", " ", text)

        return text

    def _check_suspicious_patterns(self, text: str) -> bool:
        """Returns True if text matches instruction-override or role-injection patterns."""
        for pattern in self._SUSPICIOUS_PATTERNS:
            if pattern.search(text):
                return True
        return False

    def sanitize(self, text: str) -> str:
        """Normalizes and removes banned phrases from input text."""
        # Normalize first to defeat obfuscation
        sanitized_text = self._normalize(text)

        for phrase in self.BANNED_PHRASES:
            # Case insensitive replacement
            pattern = re.compile(re.escape(phrase), re.IGNORECASE)
            sanitized_text = pattern.sub("[REDACTED]", sanitized_text)

        # Flag suspicious patterns
        if self._check_suspicious_patterns(sanitized_text):
            sanitized_text = "[SUSPICIOUS INPUT DETECTED] " + sanitized_text

        return sanitized_text

class AuditLogger:
    """F-011: Tamper-evident audit logger with hash chaining.

    Each log entry includes the SHA-256 hash of the previous entry,
    creating a chain where any modification invalidates all subsequent
    entries. The chain starts with a zero hash on initialization.
    """

    def __init__(self, log_path="/home/lancelot/data/audit.log"):
        self.log_path = log_path
        self._lock = threading.Lock()
        self._prev_hash = self._recover_last_hash()

    def _recover_last_hash(self) -> str:
        """Recover the last entry's hash from the log file for chain continuity."""
        try:
            with open(self.log_path, "rb") as f:
                # Read last non-empty line
                f.seek(0, 2)
                pos = f.tell()
                if pos == 0:
                    return "0" * 64
                # Scan backward for the last complete line
                lines = []
                while pos > 0:
                    pos = max(pos - 4096, 0)
                    f.seek(pos)
                    lines = f.read().decode("utf-8", errors="replace").strip().split("\n")
                    if len(lines) >= 1:
                        break
                last_line = lines[-1].strip() if lines else ""
                if last_line:
                    return hashlib.sha256(last_line.encode()).hexdigest()
        except FileNotFoundError:
            pass
        except Exception:
            pass
        return "0" * 64

    def _write_entry(self, entry_content: str) -> None:
        """Write an entry with hash chaining."""
        with self._lock:
            # Build chained entry: includes previous hash
            entry = f"{entry_content} | PrevHash: {self._prev_hash}\n"
            try:
                with open(self.log_path, "a") as f:
                    f.write(entry)
                self._prev_hash = hashlib.sha256(entry.strip().encode()).hexdigest()
            except Exception as e:
                _security_logger.critical("Failed to write to audit log: %s", e)

    def log_command(self, command: str, user: str = "System"):
        """Hashes and logs execution commands."""
        timestamp = datetime.datetime.utcnow().isoformat()
        cmd_hash = hashlib.sha256(command.encode()).hexdigest()
        self._write_entry(
            f"[{timestamp}] User: {user} | Hash: {cmd_hash} | Command: {command}"
        )

    def log_event(self, event_type: str, details: str, user: str = "System"):
        """Logs a structured event (mode changes, auto-pause triggers, etc.)."""
        timestamp = datetime.datetime.utcnow().isoformat()
        detail_hash = hashlib.sha256(details.encode()).hexdigest()
        self._write_entry(
            f"[{timestamp}] Event: {event_type} | User: {user} "
            f"| Hash: {detail_hash} | Details: {details}"
        )

class NetworkInterceptor:
    # Core domains that are always allowed (infrastructure, not user-configurable)
    _CORE_DOMAINS = [
        "localhost",
        "127.0.0.1",
        "api.projectlancelot.dev",
        "ghcr.io",
    ]

    # Config file path — editable via Kill Switches UI
    _ALLOWLIST_CONFIG = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "config", "network_allowlist.yaml",
    )

    BLOCKED_IP_RANGES = [
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("169.254.0.0/16"),
        ipaddress.ip_network("0.0.0.0/8"),
    ]

    # F-010: Auto-reload interval in seconds (0 = disabled)
    _RELOAD_INTERVAL_S = 300

    def __init__(self):
        self.ALLOW_LIST = list(self._CORE_DOMAINS)
        self._reload_timer: threading.Timer | None = None
        self._load_config_domains()
        self._schedule_reload()

    def _load_config_domains(self):
        """Load domains from config/network_allowlist.yaml and merge with core domains."""
        try:
            import yaml
            with open(self._ALLOWLIST_CONFIG, "r") as f:
                data = yaml.safe_load(f) or {}
            config_domains = data.get("domains", [])
            # Merge: core + config, deduplicated
            merged = set(self._CORE_DOMAINS)
            merged.update(d.strip().lower() for d in config_domains if d and d.strip())
            self.ALLOW_LIST = list(merged)
            _security_logger.debug(
                "Network allowlist loaded: %d domains (%d from config)",
                len(self.ALLOW_LIST), len(config_domains),
            )
        except FileNotFoundError:
            _security_logger.warning(
                "Network allowlist config not found at %s — using %d core domains only. "
                "Create this file with a 'domains:' YAML list to add allowed domains.",
                self._ALLOWLIST_CONFIG, len(self._CORE_DOMAINS),
            )
        except Exception as e:
            _security_logger.error("Failed to load network allowlist config: %s", e)

    def _schedule_reload(self):
        """F-010: Schedule periodic auto-reload of the allowlist."""
        if self._RELOAD_INTERVAL_S <= 0:
            return
        self._reload_timer = threading.Timer(
            self._RELOAD_INTERVAL_S, self._auto_reload,
        )
        self._reload_timer.daemon = True
        self._reload_timer.start()

    def _auto_reload(self):
        """Periodic reload callback."""
        try:
            self._load_config_domains()
        except Exception as e:
            _security_logger.error("Auto-reload of network allowlist failed: %s", e)
        self._schedule_reload()

    def reload_allowlist(self):
        """Reload the allowlist from config (called after Kill Switches UI updates)."""
        self._load_config_domains()

    def _is_private_ip(self, hostname: str) -> bool:
        """Resolves hostname and returns True if it points to a private/blocked IP range.

        Returns True on resolution failure for safety (fail-closed).
        """
        try:
            addr = socket.gethostbyname(hostname)
            ip = ipaddress.ip_address(addr)
            for network in self.BLOCKED_IP_RANGES:
                if ip in network:
                    return True
            return False
        except Exception:
            # Fail closed: if we cannot resolve, treat as private/blocked
            return True

    @staticmethod
    def _strip_credentials(url: str) -> str:
        """Strips user:pass@ credentials from a URL."""
        parsed = urlparse(url)
        if parsed.username or parsed.password:
            # Rebuild netloc without credentials
            host_part = parsed.hostname or ""
            if parsed.port:
                host_part = f"{host_part}:{parsed.port}"
            # Replace full netloc with credential-free version
            return parsed._replace(netloc=host_part).geturl()
        return url

    def check_url(self, url: str) -> bool:
        """Checks if a URL domain is in the allow-list and not targeting private IPs."""
        try:
            # Strip credentials first
            url = self._strip_credentials(url)

            parsed = urlparse(url)
            domain = parsed.netloc
            if not domain:
                return False  # Block if no domain parsed

            # Strip port from domain for matching
            hostname = parsed.hostname or domain
            if not hostname:
                return False

            # Check for private IP / SSRF
            if self._is_private_ip(hostname):
                print(f"SECURITY ALERT: Blocked connection to private/internal address {hostname}")
                return False

            # Check if domain ends with allowed domain (to allow subdomains)
            for allowed in self.ALLOW_LIST:
                if hostname == allowed or hostname.endswith("." + allowed):
                    return True

            print(f"SECURITY ALERT: Blocked outbound connection to {hostname}")
            return False
        except Exception:
            # Fail closed: if we cannot resolve, treat as blocked
            return False

class CognitionGovernor:
    """Limits the agent's improved cognition to prevent runaway costs or infinite loops.

    F-015: All file I/O is guarded by a threading.Lock to prevent
    concurrent read/write corruption of usage_stats.json.
    """
    LIMITS = {
        "tokens_daily": 2_000_000,
        "tool_calls_daily": 1000,
        "actions_per_minute": 60
    }

    def __init__(self, data_dir="/home/lancelot/data"):
        self.data_dir = data_dir
        self.usage_file = os.path.join(data_dir, "usage_stats.json")
        self._file_lock = threading.Lock()
        self._load_usage()

    def _load_usage(self):
        import json
        with self._file_lock:
            if os.path.exists(self.usage_file):
                try:
                    with open(self.usage_file, "r") as f:
                        self.usage = json.load(f)
                except Exception:
                    self.usage = {}
            else:
                self.usage = {}

            # Reset if new day (simple logic)
            today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
            if self.usage.get("date") != today:
                self.usage = {"date": today, "tokens": 0, "tool_calls": 0, "actions": 0}
                self._save_usage_locked()

    def _save_usage_locked(self):
        """Write usage file (caller must hold _file_lock)."""
        import json
        try:
            tmp_path = self.usage_file + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(self.usage, f)
            os.replace(tmp_path, self.usage_file)
        except Exception as e:
            _security_logger.warning("Failed to save usage stats: %s", e)

    def _save_usage(self):
        import json
        with self._file_lock:
            self._save_usage_locked()

    def check_limit(self, metric: str, cost: int = 1) -> bool:
        """Returns True if the action is allowed, False if blocked."""
        self._load_usage()  # Sync

        if metric == "tokens":
            if self.usage.get("tokens", 0) + cost > self.LIMITS["tokens_daily"]:
                _security_logger.warning("GOVERNANCE BLOCK: Daily token limit exceeded.")
                return False
        elif metric == "tool_calls":
            if self.usage.get("tool_calls", 0) + cost > self.LIMITS["tool_calls_daily"]:
                _security_logger.warning("GOVERNANCE BLOCK: Daily tool call limit exceeded.")
                return False

        return True

    def log_usage(self, metric: str, cost: int = 1):
        """Updates internal counters."""
        with self._file_lock:
            self.usage[metric] = self.usage.get(metric, 0) + cost
            self._save_usage_locked()


class Sentry:
    """Human-in-the-Loop Permission System with Persistence."""
    
    def __init__(self, data_dir="/home/lancelot/data"):
        self.data_dir = data_dir
        self.approvals_file = os.path.join(data_dir, "sentry_whitelist.json")
        self.approvals = {}
        self._load_approvals()

    def _load_approvals(self):
        import json
        if os.path.exists(self.approvals_file):
            try:
                with open(self.approvals_file, "r") as f:
                    self.approvals = json.load(f)
            except Exception:
                self.approvals = {}

    def _save_approvals(self):
        import json
        try:
            with open(self.approvals_file, "w") as f:
                json.dump(self.approvals, f)
        except Exception:
            pass

    def add_approval(self, action_type: str, metadata: dict):
        """Whitelists an action signature."""
        sig = self._generate_signature(action_type, metadata)
        self.approvals[sig] = {
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "metadata": metadata
        }
        self._save_approvals()

    def _generate_signature(self, action_type: str, metadata: dict) -> str:
        """Creates a deterministic signature for the action."""
        s = f"{action_type}:{sorted(metadata.items())}"
        return hashlib.sha256(s.encode()).hexdigest()

    def check_permission(self, action_type: str, metadata: dict) -> dict:
        """Determines if an action requires human approval."""
        # 1. Check Whitelist
        sig = self._generate_signature(action_type, metadata)
        if sig in self.approvals:
            return {"status": "ALLOWED", "message": "Pre-approved action"}

        # SafeREPL actions are generally ALLOWED
        if action_type == "cli_shell":
            return {"status": "PENDING", "message": "Unsafe Command Execution", "request_id": sig[:8]}
            
        return {"status": "ALLOWED", "message": "Low risk action"}
