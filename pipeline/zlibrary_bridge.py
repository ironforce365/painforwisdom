"""Z-Library bridge — subprocess adapter for the zlibrary-mcp Python bridge.

Wraps `/home/gonzalo/workspace/extensions/zlibrary-mcp/lib/python_bridge.py`
as a callable: `fetch(title, author) -> BookText | BookFailure`.

Auth requires `ZLIBRARY_EMAIL` and `ZLIBRARY_PASSWORD` env vars. When
unconfigured, returns `BookFailure(reason="not-configured")` so the augment
script can transparently fall through to web-search.

Setup once:
    cd /home/gonzalo/workspace/extensions/zlibrary-mcp
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
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Union


ZLIBRARY_REPO = Path(os.environ.get(
    "ZLIBRARY_REPO_PATH",
    "/home/gonzalo/workspace/extensions/zlibrary-mcp",
))
ZLIBRARY_PYTHON = ZLIBRARY_REPO / ".venv" / "bin" / "python"
# The bridge file uses sibling imports (`from filename_utils import ...`) so
# cwd must be `lib/`, not the repo root.
ZLIBRARY_LIB_DIR = ZLIBRARY_REPO / "lib"
ZLIBRARY_BRIDGE_SCRIPT = ZLIBRARY_LIB_DIR / "python_bridge.py"

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

# Quality threshold for image-only PDF detection.
IMAGE_ONLY_PDF_CHAR_RATIO = 200  # chars per page; below = scanned image


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


def _credentials_configured() -> bool:
    return bool(os.environ.get("ZLIBRARY_EMAIL") and os.environ.get("ZLIBRARY_PASSWORD"))


def _bridge_call(function: str, args: dict) -> tuple[int, str, str]:
    """Subprocess into the z-library bridge. Returns (rc, stdout, stderr).

    Invokes the bridge as a script with cwd=lib/ so its sibling imports
    (`from filename_utils import ...`) resolve. The bridge itself adds the
    repo root to sys.path internally for `from lib import ...` imports.
    """
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


def get_download_limits() -> dict | BookFailure:
    """Returns {"daily_limit": N, "remaining": M} or BookFailure."""
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
    if not _credentials_configured():
        return BookFailure(
            "not-configured",
            "Set ZLIBRARY_EMAIL + ZLIBRARY_PASSWORD in .env to enable Z-Library bridge.",
        )

    # 1. Search via EAPI (not multi-source — that routes to Anna's Archive/LibGen).
    search_query = f"{title} {author}".strip()
    rc, stdout, stderr = _bridge_call(
        "search",
        {"query": search_query, "count": 5, "languages": ["english"]},
    )
    if rc != 0:
        return BookFailure("subprocess-error", stderr[:500])
    search_result = _parse_bridge_response(stdout)
    if not search_result:
        return BookFailure("subprocess-error", "could not parse search response")

    books = search_result.get("books") or search_result.get("results") or []
    if not books:
        return BookFailure("not-found", f"No results for: {search_query}")

    book = books[0]
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
    if kind == "pdf" and char_count / max(page_count, 1) < IMAGE_ONLY_PDF_CHAR_RATIO:
        return BookFailure(
            "image-only-pdf",
            f"PDF char/page ratio {char_count / max(page_count, 1):.0f} < {IMAGE_ONLY_PDF_CHAR_RATIO}",
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
