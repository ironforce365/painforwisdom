"""Stage 5c — WordPress draft creation.

Runs after both ``notion_blog`` (for the Notion page id round-trip) and
``extract_image`` (for the featured image). Bundles the post into a
WP-ready form regardless of dormant state, so a future
``WORDPRESS_ENABLED=true`` flip can replay the run if needed.

Skip rules:
  - ``validator_verdict == "FAIL"`` (replay-aware).
  - missing ``blog_post_text`` / title / date.
  - ``WORDPRESS_ENABLED != "true"`` → bundle to disk, no API call.

Body format (matches painforwisdom blog convention):
  - first paragraph: italic ``MM/DD/YY`` + YouTube short placeholder link
  - featured image rendered by WP via ``featured_media`` (set on the post)
  - body paragraphs with **bold** preserved
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

from pipeline.contracts import assert_inputs
from pipeline.notion_client import update_blog_page_wordpress_url
from pipeline.runtime import append_metric, run_telemetry_path
from pipeline.state import State
from pipeline.wordpress_client import (
    WordPressClient,
    WordPressClientError,
    first_50_words,
    render_wp_html,
    write_dormant_bundle,
)


def _write_marker(out_dir: Path, name: str, body: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / name).write_text(body)


def _yyyymmdd_to_mmddyy(date_iso: str) -> str:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", date_iso or "")
    if not m:
        return date_iso or ""
    return f"{m.group(2)}/{m.group(3)}/{m.group(1)[2:]}"


def _strip_title_line(body: str) -> str:
    """Drop the writer's ``**Title:**`` line — title is set on the WP post."""
    lines = body.splitlines()
    out: list[str] = []
    skipped = False
    for line in lines:
        if not skipped and re.match(r"^\s*\*\*Title:\*\*\s+", line):
            skipped = True
            continue
        out.append(line)
    return "\n".join(out).strip()


