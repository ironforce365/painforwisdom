"""Stage 2 — coaching-thought-extractor.

Loads the existing agent prompt body (stripped of frontmatter and OUTPUT
section), appends the EXECUTION CONTEXT block, and asks Sonnet 4.6 for the
structured extraction report. The full report goes to state and to disk;
key fields (Content Quality, Core Insight, Story Anchor, Blog Post Seed)
are parsed out for downstream nodes.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List

from pipeline.contracts import assert_inputs
from pipeline.llm import call_llm
from pipeline.runtime import (
    append_metric,
    load_agent_prompt,
    parse_extraction_field,
    run_telemetry_path,
)
from pipeline.state import State


_EXTRACT_OUTPUT_SPEC = """\

## OUTPUT SPEC

Reply with ONLY the structured report below — no preamble, no closing remarks.

---
### COACHING THOUGHT EXTRACTION REPORT

**Content Quality:** [Strong / Weak / Flagged]

**If weak or flagged, reason:**
(explain specifically why)

---

**Core Insight:**
(one sentence — the distilled lesson)

**Story Anchor:**
(specific moment from the transcript that grounds this — concrete sensory detail)

**Framework Connection:**
(which named framework this entry connects to — or "(none)")

**Practical Application:**
(what someone would actually do)

**Who It's For:**
(specific person — concrete, not generic)

**Integrity Check:**
(is Gonzalo applying this himself, or is he extrapolating to others — one sentence)

**Blog Post Seed:**
(2-3 sentences ready for a writer)

**Themes:**
(comma-separated list of 1-3 theme slugs in kebab-case)

**Frameworks:**
(comma-separated list of 0-3 framework slugs in kebab-case, or "(none)")
---
"""


def _build_system_prompt() -> str:
    return load_agent_prompt("coaching-thought-extractor.md") + _EXTRACT_OUTPUT_SPEC


def _parse_list_field(report: str, field: str) -> List[str]:
    raw = parse_extraction_field(report, field) or ""
    raw = raw.strip().strip("()")
    if not raw or raw.lower() == "none":
        return []
    return [s.strip() for s in re.split(r"[,;]", raw) if s.strip()]


def node_extract(state: State) -> Dict[str, Any]:
    assert_inputs("extract", state)
    t0 = time.time()
    print("[extract] start")

    system_prompt = _build_system_prompt()
    user_msg = (
        f"Video date: {state.get('video_date','')}\n"
        f"Transcript file: {os.path.basename(state.get('transcript_path',''))}\n\n"
        f"Transcript:\n{state['transcript_text']}\n"
    )
    model = os.environ.get("PIPELINE_MODEL", "claude-sonnet-4-6")
    result = call_llm(model, system_prompt, user_msg, max_tokens=2000)

    text = result["text"]

    # Always stash the raw response to disk so debugging is possible regardless
    # of parse outcome.
    run_dir = Path(state["run_dir"])
    out_dir = run_dir / "coaching-thought-extractor"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "extraction_report.md"
    report_path.write_text(text)

    if "Content Quality" not in text or "Core Insight" not in text:
        raise RuntimeError(
            "extract: response missing required structural fields "
            "(Content Quality / Core Insight). See: " + str(report_path)
        )

    quality_raw = parse_extraction_field(text, "Content Quality") or ""
    quality_lines = [ln for ln in quality_raw.splitlines() if ln.strip()]
    quality = quality_lines[0].strip() if quality_lines else "?"
    core_insight = parse_extraction_field(text, "Core Insight") or "(unparsed)"
    story_anchor = parse_extraction_field(text, "Story Anchor") or ""
    blog_seed = parse_extraction_field(text, "Blog Post Seed") or ""
    themes = _parse_list_field(text, "Themes")
    frameworks = _parse_list_field(text, "Frameworks")

    duration = time.time() - t0
    append_metric(
        run_telemetry_path(state["run_dir"]),
        "extract",
        duration_s=round(duration, 2),
        model=result["model"],
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        cache_read_tokens=result["cache_read_tokens"],
        cache_creation_tokens=result["cache_creation_tokens"],
        cost_usd=round(result["cost_usd"], 6),
        billing_mode=result["billing_mode"],
        quality=quality,
    )
    print(f"[extract] done {duration:.1f}s quality={quality} themes={themes}")
    return {
        "extraction_report": text,
        "extraction_report_path": str(report_path),
        "content_quality": quality,
        "core_insight": core_insight,
        "story_anchor": story_anchor,
        "blog_post_seed": blog_seed,
        "themes": themes,
        "frameworks": frameworks,
    }
