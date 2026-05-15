"""Single source of truth for domains that must never appear as a Source URL.

Consumed by the audit script, augment script, research node's `_verify_urls`,
the daily summarizer fetcher, and the curator agent prompt.

Even when an HTTP 200 returns, these domains either sit behind a paywall or
emit non-fetchable content (product page, listing, preview, abstract). The
Phase 0 audit demonstrated this — `archive.org/details`, `amazon.com`,
`pubmed.ncbi.nlm.nih.gov` all classified as `paywalled` because trafilatura
extracted <500 chars.

Usage:
    from pipeline.banned_sources import is_banned, BANNED_DOMAINS
    if is_banned(url):
        ...

CLI:
    python -m pipeline.banned_sources https://www.amazon.com/foo
    BANNED  amazon.com
"""
from __future__ import annotations

import sys
from urllib.parse import urlparse

BANNED_DOMAINS: frozenset[str] = frozenset(
    {
        # Listings / product pages — fetchable HTML but no source content
        "amazon.com",
        "amazon.co.uk",
        "amazon.de",
        "goodreads.com",
        # Preview-only or borrow-only archives
        "archive.org",
        # Abstract-only — pmc.ncbi.nlm.nih.gov is allowed (full PMC text)
        "pubmed.ncbi.nlm.nih.gov",
        # Academic paywalls
        "jstor.org",
        "sciencedirect.com",
        "springer.com",
        "link.springer.com",
        "wiley.com",
        "onlinelibrary.wiley.com",
        "tandfonline.com",
        "journals.sagepub.com",
        # News paywalls
        "nytimes.com",
        "wsj.com",
        "ft.com",
        "economist.com",
        "newyorker.com",
        "bloomberg.com",
        "harpers.org",
        "theatlantic.com",
        # AI / commercial book-summary sites — substitute for the actual book,
        # commonly 403 to bots, and even when fetchable they emit summary prose
        # rather than the source text. Not a legitimate citation analog.
        "bookey.app",
        "blinkist.com",
        "getabstract.com",
        "shortform.com",
        "12min.com",
        "fourminutebooks.com",
    }
)


def _normalize_host(host: str) -> str:
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def is_banned(url: str) -> bool:
    """True if URL's host (or any parent domain) is in BANNED_DOMAINS."""
    if not url:
        return False
    parsed = urlparse(url)
    host = _normalize_host(parsed.netloc or parsed.path.split("/", 1)[0])
    if not host:
        return False
    if host in BANNED_DOMAINS:
        return True
    parts = host.split(".")
    for i in range(1, len(parts) - 1):
        if ".".join(parts[i:]) in BANNED_DOMAINS:
            return True
    return False


def banned_reason(url: str) -> str | None:
    """Return matched banned domain, or None if not banned."""
    if not url:
        return None
    parsed = urlparse(url)
    host = _normalize_host(parsed.netloc or parsed.path.split("/", 1)[0])
    if not host:
        return None
    if host in BANNED_DOMAINS:
        return host
    parts = host.split(".")
    for i in range(1, len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in BANNED_DOMAINS:
            return candidate
    return None


def render_for_prompt() -> str:
    """Render BANNED_DOMAINS as a sorted comma list for inclusion in agent prompts."""
    return ", ".join(sorted(BANNED_DOMAINS))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m pipeline.banned_sources <url>", file=sys.stderr)
        return 2
    url = argv[1]
    reason = banned_reason(url)
    if reason is not None:
        print(f"BANNED  {reason}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
