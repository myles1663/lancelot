"""
Tool Fabric — Main Orchestration Layer
======================================

This module provides the main ToolFabric class that coordinates all
Tool Fabric components:
- Provider management
- Capability routing
- Policy enforcement
- Receipt generation
- Health monitoring

ToolFabric is the primary interface for executing tool operations.
All tool calls should go through this layer for proper auditing,
security, and provider management.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from src.tools.contracts import (
    Capability,
    RiskLevel,
    ExecResult,
    FileChange,
    PatchResult,
    ProviderHealth,
    ProviderState,
    ToolIntent,
    BaseProvider,
    ShellExecCapability,
    RepoOpsCapability,
    FileOpsCapability,
)
from src.tools.receipts import (
    ToolReceipt,
    create_tool_receipt,
)
from src.tools.policies import (
    PolicyEngine,
    PolicyConfig,
    PolicyDecision,
)
from src.tools.health import (
    HealthMonitor,
    HealthConfig,
)
from src.tools.router import (
    ProviderRouter,
    RouterConfig,
    RouteDecision,
)
from src.tools.providers.local_sandbox import (
    LocalSandboxProvider,
    SandboxConfig,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Tool Fabric Configuration
# =============================================================================


@dataclass
class ToolFabricConfig:
    """Configuration for Tool Fabric."""

    # Enable/disable flags
    enabled: bool = True
    safe_mode: bool = False  # When True, only local_sandbox + ui_templates

    # Component configs
    sandbox_config: Optional[SandboxConfig] = None
    health_config: Optional[HealthConfig] = None
    router_config: Optional[RouterConfig] = None
    policy_config: Optional[PolicyConfig] = None

    # Default workspace
    default_workspace: Optional[str] = None

    # Receipt settings
    emit_receipts: bool = True
    max_receipt_output: int = 10000


# =============================================================================
# Tool Fabric
# =============================================================================


class ToolFabric:
    """
    Main Tool Fabric orchestration class.

    Coordinates tool execution through:
    1. Intent creation
    2. Policy evaluation
    3. Provider selection
    4. Execution
    5. Receipt generation

    Example usage:
        fabric = ToolFabric()
        result = fabric.run_command("git status", workspace="/project")
        print(result.stdout)
    """

    def __init__(self, config: Optional[ToolFabricConfig] = None):
        """
        Initialize Tool Fabric.

        Args:
            config: Optional configuration (uses defaults if not provided)
        """
        self.config = config or ToolFabricConfig()

        # Initialize components
        self._policy_engine = PolicyEngine(self.config.policy_config)
        self._health_monitor = HealthMonitor(self.config.health_config)
        self._router = ProviderRouter(
            config=self.config.router_config,
            health_monitor=self._health_monitor,
            policy_engine=self._policy_engine,
        )

        # Initialize and register default providers
        self._setup_default_providers()

        # Run initial health sweep
        if self.config.enabled:
            self._health_monitor.sweep()

    # =========================================================================
    # Provider Setup
    # =========================================================================

    def _setup_default_providers(self) -> None:
        """Set up and register default providers."""
        # LocalSandboxProvider is always registered (required)
        sandbox = LocalSandboxProvider(
            config=self.config.sandbox_config,
            workspace=self.config.default_workspace,
        )
        self._health_monitor.register(sandbox)

    def register_provider(self, provider: BaseProvider) -> None:
        """
        Register an additional provider.

        Args:
            provider: Provider to register
        """
        self._health_monitor.register(provider)
        logger.info("Registered provider: %s", provider.provider_id)

    def unregister_provider(self, provider_id: str) -> None:
        """
        Unregister a provider.

        Args:
            provider_id: Provider ID to unregister
        """
        self._health_monitor.unregister(provider_id)
        logger.info("Unregistered provider: %s", provider_id)

    # =========================================================================
    # Command Execution (ShellExec)
    # =========================================================================

    def run_command(
        self,
        command: Union[str, List[str]],
        workspace: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout_s: int = 60,
        network: bool = False,
        risk: RiskLevel = RiskLevel.LOW,
        session_id: Optional[str] = None,
    ) -> ExecResult:
        """
        Execute a shell command through Tool Fabric.

        Args:
            command: Command string or list of arguments
            workspace: Working directory (uses default if not provided)
            env: Optional environment variables
            timeout_s: Timeout in seconds
            network: Whether to allow network access
            risk: Risk level for policy evaluation
            session_id: Optional session ID for receipt grouping

        Returns:
            ExecResult with stdout, stderr, exit code
        """
        if not self.config.enabled:
            return ExecResult(
                exit_code=1,
                stdout="",
                stderr="Tool Fabric is disabled",
                duration_ms=0,
            )

        workspace = workspace or self.config.default_workspace or "."
        cmd_str = command if isinstance(command, str) else " ".join(command)

        # Create intent
        intent = ToolIntent(
            capability=Capability.SHELL_EXEC,
            action="run",
            workspace=workspace,
            risk=risk,
            inputs={
                "command": cmd_str,
                "timeout_s": timeout_s,
                "network": network,
            },
        )

        # Route and execute
        return self._execute_shell(intent, command, env, timeout_s, network, session_id)

    def _execute_shell(
        self,
        intent: ToolIntent,
        command: Union[str, List[str]],
        env: Optional[Dict[str, str]],
        timeout_s: int,
        network: bool,
        session_id: Optional[str],
    ) -> ExecResult:
        """Execute shell command with routing and receipts."""
        start_time = time.time()

        # Create receipt
        receipt = None
        if self.config.emit_receipts:
            receipt = create_tool_receipt(
                capability=Capability.SHELL_EXEC,
                action="run",
                provider_id="pending",
                workspace=intent.workspace,
                inputs=intent.inputs,
                session_id=session_id,
            )

        # Route to provider
        route = self._router.select_for_intent(intent)

        if not route.success:
            result = ExecResult(
                exit_code=126,
                stdout="",
                stderr=f"Routing failed: {route.reason}",
                duration_ms=int((time.time() - start_time) * 1000),
                command=str(command),
                working_dir=intent.workspace,
            )
            if receipt:
                receipt.fail(route.reason)
                self._store_receipt(receipt)
            return result

        # Get provider
        provider = self._health_monitor.get_provider(route.provider_id)
        if not provider or not isinstance(provider, ShellExecCapability):
            result = ExecResult(
                exit_code=127,
                stdout="",
                stderr=f"Provider {route.provider_id} does not support ShellExec",
                duration_ms=int((time.time() - start_time) * 1000),
                command=str(command),
                working_dir=intent.workspace,
            )
            if receipt:
                receipt.fail("Provider capability mismatch")
                self._store_receipt(receipt)
            return result

        # Execute
        try:
            result = provider.run(
                command=command,
                cwd=intent.workspace,
                env=env,
                timeout_s=timeout_s,
                network=network,
            )

            # Update receipt
            if receipt:
                receipt.provider_id = route.provider_id
                receipt.with_exec_result(result, self.config.max_receipt_output)
                if route.policy_decision:
                    from src.tools.contracts import PolicySnapshot
                    snapshot = PolicySnapshot(
                        risk_level=intent.risk,
                        network_allowed=network,
                    )
                    receipt.with_policy(snapshot)
                self._store_receipt(receipt)

            return result

        except Exception as e:
            logger.exception("Command execution failed")
            result = ExecResult(
                exit_code=1,
                stdout="",
                stderr=f"Execution error: {str(e)[:200]}",
                duration_ms=int((time.time() - start_time) * 1000),
                command=str(command),
                working_dir=intent.workspace,
            )
            if receipt:
                receipt.fail(str(e))
                self._store_receipt(receipt)
            return result

    # =========================================================================
    # Repository Operations (RepoOps)
    # =========================================================================

    def git_status(
        self,
        workspace: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get git repository status."""
        workspace = workspace or self.config.default_workspace or "."
        provider = self._get_repo_provider(workspace)
        if not provider:
            return {"error": "No RepoOps provider available"}
        return provider.status(workspace)

    def git_diff(
        self,
        workspace: Optional[str] = None,
        ref: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        """Get git diff."""
        workspace = workspace or self.config.default_workspace or "."
        provider = self._get_repo_provider(workspace)
        if not provider:
            return "Error: No RepoOps provider available"
        return provider.diff(workspace, ref)

    def git_apply_patch(
        self,
        patch: str,
        workspace: Optional[str] = None,
        dry_run: bool = False,
        session_id: Optional[str] = None,
    ) -> PatchResult:
        """Apply a git patch."""
        workspace = workspace or self.config.default_workspace or "."
        provider = self._get_repo_provider(workspace)
        if not provider:
            return PatchResult(
                success=False,
                files_changed=[],
                error_message="No RepoOps provider available",
            )
        return provider.apply_patch(workspace, patch, dry_run)

    def git_commit(
        self,
        message: str,
        workspace: Optional[str] = None,
        files: Optional[List[str]] = None,
        session_id: Optional[str] = None,
    ) -> str:
        """Create a git commit."""
        workspace = workspace or self.config.default_workspace or "."
        provider = self._get_repo_provider(workspace)
        if not provider:
            return "Error: No RepoOps provider available"
        return provider.commit(workspace, message, files)

    def _get_repo_provider(self, workspace: str) -> Optional[RepoOpsCapability]:
        """Get a provider that supports RepoOps."""
        route = self._router.select_provider(Capability.REPO_OPS, workspace=workspace)
        if not route.success:
            return None
        provider = self._health_monitor.get_provider(route.provider_id)
        if isinstance(provider, RepoOpsCapability):
            return provider
        return None

    # =========================================================================
    # File Operations (FileOps)
    # =========================================================================

    def read_file(
        self,
        path: str,
        workspace: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        """Read file contents."""
        workspace = workspace or self.config.default_workspace or "."
        provider = self._get_file_provider(workspace)
        if not provider:
            return "Error: No FileOps provider available"

        # Evaluate path policy
        decision = self._policy_engine.evaluate_path(path, workspace, "read")
        if not decision.allowed:
            return f"Error: {'; '.join(decision.reasons)}"

        return provider.read(path)

    def write_file(
        self,
        path: str,
        content: str,
        workspace: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> FileChange:
        """Write file contents."""
        workspace = workspace or self.config.default_workspace or "."
        provider = self._get_file_provider(workspace)
        if not provider:
            return FileChange(path=path, action="error")

        # Evaluate path policy
        decision = self._policy_engine.evaluate_path(path, workspace, "write")
        if not decision.allowed:
            return FileChange(path=path, action="error")

        return provider.write(path, content)

    def list_files(
        self,
        path: str,
        workspace: Optional[str] = None,
        recursive: bool = False,
        session_id: Optional[str] = None,
    ) -> List[str]:
        """List files in directory."""
        workspace = workspace or self.config.default_workspace or "."
        provider = self._get_file_provider(workspace)
        if not provider:
            return ["Error: No FileOps provider available"]
        return provider.list(path, recursive)

    def _get_file_provider(self, workspace: str) -> Optional[FileOpsCapability]:
        """Get a provider that supports FileOps."""
        route = self._router.select_provider(Capability.FILE_OPS, workspace=workspace)
        if not route.success:
            return None
        provider = self._health_monitor.get_provider(route.provider_id)
        if isinstance(provider, FileOpsCapability):
            return provider
        return None

    # =========================================================================
    # Health and Status
    # =========================================================================

    def get_health(self) -> Dict[str, ProviderHealth]:
        """Get health status of all providers."""
        return self._health_monitor.get_all_health()

    def probe_health(self, provider_id: Optional[str] = None) -> Dict[str, ProviderHealth]:
        """
        Probe health of providers.

        Args:
            provider_id: Specific provider to probe, or None for all

        Returns:
            Dict of provider health
        """
        if provider_id:
            health = self._health_monitor.probe(provider_id, force=True)
            return {provider_id: health}
        return self._health_monitor.sweep()

    def get_routing_summary(self) -> Dict[str, Any]:
        """Get routing configuration summary."""
        return self._router.get_routing_summary()

    def is_available(self, capability: Capability) -> bool:
        """Check if a capability is available."""
        route = self._router.select_provider(capability)
        return route.success

    # =========================================================================
    # Safe Mode
    # =========================================================================

    def enable_safe_mode(self) -> None:
        """
        Enable safe mode.

        In safe mode, only local_sandbox and ui_templates are used.
        All optional CLI providers and Antigravity are disabled.
        """
        self.config.safe_mode = True
        self._router.set_preferences(
            Capability.UI_BUILDER,
            ["ui_templates"],
        )
        logger.info("Safe mode enabled")

    def disable_safe_mode(self) -> None:
        """Disable safe mode."""
        self.config.safe_mode = False
        # Restore default preferences
        self._router.set_preferences(
            Capability.UI_BUILDER,
            ["ui_templates", "ui_antigravity"],
        )
        logger.info("Safe mode disabled")

    # =========================================================================
    # Receipt Management
    # =========================================================================

    def _store_receipt(self, receipt: ToolReceipt) -> None:
        """Store a tool receipt."""
        # For now, just log. Integration with ReceiptService in future prompt.
        logger.debug(
            "Tool receipt: %s/%s provider=%s success=%s",
            receipt.capability,
            receipt.action,
            receipt.provider_id,
            receipt.success,
        )


# =============================================================================
# Global Tool Fabric Instance
# =============================================================================


_fabric: Optional[ToolFabric] = None
_fabric_lock = __import__("threading").Lock()


def get_tool_fabric(config: Optional[ToolFabricConfig] = None) -> ToolFabric:
    """
    Get the global ToolFabric instance.

    Args:
        config: Optional config (only used on first call)

    Returns:
        Global ToolFabric instance
    """
    global _fabric
    if _fabric is None:
        with _fabric_lock:
            if _fabric is None:
                _fabric = ToolFabric(config)
    return _fabric


def reset_tool_fabric() -> None:
    """Reset the global ToolFabric (for testing)."""
    global _fabric
    with _fabric_lock:
        _fabric = None
