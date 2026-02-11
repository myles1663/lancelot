"""
Memory vNext Configuration — Token budgets, defaults, and settings.

This module defines hard limits and defaults for the memory subsystem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


# Default token budgets for core blocks
DEFAULT_CORE_BLOCK_BUDGETS: Dict[str, int] = {
    "persona": 500,
    "human": 400,
    "mission": 300,
    "operating_rules": 400,
    "workspace_state": 300,
}

# Total context budget for compiled context
MAX_CONTEXT_TOKENS: int = 128_000
MAX_CORE_BLOCKS_TOTAL_TOKENS: int = 2000
MAX_WORKING_MEMORY_TOKENS: int = 4000
MAX_RETRIEVAL_TOKENS: int = 16_000

# Confidence thresholds
MINIMUM_CONFIDENCE_FOR_CORE: float = 0.8
MINIMUM_CONFIDENCE_FOR_ARCHIVAL: float = 0.3
DEFAULT_CONFIDENCE: float = 0.5

# TTL defaults (in hours)
DEFAULT_WORKING_MEMORY_TTL_HOURS: int = 24
DEFAULT_ARCHIVAL_DECAY_HALF_LIFE_DAYS: int = 30

# Quarantine settings
QUARANTINE_BY_DEFAULT_FOR_CORE: bool = True
REQUIRE_PROVENANCE_FOR_CORE: bool = True

# Storage paths (relative to lancelot_data/)
MEMORY_DIR: str = "memory"
CORE_BLOCKS_FILE: str = "core_blocks.json"
WORKING_MEMORY_DB: str = "working_memory.sqlite"
EPISODIC_DB: str = "episodic.sqlite"
ARCHIVAL_DB: str = "archival.sqlite"
COMMITS_DIR: str = "commits"

# Secret patterns to detect and scrub
SECRET_PATTERNS: list[str] = [
    r"(?i)(api[_-]?key|apikey)\s*[:=]\s*['\"]?[\w-]{20,}",
    r"(?i)(secret|password|token)\s*[:=]\s*['\"]?[\w-]{8,}",
    r"(?i)bearer\s+[\w-]{20,}",
    r"sk-[a-zA-Z0-9]{24,}",  # OpenAI-style keys
    r"AIza[a-zA-Z0-9_-]{35}",  # Google API keys
]


@dataclass
class MemoryConfig:
    """Runtime configuration for the memory subsystem."""

    core_block_budgets: Dict[str, int] = field(
        default_factory=lambda: DEFAULT_CORE_BLOCK_BUDGETS.copy()
    )
    max_context_tokens: int = MAX_CONTEXT_TOKENS
    max_core_blocks_total: int = MAX_CORE_BLOCKS_TOTAL_TOKENS
    max_working_memory_tokens: int = MAX_WORKING_MEMORY_TOKENS
    max_retrieval_tokens: int = MAX_RETRIEVAL_TOKENS

    min_confidence_core: float = MINIMUM_CONFIDENCE_FOR_CORE
    min_confidence_archival: float = MINIMUM_CONFIDENCE_FOR_ARCHIVAL

    working_memory_ttl_hours: int = DEFAULT_WORKING_MEMORY_TTL_HOURS
    archival_decay_half_life_days: int = DEFAULT_ARCHIVAL_DECAY_HALF_LIFE_DAYS

    quarantine_by_default: bool = QUARANTINE_BY_DEFAULT_FOR_CORE
    require_provenance: bool = REQUIRE_PROVENANCE_FOR_CORE

    def get_block_budget(self, block_type: str) -> int:
        """Get token budget for a specific block type."""
        return self.core_block_budgets.get(block_type, 200)

    def validate_block_size(self, block_type: str, token_count: int) -> tuple[bool, str]:
        """
        Validate if a block's token count is within budget.

        Returns:
            Tuple of (is_valid, message)
        """
        budget = self.get_block_budget(block_type)
        if token_count > budget:
            return False, f"Block '{block_type}' exceeds budget: {token_count} > {budget}"

        warning_threshold = int(budget * 0.8)
        if token_count > warning_threshold:
            return True, f"Block '{block_type}' approaching budget: {token_count}/{budget}"

        return True, ""


# Global default configuration instance
default_config = MemoryConfig()
