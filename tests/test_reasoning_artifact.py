from src.core.reasoning_artifact import (
    GovernanceFeedback,
    ReasoningArtifact,
    TaskExperience,
)


def test_reasoning_artifact_formats_context_and_extracts_capability_gaps():
    text = (
        "Analyze the task.\n"
        "CAPABILITY GAP: Need desktop bridge access\n"
        "CAPABILITY GAP: x\n"
        "CAPABILITY GAP: Need receipt lookup"
    )
    gaps = ReasoningArtifact.parse_capability_gaps(text)
    artifact = ReasoningArtifact(
        reasoning_text=text,
        model_used="gpt-test",
        thinking_level="high",
        capability_gaps=gaps,
    )

    block = artifact.to_context_block()

    assert gaps == ["Need desktop bridge access", "Need receipt lookup"]
    assert block.startswith("--- DEEP REASONING ANALYSIS ---")
    assert "CAPABILITY GAPS IDENTIFIED" in block
    assert "  - Need receipt lookup" in block
    assert block.endswith("--- END DEEP REASONING ---")


def test_reasoning_artifact_formats_without_gaps():
    block = ReasoningArtifact(
        reasoning_text="No gaps.",
        model_used="gpt-test",
        thinking_level="low",
    ).to_context_block()

    assert "CAPABILITY GAPS IDENTIFIED" not in block
    assert "No gaps." in block


def test_task_experience_memory_content_includes_only_present_sections():
    experience = TaskExperience(
        task_summary="Clean repo",
        approach_taken="Audit then patch",
        tools_used=["read", "pytest"],
        tools_failed=["pytest"],
        actions_blocked=["git push"],
        outcome="fixed",
        reasoning_was_used=True,
        capability_gaps=["needs CI token"],
    )

    content = experience.to_memory_content()

    assert "Task: Clean repo" in content
    assert "Tools used: read, pytest" in content
    assert "Tools that failed: pytest" in content
    assert "Actions blocked by governance: git push" in content
    assert "Capability gaps: needs CI token" in content
    assert "Deep reasoning pass was used" in content


def test_task_experience_extracts_unique_tool_stats_from_receipts():
    stats = TaskExperience.from_tool_receipts([
        {"skill": "read", "result": "SUCCESS"},
        {"skill": "read", "result": "SUCCESS"},
        {"skill": "write", "result": "ESCALATED for approval"},
        {"skill": "deploy", "result": "FAILED"},
        {"skill": "notify", "result": "EXCEPTION"},
        {"skill": "repo", "result": "REJECTED"},
        {"result": "SUCCESS"},
    ])

    assert stats == {
        "tools_used": ["read", "write", "deploy", "notify", "repo", "unknown"],
        "tools_succeeded": ["read", "unknown"],
        "tools_failed": ["deploy", "notify", "repo"],
        "actions_blocked": ["write"],
    }


def test_governance_feedback_formats_minimal_and_full_results():
    minimal = GovernanceFeedback(
        skill_name="repo_writer",
        action_detail="write README",
        blocked_reason="approval required",
        permission_state="pending",
    ).to_tool_result()
    full = GovernanceFeedback(
        skill_name="command_runner",
        action_detail="restart service",
        blocked_reason="T3 action",
        permission_state="blocked",
        trust_record_summary="2 successes, 1 failure",
        alternatives=["read logs", "request approval"],
        resolution_hint="Open an ActionCard",
        request_id="approval-1",
    ).to_tool_result()

    assert "GOVERNANCE FEEDBACK for repo_writer" in minimal
    assert "Suggested alternatives" not in minimal
    assert "Trust record: 2 successes, 1 failure" in full
    assert "    - request approval" in full
    assert "How to resolve: Open an ActionCard" in full
    assert "Approval request ID: approval-1" in full
    assert "Do NOT repeat the blocked action" in full
