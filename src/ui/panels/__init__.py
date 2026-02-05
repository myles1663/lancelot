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

__all__ = [
    "HealthPanel",
    "SchedulerPanel",
    "SkillsPanel",
    "SoulPanel",
    "MemoryPanel",
]
