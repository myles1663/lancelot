"""
Tests for Response Assembler + Output Hygiene (Fix Pack V1 PR1).
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.response.assembler import ResponseAssembler, AssembledResponse
from src.core.response.policies import OutputPolicy
from src.core.response.war_room_artifact import ArtifactType, WarRoomArtifact


# =========================================================================
# OutputPolicy Tests
# =========================================================================


class TestOutputPolicy:
    def test_verbose_sections_route_to_war_room(self):
        for section in OutputPolicy.VERBOSE_SECTIONS:
            assert OutputPolicy.should_route_to_war_room(section) is True

    def test_non_verbose_sections_stay_in_chat(self):
        assert OutputPolicy.should_route_to_war_room("goal") is False
        assert OutputPolicy.should_route_to_war_room("plan_steps") is False
        assert OutputPolicy.should_route_to_war_room("next_action") is False

    def test_enforce_chat_limits_short_text(self):
        text = "Short text\nTwo lines"
        assert OutputPolicy.enforce_chat_limits(text) == text

    def test_enforce_chat_limits_long_text(self):
        lines = [f"Line {i}" for i in range(50)]
        text = "\n".join(lines)
        result = OutputPolicy.enforce_chat_limits(text)
        result_lines = result.split("\n")
        # Should be MAX_CHAT_LINES + overflow indicator
        assert len(result_lines) <= OutputPolicy.MAX_CHAT_LINES + 2

    def test_extract_verbose_sections(self):
        md = """## Goal
My goal here.

## Assumptions
- Assumption 1
- Assumption 2

## Plan Steps
1. Step 1
2. Step 2

## Risks
- Risk 1

## Next Action
Do the thing."""
        chat, verbose = OutputPolicy.extract_verbose_sections(md)
        assert "Goal" in chat
        assert "Plan Steps" in chat
        assert "Next Action" in chat
        assert "Assumptions" in verbose
        assert "Risks" in verbose

    def test_trim_plan_summary_short(self):
        steps = ["Step 1", "Step 2", "Step 3"]
        assert OutputPolicy.trim_plan_summary(steps) == steps

    def test_trim_plan_summary_long(self):
        steps = [f"Step {i}" for i in range(30)]
        result = OutputPolicy.trim_plan_summary(steps)
        assert len(result) == OutputPolicy.MAX_PLAN_SUMMARY_LINES + 1
        assert "more steps" in result[-1]

    def test_trim_next_actions(self):
        actions = [f"Action {i}" for i in range(10)]
        result = OutputPolicy.trim_next_actions(actions)
        assert len(result) == OutputPolicy.MAX_NEXT_ACTIONS


# =========================================================================
# WarRoomArtifact Tests
# =========================================================================


class TestWarRoomArtifact:
    def test_creation(self):
        art = WarRoomArtifact(
            type=ArtifactType.ASSUMPTIONS.value,
            content={"assumptions": ["A1"]},
            session_id="test-session",
        )
        assert art.type == "ASSUMPTIONS"
        assert art.session_id == "test-session"
        assert art.id  # UUID generated

    def test_to_dict_roundtrip(self):
        art = WarRoomArtifact(
            type=ArtifactType.RISKS.value,
            content={"risks": [{"risk": "R1", "mitigation": "M1"}]},
            session_id="s1",
        )
        d = art.to_dict()
        art2 = WarRoomArtifact.from_dict(d)
        assert art2.type == art.type
        assert art2.content == art.content
        assert art2.session_id == art.session_id


# =========================================================================
# ResponseAssembler Tests
# =========================================================================


class TestResponseAssembler:
    def test_assemble_empty(self):
        asm = ResponseAssembler(session_id="test")
        result = asm.assemble()
        assert isinstance(result, AssembledResponse)
        assert result.chat_response == ""
        assert result.war_room_artifacts == []

    def test_assemble_from_markdown(self):
        asm = ResponseAssembler(session_id="test")
        md = """## Goal
Do the thing.

## Plan Steps
1. First step
2. Second step

## Assumptions
- We have access to the API

## Risks
- The API might be down"""
        result = asm.assemble(raw_planner_output=md)
        # Chat should not contain Assumptions or Risks
        assert "Assumptions" not in result.chat_response
        assert "Risks" not in result.chat_response
        # War Room should have verbose content
        assert len(result.war_room_artifacts) >= 1

    def test_assemble_from_plan_artifact(self):
        from plan_types import PlanArtifact, RiskItem
        artifact = PlanArtifact(
            goal="Migrate the database",
            context=["Production PostgreSQL 14"],
            assumptions=["Backup exists"],
            plan_steps=["Backup database", "Run migration", "Verify data"],
            decision_points=["Rollback threshold"],
            risks=[RiskItem(risk="Data loss", mitigation="Backup first")],
            done_when=["All tables migrated"],
            next_action="Create backup",
        )
        asm = ResponseAssembler(session_id="test")
        result = asm.assemble(plan_artifact=artifact)
        assert "Migrate the database" in result.chat_response
        assert "Backup database" in result.chat_response
        assert "Create backup" in result.chat_response
        # Assumptions routed to War Room
        war_types = [a.type for a in result.war_room_artifacts]
        assert ArtifactType.ASSUMPTIONS.value in war_types
        assert ArtifactType.RISKS.value in war_types
        assert ArtifactType.DECISION_POINTS.value in war_types

    def test_assembler_never_leaks_assumptions_to_chat(self):
        from plan_types import PlanArtifact, RiskItem
        artifact = PlanArtifact(
            goal="Test goal",
            context=["ctx"],
            assumptions=["SECRET_ASSUMPTION_XYZ"],
            plan_steps=["Step 1", "Step 2", "Step 3"],
            decision_points=["INTERNAL_DECISION_ABC"],
            risks=[RiskItem(risk="INTERNAL_RISK_DEF", mitigation="mit")],
            done_when=["done"],
            next_action="next",
        )
        asm = ResponseAssembler(session_id="test")
        result = asm.assemble(plan_artifact=artifact)
        assert "SECRET_ASSUMPTION_XYZ" not in result.chat_response
        assert "INTERNAL_DECISION_ABC" not in result.chat_response
        assert "INTERNAL_RISK_DEF" not in result.chat_response

    def test_chat_stays_under_max_lines(self):
        from plan_types import PlanArtifact, RiskItem
        artifact = PlanArtifact(
            goal="Big goal",
            context=["ctx"],
            assumptions=["a"],
            plan_steps=[f"Step {i}: Do something important" for i in range(30)],
            decision_points=["d"],
            risks=[RiskItem(risk="r", mitigation="m")],
            done_when=["done"],
            next_action="next",
        )
        asm = ResponseAssembler(session_id="test")
        result = asm.assemble(plan_artifact=artifact)
        lines = result.chat_response.split("\n")
        assert len(lines) <= OutputPolicy.MAX_CHAT_LINES + 2

    def test_permission_request_format(self):
        asm = ResponseAssembler(session_id="test")
        result = asm.assemble_permission_request(
            what_i_will_do=["Edit config file", "Run tests"],
            tools_enabled={"FILE_EDIT", "COMMAND"},
            risk_tier="MED",
            limits={"duration": 300, "actions": 10},
        )
        assert "Permission required" in result
        assert "What I will do" in result
        assert "Edit config file" in result
        assert "MED" in result
        assert "Approve or Deny" in result
