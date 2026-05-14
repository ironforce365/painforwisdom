"""Scrape the latest YouTube Shorts metadata from @painforwisdom and refresh
``config/youtube_metadata.json`` with the union of their tags.

Requires ``yt-dlp`` on PATH. Install via ``pipx install yt-dlp``.

Usage:
    python scripts/scrape_youtube_tags.py                # scrape last 20 shorts
    python scripts/scrape_youtube_tags.py --limit 50     # custom window
    python scripts/scrape_youtube_tags.py --channel @foo # override channel
    python scripts/scrape_youtube_tags.py --dry-run      # print, do not write
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set

DEFAULT_CHANNEL = "@painforwisdom"
DEFAULT_LIMIT = 20
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "youtube_metadata.json"


def _which_yt_dlp() -> str:
    try:
        result = subprocess.run(
            ["yt-dlp", "--version"], check=True, capture_output=True, timeout=10
        )
        return result.stdout.decode().strip()
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        print(
            "✗ yt-dlp not available on PATH. Install via: pipx install yt-dlp",
            file=sys.stderr,
        )
        sys.exit(2)


def _scrape(channel: str, limit: int) -> List[Dict]:
    url = f"https://www.youtube.com/{channel}/shorts"
    cmd = [
        "yt-dlp",
        "-j",
        "--skip-download",
        "--playlist-end",
        str(limit),
        "--ignore-errors",
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0 and not proc.stdout.strip():
        print(f"✗ yt-dlp failed (rc={proc.returncode}):", file=sys.stderr)
        print(proc.stderr[-2000:], file=sys.stderr)
        sys.exit(2)

    entries: List[Dict] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _union_tags(entries: List[Dict]) -> List[str]:
    seen: Set[str] = set()
    ordered: List[str] = []
    for entry in entries:
        tags = entry.get("tags") or []
        if not isinstance(tags, list):
            continue
        for tag in tags:
            if not isinstance(tag, str):
                continue
            tag = tag.strip()
            if not tag:
                continue
            key = tag.lower()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(tag)
    return ordered


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", default=DEFAULT_CHANNEL)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    print(f"yt-dlp: {_which_yt_dlp()}")
    print(f"Scraping {args.limit} shorts from {args.channel}...")
    entries = _scrape(args.channel, args.limit)
    print(f"  parsed {len(entries)} shorts metadata entries.")

    tags = _union_tags(entries)
    print(f"  tag union size: {len(tags)}")
    if not tags:
        print("✗ No tags found — channel may have no tagged shorts or yt-dlp output changed.")
        return 1

    print("Top tags:")
    for tag in tags[:25]:
        print(f"  - {tag}")

    if args.dry_run:
        print("--dry-run: not writing config.")
        return 0

    if not CONFIG_PATH.is_file():
        config: Dict = {
            "default_tags": [],
            "default_description_suffix": "",
            "default_category_id": "22",
            "default_privacy_status": "private",
            "scraped_at": None,
            "scraped_from_channel": args.channel,
        }
    else:
        config = json.loads(CONFIG_PATH.read_text())

    config["default_tags"] = tags
    config["scraped_at"] = datetime.now(timezone.utc).isoformat()
    config["scraped_from_channel"] = args.channel
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n")
    print(f"✓ Wrote {len(tags)} tags to {CONFIG_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
