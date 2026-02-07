"""
Tests for plan_renderer — PlanArtifact → Markdown rendering.
"""

import os
import sys
import pytest

# Ensure src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))

from plan_renderer import render_plan_artifact_markdown
from plan_types import PlanArtifact, RiskItem


def _make_artifact() -> PlanArtifact:
    """Helper: returns a fully populated PlanArtifact."""
    return PlanArtifact(
        goal="Migrate database to PostgreSQL",
        context=["Current DB is SQLite", "Production traffic is low"],
        assumptions=["Downtime window of 2 hours is acceptable"],
        plan_steps=[
            "Export SQLite data to CSV",
            "Create PostgreSQL schema",
            "Import CSV into PostgreSQL",
            "Update connection strings",
        ],
        decision_points=["Choose managed vs self-hosted PostgreSQL"],
        risks=[RiskItem(risk="Data loss during migration", mitigation="Run parallel databases for 48h")],
        done_when=["PostgreSQL is primary database and all queries work"],
        next_action="Export current SQLite schema for review",
    )


# =========================================================================
# Section Presence
# =========================================================================


class TestSectionPresence:
    def test_contains_goal_section(self):
        md = render_plan_artifact_markdown(_make_artifact())
        assert "## Goal" in md

    def test_contains_context_section(self):
        md = render_plan_artifact_markdown(_make_artifact())
        assert "## Context" in md

    def test_contains_assumptions_section(self):
        md = render_plan_artifact_markdown(_make_artifact())
        assert "## Assumptions" in md

    def test_contains_plan_steps_section(self):
        md = render_plan_artifact_markdown(_make_artifact())
        assert "## Plan Steps" in md

    def test_contains_decision_points_section(self):
        md = render_plan_artifact_markdown(_make_artifact())
        assert "## Decision Points" in md

    def test_contains_risks_section(self):
        md = render_plan_artifact_markdown(_make_artifact())
        assert "## Risks" in md

    def test_contains_done_when_section(self):
        md = render_plan_artifact_markdown(_make_artifact())
        assert "## Done When" in md

    def test_contains_next_action_section(self):
        md = render_plan_artifact_markdown(_make_artifact())
        assert "## Next Action" in md


# =========================================================================
# Content Rendering
# =========================================================================


class TestContentRendering:
    def test_goal_text_present(self):
        md = render_plan_artifact_markdown(_make_artifact())
        assert "Migrate database to PostgreSQL" in md

    def test_context_items_as_bullets(self):
        md = render_plan_artifact_markdown(_make_artifact())
        assert "- Current DB is SQLite" in md
        assert "- Production traffic is low" in md

    def test_plan_steps_numbered(self):
        md = render_plan_artifact_markdown(_make_artifact())
        assert "1. Export SQLite data to CSV" in md
        assert "2. Create PostgreSQL schema" in md
        assert "3. Import CSV into PostgreSQL" in md
        assert "4. Update connection strings" in md

    def test_risks_with_mitigation(self):
        md = render_plan_artifact_markdown(_make_artifact())
        assert "**Risk:** Data loss during migration" in md
        assert "**Mitigation:** Run parallel databases for 48h" in md

    def test_next_action_text(self):
        md = render_plan_artifact_markdown(_make_artifact())
        assert "Export current SQLite schema for review" in md


# =========================================================================
# DRAFT: Behavior
# =========================================================================


class TestDraftBehavior:
    def test_no_draft_by_default(self):
        md = render_plan_artifact_markdown(_make_artifact())
        assert "DRAFT:" not in md
        assert "DRAFT" not in md.split("\n")[0]

    def test_draft_prefix_when_requested(self):
        md = render_plan_artifact_markdown(_make_artifact(), draft=True)
        assert "**DRAFT:**" in md

    def test_draft_false_explicit(self):
        md = render_plan_artifact_markdown(_make_artifact(), draft=False)
        assert "DRAFT:" not in md


# =========================================================================
# Optional Sections
# =========================================================================


class TestOptionalSections:
    def test_no_mvp_path_by_default(self):
        md = render_plan_artifact_markdown(_make_artifact())
        assert "## MVP Path" not in md

    def test_mvp_path_when_present(self):
        artifact = _make_artifact()
        artifact.mvp_path = "Start with read-only migration, add writes later"
        md = render_plan_artifact_markdown(artifact)
        assert "## MVP Path" in md
        assert "read-only migration" in md

    def test_test_plan_when_present(self):
        artifact = _make_artifact()
        artifact.test_plan = "Run integration tests on staging first"
        md = render_plan_artifact_markdown(artifact)
        assert "## Test Plan" in md

    def test_estimate_when_present(self):
        artifact = _make_artifact()
        artifact.estimate = "Roughly 4-6 hours"
        md = render_plan_artifact_markdown(artifact)
        assert "## Estimate" in md

    def test_references_when_present(self):
        artifact = _make_artifact()
        artifact.references = ["PostgreSQL docs", "SQLite export guide"]
        md = render_plan_artifact_markdown(artifact)
        assert "## References" in md
        assert "- PostgreSQL docs" in md


# =========================================================================
# Leakage Prevention
# =========================================================================


class TestLeakagePrevention:
    def test_clean_output_passes(self):
        # Should not raise
        md = render_plan_artifact_markdown(_make_artifact())
        assert len(md) > 0

    def test_leakage_in_goal_raises(self):
        artifact = _make_artifact()
        artifact.goal = "DRAFT: Migrate database"
        with pytest.raises(ValueError, match="leakage"):
            render_plan_artifact_markdown(artifact, draft=False)

    def test_leakage_in_context_raises(self):
        artifact = _make_artifact()
        artifact.context = ["PLANNER: internal note"]
        with pytest.raises(ValueError, match="leakage"):
            render_plan_artifact_markdown(artifact, draft=False)

    def test_leakage_in_steps_raises(self):
        artifact = _make_artifact()
        artifact.plan_steps = ["[INTERNAL] step 1", "step 2", "step 3"]
        with pytest.raises(ValueError, match="leakage"):
            render_plan_artifact_markdown(artifact, draft=False)

    def test_leakage_allowed_in_draft_mode(self):
        artifact = _make_artifact()
        # draft=True bypasses leakage check (the DRAFT: prefix is intentional)
        md = render_plan_artifact_markdown(artifact, draft=True)
        assert "DRAFT:" in md


# =========================================================================
# Formatting Stability
# =========================================================================


class TestFormattingStability:
    def test_deterministic_output(self):
        """Same input produces same output."""
        artifact = _make_artifact()
        md1 = render_plan_artifact_markdown(artifact)
        md2 = render_plan_artifact_markdown(artifact)
        assert md1 == md2

    def test_ends_with_newline(self):
        md = render_plan_artifact_markdown(_make_artifact())
        assert md.endswith("\n")

    def test_no_trailing_whitespace_lines(self):
        md = render_plan_artifact_markdown(_make_artifact())
        for line in md.split("\n"):
            assert line == line.rstrip(), f"Trailing whitespace in: {line!r}"
