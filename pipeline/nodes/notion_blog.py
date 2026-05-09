"""Stage 5a — log blog post to Notion 'Blog post pending publications' DB.

No LLM call; pure REST. Reads blog_post.md content from state, parses title
(first non-empty H1 or 'Title:' line) + body, creates a Notion page in the
target data source via the python notion-client SDK. Verifies creation by
fetching the page back and asserting non-empty body.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Dict

from pipeline.contracts import assert_inputs
from pipeline.notion_client import (
    create_blog_page,
    fetch_page_blocks,
    page_id,
    page_url,
)
from pipeline.runtime import append_metric, run_telemetry_path
from pipeline.state import State


def _strip_title_line(body: str) -> str:
    """Drop a leading **Title:** ... line or H1 line if present (Notion stores
    title in the property, not in the body)."""
    lines = body.splitlines()
    out_lines = []
    skipped = False
    for line in lines:
        if not skipped and re.match(r"^\s*(\*\*Title:\*\*|#)\s+", line):
            skipped = True
            continue
        out_lines.append(line)
    return "\n".join(out_lines).strip()


def node_notion_blog(state: State) -> Dict[str, Any]:
    assert_inputs("notion_blog", state)
    t0 = time.time()
    print("[notion-blog] start")

    title = state.get("blog_post_title", "")
    body = state.get("blog_post_text", "")
    video_date = state.get("video_date", "")
    if not title or not body or not video_date:
        raise RuntimeError("notion-blog: missing required state (title/body/video_date)")

    body_no_title = _strip_title_line(body)
    page = create_blog_page(title=title, body_text=body_no_title, video_date=video_date)
    pid = page_id(page)
    purl = page_url(page)

    # Verify body landed.
    blocks = fetch_page_blocks(pid)
    if not blocks:
        raise RuntimeError(f"notion-blog: created page {pid} has empty body")

    run_dir = Path(state["run_dir"])
    out_dir = run_dir / "notion-blog-post-logger"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "notion_blog_summary.md"
    summary_path.write_text(
        "✓ Blog post logged to Notion\n\n"
        f"Title:    {title}\n"
        f"Date:     {video_date}\n"
        f"Status:   Pending publication\n"
        f"Notion:   {purl}\n"
    )

    duration = time.time() - t0
    append_metric(
        run_telemetry_path(state["run_dir"]),
        "notion-blog",
        duration_s=round(duration, 2),
        page_id=pid,
        page_url=purl,
        body_blocks=len(blocks),
    )
    print(f"[notion-blog] done {duration:.1f}s url={purl}")
    return {
        "notion_blog_url": purl,
        "notion_blog_summary_path": str(summary_path),
    }
