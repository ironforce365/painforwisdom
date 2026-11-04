"""YouTube metadata (spec 2026-11-03-console-operability §7.2): writer → youtube_metadata → youtube_upload.

Calls ``youtube-upload-agent`` with the extraction report and the blog post, merges the channel default tags
and writes ``youtube-upload/metadata.json``. An empty title after one retry is a warning, never an exception:
the package then needs the user to fill it.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from pipeline.contracts import assert_inputs
from pipeline.llm import call_llm
from pipeline.publishing_config import current_profile, load
from pipeline.runtime import DEFAULT_MODEL, append_metric, load_agent_prompt, run_telemetry_path
from pipeline.state import State

TITLE_MAX = 100
TAGS_MAX_CHARS = 500
LLM_ATTEMPTS = 2
MAX_TOKENS = 1000
_TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens", "cost_usd")


def skip_reason(state: State) -> Optional[str]:
    """Why this run has nothing to upload; shared with youtube_upload."""
    if state.get("validator_verdict") == "FAIL":
        return "validator FAIL"
    video = state.get("video_path", "")
    return None if video and Path(video).is_file() else "no video"


def _strip_json_fences(text: str) -> str:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.+?)\s*```$", text, re.DOTALL)
    return fence.group(1).strip() if fence else text


def _parse_agent_response(text: str) -> Dict[str, Any]:
    body = _strip_json_fences(text)
    match = re.search(r"\{.*\}", body, re.DOTALL)  # a prose wrapper around the object is fine
    try:
        parsed = json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def merge_tags(defaults: List[str], extras: List[str], *, limit: int = 25) -> List[str]:
    """Defaults first, deduped case-blind; a tag that would pass YouTube's 500-char total is left out."""
    seen: set[str] = set()
    merged: List[str] = []
    size = 0
    for tag in list(defaults or []) + list(extras or []):
        tag = tag.strip() if isinstance(tag, str) else ""
        if not tag or tag.lower() in seen or size + len(tag) > TAGS_MAX_CHARS:
            continue
        seen.add(tag.lower())
        merged.append(tag)
        size += len(tag)
        if len(merged) >= limit:
            break
    return merged


def _user_message(state: State) -> str:
    return (
        f"Video date: {state.get('video_date', '')}\n\n"
        f"## EXTRACTION REPORT\n```\n{state.get('extraction_report', '')}\n```\n\n"
        f"## BLOG POST TITLE\n{state.get('blog_post_title', '')}\n\n"
        f"## BLOG POST\n{state.get('blog_post_text', '')}\n"
    )


def _ask_agent(state: State, model: str) -> tuple[Dict[str, Any], Dict[str, Any], Optional[str]]:
    """Up to two calls until a title comes back. Returns (parsed, usage over all calls, warning)."""
    # "## OUTPUT" holds the JSON format here, not file-writing steps: stripping it made the agent answer in prose.
    system_prompt = load_agent_prompt("youtube-upload-agent.md", strip_output_section=False)
    usage: Dict[str, Any] = {k: 0 for k in _TOKEN_FIELDS}
    usage["llm_calls"] = 0
    parsed: Dict[str, Any] = {}
    last = ""
    for _ in range(LLM_ATTEMPTS):
        usage["llm_calls"] += 1
        try:
            result = call_llm(model, system_prompt, _user_message(state), max_tokens=MAX_TOKENS)
        except Exception as exc:  # noqa: BLE001 - a dead agent means the user fills the package, not a failed run
            last = f"{type(exc).__name__}: {exc}"
            continue
        for key in _TOKEN_FIELDS:
            usage[key] += result.get(key) or 0
        parsed = _parse_agent_response(result.get("text", ""))
        if str(parsed.get("title") or "").strip():
            return parsed, usage, None
        last = f"stop_reason={result.get('stop_reason')}"
    return parsed, usage, f"empty title after {LLM_ATTEMPTS} attempts ({last})"


def node_youtube_metadata(state: State) -> Dict[str, Any]:
    assert_inputs("youtube_metadata", state)
    t0 = time.time()
    print("[youtube-metadata] start")
    telemetry = run_telemetry_path(state["run_dir"])

    reason = skip_reason(state)
    if reason:
        append_metric(telemetry, "youtube-metadata", duration_s=round(time.time() - t0, 2), skipped=True, reason=reason)
        print(f"[youtube-metadata] skipped ({reason})")
        return {"youtube_skipped": True, "youtube_skip_reason": reason}

    config = load(current_profile())["youtube"]
    model = os.environ.get("PIPELINE_MODEL", DEFAULT_MODEL)
    parsed, usage, warning = _ask_agent(state, model)

    title = str(parsed.get("title") or "").strip()[:TITLE_MAX]
    suffix = str(config.get("description_suffix") or "").strip()
    description = f"{str(parsed.get('description') or '').strip()} {suffix}".strip()
    extras = parsed.get("tags_extra")
    tags = merge_tags(config.get("default_tags") or [], extras if isinstance(extras, list) else [])

    out_dir = Path(state["run_dir"]) / "youtube-upload"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "metadata.json"
    path.write_text(json.dumps({"title": title, "description": description, "tags": tags, "warning": warning}, indent=2))

    usage["cost_usd"] = round(usage["cost_usd"], 6)
    duration = time.time() - t0
    append_metric(telemetry, "youtube-metadata", duration_s=round(duration, 2), model=model, **usage,
                  title=title, tag_count=len(tags), **({"warning": warning} if warning else {}))
    print(f"[youtube-metadata] done {duration:.1f}s title={title!r} warning={warning!r}")
    return {"youtube_metadata_path": str(path), **({"youtube_metadata_warning": warning} if warning else {})}
