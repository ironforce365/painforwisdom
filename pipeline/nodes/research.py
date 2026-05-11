"""Stage 4b — research-curator with Anthropic native web_search tool.

Loads the research-curator agent prompt, supplies the extraction report +
existing research index, and asks Sonnet 4.6 (with web_search_20250305 tool
attached) for verified references. Output is a CSV row block; we parse it,
write it to disk, optionally HEAD-check the URLs, and store the row count.

Web search is server-side: results count toward billing on the API path
($10 / 1k searches as of 2025-04) but are $0 on subscription. Results from
search are also inflated input tokens; that's accepted as part of the cost.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List

import httpx

try:
    import trafilatura
except ImportError:  # pragma: no cover
    trafilatura = None  # type: ignore

from pipeline.banned_sources import banned_reason
from pipeline.contracts import assert_inputs
from pipeline.llm import call_llm
from pipeline.runtime import (
    VAULT_PATH,
    append_metric,
    load_agent_prompt,
    run_telemetry_path,
)
from pipeline.state import State


_PAYWALL_PHRASES = (
    "subscribe to continue",
    "sign in to read",
    "subscribers only",
    "members only",
    "create a free account to continue",
    "to continue reading",
    "this content is for subscribers",
    "log in to read",
)
_TRAFILATURA_MIN_CHARS = 500


_RESEARCH_OUTPUT_SPEC = """\

## OUTPUT SPEC (STRICT)

Use the web_search tool aggressively to verify EVERY reference before
including it. Do not include any reference whose existence and contents you
have not confirmed via at least one web search result.

After research, reply with ONLY the CSV block below. No prose, no markdown
outside the fenced block.

```csv
Title,Type,Author/Host,Specific Location,Category,Research Angle,Relevance,Source URL,Paywall,Coaching Theme,Vault Entry,Reachable,Reachability Reason
"<title>","<Book|Podcast|Paper|Video/Talk|Article>","<author or host>","<chapter/episode/section>","<Comprehensive Understanding|Going Deeper>","<angle slug>","<one-sentence relevance>","<https url or empty>","<true|false>","<single-theme-slug>","<vault-entry-slug>","<yes|no|unknown>","<short reason or empty>"
...
```

Rules:
- One row per VERIFIED reference. No row may include unverifiable detail.
- Coaching Theme is a SINGLE slug. Pick the best match per the agent rules above.
- Vault Entry is the slug passed in input (no .md extension).
- Source URL is HTTPS or empty. No tracking parameters.
- Reachable: `yes` by default since the Reachability gate in the curator prompt
  means proposed URLs are already verified as fetchable. Use `no` only when
  including a paywalled-but-canonical reference (book chapter, hard paywall) for
  bibliographic completeness; in that case fill Reachability Reason.
- Banned domains (Source URL must NOT be on this list): amazon.com,
  goodreads.com, archive.org, pubmed.ncbi.nlm.nih.gov, jstor.org,
  sciencedirect.com, springer.com, wiley.com, tandfonline.com,
  journals.sagepub.com, paid news sites. If the only canonical URL is one of
  these, find a freely-readable analog and put THAT as Source URL.
