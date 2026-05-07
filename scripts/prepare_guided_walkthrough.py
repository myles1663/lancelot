#!/usr/bin/env python3
"""Prepare safe local files for the governed execution walkthrough."""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path


REQUEST_TEXT = (
    "Use repo_writer exactly once. workspace: /home/lancelot/workspace. "
    "action: delete. path: guided-walkthrough/t3-delete-candidate.txt. "
    "Do not use command_runner or service_runner."
)


README_TEXT = """# Guided Walkthrough Workspace

This directory is intentionally safe to modify during the guided walkthrough.
It contains one disposable file:

- t3-delete-candidate.txt

The walkthrough asks Lancelot to delete that file through the governed runtime.
Do not point the walkthrough at repository files, secrets, or personal documents.
"""


DEMO_FILE_TEXT = """Lancelot guided walkthrough disposable file.

This file exists so a reviewer can watch a governed destructive action move
through classification, approval, execution, and receipt review. It is safe to
delete.
"""


def default_workspace() -> Path:
    configured = (
        os.getenv("LANCELOT_WORKSPACE")
        or os.getenv("LANCELOT_WORKSPACE_MOUNT")
        or "lancelot_workspace"
    )
    return Path(configured).expanduser()


def resolve_workspace(path: str | None = None) -> Path:
    workspace = Path(path).expanduser() if path else default_workspace()
    return workspace.resolve()


def ensure_inside(parent: Path, child: Path) -> None:
    parent = parent.resolve()
    child = child.resolve()
    try:
        child.relative_to(parent)
    except ValueError as exc:
        raise ValueError(f"Refusing to write outside workspace: {child}") from exc


def prepare_walkthrough(workspace: Path, *, reset: bool = True) -> dict[str, Path]:
    workspace = workspace.resolve()
    target_dir = workspace / "guided-walkthrough"
    ensure_inside(workspace, target_dir)

    if reset and target_dir.exists():
        shutil.rmtree(target_dir)

    candidate = target_dir / "t3-delete-candidate.txt"
    request = target_dir / "walkthrough-request.txt"
    readme = target_dir / "README.md"

    for path in (candidate, request, readme):
        ensure_inside(workspace, path)

    target_dir.mkdir(parents=True, exist_ok=True)
    candidate.write_text(DEMO_FILE_TEXT, encoding="utf-8")
    request.write_text(REQUEST_TEXT + "\n", encoding="utf-8")
    readme.write_text(README_TEXT, encoding="utf-8")

    return {
        "workspace": workspace,
        "directory": target_dir,
        "candidate": candidate,
        "request": request,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        help=(
            "Workspace path to prepare. Defaults to LANCELOT_WORKSPACE, "
            "LANCELOT_WORKSPACE_MOUNT, or ./lancelot_workspace."
        ),
    )
    parser.add_argument(
        "--no-reset",
        action="store_true",
        help="Do not remove an existing guided-walkthrough directory first.",
    )
    args = parser.parse_args()

    paths = prepare_walkthrough(
        resolve_workspace(args.workspace),
        reset=not args.no_reset,
    )
    print("Guided walkthrough workspace prepared.")
    print(f"Directory: {paths['directory']}")
    print(f"Candidate: {paths['candidate']}")
    print(f"Request:   {paths['request']}")
    print()
    print(REQUEST_TEXT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
