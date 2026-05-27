"""URL → clean text dispatcher for the daily summarizer.

Dispatch:
    file://, plain path  -> read directly (z-library extracts live here)
    youtube.com / youtu.be -> youtube_transcript_api (if installed)
    *.pdf, arxiv.org/pdf -> httpx + pypdf (if installed)
    everything else      -> httpx.get + trafilatura.extract

Caches every successful fetch by SHA-256 of URL at
`briefs/.cache/<sha>.txt` so re-runs do not re-fetch.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

from pipeline.runtime import PROJECT_ROOT


# Binary book formats need to be text-extracted before they're fed to any LLM
# (raw PDF/EPUB bytes broke the daily-brief synthesis on 2026-05-16).
# `pdftotext` ships with poppler-utils; `ebook-convert` ships with calibre.
_PDFTOTEXT = shutil.which("pdftotext")
_EBOOK_CONVERT = shutil.which("ebook-convert")
_EXTRACT_TIMEOUT_S = 600

try:
    import trafilatura
except ImportError:  # pragma: no cover
    trafilatura = None  # type: ignore

try:
    from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
except ImportError:  # pragma: no cover
    YouTubeTranscriptApi = None  # type: ignore

try:
    from pypdf import PdfReader  # type: ignore
except ImportError:  # pragma: no cover
    PdfReader = None  # type: ignore

CACHE_DIR = PROJECT_ROOT / "briefs" / ".cache"
FETCH_TIMEOUT = 30.0
DENYLIST_FILE = PROJECT_ROOT / "config" / "fetch_denylist.txt"


class FetchError(Exception):
    pass


def _load_denylist() -> set[str]:
    if not DENYLIST_FILE.exists():
        return set()
    out: set[str] = set()
    for raw in DENYLIST_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.add(line)
    return out


def is_denylisted(url: str) -> bool:
    return bool(url) and url.strip() in _load_denylist()


def _cache_path(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    return CACHE_DIR / f"{digest}.txt"


_PDF_MAGIC = b"%PDF-"
_EPUB_MAGIC = b"PK\x03\x04"
# MOBI / AZW PalmDOC header puts "BOOKMOBI" at offset 60 in a virgin binary file.
# Older corrupted caches went through Python `errors="replace"` UTF-8 decode +
# re-encode, which inserts 3-byte U+FFFD sequences for every invalid byte and
# shifts later content by 4+ bytes (cache files we measured had BOOKMOBI at
# offset 64). Sniff with a substring search over the first 128 bytes instead
# of a fixed offset so both raw files AND already-corrupted caches are caught.
_MOBI_MAGIC = b"BOOKMOBI"
_BINARY_SNIFF_LEN = 128


def _looks_binary(head: bytes) -> bool:
    """Coarse sniff for PDF/EPUB/MOBI headers. Covers every binary format the
    z-library bridge drops into `books/`, plus already-corrupted text caches
    written by an earlier version of `_fetch_local` that read raw MOBI bytes
    through `errors="replace"`."""
    if head.startswith(_PDF_MAGIC) or head.startswith(_EPUB_MAGIC):
        return True
    return _MOBI_MAGIC in head[:_BINARY_SNIFF_LEN]


def _extract_pdf(path: Path) -> str:
    if _PDFTOTEXT is None:
        raise FetchError("pdftotext not installed (apt-get install poppler-utils)")
    proc = subprocess.run(
        [_PDFTOTEXT, "-layout", "-enc", "UTF-8", str(path), "-"],
        capture_output=True,
        text=True,
        timeout=_EXTRACT_TIMEOUT_S,
    )
    if proc.returncode != 0:
        raise FetchError(f"pdftotext failed (rc={proc.returncode}): {proc.stderr[:200]}")
    out = proc.stdout or ""
    if len(out) < 200:
        raise FetchError(f"pdftotext thin-extract: {len(out)} chars")
    return out


def _extract_with_ebook_convert(path: Path) -> str:
    """Run calibre's `ebook-convert` to turn EPUB/MOBI/AZW3 into UTF-8 text.
    Calibre infers input format from file extension."""
    if _EBOOK_CONVERT is None:
        raise FetchError("ebook-convert not installed (apt-get install calibre)")
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        proc = subprocess.run(
            [_EBOOK_CONVERT, str(path), str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=_EXTRACT_TIMEOUT_S,
        )
        if proc.returncode != 0:
            raise FetchError(f"ebook-convert failed (rc={proc.returncode}): {proc.stderr[:200]}")
        text = tmp_path.read_text(errors="replace")
    finally:
        tmp_path.unlink(missing_ok=True)
    if len(text) < 200:
        raise FetchError(f"ebook-convert thin-extract: {len(text)} chars")
    return text


# Back-compat alias for callers that imported the old name.
_extract_epub = _extract_with_ebook_convert


def _fetch_local(path: str) -> str:
    """Read a local file. If it's a PDF or EPUB (z-library bridge drops both
    into `books/`), extract the text — otherwise the raw bytes get cached and
    later fed to claude -p / litellm, which both choke on binary input (this
    was the 2026-05-16 daily-brief failure)."""
    if path.startswith("file://"):
        path = urlparse(path).path
    p = Path(path)
    if not p.exists():
        raise FetchError(f"local-file missing: {path}")
    head = p.read_bytes()[:_BINARY_SNIFF_LEN]
    if head.startswith(_PDF_MAGIC):
        return _extract_pdf(p)
    if head.startswith(_EPUB_MAGIC):
        return _extract_with_ebook_convert(p)
    if _MOBI_MAGIC in head:
        return _extract_with_ebook_convert(p)
    return p.read_text(errors="replace")


def _fetch_youtube(url: str) -> str:
    if YouTubeTranscriptApi is None:
        raise FetchError("youtube-transcript-api not installed")
    parsed = urlparse(url)
    if "youtu.be" in parsed.netloc:
        video_id = parsed.path.lstrip("/")
    else:
        from urllib.parse import parse_qs

        qs = parse_qs(parsed.query)
        video_id = (qs.get("v") or [""])[0]
    if not video_id:
        raise FetchError(f"cannot extract video_id from: {url}")
    transcript = YouTubeTranscriptApi.get_transcript(video_id)
    return "\n".join(seg.get("text", "") for seg in transcript)


def _fetch_pdf(url: str, client: httpx.Client) -> str:
    if PdfReader is None:
        raise FetchError("pypdf not installed")
    resp = client.get(url, follow_redirects=True, timeout=FETCH_TIMEOUT)
    if resp.status_code != 200:
        raise FetchError(f"pdf http-status {resp.status_code}")
    import io

    reader = PdfReader(io.BytesIO(resp.content))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _fetch_html(url: str, client: httpx.Client) -> str:
    if trafilatura is None:
        raise FetchError("trafilatura not installed")
    resp = client.get(url, follow_redirects=True, timeout=FETCH_TIMEOUT)
    if resp.status_code != 200:
        raise FetchError(f"html http-status {resp.status_code}")
    extracted = trafilatura.extract(resp.text) or ""
    if len(extracted) < 200:
        raise FetchError(f"thin-extract: {len(extracted)} chars")
    return extracted


def fetch(url: str, *, client: Optional[httpx.Client] = None, use_cache: bool = True) -> str:
    """Return cleaned source text for any supported URL/path.

    Raises FetchError if the URL is unsupported or fetch/extract fails.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = _cache_path(url)
    if use_cache and cached.exists() and cached.stat().st_size > 0:
        # Older cache entries from before the binary-extraction fix may
        # contain raw PDF/EPUB/MOBI bytes. Detect by magic and invalidate so we
        # re-fetch through the new text-extraction path.
        head = cached.read_bytes()[:_BINARY_SNIFF_LEN]
        if _looks_binary(head):
            cached.unlink()
        else:
            return cached.read_text()

    own_client = client is None
    if own_client:
        client = httpx.Client(
            headers={"User-Agent": "Mozilla/5.0 painforwisdom-summarizer/4"},
            follow_redirects=True,
        )
    try:
        scheme = urlparse(url).scheme.lower()
        host = (urlparse(url).hostname or "").lower()

        if scheme in ("", "file") or not scheme.startswith("http"):
            text = _fetch_local(url)
        elif "youtube.com" in host or "youtu.be" in host:
            text = _fetch_youtube(url)
        elif url.lower().endswith(".pdf") or "arxiv.org/pdf" in url.lower():
            text = _fetch_pdf(url, client)
        else:
            text = _fetch_html(url, client)
    finally:
        if own_client and client is not None:
            client.close()

    if text:
        cached.write_text(text)
    return text
