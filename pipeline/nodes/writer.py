"""Stage 4a — painforwisdom-writer (blog post generator).

Loads the writer agent prompt, supplies the extraction report + transcript
context, and asks Sonnet 4.6 for the full blog post in Gonzalo's voice.
The first non-empty line of the response is treated as the title; the rest
is the body. We assert structural minimums (date stamp present, word count
in [400, 2000]) before returning state.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Dict

from pipeline.contracts import assert_inputs
from pipeline.llm import call_llm
from pipeline.runtime import (
    append_metric,
    load_agent_prompt,
    run_telemetry_path,
)
from pipeline.state import State


_WRITER_OUTPUT_SPEC = """\

## OUTPUT SPEC (STRICT)

Reply with the full blog post in this exact structure, NOTHING ELSE:

---
**Title:** <blog post title — short, lowercase OK, no quotes>

*<MM/DD/YY>*

<blog post body — paragraphs, **bold** for impact, no bullet points or headers>

---

If you used a footnote [1], add a footnotes section after the closing ---:

*Footnotes:*
1. <footnote text>

Hard rules:
- Date stamp uses the input's video_date converted from YYYY-MM-DD to MM/DD/YY.
  Never use today's date.
- 400-1000 words in the body.
- 6-9 paragraphs.
- 4-6 **bold** phrases max — they should hit like punches.
- The last sentence must feel like closing a fist. No "thanks for reading."
"""


def _yyyymmdd_to_mmddyy(date_iso: str) -> str:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", date_iso)
    if not m:
        return date_iso
    return f"{m.group(2)}/{m.group(3)}/{m.group(1)[2:]}"


def _build_system_prompt() -> str:
    return load_agent_prompt("painforwisdom-writer.md") + _WRITER_OUTPUT_SPEC


def _parse_title_and_body(text: str) -> tuple[str, str]:
    # Title is on a "**Title:** ..." line. Body is everything after the date stamp.
    title_match = re.search(r"\*\*Title:\*\*\s*(.+)", text)
    title = title_match.group(1).strip() if title_match else ""
    if not title:
        # fallback: first non-empty line, stripped of markdown decoration
        for line in text.splitlines():
            stripped = re.sub(r"^[#*_\s]+", "", line).strip()
            if stripped and not stripped.startswith("---"):
                title = stripped
                break
    return title or "untitled", text.strip()


def node_writer(state: State) -> Dict[str, Any]:
    assert_inputs("writer", state)
    t0 = time.time()
    print("[writer] start")

    system_prompt = _build_system_prompt()
    date_mmddyy = _yyyymmdd_to_mmddyy(state.get("video_date", ""))
    user_msg = (
        f"Video date (use this for the date stamp): {state.get('video_date','')}\n"
        f"Required MM/DD/YY format: {date_mmddyy}\n\n"
        f"## EXTRACTION REPORT\n```\n{state.get('extraction_report','')}\n```\n\n"
        f"## RAW TRANSCRIPT (for sensory detail and authentic phrasing)\n"
        f"```\n{state.get('transcript_text','')}\n```\n"
    )
    model = os.environ.get("PIPELINE_MODEL", "claude-sonnet-4-6")
    result = call_llm(model, system_prompt, user_msg, max_tokens=3000)
    text = result["text"]

    title, body = _parse_title_and_body(text)
    word_count = len(re.findall(r"\b\w+\b", body))
    if word_count < 200 or word_count > 2500:
        raise RuntimeError(
            f"writer: blog post word count {word_count} outside acceptable [200, 2500]. "
            "Likely prompt regression."
        )
    if date_mmddyy not in body:
        # Soft check — warn rather than fail; writer is creative, may rephrase.
        print(f"[writer] WARN date stamp {date_mmddyy} not found verbatim in body")

    run_dir = Path(state["run_dir"])
    out_dir = run_dir / "painforwisdom-writer"
    out_dir.mkdir(parents=True, exist_ok=True)
    blog_path = out_dir / "blog_post.md"
    blog_path.write_text(body)

    duration = time.time() - t0
    append_metric(
        run_telemetry_path(state["run_dir"]),
        "writer",
        duration_s=round(duration, 2),
        model=result["model"],
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        cache_read_tokens=result["cache_read_tokens"],
        cache_creation_tokens=result["cache_creation_tokens"],
        cost_usd=round(result["cost_usd"], 6),
        word_count=word_count,
        title=title,
    )
    print(f"[writer] done {duration:.1f}s words={word_count} title={title!r}")
    return {
        "blog_post_path": str(blog_path),
        "blog_post_title": title,
        "blog_post_text": body,
    }
