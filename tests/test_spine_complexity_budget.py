"""Complexity budgets for governance-spine monolith reduction."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SPINE_LINE_BUDGETS = {
    "src/core/gateway.py": 700,
    "src/core/gateway_chat_runtime.py": 500,
    "src/core/boot.py": 1100,
    "src/core/orchestrator.py": 1000,
    "src/core/memory/sqlite_store.py": 950,
}


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def test_governance_spine_target_files_stay_within_line_budgets() -> None:
    over_budget = []
    for relative_path, max_lines in SPINE_LINE_BUDGETS.items():
        actual = _line_count(ROOT / relative_path)
        if actual > max_lines:
            over_budget.append(f"{relative_path}: {actual} > {max_lines}")

    assert not over_budget, "Spine monolith budget exceeded: " + "; ".join(over_budget)
