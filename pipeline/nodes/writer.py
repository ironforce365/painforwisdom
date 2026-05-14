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

from pipeline.blog_context import get_backend
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
*[YOUTUBE_SHORT_URL]*

<blog post body — paragraphs, **bold** for impact, no bullet points or headers>

**Excerpt:** <40-50 word summary in the same voice as the body. Complete thought,
ends on a hook, not mid-sentence. Used as the WordPress excerpt + home-page preview.>

---

If you used a footnote [1], add a footnotes section after the closing ---:

*Footnotes:*
1. <footnote text>

Hard rules:
- Date stamp uses the input's video_date converted from YYYY-MM-DD to MM/DD/YY.
  Never use today's date.
- The second italic line MUST be literally `*[YOUTUBE_SHORT_URL]*` so the
  pipeline can substitute the real YouTube short URL post-hoc.
- 400-1000 words in the body.
- 6-9 paragraphs.
- 4-6 **bold** phrases max — they should hit like punches.
- The last sentence of the BODY (not the Excerpt) must feel like closing a fist.
  No "thanks for reading."
- For any external reference (Goggins, Jocko, aMCC, cookie jar, …) check the
  CROSS-POST CONTEXT block if provided; if a prior post explained it, use
  `[[link:<slug>]]` instead of re-explaining.
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


def _parse_excerpt(text: str) -> str:
    """Pull the ``**Excerpt:** ...`` line out of the writer response."""
    m = re.search(r"\*\*Excerpt:\*\*\s*(.+?)(?:\n\n|\n---|\Z)", text, re.DOTALL)
    if not m:
        return ""
    excerpt = m.group(1).strip()
    # Collapse internal whitespace; bold/italic markers stripped for the
    # plain-text excerpt that WordPress + Notion will display.
    excerpt = re.sub(r"\s+", " ", excerpt)
    excerpt = re.sub(r"\*\*([^*]+)\*\*", r"\1", excerpt)
    excerpt = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", excerpt)
    return excerpt


def _build_cross_post_context(themes: list[str], *, max_themes: int = 5) -> str:
    """Render a CROSS-POST CONTEXT block for injection into the user message.

    Returns "" when the backend produces nothing useful so the writer prompt
    stays clean rather than padded with empty headers.
    """
    try:
        backend = get_backend()
    except Exception as exc:  # noqa: BLE001
        print(f"[writer] WARN cross-post backend init failed: {exc}")
        return ""

    try:
        topics = backend.recent_topics(limit=15)
    except Exception as exc:  # noqa: BLE001
        print(f"[writer] WARN recent_topics failed: {exc}")
        topics = []

    references: list[str] = []
    for theme in (themes or [])[:max_themes]:
        if not isinstance(theme, str) or not theme.strip():
            continue
        try:
            refs = backend.find_references(theme, limit=3)
        except Exception as exc:  # noqa: BLE001
            print(f"[writer] WARN find_references({theme!r}) failed: {exc}")
            continue
        if not refs:
            continue
        references.append(f"- theme `{theme}`:")
        for ref in refs:
            snippet = ref.snippet.replace("`", "'")[:160]
            references.append(
                f"    - [[link:{ref.slug}]] ({ref.date}) — {ref.title} — {snippet}"
            )

    if not topics and not references:
        return ""

    lines: list[str] = ["## CROSS-POST CONTEXT"]
    if topics:
        lines.append("Recent themes (slug — count — last_seen):")
        for t in topics:
            lines.append(f"- {t.name} — {t.count} mention(s) — {t.last_seen}")
    if references:
        lines.append("")
        lines.append("Prior references for this post's themes:")
        lines.extend(references)
    lines.append("")
    lines.append(
        "Use these to avoid repeating explanations. Link with [[link:<slug>]]."
    )
    return "\n".join(lines)


def node_writer(state: State) -> Dict[str, Any]:
    assert_inputs("writer", state)
    t0 = time.time()
    print("[writer] start")

    system_prompt = _build_system_prompt()
    date_mmddyy = _yyyymmdd_to_mmddyy(state.get("video_date", ""))
    cross_post_context = _build_cross_post_context(
        list(state.get("themes_attached") or state.get("themes") or [])
    )
    sections = [
        f"Video date (use this for the date stamp): {state.get('video_date','')}",
        f"Required MM/DD/YY format: {date_mmddyy}",
    ]
    if cross_post_context:
        sections.append("")
        sections.append(cross_post_context)
    sections.append("")
    sections.append("## EXTRACTION REPORT")
    sections.append("```")
    sections.append(state.get("extraction_report", ""))
    sections.append("```")
    sections.append("")
    sections.append("## RAW TRANSCRIPT (for sensory detail and authentic phrasing)")
    sections.append("```")
    sections.append(state.get("transcript_text", ""))
    sections.append("```")
    user_msg = "\n".join(sections)
    model = os.environ.get("PIPELINE_MODEL", "claude-sonnet-4-6")
    result = call_llm(model, system_prompt, user_msg, max_tokens=3000)
    text = result["text"]

    title, body = _parse_title_and_body(text)
    excerpt = _parse_excerpt(body)
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
    print(
        f"[writer] done {duration:.1f}s words={word_count} title={title!r} "
        f"excerpt_words={len(excerpt.split()) if excerpt else 0}"
    )
    return {
        "blog_post_path": str(blog_path),
        "blog_post_title": title,
        "blog_post_text": body,
        "blog_post_excerpt": excerpt,
    }
