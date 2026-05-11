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
from pipeline.notion_client import get_client  # noqa: E402
from pipeline.runtime import append_metric  # noqa: E402
from pipeline.zlibrary_bridge import BookFailure, BookText, fetch as zlib_fetch  # noqa: E402

AUGMENT_LOG_PATH = PROJECT_ROOT / "reports" / "augment-runs.jsonl"


COST_PER_ROW_FORECAST = 0.05
TRAFILATURA_MIN_CHARS = 500
WEB_FETCH_TIMEOUT = 15.0
NOTION_PACING_S = 0.5

EXTRACTED_BOOKS_DIR = PROJECT_ROOT / "books" / "extracted"
CURATED_BOOKS_DIR = PROJECT_ROOT / "books"
# Subdirs that are NOT manually-curated book folders.
CURATED_SKIP_DIRS = frozenset({"extracted", "raw"})
CURATED_BOOK_SUFFIXES = frozenset({".epub", ".pdf", ".txt", ".mobi", ".azw3"})

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


def _title_slug_candidates(title: str) -> list[str]:
    """Map a Notion row title to ordered candidate slugs for books/<slug>/.

    Tries the broader (full-title) slug first, then narrower (subtitle-stripped)
    slugs. Each slug capped at 80 chars to match how the user names folders.
    """
    candidates: list[str] = []

    def add(text: str) -> None:
        slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80]
        if slug and slug not in candidates:
            candidates.append(slug)

    # 1. Full title (no stripping) — matches folders named after the full title.
    add(title)
    # 2. Strip chapter markers like "-- Ch. 8 ...".
    no_chapters = re.split(r"\s+--\s+|\s+—\s+", title, maxsplit=1)[0]
    no_chapters = re.sub(r"\bch\.?\s*\d+\b", "", no_chapters, flags=re.IGNORECASE)
    add(no_chapters)
    # 3. Strip colon-subtitle.
    no_subtitle = re.split(r":\s+", no_chapters, maxsplit=1)[0]
    add(no_subtitle)
    return candidates


def _check_curated_local(row: Dict[str, Any]) -> tuple[Optional[str], str]:
    """Check `books/<slug>/` for a manually-curated copy. Returns
    (alt_source_url, reason) on hit, (None, "") on miss."""
    title = row.get("title", "")
    for slug in _title_slug_candidates(title):
        folder = CURATED_BOOKS_DIR / slug
        if not folder.is_dir() or folder.name in CURATED_SKIP_DIRS:
            continue
        files = [
            f for f in folder.iterdir()
            if f.is_file() and f.suffix.lower() in CURATED_BOOK_SUFFIXES
        ]
        if not files:
            continue
        # Pick largest file (full book over excerpts).
        chosen = max(files, key=lambda f: f.stat().st_size)
        return (
            f"file://{chosen}",
            f"curated-local: {chosen.suffix.lstrip('.')} ({chosen.stat().st_size} bytes)",
        )
    return None, ""


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
    if args.skip_books:
        unreachable = [r for r in unreachable if (r.get("type") or "").lower() != "book"]
    if args.limit is not None:
        unreachable = unreachable[: args.limit]

    print(f"Total rows in audit: {len(rows)}")
    print(f"Unreachable to augment: {len(unreachable)}")
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
    n_halt = 0

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

        if type_ == "book":
            # First: check books/<slug>/ for a manually curated copy. Skips
            # z-library entirely on hit — saves quota + bandwidth.
            alt_url, reason = _check_curated_local(row)
            if alt_url:
                print(f"  curated-local hit -> {alt_url}")
            else:
                alt_url, reason = _try_zlibrary(row)
                if alt_url:
                    n_zlib_hit += 1
                    print(f"  z-library hit -> {alt_url}")
                elif "quota-exceeded" in reason:
                    print(f"  HALT: Z-Library quota exceeded — {reason}")
                    n_halt = len(unreachable) - i + 1
                    break
                elif "not-configured" in reason:
                    print(f"  z-library skip (not configured) — falling through to web search")
                    reason = ""

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
    print(f"  z-library hit:  {n_zlib_hit}")
    print(f"  marked Reachable=no: {n_fail}")
    print(f"  halted/skipped: {n_halt}")
    print(f"LLM spend:        ${spent:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
