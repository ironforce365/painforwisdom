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
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

from pipeline.runtime import PROJECT_ROOT

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


def _fetch_local(path: str) -> str:
    if path.startswith("file://"):
        path = urlparse(path).path
    p = Path(path)
    if not p.exists():
        raise FetchError(f"local-file missing: {path}")
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
