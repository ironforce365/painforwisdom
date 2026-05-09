"""LangGraph 4-stage PoC: transcribe → extract → refine → notify.

The `refine` stage exists primarily to validate Anthropic prompt caching:
it issues a second LLM call with the same large extractor system prompt
(>1024 tokens, the cache-write threshold). If caching engages, the second
call's `cache_read_tokens` should equal the size of the cached prefix.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from poc.llm import call_llm
from poc.state import State

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNS_LOG = Path(__file__).resolve().parent / "runs.jsonl"
EXTRACTOR_PROMPT_FILE = PROJECT_ROOT / ".claude" / "agents" / "coaching-thought-extractor.md"
TELEGRAM_SCRIPT = PROJECT_ROOT / "telegram_io.sh"
EXTRACT_TRANSCRIPTION = PROJECT_ROOT / "extract_transcription.sh"


def append_metric(stage: str, **fields: Any) -> Dict[str, Any]:
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "stage": stage, **fields}
    RUNS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with RUNS_LOG.open("a") as f:
        f.write(json.dumps(row) + "\n")
    return row


def load_extractor_prompt() -> str:
    """Read the existing extractor agent body. Strip the YAML frontmatter and
    replace the OUTPUT section (which tells the agent to write a file via Bash)
    with a simple instruction to return the report text directly.

    A `## VOICE & TONE REFERENCE` appendix is appended to push the cached
    prefix above ~2,200 tokens. Empirical finding: Sonnet 4.6 prompt-cache
    minimum is ≈2,048 tokens for the cached block, not the 1,024 claimed in
    the public docs. Without padding, the existing extractor body (~1,635
    tokens) sits just below threshold and the API silently skips caching.
    The appendix is real instructional content in Gonzalo's voice — useful
    to the extractor, not filler — so it's an asset, not a workaround."""
    raw = EXTRACTOR_PROMPT_FILE.read_text()
    m = re.match(r"^---\n.*?\n---\n(.*)", raw, re.DOTALL)
    body = m.group(1) if m else raw
    body = re.split(r"\n## OUTPUT\n", body, maxsplit=1)[0]
    body += (
        "\n\n## OUTPUT\n\n"
        "Reply with ONLY the structured report below — no Bash, no file writing, "
        "no preamble, no closing remarks. Use this exact structure:\n\n"
        "---\n"
        "### COACHING THOUGHT EXTRACTION REPORT\n\n"
        "**Content Quality:** [Strong / Weak / Flagged]\n\n"
        "**If weak or flagged, reason:**\n"
        "(explain specifically why)\n\n"
        "---\n\n"
        "**Core Insight:**\n(one sentence — the distilled lesson)\n\n"
        "**Story Anchor:**\n(specific moment from the transcript that grounds this)\n\n"
        "**Practical Application:**\n(what someone would actually do)\n\n"
        "**Who It's For:**\n(specific person — concrete, not generic)\n\n"
        "**Blog Post Seed:**\n(2–3 sentences ready for a writer)\n\n"
        "---\n"
        "\n\n"
        "## VOICE & TONE REFERENCE (use to calibrate extraction)\n\n"
        "Gonzalo's writing voice is grounded, first-person, low-drama. He talks "
        "about his own behavior in the present continuous tense and admits "
        "tradeoffs without resolving them. Useful tells of authentic Gonzalo voice:\n\n"
        "- He names the cheap version of an action before naming the costly one. "
        "Example: 'the easy thing is to call this discipline; the harder thing is "
        "to admit it's avoidance dressed up.' He earns the second clause through "
        "the first.\n"
        "- He describes the body before the lesson. Heart rate, breath, sweat, "
        "shoulder tension — all surface before he says what it meant.\n"
        "- He undercuts hero framing. If a sentence reads heroic, he adds the "
        "next sentence that grounds it: 'and then I sat down on the curb and ate "
        "a stroopwafel'. The undercut is the signature.\n"
        "- He uses the second person sparingly and almost always to point at "
        "himself, not the reader. 'You tell yourself…' usually means 'I tell "
        "myself…'\n"
        "- He prefers concrete specifics over generic principles: 'I had 47 "
        "kilometers left and the rain hadn't started yet' rather than 'I was "
        "tired and the conditions were difficult.'\n"
        "- He references his frameworks (cookies, friction types, strategic vs "
        "manufactured suffering, aMCC effect) only when the lived moment "
        "earns it. He does not name-drop.\n"
        "- He uses Stoic framing in the dichotomy of control sense, not the "
        "stoic-as-stoicism cosplay sense. He notices when something is outside "
        "his control and stops trying to muscle it.\n\n"
        "## FAILURE MODES TO AVOID\n\n"
        "- Goggins-style 'we all must' framing. If extracted content reads like "
        "a David Goggins quote, that's a flag.\n"
        "- Quoting the transcript verbatim as the Story Anchor without the "
        "concrete sensory detail. The anchor must be specific enough that the "
        "reader can see what Gonzalo saw.\n"
        "- Inventing details to fill out the Practical Application. If the "
        "transcript doesn't say what someone would do, leave the section "
        "narrower rather than fabricate.\n"
        "- Borrowing from common coaching territory (atomic habits, deep work, "
        "growth mindset) without grounding in Gonzalo's lived take.\n"
        "- Generic 'Who It's For' descriptions. 'The high-performer who…' is "
        "weak. 'The 38-year-old engineer-parent who hits the gym at 5 a.m. "
        "because that's the only window his kids are asleep' is concrete.\n\n"
        "## CONTENT QUALITY CALIBRATION\n\n"
        "Strong: the transcript contains a specific lived event, an emotional "
        "or physical detail anchoring it, and an unresolved tension Gonzalo is "
        "still working out. Strong content has texture — surprise, contradiction, "
        "self-correction.\n\n"
        "Weak: the transcript is mostly a principle Gonzalo has read about "
        "elsewhere and is paraphrasing without testing it on himself. Or the "
        "transcript names a feeling but doesn't ground it in an event. Or the "
        "transcript escalates into 'we all should' territory.\n\n"
        "Flagged: the transcript advocates voluntary suffering without a "
        "stated benefit, frames a behavior as universal that's actually "
        "personal, or sounds like motivational hype detached from a real moment.\n"
    )
    return body


