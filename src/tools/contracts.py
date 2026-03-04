"""
Tool Fabric Contracts — Capability Interfaces and Type Definitions
===================================================================

This module defines the stable interfaces for Tool Fabric capabilities.
Capabilities describe "what needs to be done" independent of vendor tooling.
Providers implement these capabilities with "how it gets done."

Capabilities:
- ShellExec: Run commands, stream logs
- RepoOps: Git operations (clone, status, branch, commit, diff, apply patch)
- FileOps: File operations (read, write, list, apply diff)
- WebOps: Web operations (fetch, screenshot, download)
- UIBuilder: UI scaffolding from templates or generative
- DeployOps: Build, test, package, deploy
- VisionControl: Screen perception and action (requires Antigravity)
- AppControl: Framework-level desktop app control (requires UAB)
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Protocol,
    Tuple,
    Union,
    runtime_checkable,
)


# =============================================================================
# Enums
# =============================================================================


class RiskLevel(str, Enum):
    """Risk classification for tool operations."""
    LOW = "low"          # read/list/status, safe scaffolding
    MEDIUM = "medium"    # apply patches, install deps in container, run tests
    HIGH = "high"        # network enabled, deploy, delete operations, creds

    def __lt__(self, other):
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return _RISK_ORDER[self] < _RISK_ORDER[other]

    def __le__(self, other):
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return _RISK_ORDER[self] <= _RISK_ORDER[other]

    def __gt__(self, other):
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return _RISK_ORDER[self] > _RISK_ORDER[other]

    def __ge__(self, other):
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return _RISK_ORDER[self] >= _RISK_ORDER[other]


_RISK_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
}


class ProviderState(str, Enum):
    """Health state of a provider."""
    HEALTHY = "healthy"      # Fully operational
    DEGRADED = "degraded"    # Partially working, some features unavailable
    OFFLINE = "offline"      # Not available


class Capability(str, Enum):
    """Capability identifiers for routing."""
    SHELL_EXEC = "shell_exec"
    REPO_OPS = "repo_ops"
    FILE_OPS = "file_ops"
    WEB_OPS = "web_ops"
    UI_BUILDER = "ui_builder"
    DEPLOY_OPS = "deploy_ops"
    VISION_CONTROL = "vision_control"
    APP_CONTROL = "app_control"
    # Connector capabilities (Capability Upgrade)
    CONNECTOR_READ = "connector.read"
    CONNECTOR_WRITE = "connector.write"
    CONNECTOR_DELETE = "connector.delete"
    CREDENTIAL_READ = "credential.read"
    CREDENTIAL_WRITE = "credential.write"


class UIBuilderMode(str, Enum):
    """Mode for UIBuilder operations."""
    DETERMINISTIC = "deterministic"  # Template-based scaffolding
    GENERATIVE = "generative"        # AI-generated (Antigravity)


# =============================================================================
# Result Types
# =============================================================================


@dataclass
class ExecResult:
    """
    Result of a command execution.

    Captures stdout, stderr, exit code, and timing information.
    Output is bounded to prevent memory issues with large outputs.
    """
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool = False  # True if output was truncated

    # Optional metadata
    command: Optional[str] = None
    working_dir: Optional[str] = None
    timed_out: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExecResult":
        """Create ExecResult from dictionary."""
        return cls(**data)

    @property
    def success(self) -> bool:
        """True if command succeeded (exit code 0)."""
        return self.exit_code == 0


@dataclass
class FileChange:
    """Record of a file change with before/after hashes."""
    path: str
    action: str  # "created", "modified", "deleted"
    hash_before: Optional[str] = None
    hash_after: Optional[str] = None
    size_before: Optional[int] = None
    size_after: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FileChange":
        """Create FileChange from dictionary."""
        return cls(**data)


@dataclass
class PatchResult:
    """Result of applying a patch."""
    success: bool
    files_changed: List[FileChange]
    rejected_hunks: List[str] = field(default_factory=list)
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        d = asdict(self)
        d["files_changed"] = [fc.to_dict() for fc in self.files_changed]
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PatchResult":
        """Create PatchResult from dictionary."""
        data["files_changed"] = [FileChange.from_dict(fc) for fc in data.get("files_changed", [])]
        return cls(**data)


@dataclass
class ScaffoldResult:
    """Result of a UI scaffolding operation."""
    success: bool
    output_path: str
    template_id: Optional[str] = None
    files_created: List[str] = field(default_factory=list)
    build_verified: bool = False
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScaffoldResult":
        """Create ScaffoldResult from dictionary."""
        return cls(**data)


@dataclass
class VisionResult:
    """Result of a vision control operation."""
    success: bool
    screenshot_hash: Optional[str] = None
    elements_detected: List[Dict[str, Any]] = field(default_factory=list)
    action_performed: Optional[str] = None
    confidence: float = 0.0
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VisionResult":
        """Create VisionResult from dictionary."""
        return cls(**data)


@dataclass
class DetectedApp:
    """A desktop application detected by UAB framework detection."""
    pid: int
    name: str
    path: Optional[str] = None
    framework: Optional[str] = None
    confidence: float = 0.0
    window_title: Optional[str] = None
    connection_info: Optional[Dict[str, Any]] = None  # v0.5.0: framework-specific

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DetectedApp":
        return cls(**data)


@dataclass
class UIElement:
    """A UI element in the unified UAB element tree."""
    id: str
    type: str  # button, textfield, menu, list, etc.
    label: Optional[str] = None
    properties: Dict[str, Any] = field(default_factory=dict)
    bounds: Optional[Dict[str, int]] = None  # {x, y, width, height}
    children: List["UIElement"] = field(default_factory=list)
    actions: List[str] = field(default_factory=list)
    visible: bool = True
    enabled: bool = True
    meta: Optional[Dict[str, Any]] = None  # v0.5.0: framework-specific metadata

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["children"] = [c.to_dict() for c in self.children]
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UIElement":
        children = [UIElement.from_dict(c) for c in data.pop("children", [])]
        return cls(**data, children=children)


@dataclass
class AppActionResult:
    """Result of performing an action on a UI element via UAB."""
    success: bool
    action: str = ""
    element_id: Optional[str] = None
    state_changes: List[Dict[str, Any]] = field(default_factory=list)
    error_message: Optional[str] = None
    duration_ms: int = 0
    result_data: Optional[Any] = None  # v0.5.0: Office read results, screenshot paths, etc.

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppActionResult":
        return cls(**data)


@dataclass
class AppState:
    """Current state of a connected application."""
    pid: int
    window_title: Optional[str] = None
    window_size: Optional[Dict[str, int]] = None
    window_position: Optional[Dict[str, int]] = None
    focused: bool = False
    active_element: Optional[Dict[str, Any]] = None
    modals: List[Dict[str, Any]] = field(default_factory=list)
    menus: List[Dict[str, Any]] = field(default_factory=list)
    clipboard: Optional[str] = None  # v0.5.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppState":
        return cls(**data)


@dataclass
class ConnectionResult:
    """Result of connecting to an application via UAB."""
    success: bool
    pid: int = 0
    framework: Optional[str] = None
    connection_method: Optional[str] = None
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConnectionResult":
        return cls(**data)


# =============================================================================
# Provider Health
# =============================================================================


@dataclass
class ProviderHealth:
    """
    Health status of a tool provider.

    Includes version, state, and diagnostic information.
    """
    provider_id: str
    state: ProviderState
    version: Optional[str] = None
    last_check: Optional[str] = None
    capabilities: List[str] = field(default_factory=list)
    degraded_reasons: List[str] = field(default_factory=list)
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        d = asdict(self)
        d["state"] = self.state.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProviderHealth":
        """Create ProviderHealth from dictionary."""
        data["state"] = ProviderState(data["state"])
        return cls(**data)

    @property
    def is_healthy(self) -> bool:
        """True if provider is healthy."""
        return self.state == ProviderState.HEALTHY

    @property
    def is_available(self) -> bool:
        """True if provider is available (healthy or degraded)."""
        return self.state != ProviderState.OFFLINE


# =============================================================================
# Tool Intent
# =============================================================================


@dataclass
class ToolIntent:
    """
    Structured tool invocation intent from the Planner.

    Captures what needs to be done, the risk level, and verification steps.
    Used by the Router to select the appropriate provider.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    capability: Capability = Capability.SHELL_EXEC
    action: str = ""
    workspace: str = ""
    risk: RiskLevel = RiskLevel.LOW
    inputs: Dict[str, Any] = field(default_factory=dict)
    verify: List[str] = field(default_factory=list)
    provider_hint: Optional[str] = None  # Optional preferred provider
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        d = asdict(self)
        d["capability"] = self.capability.value
        d["risk"] = self.risk.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolIntent":
        """Create ToolIntent from dictionary."""
        data["capability"] = Capability(data["capability"])
        data["risk"] = RiskLevel(data["risk"])
        return cls(**data)


