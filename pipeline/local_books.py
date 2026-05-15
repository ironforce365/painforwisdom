"""Local book inventory — single source of truth for `books/` lookups.

Two callers:
- `pipeline.nodes.research` — pre-LLM (render inventory into prompt) and
  post-LLM (override a Book row's web URL with a `file://` path on match).
- `pipeline.scripts.augment_research_tasks` — retro pass over Notion rows
  flagged unreachable by the audit.

The inventory has three sub-locations under `books/`:

- `books/<slug>/` — manually-curated folders. Title slug matches `folder.name`.
- `books/raw/` — z-library bridge downloads. Filename pattern
  `AuthorChunk_TitleChunkCamelCase_YYYY_lang_id.ext`.
- `books/extracted/` — RAG-ready text extractions from the bridge.

A reference is considered locally available if the requested (title, author)
matches an entry under any of these via token overlap. Author lastname must
overlap; title token overlap ratio (intersection / size of smaller token set)
must be >= 0.5.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BOOKS_ROOT = PROJECT_ROOT / "books"

BOOK_SUFFIXES = frozenset({".epub", ".pdf", ".txt", ".mobi", ".azw3"})
SKIP_SUBDIRS = frozenset({"raw", "extracted"})
TITLE_STOPWORDS = frozenset(
    {
        "the", "a", "an", "and", "of", "to", "in", "on", "for", "by",
        "is", "with", "from", "or", "as", "at", "it",
    }
)
TITLE_MATCH_THRESHOLD = 0.5


@dataclass(frozen=True)
class LocalBook:
    """One discovered local book file with display metadata for the prompt."""

    path: Path
    display_title: str
    display_author: str

    @property
    def file_url(self) -> str:
        return f"file://{self.path}"


def _split_camelcase(s: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", s)


def _tokenize(s: str) -> set[str]:
    return {
        t
        for t in re.findall(r"[a-z0-9]+", s.lower())
        if t not in TITLE_STOPWORDS and len(t) > 1
    }


def _title_slug_candidates(title: str) -> list[str]:
    """Mirror of augment_research_tasks._title_slug_candidates — broadest first."""
    candidates: list[str] = []

    def add(text: str) -> None:
        slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80]
        if slug and slug not in candidates:
            candidates.append(slug)

    add(title)
    no_chapters = re.split(r"\s+--\s+|\s+—\s+", title, maxsplit=1)[0]
    no_chapters = re.sub(r"\bch\.?\s*\d+\b", "", no_chapters, flags=re.IGNORECASE)
    add(no_chapters)
    no_subtitle = re.split(r":\s+", no_chapters, maxsplit=1)[0]
    add(no_subtitle)
    return candidates


def _parse_zlib_filename(stem: str) -> Optional[tuple[set[str], set[str], str, str]]:
    """Return (author_tokens, title_tokens, display_author, display_title) or None.

    Z-Library raw filename pattern: AuthorChunk_TitleChunk_YYYY_lang_id
    where AuthorChunk and TitleChunk are CamelCase concatenations.
    """
    parts = stem.split("_")
    if len(parts) < 2:
        return None
    author_raw = _split_camelcase(parts[0])
    title_raw = _split_camelcase(parts[1])
    author_tokens = _tokenize(author_raw)
    title_tokens = _tokenize(title_raw)
    return author_tokens, title_tokens, author_raw.strip(), title_raw.strip()


def _title_match_ratio(req: set[str], cand: set[str]) -> float:
    if not req or not cand:
        return 0.0
    return len(req & cand) / min(len(req), len(cand))


def _author_match(req: set[str], cand: set[str]) -> bool:
    if not req:
        return True
    return bool(req & cand)


def _iter_book_files(folder: Path) -> Iterable[Path]:
    if not folder.is_dir():
        return
    for f in folder.iterdir():
        if f.is_file() and f.suffix.lower() in BOOK_SUFFIXES:
            yield f


def _scan_curated(books_root: Path) -> list[LocalBook]:
    """One LocalBook per non-skip subfolder containing a book file."""
    if not books_root.is_dir():
        return []
    out: list[LocalBook] = []
    for sub in sorted(books_root.iterdir()):
        if not sub.is_dir() or sub.name in SKIP_SUBDIRS:
            continue
        files = list(_iter_book_files(sub))
        if not files:
            continue
        chosen = max(files, key=lambda f: f.stat().st_size)
        parsed = _parse_zlib_filename(chosen.stem)
        if parsed:
            _, _, author, title = parsed
        else:
            author = ""
            title = sub.name.replace("-", " ").title()
        out.append(LocalBook(path=chosen, display_title=title, display_author=author))
    return out


def _scan_raw(books_root: Path) -> list[LocalBook]:
    raw = books_root / "raw"
    out: list[LocalBook] = []
    for f in sorted(_iter_book_files(raw)):
        parsed = _parse_zlib_filename(f.stem)
        if not parsed:
            continue
        _, _, author, title = parsed
        out.append(LocalBook(path=f, display_title=title, display_author=author))
    return out


def scan_inventory(books_root: Path = DEFAULT_BOOKS_ROOT) -> list[LocalBook]:
    """All books available locally, curated first then raw dumps."""
    return _scan_curated(books_root) + _scan_raw(books_root)


def find_local_book(
    title: str,
    author: str,
    books_root: Path = DEFAULT_BOOKS_ROOT,
) -> Optional[LocalBook]:
    """Best match for (title, author) in local inventory, or None.

    Priority: curated `books/<slug>/` folders (slug-exact), then `books/raw/`
    (token-overlap with author check).
    """
    req_title = _tokenize(title)
    req_author = _tokenize(author)

    # 1. Curated folder exact slug.
    for slug in _title_slug_candidates(title):
        folder = books_root / slug
        if not folder.is_dir() or folder.name in SKIP_SUBDIRS:
            continue
        files = list(_iter_book_files(folder))
        if not files:
            continue
        chosen = max(files, key=lambda f: f.stat().st_size)
        parsed = _parse_zlib_filename(chosen.stem)
        if parsed:
            _, _, disp_author, disp_title = parsed
        else:
            disp_author = author
            disp_title = title
        return LocalBook(path=chosen, display_title=disp_title, display_author=disp_author)

    # 2. books/raw/ fuzzy match.
    raw = books_root / "raw"
    if not raw.is_dir():
        return None
    best: Optional[LocalBook] = None
    best_score: float = 0.0
    for f in _iter_book_files(raw):
        parsed = _parse_zlib_filename(f.stem)
        if not parsed:
            continue
        cand_author, cand_title, disp_author, disp_title = parsed
        if not _author_match(req_author, cand_author):
            continue
        score = _title_match_ratio(req_title, cand_title)
        if score >= TITLE_MATCH_THRESHOLD and score > best_score:
            best = LocalBook(path=f, display_title=disp_title, display_author=disp_author)
            best_score = score
    return best


def render_inventory_for_prompt(books_root: Path = DEFAULT_BOOKS_ROOT) -> str:
    """Compact block for the research-curator user message.

    Empty string when inventory is empty (test envs) so the prompt skips it
    cleanly.
    """
    inv = scan_inventory(books_root)
    if not inv:
        return ""
    lines = [
        "## LOCAL BOOK INVENTORY",
        (
            "These books are already downloaded. If your reference is one of "
            "them — or a chapter from one — set `Source URL` to the `file://` "
            "path below, `Reachable=yes`, `Reachability Reason=local-curated`. "
            "Do NOT propose a web URL for these."
        ),
        "",
    ]
    for book in inv:
        author = f" — {book.display_author}" if book.display_author else ""
        lines.append(f"- {book.display_title}{author}")
        lines.append(f"  -> {book.file_url}")
    return "\n".join(lines)