def _date_from_filename(path: str) -> str:
    m = re.search(r"PXL_(\d{4})(\d{2})(\d{2})_", os.path.basename(path))
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return time.strftime("%Y-%m-%d")


def node_transcribe(state: State) -> Dict[str, Any]:
    t0 = time.time()
    video = state["video_path"]
    date = _date_from_filename(video)
    print(f"[transcribe] start video={os.path.basename(video)} date={date}")
    proc = subprocess.run(
        [str(EXTRACT_TRANSCRIPTION), video, "English", date],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, flush=True)
        raise RuntimeError(f"extract_transcription.sh exit {proc.returncode}")

    out_dir = Path(video).parent / "auto-generated"
    candidates = sorted(
        out_dir.glob(f"transcript_{date}*.txt"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise RuntimeError(f"transcript not found in {out_dir} for date={date}")
    transcript_path = candidates[0]
    transcript_text = transcript_path.read_text()
    duration = time.time() - t0
    word_count = len(transcript_text.split())
    metric = append_metric(
        "transcribe",
        duration_s=round(duration, 2),
        transcript_path=str(transcript_path),
        transcript_words=word_count,
        transcript_chars=len(transcript_text),
    )
    print(f"[transcribe] done {duration:.1f}s words={word_count} path={transcript_path.name}")
    return {
        "transcript_path": str(transcript_path),
        "transcript_text": transcript_text,
        "metrics": state.get("metrics", []) + [metric],
    }


def node_extract(state: State) -> Dict[str, Any]:
    t0 = time.time()
    print("[extract] start")
    system_prompt = load_extractor_prompt()
    date = _date_from_filename(state.get("video_path", ""))
    user_msg = (
        f"Video date: {date}\n"
        f"Transcript file: {os.path.basename(state.get('transcript_path',''))}\n\n"
        f"Transcript:\n{state['transcript_text']}\n"
    )
    model = os.environ.get("POC_MODEL", "claude-sonnet-4-6")
    result = call_llm(model, system_prompt, user_msg)
    duration = time.time() - t0
    text = result["text"]
    m = re.search(r"\*\*Core Insight:\*\*\s*\n(.+?)(?:\n\n|\Z)", text, re.DOTALL)
    core_insight = m.group(1).strip() if m else "(could not parse Core Insight)"
    metric = append_metric(
        "extract",
        duration_s=round(duration, 2),
        model=result["model"],
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        cache_read_tokens=result["cache_read_tokens"],
        cache_creation_tokens=result["cache_creation_tokens"],
        cost_usd=round(result["cost_usd"], 6),
        billing_mode=result["billing_mode"],
        stop_reason=result["stop_reason"],
    )
    print(
        f"[extract] done {duration:.1f}s in={result['input_tokens']} "
        f"out={result['output_tokens']} cache_r={result['cache_read_tokens']} "
        f"cost=${result['cost_usd']:.4f} mode={result['billing_mode']}"
    )
    return {
        "extraction_report": text,
        "core_insight": core_insight,
        "metrics": state.get("metrics", []) + [metric],
    }


def node_refine(state: State) -> Dict[str, Any]:
    """Second LLM call with the same extractor system prompt. Validates that
    Anthropic prompt caching engages on the cached prefix (cache_read_tokens > 0)."""
    t0 = time.time()
    print("[refine] start")
    system_prompt = load_extractor_prompt()
    user_msg = (
        "Below is your prior extraction report. Distill the Core Insight into a "
        "single tweet (≤280 characters) in Gonzalo's voice — first-person, grounded, "
        "no hashtags, no emoji, no preamble. Reply with ONLY the tweet text.\n\n"
        f"--- prior extraction ---\n{state.get('extraction_report','')}"
    )
    model = os.environ.get("POC_MODEL", "claude-sonnet-4-6")
    result = call_llm(model, system_prompt, user_msg, max_tokens=400)
    duration = time.time() - t0
    tweet = result["text"].strip().strip('"').strip()
    metric = append_metric(
        "refine",
        duration_s=round(duration, 2),
        model=result["model"],
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        cache_read_tokens=result["cache_read_tokens"],
        cache_creation_tokens=result["cache_creation_tokens"],
        cost_usd=round(result["cost_usd"], 6),
        billing_mode=result["billing_mode"],
        stop_reason=result["stop_reason"],
    )
    cache_status = (
        "HIT" if result["cache_read_tokens"] > 0
        else ("WRITE" if result["cache_creation_tokens"] > 0 else "MISS")
    )
    print(
        f"[refine] done {duration:.1f}s in={result['input_tokens']} "
        f"out={result['output_tokens']} cache={cache_status} "
        f"(read={result['cache_read_tokens']} write={result['cache_creation_tokens']}) "
        f"cost=${result['cost_usd']:.4f}"
    )
    return {
        "tweet": tweet,
        "metrics": state.get("metrics", []) + [metric],
    }


def node_notify(state: State) -> Dict[str, Any]:
    t0 = time.time()
    insight = state.get("core_insight", "(no insight)")
    tweet = state.get("tweet", "")
    transcript_path = state.get("transcript_path", "")
    msg_lines = [
        "🧪 PoC complete",
        f"File: {os.path.basename(transcript_path)}",
        f"Core insight: {insight[:400]}",
    ]
    if tweet:
        msg_lines.append(f"Tweet: {tweet[:400]}")
    msg = "\n".join(msg_lines)
    proc = subprocess.run(
        [str(TELEGRAM_SCRIPT), "send", msg],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    duration = time.time() - t0
    metric = append_metric(
        "notify",
        duration_s=round(duration, 2),
        exit_code=proc.returncode,
    )
    print(f"[notify] done {duration:.1f}s exit={proc.returncode}")
    if proc.returncode != 0:
        print("telegram stderr:", proc.stderr)
    return {"metrics": state.get("metrics", []) + [metric]}


def build_graph():
    g = StateGraph(State)
    g.add_node("transcribe", node_transcribe)
    g.add_node("extract", node_extract)
    g.add_node("refine", node_refine)
    g.add_node("notify", node_notify)
    g.add_edge(START, "transcribe")
    g.add_edge("transcribe", "extract")
    g.add_edge("extract", "refine")
    g.add_edge("refine", "notify")
    g.add_edge("notify", END)
    return g.compile(checkpointer=MemorySaver())
