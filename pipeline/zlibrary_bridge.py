"""Z-Library bridge — subprocess adapter for the zlibrary-mcp Python bridge.

Wraps the zlibrary-mcp Python bridge as a callable:
`fetch(title, author) -> BookText | BookFailure`.

Configuration via env vars (no hardcoded paths — works on any host):
- `ZLIBRARY_REPO_PATH` (required): absolute path to a local zlibrary-mcp checkout.
  Bridge is not configured if unset; `fetch()` returns `BookFailure("not-configured")`.
- `ZLIBRARY_EMAIL`, `ZLIBRARY_PASSWORD` (required): Z-Library credentials.

Setup once:
    git clone <zlibrary-mcp repo> $ZLIBRARY_REPO_PATH
    cd $ZLIBRARY_REPO_PATH
    # populate .env with ZLIBRARY_EMAIL + ZLIBRARY_PASSWORD
    bash setup-uv.sh && npm install && npm run build

Usage:
    from pipeline.zlibrary_bridge import fetch
    result = fetch("The Comfort Crisis", "Michael Easter")
    if isinstance(result, BookText):
        ...
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Union


_ZLIBRARY_REPO_RAW = os.environ.get("ZLIBRARY_REPO_PATH", "").strip()
ZLIBRARY_REPO: Path | None = Path(_ZLIBRARY_REPO_RAW) if _ZLIBRARY_REPO_RAW else None
ZLIBRARY_PYTHON: Path | None = (ZLIBRARY_REPO / ".venv" / "bin" / "python") if ZLIBRARY_REPO else None
# Bridge uses sibling imports (`from filename_utils import ...`) so cwd must be
# `lib/`, not the repo root.
ZLIBRARY_LIB_DIR: Path | None = (ZLIBRARY_REPO / "lib") if ZLIBRARY_REPO else None
ZLIBRARY_BRIDGE_SCRIPT: Path | None = (ZLIBRARY_LIB_DIR / "python_bridge.py") if ZLIBRARY_LIB_DIR else None

# Final home for extracted text. The bridge writes to its own cwd-relative
# `processed_rag_output/`; this module moves results here before returning so
# the rest of the pipeline only ever sees painforwisdom-local paths.
PAINFORWISDOM_ROOT = Path(__file__).resolve().parent.parent
BOOKS_EXTRACTED_DIR = Path(
    os.environ.get(
        "PAINFORWISDOM_BOOKS_EXTRACTED",
        str(PAINFORWISDOM_ROOT / "books" / "extracted"),
    )
)

# Quality threshold for image-only / empty-extraction detection. Applied to
# every bridge output (epub/pdf/mobi) — `processed_output_format="txt"` flattens
# the extension to .txt, so a suffix-gated check would silently miss scanned
# PDFs that processed to near-empty text. page_count defaults to 1 for formats
# without paging, so the ratio collapses to absolute char_count for those.
IMAGE_ONLY_PDF_CHAR_RATIO = 200


FailureReason = Literal[
    "not-configured",
    "not-found",
    "image-only-pdf",
    "quota-exceeded",
    "extract-failed",
    "subprocess-error",
]


@dataclass
class BookText:
    local_path: Path
    char_count: int
    kind: Literal["epub", "pdf", "txt"]


@dataclass
class BookFailure:
    reason: FailureReason
    detail: str = ""


def _repo_configured() -> bool:
    return ZLIBRARY_REPO is not None


def _credentials_configured() -> bool:
    return bool(os.environ.get("ZLIBRARY_EMAIL") and os.environ.get("ZLIBRARY_PASSWORD"))


# Z-Library EAPI returns 5xx intermittently — measured ~10% rate during sequential
# batches (2026-05-27 stress test: 1/10 queries hit 500 mid-run). The full error
# `httpx.HTTPStatusError: Server error '500 Internal Server Error'` lands at the
# tail of bridge stderr; `_is_transient_error` matches enough patterns to retry
# only on upstream flakiness, not on 4xx / parse / auth failures.
_TRANSIENT_ERROR_PATTERNS = (
    "Server error '5",
    "500 Internal Server Error",
    "502 Bad Gateway",
    "503 Service Unavailable",
    "504 Gateway Timeout",
    "ConnectError",
    "ReadTimeout",
    "RemoteProtocolError",
)


def _is_transient_error(stderr: str) -> bool:
    return any(pat in (stderr or "") for pat in _TRANSIENT_ERROR_PATTERNS)


def _bridge_call(function: str, args: dict) -> tuple[int, str, str]:
    """Subprocess into the z-library bridge. Returns (rc, stdout, stderr).

    Invokes the bridge as a script with cwd=lib/ so its sibling imports
    (`from filename_utils import ...`) resolve. The bridge itself adds the
    repo root to sys.path internally for `from lib import ...` imports.
    """
    if ZLIBRARY_PYTHON is None or ZLIBRARY_BRIDGE_SCRIPT is None or ZLIBRARY_LIB_DIR is None:
        return 127, "", "ZLIBRARY_REPO_PATH env var not set"
    if not ZLIBRARY_PYTHON.exists():
        return 127, "", f"zlibrary-mcp venv missing at {ZLIBRARY_PYTHON}"
    if not ZLIBRARY_BRIDGE_SCRIPT.exists():
        return 127, "", f"zlibrary-mcp bridge missing at {ZLIBRARY_BRIDGE_SCRIPT}"

    env = os.environ.copy()
    # Downloads of 50-200 MB books take well over 180s on consumer bandwidth.
    timeout_s = int(os.environ.get("ZLIBRARY_TIMEOUT_S", "900"))
    try:
        completed = subprocess.run(
            [str(ZLIBRARY_PYTHON), str(ZLIBRARY_BRIDGE_SCRIPT), function, json.dumps(args)],
            cwd=str(ZLIBRARY_LIB_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"bridge call '{function}' exceeded {timeout_s}s timeout"
    return completed.returncode, completed.stdout, completed.stderr


def _bridge_call_search(args: dict, retries: int = 2, backoff_s: float = 2.0) -> tuple[int, str, str]:
    """`_bridge_call("search", ...)` with retry on transient 5xx / connection errors.

    Search is idempotent + cheap (no download quota consumed), so retrying is
    safe. Download is not wrapped here because a retry of a download that
    actually succeeded server-side could double-spend the daily quota.
    """
    last = (0, "", "")
    for attempt in range(retries + 1):
        rc, stdout, stderr = _bridge_call("search", args)
        if rc == 0:
            return rc, stdout, stderr
        last = (rc, stdout, stderr)
        if attempt < retries and _is_transient_error(stderr):
            time.sleep(backoff_s * (attempt + 1))
            continue
        return rc, stdout, stderr
    return last


def _parse_bridge_response(stdout: str) -> dict | None:
    """Bridge wraps results as {"content": [{"type": "text", "text": json.dumps(result)}]}."""
    try:
        outer = json.loads(stdout.strip().splitlines()[-1])
        content = outer.get("content") or []
        if content and isinstance(content[0], dict):
            inner_text = content[0].get("text") or ""
            return json.loads(inner_text)
    except (json.JSONDecodeError, IndexError):
        pass
    return None


# Z-Library indexes books by clean canonical titles. Notion entries often arrive
# decorated with chapter / pillar / edition annotations that make the query
# string longer than any real book title and produce hits=0 (verified 2026-05-27
# against Z-Library EAPI). Strip these before building the ladder.
_TITLE_NORMALIZATION_PATTERNS = (
    # everything after an em-dash or en-dash separator (chapter topic / subtitle)
    re.compile(r"\s+[—–]\s+.*$"),
    # "(2nd Ed.)", "(3rd Edition)", "(Revised Edition)", "(Expanded Edition)"
    re.compile(
        r"\s*\((?:\d+(?:st|nd|rd|th)\s+)?(?:revised|updated|expanded|new|2nd|3rd|4th|5th|6th|7th|8th|9th|10th)?\s*ed(?:ition)?\.?\)",
        re.IGNORECASE,
    ),
    # "Ch. 2:" or "Chapter 7:" headers
    re.compile(r"\s*(?:Ch(?:apter)?\.?\s*\d+)\s*:\s*", re.IGNORECASE),
)


def _normalize_title(title: str) -> str:
    """Strip chapter / edition annotations a Notion curator commonly appends.
    The Z-Library ladder's job is to find the book; the chapter topic is the
    caller's mental tag, not part of the canonical title."""
    out = (title or "").strip()
    for pat in _TITLE_NORMALIZATION_PATTERNS:
        out = pat.sub("", out)
    return " ".join(out.split()).strip()


