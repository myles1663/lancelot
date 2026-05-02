"""
Structured Memory Context Compiler — Deterministic prompt assembly.

This module provides the ContextCompiler that assembles the runtime prompt from:
1. Core Memory Blocks (persona, human, operating_rules, mission, workspace_state)
2. Working Memory (task-scoped, TTL-filtered)
3. Retrieved memories (archival/episodic, relevance-ranked)

Features:
- Deterministic compilation order
- Token budget enforcement
- Receipt emission for audit
- Inclusion/exclusion tracing
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from .config import (
    MemoryConfig,
    default_config,
    MAX_CONTEXT_TOKENS,
    MAX_CORE_BLOCKS_TOTAL_TOKENS,
    MAX_WORKING_MEMORY_TOKENS,
    MAX_RETRIEVAL_TOKENS,
)
from .schemas import (
    CompiledContext,
    CoreBlockType,
    MemoryItem,
    MemoryStatus,
    MemoryTier,
    Provenance,
    ProvenanceType,
)
from .store import CoreBlockStore, estimate_tokens
from .ethics import MemoryEthicsAction, MemoryEthicsEvaluator
from src.core.security import detect_injection

logger = logging.getLogger(__name__)


# Canonical order for core blocks in compiled context
CORE_BLOCK_ORDER = [
    CoreBlockType.persona,
    CoreBlockType.human,
    CoreBlockType.operating_rules,
    CoreBlockType.mission,
    CoreBlockType.workspace_state,
]


class ContextCompiler:
    """
    Compiles a deterministic context from memory blocks and retrieval.

    The compiler assembles the prompt in a fixed order:
    1. Core blocks (in CORE_BLOCK_ORDER)
    2. Working memory (filtered by namespace and TTL)
    3. Retrieved items (from archival/episodic)

    Each step respects token budgets and emits detailed traces.
    """

    VERSION = "1.0.0"

    def __init__(
        self,
        core_store: CoreBlockStore,
        config: Optional[MemoryConfig] = None,
        soul_version: Optional[str] = None,
    ):
        """
        Initialize the context compiler.

        Args:
            core_store: The CoreBlockStore for core blocks
            config: Memory configuration (uses default if not provided)
            soul_version: Optional Soul version string for tracking
        """
        self.core_store = core_store
        self.config = config or default_config
        self.soul_version = soul_version
        self.ethics = MemoryEthicsEvaluator()

    def compile(
        self,
        objective: str,
        quest_id: Optional[str] = None,
        mode: str = "normal",
        working_items: Optional[list[MemoryItem]] = None,
        retrieved_items: Optional[list[MemoryItem]] = None,
        max_total_tokens: Optional[int] = None,
    ) -> CompiledContext:
        """
        Compile a context for the given objective.

        Args:
            objective: The current objective/goal
            quest_id: Optional quest ID for scoping
            mode: Execution mode ('normal' or 'crusader')
            working_items: Pre-fetched working memory items
            retrieved_items: Pre-fetched retrieval results
            max_total_tokens: Override for max context tokens

        Returns:
            A CompiledContext with the assembled prompt
        """
        max_tokens = max_total_tokens or MAX_CONTEXT_TOKENS

        # Initialize context
        ctx = CompiledContext(
            objective=objective,
            quest_id=quest_id,
            mode=mode,
            compiler_version=self.VERSION,
            soul_version=self.soul_version,
        )

        sections: list[str] = []
        token_breakdown: dict[str, int] = {}
        total_tokens = 0

        # Step 1: Compile core blocks
        core_tokens, core_section = self._compile_core_blocks(ctx)
        if core_section:
            sections.append(core_section)
            token_breakdown["core_blocks"] = core_tokens
            total_tokens += core_tokens

        # Step 2: Add objective section
        objective_section = self._compile_objective(objective, quest_id, mode)
        objective_tokens = estimate_tokens(objective_section)
        sections.append(objective_section)
        token_breakdown["objective"] = objective_tokens
        total_tokens += objective_tokens

        # Step 3: Add working memory
        if working_items:
            remaining_budget = min(
                MAX_WORKING_MEMORY_TOKENS,
                max_tokens - total_tokens - MAX_RETRIEVAL_TOKENS
            )
            working_tokens, working_section = self._compile_working_memory(
                ctx, working_items, remaining_budget, quest_id
            )
            if working_section:
                sections.append(working_section)
                token_breakdown["working_memory"] = working_tokens
                total_tokens += working_tokens

        # Step 4: Add retrieved items
        if retrieved_items:
            remaining_budget = min(
                MAX_RETRIEVAL_TOKENS,
                max_tokens - total_tokens
            )
            retrieval_tokens, retrieval_section = self._compile_retrieval(
                ctx, retrieved_items, remaining_budget
            )
            if retrieval_section:
                sections.append(retrieval_section)
                token_breakdown["retrieval"] = retrieval_tokens
                total_tokens += retrieval_tokens

        # Assemble final prompt
        ctx.rendered_prompt = "\n\n".join(sections)
        ctx.token_estimate = total_tokens
        ctx.token_breakdown = token_breakdown

        logger.info(
            "Compiled context %s: %d tokens (core=%d, working=%d, retrieval=%d)",
            ctx.context_id,
            total_tokens,
            token_breakdown.get("core_blocks", 0),
            token_breakdown.get("working_memory", 0),
            token_breakdown.get("retrieval", 0),
        )

        return ctx

    def _compile_core_blocks(
        self,
        ctx: CompiledContext,
    ) -> tuple[int, str]:
        """
        Compile core blocks in canonical order.

        Returns:
            Tuple of (total_tokens, rendered_section)
        """
        blocks = self.core_store.get_all_blocks()
        lines: list[str] = []
        total_tokens = 0

        lines.append("=== CORE MEMORY ===")

        for block_type in CORE_BLOCK_ORDER:
            block = blocks.get(block_type.value)

            if block is None:
                continue

            # Skip empty blocks
            if not block.content.strip():
                continue

            # Skip quarantined blocks
            if block.status == MemoryStatus.quarantined:
                ctx.add_exclusion(
                    f"core:{block_type.value}",
                    "quarantined",
                    status=block.status.value,
                )
                continue

            # Check if within budget
            if not block.within_budget():
                ctx.add_exclusion(
                    f"core:{block_type.value}",
                    "exceeded_budget",
                    tokens=block.token_count,
                    budget=block.token_budget,
                )
                logger.warning(
                    "Core block %s exceeds budget: %d > %d",
                    block_type.value, block.token_count, block.token_budget
                )
                continue

            # Check total core budget
            if total_tokens + block.token_count > MAX_CORE_BLOCKS_TOTAL_TOKENS:
                ctx.add_exclusion(
                    f"core:{block_type.value}",
                    "total_budget_exceeded",
                    tokens=block.token_count,
                    current_total=total_tokens,
                    max_total=MAX_CORE_BLOCKS_TOTAL_TOKENS,
                )
                continue

            # Add block
            lines.append(f"\n[{block_type.value.upper()}]")
            lines.append(block.content)
            total_tokens += block.token_count
            ctx.included_blocks.append(block_type)

        if len(ctx.included_blocks) == 0:
            return 0, ""

        return total_tokens, "\n".join(lines)

    def _compile_objective(
        self,
        objective: str,
        quest_id: Optional[str],
        mode: str,
    ) -> str:
        """Compile the objective section."""
        lines = ["=== CURRENT OBJECTIVE ==="]
        lines.append(f"Objective: {objective}")

        if quest_id:
            lines.append(f"Quest: {quest_id}")

        if mode != "normal":
            lines.append(f"Mode: {mode.upper()}")

        return "\n".join(lines)

    def _compile_working_memory(
        self,
        ctx: CompiledContext,
        items: list[MemoryItem],
        budget: int,
        quest_id: Optional[str],
    ) -> tuple[int, str]:
        """
        Compile working memory items within budget.

        Items are prioritized by:
        1. Quest-scoped items (if quest_id provided)
        2. Global items
        3. Confidence score

        Returns:
            Tuple of (total_tokens, rendered_section)
        """
        if budget <= 0 or not items:
            return 0, ""

        # Filter and sort items
        valid_items = [
            item for item in items
            if item.status == MemoryStatus.active and not item.is_expired()
        ]

        # Sort: quest-scoped first, then by confidence
        def sort_key(item: MemoryItem) -> tuple[int, float]:
            is_quest = 1 if quest_id and item.namespace == f"quest:{quest_id}" else 0
            return (-is_quest, -item.confidence)

        valid_items.sort(key=sort_key)

        lines: list[str] = ["=== WORKING MEMORY ==="]
        total_tokens = 0

        for item in valid_items:
            ethics_decision = self.ethics.evaluate_retrieval(item)
            if ethics_decision.action == MemoryEthicsAction.quarantine:
                ctx.add_exclusion(
                    item.id,
                    "memory_ethics",
                    rule=ethics_decision.rule_name,
                    reason_detail=ethics_decision.reason,
                    action=ethics_decision.action.value,
                )
                continue
            if ethics_decision.action == MemoryEthicsAction.exclude:
                ctx.add_exclusion(
                    item.id,
                    "memory_ethics",
                    rule=ethics_decision.rule_name,
                    reason_detail=ethics_decision.reason,
                )
                continue

            rendered_content = ethics_decision.scrubbed_content or item.content
            injection = detect_injection(rendered_content)
            if injection.detected:
                ctx.add_exclusion(
                    item.id,
                    "injection_detected",
                    detection_reason=injection.reason,
                    matched=injection.matched,
                )
                continue

            item_tokens = estimate_tokens(rendered_content)

            if total_tokens + item_tokens > budget:
                ctx.add_exclusion(
                    item.id,
                    "exceeded_budget",
                    tokens=item_tokens,
                    remaining_budget=budget - total_tokens,
                )
                continue

            # Skip low confidence items
            if item.confidence < self.config.min_confidence_archival:
                ctx.add_exclusion(
                    item.id,
                    "low_confidence",
                    confidence=item.confidence,
                    threshold=self.config.min_confidence_archival,
                )
                continue

            lines.append(f"\n[{item.title}]")
            lines.append(rendered_content)
            if item.tags:
                lines.append(f"Tags: {', '.join(item.tags)}")

            total_tokens += item_tokens
            ctx.included_memory_item_ids.append(item.id)

        if len(ctx.included_memory_item_ids) == 0:
            return 0, ""

        return total_tokens, "\n".join(lines)

    def _compile_retrieval(
        self,
        ctx: CompiledContext,
        items: list[MemoryItem],
        budget: int,
    ) -> tuple[int, str]:
        """
        Compile retrieved items within budget.

        Items should already be ranked by relevance (from search).

        Returns:
            Tuple of (total_tokens, rendered_section)
        """
        if budget <= 0 or not items:
            return 0, ""

        lines: list[str] = ["=== RELEVANT MEMORIES ==="]
        total_tokens = 0
        included_count = 0

        for item in items:
            # Skip if already in working memory
            if item.id in ctx.included_memory_item_ids:
                continue

            if (item.metadata or {}).get("blob_type") == "full_source":
                ctx.add_exclusion(
                    item.id,
                    "blob_excluded",
                    blob_type="full_source",
                )
                continue

            ethics_decision = self.ethics.evaluate_retrieval(item)
            if ethics_decision.action == MemoryEthicsAction.quarantine:
                ctx.add_exclusion(
                    item.id,
                    "memory_ethics",
                    rule=ethics_decision.rule_name,
                    reason_detail=ethics_decision.reason,
                    action=ethics_decision.action.value,
                )
                continue
            if ethics_decision.action == MemoryEthicsAction.exclude:
                ctx.add_exclusion(
                    item.id,
                    "memory_ethics",
                    rule=ethics_decision.rule_name,
                    reason_detail=ethics_decision.reason,
                )
                continue

            rendered_content = ethics_decision.scrubbed_content or item.content
            injection = detect_injection(rendered_content)
            if injection.detected:
                ctx.add_exclusion(
                    item.id,
                    "injection_detected",
                    detection_reason=injection.reason,
                    matched=injection.matched,
                )
                continue

            # Skip quarantined
            if item.status == MemoryStatus.quarantined:
                ctx.add_exclusion(
                    item.id,
                    "quarantined",
                    status=item.status.value,
                )
                continue

            item_tokens = estimate_tokens(rendered_content)

            if total_tokens + item_tokens > budget:
                ctx.add_exclusion(
                    item.id,
                    "exceeded_budget",
                    tokens=item_tokens,
                    remaining_budget=budget - total_tokens,
                )
                continue

            # Skip low confidence
            if item.confidence < self.config.min_confidence_archival:
                ctx.add_exclusion(
                    item.id,
                    "low_confidence",
                    confidence=item.confidence,
                    threshold=self.config.min_confidence_archival,
                )
                continue

            tier_label = item.tier.value.upper()
            lines.append(f"\n[{tier_label}: {item.title}]")
            lines.append(rendered_content)

            total_tokens += item_tokens
            ctx.included_memory_item_ids.append(item.id)
            included_count += 1

        if included_count == 0:
            return 0, ""

        return total_tokens, "\n".join(lines)

    def create_receipt_data(self, ctx: CompiledContext) -> dict[str, Any]:
        """
        Create receipt data for the compiled context.

        Returns:
            Dictionary suitable for receipt creation
        """
        return {
            "context_id": ctx.context_id,
            "objective": ctx.objective,
            "quest_id": ctx.quest_id,
            "mode": ctx.mode,
            "included_blocks": [b.value for b in ctx.included_blocks],
            "included_memory_count": len(ctx.included_memory_item_ids),
            "excluded_count": len(ctx.excluded_candidates),
            "token_estimate": ctx.token_estimate,
            "token_breakdown": ctx.token_breakdown,
            "compiler_version": ctx.compiler_version,
            "soul_version": ctx.soul_version,
        }


class ContextCompilerService:
    """
    High-level service for context compilation with full integration.

    Combines CoreBlockStore, MemoryStoreManager, and ContextCompiler
    for a complete compilation workflow.
    """

    def __init__(
        self,
        data_dir: str | Path,
        config: Optional[MemoryConfig] = None,
        soul_version: Optional[str] = None,
        core_store: Optional[Any] = None,
        memory_manager: Optional[Any] = None,
    ):
        """
        Initialize the compiler service.

        Args:
            data_dir: Base data directory
            config: Memory configuration
            soul_version: Current Soul version
            core_store: Optional existing CoreBlockStore (avoids duplicate instances)
            memory_manager: Optional existing MemoryStoreManager (avoids duplicate instances)
        """
        self.data_dir = Path(data_dir)
        self.config = config or default_config

        if core_store is not None:
            self.core_store = core_store
        else:
            from .store import CoreBlockStore
            self.core_store = CoreBlockStore(data_dir=self.data_dir, config=self.config)
            self.core_store.initialize()

        if memory_manager is not None:
            self.memory_manager = memory_manager
        else:
            from .sqlite_store import MemoryStoreManager
            self.memory_manager = MemoryStoreManager(data_dir=self.data_dir)

        self.compiler = ContextCompiler(
            core_store=self.core_store,
            config=self.config,
            soul_version=soul_version,
        )

    def compile_for_objective(
        self,
        objective: str,
        quest_id: Optional[str] = None,
        mode: str = "normal",
        search_query: Optional[str] = None,
        retrieval_limit: int = 10,
        emit_receipt: bool = False,
        receipt_emitter: Optional[Any] = None,
    ) -> CompiledContext:
        """
        Compile a context for the given objective with automatic retrieval.

        Args:
            objective: The current objective
            quest_id: Optional quest ID for scoping
            mode: Execution mode
            search_query: Query for retrieval (defaults to objective)
            retrieval_limit: Max items to retrieve

        Returns:
            Compiled context ready for use
        """
        # Fetch working memory
        working_namespace = f"quest:{quest_id}" if quest_id else "global"
        working_items = self.memory_manager.working.list_items(
            namespace=working_namespace,
            status=MemoryStatus.active,
            limit=50,
        )
        # Also get global working items if quest-scoped
        if quest_id:
            global_working = self.memory_manager.working.list_items(
                namespace="global",
                status=MemoryStatus.active,
                limit=20,
            )
            working_items.extend(global_working)

        # Retrieve relevant items
        query = search_query or objective
        retrieved_items = self.memory_manager.search_all(
            query=query,
            tiers=[MemoryTier.episodic, MemoryTier.archival],
            limit=retrieval_limit,
            total_limit=retrieval_limit,
            current_quest_id=quest_id or "",
        )
        self._quarantine_injection_candidates([*working_items, *retrieved_items])

        # Compile
        ctx = self.compiler.compile(
            objective=objective,
            quest_id=quest_id,
            mode=mode,
            working_items=working_items,
            retrieved_items=retrieved_items,
        )
        if emit_receipt and receipt_emitter is not None:
            receipt_emitter.emit(
                action_type="memory_compile",
                action_name="compile_for_objective",
                inputs={
                    "objective": objective,
                    "quest_id": quest_id,
                    "mode": mode,
                    "search_query": query,
                },
                outputs={
                    "context_id": ctx.context_id,
                    "objective": objective,
                    "included_count": len(ctx.included_memory_item_ids),
                    "excluded_count": len(ctx.excluded_candidates),
                    "token_estimate": ctx.token_estimate,
                    "soul_version": ctx.soul_version,
                },
                token_count=ctx.token_estimate,
                quest_id=quest_id,
            )
        self._mark_included_items_retrieved(ctx.included_memory_item_ids)
        return ctx

    def _mark_included_items_retrieved(self, item_ids: list[str]) -> None:
        """Track retrieval of memories included in compiled context."""
        seen = set(item_ids)
        for tier in (MemoryTier.working, MemoryTier.episodic, MemoryTier.archival):
            store = self.memory_manager.get_store(tier)
            for item_id in seen:
                try:
                    store.mark_retrieved(item_id)
                except Exception as exc:  # pragma: no cover - telemetry failure should not block context compilation
                    logger.debug("Failed to mark memory %s as retrieved in %s: %s", item_id, tier.value, exc)

    def _quarantine_injection_candidates(self, items: list[MemoryItem]) -> None:
        """Persistently quarantine memory items that contain injection patterns."""
        seen: set[str] = set()
        for item in items:
            if item.id in seen:
                continue
            seen.add(item.id)
            injection = detect_injection(item.content)
            if not injection.detected:
                continue
            metadata = dict(item.metadata or {})
            metadata["flagged_reason"] = "injection_detected"
            metadata["injection_detection"] = {
                "reason": injection.reason,
                "matched": injection.matched,
            }
            item.metadata = metadata
            item.status = MemoryStatus.quarantined
            try:
                self.memory_manager.get_store(item.tier).update(item)
            except Exception as exc:  # pragma: no cover - compiler exclusion still protects prompt
                logger.warning("Failed to persist injection quarantine for memory %s: %s", item.id, exc)

    def record_active_objective(
        self,
        *,
        objective: str,
        quest_id: Optional[str],
        channel: str = "api",
        ttl_hours: int = 24,
    ) -> MemoryItem:
        """Persist the current objective into working memory as a TTL-backed task scratch item."""
        if ttl_hours <= 0:
            raise ValueError("ttl_hours must be positive")

        quest_key = (quest_id or "global").replace("-", "_")
        item_id = f"active_objective_{quest_key}"
        namespace = f"quest:{quest_id}" if quest_id else "global"
        now = datetime.utcnow()
        content = (
            f"Objective: {objective}\n"
            f"Quest ID: {quest_id or 'global'}\n"
            f"Channel: {channel}\n"
            f"Recorded At: {now.isoformat()}"
        )
        store = self.memory_manager.working
        existing = store.get(item_id)
        item = existing or MemoryItem(
            id=item_id,
            tier=MemoryTier.working,
            namespace=namespace,
            title="Active Objective",
            content="",
        )
        item.namespace = namespace
        item.title = "Active Objective"
        item.content = content
        item.tags = sorted({"active_objective", f"channel:{channel}"})
        item.confidence = 0.9
        item.status = MemoryStatus.active
        item.updated_at = now
        item.expires_at = now + timedelta(hours=ttl_hours)
        item.provenance = [
            Provenance(
                type=ProvenanceType.user_message,
                ref=quest_id or "global-objective",
                snippet=objective[:160],
                timestamp=now,
            )
        ]
        item.metadata = {
            "quest_id": quest_id or "",
            "channel": channel,
            "kind": "active_objective",
        }
        item.token_count = estimate_tokens(content)

        if existing:
            store.update(item)
        else:
            store.insert(item)
        return item

    def get_core_blocks_summary(self) -> dict[str, dict[str, Any]]:
        """
        Get a summary of all core blocks.

        Returns:
            Dictionary mapping block type to summary info
        """
        blocks = self.core_store.get_all_blocks()
        summary = {}

        for block_type, block in blocks.items():
            summary[block_type] = {
                "status": block.status.value,
                "token_count": block.token_count,
                "token_budget": block.token_budget,
                "utilization": f"{block.budget_utilization():.1%}",
                "updated_at": block.updated_at.isoformat(),
                "updated_by": block.updated_by,
                "version": block.version,
            }

        return summary
