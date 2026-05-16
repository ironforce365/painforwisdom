"""Phase 1 PoC v2 — three-file brief output for a hand-defined sub-cluster.

Per cluster, produces (theme-first nested layout, locked 2026-05-10):
  briefs/<theme>/<date>--<sub-slug>/deep-dive.md
  briefs/<theme>/<date>--<sub-slug>/application.md
  briefs/<theme>/<date>--<sub-slug>/audio-prompts.md

Cluster definitions are hardcoded in CLUSTERS at the bottom — the daily
summarizer in Phase 4 will populate this list dynamically from Notion.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import date
from typing import Any, Dict, List

from pipeline.llm import call_llm
from pipeline.runtime import PROJECT_ROOT


# All content-generation LLM calls go to Opus 4.7. Sonnet's synthesis wasn't
# producing dense-enough briefs; Opus is the quality bar for content gen.
# (Subscription billing — no $ cost, only quota.)
BRIEF_MODEL = "claude-opus-4-7"

# Local pre-summarization via `claude -p`: bypass the Anthropic API 200k
# per-request cap by routing long sources through the Claude Code CLI
# (which uses the Ultra subscription's 1M context window). The focalized
# summary is then fed to litellm.completion in place of the raw source.
LOCAL_SUMMARY_THRESHOLD_CHARS = 500_000  # ~125k tokens — anything larger gets focal-summarized
LOCAL_SUMMARY_CHUNK_CHARS = 500_000      # chunk size when source needs splitting
LOCAL_SUMMARY_MAX_CHUNKS = 5             # caps coverage at ~2.5MB of source text
LOCAL_CLAUDE_TIMEOUT_S = 900             # 15 min per claude -p invocation
LOCAL_CLAUDE_MODEL = "claude-sonnet-4-6"  # focal pre-summary doesn't need Opus quality

# Absolute path so this works under systemd-user (which inherits a minimal
# PATH that does NOT include `~/.local/bin`). Override with $CLAUDE_BIN.
LOCAL_CLAUDE_BIN = os.environ.get("CLAUDE_BIN") or os.path.expanduser("~/.local/bin/claude")


# ---------- prompts ----------

DEEP_DIVE_PER_ROW_SYSTEM = """You are a research analyst summarising a single source for a daily learning brief.

Output exactly:
- 1 paragraph (~180-220 words) summarising the source's load-bearing claims, focused on the supplied research angle.
- 2 to 3 short verbatim quotes from the source (each on its own line, prefixed with "> "). The quotes must be present in the supplied text — do not paraphrase or invent.
- 1 sentence labelled "**Takeaway:**" giving the operator-grade conclusion.

Be specific. Cite mechanisms, not vibes. No preamble, no narration. Just the three blocks above, separated by blank lines.
"""

DEEP_DIVE_SYNTHESIS_SYSTEM = """You are a research analyst writing the cross-source synthesis section of a daily learning brief.

You have just summarised {n} sources on the same research angle. Now write 250-400 words on:
1. What claims do the sources agree on?
2. Where do they contradict (or appear to)?
3. Is there a single mechanism running underneath, or are they describing different layers of one phenomenon?
4. End with one open question the brief leaves on the table.

No preamble. No narration. Just the synthesis prose, then a final line: "**Open question:** ..."
"""

APPLICATION_SYSTEM = """You are a coaching writer connecting research material to one operator's lived practice.

You have:
- A vault entry written by Gonzalo after one of his runs/sessions, capturing what he was teaching/processing in his own words.
- The 'deep-dive' summary of {n} academic/expert sources on the same theme.

Your job: write the **application brief** — what the deep-dive material means for Gonzalo's lived practice as captured in the vault entry. Output 350-500 words structured as:

## What the deep-dive validates
A paragraph naming what the sources confirm about Gonzalo's lived insight. Be specific — cite which mechanism from the sources matches which observation in the vault entry.

## What the deep-dive refines or contradicts
A paragraph naming where the sources go beyond, sharpen, or challenge the vault entry's framing. If a contradiction exists, name it directly. If the deep-dive offers a more precise mechanism, name it.

## Three concrete adjustments to the practice
Three numbered points. Each is a specific operational change Gonzalo could make to his protocol, derived from a source claim. Format: "1. [adjustment] — [source mechanism that justifies it]".

