"""Backfill WordPress drafts from the Notion "Blog post pending publications" DB.

Goal: rotate the long tail of unpublished Notion entries into WordPress
drafts at a controlled cadence, **strictly in chronological order** so the
internal post-to-post references planted by the writer stay coherent (an
older post can't reference a newer one that hasn't been drafted yet).

Behaviour:
  - Query unpublished Notion pages, sorted by ``Date`` ascending.
  - For each page (serial, single-threaded):
      1. Skip pages that already have ``WordPress URL`` set.
      2. Try to locate a source video under ``processed/*/source/*`` whose
         date prefix matches the Notion page Date — used for the featured
         image. Missing video ⇒ no featured image (warn, continue).
      3. Convert the Notion page body back to markdown, build a 50-word
         excerpt if absent, render WordPress HTML, upload media, create
         the draft.
      4. Patch the Notion page with ``WordPress URL`` and
         ``Status="Draft Created"``.
  - Halts on the first non-network failure so the loop can be resumed
    cleanly from the same row.

Flags:
  --limit N        cap how many pages to process this run (default: 1)
  --dry-run        do everything but the WP write + Notion update
  --profile NAME   prod (default) / sandbox — selects .env file
  --status FOO     extra filter: only rows whose Status select == FOO

Usage examples:
    python -m pipeline.backfill_wordpress --profile prod --limit 1 --dry-run
    python -m pipeline.backfill_wordpress --profile prod --limit 1
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _peek_profile(argv: List[str]) -> str:
    for i, a in enumerate(argv):
        if a == "--profile" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--profile="):
            return a.split("=", 1)[1]
    return "prod"


_PROFILE = _peek_profile(sys.argv[1:])
_ENV_FILE = ".env.sandbox" if _PROFILE == "sandbox" else ".env"

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / _ENV_FILE)


# Heavy imports come after env load.
from pipeline.image_extractor import (  # noqa: E402
    ImageExtractionError,
    pick_and_resize_best,
)
from pipeline.notion_client import (  # noqa: E402
    extract_property,
    get_blog_page_markdown,
    page_id,
    query_blog_pages,
    update_blog_page_wordpress_url,
)
from pipeline.wordpress_client import (  # noqa: E402
    WordPressClient,
    WordPressClientError,
    first_50_words,
    render_wp_html,
)


PROCESSED_ROOT = PROJECT_ROOT / "processed"
BACKFILL_ROOT = PROCESSED_ROOT / "_backfill"


def _yyyymmdd_to_mmddyy(date_iso: str) -> str:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", date_iso or "")
    if not m:
        return date_iso or ""
    return f"{m.group(2)}/{m.group(3)}/{m.group(1)[2:]}"


def _find_source_video(video_date: str) -> Optional[Path]:
    """Return the source video archived for the given ``YYYY-MM-DD`` if any.

    The archive lives at ``processed/<run_id>/source/<file>`` (see
    ``run.py`` archival on success). Multiple runs per day are possible
    (re-process); the first match wins. No video matches return None.
    """
    if not video_date:
        return None
    runs = sorted(PROCESSED_ROOT.glob(f"{video_date}_*"))
    for run_dir in runs:
        source_dir = run_dir / "source"
        if not source_dir.is_dir():
            continue
        for candidate in sorted(source_dir.iterdir()):
            if candidate.is_file() and candidate.suffix.lower() in {
                ".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"
            }:
                return candidate
    return None


def _slug_for_backfill(page_id_str: str) -> str:
    return re.sub(r"[^a-z0-9]", "", page_id_str.lower())[:16] or "page"


def _is_published(page: Dict[str, Any]) -> bool:
    return bool(extract_property(page, "Published?"))


def _existing_wordpress_url(page: Dict[str, Any]) -> str:
    return str(extract_property(page, "WordPress URL") or "")


def _existing_excerpt(page: Dict[str, Any]) -> str:
    return str(extract_property(page, "Excerpt") or "")


def _process_one(
    page: Dict[str, Any],
    *,
    dry_run: bool,
) -> Dict[str, Any]:
    pid = page_id(page)
    title = str(extract_property(page, "Title") or "")
    video_date = str(extract_property(page, "Date") or "")
    date_mmddyy = _yyyymmdd_to_mmddyy(video_date)
    wp_url_already = _existing_wordpress_url(page)
    excerpt_already = _existing_excerpt(page)

    print(f"\n── processing {pid} [{video_date}] {title!r}")

    if wp_url_already:
        print(f"   already has WordPress URL: {wp_url_already}; skipping.")
        return {"status": "skipped-already-wp", "page_id": pid, "url": wp_url_already}

    body_md = get_blog_page_markdown(pid)
    if not body_md.strip():
        return {"status": "skipped-empty-body", "page_id": pid}

    excerpt = excerpt_already or first_50_words(body_md)

    # Locate the source video, if any, and extract a featured image.
    video_path = _find_source_video(video_date)
    bundle_dir = BACKFILL_ROOT / _slug_for_backfill(pid)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    featured_path: Optional[Path] = None
    if video_path is not None:
        try:
            featured_path, score, _ = pick_and_resize_best(
                video_path, bundle_dir / "featured.jpg"
            )
            print(f"   featured frame from {video_path.name} (score={score:.1f})")
        except ImageExtractionError as exc:
            print(f"   WARN image extraction failed: {exc}")
            featured_path = None
    else:
        print("   WARN no source video archived for this date; posting without featured image.")

    html = render_wp_html(body_md=body_md, date_mmddyy=date_mmddyy)
    (bundle_dir / "post.md").write_text(body_md)
    (bundle_dir / "post.html").write_text(html)
    (bundle_dir / "meta.json").write_text(
        json.dumps(
            {
                "page_id": pid,
                "title": title,
                "video_date": video_date,
                "date_mmddyy": date_mmddyy,
                "excerpt": excerpt,
                "featured_image": str(featured_path) if featured_path else "",
            },
            indent=2,
        )
    )

    if dry_run:
        print(f"   --dry-run: bundle written to {bundle_dir}; not posting to WP/Notion.")
        return {"status": "dry-run", "page_id": pid, "bundle": str(bundle_dir)}

    # Live publish path.
    with WordPressClient() as wp:
        featured_media_id: Optional[int] = None
        if featured_path is not None:
            media = wp.upload_media(featured_path, alt_text=title)
            featured_media_id = media.media_id
        post = wp.create_draft_post(
            title=title,
            html=html,
            excerpt=excerpt,
            tags=["painforwisdom", "backfill"],
            featured_media_id=featured_media_id,
        )

    update_blog_page_wordpress_url(
        pid,
        post.url,
        status=("Published" if _is_published(page) else "Draft Created"),
        excerpt=excerpt,
    )
    print(f"   ✓ WordPress draft: {post.url}")
    return {
        "status": "drafted",
        "page_id": pid,
        "url": post.url,
        "wp_post_id": post.post_id,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["prod", "sandbox"], default="prod")
    parser.add_argument("--limit", type=int, default=1, help="max pages to process this run")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--status",
        default=None,
        help="optional Status select filter (e.g. 'Pending')",
    )
    args = parser.parse_args(argv)

    BACKFILL_ROOT.mkdir(parents=True, exist_ok=True)

    extra_filter: Optional[Dict[str, Any]] = None
    if args.status:
        extra_filter = {"property": "Status", "select": {"equals": args.status}}

    pages = list(query_blog_pages(only_unpublished=True, sort_date_asc=True, extra_filter=extra_filter))
    print(f"Found {len(pages)} unpublished blog pages (sorted oldest first).")

    processed = 0
    results: List[Dict[str, Any]] = []
    for page in pages:
        if processed >= args.limit:
            break
        try:
            result = _process_one(page, dry_run=args.dry_run)
        except WordPressClientError as exc:
            print(f"   ✗ WordPress error: {exc}\n   halting to preserve chronological order.")
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"   ✗ unexpected error: {type(exc).__name__}: {exc}\n   halting.")
            return 1
        results.append(result)
        processed += 1
        time.sleep(1.0)

    summary_path = BACKFILL_ROOT / "last_run_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "profile": args.profile,
                "dry_run": args.dry_run,
                "limit": args.limit,
                "processed": processed,
                "results": results,
            },
            indent=2,
        )
    )
    print(f"\nProcessed {processed} page(s). Summary: {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
