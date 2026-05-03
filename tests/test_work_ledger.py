from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

from work_ledger import WorkLedgerStore


def test_work_ledger_tracks_chat_run_lifecycle(tmp_path):
    store = WorkLedgerStore(str(tmp_path / "work.sqlite"))
    try:
        run = SimpleNamespace(
            run_id="run-1",
            request_id="req-1",
            status="queued",
            phase="queued",
            message_text="finish the ticket dashboard",
            message_preview="finish the ticket dashboard",
            session_id="sess-1",
            operator_id="op-1",
            channel="warroom",
            retry_of_run_id="",
            retry_count=0,
            last_progress_message="",
            error="",
            cancel_reason="",
        )

        item = store.upsert_from_chat_run(run, event_type="chat_run_queued")
        assert item.quest_id == "run-1"
        assert item.status == "active"
        assert item.phase == "queued"
        assert item.objective == "finish the ticket dashboard"

        run.status = "running"
        run.phase = "execution"
        run.last_progress_message = "Executing governed tool: repo_writer"
        updated = store.upsert_from_chat_run(run, event_type="chat_run_progress")

        assert updated.status == "active"
        assert updated.phase == "execution"
        assert updated.next_action == "Executing governed tool: repo_writer"
        assert store.list_work(session_id="sess-1")[0].quest_id == "run-1"
        assert store.list_work(session_id="other") == []

        run.status = "succeeded"
        run.phase = "completed"
        completed = store.upsert_from_chat_run(run, event_type="chat_run_completed")

        assert completed.status == "completed"
        assert store.list_work(session_id="sess-1") == []
        checkpoints = store.list_checkpoints("run-1")
        assert checkpoints
        assert checkpoints[0]["quest_id"] == "run-1"
    finally:
        store.close()


def test_work_ledger_checkpoint_captures_blockers_receipts_and_approvals(tmp_path):
    store = WorkLedgerStore(str(tmp_path / "work.sqlite"))
    try:
        store.upsert_work(
            quest_id="quest-1",
            objective="ship the hardened workflow",
            session_id="sess-1",
            operator_id="op-1",
            status="blocked",
            phase="approval",
            next_action="Wait for Commander approval",
            blocker="Approval required before repo write",
        )
        store.append_event(
            quest_id="quest-1",
            event_type="tool_blocked",
            summary="repo_writer requested approval",
            receipt_id="receipt-1",
            phase="approval",
            status="blocked",
            metadata={
                "path": "src/core/example.py",
                "approval_id": "approval-1",
            },
        )

        checkpoint = store.create_checkpoint("quest-1", reason="operator_pause")

        assert checkpoint is not None
        assert checkpoint["open_decisions"] == ["Approval required before repo write"]
        assert "Wait for Commander approval" in checkpoint["pending_work"]
        assert checkpoint["files_touched"] == ["src/core/example.py"]
        assert checkpoint["approvals"] == ["approval-1"]
        assert checkpoint["receipt_ids"] == ["receipt-1"]
    finally:
        store.close()


def test_work_ledger_context_block_prefers_current_quest_then_session(tmp_path):
    store = WorkLedgerStore(str(tmp_path / "work.sqlite"))
    try:
        store.upsert_work(
            quest_id="quest-1",
            objective="older active task",
            session_id="sess-1",
            phase="planning",
            next_action="Plan older task",
        )
        store.upsert_work(
            quest_id="quest-2",
            objective="current active task",
            session_id="sess-1",
            phase="execution",
            next_action="Run the next governed tool",
        )
        store.append_event(
            quest_id="quest-2",
            event_type="progress",
            summary="Prepared governed model request",
            phase="execution",
            status="active",
        )

        current_block = store.render_context_block(quest_id="quest-2", session_id="sess-1")
        session_block = store.render_context_block(session_id="sess-1")

        assert "Quest: quest-2" in current_block
        assert "Objective: current active task" in current_block
        assert "Run the next governed tool" in current_block
        assert "Prepared governed model request" in current_block
        assert "ACTIVE WORK STATE" in session_block
    finally:
        store.close()


def test_work_ledger_lists_and_renders_items_for_same_operator_across_sessions(tmp_path):
    store = WorkLedgerStore(str(tmp_path / "work.sqlite"))
    try:
        store.upsert_work(
            quest_id="quest-1",
            objective="work from prior browser session",
            session_id="old-session",
            operator_id="op-1",
            status="active",
            phase="execution",
            next_action="Continue the governed run",
        )

        assert store.list_work(session_id="new-session") == []
        operator_items = store.list_work(session_id="new-session", operator_id="op-1")
        assert [item.quest_id for item in operator_items] == ["quest-1"]

        block = store.render_context_block(session_id="new-session", operator_id="op-1")
        assert "work from prior browser session" in block
        assert "Continue the governed run" in block
    finally:
        store.close()


