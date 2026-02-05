"""
Memory vNext Write Gates — Safety validation for memory writes.

This module provides the WriteGateValidator for:
- Block allowlist enforcement
- Provenance validation
- Secret detection and scrubbing
- Quarantine-by-default for core edits
- Confidence threshold enforcement
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .config import (
    MemoryConfig,
    default_config,
    SECRET_PATTERNS,
    MINIMUM_CONFIDENCE_FOR_CORE,
    QUARANTINE_BY_DEFAULT_FOR_CORE,
    REQUIRE_PROVENANCE_FOR_CORE,
)
from .schemas import (
    CoreBlockType,
    MemoryEdit,
    MemoryEditOp,
    MemoryStatus,
    ProvenanceType,
)

logger = logging.getLogger(__name__)


@dataclass
class GateResult:
    """Result of a write gate validation."""
    allowed: bool
    reason: str
    scrubbed_content: Optional[str] = None
    suggested_status: MemoryStatus = MemoryStatus.active
    warnings: list[str] = field(default_factory=list)

    def add_warning(self, warning: str) -> None:
        """Add a warning message."""
        self.warnings.append(warning)


class WriteGateValidator:
    """
    Validates memory writes against safety rules.

    Enforces:
    - Block allowlist: which blocks agents can modify
    - Provenance requirements: evidence needed for writes
    - Secret scrubbing: removes API keys, tokens, etc.
    - Confidence thresholds: minimum confidence for core blocks
    - Quarantine-by-default: new core edits go to quarantine
    """

    # Blocks that agents are allowed to modify
    DEFAULT_AGENT_ALLOWLIST = {
        CoreBlockType.mission,
        CoreBlockType.workspace_state,
        # human and persona require owner approval
    }

    # Blocks that require owner approval
    OWNER_ONLY_BLOCKS = {
        CoreBlockType.persona,
        CoreBlockType.human,
        CoreBlockType.operating_rules,
    }

    def __init__(
        self,
        config: Optional[MemoryConfig] = None,
        agent_allowlist: Optional[set[CoreBlockType]] = None,
        secret_patterns: Optional[list[str]] = None,
    ):
        """
        Initialize the write gate validator.

        Args:
            config: Memory configuration
            agent_allowlist: Blocks agents can modify (defaults to DEFAULT_AGENT_ALLOWLIST)
            secret_patterns: Regex patterns for secret detection
        """
        self.config = config or default_config
        self.agent_allowlist = agent_allowlist or self.DEFAULT_AGENT_ALLOWLIST
        self.secret_patterns = [
            re.compile(p, re.IGNORECASE)
            for p in (secret_patterns or SECRET_PATTERNS)
        ]

    def validate_edit(
        self,
        edit: MemoryEdit,
        editor: str = "agent",
    ) -> GateResult:
        """
        Validate a memory edit against write gates.

        Args:
            edit: The memory edit to validate
            editor: Who is making the edit ('owner', 'agent', 'system')

        Returns:
            GateResult indicating if the edit is allowed
        """
        # Check if this is a core block edit
        if edit.is_core_edit():
            return self._validate_core_edit(edit, editor)
        else:
            return self._validate_item_edit(edit, editor)

    def _validate_core_edit(
        self,
        edit: MemoryEdit,
        editor: str,
    ) -> GateResult:
        """Validate a core block edit."""
        _, block_type_str = edit.get_target_parts()

        try:
            block_type = CoreBlockType(block_type_str)
        except ValueError:
            return GateResult(
                allowed=False,
                reason=f"Invalid core block type: {block_type_str}",
            )

        # Check allowlist for agents
        if editor == "agent":
            if block_type not in self.agent_allowlist:
                return GateResult(
                    allowed=False,
                    reason=f"Block '{block_type.value}' requires owner approval for agent edits",
                )

        # Validate provenance for sensitive blocks
        if block_type in self.OWNER_ONLY_BLOCKS and self.config.require_provenance:
            if not edit.provenance:
                return GateResult(
                    allowed=False,
                    reason=f"Block '{block_type.value}' requires provenance for edits",
                )

            # Check for user message provenance
            has_valid_provenance = any(
                p.type in (ProvenanceType.user_message, ProvenanceType.system)
                for p in edit.provenance
            )
            if not has_valid_provenance and editor == "agent":
                return GateResult(
                    allowed=False,
                    reason=f"Block '{block_type.value}' requires user message or system provenance",
                )

        # Check confidence threshold
        if edit.confidence < self.config.min_confidence_core:
            return GateResult(
                allowed=False,
                reason=f"Confidence {edit.confidence:.2f} below threshold {self.config.min_confidence_core}",
            )

        # Check for secrets in content
        content = edit.after or ""
        scrubbed, had_secrets = self._scrub_secrets(content)
        if had_secrets:
            result = GateResult(
                allowed=True,
                reason="Content scrubbed of secrets",
                scrubbed_content=scrubbed,
                suggested_status=MemoryStatus.quarantined if self.config.quarantine_by_default else MemoryStatus.staged,
            )
            result.add_warning("Secret patterns detected and scrubbed from content")
            return result

        # Determine suggested status
        if self.config.quarantine_by_default and editor == "agent":
            suggested_status = MemoryStatus.quarantined
        else:
            suggested_status = MemoryStatus.staged if editor == "agent" else MemoryStatus.active

        return GateResult(
            allowed=True,
            reason="Passed all write gates",
            suggested_status=suggested_status,
        )

    def _validate_item_edit(
        self,
        edit: MemoryEdit,
        editor: str,
    ) -> GateResult:
        """Validate a memory item edit."""
        # Check confidence for archival
        tier, _ = edit.get_target_parts()
        if tier == "archival" and edit.confidence < self.config.min_confidence_archival:
            return GateResult(
                allowed=False,
                reason=f"Confidence {edit.confidence:.2f} below archival threshold {self.config.min_confidence_archival}",
            )

        # Check for secrets
        content = edit.after or ""
        scrubbed, had_secrets = self._scrub_secrets(content)
        if had_secrets:
            result = GateResult(
                allowed=True,
                reason="Content scrubbed of secrets",
                scrubbed_content=scrubbed,
            )
            result.add_warning("Secret patterns detected and scrubbed from content")
            return result

        return GateResult(
            allowed=True,
            reason="Passed all write gates",
        )

    def _scrub_secrets(self, content: str) -> tuple[str, bool]:
        """
        Scrub secrets from content.

        Args:
            content: Content to scrub

        Returns:
            Tuple of (scrubbed_content, had_secrets)
        """
        if not content:
            return content, False

        scrubbed = content
        had_secrets = False

        for pattern in self.secret_patterns:
            if pattern.search(scrubbed):
                had_secrets = True
                scrubbed = pattern.sub("[REDACTED]", scrubbed)

        return scrubbed, had_secrets

    def check_for_secrets(self, content: str) -> list[str]:
        """
        Check content for secret patterns.

        Args:
            content: Content to check

        Returns:
            List of detected secret pattern names
        """
        detected = []
        for i, pattern in enumerate(self.secret_patterns):
            if pattern.search(content):
                detected.append(f"secret_pattern_{i}")
        return detected

    def is_block_agent_writable(self, block_type: CoreBlockType) -> bool:
        """
        Check if a block type is writable by agents.

        Args:
            block_type: The block type to check

        Returns:
            True if agents can write to this block
        """
        return block_type in self.agent_allowlist

    def get_allowlist_summary(self) -> dict[str, Any]:
        """
        Get a summary of the allowlist configuration.

        Returns:
            Dictionary with allowlist info
        """
        return {
            "agent_writable": [b.value for b in self.agent_allowlist],
            "owner_only": [b.value for b in self.OWNER_ONLY_BLOCKS],
            "all_blocks": [b.value for b in CoreBlockType],
            "quarantine_by_default": self.config.quarantine_by_default,
            "require_provenance": self.config.require_provenance,
        }


class QuarantineManager:
    """
    Manages quarantined memory items.

    Provides functionality to:
    - List quarantined items
    - Approve/reject quarantined items
    - Track quarantine history
    """

    def __init__(
        self,
        core_store: Any,  # CoreBlockStore
        store_manager: Any,  # MemoryStoreManager
    ):
        """
        Initialize the quarantine manager.

        Args:
            core_store: Store for core blocks
            store_manager: Manager for tiered memory stores
        """
        self.core_store = core_store
        self.store_manager = store_manager

    def list_quarantined_core_blocks(self) -> list[tuple[CoreBlockType, Any]]:
        """
        List quarantined core blocks.

        Returns:
            List of (block_type, block) tuples
        """
        quarantined = []
        blocks = self.core_store.get_all_blocks()

        for block_type_str, block in blocks.items():
            if block.status == MemoryStatus.quarantined:
                quarantined.append((CoreBlockType(block_type_str), block))

        return quarantined

    def list_quarantined_items(self, tier: str = "all") -> list[Any]:
        """
        List quarantined memory items.

        Args:
            tier: Tier to check ('working', 'episodic', 'archival', or 'all')

        Returns:
            List of quarantined MemoryItems
        """
        from .schemas import MemoryTier

        quarantined = []
        tiers_to_check = []

        if tier == "all":
            tiers_to_check = [MemoryTier.working, MemoryTier.episodic, MemoryTier.archival]
        else:
            tiers_to_check = [MemoryTier(tier)]

        for t in tiers_to_check:
            store = self.store_manager.get_store(t)
            items = store.list_items(status=MemoryStatus.quarantined, include_expired=True)
            quarantined.extend(items)

        return quarantined

    def approve_core_block(
        self,
        block_type: CoreBlockType,
        approver: str,
    ) -> bool:
        """
        Approve a quarantined core block.

        Args:
            block_type: Block type to approve
            approver: Who is approving

        Returns:
            True if approved successfully
        """
        block = self.core_store.get_block(block_type)
        if block is None:
            return False

        if block.status != MemoryStatus.quarantined:
            logger.warning("Block %s is not quarantined", block_type.value)
            return False

        result = self.core_store.update_block_status(block_type, MemoryStatus.active)
        if result:
            logger.info("Approved quarantined block %s by %s", block_type.value, approver)
        return result is not None

    def reject_core_block(
        self,
        block_type: CoreBlockType,
        rejector: str,
        reason: str,
    ) -> bool:
        """
        Reject a quarantined core block (revert to previous content).

        Args:
            block_type: Block type to reject
            rejector: Who is rejecting
            reason: Reason for rejection

        Returns:
            True if rejected successfully
        """
        # For now, just mark as deprecated
        # In a full implementation, this would restore previous version
        block = self.core_store.get_block(block_type)
        if block is None:
            return False

        if block.status != MemoryStatus.quarantined:
            return False

        result = self.core_store.update_block_status(block_type, MemoryStatus.deprecated)
        if result:
            logger.info("Rejected quarantined block %s by %s: %s", block_type.value, rejector, reason)
        return result is not None

    def approve_item(
        self,
        item_id: str,
        tier: str,
        approver: str,
    ) -> bool:
        """
        Approve a quarantined memory item.

        Args:
            item_id: Item ID to approve
            tier: Memory tier
            approver: Who is approving

        Returns:
            True if approved successfully
        """
        from .schemas import MemoryTier

        store = self.store_manager.get_store(MemoryTier(tier))
        result = store.update_status(item_id, MemoryStatus.active)
        if result:
            logger.info("Approved quarantined item %s by %s", item_id, approver)
        return result

    def reject_item(
        self,
        item_id: str,
        tier: str,
        rejector: str,
    ) -> bool:
        """
        Reject (delete) a quarantined memory item.

        Args:
            item_id: Item ID to reject
            tier: Memory tier
            rejector: Who is rejecting

        Returns:
            True if deleted successfully
        """
        from .schemas import MemoryTier

        store = self.store_manager.get_store(MemoryTier(tier))
        result = store.delete(item_id)
        if result:
            logger.info("Rejected and deleted quarantined item %s by %s", item_id, rejector)
        return result
