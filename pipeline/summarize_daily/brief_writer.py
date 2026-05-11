"""Brief writer for the daily summarizer.

Translates a `Cluster` from `clusterer` into a v2 PoC-format cluster dict, caches
each source's text, then delegates rendering to the existing `poc_brief_v2.run_cluster`.

Output: `briefs/<theme>/<YYYY-MM-DD>--<sub-angle-slug>/{deep-dive,application,audio-prompts}.md`
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict

from pipeline.runtime import PROJECT_ROOT
from pipeline.scripts.poc_brief_v2 import run_cluster
from pipeline.summarize_daily.clusterer import Cluster
from pipeline.summarize_daily.fetcher import fetch as fetch_url


CACHE_DIR = PROJECT_ROOT / "briefs" / ".cache"


def _slug_for_source(row: Dict[str, Any]) -> str:
    url = row.get("alt_source_url") or row.get("source_url") or ""
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    return f"daily-{digest}"


def _ensure_source_cached(row: Dict[str, Any]) -> str:
    """Fetch the row's effective URL, write cleaned text into the per-row
    cache slug the v2 PoC will read. Returns the slug."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slug_for_source(row)
    cache_file = CACHE_DIR / f"{slug}.txt"
    if cache_file.exists() and cache_file.stat().st_size > 0:
        return slug
    url = row.get("alt_source_url") or row.get("source_url") or ""
    text = fetch_url(url)
    cache_file.write_text(text)
    return slug


def cluster_to_v2_dict(cluster: Cluster) -> Dict[str, Any]:
    sources = []
    for r in cluster.rows:
        slug = _ensure_source_cached(r)
        sources.append(
            {
                "slug": slug,
                "title": r.get("title", ""),
                "author_host": r.get("author_host", ""),
                "type": r.get("type", ""),
                "specific_location": r.get("specific_location", ""),
                "url": r.get("alt_source_url") or r.get("source_url") or "",
                "research_angle": r.get("research_angle", "") or cluster.sub_angle,
                "relevance": r.get("relevance", ""),
            }
        )

    framing = (
        f"Daily brief for the `{cluster.theme}` theme on the angle "
        f"'{cluster.sub_angle}'. {len(sources)} reachable references "
        f"selected from Notion's research queue, anchored to Gonzalo's vault "
        f"entry `{cluster.vault_entry}`."
    )
    return {
        "theme": cluster.theme,
        "sub_angle": cluster.sub_angle.replace("-", " "),
        "framing": framing,
        "vault_entry": cluster.vault_entry,
        "sources": sources,
    }


def write_brief(cluster: Cluster) -> Path:
    cluster_dict = cluster_to_v2_dict(cluster)
    run_cluster(cluster_dict)
    # v2 PoC writes briefs/<theme>/<date>--<sub-slug>/
    # Resolve the same path here so the caller can return it.
    from datetime import date
    import re

    def _slug(s: str) -> str:
        s = s.lower()
        s = re.sub(r"[^a-z0-9]+", "-", s)
        return s.strip("-")[:50]

    today = date.today().isoformat()
    sub_slug = _slug(cluster_dict["sub_angle"])
    return PROJECT_ROOT / "briefs" / cluster.theme / f"{today}--{sub_slug}"
