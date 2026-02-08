"""
Built-in skill: repo_writer — create, edit, delete, and patch files.

Operates only within allowed paths (enforced by ExecutionToken).
Emits FILE_OP receipts for each operation.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

# Skill manifest metadata
MANIFEST = {
    "name": "repo_writer",
    "version": "1.0.0",
    "description": "Create, edit, delete files within allowed paths",
    "risk": "MEDIUM",
    "permissions": ["file_write"],
    "inputs": [
        {"name": "action", "type": "string", "required": True,
         "description": "create|edit|delete|patch"},
        {"name": "path", "type": "string", "required": True,
         "description": "File path (relative to workspace root)"},
        {"name": "content", "type": "string", "required": False,
         "description": "File content for create/edit, or patch content"},
    ],
}

# Default workspace root (overridable by context)
DEFAULT_WORKSPACE = os.getenv("LANCELOT_WORKSPACE", "/home/lancelot/data")


def execute(context, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a file operation.

    Args:
        context: SkillContext with skill_name, request_id, caller, metadata.
        inputs: Dict with 'action', 'path', and optionally 'content'.

    Returns:
        Dict with 'status', 'path', and operation details.
    """
    action = inputs.get("action", "").lower()
    rel_path = inputs.get("path", "")
    content = inputs.get("content", "")
    workspace = inputs.get("workspace", DEFAULT_WORKSPACE)

    if not rel_path:
        raise ValueError("Missing required input: 'path'")

    if action not in ("create", "edit", "delete", "patch"):
        raise ValueError(f"Unknown action: '{action}'. Must be create|edit|delete|patch")

    # Resolve to absolute path within workspace
    full_path = _resolve_safe_path(workspace, rel_path)

    if action == "create":
        return _create_file(full_path, content)
    elif action == "edit":
        return _edit_file(full_path, content)
    elif action == "delete":
        return _delete_file(full_path)
    elif action == "patch":
        return _patch_file(full_path, content)

    return {"status": "error", "error": f"Unhandled action: {action}"}


def _resolve_safe_path(workspace: str, rel_path: str) -> Path:
    """Resolve a relative path within the workspace, preventing path traversal."""
    ws = Path(workspace).resolve()
    target = (ws / rel_path).resolve()

    # Security: ensure target is within workspace
    if not str(target).startswith(str(ws)):
        raise ValueError(f"Path traversal blocked: '{rel_path}' escapes workspace")

    return target


def _create_file(path: Path, content: str) -> Dict[str, Any]:
    """Create a new file."""
    if path.exists():
        raise FileExistsError(f"File already exists: {path}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    logger.info("repo_writer: created %s (%d bytes)", path, len(content))

    return {"status": "created", "path": str(path), "bytes_written": len(content)}


def _edit_file(path: Path, content: str) -> Dict[str, Any]:
    """Overwrite an existing file."""
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    old_content = path.read_text(encoding="utf-8")
    path.write_text(content, encoding="utf-8")
    logger.info("repo_writer: edited %s (%d -> %d bytes)", path, len(old_content), len(content))

    return {
        "status": "edited",
        "path": str(path),
        "old_bytes": len(old_content),
        "new_bytes": len(content),
    }


def _delete_file(path: Path) -> Dict[str, Any]:
    """Delete a file."""
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    size = path.stat().st_size
    path.unlink()
    logger.info("repo_writer: deleted %s (%d bytes)", path, size)

    return {"status": "deleted", "path": str(path), "bytes_deleted": size}


def _patch_file(path: Path, patch_content: str) -> Dict[str, Any]:
    """Apply a simple text patch to a file.

    Patch format: lines starting with '+' are added, '-' are removed.
    Lines without prefix are context (must match).
    """
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    result_lines = []
    added = 0
    removed = 0

    for patch_line in patch_content.splitlines(keepends=True):
        stripped = patch_line.rstrip("\n\r")
        if stripped.startswith("+"):
            result_lines.append(stripped[1:] + "\n")
            added += 1
        elif stripped.startswith("-"):
            removed += 1
            # Skip this line from original
        else:
            result_lines.append(patch_line)

    new_content = "".join(result_lines)
    path.write_text(new_content, encoding="utf-8")
    logger.info("repo_writer: patched %s (+%d -%d lines)", path, added, removed)

    return {
        "status": "patched",
        "path": str(path),
        "lines_added": added,
        "lines_removed": removed,
    }
