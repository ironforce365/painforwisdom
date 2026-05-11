"""Phase 3d — build per-theme saturation stats for curator injection.

Reads every Research Tasks row from Notion, groups by `Coaching Theme`, counts
pending vs summarized, flags themes ≥ saturation_threshold (default 30) and
emits a precomputed `covered_angles` list (cheap LLM dedupe pass per saturated
theme — cached on disk).

Output: `pipeline/state/theme_stats.json`. The research node loads this and
injects it into the curator's prompt so saturated themes get the
"propose novel angle OR sub-theme split OR skip" rule.

Run:
    python -m pipeline.scripts.build_theme_stats             # full build
    python -m pipeline.scripts.build_theme_stats --offline   # skip LLM dedupe
    python -m pipeline.scripts.build_theme_stats --refresh-angles  # ignore cache
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from pipeline.llm import call_llm  # noqa: E402
from pipeline.notion_client import extract_property, query_research_tasks  # noqa: E402


SATURATION_THRESHOLD = 30
STATE_DIR = PROJECT_ROOT / "pipeline" / "state"
STATS_PATH = STATE_DIR / "theme_stats.json"
ANGLES_CACHE_DIR = STATE_DIR / "covered_angles"


DEDUPE_SYSTEM = """You are deduplicating research-angle slugs to a canonical list.

Given a list of raw research-angle strings — many of which are minor variants of
each other (different word order, plural/singular, hyphenation) — return a
canonical deduplicated list. Use the most common phrasing as the canonical form.
Be aggressive: if two angles describe the same concept at the same level, merge.

Output: one canonical angle per line, no preamble, no numbering, no commentary.
"""


def _dedupe_angles(raw_angles: list[str], model: str = "claude-sonnet-4-6") -> list[str]:
    if not raw_angles:
        return []
    deduped = sorted(set(a.strip() for a in raw_angles if a and a.strip()))
    if len(deduped) <= 1:
        return deduped
    user_msg = "Raw angles (deduplicate to canonical list):\n" + "\n".join(
        f"- {a}" for a in deduped
    )
    result = call_llm(
        model=model,
        system_prompt=DEDUPE_SYSTEM,
        user_message=user_msg,
        max_tokens=2000,
    )
    text = result.get("text", "")
    out = [line.lstrip("- ").strip() for line in text.splitlines() if line.strip()]
    return out or deduped


def _theme_dedupe_cache_path(theme: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in theme)
    return ANGLES_CACHE_DIR / f"{safe}.json"


def collect_theme_data() -> Dict[str, Dict[str, Any]]:
    """Return raw per-theme buckets: pending/summarized/total/angles."""
    by_theme: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"pending": 0, "summarized": 0, "total": 0, "angles": []}
    )
    for page in query_research_tasks(page_size=100):
        theme = extract_property(page, "Coaching Theme") or "(blank)"
        status = extract_property(page, "Status") or ""
        angle = extract_property(page, "Research Angle") or ""
        bucket = by_theme[theme]
        bucket["total"] += 1
        if status in ("Summarized", "Done"):
            bucket["summarized"] += 1
        else:
            bucket["pending"] += 1
        if angle:
            bucket["angles"].append(angle)
    return dict(by_theme)


def build_stats(*, offline: bool, refresh_angles: bool) -> Dict[str, Any]:
    raw = collect_theme_data()
    ANGLES_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    themes_out: Dict[str, Any] = {}
    for theme, bucket in raw.items():
        saturated = bucket["total"] >= SATURATION_THRESHOLD
        cache_path = _theme_dedupe_cache_path(theme)
        covered_angles: list[str] = []
        if saturated and not offline:
            if cache_path.exists() and not refresh_angles:
                covered_angles = json.loads(cache_path.read_text()).get("angles", [])
            else:
                covered_angles = _dedupe_angles(bucket["angles"])
                cache_path.write_text(
                    json.dumps({"angles": covered_angles, "ts": int(time.time())}, indent=2)
                )
        elif saturated:
            covered_angles = sorted(set(a for a in bucket["angles"] if a))[:50]
        else:
            covered_angles = sorted(set(bucket["angles"]))[:20]

        themes_out[theme] = {
            "pending": bucket["pending"],
            "summarized": bucket["summarized"],
            "total": bucket["total"],
            "saturated": saturated,
            "covered_angles": covered_angles,
        }

    return {
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "saturation_threshold": SATURATION_THRESHOLD,
        "themes": themes_out,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Skip LLM dedupe.")
    parser.add_argument("--refresh-angles", action="store_true", help="Ignore covered-angles cache.")
    parser.add_argument("--out", type=Path, default=STATS_PATH)
    args = parser.parse_args(argv)

    print(f"Building theme stats (offline={args.offline}, refresh={args.refresh_angles})...")
    stats = build_stats(offline=args.offline, refresh_angles=args.refresh_angles)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(stats, indent=2))

    print(f"\nWrote {args.out}")
    saturated_count = sum(1 for t in stats["themes"].values() if t["saturated"])
    print(f"Themes: {len(stats['themes'])}  saturated: {saturated_count}  threshold: {stats['saturation_threshold']}")
    print("\nTop pending themes:")
    for theme, info in sorted(stats["themes"].items(), key=lambda kv: -kv[1]["pending"])[:10]:
        flag = "★" if info["saturated"] else " "
        print(f"  {flag} {theme:50s} pending={info['pending']:3d}  total={info['total']:3d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