def node_wordpress_draft(state: State) -> Dict[str, Any]:
    assert_inputs("wordpress_draft", state)
    t0 = time.time()
    print("[wordpress-draft] start")

    run_dir = Path(state["run_dir"])
    out_dir = run_dir / "wordpress-draft"
    out_dir.mkdir(parents=True, exist_ok=True)

    if state.get("validator_verdict") == "FAIL":
        _write_marker(out_dir, "SKIPPED.md", "WordPress draft skipped: validator_verdict=FAIL\n")
        print("[wordpress-draft] skipped (validator FAIL)")
        return {"wordpress_skipped": True, "wordpress_skip_reason": "validator FAIL"}

    title = state.get("blog_post_title", "")
    body_md_full = state.get("blog_post_text", "")
    video_date = state.get("video_date", "")
    if not title or not body_md_full:
        _write_marker(
            out_dir,
            "SKIPPED.md",
            "WordPress draft skipped: missing blog_post_title or blog_post_text.\n",
        )
        print("[wordpress-draft] skipped (no blog post)")
        return {"wordpress_skipped": True, "wordpress_skip_reason": "no blog post"}

    body_md = _strip_title_line(body_md_full)
    date_mmddyy = _yyyymmdd_to_mmddyy(video_date)
    excerpt = state.get("blog_post_excerpt", "") or first_50_words(body_md)
    featured_image_path: Optional[Path] = None
    fip = state.get("featured_image_path", "")
    if fip and Path(fip).is_file():
        featured_image_path = Path(fip)

    # Tags: lowercase singular slugs derived from themes + frameworks +
    # always include "painforwisdom" so the blog stays browseable by tag.
    tags: list[str] = ["painforwisdom"]
    for slug in (state.get("themes_attached") or []) + (state.get("frameworks_attached") or []):
        if isinstance(slug, str) and slug:
            tags.append(slug.replace("-", " "))
    # Dedupe preserving order.
    seen: set[str] = set()
    tags = [t for t in tags if not (t.lower() in seen or seen.add(t.lower()))]

    html = render_wp_html(
        body_md=body_md,
        date_mmddyy=date_mmddyy,
        featured_image_url="",  # featured_media handles image rendering
    )

    bundle_meta = {
        "video_date": video_date,
        "date_mmddyy": date_mmddyy,
        "youtube_url_placeholder": "[YOUTUBE_SHORT_URL]",
        "youtube_url": state.get("youtube_url", ""),
        "notion_blog_url": state.get("notion_blog_url", ""),
        "notion_blog_page_id": state.get("notion_blog_page_id", ""),
        "themes_attached": state.get("themes_attached", []),
        "frameworks_attached": state.get("frameworks_attached", []),
    }
    bundle_paths = write_dormant_bundle(
        out_dir,
        title=title,
        body_md=body_md,
        html=html,
        excerpt=excerpt,
        tags=tags,
        featured_image_path=featured_image_path,
        metadata=bundle_meta,
    )

    if os.environ.get("WORDPRESS_ENABLED", "").lower() != "true":
        _write_marker(
            out_dir,
            "SKIPPED.md",
            "WordPress draft skipped: WORDPRESS_ENABLED != 'true'.\n"
            "Bundle written for manual upload (post.md / post.html / meta.json).\n",
        )
        duration = time.time() - t0
        append_metric(
            run_telemetry_path(state["run_dir"]),
            "wordpress-draft",
            duration_s=round(duration, 2),
            skipped=True,
            reason="dormant",
            tag_count=len(tags),
            has_image=bool(featured_image_path),
        )
        print(f"[wordpress-draft] dormant {duration:.1f}s bundle={bundle_paths['bundle_dir']}")
        return {
            "wordpress_skipped": True,
            "wordpress_dormant": True,
            "wordpress_skip_reason": "dormant (WORDPRESS_ENABLED!=true)",
            "wordpress_bundle_path": bundle_paths["bundle_dir"],
        }

    # Live publish path. Any error degrades to dormant-style state (bundle
    # already on disk) plus a skip reason — never raises into the graph,
    # because a WordPress outage should not poison the rest of the run.
    try:
        with WordPressClient() as wp:
            featured_media_id: Optional[int] = None
            if featured_image_path:
                media = wp.upload_media(featured_image_path, alt_text=title)
                featured_media_id = media.media_id

            post = wp.create_draft_post(
                title=title,
                html=html,
                excerpt=excerpt,
                tags=tags,
                featured_media_id=featured_media_id,
            )

        wordpress_url = post.url
        wordpress_post_id = post.post_id
        (out_dir / "publish.json").write_text(
            json.dumps(
                {
                    "post_id": wordpress_post_id,
                    "url": wordpress_url,
                    "tags": tags,
                    "excerpt": excerpt,
                    "featured_media_id": featured_media_id,
                },
                indent=2,
            )
        )

        # Notion round-trip: write the new URL back so the Notion page is
        # the canonical state for backfill / browsing.
        notion_page_id = state.get("notion_blog_page_id", "")
        if notion_page_id:
            try:
                update_blog_page_wordpress_url(
                    notion_page_id,
                    wordpress_url,
                    status="Draft Created",
                    excerpt=excerpt,
                )
            except Exception as exc:  # noqa: BLE001
                _write_marker(
                    out_dir,
                    "NOTION_UPDATE_FAILED.md",
                    f"WP draft created at {wordpress_url} but Notion update failed: "
                    f"{type(exc).__name__}: {exc}\n",
                )

        duration = time.time() - t0
        append_metric(
            run_telemetry_path(state["run_dir"]),
            "wordpress-draft",
            duration_s=round(duration, 2),
            post_id=wordpress_post_id,
            url=wordpress_url,
            tag_count=len(tags),
            has_image=bool(featured_media_id),
        )
        print(f"[wordpress-draft] done {duration:.1f}s url={wordpress_url}")
        return {
            "wordpress_post_id": wordpress_post_id,
            "wordpress_url": wordpress_url,
            "wordpress_skipped": False,
            "wordpress_dormant": False,
            "wordpress_bundle_path": bundle_paths["bundle_dir"],
        }
    except WordPressClientError as exc:
        _write_marker(
            out_dir,
            "PUBLISH_FAILED.md",
            f"WordPress publish failed: {exc}\n",
        )
        duration = time.time() - t0
        append_metric(
            run_telemetry_path(state["run_dir"]),
            "wordpress-draft",
            duration_s=round(duration, 2),
            error=str(exc),
        )
        print(f"[wordpress-draft] failed {duration:.1f}s: {exc}")
        return {
            "wordpress_skipped": True,
            "wordpress_skip_reason": f"publish error: {exc}",
            "wordpress_bundle_path": bundle_paths["bundle_dir"],
        }