def test_work_ledger_checkpoint_dedupes_within_policy_window(tmp_path):
    store = WorkLedgerStore(str(tmp_path / "work.sqlite"))
    try:
        store.upsert_work(
            quest_id="quest-1",
            objective="long running work",
            session_id="sess-1",
            status="active",
            phase="execution",
            next_action="Continue execution",
        )

        first = store.create_checkpoint(
            "quest-1",
            reason="quiet_phase",
            dedupe_window_seconds=300,
        )
        second = store.create_checkpoint(
            "quest-1",
            reason="quiet_phase",
            dedupe_window_seconds=300,
        )

        assert first is not None
        assert second is not None
        assert second["checkpoint_id"] == first["checkpoint_id"]
        assert len(store.list_checkpoints("quest-1")) == 1
    finally:
        store.close()


def test_work_ledger_checkpoints_quiet_active_work(tmp_path):
    store = WorkLedgerStore(str(tmp_path / "work.sqlite"))
    try:
        item = store.upsert_work(
            quest_id="quest-1",
            objective="quiet long running work",
            session_id="sess-1",
            status="active",
            phase="provider_call",
            next_action="Waiting on governed provider response",
        )
        old_updated_at = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        with store._transaction() as conn:
            conn.execute(
                "UPDATE active_work_items SET updated_at = ? WHERE quest_id = ?",
                (old_updated_at, item.quest_id),
            )

        checkpoints = store.checkpoint_quiet_work(
            max_quiet_seconds=300,
            reason="quiet_phase",
            session_id="sess-1",
        )
        duplicate = store.checkpoint_quiet_work(
            max_quiet_seconds=300,
            reason="quiet_phase",
            session_id="sess-1",
        )

        assert len(checkpoints) == 1
        assert checkpoints[0]["reason"] == "quiet_phase"
        assert duplicate == []
        assert len(store.list_checkpoints("quest-1")) == 1
    finally:
        store.close()


def test_work_ledger_context_block_is_bounded(tmp_path):
    store = WorkLedgerStore(str(tmp_path / "work.sqlite"))
    try:
        long_text = "detail " * 2000
        store.upsert_work(
            quest_id="quest-1",
            objective=long_text,
            session_id="sess-1",
            status="active",
            phase="execution",
            next_action=long_text,
        )
        for index in range(40):
            store.append_event(
                quest_id="quest-1",
                event_type="progress",
                summary=f"{index}: {long_text}",
                phase="execution",
                status="active",
            )

        block = store.render_context_block(
            quest_id="quest-1",
            session_id="sess-1",
            max_items=1,
            max_events=5,
        )

        assert "ACTIVE WORK STATE" in block
        assert block.count("execution:") <= 5
        assert len(block) < 12000
    finally:
        store.close()


def test_work_ledger_supersedes_retry_source_and_hides_it_from_active_list(tmp_path):
    store = WorkLedgerStore(str(tmp_path / "work.sqlite"))
    try:
        store.upsert_work(
            quest_id="source-run",
            objective="blocked request",
            session_id="sess-1",
            status="blocked",
            phase="approval",
            next_action="Waiting for Commander approval",
            blocker="Approval required before repo write",
        )

        updated = store.mark_superseded_by_retry(
            "source-run",
            retry_run_id="retry-run",
            retry_status="succeeded",
        )
        second = store.mark_superseded_by_retry(
            "source-run",
            retry_run_id="retry-run",
            retry_status="succeeded",
        )

        assert updated is not None
        assert updated.status == "completed"
        assert updated.phase == "superseded"
        assert updated.blocker == ""
        assert updated.next_action == "Superseded by retry retry-run (succeeded)."
        assert updated.metadata["superseded_by_retry_run_id"] == "retry-run"
        assert second is not None
        assert second.status == "completed"
        assert store.list_work(session_id="sess-1") == []
        events = store.list_events("source-run")
        assert any(event.event_type == "work_superseded_by_retry" for event in events)
        assert store.list_checkpoints("source-run")[0]["reason"] == "work_superseded_by_retry"
    finally:
        store.close()


