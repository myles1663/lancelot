#!/usr/bin/env python3
"""Run the public credibility checks used before a source release."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_RELEASE_EXCLUDE_FILE = ROOT / "scripts" / "public-release-exclude.txt"
SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".cjs"}
EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "node_modules",
    "tests",
    "test_data",
    "data",
    "lancelot_data",
}


def run(command: list[str], *, required: bool = True) -> int:
    resolved = list(command)
    executable = shutil.which(resolved[0])
    if executable is None and os.name == "nt":
        executable = shutil.which(f"{resolved[0]}.cmd") or shutil.which(f"{resolved[0]}.exe")
    if executable is None:
        print(f"Required executable not found: {resolved[0]}")
        if required:
            raise SystemExit(127)
        return 127
    resolved[0] = executable
    print(f"$ {' '.join(command)}", flush=True)
    completed = subprocess.run(resolved, cwd=ROOT)
    if required and completed.returncode != 0:
        raise SystemExit(completed.returncode)
    return completed.returncode


def git_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    )
    return [ROOT / line for line in completed.stdout.splitlines() if line.strip()]


def source_files() -> list[Path]:
    files = []
    for path in git_files():
        rel_parts = set(path.relative_to(ROOT).parts)
        if rel_parts & EXCLUDED_PARTS:
            continue
        if path.suffix in SOURCE_SUFFIXES:
            files.append(path)
    return files


def check_no_local_only_files() -> None:
    tracked = {str(path.relative_to(ROOT)).replace("\\", "/") for path in git_files()}
    forbidden = {
        "AGENTS.md",
        "CLAUDE.md",
        "scripts/prepare-public-release.ps1",
        "scripts/public-release-exclude.txt",
        "scripts/seed-demo.py",
        "artifacts/",
        "docs/AGENTS.md",
        "docs/CLAUDE.md",
        "docs/internal/",
        "docs/archive/",
        "docs/specs/",
        "docs/blueprints/",
    }
    violations = sorted(
        path
        for path in tracked
        if path in forbidden or any(path.startswith(prefix) for prefix in forbidden if prefix.endswith("/"))
    )
    if violations:
        print("Tracked local/internal release artifacts:")
        for path in violations:
            print(f"  {path}")
        raise SystemExit(1)


def check_line_counts(max_lines: int) -> None:
    offenders: list[tuple[int, str]] = []
    for path in source_files():
        line_count = sum(1 for _ in path.open("r", encoding="utf-8", errors="ignore"))
        if line_count > max_lines:
            offenders.append((line_count, str(path.relative_to(ROOT)).replace("\\", "/")))
    if offenders:
        print(f"Tracked source files over {max_lines} lines:")
        for line_count, path in sorted(offenders, reverse=True):
            print(f"  {line_count:5d}  {path}")
        raise SystemExit(1)


def check_no_print_statements() -> None:
    offenders: list[str] = []
    for path in (ROOT / "src").rglob("*.py"):
        if any(part in EXCLUDED_PARTS for part in path.relative_to(ROOT).parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "print(" in text:
            offenders.append(str(path.relative_to(ROOT)).replace("\\", "/"))
    if offenders:
        print("Production Python files contain print():")
        for path in offenders:
            print(f"  {path}")
        raise SystemExit(1)


def ensure_env_for_compose() -> bool:
    env_path = ROOT / ".env"
    if env_path.exists():
        return False
    example = ROOT / ".env.example"
    if not example.exists():
        return False
    env_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-pytest", action="store_true")
    parser.add_argument("--skip-coverage", action="store_true")
    parser.add_argument("--skip-uab", action="store_true")
    parser.add_argument("--skip-docker", action="store_true")
    parser.add_argument(
        "--public-artifact",
        action="store_true",
        help="enforce checks for a prepared public release tree",
    )
    parser.add_argument("--max-source-lines", type=int, default=1500)
    args = parser.parse_args()

    if args.public_artifact or not PUBLIC_RELEASE_EXCLUDE_FILE.exists():
        check_no_local_only_files()
    else:
        print("Dev checkout detected; public-only artifact exclusions are checked after release prep.")
    check_line_counts(args.max_source_lines)
    check_no_print_statements()

    if not args.skip_pytest:
        pytest_command = [sys.executable, "-m", "pytest", "-q"]
        if not args.skip_coverage:
            pytest_command.extend([
                "--cov=src",
                "--cov-report=term",
                "--cov-report=xml:coverage.xml",
            ])
        run(pytest_command)
    if not args.skip_uab:
        run(["npm", "--prefix", "packages/uab", "test"])
    if not args.skip_docker:
        created_env = ensure_env_for_compose()
        try:
            run(["docker", "compose", "config", "--quiet"])
        finally:
            if created_env:
                try:
                    os.remove(ROOT / ".env")
                except OSError:
                    pass

    print("Public release verification completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
