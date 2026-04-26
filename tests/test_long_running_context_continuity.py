from datetime import datetime, timedelta, timezone

import src.core.context_env as context_env_module
from src.core.context_env import ContextEnvironment
from src.core.memory.compiler import ContextCompilerService
from src.core.memory.jobs import MemoryJobExecutor
from src.core.memory.schemas import MemoryItem, MemoryStatus, MemoryTier, Provenance, ProvenanceType
from src.core.work_ledger import WorkLedgerStore


def test_long_running_context_continuity_smoke(monkeypatch, tmp_data_dir):
    monkeypatch.setattr(context_env_module, "MAX_CHAT_HISTORY_MESSAGES", 8)
    monkeypatch.setattr(context_env_module, "CHAT_HISTORY_RECENT_KEEP", 4)
    monkeypatch.setattr(context_env_module, "CHAT_HISTORY_COMPACT_BATCH", 4)

    context_env = ContextEnvironment(str(tmp_data_dir))
    for index in range(12):
        if index % 2 == 0:
            context_env.add_history("user", f"[via warroom] Continue dashboard build step {index}")
        else:
            context_env.add_history("assistant", f"Finished dashboard build step {index}")

    chat_context = context_env.get_history_string(channel="warroom")
    assert "--- COMPACTED CHAT HISTORY ---" in chat_context
    assert "--- RECENT CHAT HISTORY ---" in chat_context
    assert "Continue dashboard build step 0" in chat_context
    assert "Finished dashboard build step 11" in chat_context

    ledger = WorkLedgerStore(str(tmp_data_dir / "work" / "work.sqlite"))
    try:
        ledger.upsert_work(
            quest_id="quest-dashboard",
            objective="Finish the local ticket dashboard and scheduler job",
            session_id="sess-warroom",
            channel="warroom",
            status="active",
            phase="execution",
            current_step="Wire scheduler visibility",
            next_action="Run the next governed smoke test",
        )
        ledger.append_event(
            quest_id="quest-dashboard",
            event_type="tool_completed",
            summary="Dashboard route was verified",
            receipt_id="receipt-dashboard-1",
            phase="execution",
            status="completed",
            metadata={"path": "local/ticket_sentinel/dashboard.py"},
        )
        old_updated_at = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        with ledger._transaction() as conn:
            conn.execute(
                "UPDATE active_work_items SET updated_at = ? WHERE quest_id = ?",
                (old_updated_at, "quest-dashboard"),
            )

        checkpoints = ledger.checkpoint_quiet_work(
            max_quiet_seconds=300,
            reason="long_running_smoke",
            session_id="sess-warroom",
        )
        work_context = ledger.render_context_block(
            quest_id="quest-dashboard",
            session_id="sess-warroom",
        )

        assert len(checkpoints) == 1
        assert "ACTIVE WORK STATE" in work_context
        assert "Run the next governed smoke test" in work_context
        assert "receipt-dashboard-1" in work_context
    finally:
        ledger.close()

    compiler_service = ContextCompilerService(data_dir=tmp_data_dir)
    compiler_service.record_active_objective(
        objective="Finish the local ticket dashboard and scheduler job",
        quest_id="quest-dashboard",
        channel="warroom",
        ttl_hours=6,
    )

    episodic = compiler_service.memory_manager.episodic
    for index in range(6):
        episodic.insert(
            MemoryItem(
                id=f"episode-{index}",
                tier=MemoryTier.episodic,
                namespace="quest:quest-dashboard",
                title=f"Dashboard event {index}",
                content=f"Ticket dashboard scheduler verification detail {index}",
                tags=["ticket-dashboard", "verification"],
                confidence=0.8,
                status=MemoryStatus.active,
                provenance=[
                    Provenance(
                        type=ProvenanceType.receipt,
                        ref=f"receipt-dashboard-{index}",
                    )
                ],
            )
        )

    executor = MemoryJobExecutor(
        core_store=compiler_service.core_store,
        store_manager=compiler_service.memory_manager,
        data_dir=tmp_data_dir,
    )
    summary_result = executor.run_episodic_summarization(
        items_per_batch=6,
        min_items_for_summary=5,
    )
    archival_items = compiler_service.memory_manager.archival.list_items()
    compiled = compiler_service.compile_for_objective(
        objective="Continue dashboard scheduler verification",
        quest_id="quest-dashboard",
        search_query="dashboard scheduler verification",
    )

    assert summary_result.success is True
    assert summary_result.details["summaries_created"] == 1
    assert archival_items[0].metadata["promotion_decision"]["allowed"] is True
    assert "WORKING MEMORY" in compiled.rendered_prompt
    assert "Active Objective" in compiled.rendered_prompt
    assert "RELEVANT MEMORIES" in compiled.rendered_prompt

    compiler_service.memory_manager.close_all()