def test_work_ledger_archive_work_hides_item_without_deleting_history(tmp_path):
    store = WorkLedgerStore(str(tmp_path / "work.sqlite"))
    try:
        store.upsert_work(
            quest_id="quest-archive",
            objective="stale blocked request",
            session_id="sess-1",
            operator_id="op-1",
            status="blocked",
            phase="approval",
            next_action="Waiting for approval",
            blocker="Approval required",
        )

        archived = store.archive_work(
            "quest-archive",
            reason="Operator cleared stale work after retry succeeded.",
            archived_by_run_id="run-1",
            archived_by_operator_id="op-1",
            archived_by_session_id="sess-1",
        )

        assert archived is not None
        assert archived.status == "cancelled"
        assert archived.phase == "archived"
        assert archived.blocker == ""
        assert archived.next_action == ""
        assert archived.metadata["archived"] is True
        assert archived.metadata["archived_by_run_id"] == "run-1"
        assert store.list_work(session_id="sess-1") == []
        events = store.list_events("quest-archive")
        assert any(event.event_type == "work_archived_by_operator" for event in events)
        assert store.list_checkpoints("quest-archive")[0]["reason"] == "work_archived_by_operator"
    finally:
        store.close()


def test_work_ledger_session_brief_captures_complete_and_left(tmp_path):
    store = WorkLedgerStore(str(tmp_path / "work.sqlite"))
    try:
        store.upsert_work(
            quest_id="quest-complete",
            objective="finish continuity docs",
            session_id="sess-1",
            operator_id="op-1",
            status="completed",
            phase="completed",
            last_receipt_id="receipt-complete",
        )
        store.append_event(
            quest_id="quest-complete",
            event_type="file_updated",
            summary="Updated continuity docs",
            receipt_id="receipt-doc",
            phase="execution",
            status="completed",
            metadata={"path": "docs/continuity.md"},
        )
        store.create_checkpoint("quest-complete", reason="session_end")

        store.upsert_work(
            quest_id="quest-left",
            objective="finish memory retrieval ranking",
            session_id="sess-1",
            operator_id="op-1",
            status="blocked",
            phase="validation",
            next_action="Add retrieval ranking proof tests",
            blocker="Need approval before changing ranking defaults",
            metadata={"known_risks": ["Ranking defaults can change retrieval ordering"]},
        )
        store.append_event(
            quest_id="quest-left",
            event_type="approval_requested",
            summary="Ranking defaults need Commander approval",
            receipt_id="receipt-approval",
            phase="validation",
            status="blocked",
            metadata={
                "target_path": "src/core/memory/sqlite_store.py",
                "approval_request_id": "approval-1",
            },
        )
        store.create_checkpoint("quest-left", reason="session_end")

        brief = store.create_session_brief(
            session_id="sess-1",
            operator_id="op-1",
            reason="session_end",
        )
        stored = store.latest_session_brief(session_id="sess-1", operator_id="op-1")

        assert stored is not None
        assert stored.brief_id == brief.brief_id
        assert "quest-complete" in stored.quest_ids
        assert "quest-left" in stored.quest_ids
        assert "finish continuity docs" in stored.objective
        assert "finish memory retrieval ranking" in stored.objective
        assert any("quest-complete finished" in entry for entry in stored.completed_work)
        assert "Add retrieval ranking proof tests" in stored.pending_work
        assert stored.next_action == "Add retrieval ranking proof tests"
        assert "Need approval before changing ranking defaults" in stored.blockers
        assert "Need approval before changing ranking defaults" in stored.open_decisions
        assert "Ranking defaults can change retrieval ordering" in stored.known_risks
        assert "docs/continuity.md" in stored.files_touched
        assert "src/core/memory/sqlite_store.py" in stored.files_touched
        assert "approval-1" in stored.approvals
        assert "receipt-doc" in stored.receipt_ids
        assert "receipt-approval" in stored.receipt_ids
    finally:
        store.close()


def test_work_ledger_context_block_falls_back_to_latest_session_brief(tmp_path):
    store = WorkLedgerStore(str(tmp_path / "work.sqlite"))
    try:
        store.upsert_work(
            quest_id="quest-complete",
            objective="complete session work",
            session_id="sess-1",
            operator_id="op-1",
            status="completed",
            phase="completed",
        )
        store.append_event(
            quest_id="quest-complete",
            event_type="work_completed",
            summary="Completed the session scope",
            status="completed",
        )
        store.create_checkpoint("quest-complete", reason="session_end")
        store.create_session_brief(
            session_id="sess-1",
            operator_id="op-1",
            reason="session_end",
        )

        block = store.render_context_block(session_id="sess-1", operator_id="op-1")
        briefs = store.list_session_briefs(session_id="sess-1", operator_id="op-1")

        assert "LAST SESSION BRIEF" in block
        assert "Objective:" in block
        assert "Complete:" in block
        assert "quest-complete finished with status completed" in block
        assert len(briefs) == 1
        assert briefs[0].reason == "session_end"
    finally:
        store.close()