def _build_query_ladder(title: str, author: str) -> list[str]:
    """Return up to 3 z-lib search queries, ordered most→least specific.

    Z-lib search is finicky on:
      - Long titles with `:` subtitles
      - Multi-author strings ("Ryan, Deci" / "A & B" / "X; Y")
      - Initials with dots ("James W. Pennebaker")

    Rungs:
      1. `f"{title} {author}"` — current behavior; literal.
      2. title-before-colon + first-author-lastname.
      3. title-before-colon only.

    Duplicates and empty rungs are filtered. Always returns at least one query
    if either title or author is non-empty.
    """
    title = _normalize_title(title)
    author = (author or "").strip()

    # Title minus subtitle (everything before first ':').
    title_core = title.split(":", 1)[0].strip()

    # First author's last whitespace-separated token. Handles:
    #   "Ryan, Deci"            -> "Ryan"      (comma = first author then list)
    #   "James W. Pennebaker"   -> "Pennebaker"
    #   "Pennebaker & Smyth"    -> "Pennebaker"
    #   "Tedeschi and Calhoun"  -> "Tedeschi"
    first_author = re.split(r"[,&;]| and ", author, maxsplit=1)[0].strip()
    first_lastname = first_author.split()[-1] if first_author else ""

    rungs = [
        f"{title} {author}".strip(),
        f"{title_core} {first_lastname}".strip(),
        title_core,
    ]
    # Dedupe while preserving order; drop empties.
    seen: set[str] = set()
    ladder: list[str] = []
    for q in rungs:
        q = " ".join(q.split())  # collapse whitespace
        if q and q not in seen:
            seen.add(q)
            ladder.append(q)
    return ladder