@dataclass
class PolicySnapshot:
    """
    Snapshot of the policy state applied to a tool invocation.

    Captures the security context at invocation time for auditing.
    """
    network_allowed: bool = False
    allowlist_applied: List[str] = field(default_factory=list)
    denylist_applied: List[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    workspace_restricted: bool = True
    verifier_approved: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        d = asdict(self)
        d["risk_level"] = self.risk_level.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PolicySnapshot":
        """Create PolicySnapshot from dictionary."""
        data["risk_level"] = RiskLevel(data["risk_level"])
        return cls(**data)


# =============================================================================
# Capability Protocols (Interfaces)
# =============================================================================


@runtime_checkable
class ShellExecCapability(Protocol):
    """
    Capability for executing shell commands.

    Implementations must run commands in a sandboxed environment
    with bounded output, timeout protection, and no host execution.
    """

    def run(
        self,
        command: Union[str, List[str]],
        cwd: str,
        env: Optional[Dict[str, str]] = None,
        timeout_s: int = 60,
        stream: bool = False,
        network: bool = False,
    ) -> ExecResult:
        """
        Execute a command in the sandbox.

        Args:
            command: Command string or list of arguments
            cwd: Working directory (must be within workspace)
            env: Optional environment variables
            timeout_s: Timeout in seconds
            stream: If True, stream output (implementation-specific)
            network: If True, allow network access (requires policy approval)

        Returns:
            ExecResult with stdout, stderr, exit code, and timing
        """
        ...


@runtime_checkable
class RepoOpsCapability(Protocol):
    """
    Capability for Git repository operations.

    Implementations must validate paths, prevent traversal,
    and track file changes for receipts.
    """

    def status(self, workspace: str) -> Dict[str, Any]:
        """Get repository status."""
        ...

    def diff(self, workspace: str, ref: Optional[str] = None) -> str:
        """Get diff output."""
        ...

    def apply_patch(
        self,
        workspace: str,
        patch: str,
        dry_run: bool = False,
    ) -> PatchResult:
        """
        Apply a unified diff patch.

        Args:
            workspace: Repository path
            patch: Unified diff content
            dry_run: If True, validate without applying

        Returns:
            PatchResult with success status and file changes
        """
        ...

    def commit(
        self,
        workspace: str,
        message: str,
        files: Optional[List[str]] = None,
    ) -> str:
        """Create a commit. Returns commit hash."""
        ...

    def branch(
        self,
        workspace: str,
        name: str,
        checkout: bool = True,
    ) -> bool:
        """Create and optionally checkout a branch."""
        ...

    def checkout(self, workspace: str, ref: str) -> bool:
        """Checkout a ref (branch, tag, commit)."""
        ...


@runtime_checkable
class FileOpsCapability(Protocol):
    """
    Capability for file system operations.

    Implementations must enforce workspace boundaries and
    use atomic writes to prevent corruption.
    """

    def read(self, path: str) -> str:
        """Read file contents."""
        ...

    def write(self, path: str, content: str, atomic: bool = True) -> FileChange:
        """
        Write content to file.

        Args:
            path: File path (must be within workspace)
            content: Content to write
            atomic: If True, use atomic write (temp + rename)

        Returns:
            FileChange record with hashes
        """
        ...

    def list(self, path: str, recursive: bool = False) -> List[str]:
        """List files in directory."""
        ...

    def apply_diff(self, path: str, diff: str) -> FileChange:
        """Apply a unified diff to a file."""
        ...

    def delete(self, path: str) -> FileChange:
        """Delete a file."""
        ...


@runtime_checkable
class WebOpsCapability(Protocol):
    """
    Capability for web operations.

    Implementations must respect network policies and
    sandbox all downloads to the workspace.
    """

    def fetch(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        timeout_s: int = 30,
    ) -> Tuple[int, str, Dict[str, str]]:
        """
        Fetch URL content.

        Returns:
            Tuple of (status_code, body, response_headers)
        """
        ...

    def download(
        self,
        url: str,
        dest_path: str,
        timeout_s: int = 60,
    ) -> FileChange:
        """
        Download file to workspace.

        Args:
            url: Source URL
            dest_path: Destination path (must be within workspace)
            timeout_s: Timeout in seconds

        Returns:
            FileChange record for the downloaded file
        """
        ...

    def screenshot(
        self,
        url: str,
        dest_path: str,
        width: int = 1280,
        height: int = 720,
    ) -> FileChange:
        """Take screenshot of URL."""
        ...


@runtime_checkable
class UIBuilderCapability(Protocol):
    """
    Capability for UI scaffolding.

    Two modes:
    - DETERMINISTIC: Template-based scaffolding (required provider)
    - GENERATIVE: AI-generated (optional Antigravity provider)
    """

    def scaffold(
        self,
        template_id: str,
        spec: Dict[str, Any],
        workspace: str,
        mode: UIBuilderMode = UIBuilderMode.DETERMINISTIC,
    ) -> ScaffoldResult:
        """
        Scaffold a UI project.

        Args:
            template_id: Template identifier (e.g., "nextjs_shadcn_dashboard")
            spec: Specification with routes, components, data models, etc.
            workspace: Output directory
            mode: DETERMINISTIC (templates) or GENERATIVE (Antigravity)

        Returns:
            ScaffoldResult with created files and build status
        """
        ...

    def list_templates(self) -> List[Dict[str, Any]]:
        """List available templates with metadata."""
        ...

    def verify_build(self, workspace: str) -> bool:
        """Verify the scaffolded project builds successfully."""
        ...


@runtime_checkable
class DeployOpsCapability(Protocol):
    """
    Capability for deployment operations.

    All operations run inside the sandbox container.
    """

    def build(
        self,
        workspace: str,
        dockerfile: Optional[str] = None,
        tag: str = "latest",
    ) -> ExecResult:
        """Build container image."""
        ...

    def test(
        self,
        workspace: str,
        test_command: str = "pytest",
    ) -> ExecResult:
        """Run tests."""
        ...

    def package(
        self,
        workspace: str,
        output_path: str,
        format: str = "tar.gz",
    ) -> FileChange:
        """Package artifacts for deployment."""
        ...

    def deploy(
        self,
        workspace: str,
        target: str,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Deploy to target.

        Args:
            workspace: Source directory
            target: Deployment target identifier
            config: Deployment configuration

        Returns:
            Deployment result with URLs, status, etc.
        """
        ...


@runtime_checkable
class VisionControlCapability(Protocol):
    """
    Capability for vision-based UI control.

    Requires Antigravity provider. Explicit failure when unavailable
    (no silent downgrade to alternative providers).
    """

    def capture_screen(self) -> Tuple[bytes, str]:
        """
        Capture current screen state.

        Returns:
            Tuple of (screenshot_bytes, hash)
        """
        ...

    def locate_element(
        self,
        selector_or_description: str,
        screenshot: Optional[bytes] = None,
    ) -> List[Dict[str, Any]]:
        """
        Locate UI elements by selector or natural language description.

        Returns:
            List of detected elements with coordinates and confidence
        """
        ...

    def perform_action(
        self,
        action: str,  # "click", "type", "drag", "scroll"
        target: Dict[str, Any],
        value: Optional[str] = None,
    ) -> VisionResult:
        """
        Perform UI action.

        Args:
            action: Action type
            target: Target element with coordinates
            value: Optional value for type action

        Returns:
            VisionResult with action status
        """
        ...

    def verify_state(
        self,
        expected: Dict[str, Any],
        screenshot: Optional[bytes] = None,
    ) -> VisionResult:
        """Verify UI matches expected state."""
        ...


@runtime_checkable
class AppControlCapability(Protocol):
    """
    Capability for framework-level desktop application control.

    Requires the Universal App Bridge (UAB) daemon running on the host.
    Uses framework-specific hooks (CDP, Qt MOC, GTK GIR, etc.) to get
    structured access to application UI trees.

    Licensed separately under BSL 1.1 — see packages/uab/LICENSE.
    """

    def detect(self) -> List[DetectedApp]:
        """
        Detect controllable desktop applications.

        Scans running processes and identifies UI frameworks via
        library signatures, process metadata, and binary structure.

        Returns:
            List of detected applications with framework info
        """
        ...

    def connect(self, target: Union[int, str]) -> ConnectionResult:
        """
        Connect to an application by PID or name.

        Establishes a framework-specific hook (e.g., CDP for Electron,
        MOC for Qt) to enable structured UI access.

        Args:
            target: Process ID or application name

        Returns:
            ConnectionResult with connection status and method used
        """
        ...

    def enumerate(self, pid: int) -> List[UIElement]:
        """
        Enumerate all UI elements in a connected application.

        Walks the framework's UI tree and maps elements to the unified
        UIElement schema with types, labels, properties, and bounds.

        Args:
            pid: Process ID of connected application

        Returns:
            List of top-level UIElements (with nested children)
        """
        ...

    def query(self, pid: int, selector: Dict[str, Any]) -> List[UIElement]:
        """
        Search for UI elements matching a selector.

        Supports filtering by type, label (fuzzy), properties, visibility,
        and combination selectors.

        Args:
            pid: Process ID of connected application
            selector: Search criteria (type, label, properties, visible, limit)

        Returns:
            List of matching UIElements
        """
        ...

    def act(
        self,
        pid: int,
        element_id: str,
        action: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> AppActionResult:
        """
        Perform an action on a UI element.

        Supported actions: click, doubleclick, rightclick, type, clear,
        select, scroll, focus, hover, expand, collapse, invoke, check,
        uncheck, toggle, keypress, hotkey.

        Args:
            pid: Process ID of connected application
            element_id: Target element identifier
            action: Action type
            params: Optional parameters (text for type, value for select, etc.)

        Returns:
            AppActionResult with success status and observed state changes
        """
        ...

    def state(self, pid: int) -> AppState:
        """
        Get current application state.

        Returns window info, active element, open modals, and menu state.

        Args:
            pid: Process ID of connected application

        Returns:
            AppState snapshot
        """
        ...


# =============================================================================
# Provider Base Class
# =============================================================================


class BaseProvider(ABC):
    """
    Base class for Tool Fabric providers.

    Providers implement one or more capabilities and must
    provide health checks for status monitoring.
    """

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Unique provider identifier."""
        ...

    @property
    @abstractmethod
    def capabilities(self) -> List[Capability]:
        """List of capabilities this provider implements."""
        ...

    @abstractmethod
    def health_check(self) -> ProviderHealth:
        """
        Check provider health.

        Returns:
            ProviderHealth with current state and diagnostics
        """
        ...

    def supports(self, capability: Capability) -> bool:
        """Check if provider supports a capability."""
        return capability in self.capabilities
