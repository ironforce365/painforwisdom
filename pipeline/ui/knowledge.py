"""What the videos have been about, read from the vault's entries (spec 2026-11-06-console-dashboards). Read only."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values

from pipeline import runtime
from pipeline.ui.settings import Settings

_HEADING = re.compile(r"^#\s+(?:\d{4}-\d{2}-\d{2}\s+[-\u2013\u2014]\s+)?(.+?)\s*$", re.MULTILINE)
_DATE = re.compile(r"^\*\*Date:\*\*\s*(\S+)", re.MULTILINE)
_THEMES = re.compile(r"^\*\*Themes:\*\*(.*)$", re.MULTILINE)
_LINK = re.compile(r"\[\[([^\]|]+)")
_SLUG_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def vault_for(settings: Settings) -> Path:
    """The setting, else the profile's VAULT_PATH: the file the runner of that profile reads, without entering this env."""
    if settings.vault_root:
        return settings.vault_root
    env_file = runtime.STATE_ROOT / (".env.sandbox" if settings.profile == "sandbox" else ".env")
    path = dotenv_values(env_file).get("VAULT_PATH")
    return Path(path) if path else runtime.VAULT_PATH


def _entries_dir(vault: Path) -> Path:
    return vault / "gonzalo-book" / "entries"


def _parse(path: Path) -> dict:
    text = path.read_text(errors="replace")
    heading, date, themes = _HEADING.search(text), _DATE.search(text), _THEMES.search(text)
    slug_date = _SLUG_DATE.match(path.stem)
    return {
        "slug": path.stem,
        "title": heading.group(1) if heading else path.stem,
        "date": date.group(1) if date else (slug_date.group(0) if slug_date else None),
        "themes": _LINK.findall(themes.group(1)) if themes else [],
    }


def overview(vault: Path, limit: Optional[int] = None) -> dict:
    folder = _entries_dir(vault)
    paths = sorted(folder.glob("*.md"), key=lambda p: p.stem, reverse=True) if folder.is_dir() else []
    entries = [_parse(p) for p in paths]
    counts = Counter(theme for e in entries for theme in e["themes"])
    return {
        "entries": entries[:limit] if limit else entries,
        "themes": [{"name": name, "entries": n} for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))],
        "entry_count": len(entries),
        "theme_count": len(counts),
    }


def entry(vault: Path, slug: str) -> Optional[str]:
    """Only a plain name in the entries folder: the slug comes from the URL."""
    if slug != Path(slug).name or slug.startswith("."):
        return None
    path = _entries_dir(vault) / f"{slug}.md"
    return path.read_text(errors="replace") if path.is_file() else None
