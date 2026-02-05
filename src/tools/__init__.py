"""
Lancelot Tool Fabric
====================

A capability-based abstraction layer for tool execution that decouples
Lancelot from vendor-specific tooling (Gemini CLI, Antigravity).

Core modules:
- contracts: Capability interfaces and type definitions
- receipts: Tool-specific receipt extensions
- policies: Risk policies, allowlists, network rules
- router: Provider routing and failover
- health: Provider discovery and health probes
- fabric: Main Tool Fabric orchestration

Feature Flags:
- FEATURE_TOOLS_FABRIC: Global enable (default: true)
- FEATURE_TOOLS_CLI_PROVIDERS: Enable optional CLI providers (default: false)
- FEATURE_TOOLS_ANTIGRAVITY: Enable Antigravity providers (default: false)
- FEATURE_TOOLS_NETWORK: Allow network access in sandbox (default: false)
- FEATURE_TOOLS_HOST_EXECUTION: Allow host execution (default: false, dangerous)
"""

from src.tools.contracts import (
    Capability,
    ShellExecCapability,
    RepoOpsCapability,
    FileOpsCapability,
    WebOpsCapability,
    UIBuilderCapability,
    DeployOpsCapability,
    VisionControlCapability,
    ExecResult,
    ProviderHealth,
    ProviderState,
    ToolIntent,
    RiskLevel,
)

from src.tools.receipts import (
    ToolReceipt,
    VisionReceipt,
    create_tool_receipt,
)

__all__ = [
    # Capabilities
    "Capability",
    "ShellExecCapability",
    "RepoOpsCapability",
    "FileOpsCapability",
    "WebOpsCapability",
    "UIBuilderCapability",
    "DeployOpsCapability",
    "VisionControlCapability",
    # Types
    "ExecResult",
    "ProviderHealth",
    "ProviderState",
    "ToolIntent",
    "RiskLevel",
    # Receipts
    "ToolReceipt",
    "VisionReceipt",
    "create_tool_receipt",
]
