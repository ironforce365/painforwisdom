"""Stage 5c — WordPress draft creation.

Runs after both ``notion_blog`` (for the Notion page id round-trip) and
``extract_image`` (for the featured image). Bundles the post into a
WP-ready form in every mode, writes the C9 package unless the mode is ``off``,
then follows the mode table (spec 2026-11-03-console-operability §1.3).

Skip rules:
  - ``validator_verdict == "FAIL"`` (replay-aware).
  - missing ``blog_post_text`` / title / date.
  - ``wordpress.mode`` in ``config/publishing.json`` is ``off`` or ``manual`` → no API call.

Body format (matches painforwisdom blog convention):
  - first paragraph: italic ``MM/DD/YY`` + YouTube short placeholder link
  - featured image rendered by WP via ``featured_media`` (set on the post)
  - body paragraphs with **bold** preserved
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

from pipeline.contracts import assert_inputs
from pipeline.publish import package as publish_package
from pipeline.publish import run_target, specs
from pipeline.publishing_config import current_profile, load
from pipeline.runtime import append_metric, run_telemetry_path
from pipeline.state import State
from pipeline.wordpress_client import (
    first_50_words,
    render_wp_html,
    write_dormant_bundle,
)

FIELD_SPECS = specs.WORDPRESS


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

    # wordpress_draft fires once per inbound edge, and a retry replays it: never publish twice.
    existing = publish_package.read(run_dir, "wordpress")
    if existing and existing["status"] == "published":
        print("[wordpress-draft] already published")
        return {
            "wordpress_post_id": int(existing["result"]["id"]),
            "wordpress_url": existing["result"]["url"],
            "wordpress_skipped": False,
            "wordpress_bundle_path": str(out_dir),
        }
    if existing and existing["status"] == "publishing":  # a publisher holds it: its result lands there
        append_metric(run_telemetry_path(state["run_dir"]), "wordpress-draft", duration_s=round(time.time() - t0, 2),
                      skipped=True, reason="publishing")
        print("[wordpress-draft] a publish is in progress, leaving it alone")
        return {"wordpress_skipped": True, "wordpress_skip_reason": "publishing", "wordpress_bundle_path": str(out_dir)}

    config = load(current_profile())["wordpress"]
    mode = publish_package.replay_mode(existing, config["mode"])
    body_md = _strip_title_line(body_md_full)
    date_mmddyy = _yyyymmdd_to_mmddyy(video_date)
    excerpt = state.get("blog_post_excerpt", "") or first_50_words(body_md)
    featured_image_path: Optional[Path] = None
    fip = state.get("featured_image_path", "")
    if fip and Path(fip).is_file():
        featured_image_path = Path(fip)

    # Tags: lowercase singular slugs derived from themes + frameworks +
    # always include "painforwisdom" so the blog stays browseable by tag.
    tags: list[str] = ["painforwisdom", *config["default_tags"]]
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

    if mode != "off":
        publish_package.write(run_dir, publish_package.kept_on_replay(existing, publish_package.new_package(
            "wordpress",
            mode,
            {
                "title": title[:FIELD_SPECS["title"]["max"]],
                "body": body_md,
                "excerpt": excerpt[:FIELD_SPECS["excerpt"]["max"]],
                "tags": tags,
                "categories": list(config["categories"]),
                "status": config["status"],
                "youtube_url": state.get("youtube_url", ""),
            },
            FIELD_SPECS,
            readonly={
                "date_mmddyy": date_mmddyy,
                "notion_blog_url": state.get("notion_blog_url", ""),
                "notion_blog_page_id": state.get("notion_blog_page_id", ""),
            },
            assets=[{"role": "featured_image", "path": str(featured_image_path)}] if featured_image_path else [],
        )))

    if mode != "auto":
        _write_marker(
            out_dir,
            "SKIPPED.md",
            f"WordPress publish skipped: mode={mode}.\n"
            "Bundle written for manual upload (post.md / post.html / meta.json).\n",
        )
        duration = time.time() - t0
        append_metric(
            run_telemetry_path(state["run_dir"]),
            "wordpress-draft",
            duration_s=round(duration, 2),
            skipped=True,
            reason=mode,
            tag_count=len(tags),
            has_image=bool(featured_image_path),
        )
        print(f"[wordpress-draft] {mode} {duration:.1f}s bundle={bundle_paths['bundle_dir']}")
        return {
            "wordpress_skipped": True,
            "wordpress_skip_reason": mode,
            "wordpress_bundle_path": bundle_paths["bundle_dir"],
        }

    # Any error lands in the package and never raises into the graph:
    # a WordPress outage should not poison the rest of the run.
    published = run_target(run_dir, "wordpress")
    duration = time.time() - t0
    if published["status"] != "published":
        append_metric(
            run_telemetry_path(state["run_dir"]),
            "wordpress-draft",
            duration_s=round(duration, 2),
            error=published["error"],
        )
        print(f"[wordpress-draft] failed {duration:.1f}s: {published['error']}")
        return {
            "wordpress_skipped": True,
            "wordpress_skip_reason": f"publish error: {published['error']}",
            "wordpress_bundle_path": bundle_paths["bundle_dir"],
        }
    wordpress_url = published["result"]["url"]
    wordpress_post_id = int(published["result"]["id"])
    append_metric(
        run_telemetry_path(state["run_dir"]),
        "wordpress-draft",
        duration_s=round(duration, 2),
        post_id=wordpress_post_id,
        url=wordpress_url,
        tag_count=len(tags),
        has_image=bool(featured_image_path),
    )
    print(f"[wordpress-draft] done {duration:.1f}s url={wordpress_url}")
    return {
        "wordpress_post_id": wordpress_post_id,
        "wordpress_url": wordpress_url,
        "wordpress_skipped": False,
        "wordpress_bundle_path": bundle_paths["bundle_dir"],
    }
