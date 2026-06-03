"""Phase 2b/2c — augment-in-place audit of the Research Tasks data source.

For each row not classified as reachable in the Phase 0 audit:
  1. If Type == Book: try Z-Library bridge first (Phase 2c).
  2. Otherwise (or on Z-Library failure): Sonnet 4.6 + web_search for a
     freely-readable analog covering the same research angle.
  3. Verify the result with `httpx.get` + `trafilatura.extract` and the
     `banned_sources` defense-in-depth list.
  4. On success: `client.pages.update` to set Alt Source URL, Reachable=yes,
     Reachability Reason. Original Source URL is never overwritten.
  5. On failure: mark Reachable=no with reason; daily summarizer excludes.

Cost guardrail: --max-cost-usd (default $7.00, Phase 0 forecast +10%).
Pacing: 0.5s between Notion writes.

Usage:
    python -m pipeline.scripts.augment_research_tasks --dry-run
    python -m pipeline.scripts.augment_research_tasks --apply --limit 5
    python -m pipeline.scripts.augment_research_tasks --apply
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

try:
    import trafilatura
except ImportError:
    trafilatura = None  # type: ignore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from pipeline.banned_sources import is_banned  # noqa: E402
from pipeline.llm import call_llm  # noqa: E402
from pipeline.local_books import find_local_book  # noqa: E402
from pipeline.notion_client import get_client  # noqa: E402
from pipeline.runtime import append_metric, canonical_project_root  # noqa: E402
from pipeline.telegram import send as telegram_send  # noqa: E402
from pipeline.zlibrary_bridge import (  # noqa: E402
    BookFailure,
    BookText,
    fetch as zlib_fetch,
    get_download_limits as zlib_get_limits,
)

AUGMENT_LOG_PATH = PROJECT_ROOT / "reports" / "augment-runs.jsonl"
AUGMENT_FAILURES_DIR = PROJECT_ROOT / "reports"


COST_PER_ROW_FORECAST = 0.05
TRAFILATURA_MIN_CHARS = 500
WEB_FETCH_TIMEOUT = 15.0
NOTION_PACING_S = 0.5

# Rooted at the canonical checkout — the file:// path built here is persisted
# to Notion, so it must survive this run's (possibly worktree) checkout being
# removed (2026-06-02 self-compassion incident). Matches zlibrary_bridge.
EXTRACTED_BOOKS_DIR = Path(
    os.environ.get(
        "PAINFORWISDOM_BOOKS_EXTRACTED",
        str(canonical_project_root() / "books" / "extracted"),
    )
)

SEARCH_SYSTEM = """You are a research librarian helping a runner-coach find freely-readable analog references.

The user has a reference (book, podcast, paper, article) that is unreachable: paywalled, scanned-only, or behind a publisher landing page. Your job: find ONE freely-readable analog (open-access paper, blog post, podcast transcript, article) that covers the SAME research angle.

