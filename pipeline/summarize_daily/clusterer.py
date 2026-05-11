"""Cluster picker for the daily summarizer.

Strategy:
1. Query Notion Research Tasks for rows where Status=To Read/Listen AND
   Reachable=yes.
2. Group by Coaching Theme. Pick the theme with most pending rows.
3. Within that theme, sub-cluster by exact Research Angle match. Pick the
   sub-cluster with the most pending rows. Cap at MAX_ROWS_PER_BRIEF
   (default 3 — matches the 40-min audio sweet spot per smoke tests).
4. Return the selected rows + theme + sub-angle slug.

If multiple sub-clusters tie, pick the one whose rows all share the same
Vault Entry — keeps the application brief well-anchored.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from pipeline.notion_client import extract_property, query_research_tasks


MAX_ROWS_PER_BRIEF = 3
MIN_ROWS_PER_BRIEF = 2


@dataclass
class Cluster:
    theme: str
    sub_angle: str
    vault_entry: str
    rows: List[Dict[str, Any]]


def _row_dict(page: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "page_id": page.get("id", ""),
        "url": page.get("url", ""),
        "title": extract_property(page, "Title"),
        "type": extract_property(page, "Type"),
        "status": extract_property(page, "Status"),
        "priority": extract_property(page, "Priority"),
        "category": extract_property(page, "Category"),
        "coaching_theme": extract_property(page, "Coaching Theme") or "",
        "research_angle": extract_property(page, "Research Angle") or "",
        "author_host": extract_property(page, "Author/Host") or "",
        "specific_location": extract_property(page, "Specific Location") or "",
        "relevance": extract_property(page, "Relevance") or "",
        "source_url": extract_property(page, "Source URL") or "",
        "alt_source_url": extract_property(page, "Alt Source URL") or "",
        "reachable": extract_property(page, "Reachable") or "",
        "vault_entry": extract_property(page, "Vault Entry") or "",
    }


def fetch_pending_rows() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for page in query_research_tasks(page_size=100):
        r = _row_dict(page)
        if r["status"] != "To Read/Listen":
            continue
        if r["reachable"] != "yes":
            continue
        rows.append(r)
    return rows


def _effective_url(row: Dict[str, Any]) -> str:
    return row.get("alt_source_url") or row.get("source_url") or ""


def pick_cluster(
    rows: List[Dict[str, Any]],
    *,
    skip_themes: Optional[set[str]] = None,
) -> Optional[Cluster]:
    """Pick the largest sub-cluster within the most-pending theme.

    skip_themes: themes to deprioritize (already covered this week, etc.).
    """
    skip_themes = skip_themes or set()
    by_theme: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["coaching_theme"] in skip_themes:
            continue
        by_theme[r["coaching_theme"]].append(r)

    if not by_theme:
        return None

    theme = max(by_theme.keys(), key=lambda t: len(by_theme[t]))
    theme_rows = by_theme[theme]

    # Sub-cluster by Research Angle
    by_angle: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in theme_rows:
        by_angle[r["research_angle"] or "(no-angle)"].append(r)

    # Pick sub-cluster with most rows, tie-break: rows sharing same vault entry
    def angle_score(angle: str) -> tuple[int, int]:
        ars = by_angle[angle]
        ve_count = Counter(r["vault_entry"] for r in ars)
        most_common_ve = ve_count.most_common(1)
        ve_homogeneity = most_common_ve[0][1] if most_common_ve else 0
        return (len(ars), ve_homogeneity)

    angle = max(by_angle.keys(), key=angle_score)
    angle_rows = by_angle[angle]

    if len(angle_rows) < MIN_ROWS_PER_BRIEF:
        # Single-row sub-cluster — broaden to top N rows from the theme by
        # vault-entry homogeneity instead.
        sorted_rows = sorted(
            theme_rows,
            key=lambda r: (-len([x for x in theme_rows if x["vault_entry"] == r["vault_entry"]]),),
        )
        angle_rows = sorted_rows[:MAX_ROWS_PER_BRIEF]
        angle = "mixed-angles"

    selected = angle_rows[:MAX_ROWS_PER_BRIEF]
    # Vault entry: most common among selected
    ve_counter = Counter(r["vault_entry"] for r in selected if r["vault_entry"])
    vault_entry = (ve_counter.most_common(1)[0][0] if ve_counter else "")

    return Cluster(
        theme=theme,
        sub_angle=angle,
        vault_entry=vault_entry,
        rows=selected,
    )
