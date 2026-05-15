"""Phase 0 — read-only audit of the Notion Research Tasks database.

Classifies every row by reachability (can an automated agent fetch the source
text without a human in the loop?). Produces a markdown report under
`reports/` plus a JSONL row-level dump for the Phase 2 augment script.

No Notion writes. No LLM calls. Cheap to re-run.

Usage:
    python -m pipeline.scripts.audit_research_tasks
    python -m pipeline.scripts.audit_research_tasks --limit 25  # smoke
    python -m pipeline.scripts.audit_research_tasks --skip-fetch  # offline pass
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

try:
    import trafilatura
except ImportError:  # pragma: no cover
    trafilatura = None

# Load .env so NOTION_API_KEY is available when invoked from systemd / cron
# (no inherited shell env). Mirrors augment_research_tasks.py.
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from pipeline.notion_client import (
    RESEARCH_DATA_SOURCE_ID,
    extract_property,
    query_research_tasks,
)
from pipeline.runtime import PROJECT_ROOT


# Reachability classes — order matters: first match wins in the classifier.
CLASS_REACHABLE = "reachable"
CLASS_LOCAL_FILE = "local-file"
CLASS_PAYWALLED = "paywalled"
CLASS_BOOK_NO_URL = "book-no-url"
CLASS_PODCAST_NO_TRANSCRIPT = "podcast-no-transcript"
CLASS_404 = "404"
CLASS_NO_URL = "no-url"
CLASS_BAD_URL = "bad-url"
CLASS_UNKNOWN = "unknown"

REACHABLE_CLASSES = {CLASS_REACHABLE, CLASS_LOCAL_FILE}

UNREACHABLE_CLASSES = {
    CLASS_PAYWALLED,
    CLASS_BOOK_NO_URL,
    CLASS_PODCAST_NO_TRANSCRIPT,
    CLASS_404,
    CLASS_NO_URL,
    CLASS_BAD_URL,
    CLASS_UNKNOWN,
}

# Domains that almost always front-page-paywall actual content.
KNOWN_PAYWALL_DOMAINS = {
    "nytimes.com",
    "ft.com",
    "wsj.com",
    "economist.com",
    "newyorker.com",
    "bloomberg.com",
    "jstor.org",
    "wiley.com",
    "sciencedirect.com",
    "springer.com",
    "tandfonline.com",
    "harpers.org",
    "theatlantic.com",
}

# Podcast directory hosts — URL alone is not a fetchable transcript. Override
# applies only if `Specific Location` carries a separate transcript hint.
PODCAST_DIRECTORY_DOMAINS = {
    "apple.com",
    "podcasts.apple.com",
    "open.spotify.com",
    "spotify.com",
    "podcasts.google.com",
    "music.amazon.com",
}

PAYWALL_PHRASES = (
    "subscribe to continue",
    "sign in to read",
    "subscribers only",
    "members only",
    "create a free account to continue",
    "to continue reading",
    "this content is for subscribers",
    "log in to read",
)

# Per-row LLM cost estimates (Sonnet 4.6 with web_search) for forecasting.
COST_PER_ANALOG_LOOKUP_USD = 0.05
COST_PER_DAILY_BRIEF_USD = 0.40  # rough — 8 rows × $0.04 + synthesis $0.10
ROWS_PER_DAILY_BRIEF = 8


def _domain(url: str) -> str:
    if not url:
        return ""
    try:
        host = (urlparse(url).hostname or "").lower()
        return host.removeprefix("www.")
    except Exception:
        return ""


def _root_domain(host: str) -> str:
    """Best-effort eTLD+1 — naive split on dots, returns last 2 labels."""
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    return ".".join(parts[-2:])


def _has_paywall_phrase(text: str) -> bool:
    lower = text.lower()
    return any(p in lower for p in PAYWALL_PHRASES)


def classify_row(
    *,
    ref_type: str,
    source_url: str,
    specific_location: str,
    paywall_flag: bool,
    skip_fetch: bool = False,
    http_client: Optional[httpx.Client] = None,
    reachable: str = "",
) -> Tuple[str, str]:
    """Return (class, reason) for a single row."""
    # Short-circuit: if the augmenter has already marked this row Reachable=yes
    # in Notion (Alt Source URL + reason set), trust that. Otherwise the audit
    # would reclassify every previously-augmented row as paywalled/404 (because
    # the original Source URL is still the broken one) and the daily timer
    # would burn quota re-augmenting the same rows.
    if (reachable or "").lower() == "yes":
        return CLASS_REACHABLE, "Already augmented (Notion Reachable=yes)."
    ref_type_lower = (ref_type or "").lower()
    spec_loc = specific_location or ""

    # Type-driven shortcuts (no network).
    if "book" in ref_type_lower and not source_url:
        return CLASS_BOOK_NO_URL, "Book reference with no public URL."
    if "podcast" in ref_type_lower:
        host = _domain(source_url)
        is_directory = (
            host in PODCAST_DIRECTORY_DOMAINS
            or _root_domain(host) in PODCAST_DIRECTORY_DOMAINS
        )
        spec_has_url = "http://" in spec_loc or "https://" in spec_loc
        if is_directory and not spec_has_url:
            return (
                CLASS_PODCAST_NO_TRANSCRIPT,
                f"Podcast directory URL ({host}) with no transcript link.",
            )

    if not source_url:
        return CLASS_NO_URL, "Row has no Source URL."

    # Non-http schemes — local file paths, file://, etc. Treat existing local
    # files as reachable (the daily summarizer can read them); broken paths
    # as bad-url.
    scheme = urlparse(source_url).scheme.lower()
    if scheme in ("", "file"):
        local_path = source_url
        if scheme == "file":
            local_path = urlparse(source_url).path
        if Path(local_path).exists():
            return CLASS_LOCAL_FILE, f"Local file present at {local_path}."
        return CLASS_BAD_URL, f"Source URL is a non-http path that does not exist: {source_url}"
    if scheme not in ("http", "https"):
        return CLASS_BAD_URL, f"Unsupported URL scheme `{scheme}` in {source_url}"

    if paywall_flag:
        return CLASS_PAYWALLED, "Paywall flag set on the Notion row."

    host = _domain(source_url)
    root = _root_domain(host)
    if host in KNOWN_PAYWALL_DOMAINS or root in KNOWN_PAYWALL_DOMAINS:
        return CLASS_PAYWALLED, f"Domain {host} is on the known-paywall list."

    if skip_fetch:
        return CLASS_UNKNOWN, "Skipped network fetch (--skip-fetch)."

    # Live check.
    assert http_client is not None  # set by caller when skip_fetch=False
    try:
        resp = http_client.get(source_url)
    except httpx.HTTPError as exc:
        return CLASS_404, f"HTTP error: {type(exc).__name__}: {exc}"

    if resp.status_code != 200:
        return CLASS_404, f"HTTP {resp.status_code}"

    body = resp.text or ""
    if _has_paywall_phrase(body):
        return CLASS_PAYWALLED, "Paywall phrase detected in HTML body."

    extracted = ""
    if trafilatura is not None:
        try:
            extracted = trafilatura.extract(body) or ""
        except Exception as exc:  # noqa: BLE001
            return CLASS_UNKNOWN, f"trafilatura.extract raised: {exc}"

    if len(extracted) < 500:
        return (
            CLASS_PAYWALLED,
            f"Extracted content too short ({len(extracted)} chars) — likely paywalled or JS-rendered.",
        )

    return CLASS_REACHABLE, f"Fetched {len(extracted)} chars of clean text."


def collect_rows(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for page in query_research_tasks(page_size=100):
        rows.append(
            {
                "page_id": page.get("id", ""),
                "url": page.get("url", ""),
                "title": extract_property(page, "Title"),
                "type": extract_property(page, "Type"),
                "status": extract_property(page, "Status"),
                "priority": extract_property(page, "Priority"),
                "category": extract_property(page, "Category"),
                "coaching_theme": extract_property(page, "Coaching Theme"),
                "research_angle": extract_property(page, "Research Angle"),
                "author_host": extract_property(page, "Author/Host"),
                "specific_location": extract_property(page, "Specific Location"),
                "source_url": extract_property(page, "Source URL"),
                "paywall_flag": extract_property(page, "Paywall"),
                "vault_entry": extract_property(page, "Vault Entry"),
                "reachable": extract_property(page, "Reachable"),
                "alt_source_url": extract_property(page, "Alt Source URL"),
            }
        )
        if limit and len(rows) >= limit:
            break
    return rows


def build_report(
    rows: List[Dict[str, Any]],
    classified: List[Dict[str, Any]],
) -> str:
    by_type = Counter(r["type"] or "(blank)" for r in rows)
    by_theme = Counter(r["coaching_theme"] or "(blank)" for r in rows)
    by_status = Counter(r["status"] or "(blank)" for r in rows)
    by_class = Counter(c["class"] for c in classified)

    pending_rows = [r for r in rows if r["status"] == "To Read/Listen"]
    pending_reachable = sum(
        1
        for r, c in zip(rows, classified)
        if r["status"] == "To Read/Listen" and c["class"] in REACHABLE_CLASSES
    )
    pending_unreachable = len(pending_rows) - pending_reachable

    pending_by_theme = Counter(
        r["coaching_theme"] or "(blank)"
        for r, c in zip(rows, classified)
        if r["status"] == "To Read/Listen" and c["class"] in REACHABLE_CLASSES
    )

    rows_needing_analog = sum(
        1 for c in classified if c["class"] in UNREACHABLE_CLASSES
    )
    augment_cost_usd = rows_needing_analog * COST_PER_ANALOG_LOOKUP_USD

    days_to_drain_pending = 0
    if pending_reachable:
        days_to_drain_pending = max(1, (pending_reachable + ROWS_PER_DAILY_BRIEF - 1) // ROWS_PER_DAILY_BRIEF)
    daily_burn_cost_usd = days_to_drain_pending * COST_PER_DAILY_BRIEF_USD

    sample_unreachable = [
        {**r, "reason": c["reason"], "class": c["class"]}
        for r, c in zip(rows, classified)
        if c["class"] in UNREACHABLE_CLASSES
    ][:10]

    lines: List[str] = []
    lines.append(f"# Research Tasks audit — {date.today().isoformat()}")
    lines.append("")
    lines.append(f"Data source: `{RESEARCH_DATA_SOURCE_ID}`")
    lines.append(f"Total rows scanned: **{len(rows)}**")
    lines.append("")

    lines.append("## Reachability tally")
    lines.append("")
    lines.append("| Class | Count |")
    lines.append("|---|---|")
    for cls in [
        CLASS_REACHABLE,
        CLASS_LOCAL_FILE,
        CLASS_PAYWALLED,
        CLASS_BOOK_NO_URL,
        CLASS_PODCAST_NO_TRANSCRIPT,
        CLASS_404,
        CLASS_NO_URL,
        CLASS_BAD_URL,
        CLASS_UNKNOWN,
    ]:
        lines.append(f"| `{cls}` | {by_class.get(cls, 0)} |")
    lines.append("")

    lines.append("## Status breakdown")
    lines.append("")
    for status, n in by_status.most_common():
        lines.append(f"- `{status}`: {n}")
    lines.append("")

    lines.append("## By Type")
    lines.append("")
    for t, n in by_type.most_common():
        lines.append(f"- `{t}`: {n}")
    lines.append("")

    lines.append("## By Coaching Theme")
    lines.append("")
    for t, n in by_theme.most_common():
        lines.append(f"- `{t}`: {n}")
    lines.append("")

    lines.append("## Pending queue (Status = To Read/Listen)")
    lines.append("")
    lines.append(f"- Total pending: **{len(pending_rows)}**")
    lines.append(f"- Pending reachable: **{pending_reachable}**")
    lines.append(f"- Pending unreachable (excluded from daily briefs unless augmented): **{pending_unreachable}**")
    lines.append("")
    lines.append("Pending reachable per theme:")
    for t, n in pending_by_theme.most_common():
        lines.append(f"  - `{t}`: {n}")
    lines.append("")

    lines.append("## Cost forecast")
    lines.append("")
    lines.append(f"- Phase 2 augment (`rows_needing_analog` × ${COST_PER_ANALOG_LOOKUP_USD:.2f}): "
                 f"**{rows_needing_analog}** × ${COST_PER_ANALOG_LOOKUP_USD:.2f} ≈ **${augment_cost_usd:.2f}**")
    lines.append(f"- Daily summarizer at {ROWS_PER_DAILY_BRIEF} rows/day, ${COST_PER_DAILY_BRIEF_USD:.2f}/day: "
                 f"≈ **{days_to_drain_pending}** days to drain pending reachable, total ≈ **${daily_burn_cost_usd:.2f}**")
    lines.append("")
    lines.append("> Estimates use Sonnet 4.6 + web_search rates; per-row real cost will vary with fetched content size.")
    lines.append("")

    lines.append("## Sample unreachable rows (up to 10)")
    lines.append("")
    if not sample_unreachable:
        lines.append("(none — every row is reachable)")
    for r in sample_unreachable:
        title = (r.get("title") or "(untitled)").replace("|", "\\|")
        lines.append(f"### {title}")
        lines.append(f"- Class: `{r['class']}` — {r['reason']}")
        lines.append(f"- Type: `{r.get('type','')}` | Theme: `{r.get('coaching_theme','')}` | Status: `{r.get('status','')}`")
        if r.get("source_url"):
            lines.append(f"- URL: {r['source_url']}")
        if r.get("specific_location"):
            lines.append(f"- Specific Location: {r['specific_location']}")
        lines.append(f"- Suggested analog query: `{r.get('coaching_theme','')} {r.get('research_angle','')} {r.get('author_host','')}`".strip())
        lines.append("")

    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="Cap rows scanned (smoke testing).")
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Skip httpx GETs; classify on type/domain heuristics only.",
    )
    parser.add_argument(
        "--out-dir",
        default=str(PROJECT_ROOT / "reports"),
        help="Where to write the audit artifacts.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Per-request HTTP timeout (seconds).",
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    report_path = out_dir / f"research-audit-{today}.md"
    jsonl_path = out_dir / f"research-audit-{today}.jsonl"

    print(f"[audit] data source = {RESEARCH_DATA_SOURCE_ID}", flush=True)
    print(f"[audit] fetching rows from Notion...", flush=True)
    rows = collect_rows(limit=args.limit)
    print(f"[audit] {len(rows)} rows pulled", flush=True)

    classified: List[Dict[str, Any]] = []
    headers = {"User-Agent": "painforwisdom-audit/0.1 (+https://painforwisdom.wordpress.com)"}
    with httpx.Client(
        timeout=args.timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        for i, r in enumerate(rows, 1):
            cls, reason = classify_row(
                ref_type=r["type"],
                source_url=r["source_url"],
                specific_location=r["specific_location"],
                paywall_flag=bool(r["paywall_flag"]),
                skip_fetch=args.skip_fetch,
                http_client=None if args.skip_fetch else client,
                reachable=r.get("reachable", ""),
            )
            classified.append({"page_id": r["page_id"], "class": cls, "reason": reason})
            if i % 10 == 0 or i == len(rows):
                tally = Counter(c["class"] for c in classified)
                print(f"[audit] {i}/{len(rows)} classified — {dict(tally)}", flush=True)

    # JSONL dump (consumed by Phase 2 augment script).
    with jsonl_path.open("w") as f:
        for r, c in zip(rows, classified):
            f.write(json.dumps({**r, "class": c["class"], "reason": c["reason"]}) + "\n")

    report_md = build_report(rows, classified)
    report_path.write_text(report_md)

    print(f"[audit] wrote {report_path}")
    print(f"[audit] wrote {jsonl_path}")
    print()
    print(report_md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
