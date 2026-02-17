"""
Tool Fabric Providers
=====================

Provider implementations for Tool Fabric capabilities.

Required:
- local_sandbox: Docker-based tool runner (LocalSandboxProvider)
- ui_templates: Deterministic template scaffolder (TemplateScaffolder)

Optional:
- host_execution: Direct host execution (DANGEROUS, gated by FEATURE_TOOLS_HOST_EXECUTION)
- cli_aider: Aider CLI adapter
- cli_opencode: OpenCode CLI adapter
- cli_continue: Continue headless CLI adapter
- cli_open_interpreter: Open Interpreter CLI adapter
- ui_antigravity: Antigravity UI generation adapter
- vision_antigravity: Antigravity vision control adapter
"""

from src.tools.providers.local_sandbox import (
    LocalSandboxProvider,
    SandboxConfig,
    create_local_sandbox,
)
from src.tools.providers.ui_templates import (
    TemplateScaffolder,
    TemplateConfig,
    create_template_scaffolder,
    TEMPLATES,
)
from src.tools.providers.ui_antigravity import (
    AntigravityUIProvider,
    AntigravityUIConfig,
    create_antigravity_ui_provider,
)
from src.tools.providers.vision_antigravity import (
    AntigravityVisionProvider,
    VisionConfig,
    create_vision_provider,
    AntigravityUnavailableError,
)
from src.tools.providers.host_execution import (
    HostExecutionProvider,
    HostExecConfig,
)

__all__ = [
    # Local Sandbox
    "LocalSandboxProvider",
    "SandboxConfig",
    "create_local_sandbox",
    # UI Templates
    "TemplateScaffolder",
    "TemplateConfig",
    "create_template_scaffolder",
    "TEMPLATES",
    # Antigravity UI
    "AntigravityUIProvider",
    "AntigravityUIConfig",
    "create_antigravity_ui_provider",
    # Antigravity Vision
    "AntigravityVisionProvider",
    "VisionConfig",
    "create_vision_provider",
    "AntigravityUnavailableError",
    # Host Execution
    "HostExecutionProvider",
    "HostExecConfig",
]
