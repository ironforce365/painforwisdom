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
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List

import httpx

from pipeline.contracts import assert_inputs
from pipeline.llm import call_llm
from pipeline.runtime import (
    VAULT_PATH,
    append_metric,
    load_agent_prompt,
    run_telemetry_path,
)
from pipeline.state import State


_RESEARCH_OUTPUT_SPEC = """\

## OUTPUT SPEC (STRICT)

Use the web_search tool aggressively to verify EVERY reference before
including it. Do not include any reference whose existence and contents you
have not confirmed via at least one web search result.

After research, reply with ONLY the CSV block below. No prose, no markdown
outside the fenced block.

```csv
Title,Type,Author/Host,Specific Location,Category,Research Angle,Relevance,Source URL,Paywall,Coaching Theme,Vault Entry
"<title>","<Book|Podcast|Paper|Video/Talk|Article>","<author or host>","<chapter/episode/section>","<Comprehensive Understanding|Going Deeper>","<angle slug>","<one-sentence relevance>","<https url or empty>","<true|false>","<single-theme-slug>","<vault-entry-slug>"
...
```

Rules:
- One row per VERIFIED reference. No row may include unverifiable detail.
- Coaching Theme is a SINGLE slug. Pick the best match per the agent rules above.
- Vault Entry is the slug passed in input (no .md extension).
- Source URL is HTTPS or empty. No tracking parameters.
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


def _verify_urls(rows: List[Dict[str, str]], timeout: float = 5.0) -> Dict[str, str]:
    """HEAD-check each non-empty Source URL. Returns map url -> status string."""
    out: Dict[str, str] = {}
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for r in rows:
            url = r.get("Source URL", "").strip()
            if not url or not url.startswith("http"):
                continue
            try:
                resp = client.head(url)
                out[url] = f"{resp.status_code}"
            except httpx.HTTPError as exc:
                out[url] = f"err:{type(exc).__name__}"
    return out


def node_research(state: State) -> Dict[str, Any]:
    assert_inputs("research", state)
    t0 = time.time()
    print("[research] start")

    system_prompt = _build_system_prompt()
    user_msg = (
        f"## INPUTS\n"
        f"Vault entry slug: {state.get('vault_entry_slug','')}\n"
        f"Vault path: {VAULT_PATH}\n\n"
        f"## EXTRACTION REPORT\n```\n{state.get('extraction_report','')}\n```\n\n"
        f"## CURRENT RESEARCH INDEX (last 40 lines, for de-duplication)\n"
        f"```\n{_read_research_index_summary()}\n```\n\n"
        f"Identify 2-4 research angles, then web_search to verify references "
        f"before producing the CSV block.\n"
    )
    model = os.environ.get("PIPELINE_MODEL", "claude-sonnet-4-6")
    result = call_llm(model, system_prompt, user_msg, max_tokens=3000, web_search=True)
    text = result["text"]

    rows = _parse_csv(text)
    if not rows:
        raise RuntimeError("research: no CSV rows parsed from model output:\n" + text[:600])

    # URL verification — soft check; non-200s are flagged in metrics, not fatal,
    # because some legitimate sources block HEAD or paywall the URL.
    url_status = _verify_urls(rows)
    bad = {u: s for u, s in url_status.items() if not s.startswith(("2", "3"))}
    if bad:
        print(f"[research] WARN {len(bad)} URLs returned non-2xx: {list(bad.items())[:3]}")

    run_dir = Path(state["run_dir"])
    out_dir = run_dir / "research-curator"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "research_report.csv"
    fence_match = _CSV_FENCE.search(text)
    csv_body = fence_match.group(1).strip() + "\n" if fence_match else text
    csv_path.write_text(csv_body)

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
        urls_checked=len(url_status),
        urls_bad=len(bad),
    )
    print(f"[research] done {duration:.1f}s rows={len(rows)} urls_bad={len(bad)}")
    return {
        "research_csv_path": str(csv_path),
        "research_count": len(rows),
    }
