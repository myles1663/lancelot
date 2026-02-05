"""
Tool Fabric Receipts — Tool-Specific Receipt Extensions
========================================================

This module extends the base receipt system with tool-specific fields
for comprehensive auditing of Tool Fabric operations.

Receipt Types:
- ToolReceipt: General tool invocation receipt
- VisionReceipt: Vision control operation receipt

All tool invocations must generate a receipt for:
- Audit trail and compliance
- Replay capability
- Diff-based parity testing
- Future provider comparison
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.tools.contracts import (
    Capability,
    RiskLevel,
    PolicySnapshot,
    FileChange,
    ExecResult,
    VisionResult,
)


# =============================================================================
# Tool Receipt
# =============================================================================


@dataclass
class ToolReceipt:
    """
    Receipt for a tool invocation through Tool Fabric.

    Captures complete audit trail including:
    - What was invoked (capability, action)
    - Who executed it (provider)
    - What inputs were provided (redacted)
    - What outputs were produced (bounded)
    - Security context (policy snapshot)
    - File changes with hashes
    - Verification results
    """

    # Identity
    receipt_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    session_id: Optional[str] = None
    parent_receipt_id: Optional[str] = None

    # Capability and action
    capability: str = Capability.SHELL_EXEC.value
    action: str = ""

    # Provider
    provider_id: str = ""
    provider_version: Optional[str] = None

    # Workspace (redacted path)
    workspace_id: Optional[str] = None
    workspace_path_redacted: Optional[str] = None

    # Policy context
    policy_snapshot: Dict[str, Any] = field(default_factory=dict)

    # Inputs (redacted sensitive values)
    inputs_summary: Dict[str, Any] = field(default_factory=dict)

    # Outputs (bounded)
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    exit_code: Optional[int] = None

    # File changes
    changed_files: List[Dict[str, Any]] = field(default_factory=list)

    # Verification
    verification_steps: List[str] = field(default_factory=list)
    verification_results: Dict[str, bool] = field(default_factory=dict)

    # Risk assessment
    risk_level: str = RiskLevel.LOW.value
    decision_trace: List[str] = field(default_factory=list)

    # Timing
    duration_ms: Optional[int] = None

    # Status
    success: bool = False
    error_message: Optional[str] = None

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolReceipt":
        """Create ToolReceipt from dictionary."""
        return cls(**data)

    def with_exec_result(self, result: ExecResult, max_output: int = 10000) -> "ToolReceipt":
        """
        Update receipt with execution result.

        Args:
            result: ExecResult from command execution
            max_output: Maximum characters for stdout/stderr

        Returns:
            Updated ToolReceipt
        """
        stdout = result.stdout
        stderr = result.stderr
        stdout_truncated = len(stdout) > max_output
        stderr_truncated = len(stderr) > max_output

        if stdout_truncated:
            stdout = stdout[:max_output] + "\n... (truncated)"
        if stderr_truncated:
            stderr = stderr[:max_output] + "\n... (truncated)"

        self.stdout = stdout
        self.stderr = stderr
        self.stdout_truncated = stdout_truncated
        self.stderr_truncated = stderr_truncated
        self.exit_code = result.exit_code
        self.duration_ms = result.duration_ms
        self.success = result.success
        return self

    def with_file_changes(self, changes: List[FileChange]) -> "ToolReceipt":
        """
        Update receipt with file changes.

        Args:
            changes: List of FileChange records

        Returns:
            Updated ToolReceipt
        """
        self.changed_files = [c.to_dict() for c in changes]
        return self

    def with_policy(self, snapshot: PolicySnapshot) -> "ToolReceipt":
        """
        Update receipt with policy snapshot.

        Args:
            snapshot: PolicySnapshot at invocation time

        Returns:
            Updated ToolReceipt
        """
        self.policy_snapshot = snapshot.to_dict()
        self.risk_level = snapshot.risk_level.value
        return self

    def with_verification(
        self,
        steps: List[str],
        results: Dict[str, bool],
    ) -> "ToolReceipt":
        """
        Update receipt with verification results.

        Args:
            steps: List of verification step names
            results: Dict mapping step name to pass/fail

        Returns:
            Updated ToolReceipt
        """
        self.verification_steps = steps
        self.verification_results = results
        return self

    def fail(self, error_message: str) -> "ToolReceipt":
        """
        Mark receipt as failed.

        Args:
            error_message: Error description

        Returns:
            Updated ToolReceipt
        """
        self.success = False
        self.error_message = error_message
        return self


# =============================================================================
# Vision Receipt
# =============================================================================


@dataclass
class VisionReceipt:
    """
    Receipt for vision control operations via Antigravity.

    Extends ToolReceipt with vision-specific fields:
    - Screenshots (hashed, not stored)
    - Detected UI elements
    - Actions performed
    - Timing and confidence scores
    """

    # Identity
    receipt_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    session_id: Optional[str] = None
    parent_receipt_id: Optional[str] = None

    # Provider (always Antigravity for VisionControl)
    provider_id: str = "antigravity_vision"
    provider_version: Optional[str] = None

    # Action
    action: str = ""  # "capture_screen", "locate_element", "perform_action", "verify_state"

    # Screenshots
    screenshot_before_hash: Optional[str] = None
    screenshot_after_hash: Optional[str] = None

    # Detected elements
    elements_detected: List[Dict[str, Any]] = field(default_factory=list)

    # Action details
    target_element: Optional[Dict[str, Any]] = None
    action_performed: Optional[str] = None
    action_value: Optional[str] = None

    # Verification
    expected_state: Optional[Dict[str, Any]] = None
    actual_state: Optional[Dict[str, Any]] = None
    state_matched: bool = False

    # Confidence and timing
    confidence_score: float = 0.0
    duration_ms: Optional[int] = None

    # Status
    success: bool = False
    error_message: Optional[str] = None

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VisionReceipt":
        """Create VisionReceipt from dictionary."""
        return cls(**data)

    def with_vision_result(self, result: VisionResult) -> "VisionReceipt":
        """
        Update receipt with vision result.

        Args:
            result: VisionResult from vision operation

        Returns:
            Updated VisionReceipt
        """
        self.success = result.success
        if result.screenshot_hash:
            self.screenshot_after_hash = result.screenshot_hash
        self.elements_detected = result.elements_detected
        self.action_performed = result.action_performed
        self.confidence_score = result.confidence
        if result.error_message:
            self.error_message = result.error_message
        return self

    def with_screenshots(
        self,
        before: Optional[bytes],
        after: Optional[bytes],
    ) -> "VisionReceipt":
        """
        Update receipt with screenshot hashes.

        Screenshots are not stored, only hashed for verification.

        Args:
            before: Screenshot bytes before action
            after: Screenshot bytes after action

        Returns:
            Updated VisionReceipt
        """
        if before:
            self.screenshot_before_hash = hashlib.sha256(before).hexdigest()
        if after:
            self.screenshot_after_hash = hashlib.sha256(after).hexdigest()
        return self

    def fail(self, error_message: str) -> "VisionReceipt":
        """
        Mark receipt as failed.

        Args:
            error_message: Error description

        Returns:
            Updated VisionReceipt
        """
        self.success = False
        self.error_message = error_message
        return self


# =============================================================================
# Receipt Factory Functions
# =============================================================================


def create_tool_receipt(
    capability: Capability,
    action: str,
    provider_id: str,
    workspace: Optional[str] = None,
    inputs: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    parent_receipt_id: Optional[str] = None,
    provider_version: Optional[str] = None,
) -> ToolReceipt:
    """
    Factory function for creating tool receipts.

    Args:
        capability: The capability being invoked
        action: Specific action name
        provider_id: Provider handling the invocation
        workspace: Workspace path (will be redacted)
        inputs: Input parameters (will be summarized/redacted)
        session_id: Optional session ID for grouping
        parent_receipt_id: Optional parent receipt for hierarchy
        provider_version: Optional provider version

    Returns:
        New ToolReceipt in pending state
    """
    # Redact workspace path
    workspace_redacted = None
    workspace_id = None
    if workspace:
        workspace_id = hashlib.sha256(workspace.encode()).hexdigest()[:12]
        # Keep only the last component
        parts = workspace.replace("\\", "/").split("/")
        workspace_redacted = f".../{parts[-1]}" if parts else "..."

    # Summarize inputs (redact sensitive values)
    inputs_summary = _summarize_inputs(inputs) if inputs else {}

    return ToolReceipt(
        capability=capability.value,
        action=action,
        provider_id=provider_id,
        provider_version=provider_version,
        workspace_id=workspace_id,
        workspace_path_redacted=workspace_redacted,
        inputs_summary=inputs_summary,
        session_id=session_id,
        parent_receipt_id=parent_receipt_id,
    )


def create_vision_receipt(
    action: str,
    session_id: Optional[str] = None,
    parent_receipt_id: Optional[str] = None,
    provider_version: Optional[str] = None,
) -> VisionReceipt:
    """
    Factory function for creating vision receipts.

    Args:
        action: Vision action name
        session_id: Optional session ID for grouping
        parent_receipt_id: Optional parent receipt for hierarchy
        provider_version: Optional Antigravity version

    Returns:
        New VisionReceipt in pending state
    """
    return VisionReceipt(
        action=action,
        provider_version=provider_version,
        session_id=session_id,
        parent_receipt_id=parent_receipt_id,
    )


def _summarize_inputs(inputs: Dict[str, Any], max_length: int = 100) -> Dict[str, Any]:
    """
    Summarize and redact sensitive input values.

    Redacts:
    - Keys containing "password", "secret", "token", "key", "credential"
    - Long string values (truncated)
    - Binary data (replaced with hash)
    """
    SENSITIVE_PATTERNS = ["password", "secret", "token", "key", "credential", "auth"]
    summary: Dict[str, Any] = {}

    for key, value in inputs.items():
        key_lower = key.lower()

        # Check for sensitive keys
        if any(pattern in key_lower for pattern in SENSITIVE_PATTERNS):
            summary[key] = "[REDACTED]"
            continue

        # Handle different types
        if isinstance(value, str):
            if len(value) > max_length:
                summary[key] = value[:max_length] + f"... ({len(value)} chars)"
            else:
                summary[key] = value
        elif isinstance(value, bytes):
            summary[key] = f"[binary, {len(value)} bytes, hash={hashlib.sha256(value).hexdigest()[:12]}]"
        elif isinstance(value, (list, dict)):
            # Recurse for nested structures, but limit depth
            if isinstance(value, dict):
                summary[key] = _summarize_inputs(value, max_length)
            else:
                summary[key] = f"[list, {len(value)} items]"
        else:
            summary[key] = value

    return summary
