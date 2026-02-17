"""
Tool Fabric Policies — Risk Policies, Allowlists, and Security Gates
=====================================================================

This module implements the policy engine for Tool Fabric, providing:
- Risk level evaluation for tool operations
- Command allowlist/denylist enforcement
- Path security (traversal detection, workspace boundary)
- Network policy enforcement
- Redaction rules for sensitive data

Risk Levels:
- LOW: read/list/status operations, safe scaffolding
- MEDIUM: apply patches, install deps in container, run tests
- HIGH: network enabled, deploy, delete operations, credential handling
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml

from src.tools.contracts import (
    Capability,
    RiskLevel,
    PolicySnapshot,
    ToolIntent,
)

logger = logging.getLogger(__name__)

# Path to network allowlist config (resolved at import time)
_ALLOWLIST_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "network_allowlist.yaml",
)


def _load_network_allowlist() -> List[str]:
    """Load allowed domains from config/network_allowlist.yaml."""
    try:
        with open(_ALLOWLIST_CONFIG_PATH, "r") as f:
            data = yaml.safe_load(f) or {}
        return [d.lower().strip() for d in data.get("domains", []) if d]
    except Exception:
        return []


# =============================================================================
# Policy Configuration
# =============================================================================


@dataclass
class PolicyConfig:
    """Configuration for the policy engine."""

    # Network policy
    network_allowed_default: bool = False
    network_allowed_capabilities: Set[Capability] = field(
        default_factory=lambda: {Capability.WEB_OPS}
    )

    # Command policies
    command_allowlist: List[str] = field(default_factory=lambda: [
        # Dev tools
        "git", "python", "python3", "pip", "pip3",
        "node", "npm", "npx", "pnpm", "yarn",
        "pytest", "jest", "mocha",
        # Unix/Linux
        "ls", "cat", "head", "tail", "grep", "find", "wc",
        "echo", "pwd", "whoami", "date", "uname", "hostname",
        "mkdir", "touch", "cp", "mv", "sort", "uniq",
        "df", "du", "tar", "gzip", "zip", "unzip",
        "curl", "wget", "test", "true", "false",
        "docker", "docker-compose",
        # Windows
        "dir", "ver", "systeminfo", "ipconfig", "netstat",
        "tasklist", "where", "type", "set",
        "powershell", "pwsh", "wmic",
    ])

    command_denylist: List[str] = field(default_factory=lambda: [
        # Destructive filesystem operations
        "rm -rf /",
        "rm -rf /*",
        "rm -rf .",
        "rm -rf ..",
        # Disk operations
        "mkfs",
        "fdisk",
        "dd if=/dev/zero",
        "dd if=/dev/random",
        # Fork bomb and resource exhaustion
        ":(){:|:&};:",
        ":(){ :|:& };:",
        # Permission escalation
        "chmod -R 777 /",
        "chmod 777 /",
        "chown -R",
        "sudo",
        "su -",
        # Network attacks
        "nc -l",
        "ncat -l",
        # Credential theft
        "cat /etc/passwd",
        "cat /etc/shadow",
        "cat ~/.ssh/id_rsa",
        "cat ~/.aws/credentials",
        # History/key logging
        "history",
        "cat ~/.bash_history",
        # Kernel operations
        "insmod",
        "rmmod",
        "modprobe",
    ])

    # Path policies
    allowed_path_patterns: List[str] = field(default_factory=list)
    denied_path_patterns: List[str] = field(default_factory=lambda: [
        r"\.\.\/",           # Path traversal
        r"\/etc\/",          # System config
        r"\/root\/",         # Root home
        r"\/home\/[^/]+\/\.ssh",  # SSH keys
        r"\/home\/[^/]+\/\.aws",  # AWS credentials
        r"\/home\/[^/]+\/\.gnupg",  # GPG keys
        r"\.env$",           # Environment files
        r"\.env\.",          # Environment files
        r"credentials",      # Credential files
        r"secrets?\.ya?ml",  # Secret config files
    ])

    # Sensitive data patterns (for redaction)
    sensitive_patterns: List[str] = field(default_factory=lambda: [
        r"password[\"']?\s*[:=]\s*[\"']?[^\s\"']+",
        r"api[_-]?key[\"']?\s*[:=]\s*[\"']?[^\s\"']+",
        r"secret[\"']?\s*[:=]\s*[\"']?[^\s\"']+",
        r"token[\"']?\s*[:=]\s*[\"']?[^\s\"']+",
        r"bearer\s+[a-zA-Z0-9\-_\.]+",
        r"sk-[a-zA-Z0-9]+",  # OpenAI keys
        r"ghp_[a-zA-Z0-9]+",  # GitHub tokens
        r"gho_[a-zA-Z0-9]+",  # GitHub OAuth tokens
        r"aws_access_key_id",
        r"aws_secret_access_key",
        r"AKIA[A-Z0-9]{16}",  # AWS access key ID
    ])

    # Risk thresholds
    require_verifier_for_high_risk: bool = True
    max_output_bytes: int = 100000
    max_file_size_bytes: int = 10 * 1024 * 1024  # 10 MB


# =============================================================================
# Policy Evaluation Results
# =============================================================================


@dataclass
class PolicyDecision:
    """Result of a policy evaluation."""
    allowed: bool
    risk_level: RiskLevel
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    requires_approval: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "allowed": self.allowed,
            "risk_level": self.risk_level.value,
            "reasons": self.reasons,
            "warnings": self.warnings,
            "requires_approval": self.requires_approval,
        }


# =============================================================================
# Policy Engine
# =============================================================================


class PolicyEngine:
    """
    Policy engine for evaluating tool operations.

    Provides centralized policy enforcement for:
    - Command execution
    - File access
    - Network access
    - Risk assessment
    """

    def __init__(self, config: Optional[PolicyConfig] = None):
        """
        Initialize the policy engine.

        Args:
            config: Optional PolicyConfig (uses defaults if not provided)
        """
        self.config = config or PolicyConfig()
        self._compiled_deny_patterns = [
            re.compile(p, re.IGNORECASE)
            for p in self.config.denied_path_patterns
        ]
        self._compiled_sensitive_patterns = [
            re.compile(p, re.IGNORECASE)
            for p in self.config.sensitive_patterns
        ]

    # =========================================================================
    # Command Policies
    # =========================================================================

    def evaluate_command(
        self,
        command: str,
        capability: Capability = Capability.SHELL_EXEC,
        workspace: Optional[str] = None,
    ) -> PolicyDecision:
        """
        Evaluate a command against policies.

        Args:
            command: Command string to evaluate
            capability: The capability context
            workspace: Optional workspace path for boundary checking

        Returns:
            PolicyDecision with allow/deny and risk level
        """
        reasons = []
        warnings = []
        risk_level = RiskLevel.LOW

        # Check denylist first
        if self._is_denied_command(command):
            return PolicyDecision(
                allowed=False,
                risk_level=RiskLevel.HIGH,
                reasons=["Command matches denylist pattern"],
            )

        # Check allowlist if not empty
        if self.config.command_allowlist:
            if not self._is_allowed_command(command):
                return PolicyDecision(
                    allowed=False,
                    risk_level=RiskLevel.MEDIUM,
                    reasons=["Command executable not in allowlist"],
                )

        # Assess risk level based on command content
        risk_level, risk_reasons = self._assess_command_risk(command)
        warnings.extend(risk_reasons)

        # Check for path traversal in command
        if self._contains_path_traversal(command):
            return PolicyDecision(
                allowed=False,
                risk_level=RiskLevel.HIGH,
                reasons=["Command contains path traversal attempt"],
            )

        # Check workspace boundary
        if workspace:
            paths_in_command = self._extract_paths_from_command(command)
            for path in paths_in_command:
                if not self._is_within_workspace(path, workspace):
                    warnings.append(f"Path may be outside workspace: {path}")

        return PolicyDecision(
            allowed=True,
            risk_level=risk_level,
            reasons=reasons,
            warnings=warnings,
            requires_approval=risk_level == RiskLevel.HIGH and self.config.require_verifier_for_high_risk,
        )

    def _is_denied_command(self, command: str) -> bool:
        """Check if command matches any denylist pattern."""
        import shlex
        cmd_lower = command.lower().strip()

        # Try to parse the command to check first token more precisely
        try:
            tokens = shlex.split(cmd_lower)
        except ValueError:
            tokens = None

        for denied in self.config.command_denylist:
            denied_lower = denied.lower()
            # Check if the first token matches single-word denylist entries
            if tokens and " " not in denied_lower:
                if tokens[0] == denied_lower:
                    return True
            # Fall back to substring match for multi-word patterns
            if denied_lower in cmd_lower:
                return True
        return False

    def _is_allowed_command(self, command: str) -> bool:
        """Check if command starts with an allowed executable."""
        parts = command.strip().split()
        if not parts:
            return False

        executable = parts[0]
        # Handle path prefixes
        if "/" in executable:
            executable = executable.split("/")[-1]
        if "\\" in executable:
            executable = executable.split("\\")[-1]

        return executable in self.config.command_allowlist

    def _assess_command_risk(self, command: str) -> Tuple[RiskLevel, List[str]]:
        """Assess risk level based on command content."""
        reasons = []
        risk = RiskLevel.LOW

        cmd_lower = command.lower()

        # HIGH risk indicators
        high_risk_patterns = [
            ("curl", "Network request"),
            ("wget", "Network request"),
            ("ssh", "SSH connection"),
            ("scp", "SSH file transfer"),
            ("rsync", "Remote sync"),
            ("docker push", "Container registry push"),
            ("docker login", "Container registry auth"),
            ("npm publish", "Package publish"),
            ("pip upload", "Package upload"),
            ("git push", "Remote push"),
            ("rm -r", "Recursive delete"),
            ("chmod", "Permission change"),
        ]

        for pattern, reason in high_risk_patterns:
            if pattern in cmd_lower:
                risk = RiskLevel.HIGH
                reasons.append(f"High risk: {reason}")

        # MEDIUM risk indicators
        if risk == RiskLevel.LOW:
            medium_risk_patterns = [
                ("git commit", "Repository modification"),
                ("pip install", "Dependency installation"),
                ("npm install", "Dependency installation"),
                ("apt", "Package management"),
                ("brew", "Package management"),
                ("docker run", "Container execution"),
                ("docker build", "Container build"),
            ]

            for pattern, reason in medium_risk_patterns:
                if pattern in cmd_lower:
                    risk = RiskLevel.MEDIUM
                    reasons.append(f"Medium risk: {reason}")
                    break

        return risk, reasons

    def _extract_paths_from_command(self, command: str) -> List[str]:
        """Extract potential file paths from a command."""
        # Simple extraction - look for path-like patterns
        paths = []
        parts = command.split()

        for part in parts:
            # Skip flags
            if part.startswith("-"):
                continue
            # Check for path-like strings
            if "/" in part or "\\" in part or part.startswith("."):
                paths.append(part)

        return paths

    # =========================================================================
    # Path Policies
    # =========================================================================

    def evaluate_path(
        self,
        path: str,
        workspace: Optional[str] = None,
        operation: str = "read",
    ) -> PolicyDecision:
        """
        Evaluate a file path against policies.

        Args:
            path: File path to evaluate
            workspace: Optional workspace boundary
            operation: Operation type (read, write, delete)

        Returns:
            PolicyDecision with allow/deny and risk level
        """
        reasons = []
        warnings = []
        risk_level = RiskLevel.LOW

        # Check for path traversal
        if self._contains_path_traversal(path):
            return PolicyDecision(
                allowed=False,
                risk_level=RiskLevel.HIGH,
                reasons=["Path contains traversal sequence"],
            )

        # Check against denied patterns
        for pattern in self._compiled_deny_patterns:
            if pattern.search(path):
                return PolicyDecision(
                    allowed=False,
                    risk_level=RiskLevel.HIGH,
                    reasons=[f"Path matches denied pattern: {pattern.pattern}"],
                )

        # Check workspace boundary
        if workspace:
            if not self._is_within_workspace(path, workspace):
                return PolicyDecision(
                    allowed=False,
                    risk_level=RiskLevel.HIGH,
                    reasons=["Path is outside workspace boundary"],
                )

        # Assess operation risk
        if operation == "delete":
            risk_level = RiskLevel.MEDIUM
            warnings.append("Delete operation")
        elif operation == "write":
            risk_level = RiskLevel.LOW
            # Check for sensitive file writes
            if any(p in path.lower() for p in [".env", "secret", "credential", "key"]):
                risk_level = RiskLevel.MEDIUM
                warnings.append("Writing to potentially sensitive file")

        return PolicyDecision(
            allowed=True,
            risk_level=risk_level,
            reasons=reasons,
            warnings=warnings,
        )

    def _contains_path_traversal(self, path: str) -> bool:
        """Check if path contains traversal attempts."""
        # Normalize path separators
        normalized = path.replace("\\", "/")

        # Check for obvious traversal
        if ".." in normalized:
            # Allow relative paths within workspace, but flag absolute traversal
            parts = normalized.split("/")
            depth = 0
            for part in parts:
                if part == "..":
                    depth -= 1
                elif part and part != ".":
                    depth += 1
                # If we go negative, we're escaping the root
                if depth < 0:
                    return True

        # Check for encoded traversal
        if "%2e%2e" in path.lower() or "%252e" in path.lower():
            return True

        return False

    def _is_within_workspace(self, path: str, workspace: str) -> bool:
        """Check if path is within workspace boundary (symlink-safe)."""
        try:
            # Resolve symlinks to real paths
            if os.path.isabs(path):
                abs_path = os.path.realpath(path)
            else:
                abs_path = os.path.realpath(os.path.join(workspace, path))

            abs_workspace = os.path.realpath(workspace)

            # Use os.sep to prevent prefix confusion (e.g. /workspace-evil matching /workspace)
            return abs_path == abs_workspace or abs_path.startswith(abs_workspace + os.sep)
        except Exception:
            return False

    # =========================================================================
    # Network Policies
    # =========================================================================

    def evaluate_network(
        self,
        capability: Capability,
        explicit_request: bool = False,
        target_domain: Optional[str] = None,
    ) -> PolicyDecision:
        """
        Evaluate network access request.

        Args:
            capability: The capability requesting network
            explicit_request: Whether network was explicitly requested
            target_domain: Optional target domain for allowlist checking

        Returns:
            PolicyDecision for network access
        """
        from src.core.feature_flags import FEATURE_NETWORK_ALLOWLIST

        # Check if capability is allowed network by default
        if capability in self.config.network_allowed_capabilities:
            # If allowlist is active and a domain is specified, enforce it
            if FEATURE_NETWORK_ALLOWLIST and target_domain:
                if not self._is_domain_allowed(target_domain):
                    return PolicyDecision(
                        allowed=False,
                        risk_level=RiskLevel.HIGH,
                        reasons=[
                            f"Domain '{target_domain}' not in network allowlist"
                        ],
                    )
            return PolicyDecision(
                allowed=True,
                risk_level=RiskLevel.MEDIUM,
                warnings=["Network access granted for capability"],
            )

        # Check global default
        if self.config.network_allowed_default:
            # Enforce allowlist even for globally allowed network
            if FEATURE_NETWORK_ALLOWLIST and target_domain:
                if not self._is_domain_allowed(target_domain):
                    return PolicyDecision(
                        allowed=False,
                        risk_level=RiskLevel.HIGH,
                        reasons=[
                            f"Domain '{target_domain}' not in network allowlist"
                        ],
                    )
            return PolicyDecision(
                allowed=True,
                risk_level=RiskLevel.HIGH,
                warnings=["Network access granted by default policy"],
                requires_approval=True,
            )

        # Network denied
        if explicit_request:
            return PolicyDecision(
                allowed=False,
                risk_level=RiskLevel.HIGH,
                reasons=["Network access denied by policy"],
            )

        return PolicyDecision(
            allowed=False,
            risk_level=RiskLevel.LOW,
            reasons=["Network disabled by default"],
        )

    def _is_domain_allowed(self, domain: str) -> bool:
        """Check if a domain is in the network allowlist.

        Supports suffix matching: 'api.github.com' matches allowlisted 'github.com'.
        """
        allowed = _load_network_allowlist()
        if not allowed:
            # Empty allowlist = allow all (no restrictions configured)
            return True
        domain_lower = domain.lower().strip()
        for entry in allowed:
            if domain_lower == entry or domain_lower.endswith("." + entry):
                return True
        return False

    # =========================================================================
    # Tool Intent Evaluation
    # =========================================================================

    def evaluate_intent(
        self,
        intent: ToolIntent,
        workspace: Optional[str] = None,
    ) -> PolicyDecision:
        """
        Evaluate a complete tool intent.

        Args:
            intent: ToolIntent to evaluate
            workspace: Optional workspace boundary

        Returns:
            PolicyDecision for the entire intent
        """
        reasons = []
        warnings = []
        risk_level = intent.risk

        # Check capability-specific policies
        if intent.capability == Capability.VISION_CONTROL:
            # VisionControl always requires explicit approval
            return PolicyDecision(
                allowed=True,
                risk_level=RiskLevel.HIGH,
                warnings=["VisionControl requires explicit approval"],
                requires_approval=True,
            )

        # Evaluate based on inputs
        command = intent.inputs.get("command")
        if command:
            cmd_decision = self.evaluate_command(
                command,
                intent.capability,
                workspace or intent.workspace,
            )
            if not cmd_decision.allowed:
                return cmd_decision
            warnings.extend(cmd_decision.warnings)
            if cmd_decision.risk_level > risk_level:
                risk_level = cmd_decision.risk_level

        # Check paths in inputs
        for key in ["path", "file", "workspace", "dest_path", "src_path"]:
            path = intent.inputs.get(key)
            if path:
                path_decision = self.evaluate_path(
                    path,
                    workspace or intent.workspace,
                    intent.action,
                )
                if not path_decision.allowed:
                    return path_decision
                warnings.extend(path_decision.warnings)

        return PolicyDecision(
            allowed=True,
            risk_level=risk_level,
            reasons=reasons,
            warnings=warnings,
            requires_approval=risk_level == RiskLevel.HIGH and self.config.require_verifier_for_high_risk,
        )

    # =========================================================================
    # Redaction
    # =========================================================================

    def redact_sensitive(self, text: str) -> str:
        """
        Redact sensitive information from text.

        Args:
            text: Text to redact

        Returns:
            Text with sensitive information replaced
        """
        redacted = text
        for pattern in self._compiled_sensitive_patterns:
            redacted = pattern.sub("[REDACTED]", redacted)
        return redacted

    def redact_paths(self, text: str, workspace: Optional[str] = None) -> str:
        """
        Redact absolute paths from text.

        Args:
            text: Text to redact
            workspace: Optional workspace to preserve relative paths

        Returns:
            Text with absolute paths redacted
        """
        # Replace absolute paths
        if workspace:
            workspace_hash = hashlib.sha256(workspace.encode()).hexdigest()[:8]
            text = text.replace(workspace, f"[workspace:{workspace_hash}]")

        # Replace common path patterns
        text = re.sub(r"/home/[^/\s]+", "[HOME]", text)
        text = re.sub(r"/Users/[^/\s]+", "[HOME]", text)
        text = re.sub(r"C:\\Users\\[^\\]+", "[HOME]", text)

        return text

    # =========================================================================
    # Policy Snapshot
    # =========================================================================

    def create_snapshot(
        self,
        intent: ToolIntent,
        decision: PolicyDecision,
    ) -> PolicySnapshot:
        """
        Create a policy snapshot for a tool invocation.

        Args:
            intent: The tool intent
            decision: The policy decision

        Returns:
            PolicySnapshot capturing policy state
        """
        return PolicySnapshot(
            network_allowed=intent.capability in self.config.network_allowed_capabilities,
            allowlist_applied=self.config.command_allowlist[:10],  # First 10 for brevity
            denylist_applied=self.config.command_denylist[:5],     # First 5 for brevity
            risk_level=decision.risk_level,
            workspace_restricted=intent.workspace != "",
            verifier_approved=not decision.requires_approval,
        )


# =============================================================================
# Convenience Functions
# =============================================================================


def create_policy_engine(
    network_allowed: bool = False,
    command_allowlist: Optional[List[str]] = None,
    require_approval_for_high_risk: bool = True,
) -> PolicyEngine:
    """
    Factory function for creating PolicyEngine.

    Args:
        network_allowed: Whether network is allowed by default
        command_allowlist: Optional command allowlist (uses defaults if None)
        require_approval_for_high_risk: Whether to require approval for high-risk ops

    Returns:
        Configured PolicyEngine
    """
    config = PolicyConfig(
        network_allowed_default=network_allowed,
        require_verifier_for_high_risk=require_approval_for_high_risk,
    )
    if command_allowlist is not None:
        config.command_allowlist = command_allowlist
    return PolicyEngine(config)


def is_safe_path(path: str, workspace: str) -> bool:
    """
    Quick check if a path is safe within workspace.

    Args:
        path: Path to check
        workspace: Workspace boundary

    Returns:
        True if path is safe
    """
    engine = PolicyEngine()
    decision = engine.evaluate_path(path, workspace)
    return decision.allowed


def is_safe_command(command: str) -> bool:
    """
    Quick check if a command is safe to execute.

    Args:
        command: Command to check

    Returns:
        True if command is safe
    """
    engine = PolicyEngine()
    decision = engine.evaluate_command(command)
    return decision.allowed
