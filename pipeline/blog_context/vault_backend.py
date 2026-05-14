"""Vault-backed cross-post context.

Scans the Obsidian vault entries directory once per process, caches an
inverted index in memory, and answers two questions:

  - ``find_references(topic)``: which past entries mention this topic?
  - ``recent_topics(limit)``:   which themes have been hit most lately?

Ranking: matches are ranked first by recency (newest entry first), then by
title proximity (topic appearing in the title or first section beats deep
body matches). This is intentionally simple — when Gonzalo wires
AnythingLLM, embedding similarity will subsume it.

Vault entry layout (current):

    # 2026-04-29 — Title
    **Date:** 2026-04-29
    **Themes:** [[deliberate-discomfort]], [[honest-self-assessment]]
    **Frameworks:** [[strategic-vs-manufactured-suffering]]

    ## Core Insight
    ...
    ## Story Anchor
    ...
"""
from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional

from pipeline.blog_context import Reference, Topic


@dataclass
class _VaultEntry:
    path: Path
    slug: str
    date: str
    title: str
    themes: List[str] = field(default_factory=list)
    frameworks: List[str] = field(default_factory=list)
    body: str = ""

    @property
    def lowercase_body(self) -> str:
        return self.body.lower()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _vault_root() -> Path:
    override = os.environ.get("VAULT_PATH")
    if override:
        return Path(override) / "gonzalo-book"
    return _project_root() / "obsidian-vault" / "gonzalo-book"


_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_DATE_RE = re.compile(r"\*\*Date:\*\*\s*(\d{4}-\d{2}-\d{2})")
_THEMES_RE = re.compile(r"\*\*Themes:\*\*\s*(.+)")
_FRAMEWORKS_RE = re.compile(r"\*\*Frameworks:\*\*\s*(.+)")
_FILENAME_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-")


def _parse_entry(path: Path) -> Optional[_VaultEntry]:
    try:
        text = path.read_text()
    except OSError:
        return None

    title_match = re.search(r"^#\s+(.+)", text, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else path.stem

    date = ""
    m = _DATE_RE.search(text)
    if m:
        date = m.group(1)
    if not date:
        m2 = _FILENAME_DATE_RE.match(path.name)
        if m2:
            date = m2.group(1)

    themes: List[str] = []
    m = _THEMES_RE.search(text)
    if m:
        themes = _WIKILINK_RE.findall(m.group(1))

    frameworks: List[str] = []
    m = _FRAMEWORKS_RE.search(text)
    if m:
        frameworks = _WIKILINK_RE.findall(m.group(1))

    return _VaultEntry(
        path=path,
        slug=path.stem,
        date=date,
        title=title,
        themes=themes,
        frameworks=frameworks,
        body=text,
    )


class VaultBackend:
    """Reads vault entries and provides reference / topic lookups."""

    _cache_lock = Lock()
    _cached_entries: Optional[List[_VaultEntry]] = None
    _cached_root: Optional[Path] = None

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = root or _vault_root()

    def _load_entries(self) -> List[_VaultEntry]:
        with VaultBackend._cache_lock:
            if (
                VaultBackend._cached_entries is not None
                and VaultBackend._cached_root == self.root
            ):
                return VaultBackend._cached_entries
            entries_dir = self.root / "entries"
            entries: List[_VaultEntry] = []
            if entries_dir.is_dir():
                for path in sorted(entries_dir.glob("*.md")):
                    parsed = _parse_entry(path)
                    if parsed is not None:
                        entries.append(parsed)
            # Newest first.
            entries.sort(key=lambda e: e.date, reverse=True)
            VaultBackend._cached_entries = entries
            VaultBackend._cached_root = self.root
            return entries

    @staticmethod
    def invalidate_cache() -> None:
        with VaultBackend._cache_lock:
            VaultBackend._cached_entries = None
            VaultBackend._cached_root = None

    def find_references(self, topic: str, *, limit: int = 5) -> List[Reference]:
        if not topic:
            return []
        needle = topic.strip().lower()
        topic_slug = re.sub(r"[^a-z0-9]+", "-", needle).strip("-")
        entries = self._load_entries()

        results: List[Reference] = []
        for entry in entries:
            score = 0.0
            snippet_line = ""

            # Theme / framework slug match is the strongest signal.
            if topic_slug and (
                topic_slug in entry.themes or topic_slug in entry.frameworks
            ):
                score += 3.0
                snippet_line = (
                    "themes: " + ", ".join(entry.themes)
                    if entry.themes
                    else "frameworks: " + ", ".join(entry.frameworks)
                )

            # Title match beats deep body match.
            if needle in entry.title.lower():
                score += 2.0
                if not snippet_line:
                    snippet_line = entry.title

            # Substring scan over the body — first hit wins for the snippet.
            if score == 0 or not snippet_line:
                body_lc = entry.lowercase_body
                idx = body_lc.find(needle)
                if idx >= 0:
                    score += 1.0
                    snippet_start = max(0, idx - 80)
                    snippet_end = min(len(entry.body), idx + len(needle) + 80)
                    snippet_line = entry.body[snippet_start:snippet_end].replace("\n", " ").strip()

            if score == 0:
                continue

            results.append(
                Reference(
                    source="vault",
                    title=entry.title,
                    slug=entry.slug,
                    date=entry.date,
                    snippet=snippet_line[:280],
                    uri=str(entry.path),
                    score=score,
                )
            )

        # Stable sort: recency first (entries are pre-sorted), then by score
        # descending for entries on the same day.
        results.sort(key=lambda r: (r.date, r.score), reverse=True)
        return results[:limit]

    def recent_topics(self, limit: int = 10) -> List[Topic]:
        entries = self._load_entries()
        counter: Counter[str] = Counter()
        last_seen: Dict[str, str] = {}
        for entry in entries:
            for slug in entry.themes:
                counter[slug] += 1
                if slug not in last_seen or entry.date > last_seen[slug]:
                    last_seen[slug] = entry.date
        return [
            Topic(name=slug, count=count, last_seen=last_seen.get(slug, ""))
            for slug, count in counter.most_common(limit)
        ]