## One question the brief leaves on Gonzalo's desk
A single sharp question that arises only from holding the vault entry and the deep-dive together — not answerable from either alone.

No preamble. No "I will now". No filler. Just the four sections above with their headers exactly as written.
"""

AUDIO_PROMPTS_SYSTEM = """You are an audio-content director generating prompt options for NotebookLM's Audio Overview feature.

The user has 3 markdown files (deep-dive + application + this prompt list) ready to upload. NotebookLM accepts a single text prompt that shapes the generated audio. You will produce 5 ready-to-paste prompt variants, each ~80-180 words, each tuned for a different framing:

1. **Operator-grade walkthrough** — direct, mechanism-first, no fluff. For when Gonzalo wants to learn faster.
2. **Beginner-friendly explainer** — assumes audience has never heard of these concepts. For sharing with people new to the material.
3. **Debate / steel-man** — two hosts who disagree. One holds the deep-dive's strongest claim; the other steelmans the contradiction or open question.
4. **Story arc** — narrative through Gonzalo's lived moment, then the science, then back to the practice. For emotional retention.
5. **Contrarian counter-take** — the audio purposefully argues against the deep-dive's central claim using whatever tension exists in the synthesis. For sharpening Gonzalo's own thinking.

Output structure (use these exact headers):

### Prompt 1 — Operator-grade walkthrough
[the prompt text — written as instructions to NotebookLM, addressing it directly]

### Prompt 2 — Beginner-friendly explainer
...

(and so on through Prompt 5)

