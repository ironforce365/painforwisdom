"""Stage 5b — log each verified research reference as a Notion 'Research Tasks' page.

No LLM call. Reads the research CSV from disk, reads the vault entry's Story
Anchor, and for each row builds the structured page body (3 sections per the
agent spec) before POSTing via notion-client. Pacing: 0.4s between pages
(Notion limit ~3/s).
"""
from __future__ import annotations

import csv
import io
import re
import time
from pathlib import Path
from typing import Any, Dict, List

from pipeline.contracts import assert_inputs
from pipeline import themes_db
from pipeline.notion_client import (
    _divider,
    _heading_2,
    _paragraph,
    create_research_task,
    fetch_page_blocks,
    page_id,
    text_to_blocks,
)
from pipeline.runtime import append_metric, run_telemetry_path
from pipeline.state import State


def _resolve_agent(theme: str) -> str | None:
    """Look up theme → agent from the themes DB.

    Sub-themes route to their parent umbrella's agent (see `themes_db.get_agent`).
    Returns `None` for themes not in the DB so the caller can render the
    "NEW THEME" warning."""
    if not theme:
        return None
    conn = themes_db.connect()
    try:
        return themes_db.get_agent(conn, theme)
    finally:
        conn.close()


def _read_story_anchor(vault_entry_path: str) -> str:
    """Pull the Story Anchor section out of the vault entry markdown."""
    text = Path(vault_entry_path).read_text()
    m = re.search(r"##\s+Story Anchor\s*\n(.+?)(?:\n##\s|\Z)", text, re.DOTALL)
    return m.group(1).strip() if m else "(Story Anchor not found)"


def _build_body_blocks(
    *,
    story_anchor: str,
    specific_location: str,
    title: str,
    author_host: str,
    coaching_theme: str,
    research_angle: str,
    source_url: str,
    paywall: bool,
    vault_entry: str,
) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    blocks.append(_heading_2("What Triggered This Research"))
    blocks.extend(text_to_blocks(story_anchor))
    blocks.append(_divider())

    blocks.append(_heading_2("Deep Dive Prompt"))
    upload_line = f"Upload {specific_location or 'this source'} to AnythingLLM, then use this prompt:"
    blocks.append(_paragraph(upload_line))
    blocks.append(_divider())

    prompt_text = (
        f"Context: {story_anchor}\n\n"
        f"Using \"{title}\" by {author_host}"
        + (f" ({specific_location})" if specific_location else "")
        + ", help me with these specific questions:\n\n"
        f"1. What does this source say specifically about {research_angle}?\n"
        f"2. What is the underlying mechanism the author identifies?\n"
        f"3. How does this apply to the situation I described above?"
    )
    blocks.extend(text_to_blocks(prompt_text))
    blocks.append(_divider())

    agent = _resolve_agent(coaching_theme)
    if agent:
        blocks.append(_paragraph(f"Agent: {agent}"))
    else:
        blocks.append(_paragraph(f"Agent: ⚠️ NEW THEME ({coaching_theme}) — no agent yet."))
    blocks.append(_divider())

    blocks.append(_heading_2("Reference Details"))
    detail_lines = [
        f"- Title: {title}",
        f"- Author/Host: {author_host}",
        f"- Specific Location: {specific_location or '(none)'}",
        f"- Access: {'paywalled' if paywall else 'free'}",
        f"- Source URL: {source_url or '(none)'}",
        f"- Research Angle: {research_angle}",
        f"- Vault Entry: {vault_entry}",
    ]
    blocks.append(_paragraph("\n".join(detail_lines)))
    return blocks


def _coerce_csv_value(v: Any) -> str:
    """DictReader yields None for missing fields and a list for extra fields
    that overflow the header count (bucketed under key=None). Normalize to a
    plain stripped string."""
    if v is None:
        return ""
    if isinstance(v, list):
        return ",".join(str(x) for x in v).strip()
    return str(v).strip()