# Derivative-work title markers. These are commonly attached to companion
# products built around a source book (workbook, study guide, summary, etc.)
# that share the same author but are NOT the cited source.
_DERIVATIVE_KEYWORDS = (
    "workbook",
    "handbook",
    "summary",
    "summaries",
    "study guide",
    "companion",
    "analysis",
    "abridged",
    "encyclopedia",
    "dictionary",
)


def _reject_derivatives(candidates: list, original_title: str) -> list:
    """Drop candidates whose title contains a derivative keyword UNLESS the
    same keyword appears in the original (caller-supplied) title — meaning
    the caller explicitly asked for the workbook/handbook/etc."""
    orig_lower = (original_title or "").lower()
    keep: list = []
    for c in candidates:
        ctitle = (c.get("title") or "").lower()
        bad = False
        for kw in _DERIVATIVE_KEYWORDS:
            if kw in ctitle and kw not in orig_lower:
                bad = True
                break
        if not bad:
            keep.append(c)
    return keep


def get_download_limits() -> dict | BookFailure:
    """Returns {"daily_limit": N, "remaining": M} or BookFailure."""
    if not _repo_configured():
        return BookFailure("not-configured", "ZLIBRARY_REPO_PATH env var not set")
    if not _credentials_configured():
        return BookFailure("not-configured", "ZLIBRARY_EMAIL/ZLIBRARY_PASSWORD not set")
    rc, stdout, stderr = _bridge_call("get_download_limits", {})
    if rc != 0:
        return BookFailure("subprocess-error", stderr[:500])
    result = _parse_bridge_response(stdout)
    if result is None:
        return BookFailure("subprocess-error", "could not parse bridge response")
    return result


