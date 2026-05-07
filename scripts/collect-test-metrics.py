#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def run_command(command: list[str], cwd: pathlib.Path) -> str:
    print(f"$ {' '.join(command)}", flush=True)
    executable_command = resolve_command(command)
    completed = subprocess.run(
        executable_command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {completed.returncode}: {' '.join(command)}")
    return completed.stdout


def resolve_command(command: list[str]) -> list[str]:
    if os.name != "nt" or not command:
        return command
    if command[0] not in {"npm", "npx"}:
        return command

    resolved = shutil.which(f"{command[0]}.cmd") or shutil.which(command[0])
    if not resolved:
        return command
    return [resolved, *command[1:]]


def git_value(args: list[str]) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def read_version() -> str:
    version_file = ROOT / "VERSION"
    if version_file.exists():
        return version_file.read_text(encoding="utf-8").strip()
    return ""


def count_files(base: pathlib.Path, patterns: tuple[str, ...]) -> int:
    seen: set[pathlib.Path] = set()
    for pattern in patterns:
        seen.update(path for path in base.rglob(pattern) if path.is_file())
    return len(seen)


def parse_pytest_collect(output: str) -> dict[str, int]:
    summary = re.search(
        r"(?P<selected>\d+)\s*/\s*(?P<collected>\d+)\s+tests collected\s+\((?P<deselected>\d+)\s+deselected\)",
        output,
    )
    if summary:
        return {key: int(value) for key, value in summary.groupdict().items()}

    collected = re.search(r"(?P<collected>\d+)\s+tests collected", output)
    if not collected:
        raise ValueError("Could not parse pytest collection summary.")
    total = int(collected.group("collected"))
    return {"collected": total, "selected": total, "deselected": 0}


def parse_node_test_output(output: str) -> dict[str, int | bool]:
    metrics: dict[str, int | bool] = {}
    fields = {
        "tests": "tests",
        "pass": "passed",
        "fail": "failed",
        "cancelled": "cancelled",
        "skipped": "skipped",
        "todo": "todo",
    }
    for line in output.splitlines():
        summary_line = re.sub(r"^[^A-Za-z0-9_]+\s*", "", line.strip())
        parts = summary_line.split()
        if len(parts) != 2:
            continue
        key, value = parts
        if key in fields and value.isdigit():
            metrics[fields[key]] = int(value)
        elif key == "duration_ms":
            metrics["duration_ms"] = round(float(value))

    build = re.search(r"Verified\s+(?P<outputs>\d+)\s+required outputs across\s+(?P<entries>\d+)\s+dist entries", output)
    if build:
        metrics["build_required_outputs"] = int(build.group("outputs"))
        metrics["dist_entries"] = int(build.group("entries"))

    if "daemon dispatcher tests passed" in output:
        metrics["daemon_dispatcher_passed"] = True

    if "tests" not in metrics or "passed" not in metrics:
        raise ValueError("Could not parse Node test summary.")
    return metrics


def collect_metrics(include_node: bool) -> dict[str, Any]:
    pytest_output = run_command([sys.executable, "-m", "pytest", "--collect-only", "-q"], ROOT)
    python_metrics = parse_pytest_collect(pytest_output)
    python_metrics["test_files"] = count_files(ROOT / "tests", ("test_*.py",))
    python_metrics["command"] = "python -m pytest --collect-only -q"
    python_metrics["status"] = "collected"

    internal_uab: dict[str, Any] = {
        "test_files": count_files(ROOT / "packages" / "uab" / "tests", ("*.test.mjs", "*.test.js", "*.js")),
        "status": "not_run",
    }
    installer: dict[str, Any] = {
        "test_files": count_files(ROOT / "installer" / "tests", ("*.test.mjs", "*.test.js")),
        "status": "not_run",
    }

    if include_node:
        uab_output = run_command(["npm", "test"], ROOT / "packages" / "uab")
        internal_uab.update(parse_node_test_output(uab_output))
        internal_uab["command"] = "npm test"
        internal_uab["status"] = "passed" if internal_uab.get("failed", 0) == 0 else "failed"

        installer_output = run_command(["npm", "test"], ROOT / "installer")
        installer.update(parse_node_test_output(installer_output))
        installer["command"] = "npm test"
        installer["status"] = "passed" if installer.get("failed", 0) == 0 else "failed"

    tracked_tests = int(python_metrics["selected"])
    verified_passes = 0
    for group in (internal_uab, installer):
        tracked_tests += int(group.get("tests", 0))
        verified_passes += int(group.get("passed", 0))

    test_files = int(python_metrics["test_files"]) + int(internal_uab["test_files"]) + int(installer["test_files"])

    return {
        "schema_version": 1,
        "project": "lancelot",
        "generated_at": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "branch": git_value(["rev-parse", "--abbrev-ref", "HEAD"]),
        "commit": git_value(["rev-parse", "--short", "HEAD"]),
        "version": read_version(),
        "metrics": {
            "python": python_metrics,
            "internal_uab": internal_uab,
            "installer": installer,
            "totals": {
                "tracked_tests": tracked_tests,
                "verified_node_passes": verified_passes,
                "test_files": test_files,
            },
        },
        "display": {
            "headline_tests": tracked_tests,
            "headline_label": "Runnable tests tracked",
            "test_files": test_files,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Lancelot test metrics for public website display.")
    parser.add_argument("--output", default="docs/test-metrics.json", help="Metrics JSON output path.")
    parser.add_argument(
        "--skip-node",
        action="store_true",
        help="Only collect Python pytest discovery metrics; skip package and installer Node test runs.",
    )
    args = parser.parse_args()

    metrics = collect_metrics(include_node=not args.skip_node)
    output_path = (ROOT / args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
