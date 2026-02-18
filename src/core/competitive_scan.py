"""
Competitive Scan — store and diff competitive intelligence in episodic memory.

V24: When FEATURE_COMPETITIVE_SCAN is enabled, competitive research results
are stored as episodic memory items with structured metadata.  Subsequent
scans of the same target retrieve previous scans and produce a human-readable
diff showing new findings, removed findings, and trends over time.

Requires FEATURE_MEMORY_VNEXT for episodic storage access.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Tags used for competitive scan items in episodic memory
SCAN_TAG = "competitive_scan"
SCAN_NAMESPACE_PREFIX = "competitive:"

# Keywords that suggest a message is requesting competitive research
_COMPETITIVE_KEYWORDS = [
    "competitive", "competitor", "rival", "alternative", "versus", " vs ",
    "compare", "comparison", "intel on", "intelligence on", "research on",
    "news about", "developments", "what's new with", "updates on",
    "track ", "monitor ", "analyze ",
]


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect_competitive_target(user_message: str) -> Optional[str]:
    """Detect if a user message is requesting competitive intelligence.

    Returns the target name/phrase if detected, None otherwise.
    Uses keyword matching + simple extraction heuristics.
    """
    lower = user_message.lower()

    # Check for competitive keywords
    has_keyword = any(kw in lower for kw in _COMPETITIVE_KEYWORDS)
    if not has_keyword:
        return None

    # Try to extract the target name from common patterns
    # "news about X", "research X", "intel on X", "compare X"
    patterns = [
        r"(?:news|updates|developments|intel|intelligence)\s+(?:about|on|for|regarding)\s+[\"']?(.+?)[\"']?(?:\s*[,.]|\s+and\s+|\s+then\s+|$)",
        r"(?:research|analyze|track|monitor|compare)\s+[\"']?(.+?)[\"']?(?:\s*[,.]|\s+and\s+|\s+then\s+|$)",
        r"(?:competitor|rival|alternative)\s+[\"']?(.+?)[\"']?(?:\s*[,.]|\s+and\s+|\s+then\s+|$)",
    ]

    for pattern in patterns:
        match = re.search(pattern, lower)
        if match:
            target = match.group(1).strip()
            # Clean up common trailing words
            for suffix in ["summarize", "draft", "flag", "email", "send", "telegram"]:
                if target.endswith(suffix):
                    target = target[:target.rfind(suffix)].strip().rstrip(",")
            if target and len(target) > 2:
                return target

    # Fallback: return the first quoted term if present
    quoted = re.findall(r'["\'](.+?)["\']', user_message)
    if quoted:
        return quoted[0]

    return None


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def store_scan(
    target: str,
    findings: str,
    receipt_skills: List[str],
    memory_store_manager,
) -> Optional[str]:
    """Store a competitive scan as an episodic memory item.

    Args:
        target: Name of the competitive target
        findings: The scan findings text (response content)
        receipt_skills: List of skill names used during the scan
        memory_store_manager: MemoryStoreManager instance

    Returns:
        The memory item ID, or None on failure
    """
    try:
        from memory.schemas import (
            MemoryItem, MemoryTier, Provenance, ProvenanceType, generate_id,
        )
    except ImportError:
        from src.core.memory.schemas import (
            MemoryItem, MemoryTier, Provenance, ProvenanceType, generate_id,
        )

    item_id = generate_id()
    namespace = f"{SCAN_NAMESPACE_PREFIX}{target.lower().replace(' ', '_')}"

    item = MemoryItem(
        id=item_id,
        tier=MemoryTier.episodic,
        namespace=namespace,
        title=f"Competitive Scan: {target} ({datetime.utcnow().strftime('%Y-%m-%d %H:%M')})",
        content=findings[:2000],  # Cap content to avoid bloat
        tags=[SCAN_TAG, f"target:{target.lower()}"],
        confidence=0.7,
        decay_half_life_days=30,
        provenance=[Provenance(
            type=ProvenanceType.agent_inference,
            ref=f"competitive_scan:{target}",
            snippet=findings[:100],
        )],
        metadata={
            "scan_target": target,
            "scan_date": datetime.utcnow().isoformat(),
            "tools_used": receipt_skills,
        },
        token_count=len(findings) // 4,
    )

    try:
        episodic = memory_store_manager.episodic
        episodic.insert(item)
        logger.info(f"V24: Stored competitive scan for '{target}' (id={item_id}, {len(findings)} chars)")
        return item_id
    except Exception as e:
        logger.warning(f"V24: Failed to store competitive scan: {e}")
        return None


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def retrieve_previous_scans(
    target: str,
    memory_store_manager,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """Retrieve previous competitive scans for a target from episodic memory.

    Args:
        target: Name of the competitive target
        memory_store_manager: MemoryStoreManager instance
        limit: Max number of previous scans to return

    Returns:
        List of dicts with 'content', 'date', 'metadata' from previous scans
    """
    namespace = f"{SCAN_NAMESPACE_PREFIX}{target.lower().replace(' ', '_')}"

    try:
        episodic = memory_store_manager.episodic
        results = episodic.search(
            query=target,
            namespace=namespace,
            limit=limit,
        )

        scans = []
        for item in results:
            scans.append({
                "content": item.content,
                "date": item.metadata.get("scan_date", item.created_at.isoformat()),
                "tools_used": item.metadata.get("tools_used", []),
                "title": item.title,
            })

        logger.info(f"V24: Retrieved {len(scans)} previous scans for '{target}'")
        return scans
    except Exception as e:
        logger.warning(f"V24: Failed to retrieve previous scans: {e}")
        return []


# ---------------------------------------------------------------------------
# Diffing
# ---------------------------------------------------------------------------

@dataclass
class ScanDiff:
    """Result of comparing current findings against a previous scan."""
    has_previous: bool = False
    previous_date: str = ""
    new_findings: List[str] = field(default_factory=list)
    removed_findings: List[str] = field(default_factory=list)
    summary: str = ""


def diff_scans(previous_content: str, current_content: str, previous_date: str = "") -> ScanDiff:
    """Generate a diff between a previous scan and current findings.

    Uses sentence-level comparison to identify new and removed findings.

    Args:
        previous_content: Content from the most recent previous scan
        current_content: Content from the current scan
        previous_date: Date string of the previous scan

    Returns:
        ScanDiff with new/removed findings and a human-readable summary
    """
    def _extract_findings(text: str) -> set:
        """Extract meaningful sentences/bullet points from scan text."""
        lines = set()
        for line in text.split("\n"):
            line = line.strip().lstrip("•-*123456789.)")
            line = line.strip()
            if len(line) > 20:  # Skip short lines (headers, whitespace)
                lines.add(line.lower())
        return lines

    prev_findings = _extract_findings(previous_content)
    curr_findings = _extract_findings(current_content)

    new = curr_findings - prev_findings
    removed = prev_findings - curr_findings

    diff = ScanDiff(
        has_previous=True,
        previous_date=previous_date,
        new_findings=sorted(new)[:10],  # Cap at 10 items
        removed_findings=sorted(removed)[:10],
    )

    # Build human-readable summary
    parts = [f"Compared against scan from {previous_date}:"]
    if new:
        parts.append(f"\n**New since last scan** ({len(new)} findings):")
        for item in diff.new_findings[:5]:
            parts.append(f"  + {item[:100]}")
    if removed:
        parts.append(f"\n**No longer observed** ({len(removed)} findings):")
        for item in diff.removed_findings[:5]:
            parts.append(f"  - {item[:100]}")
    if not new and not removed:
        parts.append("No significant changes detected since last scan.")

    diff.summary = "\n".join(parts)
    return diff


def build_context_from_previous(scans: List[Dict[str, Any]]) -> str:
    """Build context string from previous scans to inject into the prompt.

    Args:
        scans: List of previous scan dicts from retrieve_previous_scans()

    Returns:
        Context string to append to the user message or system instruction
    """
    if not scans:
        return ""

    most_recent = scans[0]
    lines = [
        f"\n--- PREVIOUS COMPETITIVE SCAN (from {most_recent['date']}) ---",
        most_recent["content"][:1000],  # Cap to avoid bloating context
    ]

    if len(scans) > 1:
        lines.append(f"\n({len(scans) - 1} older scans also available in memory)")

    lines.append(
        "\nCompare your findings against this previous scan. "
        "Highlight what's NEW, what's CHANGED, and what's NO LONGER relevant."
    )
    lines.append("--- END PREVIOUS SCAN ---\n")

    return "\n".join(lines)