def fetch(title: str, author: str) -> Union[BookText, BookFailure]:
    """Search Z-Library for a book and extract its text. Stubbed when creds missing."""
    if not _repo_configured():
        return BookFailure(
            "not-configured",
            "Set ZLIBRARY_REPO_PATH to a local zlibrary-mcp checkout to enable Z-Library bridge.",
        )
    if not _credentials_configured():
        return BookFailure(
            "not-configured",
            "Set ZLIBRARY_EMAIL + ZLIBRARY_PASSWORD in .env to enable Z-Library bridge.",
        )

    # 1. Search via EAPI (not multi-source — that routes to Anna's Archive/LibGen).
    # Query ladder: z-lib search is finicky on long titles with colons/subtitles
    # and multi-author strings ("Lastname, Firstname & Other"). Try literal
    # first, then progressively simplify. First rung returning >=1 hit wins.
    query_ladder = _build_query_ladder(title, author)
    # Last-name of first listed author — used to validate that downstream
    # ladder rungs (which drop author from the query) don't ingest a wrong
    # book that happens to share a keyword. Rung 1 (literal title+author)
    # skips this check since the author is already in the query.
    first_author = re.split(r"[,&;]| and ", (author or "").strip(), maxsplit=1)[0].strip()
    first_lastname = first_author.split()[-1].lower() if first_author else ""

    book = None
    attempts_log: list[str] = []
    for rung_idx, query in enumerate(query_ladder):
        rc, stdout, stderr = _bridge_call_search(
            {"query": query, "count": 5, "languages": ["english"]},
        )
        if rc != 0:
            # Capture the tail of stderr where the actual exception lives; the
            # head is INFO logging that previously truncated the real error away.
            detail = stderr[-1500:] if stderr else ""
            return BookFailure("subprocess-error", detail)
        search_result = _parse_bridge_response(stdout)
        if not search_result:
            return BookFailure("subprocess-error", "could not parse search response")
        candidates = search_result.get("books") or search_result.get("results") or []

        # On rungs that dropped the author from the query (rung 2+), require
        # the result's author field to contain the original first-author last
        # name. Prevents ingesting unrelated books that share a title keyword.
        accepted: list = []
        if candidates and rung_idx > 0 and first_lastname:
            for c in candidates:
                if first_lastname in (c.get("author") or "").lower():
                    accepted.append(c)
        else:
            accepted = candidates

        # Derivative-keyword reject: workbook/handbook/summary/etc are
        # derivative works, not the cited source. Same-author derivatives
        # still pass the author-match check — this catches them. Allowed
        # iff the same keyword is in the original title (user asked for it).
        n_pre_deriv = len(accepted)
        accepted = _reject_derivatives(accepted, title)
        n_deriv_rejected = n_pre_deriv - len(accepted)

        attempts_log.append(
            f"q={query!r} hits={len(candidates)} author-match={n_pre_deriv} derivative-rejected={n_deriv_rejected} kept={len(accepted)}"
        )
        print(f"  [zlib-search] {attempts_log[-1]}", flush=True)
        if accepted:
            book = accepted[0]
            break

    if book is None:
        return BookFailure(
            "not-found",
            f"No author-matched results after {len(query_ladder)} queries: {' | '.join(attempts_log)}",
        )

    if not book.get("id"):
        return BookFailure("not-found", "First result has no id")

    # 2. Download — bridge needs full book_details dict + output_dir.
    output_dir = Path(os.environ.get("ZLIBRARY_DOWNLOAD_DIR", "books/raw")).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rc, stdout, stderr = _bridge_call(
        "download_book",
        {
            "book_details": book,
            "output_dir": str(output_dir),
            "process_for_rag": True,
            "processed_output_format": "txt",
        },
    )
    if rc != 0:
        lowered = (stderr or "").lower()
        if "limit" in lowered and ("daily" in lowered or "quota" in lowered):
            return BookFailure("quota-exceeded", stderr[:500])
        return BookFailure("subprocess-error", stderr[:500])

    dl = _parse_bridge_response(stdout)
    if not dl:
        return BookFailure("subprocess-error", "could not parse download response")

    local_path_str = (
        dl.get("processed_file_path")
        or dl.get("processed_path")
        or dl.get("file_path")
    )
    if not local_path_str:
        return BookFailure("extract-failed", f"No path in download result: {dl}")
    local_path = Path(local_path_str)
    if not local_path.is_absolute():
        # Bridge writes processed_rag_output/ relative to its own cwd (lib/).
        if ZLIBRARY_LIB_DIR is None:
            return BookFailure("subprocess-error", "ZLIBRARY_REPO_PATH unset; cannot resolve relative download path")
        local_path = (ZLIBRARY_LIB_DIR / local_path).resolve()
    if not local_path.exists():
        return BookFailure("extract-failed", f"Download path missing: {local_path}")

    # Move out of zlibrary-mcp/lib/processed_rag_output/ into painforwisdom's
    # books/extracted/ so the rest of the pipeline only points at paths inside
    # this project. Idempotent: if a file with the same name already exists at
    # destination (re-augment of the same book), reuse it without re-reading.
    BOOKS_EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)
    dest = BOOKS_EXTRACTED_DIR / local_path.name
    if local_path.resolve() != dest.resolve():
        if dest.exists():
            local_path.unlink(missing_ok=True)
        else:
            local_path.rename(dest)
    local_path = dest

    text = local_path.read_text(errors="replace")
    char_count = len(text)
    suffix = local_path.suffix.lower().lstrip(".")
    kind: Literal["epub", "pdf", "txt"] = (
        "epub" if suffix == "epub" else ("pdf" if suffix == "pdf" else "txt")
    )

    page_count = dl.get("page_count") or 1
    ratio = char_count / max(page_count, 1)
    if ratio < IMAGE_ONLY_PDF_CHAR_RATIO:
        return BookFailure(
            "image-only-pdf",
            f"{kind} char/page ratio {ratio:.0f} < {IMAGE_ONLY_PDF_CHAR_RATIO} (chars={char_count}, pages={page_count})",
        )

    return BookText(local_path=local_path, char_count=char_count, kind=kind)


def main(argv: list[str]) -> int:
    """CLI smoke test: python -m pipeline.zlibrary_bridge --title 'X' --author 'Y'"""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True)
    parser.add_argument("--author", default="")
    parser.add_argument("--limits", action="store_true", help="Just check quota")
    args = parser.parse_args(argv)

    if args.limits:
        result = get_download_limits()
        print(result)
        return 0

    result = fetch(args.title, args.author)
    if isinstance(result, BookText):
        print(f"OK  {result.kind}  {result.char_count} chars  {result.local_path}")
        return 0
    print(f"FAIL  {result.reason}  {result.detail}")
    return 1


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