def _read_csv_rows(csv_path: str) -> List[Dict[str, str]]:
    text = Path(csv_path).read_text()
    reader = csv.DictReader(io.StringIO(text))
    rows: List[Dict[str, str]] = []
    for row in reader:
        rows.append({k: _coerce_csv_value(v) for k, v in row.items() if k is not None})
    return rows


def node_notion_research(state: State) -> Dict[str, Any]:
    assert_inputs("notion_research", state)
    t0 = time.time()
    print("[notion-research] start")

    csv_path = state.get("research_csv_path")
    vault_entry_path = state.get("vault_entry_path")
    vault_entry_slug = state.get("vault_entry_slug", "")
    if not csv_path or not vault_entry_path:
        raise RuntimeError("notion-research: research_csv_path and vault_entry_path required")

    rows = _read_csv_rows(csv_path)
    story_anchor = _read_story_anchor(vault_entry_path)

    created: List[Dict[str, str]] = []
    failed: List[Dict[str, str]] = []

    for row in rows:
        try:
            paywall_str = (row.get("Paywall", "") or "").strip().lower()
            paywall = paywall_str in ("true", "yes", "1", "✓")
            category = row.get("Category", "Comprehensive Understanding")
            priority = "High" if "Comprehensive" in category else "Medium"
            blocks = _build_body_blocks(
                story_anchor=story_anchor,
                specific_location=row.get("Specific Location", ""),
                title=row.get("Title", ""),
                author_host=row.get("Author/Host", ""),
                coaching_theme=row.get("Coaching Theme", ""),
                research_angle=row.get("Research Angle", ""),
                source_url=row.get("Source URL", ""),
                paywall=paywall,
                vault_entry=vault_entry_slug,
            )
            reachable = (row.get("Reachable", "") or "").strip().lower()
            if reachable not in ("yes", "no", "unknown"):
                reachable = "yes"  # default for legacy rows pre-Phase-3b CSV
            page = create_research_task(
                title=row.get("Title", "(untitled)"),
                ref_type=row.get("Type", "Article"),
                priority=priority,
                author_host=row.get("Author/Host", ""),
                specific_location=row.get("Specific Location", ""),
                relevance=row.get("Relevance", ""),
                research_angle=row.get("Research Angle", ""),
                category=category,
                source_url=row.get("Source URL", ""),
                paywall=paywall,
                coaching_theme=row.get("Coaching Theme", ""),
                vault_entry=vault_entry_slug,
                body_blocks=blocks,
                reachable=reachable,
                reachability_reason=row.get("Reachability Reason", ""),
            )
            pid = page_id(page)
            # Soft fetch-back to confirm body landed
            page_blocks = fetch_page_blocks(pid)
            created.append(
                {"title": row.get("Title", ""), "type": row.get("Type", ""), "page_id": pid, "blocks": str(len(page_blocks))}
            )
        except Exception as exc:
            failed.append({"title": row.get("Title", ""), "error": str(exc)[:200]})

    if not created:
        raise RuntimeError(f"notion-research: 0 of {len(rows)} pages created. Errors: {failed}")

    run_dir = Path(state["run_dir"])
    out_dir = run_dir / "notion-research-logger"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "notion_summary.md"
    summary_lines = [
        "✓ Notion Research Tasks updated",
        "",
        f"Created: {len(created)} tasks",
        f"Failed:  {len(failed)}",
        "",
        "Tasks created:",
    ]
    for c in created:
        summary_lines.append(f"  ✓ {c['title']} — {c['type']}")
    if failed:
        summary_lines.append("")
        summary_lines.append("Failures:")
        for f in failed:
            summary_lines.append(f"  ✗ {f['title']} — {f['error']}")
    summary_path.write_text("\n".join(summary_lines) + "\n")

    duration = time.time() - t0
    append_metric(
        run_telemetry_path(state["run_dir"]),
        "notion-research",
        duration_s=round(duration, 2),
        rows_in=len(rows),
        created=len(created),
        failed=len(failed),
    )
    print(f"[notion-research] done {duration:.1f}s created={len(created)} failed={len(failed)}")
    return {
        "notion_task_count": len(created),
        "notion_research_summary_path": str(summary_path),
    }
