"""EXEC_REQUEST routing tests for plan -> permission -> execute flow."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "agents"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "shared"))


class TestExecRequestRouting:
    """EXEC_REQUEST should produce a governed plan before execution."""

    def test_exec_request_builds_plan_artifact_for_permission_flow(self):
        """Executable requests must become PlanArtifacts before permission prompts."""
        from intent_classifier import IntentType
        from plan_types import OutcomeType
        from planning_pipeline import PlanningPipeline

        result = PlanningPipeline().process("Deploy the application now")

        assert result.intent == IntentType.EXEC_REQUEST
        assert result.outcome == OutcomeType.COMPLETED_WITH_PLAN_ARTIFACT
        assert result.artifact is not None
        assert result.artifact.plan_steps
        assert "planning" in result.state_trace
        assert "completed" in result.state_trace

    def test_exec_request_permission_prompt_no_tool_params(self):
        """Permission prompt should contain step descriptions, not tool params."""
        from response.assembler import ResponseAssembler

        assembler = ResponseAssembler()
        prompt = assembler.assemble_permission_request(
            what_i_will_do=[
                "Research voice communication options",
                "Select the most suitable platform",
                "Provide installation instructions",
            ],
            tools_enabled={"TOOL_CALL", "SKILL_CALL"},
            risk_tier="LOW",
            limits={"duration": 300, "actions": 10},
        )

        assert "Permission required" in prompt
        assert "Research voice communication" in prompt
        assert "(Tool:" not in prompt
        assert "Params:" not in prompt
        assert "model=" not in prompt
        assert "Approve or Deny?" in prompt

    def test_exec_request_fallback_uses_assembler(self):
        """When plan_compiler or task_store is missing, assembler should still clean output."""
        from response.assembler import ResponseAssembler

        assembler = ResponseAssembler()
        raw = (
            "## Goal\nSet up voice communication\n\n"
            "## Plan Steps\n1. Research\n2. Configure\n\n"
            "## Assumptions\n- User has internet\n\n"
            "## Risks\n- Platform may not support all devices\n\n"
            "## Next Action\n- Research options"
        )
        result = assembler.assemble(raw_planner_output=raw)

        assert "Goal" in result.chat_response or "Set up voice" in result.chat_response
        assert "Assumptions" not in result.chat_response
        assert "Risks" not in result.chat_response


class TestExecRequestWithCompiler:
    """EXEC_REQUEST with plan_compiler creates TaskGraph and requests permission."""

    def test_compile_plan_artifact_creates_graph(self):
        """PlanCompiler should produce a TaskGraph from a PlanArtifact."""
        from plan_types import PlanArtifact
        from tasking.compiler import PlanCompiler

        compiler = PlanCompiler()

        artifact = PlanArtifact(
            goal="Set up voice communication",
            context=["iPhone, iPad, PC", "Two users only"],
            assumptions=["Internet available"],
            plan_steps=[
                "Research voice platforms",
                "Select the best option",
                "Install on all devices",
            ],
            decision_points=["Which platform to use"],
            risks=[],
            done_when=["All devices connected"],
            next_action="Research platforms",
        )

        graph = compiler.compile_plan_artifact(artifact)
        assert graph is not None
        assert graph.goal == "Set up voice communication"
        assert len(graph.steps) == 3

    def test_request_permission_format(self):
        """Permission request should be user-friendly."""
        from response.assembler import ResponseAssembler

        assembler = ResponseAssembler()
        result = assembler.assemble_permission_request(
            what_i_will_do=["Step 1", "Step 2"],
            tools_enabled={"TOOL_CALL"},
            risk_tier="LOW",
            limits={"duration": 300, "actions": 4},
        )

        assert "Permission required" in result
        assert "Step 1" in result
        assert "Step 2" in result
        assert "LOW" in result
        assert "Approve or Deny?" in result
