"""
Plan Renderer — PlanArtifact → User-Facing Markdown.
=====================================================

Renders a PlanArtifact into clean, user-facing markdown.
Runs leakage detection on the final output to ensure no
planner-internal markers leak through.

Rules:
    - Clean markdown headings for each section.
    - Never prefix with "DRAFT:" unless explicit flag is True.
    - Run assert_no_planner_leakage on final output.

Public API:
    render_plan_artifact_markdown(artifact, draft=False) -> str
"""

from __future__ import annotations

from plan_types import PlanArtifact, assert_no_planner_leakage


def render_plan_artifact_markdown(
    artifact: PlanArtifact,
    draft: bool = False,
) -> str:
    """
    Render a PlanArtifact to clean user-facing markdown.

    Args:
        artifact: The PlanArtifact to render.
        draft: If True, prefix with "DRAFT: ". Defaults to False.

    Returns:
        Markdown string suitable for user display.

    Raises:
        ValueError: If planner leakage is detected in the output.
    """
    sections: list[str] = []

    # Optional DRAFT prefix
    if draft:
        sections.append("**DRAFT:**\n")

    # ── Goal ──────────────────────────────────────────────────────────
    sections.append(f"## Goal\n\n{artifact.goal}")

    # ── Context ───────────────────────────────────────────────────────
    if artifact.context:
        items = "\n".join(f"- {c}" for c in artifact.context)
        sections.append(f"## Context\n\n{items}")

    # ── Assumptions ───────────────────────────────────────────────────
    if artifact.assumptions:
        items = "\n".join(f"- {a}" for a in artifact.assumptions)
        sections.append(f"## Assumptions\n\n{items}")

    # ── Plan Steps ────────────────────────────────────────────────────
    if artifact.plan_steps:
        items = "\n".join(
            f"{i}. {step}" for i, step in enumerate(artifact.plan_steps, 1)
        )
        sections.append(f"## Plan Steps\n\n{items}")

    # ── Decision Points ───────────────────────────────────────────────
    if artifact.decision_points:
        items = "\n".join(f"- {d}" for d in artifact.decision_points)
        sections.append(f"## Decision Points\n\n{items}")

    # ── Risks ─────────────────────────────────────────────────────────
    if artifact.risks:
        items = "\n".join(
            f"- **Risk:** {r.risk}\n  **Mitigation:** {r.mitigation}"
            for r in artifact.risks
        )
        sections.append(f"## Risks\n\n{items}")

    # ── Done When ─────────────────────────────────────────────────────
    if artifact.done_when:
        items = "\n".join(f"- {d}" for d in artifact.done_when)
        sections.append(f"## Done When\n\n{items}")

    # ── Next Action ───────────────────────────────────────────────────
    if artifact.next_action:
        sections.append(f"## Next Action\n\n{artifact.next_action}")

    # ── Optional Sections ─────────────────────────────────────────────
    if artifact.mvp_path:
        sections.append(f"## MVP Path\n\n{artifact.mvp_path}")

    if artifact.test_plan:
        sections.append(f"## Test Plan\n\n{artifact.test_plan}")

    if artifact.estimate:
        sections.append(f"## Estimate\n\n{artifact.estimate}")

    if artifact.references:
        items = "\n".join(f"- {r}" for r in artifact.references)
        sections.append(f"## References\n\n{items}")

    # ── Assemble ──────────────────────────────────────────────────────
    output = "\n\n".join(sections) + "\n"

    # ── Leakage Gate ──────────────────────────────────────────────────
    if not draft:
        leaks = assert_no_planner_leakage(output)
        if leaks:
            raise ValueError(
                f"Planner leakage detected in rendered output: {leaks}"
            )

    return output
