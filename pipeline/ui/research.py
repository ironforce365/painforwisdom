"""Research items from research_report.csv, with a link the console can open (spec 2026-11-05-console-end-to-end section 3)."""
from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlparse

_COLUMNS = {"title": "title", "type": "type", "author": "author/host", "location": "specific location",
            "category": "category", "angle": "research angle", "relevance": "relevance", "paywall": "paywall",
            "theme": "coaching theme", "reachable": "reachable", "reason": "reachability reason"}
_URL_COLUMNS = ("source url", "url")  # the short form is the fake runner's and the older fixtures'
_LOCAL_TYPES = {".pdf": "application/pdf", ".txt": "text/plain; charset=utf-8", ".md": "text/markdown; charset=utf-8",
                ".epub": "application/epub+zip"}


def _rows(rd: Path) -> list[dict[str, str]]:
    path = rd / "research-curator" / "research_report.csv"
    if not path.is_file():
        return []
    reader = csv.DictReader(io.StringIO(path.read_text(errors="replace")))
    return [{(k or "").strip().lower(): (v or "") for k, v in row.items() if isinstance(v, (str, type(None)))}
            for row in reader]


def _url(row: dict[str, str]) -> str:
    return next((row[c].strip() for c in _URL_COLUMNS if row.get(c, "").strip()), "")


def _local(url: str, books_root: Path) -> Optional[Path]:
    """The file a `file://` URL names, only when it really is inside the library: resolve() follows symlinks and `..`."""
    if not url.startswith("file://") or "\0" in url:
        return None
    root = books_root.resolve()
    path = (root / url[len("file://"):]).resolve()
    return path if path.is_relative_to(root) and path.is_file() else None


def _link(url: str, books_root: Path, run_id: str, n: int) -> dict:
    parsed = urlparse(url)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return {"kind": "http", "href": url}
    if _local(url, books_root):
        return {"kind": "local", "href": f"/api/runs/{quote(run_id, safe='')}/research/{n}/file"}
    return {"kind": "none", "href": None}


def items(rd: Path, books_root: Path) -> list[dict]:
    run_id = rd.parent.name
    return [{"n": n, **{k: row.get(col, "") for k, col in _COLUMNS.items()}, "link": _link(_url(row), books_root, run_id, n)}
            for n, row in enumerate(_rows(rd), start=1)]


def local_file(rd: Path, books_root: Path, n: int) -> Optional[Path]:
    rows = _rows(rd)
    return _local(_url(rows[n - 1]), books_root) if 1 <= n <= len(rows) else None


def media_type(path: Path) -> str:
    """Anything else (an html book) downloads: it must not run as this origin."""
    return _LOCAL_TYPES.get(path.suffix.lower(), "application/octet-stream")