No preamble. No closing. Just the five prompts.
"""


# ---------- helpers ----------

def _slug(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")[:50]


def _read_cache(slug: str) -> str:
    return (PROJECT_ROOT / "briefs" / ".cache" / f"{slug}.txt").read_text()


def _focal_prompt(source: Dict[str, Any]) -> str:
    """Build a focusing prompt from the research-agent metadata so the local
    claude pass only summarizes the parts of the source that actually bear
    on the angle Gonzalo cares about.

    Three fields steer the focus, in priority order:
      1. specific_location: chapter / episode / page range to zoom into.
      2. research_angle: the question the cluster is answering.
      3. relevance: why the agent picked this source over others.
    """
    angle = source.get("research_angle") or "(no angle)"
    relevance = (source.get("relevance") or "").strip()
    location = (source.get("specific_location") or "").strip()
    title = source.get("title") or "(untitled)"
    author = source.get("author_host") or "(unknown)"
    parts = [
        "You are creating a focalized research summary of ONE source. Another LLM "
        "will use your summary (not the raw source) for cross-source synthesis, so "
        "density > polish, citations > prose.",
        f"Source: {title} — {author}",
        f"Research angle: {angle}",
    ]
    if relevance:
        parts.append(f"Why this source was chosen: {relevance}")
    if location:
        parts.append(
            f"FOCUS WINDOW: zero in on {location}. Skim past unrelated chapters, "
            "side stories, and tangential material — they are not load-bearing."
        )
    parts.append(
        "Output a dense ~1500-2500 word summary covering ONLY the parts of the source "
        "that bear on the research angle. Verbatim-quote impactful passages (each on "
        "its own line, prefixed with `> `). End with a one-line **Takeaway:** capturing "
        "the operator-grade conclusion. No motivational framing, no hedging."
    )
    return "\n\n".join(parts)


_FOCAL_SYSTEM_PROMPT = (
    "You are a research summarizer producing a focalized digest of a source "
    "for downstream synthesis. Output dense markdown only. Do not call tools. "
    "Do not narrate your process. Do not ask follow-up questions."
)
_FOCAL_DISALLOWED_TOOLS = "Bash Edit Write Read Glob Grep WebFetch WebSearch NotebookEdit"


def _claude_p_focal(prompt: str, text: str) -> str:
    """One `claude -p` invocation against the OAuth subscription.

    `--bare` is INTENTIONALLY NOT used here: bare-mode forces API-key auth
    and would 401 on the OAuth-subscription token. Instead we lean it out via:
      * `--system-prompt` — replaces Claude Code's full default prompt with a
        minimal summarizer directive.
      * `--disallowedTools` — blocks file/web/shell tool use so the model
        can only return text.
      * `--model claude-sonnet-4-6` — focal pre-summary doesn't need Opus.

    Subscription billing — no $ cost, only quota.
    """
    # Strip ANTHROPIC_* env vars so `claude` uses its own OAuth keychain
    # (~/.claude/.credentials.json) instead of a stale token that load_dotenv
    # already injected into this Python process. The keychain is refreshed by
    # the `claude` CLI itself; a stale .env token would 401.
    child_env = {k: v for k, v in os.environ.items()
                 if k not in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")}
    proc = subprocess.run(
        [
            LOCAL_CLAUDE_BIN, "-p", prompt,
            "--system-prompt", _FOCAL_SYSTEM_PROMPT,
            "--disallowedTools", _FOCAL_DISALLOWED_TOOLS,
            "--model", LOCAL_CLAUDE_MODEL,
        ],
        input=text,
        capture_output=True,
        text=True,
        timeout=LOCAL_CLAUDE_TIMEOUT_S,
        env=child_env,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p failed (rc={proc.returncode}): {proc.stderr[:500]}")
    out = (proc.stdout or "").strip()
    if not out:
        raise RuntimeError(f"claude -p produced empty output; stderr={proc.stderr[:500]}")
    return out


def _focal_summary_local(source: Dict[str, Any], raw_text: str) -> str:
    """Pre-summarize a too-large source via local `claude -p` so the downstream
    litellm call stays under the Anthropic API 200k per-request cap.

    Chunks the source if it exceeds `LOCAL_SUMMARY_CHUNK_CHARS`, then merges
    per-chunk summaries via a final `claude -p` pass. The same focal prompt
    is reused for both phases so the merge inherits the research-angle bias.
    """
    prompt = _focal_prompt(source)
    n = len(raw_text)
    if n <= LOCAL_SUMMARY_CHUNK_CHARS:
        print(f"[poc-v2]   focal pre-summary (single chunk, {n} chars)...", flush=True)
        return _claude_p_focal(prompt, raw_text)

    chunks = [
        raw_text[i:i + LOCAL_SUMMARY_CHUNK_CHARS]
        for i in range(0, n, LOCAL_SUMMARY_CHUNK_CHARS)
    ]
    if len(chunks) > LOCAL_SUMMARY_MAX_CHUNKS:
        print(
            f"[poc-v2]   source is {n} chars — capping at {LOCAL_SUMMARY_MAX_CHUNKS} chunks "
            f"(~{LOCAL_SUMMARY_MAX_CHUNKS * LOCAL_SUMMARY_CHUNK_CHARS} chars / "
            f"{LOCAL_SUMMARY_MAX_CHUNKS * LOCAL_SUMMARY_CHUNK_CHARS // 4000}k tokens). "
            "Reduce source coverage by re-augmenting the cache to chapter-only.",
            flush=True,
        )
        chunks = chunks[:LOCAL_SUMMARY_MAX_CHUNKS]

    print(f"[poc-v2]   focal pre-summary ({len(chunks)} chunks of {LOCAL_SUMMARY_CHUNK_CHARS} chars)...", flush=True)
    chunk_summaries: List[str] = []
    for i, chunk in enumerate(chunks, 1):
        print(f"[poc-v2]     chunk {i}/{len(chunks)}...", flush=True)
        s = _claude_p_focal(prompt, chunk)
        chunk_summaries.append(f"--- Chunk {i}/{len(chunks)} ---\n{s}")

    print(f"[poc-v2]   merging {len(chunk_summaries)} chunk summaries...", flush=True)
    merge_prompt = (
        prompt
        + "\n\nYou will receive multiple chunk-level summaries of one large source. "
        "Merge them into a single coherent focalized summary at the same density. "
        "Eliminate redundancy across chunks. Preserve every concrete claim, quote, "
        "mechanism, and study reference. Re-order if needed for clarity."
    )
    return _claude_p_focal(merge_prompt, "\n\n".join(chunk_summaries))


def _read_vault(rel_path: str) -> str:
    """Vault entry path is stored as a slug like '2026-03-27-pre-run-fatigue-is-forecast'.
    Resolves under obsidian-vault/gonzalo-book/entries/."""
    base = PROJECT_ROOT / "obsidian-vault" / "gonzalo-book" / "entries"
    for cand in (rel_path, f"{rel_path}.md", rel_path.removesuffix(".md") + ".md"):
        p = base / cand
        if p.exists():
            return p.read_text()
    raise FileNotFoundError(f"Vault entry not found: {rel_path} (tried {base})")


def _per_row_summary(source: Dict[str, Any]) -> str:
    raw = _read_cache(source["slug"])
    if len(raw) > LOCAL_SUMMARY_THRESHOLD_CHARS:
        print(
            f"[poc-v2]   source '{source['slug']}' is {len(raw)} chars (>{LOCAL_SUMMARY_THRESHOLD_CHARS}) — "
            "routing through local `claude -p` for a focalized pre-summary",
            flush=True,
        )
        try:
            text = _focal_summary_local(source, raw)
            print(f"[poc-v2]   focal summary: {len(text)} chars", flush=True)
        except (subprocess.TimeoutExpired, RuntimeError, FileNotFoundError) as exc:
            # Fall back to a hard truncation so the pipeline still moves —
            # losing detail is better than crashing the brief.
            print(
                f"[poc-v2]   focal pre-summary failed ({type(exc).__name__}: {exc}); "
                f"falling back to first {LOCAL_SUMMARY_THRESHOLD_CHARS} chars",
                flush=True,
            )
            text = raw[:LOCAL_SUMMARY_THRESHOLD_CHARS]
    else:
        text = raw
    user = (
        f"Research angle: {source['research_angle']}\n\n"
        f"Source title: {source['title']}\n"
        f"Source author/host: {source['author_host']}\n\n"
        f"--- BEGIN SOURCE TEXT ---\n{text}\n--- END SOURCE TEXT ---"
    )
    return call_llm(
        model=BRIEF_MODEL,
        system_prompt=DEEP_DIVE_PER_ROW_SYSTEM,
        user_message=user,
        max_tokens=1200,
    )["text"]


def _synthesis(per_row_summaries: List[Dict[str, str]]) -> str:
    blocks = []
    for s in per_row_summaries:
        blocks.append(f"### {s['title']} — {s['author_host']}\n{s['summary']}")
    user = "\n\n---\n\n".join(blocks)
    return call_llm(
        model=BRIEF_MODEL,
        system_prompt=DEEP_DIVE_SYNTHESIS_SYSTEM.format(n=len(per_row_summaries)),
        user_message=user,
        max_tokens=1500,
    )["text"]


def _application(deep_dive_md: str, vault_text: str, n_sources: int) -> str:
    user = (
        f"--- BEGIN VAULT ENTRY (Gonzalo's lived insight) ---\n{vault_text}\n--- END VAULT ENTRY ---\n\n"
        f"--- BEGIN DEEP-DIVE BRIEF ({n_sources} sources) ---\n{deep_dive_md}\n--- END DEEP-DIVE BRIEF ---"
    )
    return call_llm(
        model=BRIEF_MODEL,
        system_prompt=APPLICATION_SYSTEM.format(n=n_sources),
        user_message=user,
        max_tokens=2000,
    )["text"]


def _audio_prompts(deep_dive_md: str, application_md: str, theme: str, sub_angle: str) -> str:
    user = (
        f"Theme: {theme}\nSub-angle: {sub_angle}\n\n"
        f"--- DEEP-DIVE BRIEF ---\n{deep_dive_md}\n\n"
        f"--- APPLICATION BRIEF ---\n{application_md}"
    )
    return call_llm(
        model=BRIEF_MODEL,
        system_prompt=AUDIO_PROMPTS_SYSTEM,
        user_message=user,
        max_tokens=2000,
    )["text"]


# ---------- main ----------

def render_deep_dive(cluster: Dict[str, Any], summaries: List[Dict[str, str]], synthesis: str) -> str:
    today = date.today().isoformat()
    lines: List[str] = []
    lines.append(f"# {cluster['theme']} — {cluster['sub_angle']}")
    lines.append(f"_Sub-cluster brief — {today}_")
    lines.append("")
    lines.append(f"> {cluster['framing']}")
    lines.append("")
    lines.append("## Sources")
    lines.append("")
    for s in summaries:
        lines.append(f"### {s['title']} — {s['author_host']}")
        lines.append(f"- **Type:** {s['type']}")
        lines.append(f"- **Original location:** {s['specific_location']}")
        lines.append(f"- **Why this matters:** {s['relevance']}")
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
    return "\n".join(lines)


def render_application(cluster: Dict[str, Any], application_md: str) -> str:
    today = date.today().isoformat()
    lines: List[str] = []
    lines.append(f"# {cluster['theme']} — {cluster['sub_angle']} — Application")
    lines.append(f"_Sub-cluster application brief — {today}_")
    lines.append("")
    lines.append(f"Vault entry: `{cluster['vault_entry']}`")
    lines.append("")
    lines.append(application_md)
    lines.append("")
    return "\n".join(lines)


def render_audio_prompts(cluster: Dict[str, Any], prompts_md: str) -> str:
    today = date.today().isoformat()
    lines: List[str] = []
    lines.append(f"# {cluster['theme']} — {cluster['sub_angle']} — NotebookLM Prompts")
    lines.append(f"_Sub-cluster audio-prompt suggestions — {today}_")
    lines.append("")
    lines.append("Pick one of the prompts below, paste it into NotebookLM's customise-audio dialog, and upload the deep-dive + application markdown files as sources.")
    lines.append("")
    lines.append(prompts_md)
    lines.append("")
    return "\n".join(lines)


def run_cluster(cluster: Dict[str, Any]) -> None:
    today = date.today().isoformat()
    sub_slug = _slug(cluster["sub_angle"])
    cluster_dir = PROJECT_ROOT / "briefs" / cluster["theme"] / f"{today}--{sub_slug}"
    cluster_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[poc-v2] === {cluster['theme']} / {cluster['sub_angle']} ===", flush=True)
    print(f"[poc-v2] dir: {cluster_dir.relative_to(PROJECT_ROOT)}", flush=True)
    print(f"[poc-v2] sources: {len(cluster['sources'])}", flush=True)

    # 1. Per-row summaries
    summaries: List[Dict[str, str]] = []
    for src in cluster["sources"]:
        print(f"[poc-v2]   summarise {src['slug']}...", flush=True)
        summary = _per_row_summary(src)
        summaries.append({**src, "summary": summary})

    # 2. Synthesis
    print("[poc-v2]   synthesis...", flush=True)
    synthesis = _synthesis(summaries)

    deep_dive_md = render_deep_dive(cluster, summaries, synthesis)
    deep_dive_path = cluster_dir / "deep-dive.md"
    deep_dive_path.write_text(deep_dive_md)
    print(f"[poc-v2]   wrote {deep_dive_path.name}", flush=True)

    # 3. Application
    print("[poc-v2]   application...", flush=True)
    vault = _read_vault(cluster["vault_entry"])
    application_inner = _application(deep_dive_md, vault, len(cluster["sources"]))
    application_md = render_application(cluster, application_inner)
    application_path = cluster_dir / "application.md"
    application_path.write_text(application_md)
    print(f"[poc-v2]   wrote {application_path.name}", flush=True)

    # 4. Audio prompts
    print("[poc-v2]   audio prompts...", flush=True)
    prompts_inner = _audio_prompts(deep_dive_md, application_md, cluster["theme"], cluster["sub_angle"])
    prompts_md = render_audio_prompts(cluster, prompts_inner)
    prompts_path = cluster_dir / "audio-prompts.md"
    prompts_path.write_text(prompts_md)
    print(f"[poc-v2]   wrote {prompts_path.name}", flush=True)


# ---------- cluster definitions (PoC, hand-curated) ----------

CLUSTERS: List[Dict[str, Any]] = [
    {
        "theme": "body-literacy",
        "sub_angle": "Central Governor Theory and Anticipatory Fatigue",
        "framing": (
            "Two sources on Tim Noakes' Central Governor Model — the brain regulates "
            "exercise intensity *before* peripheral failure, anticipating depletion "
            "rather than reporting it. The relevance to Gonzalo's pre-run fatigue: "
            "what feels like 'tired body' is often 'tired forecast'."
        ),
        "vault_entry": "2026-03-27-pre-run-fatigue-is-forecast",
        "sources": [
            {
                "slug": "central-governor-tts43",
                "title": "Psychology and the Central Governor Model with Prof. Tim Noakes",
                "author_host": "That Triathlon Show — Mikael Eriksson with Tim Noakes",
                "type": "Podcast",
                "specific_location": "TTS Episode 43",
                "url": "https://scientifictriathlon.com/tts43/",
                "research_angle": "Central Governor Theory and Anticipatory Fatigue",
                "relevance": "Direct from the theory's originator — what the CGM actually claims and what it does not.",
            },
            {
                "slug": "central-governor-noakes-paper",
                "title": "Fatigue Is a Brain-Derived Emotion That Regulates Exercise Behavior",
                "author_host": "Timothy D. Noakes",
                "type": "Paper",
                "specific_location": "Frontiers in Physiology, 2012",
                "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC3323922/",
                "research_angle": "Central Governor Theory and Anticipatory Fatigue",
                "relevance": "The peer-reviewed mechanistic claim: fatigue is an emotion serving teleoanticipation, not a metabolic consequence.",
            },
        ],
    },
    {
        "theme": "amcc-effect",
        "sub_angle": "aMCC and voluntary override of comfort-seeking",
        "framing": (
            "The anterior mid-cingulate cortex sits at the intersection of effort, "
            "interoception, and motivation. Two papers — one a theoretical synthesis "
            "(Touroutoglou et al., 2020), one a causal lesion-and-stimulation study "
            "(Parvizi et al., 2013) — converge on a striking claim: the aMCC isn't just "
            "correlated with effort, it is *causally required* for voluntary persistence."
        ),
        "vault_entry": "2026-03-24-the-comfort-cage",
        "sources": [
            {
                "slug": "amcc-allostasis-paper",
                "title": "Motivation in the Service of Allostasis: The Role of Anterior Mid-Cingulate Cortex",
                "author_host": "Touroutoglou, Hollenbeck, Dickerson, Feldman Barrett",
                "type": "Paper",
                "specific_location": "Advances in Motivation Science, vol. 7 (2020), Ch. 1",
                "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC6884085/",
                "research_angle": "aMCC as the allostatic engine that initiates effortful behavior to defend physiological setpoints.",
                "relevance": "Frames the aMCC not as a 'willpower region' but as the brain area that converts predicted physiological cost into volitional action.",
            },
            {
                "slug": "amcc-tenacious-brain",
                "title": "The Tenacious Brain: How the Anterior Mid-Cingulate Cortex Contributes to Achieving Goals",
                "author_host": "Touroutoglou, Andreano, Dickerson, Feldman Barrett",
                "type": "Paper",
                "specific_location": "Cortex, vol. 123 (2020), pp. 12-29",
                "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC7381101/",
                "research_angle": "aMCC and voluntary override of comfort-seeking",
                "relevance": "The 'training the override muscle' framing — aMCC volume correlates with successful goal pursuit and grows with effortful practice.",
            },
        ],
    },
    {
        "theme": "deliberate-discomfort",
        "sub_angle": "aMCC and persistence after failure",
        "framing": (
            "Two papers on what neurally distinguishes the person who pushes again "
            "after a setback from the one who does not. One reviews the converging "
            "evidence (Touroutoglou et al., 2020); the other is a striking direct-stimulation "
            "study where electrical activation of the cingulate elicited a reportable "
            "'will to persevere' in awake patients (Parvizi et al., 2013)."
        ),
        "vault_entry": "2026-03-26-surviving-the-day-after.md",
        "sources": [
            {
                "slug": "amcc-tenacious-brain",
                "title": "The Tenacious Brain: How the Anterior Mid-Cingulate Cortex Contributes to Achieving Goals",
                "author_host": "Touroutoglou, Andreano, Dickerson, Feldman Barrett",
                "type": "Paper",
                "specific_location": "Cortex, vol. 123 (2020), pp. 12-29",
                "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC7381101/",
                "research_angle": "aMCC and persistence after failure",
                "relevance": "Reviews the body of evidence linking aMCC structure/function with the ability to keep pursuing a goal after aversive feedback.",
            },
            {
                "slug": "amcc-will-to-persevere",
                "title": "The Will to Persevere Induced by Electrical Stimulation of the Human Cingulate Gyrus",
                "author_host": "Parvizi, Rangarajan, Shirer, Desai, Greicius",
                "type": "Paper",
                "specific_location": "Neuron, vol. 80(6) (2013), pp. 1359-1367",
                "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC3877748/",
                "research_angle": "aMCC and persistence after failure",
                "relevance": "Causal evidence: stimulating the aMCC in awake patients produced a self-reported drive to keep going against an anticipated challenge.",
            },
        ],
    },
]


def main() -> int:
    for cluster in CLUSTERS:
        run_cluster(cluster)
    print("\n[poc-v2] all clusters done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