Prefer:
- The same author (a blog post or interview by them).
- An open-access version of the same paper (PMC, arXiv, author's site).
- A podcast transcript where the author discusses the same idea.

Reject:
- Amazon / Goodreads listings.
- Archive.org details (preview-only).
- Paywalled news (NYT, WSJ, FT, Economist, etc.).
- Pubmed abstracts (PMC full-text is fine).
- JSTOR, Wiley, Springer, ScienceDirect.

Use the web_search tool aggressively. After your search, output EXACTLY one line:
URL: <https://...>  | <one-sentence justification>
or:
NONE  | <one-sentence reason no good analog exists>

No preamble. No commentary. No multi-line. Just one of the two formats above.
"""


@dataclass
class AnalogProposal:
    url: Optional[str]
    justification: str
    cost_usd: float


@dataclass
class VerifyResult:
    ok: bool
    reason: str
    char_count: int = 0


def _extract_proposal(text: str) -> AnalogProposal:
    text = (text or "").strip()
    for line in text.splitlines():
        line = line.strip()
        if line.upper().startswith("NONE"):
            justification = line.split("|", 1)[1].strip() if "|" in line else ""
            return AnalogProposal(url=None, justification=justification or "no analog", cost_usd=0.0)
        if line.upper().startswith("URL:"):
            rest = line[4:].strip()
            url_part, _, just_part = rest.partition("|")
            url_match = re.search(r"https?://\S+", url_part)
            url = url_match.group(0).rstrip(".,;)]>") if url_match else None
            return AnalogProposal(url=url, justification=just_part.strip(), cost_usd=0.0)
    return AnalogProposal(url=None, justification="parser-could-not-extract", cost_usd=0.0)


def _verify_analog(url: str, client: httpx.Client) -> VerifyResult:
    if is_banned(url):
        return VerifyResult(False, f"banned-domain: {url}")
    try:
        resp = client.get(url, follow_redirects=True, timeout=WEB_FETCH_TIMEOUT)
    except httpx.HTTPError as exc:
        return VerifyResult(False, f"http-error: {type(exc).__name__}: {exc}")
    if resp.status_code != 200:
        return VerifyResult(False, f"http-status: {resp.status_code}")
    if trafilatura is None:
        return VerifyResult(False, "trafilatura-missing")
    try:
        extracted = trafilatura.extract(resp.text) or ""
    except Exception as exc:  # noqa: BLE001
        return VerifyResult(False, f"trafilatura-error: {exc}")
    if len(extracted) < TRAFILATURA_MIN_CHARS:
        return VerifyResult(False, f"thin-extract: {len(extracted)} chars")
    return VerifyResult(True, f"verified {len(extracted)} chars", char_count=len(extracted))


def _search_for_analog(row: Dict[str, Any], model: str) -> AnalogProposal:
    user_msg = (
        f"Original (unreachable) reference:\n"
        f"- Title: {row.get('title','')}\n"
        f"- Author/Host: {row.get('author_host','')}\n"
        f"- Type: {row.get('type','')}\n"
        f"- Coaching Theme: {row.get('coaching_theme','')}\n"
        f"- Research Angle: {row.get('research_angle','')}\n"
        f"- Specific Location: {row.get('specific_location','')}\n"
        f"- Original URL (unreachable, do not return this): {row.get('source_url','')}\n"
        f"- Unreachable reason: {row.get('reason','')}\n\n"
        f"Find ONE freely-readable analog covering the same research angle."
    )
    result = call_llm(
        model=model,
        system_prompt=SEARCH_SYSTEM,
        user_message=user_msg,
        max_tokens=600,
        web_search=True,
    )
    proposal = _extract_proposal(result["text"])
    proposal.cost_usd = result.get("cost_usd", 0.0)
    return proposal


def _book_identity(row: Dict[str, Any]) -> tuple[str, str]:
    """Coarse (book-title, first-author-lastname) key for deduping chapter /
    edition rows of the SAME book within a run.

    Many research rows are distinct chapters of one book ("Do Hard Things — Ch.
    2", "Do Hard Things — Ch. 11"); each previously triggered a full re-search +
    re-download of the identical book, multiplying z-library quota + login load
    (2026-06-02: 60 rows ≈ 38 unique books). The whole-book extraction already
    serves every chapter row, so the first fetch is reused for the rest."""
    title = (row.get("title") or "").lower()
    # Drop parentheticals (row annotations + editions): "(Values vs Goals)", "(2nd Ed.)".
    title = re.sub(r"\s*\([^)]*\)", " ", title)
    # Drop chapter/section/subtitle tails: " — Ch. 2: ...", " -- Pillar 4", ": subtitle".
    title = re.split(r"\s+[—–-]{1,2}\s+|:", title, maxsplit=1)[0]
    title = re.sub(r"[^a-z0-9]+", " ", title).strip()
    author = (row.get("author_host") or "").lower()
    first = re.split(r"[,&;]| and ", author, maxsplit=1)[0].strip()
    lastname = first.split()[-1] if first else ""
    return (title, lastname)


def _check_curated_local(row: Dict[str, Any]) -> tuple[Optional[str], str]:
    """Probe local book inventory (books/<slug>/ and books/raw/) for a hit.
    Returns (alt_source_url, reason) on hit, (None, "") on miss. Delegates
    to the shared `local_books.find_local_book` so the daily-pipeline node
    and this retro-augment script share one definition of "local match"."""
    title = row.get("title", "")
    author = row.get("author_host", "")
    match = find_local_book(title, author)
    if match is None:
        return None, ""
    return match.file_url, f"curated-local: {match.path.name}"


def _try_zlibrary(row: Dict[str, Any]) -> tuple[Optional[str], str]:
    """Returns (alt_source_url, reason). alt_source_url is None on failure.

    Bridge already lands files inside `books/extracted/`. Rename to a tidy
    title-slugged filename for downstream readability; the bridge's own name
    encodes author+id which is noisier."""
    title = row.get("title", "")
    author = row.get("author_host", "")
    result = zlib_fetch(title=title, author=author)
    if isinstance(result, BookText):
        EXTRACTED_BOOKS_DIR.mkdir(parents=True, exist_ok=True)
        safe_slug = re.sub(r"[^a-z0-9]+", "-", title.lower())[:60].strip("-")
        dest = EXTRACTED_BOOKS_DIR / f"{safe_slug}{result.local_path.suffix}"
        if result.local_path.resolve() != dest.resolve():
            if dest.exists():
                result.local_path.unlink(missing_ok=True)
            else:
                result.local_path.rename(dest)
        return f"file://{dest}", f"z-library: {result.kind} extracted ({result.char_count} chars)"
    assert isinstance(result, BookFailure)
    return None, f"z-library: {result.reason} ({result.detail[:120]})"


def _notion_update(
    client,
    page_id: str,
    *,
    reachable: str,
    reachability_reason: str,
    alt_source_url: Optional[str] = None,
) -> None:
    properties: Dict[str, Any] = {
        "Reachable": {"select": {"name": reachable}},
        "Reachability Reason": {"rich_text": [{"text": {"content": reachability_reason[:1900]}}]},
    }
    if alt_source_url:
        properties["Alt Source URL"] = {"url": alt_source_url}
    client.pages.update(page_id=page_id, properties=properties)
    time.sleep(NOTION_PACING_S)


def _load_audit_jsonl(path: Path) -> list[Dict[str, Any]]:
    rows: list[Dict[str, Any]] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _telemetry(row: Dict[str, Any], outcome: str, cost: float) -> None:
    append_metric(
        AUGMENT_LOG_PATH,
        "augment_research_task",
        page_id=row.get("page_id"),
        title=row.get("title"),
        type=row.get("type"),
        outcome=outcome,
        cost_usd=round(cost, 6),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audit-jsonl",
        type=Path,
        default=PROJECT_ROOT / "reports" / "research-audit-2026-05-09.jsonl",
        help="Path to Phase 0 audit JSONL.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Process at most N rows.")
    parser.add_argument(
        "--max-cost-usd",
        type=float,
        default=7.0,
        help="Halt before exceeding this LLM spend (default $7.00).",
    )
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-6",
        help="LLM model id.",
    )
    parser.add_argument(
        "--skip-books",
        action="store_true",
        help="Skip Type==Book rows entirely (faster, no Z-Library attempt).",
    )
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="Plan + cost forecast only. No writes.")
    g.add_argument("--apply", action="store_true", help="Write to Notion.")
    args = parser.parse_args(argv)

    if not args.audit_jsonl.exists():
        print(f"ERROR: audit jsonl missing: {args.audit_jsonl}", file=sys.stderr)
        return 2

    rows = _load_audit_jsonl(args.audit_jsonl)
    unreachable = [r for r in rows if r.get("class") not in ("reachable", "local-file")]

    # Z-library is the primary source for books. Also queue Type=Book rows
    # that are already marked reachable but lack a local copy, so we attempt
    # to download them too. These rows are flagged "_zlib_only": on zlib miss
    # we DO NOT fall back to LLM analog and DO NOT downgrade Notion state
    # (the row stays reachable=yes via its original URL).
    n_zlib_only_added = 0
    if not args.skip_books:
        already_targeted = {r["page_id"] for r in unreachable}
        for r in rows:
            if r["page_id"] in already_targeted:
                continue
            if (r.get("type") or "").lower() != "book":
                continue
            if r.get("class") == "local-file":
                continue
            if find_local_book(r.get("title", ""), r.get("author_host", "")) is not None:
                continue
            r = dict(r)
            r["_zlib_only"] = True
            unreachable.append(r)
            n_zlib_only_added += 1

    if args.skip_books:
        unreachable = [r for r in unreachable if (r.get("type") or "").lower() != "book"]

    # Counts BEFORE --limit truncation, for accurate scope reporting.
    n_total_queued = len(unreachable)
    n_unreach_pre_limit = n_total_queued - n_zlib_only_added

    if args.limit is not None:
        unreachable = unreachable[: args.limit]

    print(f"Total rows in audit: {len(rows)}")
    print(f"Unreachable to augment: {n_unreach_pre_limit}")
    print(f"Reachable books queued for z-lib (no-downgrade on miss): {n_zlib_only_added}")
    print(f"Total to process: {len(unreachable)} (queued={n_total_queued}, limit={args.limit})")
    print(f"Cost forecast @ ${COST_PER_ROW_FORECAST}/row: ${len(unreachable) * COST_PER_ROW_FORECAST:.2f}")
    print(f"Cost guardrail: ${args.max_cost_usd:.2f}")

    if args.dry_run:
        sample = unreachable[:5]
        print("\nSample (first 5):")
        for r in sample:
            print(f"  - {r.get('title','')[:60]:60s}  type={r.get('type','?'):8s}  class={r.get('class','?')}")
        return 0

    client = get_client()
    http = httpx.Client(
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) painforwisdom-augment/2b"},
        follow_redirects=True,
    )

    spent = 0.0
    n_success = 0
    n_fail = 0
    n_zlib_hit = 0
    n_zlib_attempted = 0  # books actually sent to z-lib (hit + real-fail + quota-trigger)
    n_curated_local = 0   # books served from local curated library (no z-lib call)
    n_dedup_reuse = 0
    n_halt = 0
    n_books_attempted = 0
    n_nonbook_attempted = 0
    book_failures: list[Dict[str, Any]] = []  # genuinely attempted z-lib downloads that failed
    quota_skipped: list[Dict[str, Any]] = []  # books NEVER attempted — quota was already gone
    dedup_reuse_entries: list[Dict[str, str]] = []  # {"chapter": ..., "book": ...}
    # Per-run book-level dedup cache: book identity -> alt_source_url. Chapter
    # rows of a book already fetched this run reuse its extraction instead of
    # re-downloading (saves z-lib quota + login load).
    book_cache: Dict[tuple, str] = {}
    # Parallel map (same keys as book_cache) of book identity -> first-seen
    # display title, so dedup-reuse can name WHICH book a chapter came from.
    book_cache_titles: Dict[tuple, str] = {}
    # Once z-library reports daily quota exhaustion, skip remaining z-library
    # attempts for this run. Per user preference (2026-05-21): on Book failure
    # we do NOT fall through to web-search analog — we mark Reachable=no and
    # surface the failure list via Telegram so Gonzalo can source manually.
    # Quota refresh is daily on z-lib side; the next run picks them up.
    zlib_quota_exhausted = False

    for i, row in enumerate(unreachable, start=1):
        title = row.get("title", "")[:60]
        type_ = (row.get("type") or "").lower()
        print(f"\n[{i}/{len(unreachable)}] {title}  (type={row.get('type')})")

        if spent + COST_PER_ROW_FORECAST > args.max_cost_usd:
            print(f"  HALT: budget guard ${spent:.2f} + ${COST_PER_ROW_FORECAST} > ${args.max_cost_usd}")
            n_halt = len(unreachable) - i + 1
            break

        alt_url: Optional[str] = None
        reason = ""
        book_skip_to_unreachable = False
        skipped_by_quota = False  # never attempted (quota gone) vs. attempted+failed

        if type_ == "book":
            n_books_attempted += 1
            ident = _book_identity(row)
            # First: check books/<slug>/ for a manually curated copy. Skips
            # z-library entirely on hit — saves quota + bandwidth.
            alt_url, reason = _check_curated_local(row)
            if alt_url:
                n_curated_local += 1
                book_cache.setdefault(ident, alt_url)
                book_cache_titles.setdefault(ident, row.get("title", ""))
                print(f"  curated-local hit -> {alt_url}")
            elif book_cache.get(ident):
                # Same book already fetched earlier this run (another chapter):
                # reuse its extraction, no re-search/re-download.
                alt_url = book_cache[ident]
                reason = "dedup-reuse: same book already fetched earlier this run"
                n_dedup_reuse += 1
                dedup_reuse_entries.append({
                    "chapter": str(row.get("title") or ""),
                    "book": str(book_cache_titles.get(ident) or row.get("title") or ""),
                })
                print(f"  dedup hit (same book this run) -> {alt_url}")
            elif zlib_quota_exhausted:
                # Daily quota already gone earlier this run — this book was
                # NOT attempted. It is deferred, not failed; z-lib refreshes
                # the quota daily and the next run picks it up automatically.
                print(f"  z-library skip (quota exhausted earlier in this run)")
                reason = "z-library: quota-exceeded (earlier in run)"
                book_skip_to_unreachable = True
                skipped_by_quota = True
            else:
                n_zlib_attempted += 1
                alt_url, reason = _try_zlibrary(row)
                if alt_url:
                    n_zlib_hit += 1
                    book_cache[ident] = alt_url
                    book_cache_titles.setdefault(ident, row.get("title", ""))
                    print(f"  z-library hit -> {alt_url}")
                elif "quota-exceeded" in reason:
                    # This book is the one that tripped the quota — it WAS
                    # attempted, so it counts as deferred (next run retries),
                    # not as a hard failure needing manual sourcing.
                    print(f"  z-library quota exceeded — disabling z-lib for rest of run: {reason}")
                    zlib_quota_exhausted = True
                    book_skip_to_unreachable = True
                    skipped_by_quota = True
                else:
                    # not-found, not-configured, image-only-pdf, subprocess-error,
                    # extract-failed — all map to "book unreachable, notify
                    # Gonzalo". No web-search analog fallback.
                    print(f"  z-library failed: {reason}")
                    book_skip_to_unreachable = True

            if book_skip_to_unreachable:
                _notion_update(
                    client,
                    page_id=row["page_id"],
                    reachable="no",
                    reachability_reason=reason or "z-library: failure",
                )
                _telemetry(row, "book-unreachable", 0.0)
                record = {
                    "page_id": row.get("page_id", ""),
                    "title": row.get("title", ""),
                    "author": row.get("author_host", ""),
                    "source_url": row.get("source_url", ""),
                    "reason": reason,
                }
                # Deferred-by-quota books are NOT failures: they were never
                # tried (or only tripped the limit) and auto-retry next run.
                (quota_skipped if skipped_by_quota else book_failures).append(record)
                n_fail += 1
                continue

        if not type_ == "book":
            n_nonbook_attempted += 1

        # Zlib-primary book that didn't hit: leave Notion alone (row was
        # already reachable via its original URL). No LLM fallback either —
        # the existing URL is the source of truth.
        if row.get("_zlib_only") and not alt_url:
            print(f"  z-lib miss on reachable book — keeping Notion state, no LLM fallback ({reason or 'no zlib hit'})")
            _telemetry(row, "zlib-only-miss", 0.0)
            n_fail += 1
            continue

        if not alt_url:
            try:
                proposal = _search_for_analog(row, model=args.model)
            except Exception as exc:  # noqa: BLE001
                print(f"  LLM error: {exc}")
                _telemetry(row, "llm-error", 0.0)
                n_fail += 1
                continue
            spent += proposal.cost_usd

            if not proposal.url:
                full_reason = reason + " | " + proposal.justification if reason else proposal.justification
                print(f"  no analog: {proposal.justification}")
                _notion_update(
                    client,
                    page_id=row["page_id"],
                    reachable="no",
                    reachability_reason=full_reason or "no fetchable analog found",
                )
                _telemetry(row, "no-analog", proposal.cost_usd)
                n_fail += 1
                continue

            verify = _verify_analog(proposal.url, http)
            if not verify.ok:
                full_reason = (
                    f"proposed {proposal.url} but verify failed: {verify.reason}"
                )
                print(f"  verify failed: {verify.reason}")
                _notion_update(
                    client,
                    page_id=row["page_id"],
                    reachable="no",
                    reachability_reason=full_reason,
                )
                _telemetry(row, "verify-failed", proposal.cost_usd)
                n_fail += 1
                continue

            alt_url = proposal.url
            reason = (
                f"web-search analog: {verify.reason} | {proposal.justification[:150]}"
            )
            print(f"  analog verified -> {alt_url}")

        _notion_update(
            client,
            page_id=row["page_id"],
            reachable="yes",
            reachability_reason=reason,
            alt_source_url=alt_url,
        )
        _telemetry(row, "augmented", 0.0)
        n_success += 1

    http.close()

    print("\n--- SUMMARY ---")
    print(f"Processed:        {n_success + n_fail}")
    print(f"  augmented:      {n_success}")
    print(f"  z-library hit:  {n_zlib_hit} / {n_zlib_attempted} attempted")
    print(f"  curated-local:  {n_curated_local}")
    print(f"  dedup-reuse:    {n_dedup_reuse} (same-book chapters)")
    print(f"  deferred (quota): {len(quota_skipped)}")
    print(f"  failed (attempted): {len(book_failures)}")
    print(f"  marked Reachable=no: {n_fail}")
    print(f"  halted/skipped: {n_halt}")
    print(f"LLM spend:        ${spent:.2f}")

    # Per-run failure log + Telegram summary (user preference 2026-05-21:
    # per-run digest, not per-row). Skipped on dry-run paths since those
    # return earlier.
    _write_failures_and_notify(
        book_failures=book_failures,
        quota_skipped=quota_skipped,
        dedup_reuse_entries=dedup_reuse_entries,
        n_zlib_hit=n_zlib_hit,
        n_zlib_attempted=n_zlib_attempted,
        n_curated_local=n_curated_local,
        n_dedup_reuse=n_dedup_reuse,
        n_success=n_success,
        n_halt=n_halt,
        zlib_quota_exhausted=zlib_quota_exhausted,
    )
    return 0


def _format_zlib_quota_line() -> Optional[str]:
    """Ask z-library for the account's real daily download allowance so the
    summary shows actual used/limit/remaining instead of guesswork. Explains
    why hits this run can be < the assumed daily cap (prior usage today, or the
    server limit simply isn't what we assumed). Best-effort: None on any error."""
    try:
        limits = zlib_get_limits()
    except Exception:  # noqa: BLE001
        return None
    if isinstance(limits, BookFailure):
        return None
    daily = limits.get("daily_limit")
    remaining = limits.get("remaining")
    if daily is None or remaining is None:
        return None
    used = max(0, daily - remaining)
    return f"   account quota today: {used}/{daily} used ({remaining} left, resets daily)"


def _write_failures_and_notify(
    *,
    book_failures: list[Dict[str, Any]],
    quota_skipped: list[Dict[str, Any]],
    dedup_reuse_entries: list[Dict[str, str]],
    n_zlib_hit: int,
    n_zlib_attempted: int,
    n_curated_local: int = 0,
    n_dedup_reuse: int = 0,
    n_success: int,
    n_halt: int,
    zlib_quota_exhausted: bool,
) -> None:
    today = time.strftime("%Y-%m-%d")
    failures_path = AUGMENT_FAILURES_DIR / f"augment-failures-{today}.jsonl"
    # Persist BOTH attempted-failures and quota-deferred books, tagged by
    # category, so the JSONL is the full record behind the digest.
    all_records = (
        [{**r, "category": "failed"} for r in book_failures]
        + [{**r, "category": "deferred-quota"} for r in quota_skipped]
    )
    if all_records:
        AUGMENT_FAILURES_DIR.mkdir(parents=True, exist_ok=True)
        with failures_path.open("w") as f:
            for rec in all_records:
                f.write(json.dumps(rec) + "\n")

    # web-search analogs = successes that weren't z-lib, curated-local, or dedup.
    nonbook_aug = max(0, n_success - n_zlib_hit - n_curated_local - n_dedup_reuse)

    # Header status: a genuinely attempted+failed book is the only thing that
    # warrants a warning. Hitting the daily quota is NOT a failure — it means we
    # pulled everything today's allowance permitted, so it stays ✅.
    status = "🟡" if book_failures else "✅"
    lines = [f"{status} augment-research run finished {today}"]

    # --- z-library downloads ---
    if n_zlib_attempted:
        lines.append(f"   z-library: {n_zlib_hit}/{n_zlib_attempted} download attempts succeeded")
    if zlib_quota_exhausted:
        lines.append("   ✅ daily z-library quota reached — downloaded everything today's quota allowed")
        quota_line = _format_zlib_quota_line()
        if quota_line:
            lines.append(quota_line)

    # --- other success channels ---
    if n_curated_local:
        lines.append(f"   curated-local: {n_curated_local} books from local library")
    if n_dedup_reuse:
        lines.append(f"   dedup-reuse: {n_dedup_reuse} chapters reused from a book already fetched this run:")
        for e in dedup_reuse_entries[:5]:
            chapter = (e.get("chapter") or "?")[:55]
            book = e.get("book") or ""
            if book and book != e.get("chapter"):
                lines.append(f"     • {chapter} ← {book[:45]}")
            else:
                lines.append(f"     • {chapter}")
        if len(dedup_reuse_entries) > 5:
            lines.append(f"     • … and {len(dedup_reuse_entries) - 5} more")
    if nonbook_aug:
        lines.append(f"   web-search analogs: {nonbook_aug} (papers/podcasts)")

    # --- deferred by quota: NOT failures, auto-retried next run ---
    if quota_skipped:
        lines.append(
            f"   deferred to next run (quota): {len(quota_skipped)} books not attempted — auto-retried tomorrow"
        )
        for fail in quota_skipped[:5]:
            t = (fail.get("title") or "?")[:70]
            lines.append(f"     • {t}")
        if len(quota_skipped) > 5:
            lines.append(f"     • … and {len(quota_skipped) - 5} more")

    # --- genuine failures: attempted, need manual sourcing ---
    if book_failures:
        lines.append(
            f"   failed (attempted, need manual sourcing): {len(book_failures)} — see reports/augment-failures-{today}.jsonl"
        )
        for fail in book_failures[:5]:
            t = (fail.get("title") or "?")[:70]
            lines.append(f"     • {t}")
        if len(book_failures) > 5:
            lines.append(f"     • … and {len(book_failures) - 5} more")
    elif not quota_skipped:
        lines.append("   failed books: 0 ✅")

    if n_halt:
        lines.append(f"   halted (budget): {n_halt} rows unprocessed")

    msg = "\n".join(lines)
    try:
        telegram_send(msg)
    except Exception as exc:  # noqa: BLE001
        print(f"[telegram] send failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
