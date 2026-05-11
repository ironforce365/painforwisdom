"""Phase 1 PoC — one-shot brief generator for a single hand-picked cluster.

Reads pre-fetched cleaned text from `briefs/.cache/`, runs three per-row
summaries + one cross-source synthesis through Sonnet 4.6, and writes
`briefs/<date>-<theme>.md` ready to drop into NotebookLM.

This is throwaway scaffolding for the manual PoC. Phase 4 replaces it with a
proper subsystem.
"""
from __future__ import annotations

import sys
from datetime import date
from typing import Any, Dict, List

from pipeline.llm import call_llm
from pipeline.runtime import PROJECT_ROOT


THEME = "deliberate-discomfort"
BRIEF_FRAMING = (
    "Three independent voices on voluntary discomfort: a 21st-century "
    "neuroscience review of hormetic dose responses, a Stoic letter from "
    "Seneca on planned poverty, and David Goggins' lived account on the "
    "Huberman Lab podcast. The brief asks: when discomfort is *chosen*, what "
    "actually changes — in the body, in the mind, in the sense of self?"
)

# Each entry mirrors a row in the Notion DB. In Phase 4 these come from
# `query_research_tasks(filter=...)`. Hand-picked here for the PoC.
SOURCES: List[Dict[str, Any]] = [
    {
        "slug": "hormesis-paper",
        "title": "Hormesis: A Fundamental Concept in Biology",
        "author_host": "Edward J. Calabrese, Mark P. Mattson",
        "type": "Paper",
        "specific_location": "Microbial Cell, vol. 4(5), 2017",
        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC5354598/",
        "research_angle": "Mechanism of low-dose stress producing protective adaptation across biological systems.",
        "relevance": (
            "The biological substrate for why voluntary discomfort works at all. "
            "Hormesis is not a metaphor — it's the dose-response curve underneath "
            "every deliberate-discomfort practice."
        ),
    },
    {
        "slug": "seneca-letter18",
        "title": "Letter 18 — On Festivals and Fasting",
        "author_host": "Seneca",
        "type": "Article",
        "specific_location": "Letters from a Stoic, Letter 18 (c. 65 CE)",
        "url": "https://therealizedman.com/seneca-letter-18-the-merits-of-voluntary-discomfort/",
        "research_angle": "Voluntary poverty as preparedness training: rehearse the feared condition so the fear loses its grip.",
        "relevance": (
            "The earliest written articulation of pre-loading discomfort — the "
            "philosophical taproot of every modern \"do hard things\" practice."
        ),
    },
    {
        "slug": "goggins-huberman",
        "title": "David Goggins: How to Build Immense Inner Strength",
        "author_host": "Andrew Huberman / David Goggins",
        "type": "Podcast",
        "specific_location": "Huberman Lab, full transcript",
        "url": "https://www.hubermanlab.com/episode/david-goggins-how-to-build-immense-inner-strength",
        "research_angle": "First-person practice: how a single person operationalizes voluntary discomfort as a daily protocol, including the inner-dialogue mechanics.",
        "relevance": (
            "What the lived application looks like when the dose-response curve "
            "from the Calabrese/Mattson paper meets the Stoic disposition from "
            "Seneca — at the level of one human's daily decisions."
        ),
    },
]


PER_ROW_SYSTEM = """You are a research analyst summarising a single source for a daily learning brief on the theme of "deliberate discomfort."

Output requirements:
- 1 paragraph (~180-220 words) summarising the source's load-bearing claims, focused on the supplied research angle.
- 2 to 3 short verbatim quotes from the source (each on its own line, prefixed with "> "). The quotes must be present in the text I give you — do not paraphrase or invent.
- 1 sentence labelled "**Takeaway:**" giving the operator-grade conclusion someone should walk away with.

Be specific. Cite the mechanism, not the vibe. No preamble, no "I will now summarise". Output only the three blocks above, separated by a blank line each.
"""

