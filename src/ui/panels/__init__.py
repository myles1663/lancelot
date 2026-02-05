"""
War Room Panels — Data providers for the War Room UI.

Each panel provides methods for displaying and interacting with
a specific subsystem of Lancelot.
"""

from .health_panel import HealthPanel
from .scheduler_panel import SchedulerPanel
from .skills_panel import SkillsPanel
from .soul_panel import SoulPanel
from .memory_panel import MemoryPanel
from .tools_panel import (
    ToolsPanel,
    ToolsPanelConfig,
    get_tools_panel,
    reset_tools_panel,
    render_tools_panel,
)

__all__ = [
    # Health Panel
    "HealthPanel",
    # Scheduler Panel
    "SchedulerPanel",
    # Skills Panel
    "SkillsPanel",
    # Soul Panel
    "SoulPanel",
    # Memory Panel
    "MemoryPanel",
    # Tools Panel
    "ToolsPanel",
    "ToolsPanelConfig",
    "get_tools_panel",
    "reset_tools_panel",
    "render_tools_panel",
]
