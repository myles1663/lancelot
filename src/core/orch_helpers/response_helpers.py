# V30: Response formatting helper functions extracted from orchestrator.py
# These are pure functions — no instance state, no side effects.
# EGOS audit Phase 1: orchestrator decomposition (conservative)

import os
from pathlib import Path


def format_tool_receipts(receipts: list, error: str = "", note: str = "") -> str:
    """Format tool call receipts into a readable summary."""
    lines = []
    if note:
        lines.append(note)
    if error:
        lines.append(f"Error: {error}")
    for r in receipts:
        status = r.get("result", "unknown")
        lines.append(f"- {r['skill']}: {status}")
    return "\n".join(lines) if lines else "No results."


def append_download_links(response: str, doc_paths: list) -> str:
    """V29b: Append download links for auto-created documents to the chat response.

    Converts absolute workspace paths to /api/files/ URLs so the War Room
    markdown renderer produces clickable download links.
    """
    if not doc_paths:
        return response
    links = []
    _ws = os.getenv("LANCELOT_WORKSPACE", "/home/lancelot/workspace")
    _tok = os.getenv("LANCELOT_API_TOKEN", "")
    for path in doc_paths:
        fname = Path(path).name
        # Determine relative path from workspace root
        rel = path.replace(f"{_ws}/", "").lstrip("/")
        ext = Path(fname).suffix.lower().lstrip(".")
        type_label = {
            "pdf": "PDF", "docx": "Word", "xlsx": "Excel",
            "pptx": "PowerPoint", "csv": "CSV", "md": "Markdown",
        }.get(ext, ext.upper())
        _dl_url = f"/api/files/{rel}?token={_tok}" if _tok else f"/api/files/{rel}"
        links.append(f"- [{fname}]({_dl_url}) ({type_label})")

    link_block = "\n\n---\n**Attached Documents:**\n" + "\n".join(links)
    return response + link_block