SYNTHESIS_SYSTEM = """You are a research analyst writing the synthesis section of a daily learning brief on "deliberate discomfort."

You have just summarised three sources. Now write 250-400 words on:
1. What claims do the three sources agree on?
2. Where do they contradict (or appear to)?
3. Is there a single mechanism running underneath all three, or are they describing different layers (biological, philosophical, behavioural) of one phenomenon?
4. End with one open question the brief leaves on the table.

No preamble. No "I will now". Just the synthesis prose, followed by a final line: "**Open question:** ..."
"""


def _read_cache(slug: str) -> str:
    return (PROJECT_ROOT / "briefs" / ".cache" / f"{slug}.txt").read_text()


def _per_row_summary(source: Dict[str, Any]) -> str:
    text = _read_cache(source["slug"])
    user = (
        f"Research angle: {source['research_angle']}\n\n"
        f"Source title: {source['title']}\n"
        f"Source author/host: {source['author_host']}\n\n"
        f"--- BEGIN SOURCE TEXT ---\n{text}\n--- END SOURCE TEXT ---"
    )
    resp = call_llm(
        model="claude-sonnet-4-6",
        system_prompt=PER_ROW_SYSTEM,
        user_message=user,
        max_tokens=1200,
    )
    return resp["text"]


def _synthesis(per_row_summaries: List[Dict[str, str]]) -> str:
    blocks = []
    for s in per_row_summaries:
        blocks.append(f"### {s['title']} — {s['author_host']}\n{s['summary']}")
    user = "\n\n---\n\n".join(blocks)
    resp = call_llm(
        model="claude-sonnet-4-6",
        system_prompt=SYNTHESIS_SYSTEM,
        user_message=user,
        max_tokens=1500,
    )
    return resp["text"]


def _vault_links() -> List[str]:
    # Phase 4 will pull this from `Vault Entry` per row. For PoC, leave as a
    # placeholder so Gonzalo can wire by hand if NotebookLM benefits from it.
    return []


def main() -> int:
    out_dir = PROJECT_ROOT / "briefs"
    out_dir.mkdir(exist_ok=True)
    today = date.today().isoformat()
    brief_path = out_dir / f"{today}-{THEME}.md"

    print(f"[poc-brief] theme={THEME} sources={len(SOURCES)}", flush=True)

    summaries: List[Dict[str, str]] = []
    for src in SOURCES:
        print(f"[poc-brief] summarising {src['slug']}...", flush=True)
        summary = _per_row_summary(src)
        summaries.append(
            {
                "title": src["title"],
                "author_host": src["author_host"],
                "type": src["type"],
                "specific_location": src["specific_location"],
                "url": src["url"],
                "research_angle": src["research_angle"],
                "relevance": src["relevance"],
                "summary": summary,
            }
        )

    print("[poc-brief] running cross-source synthesis...", flush=True)
    synthesis = _synthesis(summaries)

    lines: List[str] = []
    lines.append(f"# {THEME} — {today}")
    lines.append("")
    lines.append(f"> {BRIEF_FRAMING}")
    lines.append("")
    lines.append("## Sources")
    lines.append("")
    for s in summaries:
        lines.append(f"### {s['title']} — {s['author_host']}")
        lines.append(f"- **Type:** {s['type']}")
        lines.append(f"- **Original location:** {s['specific_location']}")
        lines.append(f"- **Why this matters:** {s['relevance']}")
        lines.append(f"- **Research angle:** {s['research_angle']}")
        lines.append(f"- **Source URL:** {s['url']}")
        lines.append("")
        lines.append(s["summary"])
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append("## Cross-source synthesis")
    lines.append("")
    lines.append(synthesis)
    lines.append("")

    vault_links = _vault_links()
    if vault_links:
        lines.append("## Vault entries this brief connects to")
        lines.append("")
        for link in vault_links:
            lines.append(f"- {link}")
        lines.append("")

    brief_path.write_text("\n".join(lines))
    print(f"[poc-brief] wrote {brief_path}")
    print(f"[poc-brief] {brief_path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