- Quote every field. Escape inner quotes by doubling: `""`.
- Aim for 4-8 rows total — quality over quantity.
- If an entire angle yields no verifiable references, skip the angle. Do not pad.
"""


_CSV_FENCE = re.compile(r"```csv\s*\n(.*?)```", re.DOTALL)


def _build_system_prompt() -> str:
    return load_agent_prompt("research-curator.md") + _RESEARCH_OUTPUT_SPEC


def _coerce_csv_value(v: Any) -> str:
    """DictReader can yield None (missing field) or list (extra fields beyond
    header count, bucketed under the None key when the model under-quotes a
    value with commas). Coerce to a plain stripped string so downstream code
    can call .strip() / .lower() safely."""
    if v is None:
        return ""
    if isinstance(v, list):
        return ",".join(str(x) for x in v).strip()
    return str(v).strip()


def _parse_csv(text: str) -> List[Dict[str, str]]:
    m = _CSV_FENCE.search(text)
    if not m:
        # Some models drop the fence — try to read whole response as CSV
        body = text.strip()
    else:
        body = m.group(1).strip()
    reader = csv.DictReader(io.StringIO(body))
    rows: List[Dict[str, str]] = []
    for row in reader:
        # Drop the None bucket DictReader uses for extra fields, otherwise
        # downstream code sees a key=None mapped to a list.
        rows.append({k: _coerce_csv_value(v) for k, v in row.items() if k is not None})
    return rows


def _read_research_index_summary() -> str:
    idx = VAULT_PATH / "gonzalo-book" / "research-index.md"
    if not idx.is_file():
        return "(research-index.md does not exist yet)"
    text = idx.read_text()
    # Last ~30 rows is enough context for de-duplication.
    return "\n".join(text.splitlines()[-40:])


_THEME_STATS_PATH = Path(__file__).resolve().parents[2] / "pipeline" / "state" / "theme_stats.json"


def _render_theme_stats_summary() -> str:
    """Render theme_stats.json into a compact block for the curator's user msg.
    Empty string when the file is missing — curator falls back to default behaviour."""
    if not _THEME_STATS_PATH.exists():
        return ""
    try:
        stats = json.loads(_THEME_STATS_PATH.read_text())
    except json.JSONDecodeError:
        return ""
    saturated = [
        (name, info)
        for name, info in stats.get("themes", {}).items()
        if info.get("saturated")
    ]
    if not saturated:
        return ""
    lines = [
        "## THEME SATURATION INTEL",
        f"As of {stats.get('as_of','?')}, saturation threshold = "
        f"{stats.get('saturation_threshold', 30)}.",
        "Saturated themes require novel angle OR sub-theme split OR zero references.",
        "",
    ]
    for name, info in saturated:
        lines.append(f"### {name}  (pending={info['pending']} total={info['total']})")
        for ang in (info.get("covered_angles") or [])[:30]:
            lines.append(f"- {ang}")
        lines.append("")
    return "\n".join(lines)


def _classify_url(url: str, client: httpx.Client) -> tuple[str, str]:
    """Return (reachable_state, reason). reachable_state ∈ {yes,no,unknown}."""
    if not url or not url.startswith("http"):
        return "unknown", "no-http-url"

    banned = banned_reason(url)
    if banned:
        return "no", f"banned-domain: {banned}"

    try:
        resp = client.get(url, follow_redirects=True, timeout=10.0)
    except httpx.HTTPError as exc:
        return "no", f"http-error: {type(exc).__name__}: {exc}"
    if resp.status_code != 200:
        return "no", f"http-status: {resp.status_code}"

    body = (resp.text or "")[:500_000]
    lowered = body.lower()
    if any(p in lowered for p in _PAYWALL_PHRASES):
        return "no", "paywall-phrase-in-body"

    if trafilatura is None:
        return "unknown", "trafilatura-missing"
    try:
        extracted = trafilatura.extract(body) or ""
    except Exception as exc:  # noqa: BLE001
        return "unknown", f"trafilatura-error: {exc}"
    if len(extracted) < _TRAFILATURA_MIN_CHARS:
        return "no", f"thin-extract: {len(extracted)} chars"
    return "yes", f"verified {len(extracted)} chars"


def _verify_rows(
    rows: List[Dict[str, str]], timeout: float = 10.0
) -> tuple[List[Dict[str, str]], int]:
    """Classify each row's Source URL, mutate Reachable + Reachability Reason
    in-place when missing, drop rows the curator marked Reachable=no with no
    Alt URL (the daily summarizer can't use them anyway). Returns the surviving
    rows and the count of dropped rows."""
    kept: List[Dict[str, str]] = []
    dropped = 0
    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 painforwisdom-research/3"},
    ) as client:
        for r in rows:
            url = (r.get("Source URL") or "").strip()
            existing_reach = (r.get("Reachable") or "").strip().lower()
            existing_reason = (r.get("Reachability Reason") or "").strip()

            if not existing_reach:
                state, reason = _classify_url(url, client)
                r["Reachable"] = state
                if not existing_reason:
                    r["Reachability Reason"] = reason
            elif existing_reach == "yes":
                # Curator already vouched — only run defense-in-depth banned check.
                banned = banned_reason(url)
                if banned:
                    r["Reachable"] = "no"
                    r["Reachability Reason"] = (
                        existing_reason + f" | OVERRIDDEN: banned-domain {banned}"
                    ).strip(" |")

            if r.get("Reachable") == "no":
                dropped += 1
                # Still keep the row in the CSV — Phase 2b/Notion needs it
                # recorded with Reachable=no. Daily summarizer filters on Reachable=yes.
            kept.append(r)
    return kept, dropped


def node_research(state: State) -> Dict[str, Any]:
    assert_inputs("research", state)
    t0 = time.time()
    print("[research] start")

    system_prompt = _build_system_prompt()
    theme_stats_block = _render_theme_stats_summary()
    user_msg = (
        f"## INPUTS\n"
        f"Vault entry slug: {state.get('vault_entry_slug','')}\n"
        f"Vault path: {VAULT_PATH}\n\n"
        f"## EXTRACTION REPORT\n```\n{state.get('extraction_report','')}\n```\n\n"
        f"## CURRENT RESEARCH INDEX (last 40 lines, for de-duplication)\n"
        f"```\n{_read_research_index_summary()}\n```\n\n"
        + (f"{theme_stats_block}\n\n" if theme_stats_block else "")
        + "Identify 2-4 research angles, then web_search to verify references "
        f"before producing the CSV block.\n"
    )
    model = os.environ.get("PIPELINE_MODEL", "claude-sonnet-4-6")
    result = call_llm(model, system_prompt, user_msg, max_tokens=3000, web_search=True)
    text = result["text"]

    rows = _parse_csv(text)
    if not rows:
        raise RuntimeError("research: no CSV rows parsed from model output:\n" + text[:600])

    # Phase 3b — full reachability check: HEAD→GET→trafilatura→paywall + banned.
    # Mutates rows in-place: Reachable / Reachability Reason filled when missing.
    rows, urls_dropped_unreachable = _verify_rows(rows)
    if urls_dropped_unreachable:
        print(f"[research] {urls_dropped_unreachable} URL(s) classified Reachable=no")

    run_dir = Path(state["run_dir"])
    out_dir = run_dir / "research-curator"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "research_report.csv"

    # Re-emit CSV from the verified rows (preserves Reachable + Reachability
    # Reason fills) rather than echoing the model's original fenced block.
    output = io.StringIO()
    fieldnames = [
        "Title", "Type", "Author/Host", "Specific Location", "Category",
        "Research Angle", "Relevance", "Source URL", "Paywall",
        "Coaching Theme", "Vault Entry", "Reachable", "Reachability Reason",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, quoting=csv.QUOTE_ALL, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r.get(k, "") for k in fieldnames})
    csv_path.write_text(output.getvalue())

    duration = time.time() - t0
    append_metric(
        run_telemetry_path(state["run_dir"]),
        "research",
        duration_s=round(duration, 2),
        model=result["model"],
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        cache_read_tokens=result["cache_read_tokens"],
        cache_creation_tokens=result["cache_creation_tokens"],
        cost_usd=round(result["cost_usd"], 6),
        rows_count=len(rows),
        urls_dropped_unreachable=urls_dropped_unreachable,
    )
    print(
        f"[research] done {duration:.1f}s rows={len(rows)} "
        f"unreachable={urls_dropped_unreachable}"
    )
    return {
        "research_csv_path": str(csv_path),
        "research_count": len(rows),
    }
